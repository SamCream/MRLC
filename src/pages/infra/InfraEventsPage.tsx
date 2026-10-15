import {useState} from 'react';
import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import {InfraEmpty,InfraError,InfraPageHead,infraBadge,infraTime} from './InfraShared';

const infra=repositories.infra;

export default function InfraEventsPage(){
  const {projectId}=useAppShell();
  const events=useRepositoryQuery(()=>infra.getEvents(projectId),[projectId],infra);
  const replay=useRepositoryQuery(()=>infra.getReplay(projectId),[projectId],infra);
  const rows=events.data?.items??[];
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const selected=rows.find(x=>x.eventId===selectedId)??rows.at(-1)??null;
  return <section className="page infra-page">
    <InfraPageHead title="Events & Replay" description="Inspect causation, correlation and deterministic state reconstructed from domain events."/>
    <InfraError error={events.error||replay.error}/>
    <div className="infra-master-detail">
      <div className="card">
        <div className="card-head"><h2>Domain event stream</h2><span className="badge push">{rows.length} loaded</span></div>
        <div className="table-wrap"><table className="data-table infra-table"><thead><tr><th>Seq</th><th>Time</th><th>Type</th><th>Actor</th><th>Correlation</th></tr></thead><tbody>
          {rows.slice().reverse().map(row=><tr key={row.eventId} className={selected?.eventId===row.eventId?'selected':''} onClick={()=>setSelectedId(row.eventId)}>
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
          <div className="card-head"><h2>Current deterministic replay</h2><span className="badge green push">Read only</span></div>
          <div className="card-body infra-replay-grid">
            {replay.data?Object.entries(replay.data).filter(([,value])=>value&&typeof value==='object'&&!Array.isArray(value)).map(([key,value])=><div key={key}><span>{key}</span><strong>{Object.keys(value as object).length}</strong></div>):<span className="muted">Loading replay state…</span>}
          </div>
        </div>
      </div>
    </div>
  </section>;
}
