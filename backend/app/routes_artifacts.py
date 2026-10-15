"""Server-owned MRLC artifacts actions."""
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
    # ====== Artifacts: byte-preserving content, version history, integrity ======
    @api.get('/api/v1/projects/{project_id}/artifacts')
    def list_artifacts(project_id:str,_:Any=Depends(logged_in)):
        project(store,project_id)
        return page(store.all('artifact',project_id))

    @api.post('/api/v1/projects/{project_id}/artifacts',status_code=201)
    async def upload_artifact(request:Request,project_id:str,file:UploadFile=File(...),
                              taskId:str|None=Form(None),artifactId:str|None=Form(None),_:Any=Depends(logged_in)):
        # Streaming cap instead of blindly loading arbitrary user uploads into RAM.
        max_size=32*1024*1024
        content=bytearray()
        while True:
            chunk=await file.read(1024*1024)
            if not chunk:break
            content.extend(chunk)
            if len(content)>max_size:bad('File larger than 32 MiB limit',413)
        digest=hashlib.sha256(content).hexdigest()
        root=file_root(request)
        shard=root / digest[:2]
        shard.mkdir(parents=True,exist_ok=True)
        path=shard / digest
        if not path.exists():
            temporary=shard / (digest+'.'+secrets.token_hex(8)+'.tmp')
            try:
                temporary.write_bytes(content)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        del content
        with store.tx():
            project(store,project_id,True)
            if taskId:
                task=obj_or_404(store,'task',taskId)
                if task['projectId']!=project_id:bad('Artifact task belongs to another project',403)
            if artifactId:
                art=obj_or_404(store,'artifact',artifactId)
                if art['projectId']!=project_id:bad('Cross-project artifact version forbidden',403)
                existing=store.all('artifactVersion')
                number=1+len([v for v in existing if v['artifactId']==artifactId])
            else:
                art=dict(id=ident('artifact'),projectId=project_id,taskId=taskId,
                    name=Path(file.filename or 'unnamed').name[:255],mimeType=(file.content_type or 'application/octet-stream'),
                    version='v0',status='partial',createdAt=now(),contentAvailable=True)
                number=1
            v=dict(id=ident('version'),artifactId=art['id'],version=f'v{number}',sizeBytes=path.stat().st_size,
                checksum=digest,createdAt=now(),contentAvailable=True)
            store.execute('INSERT INTO artifact_files(version_id,artifact_id,sha256,relative_path,size_bytes,created_at) VALUES(?,?,?,?,?,?)',
                          (v['id'],art['id'],digest,str(path.relative_to(root)),v['sizeBytes'],v['createdAt']))
            store.put('artifactVersion',v,project_id)
            art.update(name=Path(file.filename or art.get('name') or 'unnamed').name[:255],
                       mimeType=(file.content_type or art.get('mimeType') or 'application/octet-stream'),
                       version=v['version'],sizeBytes=v['sizeBytes'],checksum=digest,contentAvailable=True,createdAt=v['createdAt'])
            if taskId:art['taskId']=taskId
            event_and_audit(store,'owner','artifact',art,project_id,taskId)
            return art

    @api.get('/api/v1/artifacts/{artifact_id}')
    def get_artifact(artifact_id:str,_:Any=Depends(logged_in)):
        return obj_or_404(store,'artifact',artifact_id)

    @api.get('/api/v1/artifacts/{artifact_id}/versions')
    def artifact_versions(artifact_id:str,_:Any=Depends(logged_in)):
        obj_or_404(store,'artifact',artifact_id)
        return page([v for v in store.all('artifactVersion') if v['artifactId']==artifact_id])

    @api.get('/api/v1/artifacts/{artifact_id}/evidence')
    def artifact_evidence(artifact_id:str,_:Any=Depends(logged_in)):
        obj_or_404(store,'artifact',artifact_id)
        return page([e for e in store.all('evidenceLink') if e['artifactId']==artifact_id])

    @api.post('/api/v1/artifacts/{artifact_id}/evidence',status_code=201)
    def link_evidence(artifact_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            art=obj_or_404(store,'artifact',artifact_id)
            project(store,art['projectId'],True)
            kind=oneof(body.get('kind'),('source','run','task','decision'),'evidence kind')
            rid=nonempty(body.get('referenceId'),'referenceId')
            target_type={'task':'task','decision':'approval','run':'job'}.get(kind)
            if target_type=='job':
                row=store.execute('SELECT project_id FROM jobs WHERE id=?',(rid,)).fetchone()
                if not row:bad('Job not found',404)
                if row['project_id']!=art['projectId']:bad('Cross-project evidence forbidden',403)
            elif target_type:
                target=obj_or_404(store,target_type,rid)
                if target.get('projectId')!=art['projectId']:bad('Cross-project evidence forbidden',403)
            link=dict(id=ident('evidence'),artifactId=artifact_id,kind=kind,referenceId=rid,
                      description=str(body.get('description') or '')[:2000],createdAt=now())
            event_and_audit(store,'owner','evidenceLink',link,art['projectId'])
            return link

    @api.get('/api/v1/artifacts/{artifact_id}/content')
    def artifact_content(request:Request,artifact_id:str,versionId:str|None=None,_:Any=Depends(logged_in)):
        art=obj_or_404(store,'artifact',artifact_id)
        if versionId:
            ver=obj_or_404(store,'artifactVersion',versionId)
            if ver['artifactId']!=artifact_id:bad('Version belongs to a different artifact',403)
        else:
            versions=[v for v in store.all('artifactVersion') if v['artifactId']==artifact_id]
            if not versions:bad('No downloadable content',404)
            ver=versions[-1]
        row=store.execute('SELECT relative_path FROM artifact_files WHERE version_id=?',(ver['id'],)).fetchone()
        if not row:bad('Artifact file not available',404)
        root=file_root(request).resolve()
        path=(root / row['relative_path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():bad('Artifact file missing',404)
        from fastapi.responses import FileResponse
        from urllib.parse import quote
        return FileResponse(path,media_type=art.get('mimeType') or 'application/octet-stream',
                            headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(art['name'])})

