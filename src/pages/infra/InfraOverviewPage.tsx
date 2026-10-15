import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import {InfraError,InfraMetric,InfraPageHead,infraNum} from './InfraShared';

const infra=repositories.infra;

export default function InfraOverviewPage(){
  const {projectId}=useAppShell();
  const overview=useRepositoryQuery(()=>infra.getOverview(projectId),[projectId],infra);
  const status=useRepositoryQuery(()=>infra.getStatus(),[],infra);
  const data=overview.data;
  const anomalies=[
    data&&data.events.sequenceGapCount>0?String(data.events.sequenceGapCount)+' event sequence gap(s)':null,
    data&&data.consistency.deadLetters>0?String(data.consistency.deadLetters)+' dead-letter message(s)':null,
    data&&data.invocations.unknown>0?String(data.invocations.unknown)+' UNKNOWN invocation(s) require reconciliation':null,
    data&&!data.replay.healthy?'Replay failed: '+(data.replay.error??'unknown reason'):null,
  ].filter(Boolean) as string[];
  return <section className="page infra-page">
    <InfraPageHead title="Infra Overview" description="Authoritative backend state, replayability and consistency health. Product progress is intentionally omitted." tag="Infra Core"/>
    <InfraError error={overview.error||status.error}/>
    <div className="infra-metric-grid">
      <InfraMetric label="Domain Event Store" value={data?infraNum(data.events.count):'—'} meta={data?'seq '+String(data.events.firstSequence??'—')+' → '+String(data.events.lastSequence??'—')+' · gaps '+data.events.sequenceGapCount:'Loading…'} tone={data?.events.sequenceGapCount?'warn':''}/>
      <InfraMetric label="Replay" value={data?(data.replay.healthy?'Deterministic':'Diverged'):'—'} meta={data?.replay.healthy?'Current domain stream replays successfully':data?.replay.error??'Loading…'} tone={data?.replay.healthy?'ok':'warn'}/>
      <InfraMetric label="Consistency" value={data?infraNum(data.consistency.outboxPending):'—'} meta={data?String(data.consistency.deadLetters)+' dead letters':'Loading…'} tone={data?.consistency.deadLetters?'warn':''}/>
      <InfraMetric label="Memory Store" value={data?infraNum(data.memory.active):'—'} meta={data?String(data.memory.superseded)+' superseded · '+String(data.memory.total)+' total':'Loading…'}/>
      <InfraMetric label="Gateway Ledger" value={data?infraNum(data.invocations.total):'—'} meta={data?String(data.invocations.unknown)+' unknown · '+String(data.invocations.failed)+' failed':'Loading…'} tone={data?.invocations.unknown?'warn':''}/>
      <InfraMetric label="Scheduler Core" value={data?infraNum(data.scheduler.active):'—'} meta={data?'active leases · capacity '+String(data.scheduler.capacity):'Loading…'}/>
    </div>
    <div className="infra-two-col">
      <div className="card">
        <div className="card-head"><h2>Infra anomalies</h2><span className={'badge push '+(anomalies.length?'amber':'green')}>{anomalies.length?String(anomalies.length)+' attention':'Clear'}</span></div>
        <div className="card-body">{anomalies.length?anomalies.map(item=><div className="status-line" key={item}><span className="dot amber"/><span>{item}</span></div>):<div className="infra-empty">No replay, dead-letter, event-sequence or UNKNOWN-invocation anomaly is currently reported.</div>}</div>
      </div>
      <div className="card">
        <div className="card-head"><h2>Authority map</h2><span className="badge push">{status.data?.mode??'Loading'}</span></div>
        <div className="card-body infra-authority">
          <div><span>Product projection</span><strong>{status.data?.projection??'—'}</strong></div>
          <div><span>UI invalidation stream</span><strong>{status.data?.uiEvents??'—'}</strong></div>
          <div><span>Domain event truth</span><strong>{status.data?.domainEvents??'—'}</strong></div>
          <div><span>Database</span><strong className="mono infra-path">{status.data?.database??'—'}</strong></div>
        </div>
      </div>
    </div>
  </section>;
}
