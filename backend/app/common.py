"""Shared validation and state guards for all MRLC backend routes."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from fastapi import Request, HTTPException
from .storage import Store,ident,now,jdump
from .security import require_owner

def bad(message: str, status: int=400):
    raise HTTPException(status_code=status,detail=message)


def nonempty(value: Any, field: str, limit=200) -> str:
    if not isinstance(value,str) or not value.strip() or len(value)>limit:bad(f'{field} must be nonempty and at most {limit} characters')
    return value.strip()


def oneof(value:Any,options,field):
    if value not in options:bad('Invalid '+field)
    return value


def expect(data:Any) -> dict:
    if not isinstance(data,dict):bad('JSON object required')
    return data


def list_of_str(value,field,max_len=64):
    if not isinstance(value,list) or len(value)>max_len or any(not isinstance(x,str) or not x for x in value):bad(f'Invalid {field}')
    return list(dict.fromkeys(value))


def page(items, cursor=None):
    return {'items':items,'nextCursor':cursor}


def obj_or_404(s:Store,kind:str,ident_:str):
    obj=s.obj(kind,ident_)
    if not obj:bad(f'{kind} not found',404)
    return obj


def project(s:Store,pid:str,write=False):
    p=obj_or_404(s,'project',pid)
    if write and p['status']=='archived':bad('Archived project is read-only',409)
    return p


def role_version(role:dict):
    role['version']='v'+str(int(role['version'].removeprefix('v'))+1)


def file_root(request:Request):
    root=Path(request.app.state.artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def server(request:Request) -> Store:
    return request.app.state.store


def logged_in(request:Request):
    return require_owner(request)


def default_settings():
    return {'modelId':'local-default','contextPolicy':'task','sessionPolicy':'resumable','frontierEscalation':'ask','revision':1}


def validate_settings(data:dict):
    nonempty(data.get('modelId'),'modelId',128)
    oneof(data.get('contextPolicy'),('task','project'),'contextPolicy')
    oneof(data.get('sessionPolicy'),('resumable','ephemeral'),'sessionPolicy')
    oneof(data.get('frontierEscalation'),('ask','budget','disabled'),'frontierEscalation')


def accepted_deps(s:Store,task:dict):
    return all((s.obj('task', dep) or {}).get('status')=='accepted' for dep in task['dependencyIds'])


def event_and_audit(s:Store,source,kind,item,project_id=None,task_id=None):
    return s.change(source,kind,item,project_id,task_id)


