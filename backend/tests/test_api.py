"""End-to-end HTTP/SQLite/backend execution tests, not mocked browser events."""
import os
os.environ.setdefault('MRLC_ADMIN_PASSWORD','test-password-very-long')
os.environ.setdefault('MRLC_ADMIN_TOKEN','test-admin-token-very-long-and-independent-12345')
os.environ.setdefault('MRLC_SESSION_SECRET','test-signing-key-longer-than-thirty-two-chars-12345')
import time
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from app.main import create_app

PASSWORD='test-password-very-long'
SECRET='test-signing-key-longer-than-thirty-two-chars-12345'
ADMIN_TOKEN='test-admin-token-very-long-and-independent-12345'

class WorkingExecutor:
    available=True
    model='test-model'
    async def complete(self, system_prompt, messages):
        assert 'Role boundaries' in system_prompt
        assert messages and messages[0]['role']=='user'
        return '# Genuine test executor response\nProof draft with assumptions.'

@pytest.fixture
def setup(tmp_path):
    path=str(tmp_path/'data.sqlite')
    upload=str(tmp_path/'files')
    app=create_app(db_path=path,artifact_dir=upload,password=PASSWORD,secret=SECRET)
    with TestClient(app) as client:
        login=client.post('/api/v1/auth/login',json={'password':PASSWORD})
        assert login.status_code==200, login.text
        yield client,path,upload


def add_project(client,name):
    r=client.post('/api/v1/projects',json={'name':name,'description':'test'})
    assert r.status_code==201,r.text
    return r.json()['id']


def add_role(client):
    r=client.post('/api/v1/roles',json={'name':'Engineer','purpose':'Check assertions',
        'capabilities':['Implement'],'allowedPermissions':['tasks.execute','messages.reply'],
        'lifecycle':'active'})
    assert r.status_code==201,r.text
    return r.json()['id']


def add_agent(client,pid,rid):
    r=client.post(f'/api/v1/projects/{pid}/agents',json={'projectId':pid,'roleTemplateId':rid,'name':'Engineer'})
    assert r.status_code==201,r.text
    return r.json()['id']


def add_task(client,pid,rid=None,deps=None,criteria=None):
    r=client.post(f'/api/v1/projects/{pid}/tasks',json={'projectId':pid,'title':'A theorem task',
        'description':'Prove a bound','assigneeRoleId':rid,'dependencyIds':deps or [],
        'acceptanceCriteria':criteria or [{'description':'Verified proof'}]})
    assert r.status_code==201,r.text
    return r.json()


def test_auth_and_server_authority(setup):
    client,_,_=setup
    assert client.post('/api/v1/auth/login',json={'password':'wrong'}).status_code==401
    pid=add_project(client,'Research')
    t=add_task(client,pid)
    assert client.patch('/api/v1/tasks/'+t['id'],json={'status':'accepted'}).status_code==403
    assert client.post('/api/v1/tasks/'+t['id']+'/review').status_code==409
    assert client.post('/api/v1/tasks/'+t['id']+'/dispatch').status_code==503
    snap=client.get(f'/api/v1/projects/{pid}/overview').json()
    assert snap['taskCount']==1 and snap['progressPercent']==0
    assert client.get('/api/v1/runtime/metrics').json() is None
    assert client.get('/api/v1/system/accelerators').json()['items']==[]
    assert client.get('/api/v1/capabilities').json()['physicalGpuControlSupported'] is False
    assert len(client.get('/api/v1/system/logs').json()['items'])>0


