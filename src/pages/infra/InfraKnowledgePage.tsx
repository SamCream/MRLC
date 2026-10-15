import {useState} from 'react';
import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import type {InfraMemoryItem,InfraBankView} from '../../domain/models';
import {InfraEmpty,InfraError,InfraPageHead,infraBadge,infraTime} from './InfraShared';

const infra=repositories.infra;

export default function InfraKnowledgePage(){
  const {projectId}=useAppShell();
  const [tab,setTab]=useState<'memory'|'context'|'bank'>('memory');
  const memory=useRepositoryQuery(()=>infra.getMemory(projectId),[projectId],infra);
  const context=useRepositoryQuery(()=>infra.getContext(projectId),[projectId],infra);
  const bank=useRepositoryQuery(()=>infra.getBank(projectId),[projectId],infra);
  const memories=memory.data?.items??[];
  return <section className="page infra-page">
    <InfraPageHead title="Memory & Context" description="How durable knowledge is sourced and assembled. Workspace conversation content is intentionally not repeated."/>
    <InfraError error={memory.error||context.error||bank.error}/>
    <div className="segmented infra-tabs"><button className={tab==='memory'?'active':''} onClick={()=>setTab('memory')}>Memory provenance</button><button className={tab==='context'?'active':''} onClick={()=>setTab('context')}>Context snapshots</button><button className={tab==='bank'?'active':''} onClick={()=>setTab('bank')}>Project Bank records</button></div>
    {tab==='memory'&&<div className="card"><div className="card-head"><h2>Evidence-backed memories</h2><span className="badge push">{memories.length} active</span></div><div className="infra-memory-grid">
      {memories.map(item=><MemoryCard key={item.memory_id} item={item}/>)}
      {!memory.loading&&!memories.length&&<InfraEmpty>No active shared Memory records. This is not equivalent to “no project knowledge”; only durable Infra Memory is shown.</InfraEmpty>}
    </div></div>}
    {tab==='context'&&<div className="infra-two-col">
      <div className="card"><div className="card-head"><h2>ContextSnapshot manifests</h2><span className="badge push">{context.data?.snapshots.length??0}</span></div><div className="table-wrap"><table className="data-table infra-table"><thead><tr><th>ID</th><th>Projection</th><th>Artifacts</th><th>Task revision</th><th>Provider checkpoint</th><th>Created</th></tr></thead><tbody>
        {(context.data?.snapshots??[]).map(item=><tr key={item.id}><td className="mono">{item.id}</td><td>{item.projection??'—'}</td><td>{item.artifactCount}</td><td className="mono">{item.taskRevision??'—'}</td><td className="mono">{item.providerCheckpointId??'not supported'}</td><td>{infraTime(item.createdAt)}</td></tr>)}
      </tbody></table>{!context.loading&&!context.data?.snapshots.length&&<InfraEmpty>No ContextSnapshot has been persisted for this project.</InfraEmpty>}</div></div>
      <div className="card"><div className="card-head"><h2>History persistence index</h2><span className="sub">Counts only · no chat duplication</span></div><div className="infra-list">{(context.data?.historySessions??[]).map(item=><div className="infra-list-row" key={item.session_id}><code>{item.session_id}</code><strong>{item.entry_count} entries</strong><span>{infraTime(item.last_at)}</span></div>)}{!context.loading&&!context.data?.historySessions.length&&<InfraEmpty>No Infra history stream is persisted yet.</InfraEmpty>}</div></div>
    </div>}
    {tab==='bank'&&<BankRecords data={bank.data}/>}
  </section>;
}

function MemoryCard({item}:{item:InfraMemoryItem;key?:string}){
  return <article className="infra-memory-card"><div className="infra-memory-top"><span className="badge cyan">{item.kind}</span><span className="badge">{item.role_scope}</span><strong>{Math.round(item.confidence*100)}%</strong></div><p>{item.content}</p><div className="infra-memory-meta"><span>memory <code>{item.memory_id}</code></span><span>sources {item.source_event_refs.length}</span><span>valid {infraTime(item.valid_from)} → {item.valid_to?infraTime(item.valid_to):'∞'}</span></div></article>;
}
function BankRecords({data}:{data:InfraBankView|null}){
  const rows=[
    ...(data?.issues??[]).map(x=>({id:x.issue_id,type:'ISS',state:x.status,link:x.resolved_by??'—'})),
    ...(data?.decisions??[]).map(x=>({id:x.decision_id,type:'DEC',state:x.status,link:x.supersedes?'supersedes '+x.supersedes:'—'})),
    ...(data?.lessons??[]).map(x=>({id:x.lesson_id,type:'LES',state:'recorded',link:String(x.source_refs.length)+' source ref(s)'})),
    ...(data?.progress??[]).map(x=>({id:x.progress_id,type:'PRG',state:x.status,link:String(x.artifact_refs.length)+' artifact ref(s)'})),
  ];
  return <div className="card"><div className="card-head"><h2>Project Bank record index</h2><span className="sub">Internal IDs, state and lineage only; product decision cards stay in Control Room.</span></div><div className="table-wrap"><table className="data-table infra-table"><thead><tr><th>Record</th><th>Type</th><th>State</th><th>Lineage / refs</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}><td className="mono">{row.id}</td><td><span className="badge">{row.type}</span></td><td><span className={'badge '+infraBadge(row.state)}>{row.state}</span></td><td>{row.link}</td></tr>)}</tbody></table>{!rows.length&&<InfraEmpty>No Project Bank records are persisted.</InfraEmpty>}</div></div>;
}
