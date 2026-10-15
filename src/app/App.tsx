import {repositories,runtimeConfig} from '../data/repositories';
import {useEffect,useState,type ComponentType} from 'react';
import OverviewPage from '../pages/OverviewPage';
import WorkspacePage from '../pages/WorkspacePage';
import TasksPage from '../pages/TasksPage';
import ArtifactsPage from '../pages/ArtifactsPage';
import ProjectsPage from '../pages/ProjectsPage';
import AgentsPage from '../pages/AgentsPage';
import RolesPage from '../pages/RolesPage';
import ApprovalsPage from '../pages/ApprovalsPage';
import ComputePage from '../pages/ComputePage';
import InfrastructurePage from '../pages/InfrastructurePage';
import LogsPage from '../pages/LogsPage';
import SettingsPage from '../pages/SettingsPage';
import InfraOverviewPage from '../pages/infra/InfraOverviewPage';
import InfraEventsPage from '../pages/infra/InfraEventsPage';
import InfraSchedulerPage from '../pages/infra/InfraSchedulerPage';
import InfraKnowledgePage from '../pages/infra/InfraKnowledgePage';
import InfraProvenancePage from '../pages/infra/InfraProvenancePage';
import InfraInvocationsPage from '../pages/infra/InfraInvocationsPage';
import InfraConsistencyPage from '../pages/infra/InfraConsistencyPage';
import {useAppShell,type PageId} from './AppContext';
import {useAppearance} from './useAppearance';
import {useRepositoryQuery} from '../hooks/useRepositoryQuery';

const settings=repositories.settings;
const systemRepository=repositories.system;

type NavItem=[PageId,string,string];
const primary:NavItem[]=[['overview','⌂','Overview'],['workspace','◎','Workspace'],['tasks','◫','Tasks'],['agents','♧','Agents'],['artifacts','▱','Artifacts']];
const management:NavItem[]=[['projects','□','Projects'],['roles','♧','Role Center'],['approvals','◇','Approvals'],['compute','▣','Compute']];
const system:NavItem[]=[['infrastructure','⬡','Infrastructure'],['logs','▤','Logs'],['settings','⌘','Settings']];
const infraNav:NavItem[]=[
  ['infra-overview','⌂','Overview'],
  ['infra-events','≡','Events & Replay'],
  ['infra-scheduler','⇄','Scheduler & Leases'],
  ['infra-knowledge','◇','Memory & Context'],
  ['infra-provenance','⛓','Provenance'],
  ['infra-invocations','↗','Invocations'],
  ['infra-consistency','⌁','Consistency & Recovery'],
];
const pages:Array<[PageId,ComponentType]>=[
  ['overview',OverviewPage],['workspace',WorkspacePage],['tasks',TasksPage],['artifacts',ArtifactsPage],
  ['projects',ProjectsPage],['agents',AgentsPage],['roles',RolesPage],['approvals',ApprovalsPage],
  ['compute',ComputePage],['infrastructure',InfrastructurePage],['logs',LogsPage],['settings',SettingsPage],
  ['infra-overview',InfraOverviewPage],['infra-events',InfraEventsPage],['infra-scheduler',InfraSchedulerPage],
  ['infra-knowledge',InfraKnowledgePage],['infra-provenance',InfraProvenancePage],
  ['infra-invocations',InfraInvocationsPage],['infra-consistency',InfraConsistencyPage],
];

