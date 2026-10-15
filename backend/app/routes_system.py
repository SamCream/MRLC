"""Server-owned MRLC system actions."""
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
    # ====== Infrastructure, audit, operator preferences ======
    @api.get('/api/v1/system/health')
    def health(_:Any=Depends(logged_in)):
        return dict(healthy=False,checkedAt=now(),services=[
            {'name':'SQLite persistence','healthy':True},
            {'name':'Model provider configured (not health-verified)','healthy':False} if not runtime.available else
            {'name':'Model provider configured (connection not verified)','healthy':False}])

    @api.post('/api/v1/system/health-checks')
    async def health_check(_:Any=Depends(logged_in)):
        store.execute('SELECT 1')
        # Never equate configured credentials with successful model access.
        return dict(healthy=False,checkedAt=now(),services=[
            {'name':'SQLite persistence','healthy':True},
            {'name':'Model inference connectivity','healthy':False}])

    @api.get('/api/v1/system/topology')
    def topology(_:Any=Depends(logged_in)):
        return {'sampledAt':now(),'nodes':[{'id':'mrlc-api','label':'MRLC API','kind':'orchestrator','status':'healthy'},
             {'id':'mrlc-sqlite','label':'SQLite','kind':'persistence','status':'healthy'}],
             'edges':[{'from':'mrlc-api','to':'mrlc-sqlite','label':'transactional writes'}]}

    @api.get('/api/v1/system/nodes')
    def nodes(scope:str|None=None,_:Any=Depends(logged_in)):
        if scope and scope not in ('local','remote'):bad('Invalid scope')
        items=[dict(id='mrlc-host',name='MRLC API host',scope='local',status='online',lastSeenAt=now())]
        return page([item for item in items if not scope or item['scope']==scope])

    @api.get('/api/v1/system/accelerators')
    def accelerators(_:Any=Depends(logged_in)):
        import shutil,subprocess
        items=[]
        if shutil.which('nvidia-smi'):
            try:
                out=subprocess.run(['nvidia-smi','--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
                if out.returncode==0:
                    for line in out.stdout.splitlines():
                        x=[v.strip() for v in line.split(',')]
                        if len(x)>=7:items.append({'id':'gpu-'+x[0],'name':x[1],'memoryTotalMiB':float(x[2]),'memoryUsedMiB':float(x[3]),'utilizationPercent':float(x[4]),'temperatureC':float(x[5]),'powerW':None if x[6]=='[N/A]' else float(x[6]),'source':'nvidia-smi'})
            except Exception:pass
        return page(items)

    @api.get('/api/v1/system/logs')
    def system_logs(request:Request,_:Any=Depends(logged_in)):
        return store.logs(dict(request.query_params))

    @api.get('/api/v1/users/me/log-views')
    def log_views(_:Any=Depends(logged_in)):
        return page(store.all('logView'))

    @api.post('/api/v1/users/me/log-views',status_code=201)
    def create_log_view(body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            view=dict(id=ident('logView'),name=nonempty(body.get('name'),'name',120),filter=expect(body.get('filter',{})))
            event_and_audit(store,'owner','logView',view)
            return view

    @api.patch('/api/v1/users/me/log-views/{view_id}')
    def update_log_view(view_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            v=obj_or_404(store,'logView',view_id)
            if 'name' in body:v['name']=nonempty(body['name'],'name',120)
            if 'filter' in body:v['filter']=expect(body['filter'])
            event_and_audit(store,'owner','logView',v)
            return v

    @api.delete('/api/v1/users/me/log-views/{view_id}',status_code=204)
    def delete_log_view(view_id:str,_:Any=Depends(logged_in)):
        with store.tx():
            obj_or_404(store,'logView',view_id)
            store.delete('logView',view_id)
            store.audit('owner',f'log view {view_id} deleted')
            store.emit('logView.changed',None,{'id':view_id,'deleted':True})
        return Response(status_code=204)

    @api.post('/api/v1/system/logs/export')
    def export_logs(request:Request,body:dict,_:Any=Depends(logged_in)):
        fmt=oneof(body.get('format'),('jsonl','csv','audit_bundle'),'format')
        # On-demand export endpoint (no publicly accessible unauthenticated download token).
        # A session-authenticated streaming URL keeps logs private.
        export_id=ident('export')
        with store.tx():
            expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat().replace('+00:00','Z')
            store.put('logExport',dict(id=export_id,format=fmt,filter=expect(body.get('filter',{})),expiresAt=expires_at),None)
            store.audit('owner',f'Requested {fmt} audit export')
        return {'exportId':export_id,'downloadUrl':'/api/v1/system/logs/exports/'+export_id,
                'expiresAt':expires_at}

    @api.get('/api/v1/system/logs/exports/{export_id}')
    def download_log_export(export_id:str,_:Any=Depends(logged_in)):
        info=obj_or_404(store,'logExport',export_id)
        if info.get('expiresAt','') < now():bad('Export link has expired',410)
        query=dict(info['filter']);query['limit']=200
        rows=[];cursor=None
        while True:
            batch=store.logs({**query,**({'cursor':cursor} if cursor else {})})
            rows.extend(batch['items']);cursor=batch['nextCursor']
            if not cursor or len(rows)>=10000:break
        if info['format']=='jsonl':
            raw=''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows).encode();mime='application/x-ndjson'
        elif info['format']=='audit_bundle':
            import hmac as _hmac
            payload={'generatedAt':now(),'entries':rows}
            packed=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
            sig=_hmac.new(api.state.secret.encode(),packed,hashlib.sha256).hexdigest() if hasattr(api,'state') else ''
            raw=json.dumps({'payload':payload,'signature':sig},ensure_ascii=False).encode();mime='application/json'
        else:
            buff=io.StringIO();fields=['id','timestamp','level','source','message','projectId','taskId','traceId']
            writer=csv.DictWriter(buff,fieldnames=fields);writer.writeheader();writer.writerows(rows)
            raw=buff.getvalue().encode('utf-8-sig');mime='text/csv'
        return Response(content=raw,media_type=mime,
                headers={'Content-Disposition':f'attachment; filename="mrlc-logs.{"json" if info["format"]=="audit_bundle" else info["format"]}"'})

    @api.get('/api/v1/system/audit/verify')
    def verify_audit(_:Any=Depends(logged_in)):
        rows=store.execute('SELECT * FROM audit ORDER BY seq').fetchall();prev='';checked=0
        for r in rows:
            material=jdump({'id':r['id'],'timestamp':r['timestamp'],'level':r['level'],'source':r['source'],'message':r['message'],
                            'projectId':r['project_id'],'taskId':r['task_id'],'traceId':r['trace_id'],'actorId':r['actor_id'],'prevHash':prev})
            expected=hashlib.sha256(material.encode()).hexdigest()
            if (r['prev_hash'] or '')!=prev or (r['entry_hash'] or '')!=expected:
                return {'valid':False,'checked':checked,'brokenAt':r['id']}
            prev=expected;checked+=1
        return {'valid':True,'checked':checked,'headHash':prev or None}

    @api.get('/api/v1/system/host-metrics')
    def host_metrics(_:Any=Depends(logged_in)):
        import shutil, subprocess
        load=None;mem=None
        try:
            load=os.getloadavg()
            text=Path('/proc/meminfo').read_text(); vals={line.split(':')[0]:int(line.split()[1]) for line in text.splitlines() if ':' in line}
            mem={'totalBytes':vals.get('MemTotal',0)*1024,'availableBytes':vals.get('MemAvailable',0)*1024}
        except Exception: pass
        gpus=[]
        if shutil.which('nvidia-smi'):
            try:
                out=subprocess.run(['nvidia-smi','--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
                if out.returncode==0:
                    for line in out.stdout.splitlines():
                        x=[v.strip() for v in line.split(',')]
                        if len(x)>=7:gpus.append({'index':int(x[0]),'name':x[1],'memoryTotalMiB':float(x[2]),'memoryUsedMiB':float(x[3]),'utilizationPercent':float(x[4]),'temperatureC':float(x[5]),'powerW':None if x[6]=='[N/A]' else float(x[6])})
            except Exception: pass
        return {'sampledAt':now(),'loadAverage':load,'memory':mem,'gpus':gpus,'verifiedSource':True}

    @api.get('/api/v1/projects/{project_id}/settings')
    def get_runtime_settings(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return {k:v for k,v in (store.obj('setting',project_id) or default_settings()).items() if k!='id'}

    @api.patch('/api/v1/projects/{project_id}/settings')
    def update_runtime_settings(project_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            project(store,project_id,True)
            current=store.obj('setting',project_id) or default_settings()
            if body.get('expectedRevision')!=current['revision']:bad('Settings revision conflict',409)
            validate_settings(body)
            updated={k:body[k] for k in ('modelId','contextPolicy','sessionPolicy','frontierEscalation')}
            updated.update(id=project_id,revision=current['revision']+1)
            event_and_audit(store,'owner','setting',updated,project_id)
            return {k:v for k,v in updated.items() if k!='id'}

    @api.post('/api/v1/projects/{project_id}/settings/reset')
    def reset_runtime_settings(project_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            project(store,project_id,True)
            current=store.obj('setting',project_id) or default_settings()
            if body.get('expectedRevision')!=current['revision']:bad('Settings revision conflict',409)
            updated={**default_settings(),'id':project_id,'revision':current['revision']+1}
            event_and_audit(store,'owner','setting',updated,project_id)
            return {k:v for k,v in updated.items() if k!='id'}

    def appearance_default():
        return {'theme':'strata-dark','motion':'highlights','density':'compact','stageAnimation':'play-once'}

    @api.get('/api/v1/users/me/appearance')
    def get_appearance(_:Any=Depends(logged_in)):
        return {k:v for k,v in (store.obj('appearance','owner') or appearance_default()).items() if k!='id'}

    @api.patch('/api/v1/users/me/appearance')
    def save_appearance(body:dict,_:Any=Depends(logged_in)):
        if set(body)!={'theme','motion','density','stageAnimation'}:bad('Expected all appearance fields')
        for key,values in [('theme',('strata-dark','soft-dark')),('motion',('highlights','reduced')),
                           ('density',('compact','comfortable')),('stageAnimation',('play-once','static-gold'))]:
            oneof(body.get(key),values,key)
        with store.tx():
            store.change('owner','appearance',{'id':'owner',**body})
            return body

    @api.post('/api/v1/users/me/appearance/reset')
    def reset_appearance(_:Any=Depends(logged_in)):
        with store.tx():
            store.change('owner','appearance',{'id':'owner',**appearance_default()})
            return appearance_default()

    @api.get('/api/v1/projects/{project_id}/overview')
    def overview(project_id:str,_:Any=Depends(logged_in)):
        p=project(store,project_id)
        tasks=store.all('task',project_id)
        agents=store.all('agent',project_id)
        approvals=store.all('approval',project_id)
        channels=store.all('channel',project_id)
        ids={c['id'] for c in channels}
        stages=[dict(messageId=m['id'],title=m['content'],verified=True,level=m['stageCompletion']['level'],
               occurredAt=m['createdAt'],taskId=m['stageCompletion']['taskId'])
                for m in store.all('message') if m['channelId'] in ids and m.get('stageCompletion',{}).get('verified')]
        stages.sort(key=lambda v:v['occurredAt'],reverse=True)
        slots=[s for s in store.all('slot') if s.get('agentId') in {a['id'] for a in agents}]
        q=[r for r in store.execute("SELECT * FROM jobs WHERE project_id=? AND status='queued'",(project_id,)).fetchall()]
        return dict(projectId=project_id,taskCount=len(tasks),activeTasks=sum(t['status']=='running' for t in tasks),
            queuedTasks=len(q),blockedTasks=sum(t['status']=='blocked' for t in tasks),
            pendingApprovals=sum(a['status']=='pending' for a in approvals),
            activeAgents=sum(a['status']=='working' for a in agents),idleAgents=sum(a['status']=='idle' for a in agents),
            parkedAgents=sum(a['status']=='parked' for a in agents),
            progressPercent=round(sum(t['progress'] for t in tasks)/len(tasks)) if tasks else None,
            stages=stages,activeSlots=sum(s['state']=='active' for s in slots),totalSlots=len(slots),budget=None,metrics=None,health=None)

    @api.get('/api/v1/events')
    async def stream_events(request:Request,_:Any=Depends(logged_in)):
        last=request.headers.get('last-event-id','')
        cursor=request.query_params.get('since',last)
        seq=0
        if cursor:
            r=store.execute('SELECT seq FROM ui_events WHERE id=?',(cursor,)).fetchone()
            if r:seq=r['seq']
        else:
            r=store.execute('SELECT COALESCE(MAX(seq),0) AS n FROM ui_events').fetchone()
            seq=r['n']
        async def stream():
            nonlocal seq
            while True:
                if await request.is_disconnected():break
                events=store.events_after(seq,100)
                if events:
                    for n,event in events:
                        seq=n
                        yield f'id: {event["id"]}\ndata: {jdump(event)}\n\n'
                else:
                    yield ': keepalive\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

