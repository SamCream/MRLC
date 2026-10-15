"""MRLC API: server-authoritative business logic for the React control room.

Run with scripts/run-backend.sh from the project root after configuring backend/.env.
Paths and shapes follow the Phase 6 frontend API adapter.
"""
from __future__ import annotations
import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
from typing import Any
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from .storage import Store, ident, now
from .security import COOKIE, login as login_cookie, login_with_token, current_principal, permission_for_request, resolve_project_scope
from .executor import OpenAIExecutor
from .common import logged_in


def create_app(*, db_path:str|None=None, artifact_dir:str|None=None, password:str|None=None, secret:str|None=None, admin_token:str|None=None, executor:OpenAIExecutor|None=None) -> FastAPI:
    passwd=password or os.getenv('MRLC_ADMIN_PASSWORD','')
    signing=secret or os.getenv('MRLC_SESSION_SECRET','')
    admin_username=os.getenv('MRLC_ADMIN_USERNAME','owner') or 'owner'
    configured_admin_token=admin_token if admin_token is not None else os.getenv('MRLC_ADMIN_TOKEN','')
    if len(passwd)<12 or len(signing)<32:
        raise RuntimeError('Set MRLC_ADMIN_PASSWORD (>=12 chars) and MRLC_SESSION_SECRET (>=32 chars) in the backend environment')
    if configured_admin_token and len(configured_admin_token)<24:
        raise RuntimeError('MRLC_ADMIN_TOKEN must contain at least 24 characters when configured')
    store=Store(db_path or os.getenv('MRLC_DB_PATH','./var/mrlc.sqlite3'))
    runtime=executor or OpenAIExecutor()
    # Bootstrap the configured administrator identity on a new database. The password
    # itself stays in the environment: no password material is ever written to SQLite
    # for this account, so MRLC_ADMIN_PASSWORD remains the single source of truth.
    if not any(u.get('username')==admin_username for u in store.all('user')):
        with store.tx():
            store.put('user',{'id':'owner','username':admin_username,'displayName':'Owner','role':'administrator',
                'disabled':False,'allProjects':True,'extraPermissions':[],
                'deniedPermissions':[],'createdAt':now()})
            store.audit('auth','Bootstrap administrator created')

    @asynccontextmanager
    async def lifespan(application:FastAPI):
        # A previous process cannot keep an active inference HTTP request or its checkpoint.
        # Mark interrupted jobs as failed, not as falsely resumable.
        with store.tx():
            for a in store.all('agent'):
                if a['status']=='working':
                    a['status']='idle';a['taskId']=None
                    store.change('recovery','agent',a,a['projectId'])
            for slot in store.all('slot'):
                if slot['state']!='free':
                    slot.update(state='free',agentId=None,taskId=None,modelId=None)
                    store.change('recovery','slot',slot)
            for j in store.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
                store.execute("UPDATE jobs SET status='failed',error=?,completed_at=? WHERE id=?",
                    ('Server restarted while provider call was in flight; no replay-safe checkpoint exists',now(),j['id']))
                t=store.obj('task',j['target_id']) if j['kind']=='task' else None
                if t and t['status']=='running':t['status']='failed';store.change('runtime','task',t,t['projectId'],t['id'])
        worker=asyncio.create_task(job_worker(application))
        try:yield
        finally:
            worker.cancel()
            try:await worker
            except asyncio.CancelledError:pass
            store.close()

    api=FastAPI(title='MRLC Backend API',version='0.8.1',lifespan=lifespan)
    api.state.store=store
    api.state.password=passwd
    api.state.admin_username=admin_username
    api.state.admin_token=configured_admin_token
    api.state.secret=signing
    api.state.artifact_dir=artifact_dir or os.getenv('MRLC_ARTIFACT_DIR','./var/artifacts')
    api.state.service_token=os.getenv('MRLC_SERVICE_TOKEN','')
    api.state.executor=runtime
    # Multiple browser origins are supported for dev/LAN/reverse-proxy access.
    # Keep MRLC_ALLOWED_ORIGIN for backward compatibility.
    configured_origins=[]
    for raw in (os.getenv('MRLC_ALLOWED_ORIGINS',''),os.getenv('MRLC_ALLOWED_ORIGIN','')):
        configured_origins.extend(part.strip().rstrip('/') for part in raw.split(',') if part.strip())
    api.state.allowed_origins=set(configured_origins)

    @api.middleware('http')
    async def server_authorization(request:Request,call_next):
        path=request.url.path
        # Unauthenticated entry points: password login and URL-token sign-in.
        if path.startswith('/api/v1/') and path not in ('/api/v1/auth/login','/api/v1/auth/token'):
            try:
                principal=current_principal(request)
                permission=permission_for_request(request.method,path)
                scope=resolve_project_scope(store,path)
                if permission and not principal.allows(permission,scope):
                    return JSONResponse(status_code=403,content={'detail':f'Missing permission: {permission}'})
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code,content={'detail':exc.detail})
        return await call_next(request)

    @api.middleware('http')
    async def browser_safety(request:Request,call_next):
        # No wildcard CORS. Mutations from foreign origins are rejected, even with a cookie.
        if request.method in ('POST','PATCH','PUT','DELETE'):
            origin=request.headers.get('origin','').rstrip('/')
            expected=f'{request.url.scheme}://{request.headers.get("host","")}'
            if origin and origin != expected and origin not in api.state.allowed_origins:
                return JSONResponse(status_code=403,content={'detail':'Cross-origin mutation rejected'})
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='no-store'
        response.headers['X-Frame-Options']='DENY'
        return response

    @api.get('/api/v1/auth/me')
    def whoami(principal:Any=Depends(logged_in)):
        account=store.obj('user',principal.id) or {}
        return {'id':principal.id,'username':principal.username,'displayName':account.get('displayName',principal.username),'role':principal.role,
                'permissions':sorted(principal.permissions),'projectIds':None if principal.project_ids is None else sorted(principal.project_ids)}

    @api.post('/api/v1/auth/login')
    async def auth_login(request:Request,response:Response,body:dict):
        username=body.get('username') or None
        principal=login_cookie(request,response,str(body.get('password','')),username)
        with store.tx():store.audit('auth',f'{principal.username} signed in')
        return {'id':principal.id,'username':principal.username,'role':principal.role}

    @api.get('/api/v1/auth/token')
    async def auth_token(request:Request,response:Response,detail:str=''):
        # The browser reads `/?token=...`, removes it from the address bar immediately,
        # then presents the dedicated login token in this header. Query fallback keeps the
        # backend endpoint directly usable, but passwords are never accepted as tokens.
        header=request.headers.get('x-mrlc-token','')
        query=request.query_params.get('token','')
        token=header or query
        if not token:
            raise HTTPException(401,'Missing token')
        principal=login_with_token(request,response,token)
        with store.tx():store.audit('auth',f'{principal.username} signed in via token')
        return {'authenticated':True}

    @api.post('/api/v1/auth/logout')
    def auth_logout(response:Response,_:Any=Depends(logged_in)):
        response.delete_cookie(COOKIE,path='/')
        return {'ok':True}

    @api.get('/api/v1/capabilities')
    def capabilities(_:Any=Depends(logged_in)):
        return {'backend':'sqlite','databaseDurable':True,'modelExecutorConfigured':runtime.available,
                'agentCheckpointSupported':False,'physicalGpuControlSupported':False,
                'taskDispatchSupported':runtime.available,'simulatedMetrics':False,
                'authorizationMode':'rbac','version':'0.8.1'}

    from . import routes_auth,routes_organization,routes_workspace,routes_artifacts,routes_approvals,routes_runtime,routes_system,routes_coordination
    for routes in (routes_auth,routes_organization,routes_workspace,routes_artifacts,routes_approvals,routes_runtime,routes_system,routes_coordination):
        routes.register(api,store,runtime)

    static_directory=os.getenv('MRLC_STATIC_DIR','').strip()
    if static_directory:
        from fastapi.staticfiles import StaticFiles
        api.mount('/',StaticFiles(directory=static_directory,html=True),name='frontend')
    return api