def test_cross_project_dependency_attachment_and_cycle(setup):
    client,_,_=setup
    a=add_project(client,'A'); b=add_project(client,'B')
    r=add_role(client)
    t=add_task(client,a,r)
    cross=client.post(f'/api/v1/projects/{b}/tasks',json={'projectId':b,'title':'Cross','dependencyIds':[t['id']]})
    assert cross.status_code==409
    one=add_task(client,a,r)
    two=add_task(client,a,r,deps=[one['id']])
    assert client.patch('/api/v1/tasks/'+one['id'],json={'dependencyIds':[two['id']]}).status_code==409
    ch=client.post(f'/api/v1/projects/{b}/channels',json={'name':'report','purpose':'','contextPolicy':'task'}).json()['id']
    uploaded=client.post(f'/api/v1/projects/{a}/artifacts',files={'file':('a.txt',b'hello-world','text/plain')})
    assert uploaded.status_code==201,uploaded.text
    art=uploaded.json()
    assert client.post(f'/api/v1/channels/{ch}/messages',json={'content':'Cross','artifactIds':[art['id']]}).status_code==403
    assert client.post(f'/api/v1/projects/{b}/artifacts',data={'taskId':t['id']},files={'file':('x.txt',b'x','text/plain')}).status_code==403


def test_artifact_integrity_versions_reopen_and_acceptance(setup):
    client,path,uploads=setup
    pid=add_project(client,'Persistence')
    t=add_task(client,pid)
    artifact=client.post(f'/api/v1/projects/{pid}/artifacts',data={'taskId':t['id']},
             files={'file':('proof.tex',b'\\begin{proof}X\\end{proof}','text/plain')}).json()
    assert artifact['checksum'] and artifact['contentAvailable']
    blob=client.get(f'/api/v1/artifacts/{artifact["id"]}/content')
    assert blob.content==b'\\begin{proof}X\\end{proof}'
    updated=client.post(f'/api/v1/projects/{pid}/artifacts',data={'artifactId':artifact['id']},
            files={'file':('proof.tex',b'updated','text/plain')})
    assert updated.status_code==201,updated.text
    assert updated.json()['version']=='v2'
    versions=client.get(f'/api/v1/artifacts/{artifact["id"]}/versions').json()['items']
    assert len(versions)==2
    assert client.get(f'/api/v1/artifacts/{artifact["id"]}/content?versionId={versions[0]["id"]}').content==b'\\begin{proof}X\\end{proof}'
    assert client.get(f'/api/v1/artifacts/{artifact["id"]}/content').content==b'updated'
    criterion=t['acceptanceCriteria'][0]['id']
    assert client.post(f'/api/v1/tasks/{t["id"]}/criteria/{criterion}/verify',json={'evidenceArtifactIds':[artifact['id']]}).status_code==200
    assert client.post(f'/api/v1/tasks/{t["id"]}/review').status_code==200
    assert client.post(f'/api/v1/tasks/{t["id"]}/accept',json={'reason':'Proof checked'}).status_code==200
    snap=client.get(f'/api/v1/projects/{pid}/overview').json()
    assert snap['progressPercent']==100 and snap['stages'][0]['verified'] is True
    assert client.get(f'/api/v1/projects/{pid}/channels').json()['items'][0]['name']=='Report channel'
    assert client.get(f'/api/v1/artifacts/{artifact["id"]}').json()['status']=='verified'
    app2=create_app(db_path=path,artifact_dir=uploads,password=PASSWORD,secret=SECRET)
    try:
        assert app2.state.store.obj('task',t['id'])['status']=='accepted'
        assert app2.state.store.obj('artifact',artifact['id'])['checksum']==updated.json()['checksum']
    finally:app2.state.store.close()


def test_approval_revision_history_and_immutable_project(setup):
    client,_,_=setup
    pid=add_project(client,'Decisions');rid=add_role(client);aid=add_agent(client,pid,rid)
    r=client.post('/api/v1/approvals',json={'projectId':pid,'requestedByAgentId':aid,'title':'Spend $20',
                                           'type':'budget','risk':'high','evidenceArtifactIds':[]})
    assert r.status_code==201,r.text
    ap=r.json()
    attempt=client.post(f'/api/v1/approvals/{ap["id"]}/decisions',json={'decision':'reduce_scope','expectedRevision':1})
    assert attempt.status_code==400
    first=client.post(f'/api/v1/approvals/{ap["id"]}/decisions',json={'decision':'request_details','expectedRevision':1,'reason':'Provide cost'})
    assert first.status_code==200 and first.json()['revision']==2
    assert client.post(f'/api/v1/approvals/{ap["id"]}/decisions',json={'decision':'approve','expectedRevision':1}).status_code==409
    second=client.post(f'/api/v1/approvals/{ap["id"]}/decisions',json={'decision':'reduce_scope','expectedRevision':2,
           'scopeOverride':{'maxSpendUSD':10},'reason':'Capped'})
    assert second.status_code==200 and second.json()['status']=='resolved'
    history=client.get(f'/api/v1/approvals/{ap["id"]}/decisions').json()['items']
    assert len(history)==2 and history[1]['scopeOverride']=={'maxSpendUSD':10}
    assert client.post(f'/api/v1/approvals/{ap["id"]}/decisions',json={'decision':'reject','expectedRevision':3}).status_code==409
    assert client.post(f'/api/v1/projects/{pid}/archive',json={'reason':'Done'}).status_code==200
    assert client.post(f'/api/v1/projects/{pid}/channels',json={'name':'X'}).status_code==409


