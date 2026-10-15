import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import {InfraEmpty,InfraError,InfraMetric,InfraPageHead,infraBadge,infraSeconds} from './InfraShared';

const infra=repositories.infra;

export default function InfraSchedulerPage(){
  const {projectId}=useAppShell();
  const result=useRepositoryQuery(()=>infra.getScheduler(projectId),[projectId],infra);
  const consistency=useRepositoryQuery(()=>infra.getConsistency(projectId),[projectId],infra);
  const data=result.data;
  return <section className="page infra-page">
    <InfraPageHead title="Scheduler & Leases" description="Scheduler arbitration, queue aging and fencing ownership. Compute/GPU telemetry is intentionally not repeated here."/>
    <InfraError error={result.error||consistency.error}/>
    <div className="infra-metric-grid compact">
      <InfraMetric label="Capacity" value={data?data.capacity:'—'} meta="logical scheduler capacity"/>
      <InfraMetric label="Active leases" value={data?data.active:'—'} meta="ACTIVE / DRAINING sessions"/>
      <InfraMetric label="Queued" value={data?data.queued:'—'} meta="scheduler-level wait state"/>
      <InfraMetric label="Parked" value={data?data.parked:'—'} meta="preserved session ownership"/>
    </div>
    <div className="card">
      <div className="card-head"><h2>Arbitration state</h2><span className="sub">No task titles or GPU metrics duplicated from Compute</span></div>
      <div className="table-wrap"><table className="data-table infra-table"><thead><tr><th>Session</th><th>Slot state</th><th>Task state</th><th>Priority</th><th>Wait</th><th>Fence</th><th>Lease age</th><th>Park</th></tr></thead><tbody>
        {(data?.sessions??[]).map(item=><tr key={item.sessionId}><td className="mono">{item.sessionId}</td><td><span className={'badge '+infraBadge(item.slotState)}>{item.slotState}</span></td><td>{item.taskState}</td><td>{item.priority}</td><td>{infraSeconds(item.waitSeconds)}</td><td className="mono">#{item.fencingToken}</td><td>{infraSeconds(item.leaseAgeSeconds)}</td><td>{item.parkCount}{item.parkRequested?' · requested':''}</td></tr>)}
      </tbody></table>{!result.loading&&!data?.sessions.length&&<InfraEmpty>No sessions are currently known to the scheduler for this project.</InfraEmpty>}</div>
    </div>
    <div className="infra-two-col">
      <div className="card"><div className="card-head"><h2>Consistency leases</h2><span className="badge push">{consistency.data?.leases.length??0}</span></div><div className="infra-list">
        {(consistency.data?.leases??[]).map(item=><div className="infra-list-row" key={item.resource_id}><code>{item.resource_id}</code><span>{item.holder_id}</span><strong>fence #{item.fencing_token}</strong></div>)}
        {!consistency.loading&&!consistency.data?.leases.length&&<InfraEmpty>No project-scoped consistency leases.</InfraEmpty>}
      </div></div>
      <div className="card"><div className="card-head"><h2>Priority adjustments</h2><span className="badge push">{data?.priorityAdjustments.length??0}</span></div><div className="infra-list">
        {(data?.priorityAdjustments??[]).slice().reverse().map((item,index)=><div className="infra-list-row" key={index}><code>{String(item.session_id??'session')}</code><span>{String(item.old_priority??'—')} → {String(item.new_priority??'—')}</span><span>{String(item.reason??'')}</span></div>)}
        {!result.loading&&!data?.priorityAdjustments.length&&<InfraEmpty>No manual priority adjustment has been recorded.</InfraEmpty>}
      </div></div>
    </div>
  </section>;
}