export default function App({onLogout}:{onLogout?:()=>Promise<void>}){
  const {page,navigate,projectId,setProjectId,projects,loading,error}=useAppShell();
  useAppearance();
  const health=useRepositoryQuery(()=>systemRepository.getHealth(),[],systemRepository);
  const [mobileMore,setMobileMore]=useState(false);
  const [overlay,setOverlay]=useState<'command'|'system'|null>(null);
  const [commandQuery,setCommandQuery]=useState('');
  const [feedback,setFeedback]=useState('');
  const infraMode=page.startsWith('infra-');
  const go=(id:PageId)=>{navigate(id);setOverlay(null);setMobileMore(false);setCommandQuery('');};
  useEffect(()=>{
    const onKey=(e:KeyboardEvent)=>{
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){
        e.preventDefault();setOverlay('command');return;
      }
      if(e.key==='Escape'){setOverlay(null);setMobileMore(false);}
    };
    window.addEventListener('keydown',onKey);
    return()=>window.removeEventListener('keydown',onKey);
  },[]);
  useEffect(()=>{if(!feedback)return;const id=setTimeout(()=>setFeedback(''),2600);return()=>clearTimeout(id);},[feedback]);
  async function switchTheme(){
    try{
      const value=await settings.getAppearance();
      await settings.saveAppearance({...value,theme:value.theme==='strata-dark'?'soft-dark':'strata-dark'});
      setFeedback('Appearance preference saved');
    }catch(e){setFeedback(e instanceof Error?e.message:String(e));}
  }
  const nav=(items:NavItem[])=>items.map(([id,icon,label])=><button key={id} className={'nav-item '+(page===id?'active':'')} onClick={()=>go(id)}><span className="nav-icon">{icon}</span>{label}</button>);
  const productItems=[...primary,...management,...system];
  const paletteItems=infraMode?infraNav:productItems;
  const query=commandQuery.trim().toLowerCase();
  const matches=paletteItems.filter(([, ,label])=>!query||label.toLowerCase().includes(query)).slice(0,12);
  const ActivePage=pages.find(([id])=>id===page)?.[1]??(infraMode?InfraOverviewPage:OverviewPage);
  const projectScoped=new Set<PageId>([
    'overview','workspace','tasks','artifacts','agents','approvals',
    'infra-overview','infra-events','infra-scheduler','infra-knowledge',
    'infra-provenance','infra-invocations','infra-consistency',
  ]);
  const needsProject=projectScoped.has(page);
  const close=()=>setOverlay(null);
  const mobilePrimary:NavItem[]=infraMode?infraNav.slice(0,3):[['overview','⌂','Overview'],['workspace','◎','Workspace'],['tasks','◫','Tasks']];
  const mobileMoreItems:NavItem[]=infraMode?infraNav.slice(3):[...management,['agents','♧','Agents'],['artifacts','▱','Artifacts'],...system];

  return <>
    <div className="app">
      <header className="topbar">
        <div className="brand">MRLC</div>
        <span className={'badge '+(runtimeConfig.mode==='http'?'amber':'')} title="Current data source: server-authoritative MRLC API when configured.">{runtimeConfig.mode==='http'?'Backend · HTTP':'Demo · Local'}</span>
        <select className="project-select" aria-label="Current project" value={projectId} onChange={e=>setProjectId(e.target.value)} disabled={loading||!projects.some(p=>p.status!=='archived')}>{projects.filter(p=>p.status!=='archived').map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>
        {infraMode?
          <div className="infra-top-mode"><span className="dot cyan"/><strong>Infra Core</strong><span>authoritative backend view</span></div>:
          <nav className="top-tabs" aria-label="Project sections">{([['overview','Overview'],['workspace','Workspace'],['tasks','Tasks & Deliverables']] as [PageId,string][]).map(([id,label])=><button key={id} onClick={()=>go(id)} className={'top-tab '+(page===id?'active':'')}>{label}</button>)}</nav>
        }
        <div className="top-actions">
          <button className="icon-btn" onClick={()=>setOverlay('command')} title="Search · Ctrl K" aria-label="Search and commands">⌕</button>
          <button className="health" onClick={()=>setOverlay('system')}><span className={'dot '+(health.data?.healthy===true?'green':health.data?.healthy===false?'red':'')}/>{health.data?health.data.healthy?'System healthy':'System degraded':'Status unknown'}</button>
          <button className="icon-btn" onClick={()=>void switchTheme()} title="Toggle theme" aria-label="Toggle theme">☼</button>
          <div className="avatar small">M</div>
          {onLogout&&<button className="btn" onClick={()=>void onLogout()}>退出</button>}
        </div>
      </header>
      <div className="body">
        <aside className={'sidebar '+(infraMode?'infra-sidebar':'')}>
          {infraMode?<>
            <div className="nav-group"><div className="nav-label">Infra Core</div>{nav(infraNav)}</div>
            <div className="nav-mode-spacer"/>
            <button className="nav-mode-switch" onClick={()=>go('overview')}><span>⇄</span><span><strong>Back to Control Room</strong><small>Product &amp; collaboration UI</small></span></button>
          </>:<>
            <div className="nav-group"><div className="nav-label">Current Project</div>{nav(primary)}</div>
            <div className="nav-group"><div className="nav-label">Management</div>{nav(management)}</div>
            <div className="nav-group"><div className="nav-label">System</div>{nav(system)}</div>
            <div className="nav-mode-spacer"/>
            <button className="nav-mode-switch" onClick={()=>go('infra-overview')}><span>⇄</span><span><strong>Switch to Infra Core</strong><small>Events, replay &amp; consistency</small></span></button>
          </>}
        </aside>
        <main className="main">{error&&<p role="alert">Failed to load projects: {error}</p>}<div key={projectId+':'+page} className="react-page-slot">{needsProject&&!projectId?<section className="page"><div className="card"><div className="card-body"><p className="muted">{loading?'Loading projects…':'Create or select a project to open this page.'}</p></div></div></section>:<ActivePage/>}</div></main>
      </div>
    </div>

    {overlay&&<div className="surface-backdrop open" onClick={close} aria-hidden="true"/>}
    {overlay==='command'&&<section className="modal open" role="dialog" aria-modal="true" aria-label="Search and command palette"><div className="surface-head"><h2>{infraMode?'Infra Core':'Control Room'} · Search</h2><button className="icon-btn push" aria-label="Close" onClick={close}>×</button></div><div className="surface-body"><div className="field"><label htmlFor="command-query">Search pages</label><input id="command-query" autoFocus value={commandQuery} onChange={e=>setCommandQuery(e.target.value)} placeholder="Search pages…"/></div><div className="command-results">{matches.map(([id,,label])=><button className="command-result" key={id} onClick={()=>go(id)}><strong>{label}</strong><kbd>↵</kbd></button>)}{!matches.length&&<p className="muted">No matching pages.</p>}</div></div><div className="surface-foot"><button className="btn" onClick={close}>Close</button></div></section>}
    {overlay==='system'&&<aside className="drawer open" role="dialog" aria-modal="true" aria-label="System status"><div className="surface-head"><h2>System status</h2><button className="icon-btn push" aria-label="Close" onClick={close}>×</button></div><div className="surface-body"><span className={'badge '+(health.data?.healthy?'green':'amber')}>{health.data?health.data.healthy?'Health confirmed':'System degraded':'Status unknown · no provider'}</span><p className="muted">No static demo health figures are presented as live data.</p>{health.data?.services.map(item=><div className="status-line" key={item.name}><span className={'dot '+(item.healthy?'green':'red')}/><strong className="grow">{item.name}</strong><span className="muted">{item.latencyMs===undefined?'—':String(item.latencyMs)+' ms'}</span></div>)}</div><div className="surface-foot"><button className="btn" onClick={close}>Close</button><button className="btn primary" onClick={()=>go(infraMode?'infra-consistency':'infrastructure')}>{infraMode?'Consistency & Recovery':'Infrastructure'}</button></div></aside>}

    <nav className="mobile-nav" aria-label="Mobile navigation">{mobilePrimary.map(([id,icon,label])=><button key={id} className={page===id?'active':''} onClick={()=>go(id)}><span>{icon}</span>{label}</button>)}<button onClick={()=>setMobileMore(v=>!v)} aria-expanded={mobileMore}><span>•••</span>More</button></nav>
    <div className={'mobile-sheet '+(mobileMore?'open':'')}>{mobileMoreItems.map(([id,icon,label])=><button key={id} onClick={()=>go(id)}>{icon} {label}</button>)}<button onClick={()=>go(infraMode?'overview':'infra-overview')}>⇄ {infraMode?'Back to Control Room':'Switch to Infra Core'}</button></div>
    {feedback&&<div className="toast show" role="status">{feedback}</div>}
  </>;
}
