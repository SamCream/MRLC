import {repositories,runtimeConfig} from '../data/repositories';
import {useState} from 'react';
import {useRepositoryQuery} from '../hooks/useRepositoryQuery';
import type {InfrastructureNode} from '../domain/models';

const repo=repositories.system;
const none='Not available — no real MRLC monitoring provider is connected.';
const fmtBytes=(bytes?:number)=>bytes===undefined?'—':`${(bytes/(1024**3)).toFixed(1)} GiB`;
export default function InfrastructurePage(){
  const health=useRepositoryQuery(()=>repo.getHealth(),[],repo);
  const topo=useRepositoryQuery(()=>repo.getTopology(),[],repo);
  const nodes=useRepositoryQuery(()=>repo.listNodes(),[],repo);
  const accelerators=useRepositoryQuery(()=>repo.listAccelerators(),[],repo);
  const [scope,setScope]=useState<'all'|'local'|'remote'>('all');
  const [checking,setChecking]=useState(false);
  const [feedback,setFeedback]=useState('');
  const [selected,setSelected]=useState<InfrastructureNode|null>(null);
  const filtered=(nodes.data?.items??[]).filter(n=>scope==='all'||n.scope===scope);
  async function check(){setChecking(true);setFeedback('');try{const value=await repo.runHealthCheck();setFeedback(value?`Health result received at ${value.checkedAt}.`:'Health-check backend not connected. No diagnostic was executed.');}catch(e){setFeedback(e instanceof Error?e.message:String(e));}finally{setChecking(false);}}
  return <section className="page" id="page-infrastructure">
    <div className="page-head"><div><h1>Infrastructure <span className="badge amber">{runtimeConfig.mode==='http'?'Backend API':'Unconnected'}</span></h1><p>Hardware inventory, topology and provider health · live metrics unavailable</p></div><div className="head-spacer"/><button className="btn" onClick={()=>void check()} disabled={checking}>{checking?'Checking…':'Run health check'}</button><select aria-label="Node scope" className="select" value={scope} onChange={e=>setScope(e.target.value as typeof scope)}><option value="all">All nodes</option><option value="local">Local</option><option value="remote">Remote</option></select></div>
    {feedback&&<p role="status" className="muted">{feedback}</p>}{[health.error,topo.error,nodes.error,accelerators.error].filter(Boolean).map((e,i)=><p key={i} role="alert">{e}</p>)}
    <div className="summary-grid">
      <div className="card summary-card"><div className="summary-title">◉ Health</div><div className="summary-value" style={{fontSize:22}}>{health.data===null?'Unknown':health.data?.healthy?'Healthy':'Degraded'}</div><div className="summary-meta">{health.data?.checkedAt??'No recent sample'}</div></div>
      <div className="card summary-card"><div className="summary-title">⬡ Nodes</div><div className="summary-value">{nodes.data?.items.length??'—'}</div><div className="summary-meta">{nodes.data?.items.length?'Provider inventory':'No monitoring inventory'}</div></div>
      <div className="card summary-card"><div className="summary-title">▣ Accelerators</div><div className="summary-value">{accelerators.data?.items.length??'—'}</div><div className="summary-meta">{accelerators.data?.items.length?'Reported by provider':'Live GPU samples unavailable'}</div></div>
      <div className="card summary-card"><div className="summary-title">◷ Sample age</div><div className="summary-value" style={{fontSize:20}}>{health.data?.checkedAt?new Date(health.data.checkedAt).toLocaleTimeString():'—'}</div><div className="summary-meta">No fabricated uptime or latency</div></div>
    </div>
    <div className="card"><div className="card-head"><h2>Infrastructure topology</h2><span className="sub">Backend-reported services and edges</span></div><div className="card-body">{topo.data?.nodes.length?<><div className="topology" style={{display:'flex',flexWrap:'wrap'}}>{topo.data.nodes.map(n=><div key={n.id} className="topology-node"><span className="badge">{n.kind}</span><h3>{n.label}</h3><div className="muted">{n.status}</div></div>)}</div><p className="muted">Connections: {topo.data.edges.map(e=>`${e.from} → ${e.to}`).join(' · ')||'None reported'}</p></>:<p className="muted">{none} Original node names and latency values are only in the source reference, not real topology.</p>}</div></div>
    <div className="ops-grid" style={{marginTop:12}}><div className="card ops-card"><h3>Nodes · {scope}</h3>{filtered.map(n=><button key={n.id} className="command-result" onClick={()=>setSelected(n)}><span className={`dot ${n.status==='online'?'green':''}`}/><span>{n.name}</span><span className="muted push">{n.status}</span></button>)}{!filtered.length&&<p className="muted">No nodes reported for this scope.</p>}</div>
    <div className="card ops-card"><h3>Accelerators</h3>{accelerators.data?.items.map(g=><div className="health-line" key={g.id}><div className="grow"><strong>{g.name}</strong><div className="muted">{g.status} · VRAM {fmtBytes(g.vramUsedBytes)} / {fmtBytes(g.vramCapacityBytes)}</div></div><strong>{g.powerWatts===undefined?'—':`${g.powerWatts} W`}</strong></div>)}{!accelerators.data?.items.length&&<p className="muted">GPU statistics not connected.</p>}</div>
    <div className="card ops-card"><h3>Service status</h3>{health.data?.services.map(s=><div className="health-line" key={s.name}><span className={`dot ${s.healthy?'green':'red'}`}/><div className="grow">{s.name}</div><strong>{s.latencyMs===undefined?'—':`${s.latencyMs} ms`}</strong></div>)}{!health.data?.services.length&&<p className="muted">Provider health is unknown, not healthy.</p>}</div></div>
    {selected&&<div className="card card-body" style={{marginTop:12}}><button className="btn push" onClick={()=>setSelected(null)}>Close</button><h3>{selected.name}</h3><div className="kv"><span>Scope</span><strong>{selected.scope}</strong><span>Status</span><strong>{selected.status}</strong><span>CPU</span><strong>{selected.cpuUtilizationPercent??'—'}%</strong><span>RAM used</span><strong>{fmtBytes(selected.ramUsedBytes)}</strong><span>Last seen</span><strong>{selected.lastSeenAt??'Not sampled'}</strong></div></div>}
  </section>;
}
