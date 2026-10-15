"""Server-owned MRLC approvals actions."""
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
    # ====== Approvals: persisted decision journal, optimistic concurrency ======
    @api.get('/api/v1/approvals')
    def list_approvals(projectId:str|None=None,_:Any=Depends(logged_in)):
        if projectId:project(store,projectId)
        return page(store.all('approval',projectId))

    @api.post('/api/v1/approvals',status_code=201)
    def create_approval(body:dict,_:Any=Depends(logged_in)):
        pid=nonempty(body.get('projectId'),'projectId')
        with store.tx():
            project(store,pid,True)
            aid=nonempty(body.get('requestedByAgentId'),'requestedByAgentId')
            agent=obj_or_404(store,'agent',aid)
            if agent['projectId']!=pid:bad('Agent cannot request approval for another project',403)
            evidence=list_of_str(body.get('evidenceArtifactIds',[]),'evidenceArtifactIds')
            for x in evidence:
                if obj_or_404(store,'artifact',x)['projectId']!=pid:bad('Cross-project evidence forbidden',403)
            item=dict(id=ident('approval'),projectId=pid,requestedByAgentId=aid,requestedByLabel=agent['name'],
                title=nonempty(body.get('title'),'title',200),type=str(body.get('type') or 'decision')[:100],
                risk=oneof(body.get('risk','medium'),('low','medium','high'),'risk'),status='pending',
                evidenceArtifactIds=evidence,createdAt=now(),revision=1,
                description=str(body.get('description') or '')[:4000],impact=str(body.get('impact') or '')[:4000],effect=body.get('effect'))
            event_and_audit(store,'owner','approval',item,pid)
            return item

    @api.get('/api/v1/approvals/{approval_id}')
    def get_approval(approval_id:str,_:Any=Depends(logged_in)):
        return obj_or_404(store,'approval',approval_id)

    @api.get('/api/v1/approvals/{approval_id}/decisions')
    def approval_history(approval_id:str,_:Any=Depends(logged_in)):
        obj_or_404(store,'approval',approval_id)
        return page([h for h in store.all('approvalRecord') if h['approvalId']==approval_id])

    @api.post('/api/v1/approvals/{approval_id}/decisions')
    def decide_approval(approval_id:str,body:dict,_:Any=Depends(logged_in)):
        with store.tx():
            a=obj_or_404(store,'approval',approval_id)
            project(store,a['projectId'],True)
            if a['status']!='pending':bad('Approval already resolved',409)
            if body.get('expectedRevision')!=a['revision']:bad('Approval revision conflict',409)
            decision=oneof(body.get('decision'),('approve','reject','reduce_scope','request_details'),'decision')
            override=body.get('scopeOverride')
            if decision=='reduce_scope':
                if not isinstance(override,dict) or not override:bad('Scope override required for reduced-scope decision')
                if any(not isinstance(v,(str,int,float,bool)) for v in override.values()):bad('Scope override fields must be scalars')
            elif override is not None:bad('Scope override only valid for reduce_scope')
            record=dict(id=ident('decision'),approvalId=approval_id,decision=decision,
                reason=str(body.get('reason') or '')[:4000],decidedAt=now(),revision=a['revision']+1,
                actor='owner',**({'scopeOverride':override} if override else {}))
            # Typed effects execute in the same transaction as the approval decision.
            if decision in ('approve','reduce_scope') and a.get('effect'):
                effect=a['effect']; kind=effect.get('kind'); payload=effect.get('payload') or {}
                if kind=='grant_role_permission':
                    role=obj_or_404(store,'role',nonempty(payload.get('roleId'),'roleId'))
                    permission=nonempty(payload.get('permission'),'permission',120)
                    role['allowedPermissions']=list(dict.fromkeys(role.get('allowedPermissions',[])+[permission]));role_version(role);store.change('approval','role',role)
                elif kind=='archive_role':
                    role=obj_or_404(store,'role',nonempty(payload.get('roleId'),'roleId'))
                    if any(x['status'] not in ('parked','archived') for x in store.all('agent') if x['roleTemplateId']==role['id']):bad('Role still has active agents',409)
                    role['lifecycle']='archived';role_version(role);store.change('approval','role',role)
                elif kind=='set_project_budget':
                    value=float(payload.get('monthlyLimit',0))
                    if value<0:bad('monthlyLimit must be nonnegative')
                    store.put('budgetPolicy',{'id':a['projectId'],'projectId':a['projectId'],'monthlyLimit':value,'updatedAt':now()},a['projectId'])
                elif kind=='update_project_setting':
                    setting=store.obj('setting',a['projectId']) or {**default_settings(),'id':a['projectId']}
                    key=nonempty(payload.get('key'),'key',80)
                    if key not in ('modelId','contextPolicy','sessionPolicy','frontierEscalation'):bad('Unsupported setting key')
                    setting[key]=payload.get('value');setting['revision']=int(setting.get('revision',1))+1;store.put('setting',setting,a['projectId'])
                else:bad('Unsupported approval effect',400)
            store.put('approvalRecord',record,a['projectId'])
            a['revision']+=1
            if decision=='request_details':
                a['status']='pending'
            else:
                a.update(status='resolved',resolution=decision,resolvedAt=record['decidedAt'])
            event_and_audit(store,'owner','approval',a,a['projectId'])
            # Granting a dangerous permission does NOT call an uncontrolled side effect.
            # A later typed action still checks its own server policy.
            return a

