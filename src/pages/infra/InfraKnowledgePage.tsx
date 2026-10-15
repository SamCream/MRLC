import {useState} from 'react';
import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import type {InfraMemoryItem,InfraBankView,InfraContextAssemblyRun,InfraA2cmSnapshot,InfraEvent} from '../../domain/models';
import {InfraEmpty,InfraError,InfraPageHead,infraBadge,infraTime} from './InfraShared';

const infra=repositories.infra;

export default function InfraKnowledgePage(){
  const {projectId}=useAppShell();
  const [tab,setTab]=useState<'memory'|'assembly'|'context'|'beliefs'|'bank'>('memory');
  const [roleScope,setRoleScope]=useState('shared');
  const memory=useRepositoryQuery(()=>infra.getMemory(projectId,roleScope),[projectId,roleScope],infra);
  const context=useRepositoryQuery(()=>infra.getContext(projectId),[projectId],infra);
  const bank=useRepositoryQuery(()=>infra.getBank(projectId),[projectId],infra);
  const a2cm=useRepositoryQuery(()=>infra.getA2cm(projectId),[projectId],infra);
  const events=useRepositoryQuery(()=>infra.getEvents(projectId),[projectId],infra);
  const memories=memory.data?.items??[];
  const scopes=Array.from(new Set(['shared',...(context.data?.memoryRoleScopes??[])]));

  return <section className="page infra-page">
    <InfraPageHead title="Memory & Context" description="How durable knowledge is sourced, retrieved, filtered and assembled into real provider context."/>
    <InfraError error={memory.error||context.error||bank.error||a2cm.error||events.error}/>
    <div className="infra-toolbar-row">
      <div className="segmented infra-tabs">
        <button className={tab==='memory'?'active':''} onClick={()=>setTab('memory')}>Memory provenance</button>
        <button className={tab==='assembly'?'active':''} onClick={()=>setTab('assembly')}>Assembly trace</button>
        <button className={tab==='context'?'active':''} onClick={()=>setTab('context')}>Context snapshots</button>
        <button className={tab==='beliefs'?'active':''} onClick={()=>setTab('beliefs')}>A2CM beliefs</button>
        <button className={tab==='bank'?'active':''} onClick={()=>setTab('bank')}>Project Bank records</button>
      </div>
      {tab==='memory'&&<label className="infra-inline-field"><span>Role scope</span><select value={roleScope} onChange={e=>setRoleScope(e.target.value)}>{scopes.map(scope=><option key={scope} value={scope}>{scope}</option>)}</select></label>}
    </div>

    {tab==='memory'&&<div className="card"><div className="card-head"><h2>Evidence-backed memories</h2><span className="badge push">{memories.length} visible</span></div><div className="infra-memory-grid">
      {memories.map(item=><MemoryCard key={item.memory_id} item={item}/>)}
      {!memory.loading&&!memories.length&&<InfraEmpty>No active Memory is visible to role scope “{roleScope}”.</InfraEmpty>}
    </div></div>}

    {tab==='assembly'&&<AssemblyRuns runs={context.data?.assemblyRuns??[]}/>}

    {tab==='context'&&<div className="infra-two-col">
      <div className="card"><div className="card-head"><h2>ContextSnapshot manifests</h2><span className="badge push">{context.data?.snapshots.length??0}</span></div><div className="table-wrap"><table className="data-table infra-table"><thead><tr><th>ID</th><th>Projection</th><th>Memory bundle</th><th>Memory tokens</th><th>Artifacts</th><th>Created</th></tr></thead><tbody>
        {(context.data?.snapshots??[]).map(item=><tr key={item.id}><td className="mono">{item.id}</td><td>{item.projection??'—'}</td><td className="mono">{item.memoryBundleId??'not assembled yet'}</td><td>{item.memoryTokenCount??'—'}</td><td>{item.artifactCount}</td><td>{infraTime(item.createdAt)}</td></tr>)}
      </tbody></table>{!context.loading&&!context.data?.snapshots.length&&<InfraEmpty>No ContextSnapshot has been persisted for this project.</InfraEmpty>}</div></div>
      <div className="card"><div className="card-head"><h2>History persistence index</h2><span className="sub">Counts only · no chat duplication</span></div><div className="infra-list">{(context.data?.historySessions??[]).map(item=><div className="infra-list-row" key={item.session_id}><code>{item.session_id}</code><strong>{item.entry_count} entries</strong><span>{infraTime(item.last_at)}</span></div>)}{!context.loading&&!context.data?.historySessions.length&&<InfraEmpty>No Infra history stream is persisted yet.</InfraEmpty>}</div></div>
    </div>}

    {tab==='beliefs'&&<A2cmPanel projectId={projectId} data={a2cm.data} events={events.data?.items??[]} loading={a2cm.loading||events.loading}/>}
    {tab==='bank'&&<BankRecords data={bank.data}/>}
  </section>;
}