def test_settings_log_export_and_auth_isolation(setup,tmp_path):
    client,_,_=setup
    pid=add_project(client,'S')
    settings=client.get(f'/api/v1/projects/{pid}/settings').json()
    patch={**settings,'expectedRevision':settings['revision']}
    patch['modelId']='my-real-model'
    new=client.patch(f'/api/v1/projects/{pid}/settings',json=patch)
    assert new.status_code==200,new.text
    assert new.json()['revision']==2
    assert client.patch(f'/api/v1/projects/{pid}/settings',json=patch).status_code==409
    export=client.post('/api/v1/system/logs/export',json={'format':'jsonl','filter':{'projectId':pid}})
    assert export.status_code==200,export.text
    dl=client.get(export.json()['downloadUrl'])
    assert dl.status_code==200 and b'project' in dl.content
    with TestClient(create_app(db_path=str(tmp_path/'isolated-test.db'),
                  artifact_dir=str(tmp_path/'isolated-artifacts'),password=PASSWORD,secret=SECRET)) as separate:
        assert separate.get('/api/v1/projects').status_code==401


def test_live_executor_dispatch_saves_artifact(tmp_path):
    app=create_app(db_path=str(tmp_path/'live.sqlite'),artifact_dir=str(tmp_path/'artifacts'),
                   password=PASSWORD,secret=SECRET,executor=WorkingExecutor())
    with TestClient(app) as c:
        assert c.post('/api/v1/auth/login',json={'password':PASSWORD}).status_code==200
        pid=add_project(c,'Inference');rid=add_role(c);add_agent(c,pid,rid)
        t=add_task(c,pid,rid)
        dispatched=c.post('/api/v1/tasks/'+t['id']+'/dispatch')
        assert dispatched.status_code==202,dispatched.text
        end=time.monotonic()+8
        while time.monotonic()<end:
            jobs=c.get('/api/v1/runtime/jobs').json()['items']
            if jobs and jobs[0]['status'] in ('completed','failed'):break
            time.sleep(.1)
        assert jobs[0]['status']=='completed',jobs
        updated=c.get('/api/v1/tasks/'+t['id']).json()
        assert updated['status']=='review' and updated['evidenceStatus']=='partial'
        arts=c.get(f'/api/v1/projects/{pid}/artifacts').json()['items']
        assert arts and b'Genuine test executor' in c.get(f'/api/v1/artifacts/{arts[0]["id"]}/content').content
        assert c.get('/api/v1/runtime/sessions').json()['items']==[]


