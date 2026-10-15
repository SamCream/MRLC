"""Server-owned MRLC runtime actions."""
from __future__ import annotations
import asyncio
import csv
import hashlib
import io
import json
import os
import re
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


def _route_tokens(value:str) -> set[str]:
    text=(value or '').casefold()
    tokens=set(re.findall(r"[a-z0-9_]+",text))
    cjk=''.join(re.findall(r"[\u3400-\u9fff]",text))
    tokens.update(cjk)
    tokens.update(cjk[i:i+2] for i in range(max(0,len(cjk)-1)))
    return {token for token in tokens if token}


def _response_candidates(store:Store,project_id:str,content:str,explicit_agent_id:str|None):
    """Return authorized idle responders in deterministic routing order.

    No model call is spent on routing. Explicit Agent selection wins, then explicit
    @Agent/@Role mentions, then lexical overlap with role name/purpose/capabilities.
    """
    roles={role['id']:role for role in store.all('role') if role.get('lifecycle')=='active'}
    candidates=[]
    for agent in store.all('agent',project_id):
        role=roles.get(agent.get('roleTemplateId'))
        if agent.get('status')!='idle' or role is None or 'messages.reply' not in role.get('allowedPermissions',[]):
            continue
        busy=store.execute(
            "SELECT 1 FROM jobs WHERE agent_id=? AND status IN ('waiting','queued','running') LIMIT 1",
            (agent['id'],),
        ).fetchone()
        if busy is not None:
            continue
        candidates.append((agent,role))
    if explicit_agent_id:
        selected=next((item for item in candidates if item[0]['id']==explicit_agent_id),None)
        if selected is None:
            raise ValueError('Selected Agent is unavailable or not authorized to reply')

    folded=content.casefold()
    query=_route_tokens(content)
    ranked=[]
    for agent,role in candidates:
        labels=[str(agent.get('name') or ''),str(role.get('name') or ''),str(role.get('id') or '')]
        mention_positions=[
            folded.find('@'+label.casefold()) for label in labels
            if label and folded.find('@'+label.casefold())>=0
        ]
        mentioned=bool(mention_positions)
        material=' '.join([
            str(role.get('name') or ''),str(role.get('purpose') or ''),
            ' '.join(str(x) for x in role.get('capabilities',[]) or []),
        ])
        overlap=len(query & _route_tokens(material))
        substring=sum(
            2 for capability in role.get('capabilities',[]) or []
            if str(capability).strip() and str(capability).casefold() in folded
        )
        score=overlap+substring
        ranked.append({
            'agent':agent,'role':role,'mentioned':mentioned,
            'mentionPos':min(mention_positions) if mention_positions else 10**9,
            'score':score,
        })

    ranked.sort(key=lambda item:(
        0 if explicit_agent_id and item['agent']['id']==explicit_agent_id else 1,
        0 if item['mentioned'] else 1,
        item['mentionPos'],-item['score'],
        str(item['role']['id']),str(item['agent']['id']),
    ))
    return ranked


