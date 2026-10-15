"""Server-owned MRLC organization actions."""
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
    # ====== Projects ======
    def project_view(p):
        tasks=store.all('task',p['id'])
        agents=store.all('agent',p['id'])
        approvals=store.all('approval',p['id'])
        archived=p['status']=='archived'
        status=('archived' if archived else
                'running' if any(t['status'] in ('running','queued') for t in tasks) else
                'blocked' if any(t['status'] in ('blocked','failed') for t in tasks) else 'idle')
        attention={
            'blockedTasks':sum(t['status'] in ('blocked','failed') for t in tasks),
            'pendingApprovals':sum(a.get('status')=='pending' for a in approvals),
            'failedAgents':sum(a.get('status')=='failed' for a in agents),
        }
        return {
            **p,'status':status,
            'progress':round(sum(t['progress'] for t in tasks)/len(tasks)) if tasks else 0,
            'agentCount':len(agents),
            'attentionCount':0 if archived else sum(attention.values()),
            'attention':attention,
        }

    @api.get('/api/v1/projects')
    def list_projects(principal:Any=Depends(logged_in)):
        items=[project_view(p) for p in store.all('project')]
        if principal.project_ids is not None:items=[p for p in items if p['id'] in principal.project_ids]
        return page(items)

    @api.post('/api/v1/projects',status_code=201)
    def create_project(body:dict,_:Any=Depends(logged_in)):
        name=nonempty(body.get('name'),'name',120)
        with store.tx():
            p={'id':ident('project'),'name':name,'description':str(body.get('description') or '')[:4000],
               'status':'idle','progress':0,'createdAt':now()}
            event_and_audit(store,'owner','project',p,p['id'])
            return p

    @api.get('/api/v1/projects/{project_id}')
    def get_project(project_id:str,_:Any=Depends(logged_in)):
        return project_view(project(store,project_id))

    @api.patch('/api/v1/projects/{project_id}')
    def update_project(project_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            p=project(store,project_id,True)
            if 'name' in body:p['name']=nonempty(body['name'],'name',120)
            if 'description' in body:p['description']=str(body['description'])[:4000]
            event_and_audit(store,'owner','project',p,project_id)
            return p

    @api.post('/api/v1/projects/{project_id}/archive')
    def archive_project(project_id:str,body:dict|None=None,_:Any=Depends(logged_in)):
        with store.tx():
            p=project(store,project_id,True)
            running=[a for a in store.all('agent',project_id) if a['status']=='working']
            outstanding=store.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('waiting','queued','running') LIMIT 1",(project_id,)).fetchone()
            if running or outstanding:bad('Project has running or queued jobs; finish execution before archiving',409)
            p['status']='archived';p['archiveReason']=str((body or {}).get('reason') or '')[:2000]
            for a in store.all('agent',project_id):
                if a['status'] in ('idle','paused'):
                    a['status']='parked';store.change('owner','agent',a,project_id)
            event_and_audit(store,'owner','project',p,project_id)
            return p

    # ====== Role templates / agents ======
    @api.get('/api/v1/roles')
    def list_roles(_:Any=Depends(logged_in)):
        return page(store.all('role'))

    @api.post('/api/v1/roles',status_code=201)
    def create_role(body:dict,_:Any=Depends(logged_in)):
        name=nonempty(body.get('name'),'name',120)
        lifecycle=oneof(body.get('lifecycle','draft'),('draft','active'),'lifecycle')
        caps=list_of_str(body.get('capabilities',[]),'capabilities')
        permissions=list_of_str(body.get('allowedPermissions',[]),'allowedPermissions')
        with store.tx():
            r=dict(id=ident('role'),name=name,version='v1',purpose=str(body.get('purpose') or '')[:4000],capabilities=caps,
                   allowedPermissions=permissions,preferredModelId=body.get('preferredModelId'),lifecycle=lifecycle)
            event_and_audit(store,'owner','role',r)
            return r

    @api.patch('/api/v1/roles/{role_id}')
    def update_role(role_id:str,body:dict,_:Any=Depends(logged_in)):
        allowed={'name','purpose','capabilities','allowedPermissions','preferredModelId'}
        if set(body)-allowed:bad('Unexpected role fields')
        with store.tx():
            r=obj_or_404(store,'role',role_id)
            if r['lifecycle']=='archived':bad('Archived role is read-only',409)
            if 'name' in body:r['name']=nonempty(body['name'],'name',120)
            if 'purpose' in body:r['purpose']=str(body['purpose'])[:4000]
            for key in ('capabilities','allowedPermissions'):
                if key in body:r[key]=list_of_str(body[key],key)
            if 'preferredModelId' in body:r['preferredModelId']=body['preferredModelId']
            role_version(r)
            event_and_audit(store,'owner','role',r)
            return r

    @api.post('/api/v1/roles/{role_id}/{action}')
    def role_transition(role_id:str,action:str,_:Any=Depends(logged_in)):
        oneof(action,('activate','park','archive'),'role transition')
        with store.tx():
            r=obj_or_404(store,'role',role_id)
            if r['lifecycle']=='archived':bad('Archived roles are immutable',409)
            if action=='archive' and any(a['status'] not in ('parked','archived') for a in store.all('agent') if a['roleTemplateId']==role_id):
                bad('Park or archive agent instances before archiving role template',409)
            r['lifecycle']={'activate':'active','park':'parked','archive':'archived'}[action]
            event_and_audit(store,'owner','role',r)
            return r

    @api.get('/api/v1/projects/{project_id}/agents')
    def list_agents(project_id:str,principal:Any=Depends(logged_in)):
        project(store,project_id)
        if not principal.allows('agents.read',project_id):bad('Project access denied',403)
        return page(store.all('agent',project_id))

    @api.post('/api/v1/projects/{project_id}/agents',status_code=201)
    def assign_agent(project_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            project(store,project_id,True)
            rid=nonempty(body.get('roleTemplateId'),'roleTemplateId')
            role=obj_or_404(store,'role',rid)
            if role['lifecycle']!='active':bad('Only active role templates may be assigned',409)
            session={'id':ident('session'),'projectId':project_id,'agentId':None,'state':'idle','createdAt':now(),'lastResumedAt':now(),'lastParkedAt':None,'providerCheckpointId':None}
            agent=dict(id=ident('agent'),projectId=project_id,roleTemplateId=rid,
                       name=nonempty(body.get('name') or role['name'],'name',120),status='idle',taskId=None,sessionId=session['id'])
            session['agentId']=agent['id'];store.put('agentSession',session,project_id)
            event_and_audit(store,'owner','agent',agent,project_id)
            return agent

    @api.get('/api/v1/agents/{agent_id}')
    def get_agent(agent_id:str,_:Any=Depends(logged_in)):
        return obj_or_404(store,'agent',agent_id)

    @api.get('/api/v1/agents/{agent_id}/sessions')
    def agent_sessions(agent_id:str,_:Any=Depends(logged_in)):
        obj_or_404(store,'agent',agent_id)
        sessions=[x for x in store.all('agentSession') if x.get('agentId')==agent_id]
        return page(sessions)

    @api.get('/api/v1/agents/{agent_id}/knowledge')
    def agent_evidence_readiness(agent_id:str,taskId:str|None=None,_:Any=Depends(logged_in)):
        agent=obj_or_404(store,'agent',agent_id)
        if not taskId:return {'status':'UNKNOWN','evidenceArtifactIds':[],'reason':'No task scoped; no knowledge claim can be verified'}
        task=obj_or_404(store,'task',taskId)
        if task['projectId']!=agent['projectId']:bad('Cross-project context query',403)
        verified=[aid for criterion in task['acceptanceCriteria'] if criterion['satisfied']
                  for aid in criterion.get('evidenceArtifactIds',[]) if (store.obj('artifact',aid) or {}).get('contentAvailable')]
        if not verified:status='UNKNOWN'
        elif task['acceptanceCriteria'] and all(c['satisfied'] for c in task['acceptanceCriteria']):status='KNOWN'
        else:status='PARTIAL'
        return {'status':status,'evidenceArtifactIds':list(dict.fromkeys(verified)),
                'reason':'Based only on server-verified task evidence; not a guarantee that an LLM knows the answer'}

    @api.post('/api/v1/agents/{agent_id}/park')
    def park_agent(agent_id:str,_:Any=Depends(logged_in)):
        with store.tx():
            a=obj_or_404(store,'agent',agent_id)
            project(store,a['projectId'],True)
            if a['status']=='working' or store.execute("SELECT 1 FROM jobs WHERE agent_id=? AND status IN ('waiting','queued','running') LIMIT 1",(agent_id,)).fetchone():
                bad('Agent has an outstanding job; no checkpoint-safe park available',409)
            if a['status'] not in ('idle','paused','parked'):bad('Agent cannot be parked from current state',409)
            a['status']='parked'
            session=store.obj('agentSession',a.get('sessionId')) if a.get('sessionId') else None
            if session:session['state']='parked';session['lastParkedAt']=now();store.put('agentSession',session,a['projectId'])
            event_and_audit(store,'owner','agent',a,a['projectId'])
            return a

    @api.post('/api/v1/agents/{agent_id}/resume')
    def resume_agent(agent_id:str,_:Any=Depends(logged_in)):
        with store.tx():
            a=obj_or_404(store,'agent',agent_id)
            project(store,a['projectId'],True)
            if a['status'] not in ('parked','paused'):bad('Only parked or paused agents may become idle',409)
            role=obj_or_404(store,'role',a['roleTemplateId'])
            if role['lifecycle']!='active':bad('Role template is not active',409)
            a['status']='idle'
            session=store.obj('agentSession',a.get('sessionId')) if a.get('sessionId') else None
            if session:session['state']='idle';session['lastResumedAt']=now();store.put('agentSession',session,a['projectId'])
            event_and_audit(store,'owner','agent',a,a['projectId'])
            return a

