import {useEffect,useState} from 'react';
import type {AppearanceSettings,ProjectRuntimeSettings} from '../domain/models';
import {appearanceDefaults,runtimeDefaults} from '../data/settingsRepository';
import {repositories,runtimeConfig} from '../data/repositories';
const repo=repositories.settings;
const auth=repositories.auth;
import {useRepositoryQuery} from '../hooks/useRepositoryQuery';
import {useAppShell} from '../app/AppContext';
const options={contextPolicy:[['task','Task scoped'],['project','Project scoped']],sessionPolicy:[['resumable','Resumable'],['ephemeral','Ephemeral']],frontierEscalation:[['ask','Ask every time'],['budget','Budget gated'],['disabled','Disabled']],theme:[['strata-dark','Strata dark'],['soft-dark','Soft dark']],motion:[['highlights','First-view highlights'],['reduced','Reduced motion']],density:[['compact','Compact'],['comfortable','Comfortable']],stageAnimation:[['play-once','Play once'],['static-gold','Static gold']]} as const;
function fieldOptions(values:readonly (readonly [string,string])[]){return values.map(([value,label])=><option value={value} key={value}>{label}</option>);}
export default function SettingsPage(){
  const {projectId,project}=useAppShell();
  const runtime=useRepositoryQuery(()=>project?repo.getRuntimeSettings(projectId):Promise.resolve({...runtimeDefaults,revision:1}),[projectId,!!project],repo);
  const appearance=useRepositoryQuery(()=>repo.getAppearance(),[],repo);
  const account=useRepositoryQuery(()=>auth.getCurrentAccount(),[],auth);
  const tokenStatus=useRepositoryQuery(()=>auth.getTokenStatus(),[],auth);
  const [draft,setDraft]=useState<ProjectRuntimeSettings>({...runtimeDefaults,revision:1});
  const [look,setLook]=useState<AppearanceSettings>(appearanceDefaults);
  const [saving,setSaving]=useState(false);const [feedback,setFeedback]=useState('');const [error,setError]=useState('');
  const [tokenDraft,setTokenDraft]=useState('');const [revealedToken,setRevealedToken]=useState('');const [tokenBusy,setTokenBusy]=useState(false);const [tokenFeedback,setTokenFeedback]=useState('');
  useEffect(()=>{if(runtime.data)setDraft(runtime.data);},[runtime.data,projectId]);
  useEffect(()=>{if(appearance.data)setLook(appearance.data);},[appearance.data]);
  const canEdit=!!project&&project.status!=='archived';
  const runtimeChanged=!!runtime.data&&(draft.modelId!==runtime.data.modelId||draft.contextPolicy!==runtime.data.contextPolicy||draft.sessionPolicy!==runtime.data.sessionPolicy||draft.frontierEscalation!==runtime.data.frontierEscalation);
  const lookChanged=!!appearance.data&&JSON.stringify(look)!==JSON.stringify(appearance.data);
  async function save(){setSaving(true);setError('');setFeedback('');const done:string[]=[];try{
    if(runtimeChanged){if(!canEdit)throw new Error('No editable project selected');const {revision:_,...input}=draft;await repo.updateRuntimeSettings(projectId,input,runtime.data!.revision);done.push('Project runtime defaults');}
    if(lookChanged){await repo.saveAppearance(look);done.push('Appearance preferences');}
    setFeedback(done.length?`${done.join(' and ')} saved (${runtimeConfig.mode==='http'?'runtime on server / appearance locally':'local demo'}).`:'No unsaved changes.');
  }catch(e){setError((done.length?`${done.join(' and ')} saved; `:'')+(e instanceof Error?e.message:String(e)));}finally{setSaving(false);}}

  async function generateToken(){setTokenBusy(true);setError('');setTokenFeedback('');try{const result=await auth.rotateToken();setRevealedToken(result.token);setTokenDraft('');setTokenFeedback('已生成新的登录 Token。明文只在此处显示，请立即保存。');}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setTokenBusy(false);}}
  async function replaceToken(){setTokenBusy(true);setError('');setTokenFeedback('');try{const result=await auth.rotateToken(tokenDraft);setRevealedToken(result.token);setTokenDraft('');setTokenFeedback('登录 Token 已替换，旧 Token 已立即失效。');}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setTokenBusy(false);}}
  async function revokeToken(){setTokenBusy(true);setError('');setTokenFeedback('');try{await auth.revokeToken();setRevealedToken('');setTokenDraft('');setTokenFeedback('登录 Token 已撤销。');}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setTokenBusy(false);}}
  async function copyToken(){if(!revealedToken)return;try{await navigator.clipboard.writeText(revealedToken);setTokenFeedback('Token 已复制到剪贴板。');}catch{setTokenFeedback('无法访问剪贴板，请手动复制。');}}

  async function reset(){setSaving(true);setError('');setFeedback('');try{
    if(canEdit&&runtime.data)await repo.resetRuntimeSettings(projectId,runtime.data.revision);
    await repo.resetAppearance();setFeedback('Settings reset to defaults in this browser'+(canEdit?' for the selected project.':'.'));
  }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setSaving(false);}}
  return <section className="page" id="page-settings"><div className="page-head"><div><h1>Settings</h1><p>Project-specific runtime defaults and browser appearance preferences · {runtimeConfig.mode==='http'?'experimental HTTP runtime settings':'local demo runtime settings'}</p></div><div className="head-spacer"/><button className="btn" onClick={()=>void reset()} disabled={saving}>Reset to defaults</button><button className="btn primary" onClick={()=>void save()} disabled={saving||(!runtimeChanged&&!lookChanged)}>{saving?'Saving…':'Save changes'}</button></div>
    {feedback&&<p role="status" className="muted">{feedback}</p>}{error&&<p role="alert">{error}</p>}{runtime.error&&<p role="alert">{runtime.error}</p>}{appearance.error&&<p role="alert">{appearance.error}</p>}
    <div className="overview-grid"><div className="card"><div className="card-head"><h2>Runtime defaults</h2><span className="badge push">{project?.name??'No project'}</span></div><div className="card-body"><p className="muted">Stored per project. Changing these settings does not reconfigure an actual inference engine until a backend honors the proposed API.</p><fieldset disabled={!canEdit||runtime.loading||saving} style={{border:0,padding:0,margin:0}}><div className="kv"><label htmlFor="setting-model">Default model ID</label><input id="setting-model" className="select" value={draft.modelId} onChange={e=>setDraft(d=>({...d,modelId:e.target.value}))} maxLength={128} placeholder="e.g. local-default"/>
    <label htmlFor="setting-context">Context policy</label><select className="select" id="setting-context" value={draft.contextPolicy} onChange={e=>setDraft(d=>({...d,contextPolicy:e.target.value as ProjectRuntimeSettings['contextPolicy']}))}>{fieldOptions(options.contextPolicy)}</select>
    <label htmlFor="setting-session">Session policy</label><select className="select" id="setting-session" value={draft.sessionPolicy} onChange={e=>setDraft(d=>({...d,sessionPolicy:e.target.value as ProjectRuntimeSettings['sessionPolicy']}))}>{fieldOptions(options.sessionPolicy)}</select>
    <label htmlFor="setting-frontier">Frontier escalation</label><select className="select" id="setting-frontier" value={draft.frontierEscalation} onChange={e=>setDraft(d=>({...d,frontierEscalation:e.target.value as ProjectRuntimeSettings['frontierEscalation']}))}>{fieldOptions(options.frontierEscalation)}</select></div></fieldset><p className="muted">Revision: {runtime.data?.revision??'—'} · {runtimeChanged?'Unsaved runtime changes':'Runtime defaults saved'}</p></div></div>
    <div className="card"><div className="card-head"><h2>Appearance</h2><span className="badge push">This browser</span></div><div className="card-body"><p className="muted">Global UI settings, saved locally and applied across all MRLC pages.</p><div className="kv"><label htmlFor="setting-theme">Theme</label><select className="select" id="setting-theme" value={look.theme} onChange={e=>setLook(x=>({...x,theme:e.target.value as AppearanceSettings['theme']}))}>{fieldOptions(options.theme)}</select>
    <label htmlFor="setting-motion">Motion</label><select className="select" id="setting-motion" value={look.motion} onChange={e=>setLook(x=>({...x,motion:e.target.value as AppearanceSettings['motion']}))}>{fieldOptions(options.motion)}</select>
    <label htmlFor="setting-density">Density</label><select className="select" id="setting-density" value={look.density} onChange={e=>setLook(x=>({...x,density:e.target.value as AppearanceSettings['density']}))}>{fieldOptions(options.density)}</select>
    <label htmlFor="setting-stage">Stage animation</label><select className="select" id="setting-stage" value={look.stageAnimation} onChange={e=>setLook(x=>({...x,stageAnimation:e.target.value as AppearanceSettings['stageAnimation']}))}>{fieldOptions(options.stageAnimation)}</select></div><p className="muted">{lookChanged?'Unsaved appearance changes. Save to apply.':'Saved appearance applied.'}</p></div></div>
    <div className="card"><div className="card-head"><h2>账户登录 Token</h2><span className="badge push">{account.data?.username??'当前账户'}</span></div><div className="card-body">
      {account.error&&<p role="alert">{account.error}</p>}{tokenStatus.error&&<p role="alert">{tokenStatus.error}</p>}
      {tokenStatus.loading?<p className="muted">正在读取 Token 状态…</p>:tokenStatus.data?.managedBy==='environment'?<>
        <p>管理员 Token 由服务器环境变量 <code>MRLC_ADMIN_TOKEN</code> 管理，界面不会读取或显示其明文。</p><p className="muted">状态：{tokenStatus.data.configured?'已配置':'未配置'}。修改 backend/.env 后重启后端即可轮换。</p>
      </>:tokenStatus.data?.managedBy==='unavailable'?<p className="muted">本地 mock 模式不提供真实登录 Token。</p>:<>
        <p className="muted">状态：{tokenStatus.data?.configured?'已配置':'尚未配置'}。随机生成或手动替换都会使旧 Token 立即失效。</p>
        <div className="inline-actions"><button className="btn" type="button" disabled={tokenBusy} onClick={()=>void generateToken()}>{tokenBusy?'处理中…':'生成随机 Token'}</button>{tokenStatus.data?.configured&&<button className="btn" type="button" disabled={tokenBusy} onClick={()=>void revokeToken()}>撤销 Token</button>}</div>
        <div className="field"><label htmlFor="login-token">自定义 Token（24–256 位 URL-safe 字符）</label><input id="login-token" type="password" autoComplete="off" value={tokenDraft} onChange={e=>setTokenDraft(e.target.value)} placeholder="留空则使用随机生成"/></div>
        <button className="btn primary" type="button" disabled={tokenBusy||tokenDraft.length<24} onClick={()=>void replaceToken()}>替换为自定义 Token</button>
        {revealedToken&&<div className="card-body"><p><strong>新 Token（仅本次显示）</strong></p><code style={{wordBreak:'break-all'}}>{revealedToken}</code><div className="inline-actions"><button className="btn" type="button" onClick={()=>void copyToken()}>复制</button><button className="btn" type="button" onClick={()=>setRevealedToken('')}>隐藏</button></div></div>}
      </>}
      {tokenFeedback&&<p role="status" className="muted">{tokenFeedback}</p>}
      <p className="muted">使用方式：<code>/?token=你的Token</code>。浏览器读取后会立即从地址栏移除并换取 HttpOnly Session。</p>
    </div></div></div>
  </section>;
}