def register(api,store:Store,runtime):
    core=api.state.infra
    # ====== Live runtime: no synthetic GPUs, sessions or budgets ======
    @api.get('/api/v1/runtime/slots')
    def list_slots(principal:Any=Depends(logged_in)):
        items=store.all('slot')
        if principal.project_ids is not None:
            allowed=principal.project_ids; items=[x for x in items if (store.execute("SELECT project_id FROM objects WHERE kind='slot' AND id=?",(x['id'],)).fetchone() or {'project_id':None})['project_id'] in allowed]
        return page(items)

    @api.post('/api/v1/runtime/slots/{slot_id}/{action}')
    def change_slot(slot_id:str,action:str,_:Any=Depends(logged_in)):
        if action not in ('pause','resume','release'):bad('Unsupported slot action',404)
        slot=obj_or_404(store,'slot',slot_id)
        if slot['state']=='free' and action=='release':return slot
        # The OpenAI-compatible transport offers no universal checkpoint/pause/lease API.
        # Returning 409 is safer than advertising an operation that has no effect.
        bad('Physical runtime lifecycle operation unsupported by the configured provider adapter',409)

    @api.get('/api/v1/runtime/queue')
    def runtime_queue(principal:Any=Depends(logged_in)):
        rows=store.execute("SELECT * FROM jobs WHERE status IN ('waiting','queued') ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, priority DESC, created_at").fetchall()
        if principal.project_ids is not None:rows=[r for r in rows if r['project_id'] in principal.project_ids]
        return page([dict(id=r['id'],position=i+1,projectId=r['project_id'],taskId=r['target_id'] if r['kind']=='task' else None,
            title=(store.obj('task',r['target_id']) or {}).get('title','Agent response') if r['kind']=='task' else 'Agent response',
            requestedRole=(store.obj('agent',r['agent_id']) or {}).get('name','Agent'),
            priority=('high' if int(r['priority'])>=80 else 'low' if int(r['priority'])<40 else 'medium'),
            waitReason=('Waiting for previous role response' if r['status']=='waiting' else 'Waiting for available executor capacity')) for i,r in enumerate(rows)])

    @api.get('/api/v1/runtime/sessions')
    def runtime_sessions(principal:Any=Depends(logged_in)):
        rows=store.execute("SELECT * FROM jobs WHERE status IN ('running','queued','waiting') ORDER BY created_at").fetchall()
        if principal.project_ids is not None:rows=[r for r in rows if r['project_id'] in principal.project_ids]
        return page([dict(id=r['id'],slotId='slot-'+r['id'] if r['status']=='running' else None,
                          projectId=r['project_id'],agentId=r['agent_id'],state=r['status'],startedAt=r['started_at']) for r in rows])

    @api.get('/api/v1/runtime/jobs')
    def list_jobs(principal:Any=Depends(logged_in)):
        rows=store.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100').fetchall()
        if principal.project_ids is not None:rows=[r for r in rows if r['project_id'] in principal.project_ids]
        return page([dict(r) for r in rows])

    @api.get('/api/v1/runtime/metrics')
    def metrics(_:Any=Depends(logged_in)):
        return None  # no verified system or GPU telemetry source

    @api.get('/api/v1/runtime/budget')
    def budget(principal:Any=Depends(logged_in)):
        records=store.all('usageRecord');policies=store.all('budgetPolicy')
        if principal.project_ids is not None:
            records=[x for x in records if x.get('projectId') in principal.project_ids];policies=[x for x in policies if x.get('projectId') in principal.project_ids]
        measurable=any(x.get('costUsd') is not None for x in records)
        return {'measurable':measurable,'totalCostUsd':sum(float(x.get('costUsd') or 0) for x in records) if measurable else None,
                'inputTokens':sum(int(x.get('inputTokens') or 0) for x in records),'outputTokens':sum(int(x.get('outputTokens') or 0) for x in records),'policies':policies}

    @api.post('/api/v1/tasks/{task_id}/dispatch',status_code=202)
    def dispatch_task(task_id:str,_:Any=Depends(logged_in)):
        if not runtime.available:bad('Provider not configured; set MRLC_MODEL_BASE_URL and MRLC_MODEL_ID',503)
        with store.tx():
            t=obj_or_404(store,'task',task_id);project(store,t['projectId'],True)
            policy=store.obj('budgetPolicy',t['projectId'])
            if policy and policy.get('monthlyLimit') is not None:
                spent=sum(float(x.get('costUsd') or 0) for x in store.all('usageRecord',t['projectId']))
                if spent>=float(policy['monthlyLimit']):bad('Project monthly model budget exhausted',402)
            if t['status'] not in ('todo','blocked','failed'):bad('Task cannot be dispatched from current state',409)
            if not accepted_deps(store,t):bad('Task dependencies not yet accepted',409)
            role_id=t.get('assigneeRoleId')
            if not role_id:bad('Task needs an assigned role template',409)
            role=obj_or_404(store,'role',role_id)
            if role['lifecycle']!='active' or 'tasks.execute' not in role['allowedPermissions']:
                bad('Role not active or missing tasks.execute permission',403)
            agents=[a for a in store.all('agent',t['projectId']) if a['roleTemplateId']==role_id and a['status']=='idle']
            if not agents:bad('No idle agent assigned to this task role',409)
            agent=agents[0]
            if store.execute("SELECT 1 FROM jobs WHERE agent_id=? AND status IN ('waiting','queued','running') LIMIT 1",(agent['id'],)).fetchone():
                bad('Agent is already reserved for an execution',409)
            job=dict(id=ident('job'),kind='task',project_id=t['projectId'],target_id=t['id'],agent_id=agent['id'])
            snapshot={'id':ident('ctx'),'projectId':t['projectId'],'agentId':agent['id'],'taskId':t['id'],
                      'channelId':None,'artifactIds':[], 'taskRevision':t.get('updatedAt'), 'createdAt':now(),
                      'projection':'task','providerCheckpointId':None}
            store.put('contextSnapshot',snapshot,t['projectId'])
            priority=max(0,min(int(t.get('priorityValue',50)),100))
            store.execute('INSERT INTO jobs(id,kind,project_id,target_id,agent_id,status,created_at,priority,max_attempts,context_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (job['id'],'task',job['project_id'],job['target_id'],job['agent_id'],'queued',now(),priority,2,snapshot['id']))
            core.enqueue_job(agent,t['projectId'],priority,job_id=job['id'])
            t['status']='queued';t['assigneeAgentId']=agent['id'];t['updatedAt']=now()
            event_and_audit(store,'scheduler','task',t,t['projectId'],task_id)
            return {'jobId':job['id'],'status':'queued'}

    @api.post('/api/v1/projects/{project_id}/schedule')
    def schedule_project(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id,True)
        if not runtime.available:bad('Model executor not configured',503)
        queued=[];skipped=[]
        for task in store.all('task',project_id):
            if task['status'] not in ('todo','blocked','failed'):continue
            if not accepted_deps(store,task):
                skipped.append({'taskId':task['id'],'reason':'Dependencies not accepted'});continue
            try:
                queued.append(dispatch_task(task['id']))
            except HTTPException as e:
                skipped.append({'taskId':task['id'],'reason':str(e.detail)})
        return {'queued':queued,'skipped':skipped}

    @api.post('/api/v1/channels/{channel_id}/respond',status_code=202)
    def request_agent_response(channel_id:str,body:dict,_:Any=Depends(logged_in)):
        if not runtime.available:bad('Model provider is not configured',503)
        with store.tx():
            c=obj_or_404(store,'channel',channel_id);project(store,c['projectId'],True)
            task_id=body.get('taskId')
            if task_id is not None:
                task=obj_or_404(store,'task',nonempty(task_id,'taskId'))
                if task['projectId']!=c['projectId']:bad('Cross-project task context forbidden',403)

            history=[m for m in store.all('message') if m['channelId']==channel_id]
            if not history or history[-1]['senderName']!='You':bad('No unanswered user message',409)
            user_message=history[-1]
            artifacts=list(dict.fromkeys(
                list_of_str(body.get('artifactIds',[]),'artifactIds')
                +list_of_str(user_message.get('artifactIds',[]),'message.artifactIds')
            ))
            for aid in artifacts:
                a=obj_or_404(store,'artifact',aid)
                if a['projectId']!=c['projectId'] or not a.get('contentAvailable'):
                    bad('Unusable context evidence',403)
            response_mode=oneof(user_message.get('responseMode','single'),('single','multi'),'responseMode')
            explicit_agent_id=body.get('agentId')
            if explicit_agent_id is not None:
                explicit_agent_id=nonempty(explicit_agent_id,'agentId')
                selected_agent=obj_or_404(store,'agent',explicit_agent_id)
                if selected_agent['projectId']!=c['projectId']:
                    bad('Cross-project Agent invocation forbidden',403)
            try:
                ranked=_response_candidates(
                    store,c['projectId'],str(user_message.get('content') or ''),explicit_agent_id
                )
            except ValueError as exc:
                bad(str(exc),409)
            if not ranked:bad('No idle authorized Agent is available for this response',409)

            mentioned=[item for item in ranked if item['mentioned']]
            if response_mode=='single':
                chosen=ranked[:1]
            elif mentioned:
                chosen=mentioned[:4]
                if explicit_agent_id and all(item['agent']['id']!=explicit_agent_id for item in chosen):
                    pinned=next(item for item in ranked if item['agent']['id']==explicit_agent_id)
                    chosen=[pinned,*chosen][:4]
            else:
                relevant=[item for item in ranked if item['score']>0]
                chosen=(relevant[:4] if relevant else ranked[:1])

            # One responder per role unless the user explicitly selected a concrete Agent.
            unique=[];seen_roles=set()
            for item in chosen:
                rid=item['role']['id']
                if rid in seen_roles and item['agent']['id']!=explicit_agent_id:
                    continue
                seen_roles.add(rid);unique.append(item)
            chosen=unique
            group_id=ident('response-group')
            created=now()
            store.execute(
                "INSERT INTO chat_response_groups(id,project_id,channel_id,user_message_id,response_mode,status,created_at) "
                "VALUES(?,?,?,?,?,'QUEUED',?)",
                (group_id,c['projectId'],channel_id,user_message['id'],response_mode,created),
            )
            jobs=[];routes=[]
            for index,item in enumerate(chosen):
                agent=item['agent'];role=item['role'];job_id=ident('job')
                route_reason=(
                    'explicit_agent' if explicit_agent_id==agent['id']
                    else 'explicit_mention' if item['mentioned']
                    else 'responsibility_match' if item['score']>0
                    else 'stable_fallback'
                )
                job_status='queued' if index==0 else 'waiting'
                snapshot={
                    'id':ident('ctx'),'projectId':c['projectId'],'agentId':agent['id'],
                    'taskId':task_id,'channelId':channel_id,'artifactIds':artifacts,
                    'createdAt':created,'projection':'channel','providerCheckpointId':None,
                    'responseGroupId':group_id,'responseOrder':index+1,
                    'routeReason':route_reason,'knowledgeReadiness':'PENDING',
                    'knowledgeReadinessReason':'Evaluated at execution time against scoped evidence.',
                }
                store.put('contextSnapshot',snapshot,c['projectId'])
                store.execute(
                    'INSERT INTO jobs(id,kind,project_id,target_id,agent_id,status,created_at,priority,max_attempts,context_id) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (job_id,'chat',c['projectId'],channel_id,agent['id'],job_status,created,60,2,snapshot['id'])
                )
                store.execute(
                    "INSERT INTO chat_response_members(group_id,sequence,agent_id,job_id,route_reason,status) "
                    "VALUES(?,?,?,?,?,?)",
                    (group_id,index+1,agent['id'],job_id,route_reason,job_status.upper()),
                )
                if index==0:
                    core.enqueue_job(agent,c['projectId'],60,job_id=job_id)
                jobs.append(job_id)
                routes.append({
                    'agentId':agent['id'],'roleId':role['id'],'roleName':role['name'],
                    'routeReason':route_reason,'order':index+1,
                })
            store.audit(
                'scheduler',f'Channel response group {group_id} queued ({response_mode}, {len(jobs)} responder(s))',
                c['projectId']
            )
            store.emit(
                'chat.response.group.queued',c['projectId'],
                {'groupId':group_id,'jobIds':jobs,'responseMode':response_mode,'routes':routes},
            )
            return {
                'jobId':jobs[0],'jobIds':jobs,'groupId':group_id,'status':'queued',
                'responseMode':response_mode,'agents':routes,
            }

    @api.post('/api/v1/runtime/jobs/{job_id}/cancel')
    def cancel_job(job_id:str,_:Any=Depends(logged_in)):
        with store.tx():
            row=store.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:bad('job not found',404)
            if row['status'] in ('completed','failed','cancelled'):bad('Job is already terminal',409)
            if row['status'] in ('queued','waiting'):
                store.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,completed_at=? WHERE id=?",(now(),job_id))
                if row['status']=='queued':
                    core.finish_job(dict(row),'CANCELLED')
                if row['kind']=='chat':
                    core.advance_chat_response_group(job_id,'CANCELLED')
                if row['kind']=='task':
                    task=store.obj('task',row['target_id'])
                    if task and task['status']=='queued': task['status']='todo';task['updatedAt']=now();store.change('scheduler','task',task,row['project_id'],task['id'])
            else:
                store.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?',(job_id,))
            store.audit('scheduler',f'Cancellation requested for {job_id}',row['project_id'],row['target_id'] if row['kind']=='task' else None)
            store.emit('job.cancel.requested',row['project_id'],{'id':job_id})
            return {'jobId':job_id,'status':'cancelled' if row['status'] in ('queued','waiting') else 'cancelling'}

    @api.post('/api/v1/runtime/jobs/{job_id}/retry',status_code=202)
    def retry_job(job_id:str,_:Any=Depends(logged_in)):
        with store.tx():
            row=store.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:bad('job not found',404)
            if row['status'] not in ('failed','cancelled'):bad('Only failed or cancelled jobs may be retried',409)
            context=store.obj('contextSnapshot',row['context_id']) if row['context_id'] else None
            if not context:bad('Original context snapshot is unavailable',409)
            new_id=ident('job')
            store.execute('INSERT INTO jobs(id,kind,project_id,target_id,agent_id,status,created_at,priority,attempt,max_attempts,context_id,parent_job_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                          (new_id,row['kind'],row['project_id'],row['target_id'],row['agent_id'],'queued',now(),row['priority'],int(row['attempt'])+1,row['max_attempts'],row['context_id'],job_id))
            retry_agent=obj_or_404(store,'agent',row['agent_id'])
            core.enqueue_job(retry_agent,row['project_id'],int(row['priority']),job_id=new_id)
            if row['kind']=='task':
                task=store.obj('task',row['target_id'])
                if task:task['status']='queued';task['updatedAt']=now();store.change('scheduler','task',task,row['project_id'],task['id'])
            store.audit('scheduler',f'Job {job_id} retried as {new_id}',row['project_id'],row['target_id'] if row['kind']=='task' else None)
            store.emit('job.retried',row['project_id'],{'id':new_id,'parentJobId':job_id})
            return {'jobId':new_id,'status':'queued','contextSnapshotId':row['context_id'],'providerCheckpointRestored':False}

    @api.get('/api/v1/runtime/jobs/{job_id}/context')
    def job_context(job_id:str,_:Any=Depends(logged_in)):
        row=store.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:bad('job not found',404)
        if not row['context_id']:return None
        return obj_or_404(store,'contextSnapshot',row['context_id'])

