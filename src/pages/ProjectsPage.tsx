import { useState, type FormEvent } from 'react';
import { useAppShell } from '../app/AppContext';
import {repositories,runtimeConfig} from '../data/repositories';
const repo=repositories.organization;
import { useRepositoryQuery } from '../hooks/useRepositoryQuery';
import type { Project } from '../domain/models';
const label: Record<Project['status'],string>={running:'Running',blocked:'Blocked',idle:'Idle',archived:'Archived'};
const tone:Record<Project['status'],string>={running:'green',blocked:'amber',idle:'',archived:''};
export default function ProjectsPage(){
  const {projectId,setProjectId,navigate}=useAppShell();
  const {data,error,loading}=useRepositoryQuery(()=>repo.listProjects(),[],repo);
  const [query,setQuery]=useState('');const [status,setStatus]=useState('all');
  const [form,setForm]=useState<'new'|'edit'|'archive'|null>(null);const [target,setTarget]=useState('');
  const [name,setName]=useState('');const [description,setDescription]=useState('');const [reason,setReason]=useState('');
  const [busy,setBusy]=useState(false);const [actionError,setActionError]=useState('');
  const projects=data?.items??[];
  const visible=projects.filter(p=>(status==='all'?p.status!=='archived':p.status===status)&&`${p.name} ${p.description}`.toLowerCase().includes(query.toLowerCase()));
  const allAgents=useRepositoryQuery(async()=>{
    const projects=await repo.listProjects();const counts=await Promise.all(projects.items.map(async p=>[p.id,(await repo.listAgents(p.id)).items.length] as const));return Object.fromEntries(counts) as Record<string,number>;
  },[],repo);
  function openNew(){setForm('new');setName('');setDescription('');setActionError('');}
  function openEdit(p:Project){setTarget(p.id);setName(p.name);setDescription(p.description);setForm('edit');setActionError('');}
  function openArchive(p:Project){setTarget(p.id);setName(p.name);setReason('');setForm('archive');setActionError('');}
  async function submit(e:FormEvent){e.preventDefault();setBusy(true);setActionError('');try {
    if(form==='new'){const created=await repo.createProject({name,description});setProjectId(created.id);setForm(null);navigate('overview');}
    if(form==='edit'){await repo.updateProject(target,{name,description});setForm(null);}
    if(form==='archive'){await repo.archiveProject(target,reason);setForm(null);}
  } catch(e){setActionError(e instanceof Error?e.message:String(e));} finally{setBusy(false);}}
  return <section className="page" id="page-projects">
    <div className="page-head"><div><h1>Project Center</h1><p>Cross-project status, resources and attention</p></div><div className="head-spacer"/><div className="toolbar"><input className="search-field" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search projects" aria-label="Search projects"/><select className="select compact-select" value={status} onChange={e=>setStatus(e.target.value)} aria-label="Project state"><option value="all">All active states</option>{Object.entries(label).map(([value,text])=><option key={value} value={value}>{text}</option>)}</select><button className="btn primary" onClick={openNew}>＋ New project</button></div></div>
    {form&&<form className="card native-form" onSubmit={submit} style={{marginBottom:14}}><h3 style={{margin:0}}>{form==='new'?'Create project':form==='edit'?'Project settings':'Archive project'}</h3>{form==='archive'?<><p className="muted">Archive {name}? Existing agents will be parked. {runtimeConfig.mode==='http'?'Authorization and archival semantics must be enforced server-side.':'Data is preserved as read-only in the demo; production requires server authorization.'}</p><label>Archive reason<textarea value={reason} onChange={e=>setReason(e.target.value)}/></label></>:<><label>Project name<input value={name} onChange={e=>setName(e.target.value)} required maxLength={120}/></label><label>Description<textarea value={description} onChange={e=>setDescription(e.target.value)}/></label></>}
      <div className="inline-actions"><button className={`btn ${form==='archive'?'amber':'primary'}`} disabled={busy} type="submit">{form==='archive'?'Confirm archive':'Save'}</button><button className="btn" type="button" onClick={()=>{setForm(null);setActionError('');}}>Cancel</button></div>{actionError&&<p role="alert">{actionError}</p>}
    </form>}
    {error&&<p role="alert">{error}</p>}{loading&&<p className="muted">Loading projects…</p>}
    <div className="project-grid" id="project-grid">{visible.map(p=><div key={p.id} className={`card project-card ${p.id===projectId?'selected':''}`}>
      <span className={`badge ${tone[p.status]}`}>{label[p.status]}</span><h3>{p.name}</h3><p className="muted">{p.description||'No description'}</p>
      <div className="project-meta"><div><strong>{p.progress}%</strong><span>{runtimeConfig.mode==='http'?'Milestone':'Milestone (demo)'}</span></div><div><strong>{allAgents.data?.[p.id]??'—'}</strong><span>Agent instances</span></div><div><strong>—</strong><span>Attention (API pending)</span></div></div>
      <div className="progress"><span style={{width:`${p.progress}%`,background:p.status==='blocked'?'var(--amber)':p.status==='idle'?'var(--cyan)':'var(--green)'}}/></div>
      <div className="project-actions">{p.status!=='archived'?<><button className="btn" onClick={()=>{setProjectId(p.id);navigate('overview');}}>Open</button><button className="btn" onClick={()=>openEdit(p)}>Settings</button><button className="btn" onClick={()=>openArchive(p)}>Archive</button></>:<span className="muted">Archived · read-only</span>}</div>
    </div>)}{!loading&&visible.length===0&&<p className="muted">No projects match these filters.</p>}</div>
  </section>;
}
