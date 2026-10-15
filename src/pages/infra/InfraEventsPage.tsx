import {useState} from 'react';
import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import type {InfraReplayCompare} from '../../domain/models';
import {InfraEmpty,InfraError,InfraPageHead,infraBadge,infraTime} from './InfraShared';

const infra=repositories.infra;

export default function InfraEventsPage(){
  const {projectId}=useAppShell();
  const events=useRepositoryQuery(()=>infra.getEvents(projectId),[projectId],infra);
  const rows=events.data?.items??[];
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const selected=rows.find(x=>x.eventId===selectedId)??rows.at(-1)??null;
  const replay=useRepositoryQuery(
    ()=>infra.getReplay(projectId,selected?.sequence),
    [projectId,selected?.sequence],
    infra,
  );
  const [compareFrom,setCompareFrom]=useState<number|null>(null);
  const [compare,setCompare]=useState<InfraReplayCompare|null>(null);
  const [compareError,setCompareError]=useState<string|null>(null);
  const [comparing,setComparing]=useState(false);

  async function runCompare(){
    if(compareFrom===null||!selected)return;
    setComparing(true);setCompareError(null);
    try{setCompare(await infra.compareReplay(projectId,compareFrom,selected.sequence));}
    catch(e){setCompare(null);setCompareError(e instanceof Error?e.message:String(e));}
    finally{setComparing(false);}
  }

  return <section className="page infra-page">
    <InfraPageHead title="Events & Replay" description="Inspect causation, correlation, point-in-time replay and deterministic state differences."/>
    <InfraError error={events.error||replay.error||compareError}/>
    <div className="infra-master-detail">
      <div className="card">
        <div className="card-head"><h2>Domain event stream</h2><span className="badge push">{rows.length} loaded</span></div>
        <div className="table-wrap"><table className="data-table infra-table"><thead><tr><th>Seq</th><th>Time</th><th>Type</th><th>Actor</th><th>Correlation</th></tr></thead><tbody>
          {rows.slice().reverse().map(row=><tr key={row.eventId} className={selected?.eventId===row.eventId?'selected':''} onClick={()=>{setSelectedId(row.eventId);setCompare(null);}}>
            <td className="mono">#{row.sequence}</td><td>{infraTime(row.occurredAt)}</td><td><span className={'badge '+infraBadge(row.type)}>{row.type}</span></td><td>{row.actorId??'—'}</td><td className="mono">{row.correlationId??'—'}</td>
          </tr>)}
        </tbody></table>{!events.loading&&!rows.length&&<InfraEmpty>No domain events exist for this project yet.</InfraEmpty>}</div>
      </div>
      <div className="infra-detail-stack">
        <div className="card">
          <div className="card-head"><h2>Event envelope</h2>{selected&&<span className="badge push">#{selected.sequence}</span>}</div>
          {selected?<div className="card-body infra-kv">
            <span>Event ID</span><code>{selected.eventId}</code><span>Type</span><strong>{selected.type}</strong>
            <span>Actor</span><span>{selected.actorId??'—'}</span><span>Session</span><code>{selected.sessionId??'—'}</code>
            <span>Task</span><code>{selected.taskId??'—'}</code><span>Correlation</span><code>{selected.correlationId??'—'}</code>
            <span>Causation</span><code>{selected.causationId??'—'}</code><span>Occurred</span><span>{infraTime(selected.occurredAt)}</span>
            <span>Payload</span><pre className="infra-json">{JSON.stringify(selected.payload,null,2)}</pre>
          </div>:<InfraEmpty>Select an event to inspect its envelope and payload.</InfraEmpty>}
        </div>

        <div className="card">
          <div className="card-head"><h2>Replay @ {selected?'#'+selected.sequence:'—'}</h2><span className="badge green push">Deterministic</span></div>
          <div className="card-body infra-replay-grid">
            {replay.data?Object.entries(replay.data).filter(([,value])=>value&&typeof value==='object'&&!Array.isArray(value)).map(([key,value])=><div key={key}><span>{key}</span><strong>{Object.keys(value as object).length}</strong></div>):<span className="muted">Loading replay state…</span>}
          </div>
          {selected&&<div className="surface-foot infra-replay-actions">
            <button className="btn" onClick={()=>{setCompareFrom(selected.sequence);setCompare(null);}}>Set as compare A</button>
            <button className="btn primary" disabled={compareFrom===null||compareFrom===selected.sequence||comparing} onClick={()=>void runCompare()}>{comparing?'Comparing…':compareFrom===null?'Choose compare A':'Compare #'+compareFrom+' → #'+selected.sequence}</button>
          </div>}
        </div>

        <div className="card">
          <div className="card-head"><h2>State diff</h2>{compare&&<span className="badge push">{compare.changedSections} sections changed</span>}</div>
          {compare?<div className="infra-diff-list">{Object.entries(compare.changes).map(([section,value])=><div className="infra-diff-section" key={section}><strong>{section}</strong><pre className="infra-json">{JSON.stringify(value,null,2)}</pre></div>)}{compare.changedSections===0&&<InfraEmpty>No authoritative state changed between these event positions.</InfraEmpty>}</div>:<InfraEmpty>Set a baseline event, select another event, then compare the two replay states.</InfraEmpty>}
        </div>
      </div>
    </div>
  </section>;
}