def test_agent_replies_only_when_requested_and_authorized(tmp_path):
    app=create_app(db_path=str(tmp_path/'chat.sqlite'),artifact_dir=str(tmp_path/'files'),
                   password=PASSWORD,secret=SECRET,executor=WorkingExecutor())
    with TestClient(app) as c:
        c.post('/api/v1/auth/login',json={'password':PASSWORD})
        pid=add_project(c,'Chat');rid=add_role(c);aid=add_agent(c,pid,rid)
        channel=c.post(f'/api/v1/projects/{pid}/channels',json={'name':'research','purpose':'','contextPolicy':'task'}).json()['id']
        assert c.post(f'/api/v1/channels/{channel}/respond',json={'agentId':aid}).status_code==409
        msg=c.post(f'/api/v1/channels/{channel}/messages',json={'content':'Help review this','responseMode':'single'})
        assert msg.status_code==201
        assert len(c.get(f'/api/v1/channels/{channel}/messages').json()['items'])==1
        job=c.post(f'/api/v1/channels/{channel}/respond',json={'agentId':aid})
        assert job.status_code==202,job.text
        end=time.monotonic()+6
        while time.monotonic()<end:
            msgs=c.get(f'/api/v1/channels/{channel}/messages').json()['items']
            if len(msgs)>1:break
            time.sleep(.1)
        assert msgs[-1]['senderAgentId']==aid
        assert 'Genuine test executor response' in msgs[-1]['content']
        assert c.get(f'/api/v1/agents/{aid}/knowledge').json()['status']=='UNKNOWN'


def test_logout_origin_guards_and_server_appearance(setup):
    c,_,_=setup
    assert c.post('/api/v1/auth/login',json={'password':PASSWORD},headers={'Origin':'https://evil.invalid'}).status_code==403
    value={'theme':'soft-dark','motion':'reduced','density':'comfortable','stageAnimation':'static-gold'}
    r=c.patch('/api/v1/users/me/appearance',json=value)
    assert r.status_code==200 and c.get('/api/v1/users/me/appearance').json()==value
    assert c.post('/api/v1/users/me/appearance/reset').json()['theme']=='strata-dark'
    assert c.post('/api/v1/auth/logout').status_code==200
    assert c.get('/api/v1/projects').status_code==401


def test_batch_scheduler_obeys_dependencies(tmp_path):
    app=create_app(db_path=str(tmp_path/'scheduler.sqlite'),artifact_dir=str(tmp_path/'artifacts'),
                   password=PASSWORD,secret=SECRET,executor=WorkingExecutor())
    with TestClient(app) as c:
        c.post('/api/v1/auth/login',json={'password':PASSWORD})
        pid=add_project(c,'Scheduler');rid=add_role(c);add_agent(c,pid,rid)
        a=add_task(c,pid,rid)
        b=add_task(c,pid,rid,deps=[a['id']])
        result=c.post(f'/api/v1/projects/{pid}/schedule')
        assert result.status_code==200,result.text
        assert len(result.json()['queued'])==1
        assert len(result.json()['skipped'])==1
        assert result.json()['skipped'][0]['taskId']==b['id']


