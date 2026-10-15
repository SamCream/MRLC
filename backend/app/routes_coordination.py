"""Deterministic server-side coordination and reporting.

Coordinator findings are derived from persisted state; they are not LLM guesses. Reporter
materializes a traceable Markdown artifact and posts it to the report channel.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Any
from fastapi import Depends, Request
from .common import bad,page,project,obj_or_404,logged_in
from .storage import Store,ident,now


def register(api,store:Store,runtime):
    @api.get('/api/v1/projects/{project_id}/coordination')
    def coordination(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        tasks=store.all('task',project_id); agents=store.all('agent',project_id); approvals=store.all('approval',project_id)
        findings=[]
        by_id={t['id']:t for t in tasks}
        for t in tasks:
            missing=[d for d in t.get('dependencyIds',[]) if not by_id.get(d) or by_id[d].get('status')!='accepted']
            if missing and t.get('status') not in ('accepted','running'):
                findings.append({'kind':'dependency_wait','severity':'medium','taskId':t['id'],'detail':'Waiting for dependencies: '+', '.join(missing)})
            rid=t.get('assigneeRoleId')
            if rid and t.get('status') in ('todo','blocked','failed') and not any(a['roleTemplateId']==rid and a['status']=='idle' for a in agents):
                findings.append({'kind':'no_idle_agent','severity':'high','taskId':t['id'],'detail':'No idle agent available for assigned role'})
        for a in approvals:
            if a.get('status')=='pending': findings.append({'kind':'approval_wait','severity':'high' if a.get('risk')=='high' else 'medium','approvalId':a['id'],'detail':a['title']})
        return {'projectId':project_id,'generatedAt':now(),'findings':findings,'readyTaskIds':[t['id'] for t in tasks if t.get('status') in ('todo','failed') and all((by_id.get(d) or {}).get('status')=='accepted' for d in t.get('dependencyIds',[]))]}

    @api.post('/api/v1/projects/{project_id}/reports',status_code=201)
    def generate_report(project_id:str,request:Request,_:Any=Depends(logged_in)):
        p=project(store,project_id);tasks=store.all('task',project_id);approvals=store.all('approval',project_id)
        lines=[f'# {p["name"]} — MRLC project report','',f'Generated: {now()}','', '## Tasks']
        for t in tasks: lines.append(f'- **{t["title"]}** — {t["status"]} — {t.get("progress",0)}%')
        lines+=['','## Pending approvals']
        pending=[a for a in approvals if a.get('status')=='pending']
        lines.extend([f'- {a["title"]} ({a.get("risk","medium")})' for a in pending] or ['- None'])
        lines+=['','## Traceability',f'- Tasks: {len(tasks)}',f'- Artifacts: {len(store.all("artifact",project_id))}',f'- Pending approvals: {len(pending)}']
        content=('\n'.join(lines)+'\n').encode();digest=hashlib.sha256(content).hexdigest();root=Path(request.app.state.artifact_dir);path=root/digest[:2]/digest;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_bytes(content)
        with store.tx():
            art={'id':ident('artifact'),'projectId':project_id,'taskId':None,'name':f'{project_id}-report.md','mimeType':'text/markdown','version':'v1','status':'verified','sizeBytes':len(content),'checksum':digest,'contentAvailable':True,'createdAt':now()}
            ver={'id':ident('version'),'artifactId':art['id'],'version':'v1','sizeBytes':len(content),'checksum':digest,'createdAt':art['createdAt'],'contentAvailable':True}
            store.execute('INSERT INTO artifact_files(version_id,artifact_id,sha256,relative_path,size_bytes,created_at) VALUES(?,?,?,?,?,?)',(ver['id'],art['id'],digest,str(path.relative_to(root)),len(content),ver['createdAt']))
            store.put('artifactVersion',ver,project_id);store.change('reporter','artifact',art,project_id)
            channels=store.all('channel',project_id);report=next((c for c in channels if c.get('name')=='Report channel'),None)
            if not report:
                report={'id':ident('channel'),'projectId':project_id,'name':'Report channel','purpose':'Final reports and milestones','contextPolicy':'task','createdAt':now()};store.change('reporter','channel',report,project_id)
            msg={'id':ident('msg'),'channelId':report['id'],'senderAgentId':None,'senderName':'Reporter','content':'Generated a server-side project report.','createdAt':now(),'artifactIds':[art['id']]}
            store.change('reporter','message',msg,project_id)
            return art