async def job_worker(application:FastAPI):
    """Lease-bound provider calls. A failed call never turns into an accepted task."""
    s:Store=application.state.store
    executor:OpenAIExecutor=application.state.executor
    max_concurrent=max(1,min(int(os.getenv('MRLC_MAX_CONCURRENT_JOBS','2')),8))
    running:set[asyncio.Task]=set()
    try:
      while True:
        if executor.available and len(running)<max_concurrent:
            with s.tx():
                row=s.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY priority DESC, created_at LIMIT 1").fetchone()
                if row:
                    data=dict(row)
                    lease=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat().replace('+00:00','Z')
                    s.execute("UPDATE jobs SET status='running',started_at=?,lease_expires_at=? WHERE id=?",(now(),lease,data['id']))
                    agent=s.obj('agent',data['agent_id'])
                    if agent:
                        agent['status']='working';agent['taskId']=data['target_id'] if data['kind']=='task' else None
                        s.change('worker','agent',agent,data['project_id'])
                    if data['kind']=='task':
                        task=s.obj('task',data['target_id'])
                        if task:task['status']='running';s.change('worker','task',task,data['project_id'],task['id'])
                    slot={'id':'slot-'+data['id'],'state':'active','agentId':data['agent_id'],
                          'taskId':data['target_id'] if data['kind']=='task' else None,'modelId':executor.model}
                    s.change('worker','slot',slot,data['project_id'])
                    s.emit('job.running',data['project_id'],{'id':data['id']})
                else:data=None
            if data:
                job=asyncio.create_task(run_job(application,data))
                running.add(job);job.add_done_callback(running.discard)
        await asyncio.sleep(0.2)
    finally:
        if running:
            for task in running:task.cancel()
            await asyncio.gather(*running,return_exceptions=True)
            with s.tx():
                for row in s.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
                    s.execute("UPDATE jobs SET status='failed',error=?,completed_at=? WHERE id=?",
                              ('Server shutting down; provider request cancelled without a checkpoint',now(),row['id']))
                    if row['kind']=='task':
                        obj=s.obj('task',row['target_id'])
                        if obj and obj['status']=='running':
                            obj['status']='failed';s.change('worker','task',obj,row['project_id'],obj['id'])


