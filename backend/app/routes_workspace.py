"""Server-owned MRLC workspace actions."""
from __future__ import annotations
import asyncio
import csv
import hashlib
import io
import json
import os
import secrets
from pathlib import Path
from datetime import datetime,timezone,timedelta
from typing import Any
from fastapi import Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from .common import (bad,nonempty,oneof,expect,list_of_str,page,obj_or_404,project,
                     role_version,file_root,server,logged_in,default_settings,
                     validate_settings,accepted_deps,event_and_audit)
from .storage import Store, ident, now, jdump


def register(api,store:Store,runtime):
    # ====== Channels / conversation ======
    @api.get('/api/v1/projects/{project_id}/channels')
    def list_channels(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return page(store.all('channel',project_id))

    @api.post('/api/v1/projects/{project_id}/channels',status_code=201)
    def create_channel(project_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            project(store,project_id,True)
            name=nonempty(body.get('name'),'name',120)
            if any(c['name'].casefold()==name.casefold() for c in store.all('channel',project_id)):
                bad('Channel name already exists in project',409)
            channel=dict(id=ident('channel'),projectId=project_id,name=name,purpose=str(body.get('purpose') or '')[:3000],
                contextPolicy=oneof(body.get('contextPolicy','relevant'),('relevant','task','project'),'contextPolicy'))
            event_and_audit(store,'owner','channel',channel,project_id)
            return channel

    @api.patch('/api/v1/channels/{channel_id}')
    def update_channel(channel_id:str,body:dict,_:Any=Depends(logged_in)):
        if set(body)-{'name','purpose','contextPolicy'}:bad('Unsupported channel properties')
        with store.tx():
            c=obj_or_404(store,'channel',channel_id)
            project(store,c['projectId'],True)
            if 'name' in body:c['name']=nonempty(body['name'],'name',120)
            if 'purpose' in body:c['purpose']=str(body['purpose'])[:3000]
            if 'contextPolicy' in body:c['contextPolicy']=oneof(body['contextPolicy'],('relevant','task','project'),'contextPolicy')
            event_and_audit(store,'owner','channel',c,c['projectId'])
            return c

    @api.get('/api/v1/channels/{channel_id}/messages')
    def list_messages(channel_id:str,_:Any=Depends(logged_in)):
        obj_or_404(store,'channel',channel_id)
        return page([m for m in store.all('message') if m['channelId']==channel_id])

    @api.post('/api/v1/channels/{channel_id}/messages',status_code=201)
    def send_message(channel_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            channel=obj_or_404(store,'channel',channel_id)
            project(store,channel['projectId'],True)
            content=nonempty(body.get('content'),'content',100000)
            response_mode=oneof(body.get('responseMode','single'),('single','multi'),'responseMode')
            ids=list_of_str(body.get('artifactIds',[]),'artifactIds')
            for aid in ids:
                a=obj_or_404(store,'artifact',aid)
                if a['projectId']!=channel['projectId']:bad('Attachment belongs to another project',403)
            m=dict(id=ident('msg'),channelId=channel_id,senderName='You',content=content,
                   createdAt=now(),artifactIds=ids,responseMode=response_mode)
            event_and_audit(store,'owner','message',m,channel['projectId'])
            # No implicit inference cost. POST /respond explicitly requests model work.
            return m

    @api.get('/api/v1/channels/{channel_id}/search')
    def search_channel(channel_id:str,q:str='',_:Any=Depends(logged_in)):
        obj_or_404(store,'channel',channel_id)
        return page([m for m in store.all('message') if m['channelId']==channel_id and q.lower() in m['content'].lower()])

    # ====== Tasks: durable dependency and acceptance controls ======
    @api.get('/api/v1/projects/{project_id}/tasks')
    def list_tasks(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return page(store.all('task',project_id))

    @api.post('/api/v1/projects/{project_id}/tasks',status_code=201)
    def create_task(project_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            project(store,project_id,True)
            if body.get('projectId',project_id)!=project_id:bad('Project ID mismatch',409)
            deps=list_of_str(body.get('dependencyIds',[]),'dependencyIds')
            for d in deps:
                if obj_or_404(store,'task',d)['projectId']!=project_id:bad('Dependencies must belong to the same project',409)
            rid=body.get('assigneeRoleId')
            if rid:obj_or_404(store,'role',rid)
            criteria=body.get('acceptanceCriteria',[])
            if not isinstance(criteria,list) or len(criteria)>50:bad('Invalid acceptance criteria')
            task=dict(id=ident('task'),projectId=project_id,title=nonempty(body.get('title'),'title',250),
                description=str(body.get('description') or '')[:20000],assigneeRoleId=rid,assigneeAgentId=None,
                status='todo',progress=0,dependencyIds=deps,evidenceStatus='missing',
                acceptanceCriteria=[dict(id=ident('criterion'),description=nonempty(x.get('description') if isinstance(x,dict) else None,'criterion',2000),satisfied=False,evidenceArtifactIds=[]) for x in criteria],
                updatedAt=now())
            event_and_audit(store,'owner','task',task,project_id,task['id'])
            return task

    @api.get('/api/v1/tasks/{task_id}')
    def get_task(task_id:str,_:Any=Depends(logged_in)):
        return obj_or_404(store,'task',task_id)

    @api.patch('/api/v1/tasks/{task_id}')
    def patch_task(task_id:str,body:dict,_:Any=Depends(logged_in)):
        # Never let the browser write the authoritative state/progress/approval directly.
        allowed={'title','description','assigneeRoleId','dependencyIds','acceptanceCriteria'}
        if set(body)-allowed:bad('Task state/progress/evidence is server-controlled',403)
        with store.tx():
            t=obj_or_404(store,'task',task_id)
            project(store,t['projectId'],True)
            if t['status'] in ('running','queued','accepted','cancelled'):bad('Cannot modify task while executing or after acceptance',409)
            for k in ('title','description'):
                if k in body:t[k]=nonempty(body[k],k,20000 if k=='description' else 250)
            if 'assigneeRoleId' in body:
                if body['assigneeRoleId']:obj_or_404(store,'role',body['assigneeRoleId'])
                t['assigneeRoleId']=body['assigneeRoleId']
            if 'dependencyIds' in body:
                deps=list_of_str(body['dependencyIds'],'dependencyIds')
                if task_id in deps:bad('A task cannot depend on itself',409)
                for dep in deps:
                    other=obj_or_404(store,'task',dep)
                    if other['projectId']!=t['projectId']:bad('Cross-project dependencies forbidden',409)
                graph={x['id']:x['dependencyIds'] for x in store.all('task',t['projectId'])}
                graph[task_id]=deps
                def visit(cur,stack):
                    if cur in stack:bad('Circular task dependency',409)
                    for dep in graph.get(cur,[]):visit(dep,stack|{cur})
                visit(task_id,set())
                t['dependencyIds']=deps
            if 'acceptanceCriteria' in body:
                criteria=body['acceptanceCriteria']
                if not isinstance(criteria,list) or len(criteria)>50:bad('Invalid criteria')
                existing={x['id']:x for x in t['acceptanceCriteria']}
                out=[]
                for c in criteria:
                    cid=c.get('id') if isinstance(c,dict) else None
                    if cid and cid not in existing:bad('Criterion ID not found',404)
                    old=existing.get(cid,{})
                    # Satisfaction can only be attested by the backend evidence operation.
                    if 'satisfied' in c and c['satisfied']!=old.get('satisfied',False):bad('Use criteria evidence endpoint',403)
                    out.append({**old,'id':cid or ident('criterion'),'description':nonempty(c.get('description'),'description',2000),
                                'satisfied':old.get('satisfied',False),'evidenceArtifactIds':old.get('evidenceArtifactIds',[])})
                t['acceptanceCriteria']=out
            t['updatedAt']=now()
            event_and_audit(store,'owner','task',t,t['projectId'],task_id)
            return t

    @api.post('/api/v1/tasks/{task_id}/criteria/{criterion_id}/verify')
    def verify_criterion(task_id:str,criterion_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            t=obj_or_404(store,'task',task_id);project(store,t['projectId'],True)
            if t['status']=='accepted':bad('Accepted task is immutable',409)
            criterion=next((x for x in t['acceptanceCriteria'] if x['id']==criterion_id),None)
            if not criterion:bad('Criterion not found',404)
            evidence=list_of_str(body.get('evidenceArtifactIds',[]),'evidenceArtifactIds')
            if not evidence:bad('Evidence is required for verification')
            for aid in evidence:
                a=obj_or_404(store,'artifact',aid)
                if a['projectId']!=t['projectId'] or a.get('taskId') not in (None,task_id):bad('Evidence is out of scope',403)
                if not a.get('contentAvailable'):bad('Evidence has no actual bytes',409)
            criterion['satisfied']=True;criterion['evidenceArtifactIds']=evidence
            t['evidenceStatus']='partial' if any(not x['satisfied'] for x in t['acceptanceCriteria']) else 'complete'
            event_and_audit(store,'owner','task',t,t['projectId'],task_id)
            return t

    @api.post('/api/v1/tasks/{task_id}/review')
    def review_task(task_id:str,_:Any=Depends(logged_in)):
        with store.tx():
            t=obj_or_404(store,'task',task_id);project(store,t['projectId'],True)
            if t['status'] in ('running','queued','accepted','cancelled'):bad('Task cannot enter review from current state',409)
            if not accepted_deps(store,t):bad('Dependencies are not yet accepted',409)
            if not t['acceptanceCriteria'] or not all(c['satisfied'] for c in t['acceptanceCriteria']):bad('Verified acceptance criteria are required',409)
            t['status']='review';t['updatedAt']=now()
            event_and_audit(store,'owner','task',t,t['projectId'],task_id)
            return t

    @api.post('/api/v1/tasks/{task_id}/accept')
    def accept_task(task_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            t=obj_or_404(store,'task',task_id);project(store,t['projectId'],True)
            if t['status']!='review':bad('Task must be in review',409)
            if not accepted_deps(store,t):bad('Unaccepted dependency',409)
            if not t['acceptanceCriteria'] or not all(c['satisfied'] for c in t['acceptanceCriteria']):bad('Incomplete acceptance criteria',409)
            t['status']='accepted';t['progress']=100;t['evidenceStatus']='verified';t['updatedAt']=now()
            t['acceptedBy']='owner';t['acceptanceReason']=nonempty(body.get('reason'),'reason',4000)
            event_and_audit(store,'owner','task',t,t['projectId'],task_id)
            reports=[c for c in store.all('channel',t['projectId']) if c['name']=='Report channel']
            if reports:report=reports[0]
            else:
                report=dict(id=ident('channel'),projectId=t['projectId'],name='Report channel',
                            purpose='Backend verified milestones and acceptance events',contextPolicy='task')
                store.change('workflow','channel',report,t['projectId'])
            artifact_ids=list(dict.fromkeys(aid for c in t['acceptanceCriteria'] for aid in c.get('evidenceArtifactIds',[])))
            for aid in artifact_ids:
                art=store.obj('artifact',aid)
                if art and art['projectId']==t['projectId']:
                    art['status']='verified';store.change('workflow','artifact',art,t['projectId'],task_id)
            level='high' if len(artifact_ids)>=2 else 'medium'
            stage={'level':level,'taskId':task_id,'evidenceArtifactIds':artifact_ids,'verified':True}
            message=dict(id=ident('msg'),channelId=report['id'],senderName='STAGE',
                         content=f'Stage complete — {t["title"]}',createdAt=now(),artifactIds=artifact_ids,stageCompletion=stage)
            store.change('workflow','message',message,t['projectId'],task_id)
            return t

    @api.get('/api/v1/tasks/{task_id}/activity')
    def task_activity(task_id:str,_:Any=Depends(logged_in)):
        obj_or_404(store,'task',task_id)
        return store.logs({'taskId':task_id,'limit':100})

    # More endpoints below.
