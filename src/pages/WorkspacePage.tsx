import {repositories} from '../data/repositories';
import { useEffect, useRef, useState, type FormEvent } from 'react';
const repo=repositories.workspace;
import { useRepositoryQuery } from '../hooks/useRepositoryQuery';
import type { Channel, Message, ChannelResponseDispatch } from '../domain/models';
import { useAppShell } from '../app/AppContext';

const organization=repositories.organization;
const artifactRepo=repositories.artifacts;
const initials = (name: string) => name === 'You' ? 'M' : name.split(' ').map(x=>x[0]).join('').slice(0,2);
const tone = (name: string) => name==='Project Manager'?'amber':name==='Researcher'?'green':name==='Reviewer'?'violet':'';
export default function WorkspacePage() {
  const {projectId,navigate} = useAppShell();
  const [channelId,setChannelId] = useState('research');
  const [draft,setDraft] = useState('');
  const [pendingFile,setPendingFile]=useState<File|null>(null);
  const [responseAgentId,setResponseAgentId]=useState('');
  const [mode,setMode] = useState<'single'|'multi'>('single');
  const [busy,setBusy] = useState(false);
  const [actionError,setActionError] = useState('');
  const [lastDispatch,setLastDispatch]=useState<ChannelResponseDispatch|null>(null);
  const [search,setSearch] = useState<string|null>(null);
  const [showCreate,setShowCreate] = useState(false);
  const [newName,setNewName] = useState('');
  const [newPurpose,setNewPurpose] = useState('');
  const [newPolicy,setNewPolicy] = useState<Channel['contextPolicy']>('relevant');
  const fileRef = useRef<HTMLInputElement>(null);
  const scrollRef=useRef<HTMLDivElement>(null);
  const channels = useRepositoryQuery(()=>repo.listChannels(projectId),[projectId],repo);
  const agents = useRepositoryQuery(()=>organization.listAgents(projectId),[projectId],organization);
  const roles = useRepositoryQuery(()=>organization.listRoles(),[],organization);
  const tasks = useRepositoryQuery(()=>repo.listTasks(projectId),[projectId],repo);
  const artifacts=useRepositoryQuery(()=>artifactRepo.listArtifacts(projectId),[projectId],artifactRepo);
  const localChannels=(channels.data?.items??[]).filter(c=>c.projectId===projectId);
  const selected=localChannels.find(x=>x.id===channelId);
  const messages = useRepositoryQuery(()=>selected?repo.listMessages(selected.id):Promise.resolve({items:[]}),[selected?.id],repo);
  const responseGroups = useRepositoryQuery(()=>selected?repo.listResponseGroups(selected.id):Promise.resolve({items:[]}),[selected?.id],repo);
  useEffect(()=>{setChannelId('');setSearch(null);setDraft('');setPendingFile(null);setLastDispatch(null);},[projectId]);
  useEffect(()=>{if(!channels.loading && !selected && localChannels.length)setChannelId((localChannels.find(c=>c.id==='research')??localChannels[0]).id);},[channelId,channels.data,channels.loading,selected]);
  useEffect(()=>{const onCompose=(e:Event)=>{const value=(e as CustomEvent<{text:string}>).detail?.text;if(value)setDraft(prev=>`${prev} ${value}`.trimStart());};window.addEventListener('mrlc:compose',onCompose);return ()=>window.removeEventListener('mrlc:compose',onCompose);},[]);
  const currentTask=tasks.data?.items.find(t=>t.projectId===projectId && t.status==='running');
  const roleName=(id:string)=>roles.data?.items.find(r=>r.id===id)?.name??id;
  useEffect(()=>{if(scrollRef.current) scrollRef.current.scrollTop=scrollRef.current.scrollHeight;},[messages.data]);
  async function submit(e: FormEvent) {
    e.preventDefault();if((!draft.trim()&&!pendingFile)||busy||!selected)return;setBusy(true);setActionError('');
    let uploaded=false;
    try {
      const ids:string[]=[];
      if(pendingFile){const result=await artifactRepo.uploadArtifact(projectId,pendingFile);ids.push(result.id);uploaded=true;}
      await repo.sendMessage(selected.id,draft.trim()||(pendingFile?`Attached file: ${pendingFile.name}`:''),mode,ids);
      setLastDispatch(null);
      setDraft('');setPendingFile(null);if(fileRef.current)fileRef.current.value='';
    }
    catch(e){setActionError(`${e instanceof Error?e.message:String(e)}${uploaded?' · File was stored in Artifacts, but the message was not sent.':''}`);}
    finally{setBusy(false);}
  }
  async function requestReply(){if(!selected)return;setBusy(true);setActionError('');setLastDispatch(null);try{
    const result=await repo.respondToChannel(
      selected.id,responseAgentId||undefined,[],
      selected.contextPolicy==='task'?currentTask?.id:undefined,
    );
    setLastDispatch(result);
  }catch(e){setActionError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
  async function createChannel(e: FormEvent) {
    e.preventDefault();if(!newName.trim())return;setBusy(true);setActionError('');
    try {const result=await repo.createChannel(projectId,{name:newName.trim(),purpose:newPurpose.trim(),contextPolicy:newPolicy});setChannelId(result.id);setShowCreate(false);setNewName('');setNewPurpose('');}
    catch(e){setActionError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
  }
  const filtered=(selected?messages.data?.items??[]:[]).filter(m=>!search||`${m.content} ${m.senderName}`.toLowerCase().includes(search.toLowerCase()));
  const latestGroup=responseGroups.data?.items?.[0]??null;
  return <section className="page" id="page-workspace"><div className="workspace-layout">
    <div className="card channels"><div className="card-head"><h2>Project workspace</h2><button className="icon-btn push" onClick={()=>setShowCreate(x=>!x)} aria-label="Create channel">＋</button></div>
      <div className="card-body">
      {channels.loading&&<p className="muted">Loading channels…</p>}{channels.error&&<p role="alert">{channels.error}</p>}
      {localChannels.map(c=><button key={c.id} className={`channel channel-button ${c.id===channelId?'active':''}`} onClick={()=>{setChannelId(c.id);setSearch(null);}}><strong># {c.name}</strong><small>{c.purpose}</small></button>)}
      {showCreate&&<form className="native-form" onSubmit={createChannel}><label>Channel name<input value={newName} onChange={e=>setNewName(e.target.value)} required /></label><label>Purpose<input value={newPurpose} onChange={e=>setNewPurpose(e.target.value)} /></label><label>Context projection<select value={newPolicy} onChange={e=>setNewPolicy(e.target.value as Channel['contextPolicy'])}><option value="relevant">Relevant</option><option value="task">Task isolated</option><option value="project">Project</option></select></label><button className="btn primary" type="submit" disabled={busy}>Create</button></form>}
      <div className="team-list"><strong>Project team</strong>{(agents.data?.items??[]).filter(a=>a.projectId===projectId).map(a=><div className="team-row" key={a.id}><div className={`avatar small ${tone(a.name)}`}>{initials(a.name)}</div><div className="grow"><strong>{a.name}</strong><small className="muted">{a.status} · {roleName(a.roleTemplateId)}</small></div><span className={`dot ${a.status==='working'?'green':''}`}/></div>)}{!agents.loading&&!agents.data?.items.length&&<p className="muted">No agents assigned.</p>}</div>
      </div>
    </div>
    <div className="card conversation"><div className="card-head"><div className="grow"><h2># {selected?.name??'Channel'}</h2><div className="muted">{selected?.purpose}</div></div><span className="badge projection-badge">{selected?.contextPolicy??'relevant'} projection</span><button className="icon-btn" title="Search" onClick={()=>setSearch(x=>x===null?'':null)}>⌕</button></div>
      {search!==null&&<div className="card-body"><input className="search-field" aria-label="Search messages" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search this channel…" /></div>}
      <div className="message-list" ref={scrollRef} aria-live="polite">{selected&&messages.loading&&<p className="muted">Loading messages…</p>}{messages.error&&<p role="alert">{messages.error}</p>}{!messages.loading&&!filtered.length&&<p className="muted">{selected?'No messages in this channel.':'Create a channel to start this project workspace.'}</p>}
      {filtered.map((m:Message)=>m.stageCompletion?<div className={`card completion-card completion-${m.stageCompletion.level}`} key={m.id}><div className="card-body"><div className="completion-medal">✓</div><div className="grow"><strong>{m.content}</strong><div className="muted">{m.stageCompletion.verified?'Verified stage event':'Unverified stage event'}</div></div></div></div>:<div className="message" key={m.id}><div className={`avatar ${tone(m.senderName)}`}>{initials(m.senderName)}</div><div className="message-body"><div className="message-meta"><strong>{m.senderName}</strong><time>{new Date(m.createdAt).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time><button className="btn push" onClick={()=>navigator.clipboard?.writeText(m.content)} aria-label="Copy message">••• Copy</button></div><p>{m.content}</p>{m.knowledgeReadiness&&<div className="message-runtime-meta"><span className={'badge '+(m.knowledgeReadiness==='KNOWN'?'green':m.knowledgeReadiness==='PARTIAL'?'amber':'')}>{m.knowledgeReadiness}</span>{m.routeReason&&<span className="badge">{m.routeReason}</span>}<span className="muted">{m.knowledgeReadinessReason}</span></div>}{!!m.artifactIds?.length&&<div className="attachments">{m.artifactIds.map(id=>{const a=artifacts.data?.items.find(x=>x.id===id);return <button key={id} className="attachment" onClick={()=>navigate('artifacts')} title="Open Artifacts library">▱ {a?.name??id}</button>;})}</div>}</div></div>)}
      </div>
      <div className="composer-shell"><div className="composer-tools"><input ref={fileRef} type="file" hidden onChange={e=>{setPendingFile(e.target.files?.[0]??null);setActionError('');}}/><button className="btn" onClick={()=>fileRef.current?.click()}>{pendingFile?`▱ ${pendingFile.name}`:'＋ File'}</button>{pendingFile&&<button className="btn" aria-label="Remove attachment" onClick={()=>{setPendingFile(null);if(fileRef.current)fileRef.current.value='';}}>×</button>}<button className="btn" onClick={()=>setDraft(x=>x+'@Engineer ')}>@ Role</button><button className="btn" onClick={()=>setDraft(x=>x+'/task ')}>/ Command</button><select className="select" aria-label="Responding Agent" value={responseAgentId} onChange={e=>setResponseAgentId(e.target.value)}><option value="">后端自动路由</option>{(agents.data?.items??[]).filter(a=>a.status==='idle').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select><button className="btn" disabled={busy||!selected} onClick={()=>void requestReply()}>请求 Agent 回复（后端）</button><button className="btn response-chip" onClick={()=>setMode(x=>x==='single'?'multi':'single')} title="This mode is persisted on the next user message">{mode==='single'?'Single role':'Multi-role · serial'} ▾</button></div><form className="composer" onSubmit={submit}><input value={draft} onChange={e=>setDraft(e.target.value)} placeholder={`Message #${selected?.name??'channel'}…`} aria-label="Message"/><button className="btn primary" type="submit" disabled={busy||(!draft.trim()&&!pendingFile)||!selected}>Send</button></form>{lastDispatch&&<p className="muted response-dispatch-summary">已路由：{lastDispatch.agents.map(x=>`${x.order}. ${x.roleName}`).join(' → ')} · {lastDispatch.responseMode==='multi'?'串行执行':'单角色执行'}</p>}{actionError&&<p role="alert" className="muted">{actionError}</p>}</div>
    </div>
    <div className="context-panel"><div className="card context-card"><div className="card-head"><h3>Current task</h3><button className="btn push" onClick={()=>navigate('tasks')}>View task</button></div><div className="card-body"><strong>{currentTask?.title??'No active task'}</strong><div className="kv workspace-task-kv"><span>Assignee</span><span>{currentTask?.assigneeRoleId?roleName(currentTask.assigneeRoleId):'—'}</span><span>Status</span><span className="badge green task-status">{currentTask?.status??'idle'}</span><span>Progress</span><span>{currentTask?.progress??0}%</span></div><div className="progress"><span style={{width:`${currentTask?.progress??0}%`}} /></div></div></div><div className="card context-card"><div className="card-head"><h3>Knowledge readiness</h3></div><div className="card-body">{(()=>{const latest=[...(messages.data?.items??[])].reverse().find(m=>m.senderName!=='You'&&m.knowledgeReadiness);if(latest)return <><strong>{latest.knowledgeReadiness}</strong><p className="muted">{latest.knowledgeReadinessReason}</p>{latest.routeReason&&<span className="badge">{latest.routeReason}</span>}</>;if(lastDispatch)return <><strong>执行时评估</strong><p className="muted">{lastDispatch.agents.map(x=>`${x.order}. ${x.roleName} · ${x.routeReason}`).join('；')}</p><span className="badge">{lastDispatch.responseMode==='multi'?'串行 multi-role':'single-role'}</span></>;return <><strong>尚未执行</strong><p className="muted">后端会在真实 provider 调用前根据验证证据、显式附件和检索到的 durable Memory 写入 KNOWN / PARTIAL / UNKNOWN；不会偷偷扩大上下文。</p></>;})()}</div></div>
    <div className="card context-card"><div className="card-head"><h3>Response pipeline</h3>{latestGroup&&<span className="badge push">{latestGroup.status}</span>}</div><div className="card-body response-pipeline">{latestGroup?latestGroup.members.map(member=><div className="response-pipeline-row" key={member.jobId}><span className="mono">{member.order}</span><div className="grow"><strong>{member.roleName??member.agentName}</strong><small className="muted">{member.routeReason}</small></div><span className={'badge '+(member.status==='completed'?'green':member.status==='running'?'amber':'')}>{member.status}</span></div>):<p className="muted">还没有持久化的回复组。Multi-role 回复会在这里显示 waiting → queued → running → completed 的串行进度。</p>}</div></div>
    </div>
  </div></section>;
}
