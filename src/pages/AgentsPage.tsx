import {repositories,runtimeConfig} from '../data/repositories';
import {useEffect,useState,type FormEvent} from 'react';
import {useAppShell} from '../app/AppContext';
import {useRepositoryQuery} from '../hooks/useRepositoryQuery';
import type {Agent, RoleTemplate} from '../domain/models';

const repo=repositories.organization;
const workspace=repositories.workspace;
const tone=(status:Agent['status'])=>status==='working'?'green':status==='failed'?'red':status==='parked'||status==='paused'?'amber':'';
const acronym=(name:string)=>name.split(' ').map(x=>x[0]).join('').slice(0,2).toUpperCase();
export default function AgentsPage(){
  const {project,projectId,navigate}=useAppShell();
  const agents=useRepositoryQuery(()=>repo.listAgents(projectId),[projectId],repo);
  const roles=useRepositoryQuery(()=>repo.listRoles(),[],repo);
  const tasks=useRepositoryQuery(()=>workspace.listTasks(projectId),[projectId],workspace);
  const [selected,setSelected]=useState('');const [assign,setAssign]=useState(false);const [template,setTemplate]=useState('');const [name,setName]=useState('');
  const [busy,setBusy]=useState(false);const [actionError,setActionError]=useState('');
  const list=agents.data?.items??[];const templates=roles.data?.items??[];
  const activeTemplates=templates.filter(x=>x.lifecycle==='active');
  const agent=list.find(a=>a.id===selected)??list[0];
  const roleById=(id:string)=>templates.find(r=>r.id===id);
  const role:RoleTemplate|undefined=agent&&roleById(agent.roleTemplateId);
  const work=(tasks.data?.items??[]).filter(t=>t.assigneeAgentId===agent?.id||(!t.assigneeAgentId&&(t.assigneeRoleId===role?.id||t.assigneeRoleId===role?.name)));
  useEffect(()=>{setSelected('');setActionError('');},[projectId]);
  async function assignAgent(e:FormEvent){e.preventDefault();setBusy(true);setActionError('');try{const roleTemplateId=template||activeTemplates[0]?.id;if(!roleTemplateId)throw new Error('Choose an active role template');const created=await repo.assignAgent({projectId,roleTemplateId,name:name||undefined});setSelected(created.id);setAssign(false);setName('');}catch(e){setActionError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
  async function transition(action:'park'|'resume'){if(!agent)return;setBusy(true);setActionError('');try{await (action==='park'?repo.parkAgent(agent.id):repo.resumeAgent(agent.id));}catch(e){setActionError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
  return <section className="page" id="page-agents">
    <div className="page-head"><div><h1>Project Agents</h1><p id="agents-project-subtitle">Role instances in {project?.name??'selected project'}</p></div><div className="head-spacer"/><button className="btn" onClick={()=>setAssign(x=>!x)} disabled={!project||project.status==='archived'}>＋ Assign agent</button><button className="btn primary" onClick={()=>navigate('roles')}>Open Role Center</button></div>
    {assign&&<form className="card native-form" onSubmit={assignAgent} style={{marginBottom:14}}><h3 style={{margin:0}}>Assign a role instance</h3><label>Role template<select value={template||activeTemplates[0]?.id||''} onChange={e=>setTemplate(e.target.value)} required>{activeTemplates.map(r=><option key={r.id} value={r.id}>{r.name} · {r.version}</option>)}</select></label><label>Agent display name (optional)<input value={name} onChange={e=>setName(e.target.value)}/></label><p className="muted">{runtimeConfig.mode==='http'?'Creates a durable idle Agent instance; model execution requires an explicit server-side task or reply request.':'Demo only: creates an idle Agent and no compute lease.'}</p><div><button className="btn primary" type="submit" disabled={busy||!activeTemplates.length}>Assign</button> <button className="btn" type="button" onClick={()=>setAssign(false)}>Cancel</button></div></form>}
    {(agents.error||roles.error||tasks.error||actionError)&&<p role="alert">{agents.error||roles.error||tasks.error||actionError}</p>}
    <div className="role-layout"><div className="card role-primary-card"><div className="card-head"><h2>Assigned agents</h2><span className="sub" id="agent-count">{list.length} instances</span></div><div id="agent-list" className="stable-scroll">{agents.loading?<p className="card-body muted">Loading agents…</p>:list.length?list.map(a=>{const r=roleById(a.roleTemplateId);return <button key={a.id} className={`role-row role-row-button ${a.id===agent?.id?'selected':''}`} onClick={()=>{setSelected(a.id);setActionError('');}}><div className="role-cell"><div className="avatar small">{acronym(a.name)}</div><div><strong>{a.name}</strong><div className="muted">{r?.purpose??'Missing role template'}</div></div></div><span>{r?.version??'—'}</span><div className="chips">{r?.capabilities.slice(0,2).map(x=><span key={x} className="chip">{x}</span>)}</div><span>{r?.preferredModelId??'—'}</span><span className={`badge ${tone(a.status)}`}>{a.status}</span></button>;}):<p className="card-body muted">No agents in this project. Assign an active role template to create one.</p>}</div></div>
      <div className="stack role-detail-stack"><div className="card role-primary-card" id="agent-detail">{agent?<><div className="card-head"><div className="avatar">{acronym(agent.name)}</div><div style={{marginLeft:10}}><h2>{agent.name}</h2><div className="muted">{project?.name??projectId} · {role?.name??'Unknown template'}</div></div><span className={`badge push ${tone(agent.status)}`}>{agent.status}</span></div><div className="card-body"><h3>Current responsibility</h3><p>{role?.purpose??'Role template unavailable.'}</p><div className="kv"><span>Template</span><strong>{role?.name??'—'} {role?.version??''}</strong><span>Model preference</span><strong>{role?.preferredModelId??'—'}</strong><span>Tasks</span><strong>{work.length} role/instance-matched</strong><span>Session</span><strong>{agent.sessionId??'Not connected'}</strong></div><p className="muted">A role match is not proof of an individual Agent assignment. Live session details require backend integration.</p><div className="action-row"><button className="btn primary" onClick={()=>{navigate('workspace');window.dispatchEvent(new CustomEvent('mrlc:compose',{detail:{text:`@${agent.name.replaceAll(' ','')} `}}));}}>Message agent</button><button className="btn" disabled={busy||agent.status==='parked'||agent.status==='archived'} onClick={()=>transition('park')}>Park agent</button><button className="btn" disabled={busy||agent.status!=='parked'&&agent.status!=='paused'} onClick={()=>transition('resume')}>Resume agent</button></div></div></>:<div className="card-body muted">No agent selected.</div>}</div>
      <div className="card"><div className="card-head"><h3>Current workload</h3><span className="sub">{work.length} matching tasks</span></div><div className="item-list" id="agent-workload">{work.length?work.map(t=><div className="list-item" key={t.id}><span className={`dot ${t.status==='running'?'green':''}`}/><div className="grow"><strong>{t.title}</strong><small>{t.status}</small></div></div>):<div className="card-body muted">No matching assigned tasks.</div>}</div></div></div>
    </div>
  </section>;
}
