"""Identity, RBAC and project membership administration."""
from __future__ import annotations
from typing import Any
import re, secrets
from fastapi import Depends, Request
from .common import bad, nonempty, page, logged_in, obj_or_404
from .security import hash_password, login_token_digest, ROLE_PERMISSIONS, require_permission
from .storage import Store, ident, now


def _public_user(user:dict) -> dict:
    """Strip credential material from a user record before returning it."""
    return {k:v for k,v in user.items() if k not in ('passwordHash','loginTokenHash')}


def register(api,store:Store,runtime):
    def admin(request:Request): return require_permission(request,'admin.users')

    @api.get('/api/v1/auth/permissions')
    def permission_catalog(_:Any=Depends(logged_in)):
        return {'roles':{k:sorted(v) for k,v in ROLE_PERMISSIONS.items()}}

    @api.get('/api/v1/auth/users')
    def users(request:Request):
        admin(request)
        return page([_public_user(u) for u in store.all('user')])

    @api.post('/api/v1/auth/users',status_code=201)
    def create_user(request:Request,body:dict):
        actor=admin(request)
        username=nonempty(body.get('username'),'username',64)
        if username==request.app.state.admin_username:
            bad(f'{username} is the configured administrator; its password comes from the environment, not the database')
        if any(u.get('username')==username for u in store.all('user')):bad('Username already exists',409)
        role=body.get('role','viewer')
        if role not in ROLE_PERMISSIONS:bad('Unknown system role')
        password=nonempty(body.get('password'),'password',256)
        if len(password)<12:bad('Password must contain at least 12 characters')
        with store.tx():
            u={'id':ident('user'),'username':username,'displayName':str(body.get('displayName') or username)[:120],
               'role':role,'passwordHash':hash_password(password),'disabled':False,'allProjects':bool(body.get('allProjects',False)),
               'extraPermissions':[],'deniedPermissions':[],'createdAt':now()}
            store.put('user',u)
            store.audit('auth',f'User {username} created by {actor.username}')
            return _public_user(u)

    @api.patch('/api/v1/auth/users/{user_id}')
    def update_user(user_id:str,request:Request,body:dict):
        actor=admin(request)
        with store.tx():
            u=obj_or_404(store,'user',user_id)
            if u.get('username')==request.app.state.admin_username:
                if 'password' in body:
                    bad('The configured administrator password is set by MRLC_ADMIN_PASSWORD, not by the API')
                if {'role','disabled'}&set(body):
                    bad('The configured administrator role and enabled state are fixed; change MRLC_ADMIN_USERNAME instead')
            if 'role' in body:
                if body['role'] not in ROLE_PERMISSIONS:bad('Unknown system role')
                u['role']=body['role']
            if 'displayName' in body:u['displayName']=str(body['displayName'])[:120]
            if 'disabled' in body:
                if user_id==actor.id and bool(body['disabled']):bad('Cannot disable current administrator',409)
                u['disabled']=bool(body['disabled'])
            if 'allProjects' in body:u['allProjects']=bool(body['allProjects'])
            for field in ('extraPermissions','deniedPermissions'):
                if field in body:
                    if not isinstance(body[field],list) or any(not isinstance(x,str) for x in body[field]):bad('Invalid '+field)
                    u[field]=list(dict.fromkeys(body[field]))[:128]
            if 'password' in body:
                password=nonempty(body['password'],'password',256)
                if len(password)<12:bad('Password must contain at least 12 characters')
                u['passwordHash']=hash_password(password)
            store.put('user',u);store.audit('auth',f'User {u["username"]} updated by {actor.username}')
            return _public_user(u)


    @api.get('/api/v1/auth/me/token')
    def token_status(request:Request,principal:Any=Depends(logged_in)):
        user=obj_or_404(store,'user',principal.id)
        is_admin=user.get('username')==request.app.state.admin_username
        return {
            'managedBy':'environment' if is_admin else 'account',
            'configured':bool(request.app.state.admin_token) if is_admin else bool(user.get('loginTokenHash')),
            'canManage':not is_admin,
        }

    @api.post('/api/v1/auth/me/token')
    def rotate_token(request:Request,body:dict,principal:Any=Depends(logged_in)):
        user=obj_or_404(store,'user',principal.id)
        if user.get('username')==request.app.state.admin_username:
            bad('Administrator login token is configured with MRLC_ADMIN_TOKEN in backend/.env',409)
        supplied=body.get('token')
        generated=supplied is None or (isinstance(supplied,str) and not supplied.strip())
        token=secrets.token_urlsafe(32) if generated else str(supplied).strip()
        if len(token)<24 or len(token)>256 or not re.fullmatch(r'[A-Za-z0-9._~-]+',token):
            bad('Token must be 24-256 URL-safe characters: letters, digits, dot, underscore, tilde or hyphen')
        if request.app.state.admin_token and secrets.compare_digest(token,request.app.state.admin_token):
            bad('Token is already in use',409)
        digest=login_token_digest(request,token)
        for other in store.all('user'):
            if other.get('id')!=user['id'] and other.get('loginTokenHash') and secrets.compare_digest(other['loginTokenHash'],digest):
                bad('Token is already in use',409)
        with store.tx():
            user['loginTokenHash']=digest
            user['tokenUpdatedAt']=now()
            store.put('user',user)
            store.audit('auth',f'Login token rotated by {principal.username}')
        # Plaintext is returned exactly once. Only the keyed digest is persisted.
        return {'token':token,'managedBy':'account','configured':True,'canManage':True,'generated':generated}

    @api.delete('/api/v1/auth/me/token',status_code=204)
    def revoke_token(request:Request,principal:Any=Depends(logged_in)):
        user=obj_or_404(store,'user',principal.id)
        if user.get('username')==request.app.state.admin_username:
            bad('Administrator login token is configured with MRLC_ADMIN_TOKEN in backend/.env',409)
        with store.tx():
            user.pop('loginTokenHash',None)
            user['tokenUpdatedAt']=now()
            store.put('user',user)
            store.audit('auth',f'Login token revoked by {principal.username}')
        return None

    @api.get('/api/v1/auth/users/{user_id}/projects')
    def memberships(user_id:str,request:Request):
        admin(request);obj_or_404(store,'user',user_id)
        return page([m for m in store.all('membership') if m.get('userId')==user_id])

    @api.put('/api/v1/auth/users/{user_id}/projects/{project_id}')
    def grant_project(user_id:str,project_id:str,request:Request):
        actor=admin(request);obj_or_404(store,'user',user_id);obj_or_404(store,'project',project_id)
        with store.tx():
            mid=f'{user_id}:{project_id}'
            m={'id':mid,'userId':user_id,'projectId':project_id,'createdAt':now()}
            store.put('membership',m,project_id);store.audit('auth',f'Project access granted by {actor.username}',project_id)
            return m

    @api.delete('/api/v1/auth/users/{user_id}/projects/{project_id}',status_code=204)
    def revoke_project(user_id:str,project_id:str,request:Request):
        actor=admin(request)
        with store.tx():
            store.delete('membership',f'{user_id}:{project_id}');store.audit('auth',f'Project access revoked by {actor.username}',project_id)
        return None
