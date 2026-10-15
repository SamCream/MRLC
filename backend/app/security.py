"""Server-side authentication and RBAC.

The browser never decides whether an action is authorized. Sessions carry only a signed
subject id; roles, permissions and project memberships are reloaded from SQLite on every
request so revocation takes effect immediately.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, secrets, time
from dataclasses import dataclass
from fastapi import HTTPException, Request, Response

COOKIE='mrlc_session'
TTL=12*3600
PBKDF2_ROUNDS=310_000

ROLE_PERMISSIONS={
    'administrator': {'*','admin.users'},
    'operator': {
        'projects.read','projects.write','tasks.read','tasks.write','tasks.execute',
        'workspace.read','workspace.write','artifacts.read','artifacts.write',
        'approvals.read','approvals.decide','runtime.read','runtime.control',
        'roles.read','agents.read','agents.control','system.read','logs.read','settings.write'
    },
    'reviewer': {'projects.read','tasks.read','workspace.read','artifacts.read','approvals.read','approvals.decide','logs.read'},
    'viewer': {'projects.read','tasks.read','workspace.read','artifacts.read','approvals.read','runtime.read','roles.read','agents.read','system.read','logs.read'},
}

@dataclass(frozen=True)
class Principal:
    id:str
    username:str
    role:str
    permissions:frozenset[str]
    project_ids:frozenset[str]|None
    auth_type:str='session'

    def allows(self, permission:str, project_id:str|None=None) -> bool:
        if '*' not in self.permissions and permission not in self.permissions:
            return False
        return self.project_ids is None or project_id is None or project_id in self.project_ids


def _secret(request:Request): return request.app.state.secret.encode()

def _signed(request:Request,payload:dict):
    encoded=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
    mac=hmac.new(_secret(request),encoded.encode(),hashlib.sha256).hexdigest()
    return encoded+'.'+mac

def sign(request:Request,sub:str):
    return _signed(request,{'sub':sub,'exp':int(time.time())+TTL,'nonce':secrets.token_hex(8)})

def verify_token(request:Request,token:str):
    try:
        payload,mac=token.rsplit('.',1)
        good=hmac.new(_secret(request),payload.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac,good): return None
        parsed=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        return parsed if parsed.get('exp',0)>time.time() else None
    except (ValueError,TypeError,KeyError,json.JSONDecodeError): return None

def hash_password(password:str,salt:str|None=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),PBKDF2_ROUNDS).hex()
    return f'pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${digest}'

def verify_password(password:str,encoded:str):
    try:
        algo,rounds,salt,digest=encoded.split('$',3)
        if algo!='pbkdf2_sha256': return False
        candidate=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),int(rounds)).hex()
        return hmac.compare_digest(candidate,digest)
    except Exception:return False

def _principal_from_user(request:Request,user:dict,auth_type='session'):
    if user.get('disabled'): raise HTTPException(403,'Account disabled')
    role=user.get('role','viewer')
    perms=set(ROLE_PERMISSIONS.get(role,set()))|set(user.get('extraPermissions',[]))
    denied=set(user.get('deniedPermissions',[])); perms-=denied
    memberships=request.app.state.store.all('membership')
    scoped={m['projectId'] for m in memberships if m.get('userId')==user['id']}
    project_ids=None if role=='administrator' or user.get('allProjects') else frozenset(scoped)
    return Principal(user['id'],user['username'],role,frozenset(perms),project_ids,auth_type)

def current_principal(request:Request):
    authorization=request.headers.get('authorization','')
    service_token=request.app.state.service_token
    if service_token and authorization.startswith('Bearer ') and hmac.compare_digest(authorization[7:],service_token):
        return Principal('service','service','administrator',frozenset({'*'}),None,'service')
    token=request.cookies.get(COOKIE,'')
    parsed=verify_token(request,token) if token else None
    if not parsed: raise HTTPException(401,'Authentication required')
    user=request.app.state.store.obj('user',parsed.get('sub',''))
    if not user: raise HTTPException(401,'Session subject no longer exists')
    return _principal_from_user(request,user)

def require_owner(request:Request):
    return current_principal(request)

def require_permission(request:Request,permission:str,project_id:str|None=None):
    principal=current_principal(request)
    if not principal.allows(permission,project_id): raise HTTPException(403,f'Missing permission: {permission}')
    return principal

def _authorized_password(user:dict,password:str,request:Request):
    """Check a password against the account that owns it.

    The configured administrator password is environment-backed and is never stored
    in SQLite. Other users keep a PBKDF2 password hash in their user record.
    """
    if user.get('username')==request.app.state.admin_username:
        return hmac.compare_digest(password,request.app.state.password)
    return verify_password(password,user.get('passwordHash',''))


def login_token_digest(request:Request,token:str) -> str:
    """Keyed digest for database-held login tokens; plaintext tokens are never stored."""
    return hmac.new(_secret(request),b'mrlc-login-token:'+token.encode(),hashlib.sha256).hexdigest()


def _establish_session(request:Request,response:Response,user:dict,auth_type:str='session'):
    """Issue the session cookie and return the resolved principal."""
    if user.get('disabled'): raise HTTPException(403,'Account disabled')
    secure=request.url.scheme=='https' or os.getenv('MRLC_SECURE_COOKIE','0')=='1'
    response.set_cookie(COOKIE,sign(request,user['id']),httponly=True,secure=secure,samesite='strict',max_age=TTL,path='/')
    return _principal_from_user(request,user,auth_type)


def resolve_password(request:Request,password:str,username:str|None=None):
    """Resolve an interactive password credential to an enabled account."""
    if not password:return None
    users=list(request.app.state.store.all('user'))
    if username is None:
        # Prefer the configured administrator for the password-only login form, then
        # try remaining accounts so non-admin users can also sign in without exposing
        # account discovery in the UI.
        users=sorted((u for u in users if not u.get('disabled')),
                     key=lambda u:u.get('username')!=request.app.state.admin_username)
    else:
        users=[u for u in users if u.get('username')==username and not u.get('disabled')]
    for user in users:
        try:
            if _authorized_password(user,password,request):return user
        except Exception:
            continue
    return None


def resolve_login_token(request:Request,token:str):
    """Resolve a URL login token independently from account passwords."""
    if not token:return None
    users=[u for u in request.app.state.store.all('user') if not u.get('disabled')]
    admin=next((u for u in users if u.get('username')==request.app.state.admin_username),None)
    configured=request.app.state.admin_token
    if admin and configured and hmac.compare_digest(token,configured):
        return admin
    digest=login_token_digest(request,token)
    for user in users:
        if user.get('username')==request.app.state.admin_username:continue
        stored=user.get('loginTokenHash','')
        if stored and hmac.compare_digest(digest,stored):return user
    return None


def login(request:Request,response:Response,password:str,username:str|None=None):
    """Sign in with an interactive password."""
    user=resolve_password(request,password,username)
    if user is None:raise HTTPException(401,'Invalid credentials')
    return _establish_session(request,response,user,'password')


def login_with_token(request:Request,response:Response,token:str):
    """Sign in with a dedicated URL token, never with a password."""
    user=resolve_login_token(request,token)
    if user is None:raise HTTPException(401,'Invalid token')
    return _establish_session(request,response,user,'token')

READ_PERMISSIONS={
    'projects':'projects.read','tasks':'tasks.read','channels':'workspace.read','artifacts':'artifacts.read',
    'approvals':'approvals.read','runtime':'runtime.read','roles':'roles.read','agents':'agents.read','system':'system.read','logs':'logs.read','settings':'system.read'
}
WRITE_PERMISSIONS={
    'projects':'projects.write','tasks':'tasks.write','channels':'workspace.write','artifacts':'artifacts.write',
    'approvals':'approvals.decide','runtime':'runtime.control','roles':'projects.write','agents':'agents.control','system':'settings.write','logs':'settings.write','settings':'settings.write'
}

def permission_for_request(method:str,path:str):
    if path.startswith('/api/v1/auth/users'): return 'admin.users'
    if path.startswith('/api/v1/auth/') or path=='/api/v1/capabilities': return None
    parts=[p for p in path.removeprefix('/api/v1/').split('/') if p]
    root=parts[0] if parts else ''
    if root=='projects' and len(parts)>=3 and parts[2] in ('agents','tasks','channels','artifacts'):
        root=parts[2]
    if root=='tasks' and parts[-1:] == ['dispatch']: return 'tasks.execute'
    if root=='channels' and parts[-1:] == ['respond']: return 'tasks.execute'
    table=READ_PERMISSIONS if method in ('GET','HEAD','OPTIONS') else WRITE_PERMISSIONS
    return table.get(root)

def resolve_project_scope(store,path:str):
    parts=[p for p in path.removeprefix('/api/v1/').split('/') if p]
    if not parts:return None
    root=parts[0]; oid=parts[1] if len(parts)>1 else None
    if root=='projects' and oid and not oid in ('schedule',): return oid
    kind_map={'tasks':'task','channels':'channel','artifacts':'artifact','approvals':'approval','agents':'agent'}
    if root in kind_map and oid:
        obj=store.obj(kind_map[root],oid)
        if obj:return obj.get('projectId') or obj.get('project_id') or obj.get('project')
    if root=='runtime' and len(parts)>=3 and parts[1]=='slots':
        row=store.execute("SELECT project_id FROM objects WHERE kind='slot' AND id=?",(parts[2],)).fetchone()
        return row['project_id'] if row else None
    return None
