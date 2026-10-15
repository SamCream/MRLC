import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import {InfraEmpty,InfraError,InfraMetric,InfraPageHead,infraBadge,infraTime} from './InfraShared';

const infra=repositories.infra;

export default function InfraConsistencyPage(){
  const {projectId}=useAppShell();
  const consistency=useRepositoryQuery(()=>infra.getConsistency(projectId),[projectId],infra);
  const operations=useRepositoryQuery(()=>infra.getOperations(projectId),[projectId],infra);
  const data=consistency.data,ops=operations.data;
  const pending=data?.outbox.filter(x=>x.published_at===null&&x.dead_letter_at===null).length??0;
  const dead=data?.outbox.filter(x=>x.dead_letter_at!==null).length??0;
  return <section className="page infra-page">
    <InfraPageHead title="Consistency & Recovery" description="Outbox/inbox, Saga, fencing, continuity and recovery metadata. Host CPU/GPU/storage monitoring remains in Infrastructure."/>
    <InfraError error={consistency.error||operations.error}/>
    <div className="infra-metric-grid compact"><InfraMetric label="Outbox pending" value={pending}/><InfraMetric label="Inbox receipts" value={data?.inboxCount??'—'}/><InfraMetric label="Project sagas" value={data?.sagas.length??'—'}/><InfraMetric label="Dead letters" value={dead} tone={dead?'warn':''}/></div>
    <div className="infra-two-col">
      <div className="card"><div className="card-head"><h2>Transactional outbox</h2><span className="badge push">{data?.outbox.length??0}</span></div><div className="table-wrap"><table className="data-table infra-table"><thead><tr><th>Message</th><th>Aggregate</th><th>Event</th><th>Attempt</th><th>State</th></tr></thead><tbody>{(data?.outbox??[]).map(row=><tr key={row.message_id}><td className="mono">{row.message_id}</td><td>{row.aggregate_type} · <code>{row.aggregate_id}</code></td><td>{row.event_type}</td><td>{row.attempt_count}</td><td><span className={'badge '+infraBadge(row.dead_letter_at?'DEAD':row.published_at?'PUBLISHED':'PENDING')}>{row.dead_letter_at?'DEAD LETTER':row.published_at?'PUBLISHED':'PENDING'}</span></td></tr>)}</tbody></table>{!consistency.loading&&!data?.outbox.length&&<InfraEmpty>No project outbox messages.</InfraEmpty>}</div></div>
      <div className="card"><div className="card-head"><h2>Continuity checkpoint</h2></div>{ops?.continuity?<div className="card-body infra-kv"><span>Checkpoint</span><code>{ops.continuity.checkpointId}</code><span>Session</span><code>{ops.continuity.sessionId}</code><span>Client</span><span>{ops.continuity.client}</span><span>Git</span><code>{ops.continuity.gitHeadSha??'—'}</code><span>Verification</span><span>{ops.continuity.verificationStatus??'unknown'}</span><span>Created</span><span>{infraTime(ops.continuity.createdAt)}</span></div>:<InfraEmpty>No continuity checkpoint has been recorded for this project.</InfraEmpty>}</div>
    </div>
    <div className="infra-three-col">
      <div className="card"><div className="card-head"><h2>Sagas</h2></div><div className="infra-list">{(data?.sagas??[]).map(row=><div className="infra-list-row" key={row.sagaId}><code>{row.sagaId}</code><span>{row.sagaType}</span><span className={'badge '+infraBadge(row.state)}>{row.state}</span></div>)}{!consistency.loading&&!data?.sagas.length&&<InfraEmpty>No project-scoped Saga records.</InfraEmpty>}</div></div>
      <div className="card"><div className="card-head"><h2>Worker metadata</h2><span className="badge push">{ops?.workers.length??0}</span></div><div className="infra-list">{(ops?.workers??[]).map(row=><div className="infra-list-row" key={row.handle}><code>{row.handle}</code><span>{row.adapter}</span><span className={'badge '+infraBadge(row.status)}>{row.status}</span></div>)}{!operations.loading&&!ops?.workers.length&&<InfraEmpty>No migrated WorkerRun metadata.</InfraEmpty>}</div></div>
      <div className="card"><div className="card-head"><h2>Capability registries</h2></div><div className="card-body infra-authority"><div><span>MCP tools</span><strong>{ops?.mcp.tools.length??'—'}</strong></div><div><span>Skills</span><strong>{ops?.skills.length??'—'}</strong></div><div><span>Backup execution registry</span><strong>{ops?.backup.tracked?'Tracked':'Not tracked'}</strong></div>{ops?.backup.reason&&<p className="muted">{ops.backup.reason}</p>}</div></div>
    </div>
  </section>;
}