def test_actual_openai_compatible_http_transport(monkeypatch):
    import asyncio,json,threading
    from http.server import BaseHTTPRequestHandler,HTTPServer
    from app.executor import OpenAIExecutor
    observed={}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed['path']=self.path
            observed['authorization']=self.headers.get('Authorization')
            observed['body']=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            response=json.dumps({'choices':[{'message':{'content':'Real HTTP transport acknowledged'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(response)))
            self.end_headers();self.wfile.write(response)
        def log_message(self,*args):pass
    http=HTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=http.serve_forever,daemon=True).start()
    try:
        monkeypatch.setenv('MRLC_MODEL_BASE_URL',f'http://127.0.0.1:{http.server_port}/v1')
        monkeypatch.setenv('MRLC_MODEL_ID','test-model')
        monkeypatch.setenv('MRLC_MODEL_API_KEY','test-provider-secret')
        output=asyncio.run(OpenAIExecutor().complete('You are an engineer',[{'role':'user','content':'Test'}]))
        assert output=='Real HTTP transport acknowledged'
        assert observed['path']=='/v1/chat/completions'
        assert observed['authorization']=='Bearer test-provider-secret'
        assert observed['body']['model']=='test-model'
        assert observed['body']['stream'] is False
    finally:http.shutdown();http.server_close()

def test_phase8_rbac_project_scope_and_revocation(tmp_path):
    app=create_app(db_path=str(tmp_path/'rbac.sqlite'),artifact_dir=str(tmp_path/'files'),password=PASSWORD,secret=SECRET)
    with TestClient(app) as admin:
        admin.post('/api/v1/auth/login',json={'password':PASSWORD})
        a=add_project(admin,'A'); b=add_project(admin,'B')
        u=admin.post('/api/v1/auth/users',json={'username':'viewer1','password':'viewer-password-long','role':'viewer'});assert u.status_code==201,u.text
        uid=u.json()['id']
        assert admin.put(f'/api/v1/auth/users/{uid}/projects/{a}').status_code==200
        with TestClient(app) as viewer:
            assert viewer.post('/api/v1/auth/login',json={'username':'viewer1','password':'viewer-password-long'}).status_code==200
            visible=viewer.get('/api/v1/projects').json()['items']
            assert [x['id'] for x in visible]==[a]
            assert viewer.get(f'/api/v1/projects/{b}').status_code==403
            assert viewer.post('/api/v1/projects',json={'name':'forbidden'}).status_code==403
            assert admin.delete(f'/api/v1/auth/users/{uid}/projects/{a}').status_code==204
            assert viewer.get(f'/api/v1/projects/{a}').status_code==403


def test_phase8_scheduler_context_cancel_retry_and_audit(tmp_path):
    app=create_app(db_path=str(tmp_path/'sched8.sqlite'),artifact_dir=str(tmp_path/'files'),password=PASSWORD,secret=SECRET,executor=WorkingExecutor())
    with TestClient(app) as c:
        c.post('/api/v1/auth/login',json={'password':PASSWORD})
        pid=add_project(c,'P8');rid=add_role(c);add_agent(c,pid,rid);t=add_task(c,pid,rid)
        d=c.post(f'/api/v1/tasks/{t["id"]}/dispatch');assert d.status_code==202,d.text
        job=d.json()['jobId']
        ctx=c.get(f'/api/v1/runtime/jobs/{job}/context');assert ctx.status_code==200 and ctx.json()['taskId']==t['id']
        # A queued/running job always has a persisted context snapshot. Cancellation is server-owned.
        cancel=c.post(f'/api/v1/runtime/jobs/{job}/cancel');assert cancel.status_code in (200,409),cancel.text
        end=time.monotonic()+5
        while time.monotonic()<end:
            state=next(x for x in c.get('/api/v1/runtime/jobs').json()['items'] if x['id']==job)
            if state['status'] in ('completed','cancelled','failed'):break
            time.sleep(.05)
        if state['status'] in ('cancelled','failed'):
            retry=c.post(f'/api/v1/runtime/jobs/{job}/retry');assert retry.status_code==202,retry.text
            assert retry.json()['providerCheckpointRestored'] is False
        verify=c.get('/api/v1/system/audit/verify');assert verify.status_code==200 and verify.json()['valid'] is True
        export=c.post('/api/v1/system/logs/export',json={'format':'audit_bundle','filter':{}});assert export.status_code==200,export.text
        bundle=c.get(export.json()['downloadUrl']);assert bundle.status_code==200 and bundle.json()['signature']


def test_phase8_typed_approval_and_reporter(setup):
    c,_,_=setup
    pid=add_project(c,'Governed');rid=add_role(c);aid=add_agent(c,pid,rid);add_task(c,pid,rid)
    ap=c.post('/api/v1/approvals',json={'projectId':pid,'requestedByAgentId':aid,'title':'Grant export','risk':'medium',
        'effect':{'kind':'grant_role_permission','payload':{'roleId':rid,'permission':'artifacts.publish'}}})
    assert ap.status_code==201,ap.text
    decided=c.post(f'/api/v1/approvals/{ap.json()["id"]}/decisions',json={'decision':'approve','expectedRevision':1,'reason':'approved'})
    assert decided.status_code==200,decided.text
    role=next(x for x in c.get('/api/v1/roles').json()['items'] if x['id']==rid)
    assert 'artifacts.publish' in role['allowedPermissions']
    coord=c.get(f'/api/v1/projects/{pid}/coordination');assert coord.status_code==200 and 'findings' in coord.json()
    report=c.post(f'/api/v1/projects/{pid}/reports');assert report.status_code==201,report.text
    art=report.json();assert art['mimeType']=='text/markdown'
    assert b'MRLC project report' in c.get(f'/api/v1/artifacts/{art["id"]}/content').content


def test_multiple_allowed_origins(monkeypatch,tmp_path):
    monkeypatch.setenv('MRLC_ALLOWED_ORIGINS','http://localhost:5173,https://mrlc.example.test')
    from app.main import create_app
    from fastapi.testclient import TestClient
    app=create_app(db_path=str(tmp_path/'multi-origin.sqlite3'),artifact_dir=str(tmp_path/'artifacts'),password=PASSWORD,secret=SECRET)
    with TestClient(app) as c:
        for origin in ('http://localhost:5173','https://mrlc.example.test'):
            response=c.post('/api/v1/auth/login',json={'password':PASSWORD},headers={'Origin':origin})
            assert response.status_code==200, (origin,response.text)
        response=c.post('/api/v1/auth/login',json={'password':PASSWORD},headers={'Origin':'https://evil.invalid'})
        assert response.status_code==403


def test_password_and_dedicated_login_tokens(tmp_path):
    app=create_app(db_path=str(tmp_path/'token.sqlite3'),artifact_dir=str(tmp_path/'artifacts'),
                   password=PASSWORD,secret=SECRET,admin_token=ADMIN_TOKEN)
    with TestClient(app) as c:
        # Password and token are independent credentials with the same resulting session authority.
        assert c.post('/api/v1/auth/login',json={'password':PASSWORD}).status_code==200
        status=c.get('/api/v1/auth/me/token')
        assert status.status_code==200 and status.json()=={'managedBy':'environment','configured':True,'canManage':False}
        assert c.post('/api/v1/auth/me/token',json={}).status_code==409
        c.post('/api/v1/auth/logout')
        assert c.get('/api/v1/auth/token',headers={'X-MRLC-Token':PASSWORD}).status_code==401
        assert c.post('/api/v1/auth/login',json={'password':ADMIN_TOKEN}).status_code==401
        assert c.get('/api/v1/auth/token',headers={'X-MRLC-Token':ADMIN_TOKEN}).status_code==200
        assert c.get('/api/v1/auth/me').json()['username']=='owner'

        created=c.post('/api/v1/auth/users',json={'username':'alice','displayName':'Alice','role':'viewer','password':'alice-password-long-enough'})
        assert created.status_code==201,created.text
        user_id=created.json()['id']
        assert 'passwordHash' not in created.json() and 'loginTokenHash' not in created.json()
        c.post('/api/v1/auth/logout')

        # A non-admin can sign in with its password, then create and manage its own token.
        signed=c.post('/api/v1/auth/login',json={'username':'alice','password':'alice-password-long-enough'})
        assert signed.status_code==200,signed.text
        status=c.get('/api/v1/auth/me/token').json()
        assert status=={'managedBy':'account','configured':False,'canManage':True}
        generated=c.post('/api/v1/auth/me/token',json={})
        assert generated.status_code==200,generated.text
        token1=generated.json()['token']
        assert len(token1)>=24 and generated.json()['generated'] is True
        stored=app.state.store.obj('user',user_id)
        assert stored.get('loginTokenHash') and stored['loginTokenHash']!=token1
        c.post('/api/v1/auth/logout')
        assert c.get('/api/v1/auth/token',headers={'X-MRLC-Token':token1}).status_code==200

        custom='alice_custom_login_token_1234567890'
        rotated=c.post('/api/v1/auth/me/token',json={'token':custom})
        assert rotated.status_code==200 and rotated.json()['token']==custom and rotated.json()['generated'] is False
        c.post('/api/v1/auth/logout')
        assert c.get('/api/v1/auth/token',headers={'X-MRLC-Token':token1}).status_code==401
        assert c.get('/api/v1/auth/token',headers={'X-MRLC-Token':custom}).status_code==200
        assert c.delete('/api/v1/auth/me/token').status_code==204
        c.post('/api/v1/auth/logout')
        assert c.get('/api/v1/auth/token',headers={'X-MRLC-Token':custom}).status_code==401