function MemoryCard({item}:{item:InfraMemoryItem;key?:string}){
  return <article className="infra-memory-card"><div className="infra-memory-top"><span className="badge cyan">{item.kind}</span><span className="badge">{item.role_scope}</span><strong>{Math.round(item.confidence*100)}%</strong></div><p>{item.content}</p><div className="infra-memory-meta"><span>memory <code>{item.memory_id}</code></span><span>sources {item.source_event_refs.length}</span><span>valid {infraTime(item.valid_from)} → {item.valid_to?infraTime(item.valid_to):'∞'}</span></div></article>;
}

function AssemblyRuns({runs}:{runs:InfraContextAssemblyRun[]}){
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const selected=runs.find(run=>run.id===selectedId)??runs[0]??null;
  return <div className="infra-master-detail">
    <div className="card"><div className="card-head"><h2>Runtime assembly runs</h2><span className="badge push">{runs.length}</span></div><div className="infra-list">
      {runs.map(run=><button className={'infra-chain-row '+(selected?.id===run.id?'active':'')} key={run.id} onClick={()=>setSelectedId(run.id)}><code>{run.id}</code><span>{run.profile_id} · {run.total_tokens}/{run.token_budget} tokens</span><span className="badge">{infraTime(run.created_at)}</span></button>)}
      {!runs.length&&<InfraEmpty>No runtime has assembled durable Memory yet. A task/chat provider call will create the first trace.</InfraEmpty>}
    </div></div>
    <div className="card"><div className="card-head"><h2>Why this context?</h2>{selected&&<span className="badge push">{selected.role_id}</span>}</div>{selected?<div className="card-body infra-context-trace">
      <div className="infra-kv"><span>Job</span><code>{selected.job_id??'—'}</code><span>Profile</span><strong>{selected.profile_id}</strong><span>Budget</span><span>{selected.total_tokens} / {selected.token_budget} tokens</span><span>Query</span><p>{selected.query_excerpt||'—'}</p></div>
      <h3>Candidate decisions</h3>
      <div className="infra-list">{selected.candidates.map(item=>{
        const ref=item.sourceRef;
        const excluded=selected.excluded[ref];
        const included=selected.included.includes(ref);
        const state=included?'included':excluded??item.retrieval;
        return <div className="infra-list-row" key={ref}><code>{ref}</code><span>{item.retrieval} · conf {Math.round(item.confidence*100)}%</span><span className={'badge '+infraBadge(state)}>{state}</span></div>;
      })}{!selected.candidates.length&&<InfraEmpty>No eligible Memory candidate existed for this call.</InfraEmpty>}</div>
      <h3>Final manifest</h3>
      <pre className="infra-json">{JSON.stringify({included:selected.included,excluded:selected.excluded},null,2)}</pre>
    </div>:<InfraEmpty>Select an assembly run.</InfraEmpty>}</div>
  </div>;
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


function A2cmPanel({projectId,data,events,loading}:{projectId:string;data:InfraA2cmSnapshot|null;events:InfraEvent[];loading:boolean}){
  const [content,setContent]=useState('');
  const [eventId,setEventId]=useState('');
  const [confidence,setConfidence]=useState(0.7);
  const [source,setSource]=useState<'fast_tool'|'primary'|'frontier'>('fast_tool');
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [decision,setDecision]=useState<'PROMOTE'|'REJECT'|'DEFER'>('DEFER');
  const [policy,setPolicy]=useState('a2cm.policy.v1');
  const [rationale,setRationale]=useState('');
  const [counter,setCounter]=useState('');
  const [uncertainty,setUncertainty]=useState('');
  const [busy,setBusy]=useState<string|null>(null);
  const [status,setStatus]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null);
  const evidenceEventId=eventId||events.at(-1)?.eventId||'';
  const selected=data?.candidates.find(item=>item.candidateId===selectedId)??data?.candidates[0]??null;
  const reviewable=selected&&['CANDIDATE','DEFERRED'].includes(selected.status);

  async function ensurePrimary(){
    setBusy('primary');setError(null);setStatus(null);
    try{
      const result=await infra.a2cmEnsurePrimary(projectId);
      setStatus((result.created?'Created':'Reused')+' A2CM Primary session '+result.sessionId+'.');
    }catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(null);}
  }
  async function observe(){
    if(!content.trim()||!evidenceEventId)return;
    setBusy('observe');setError(null);setStatus(null);
    try{
      const result=await infra.a2cmObserve(projectId,{
        content:content.trim(),evidenceEventId,confidence,source,
      });
      setSelectedId(result.candidateId);setContent('');
      setStatus('Created evidence-backed candidate '+result.candidateId+'.');
    }catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(null);}
  }
  async function review(){
    if(!selected||!data?.activePrimarySessionId||!rationale.trim())return;
    setBusy('review');setError(null);setStatus(null);
    try{
      const result=await infra.a2cmReview(projectId,selected.candidateId,{
        primarySessionId:data.activePrimarySessionId,decision,policyVersion:policy.trim(),
        counterevidence:counter.split('\n').map(x=>x.trim()).filter(Boolean),
        uncertainty:uncertainty.split('\n').map(x=>x.trim()).filter(Boolean),
        rationale:rationale.trim(),
      });
      setStatus(selected.candidateId+' → '+result.status+'.');
      setRationale('');setCounter('');setUncertainty('');
    }catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy(null);}
  }

  return <div className="infra-two-col a2cm-layout">
    <div className="card">
      <div className="card-head"><h2>A2CM candidate intake</h2><span className="sub">Only Domain EventStore evidence may seed a candidate.</span></div>
      <div className="card-body infra-a2cm-form">
        {error&&<div className="infra-error">{error}</div>}
        {status&&<div className="infra-success">{status}</div>}
        <label><span>Observation</span><textarea value={content} maxLength={100000} onChange={e=>setContent(e.target.value)} placeholder="A factual or hypothesis statement to enter the candidate layer."/></label>
        <div className="infra-delegate-grid">
          <label><span>Evidence event</span><select value={evidenceEventId} onChange={e=>setEventId(e.target.value)}>{events.slice().reverse().map(event=><option key={event.eventId} value={event.eventId}>#{event.sequence} · {event.type}</option>)}</select></label>
          <label><span>Source</span><select value={source} onChange={e=>setSource(e.target.value as typeof source)}><option value="fast_tool">fast_tool</option><option value="primary">primary</option><option value="frontier">frontier</option></select></label>
          <label><span>Confidence</span><input type="number" min={0} max={1} step={0.05} value={confidence} onChange={e=>setConfidence(Math.max(0,Math.min(1,Number(e.target.value))))}/></label>
        </div>
        <div className="infra-actions"><button className="btn primary" disabled={busy!==null||!content.trim()||!evidenceEventId} onClick={()=>void observe()}>{busy==='observe'?'Recording…':'Record candidate'}</button></div>
      </div>
      <div className="infra-list">
        {(data?.candidates??[]).map(item=><button className={'infra-chain-row '+(selected?.candidateId===item.candidateId?'active':'')} key={item.candidateId} onClick={()=>setSelectedId(item.candidateId)}><code>{item.candidateId}</code><span>{Math.round(item.confidence*100)}% · {item.actorId}</span><span className={'badge '+infraBadge(item.status)}>{item.status}</span></button>)}
        {!loading&&!data?.candidates.length&&<InfraEmpty>No A2CM belief candidate exists for this project.</InfraEmpty>}
      </div>
    </div>

    <div className="card">
      <div className="card-head"><h2>Primary review</h2><span className={'badge push '+(data?.capabilities.review.available?'green':'amber')}>{data?.capabilities.review.available?'ACTIVE':'NOT READY'}</span></div>
      {!data?.capabilities.review.available?<div className="card-body"><p className="muted">{data?.capabilities.review.reason??'Loading A2CM capability state.'}</p><button className="btn primary" disabled={busy!==null} onClick={()=>void ensurePrimary()}>{busy==='primary'?'Creating…':'Enable A2CM Primary reviewer'}</button></div>:selected?<div className="card-body infra-a2cm-review">
        <div className="infra-kv"><span>Candidate</span><code>{selected.candidateId}</code><span>Evidence</span><code>{selected.evidenceRefs.join(', ')}</code><span>Statement</span><p>{selected.statement}</p><span>Current state</span><span className={'badge '+infraBadge(selected.status)}>{selected.status}</span><span>Primary session</span><code>{data.activePrimarySessionId}</code></div>
        {reviewable?<><div className="infra-delegate-grid">
          <label><span>Decision</span><select value={decision} onChange={e=>setDecision(e.target.value as typeof decision)}><option>PROMOTE</option><option>REJECT</option><option>DEFER</option></select></label>
          <label><span>Policy version</span><input value={policy} maxLength={160} onChange={e=>setPolicy(e.target.value)}/></label>
        </div>
        <label><span>Rationale</span><textarea value={rationale} maxLength={4000} onChange={e=>setRationale(e.target.value)} placeholder="Why this candidate should be promoted, rejected or deferred."/></label>
        <label><span>Counterevidence · one item per line</span><textarea value={counter} onChange={e=>setCounter(e.target.value)}/></label>
        <label><span>Known uncertainty · one item per line</span><textarea value={uncertainty} onChange={e=>setUncertainty(e.target.value)}/></label>
        <div className="infra-actions"><button className="btn primary" disabled={busy!==null||!rationale.trim()||!policy.trim()} onClick={()=>void review()}>{busy==='review'?'Reviewing…':'Commit Primary review'}</button></div></>:<InfraEmpty>This candidate is terminal and cannot be reviewed again.</InfraEmpty>}
      </div>:<InfraEmpty>Select a candidate to review.</InfraEmpty>}
    </div>
  </div>;
}
