import {useEffect,useState,type FormEvent} from 'react';
import {runtimeConfig} from '../data/repositories';
import {AppProvider} from './AppContext';
import App from './App';

/** Only UI-level sign-in; backend validates identity, session and all permissions. */
export default function AuthGate(){
  const [status,setStatus]=useState<'checking'|'signed-in'|'signed-out'>('checking');
  const [password,setPassword]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [tokenState,setTokenState]=useState<'idle'|'invalid'>('idle');
  const base=runtimeConfig.baseUrl.replace(/\/$/,'');
  const check=async()=>{
    setStatus('checking');setError('');
    try {
      const response=await fetch(`${base}/api/v1/auth/me`,{credentials:'same-origin'});
      if(response.status===401){setStatus('signed-out');return;}
      if(!response.ok)throw new Error(`Backend returned HTTP ${response.status}`);
      setStatus('signed-in');
    }catch(e){setError(`后端连接失败：${e instanceof Error?e.message:String(e)}`);setStatus('signed-out');}
  };
  async function signInWithCredential(rawCredential:string, viaUrl:boolean){
    try {
      const response=viaUrl
        ? await fetch(`${base}/api/v1/auth/token`,{credentials:'same-origin',headers:{'X-MRLC-Token':rawCredential}})
        : await fetch(`${base}/api/v1/auth/login`,{method:'POST',credentials:'same-origin',
            headers:{'Content-Type':'application/json'},body:JSON.stringify({password:rawCredential})});
      if(!response.ok)throw new Error(response.status===401?(viaUrl?'令牌无效':'密码错误'):`登录失败：HTTP ${response.status}`);
      setPassword('');setStatus('signed-in');
    }catch(e){
      setError(e instanceof Error?e.message:String(e));
      if(viaUrl)setTokenState('invalid');
      setStatus('signed-out');
    }
  }
  useEffect(()=>{
    if(runtimeConfig.mode!=='http'){setStatus('signed-in');return;}
    const url=new URL(window.location.href);
    const token=url.searchParams.get('token');
    if(token){
      // Remove the credential before any subsequent request or navigation can leak it
      // through copied URLs, browser history entries or Referer headers.
      url.searchParams.delete('token');
      window.history.replaceState(null,'',url.pathname+url.search+url.hash);
      void signInWithCredential(token,true);return;
    }
    void check();
  },[]);
  async function signIn(e:FormEvent){
    e.preventDefault();setBusy(true);setError('');
    await signInWithCredential(password,false);
    setBusy(false);
  }
  async function signOut(){
    try{await fetch(`${base}/api/v1/auth/logout`,{method:'POST',credentials:'same-origin'});}finally{setStatus('signed-out');}
  }
  if(status==='checking')return <main className="auth-screen"><div className="card card-body"><h2>MRLC</h2><p>正在检查后端登录状态…</p></div></main>;
  if(tokenState==='invalid')return <main className="auth-screen"><div className="card card-body" role="alert"><h2>MRLC</h2><p>{error||'令牌无效'}</p><p className="muted">请使用有效的访问链接（<code>?token=…</code>），或使用账户密码登录。</p><div className="inline-actions"><button className="btn primary" type="button" onClick={()=>{setTokenState('idle');void check();}}>使用密码登录</button></div></div></main>;
  if(status==='signed-out')return <main className="auth-screen"><form className="card card-body native-form auth-panel" onSubmit={signIn}>
    <h1>MRLC Control Room</h1><p className="muted">连接真实 MRLC 后端。身份验证、调度、审批及数据持久化由服务端处理。</p>
    <label>账户密码<input type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)}/></label>
    {error&&<p role="alert">{error}</p>}<div className="inline-actions"><button className="btn primary" disabled={busy} type="submit">登录</button><button className="btn" type="button" onClick={()=>void check()}>检查连接</button></div>
    <p className="muted">请勿将密码或 API 密钥写入 VITE_* 环境变量。远程使用请部署 HTTPS。</p>
  </form></main>;
  return <AppProvider><App onLogout={runtimeConfig.mode==='http'?signOut:undefined}/></AppProvider>;
}