async def run_job(application:FastAPI,job:dict):
    s:Store=application.state.store
    provider:OpenAIExecutor=application.state.executor
    agent=s.obj('agent',job['agent_id'])
    role=s.obj('role',agent['roleTemplateId']) if agent else None
    try:
        if not agent or not role:raise ValueError('Agent/role no longer exists')
        role_prompt=f'You are {role["name"]}. Role boundaries: {role["purpose"]}. State clearly when evidence is unavailable; do not claim to have verified facts you did not check.'
        if job['kind']=='task':
            task=s.obj('task',job['target_id'])
            if not task:raise ValueError('Task removed before execution')
            # Scoped task prompt, never unrestricted global/project context crawling.
            snapshot=s.obj('contextSnapshot',job.get('context_id')) if job.get('context_id') else None
            prompt=f'Task: {task["title"]}\n\n{task["description"]}\n\nAcceptance criteria:\n'+ '\n'.join(c['description'] for c in task['acceptanceCriteria'])
            if snapshot and snapshot.get('artifactIds'):
                prompt+='\n\nServer-authorized evidence artifact ids: '+', '.join(snapshot['artifactIds'])
            if s.execute('SELECT cancel_requested FROM jobs WHERE id=?',(job['id'],)).fetchone()['cancel_requested']:
                raise asyncio.CancelledError()
            result=await provider.complete_result(role_prompt,[{'role':'user','content':prompt}]) if hasattr(provider,'complete_result') else {'content':await provider.complete(role_prompt,[{'role':'user','content':prompt}]),'usageReported':False}
            completion=result['content']
        else:
            channel=s.obj('channel',job['target_id'])
            if not channel:raise ValueError('Channel no longer exists')
            history=[m for m in s.all('message') if m['channelId']==channel['id']][-12:]
            if not history:raise ValueError('No channel history available')
            context=s.obj('contextSnapshot',job.get('context_id')) if job.get('context_id') else None
            context=context or {'artifactIds':[]}
            evidence=[]
            for aid in context.get('artifactIds',[]):
                art=s.obj('artifact',aid)
                if not art or art['projectId']!=job['project_id']:raise ValueError('Cross-project context rejected')
                # Only explicit text evidence. Binary files are never placed inside prompts.
                if art.get('mimeType','').startswith('text/') or art['name'].endswith(('.md','.txt','.tex','.json')):
                    version=[v for v in s.all('artifactVersion') if v['artifactId']==aid][-1:]
                    if version:
                        file_row=s.execute('SELECT relative_path,size_bytes FROM artifact_files WHERE version_id=?',(version[0]['id'],)).fetchone()
                        if file_row and file_row['size_bytes']<=256000:
                            raw=(Path(application.state.artifact_dir)/file_row['relative_path']).read_text(errors='replace')
                            evidence.append(f'Explicit artifact {art["name"]}:\n{raw[:20000]}')
            messages=[{'role':'user','content':f'[{m["senderName"]}] {m["content"]}'} for m in history]
            if evidence:messages.insert(0,{'role':'user','content':'User-authorized task evidence:\n'+'\n'.join(evidence)})
            result=await provider.complete_result(role_prompt,messages) if hasattr(provider,'complete_result') else {'content':await provider.complete(role_prompt,messages),'usageReported':False}
            completion=result['content']
        with s.tx():
            usage={'id':ident('usage'),'projectId':job['project_id'],'jobId':job['id'],'agentId':job['agent_id'],'modelId':result.get('model',getattr(provider,'model',None)),
                   'inputTokens':result.get('inputTokens'),'outputTokens':result.get('outputTokens'),'costUsd':result.get('costUsd'),'usageReported':bool(result.get('usageReported')),'createdAt':now()}
            s.put('usageRecord',usage,job['project_id'])
            if job['kind']=='task':
                content=completion.encode('utf-8')
                digest=hashlib.sha256(content).hexdigest()
                root=Path(application.state.artifact_dir)
                path=root/digest[:2]/digest;path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():path.write_bytes(content)
                task=s.obj('task',job['target_id'])
                art=dict(id=ident('artifact'),projectId=job['project_id'],taskId=task['id'],
                         name=f'{task["id"]}-model-output.md',mimeType='text/markdown',version='v1',
                         status='partial',sizeBytes=len(content),checksum=digest,contentAvailable=True,createdAt=now())
                ver=dict(id=ident('version'),artifactId=art['id'],version='v1',sizeBytes=len(content),
                         checksum=digest,createdAt=art['createdAt'],contentAvailable=True)
                s.execute('INSERT INTO artifact_files(version_id,artifact_id,sha256,relative_path,size_bytes,created_at) VALUES(?,?,?,?,?,?)',
                          (ver['id'],art['id'],digest,str(path.relative_to(root)),len(content),ver['createdAt']))
                s.put('artifactVersion',ver,job['project_id'])
                s.change('worker','artifact',art,job['project_id'],task['id'])
                task['status']='review';task['progress']=min(task['progress'],95)
                task['evidenceStatus']='partial';task['updatedAt']=now()
                s.change('worker','task',task,job['project_id'],task['id'])
                s.execute('UPDATE jobs SET result_artifact_id=? WHERE id=?',(art['id'],job['id']))
            else:
                m=dict(id=ident('msg'),channelId=job['target_id'],senderAgentId=agent['id'],senderName=agent['name'],
                       content=completion,createdAt=now(),artifactIds=[])
                s.change('worker','message',m,job['project_id'])
            s.execute('UPDATE jobs SET status=?,completed_at=? WHERE id=?',('completed',now(),job['id']))
            s.emit('job.completed',job['project_id'],{'id':job['id']})
    except asyncio.CancelledError:
        with s.tx():
            s.execute("UPDATE jobs SET status='cancelled',error=?,completed_at=? WHERE id=?",('Cancelled by operator',now(),job['id']))
            if job['kind']=='task':
                task=s.obj('task',job['target_id'])
                if task:task['status']='todo';task['updatedAt']=now();s.change('worker','task',task,job['project_id'],task['id'])
            s.emit('job.cancelled',job['project_id'],{'id':job['id']})
    except Exception as exc:
        error=str(exc)[:3000]
        with s.tx():
            s.execute('UPDATE jobs SET status=?,error=?,completed_at=? WHERE id=?',('failed',error,now(),job['id']))
            if job['kind']=='task':
                task=s.obj('task',job['target_id'])
                if task:
                    task['status']='failed';task['updatedAt']=now();s.change('worker','task',task,job['project_id'],task['id'])
            s.audit('worker',f'Provider job {job["id"]} failed: {error}',job['project_id'],job['target_id'] if job['kind']=='task' else None,'error')
            s.emit('job.failed',job['project_id'],{'id':job['id']})
    finally:
        with s.tx():
            agent=s.obj('agent',job['agent_id'])
            if agent and agent['status']=='working':
                agent['status']='idle';agent['taskId']=None
                s.change('worker','agent',agent,job['project_id'])
            slot=s.obj('slot','slot-'+job['id'])
            if slot:
                slot.update(state='free',agentId=None,taskId=None,modelId=None)
                s.change('worker','slot',slot,job['project_id'])


# When launched by uvicorn, environment variables must be present before import.
app = create_app()
