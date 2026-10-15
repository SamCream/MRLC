import {useMemo,useState} from 'react';
import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import {InfraEmpty,InfraError,InfraMetric,InfraPageHead,infraBadge,infraNum} from './InfraShared';

const infra=repositories.infra;

export default function InfraInvocationsPage(){
  const {projectId}=useAppShell();
  const result=useRepositoryQuery(()=>infra.getInvocations(projectId),[projectId],infra);
  const rows=result.data?.items??[];
  const [selectedKey,setSelectedKey]=useState<string|null>(null);
  const selected=rows.find(x=>x.idempotency_key===selectedKey)??rows[0]??null;
  const totals=useMemo(()=>rows.reduce((acc,row)=>({input:acc.input+row.usage_input,output:acc.output+row.usage_output,unknown:acc.unknown+(row.status==='UNKNOWN'?1:0)}),{input:0,output:0,unknown:0}),[rows]);
  return <section className="page infra-page">
    <InfraPageHead title="Invocations" description="Per-call provider lifecycle and reconciliation ledger. Provider health/model catalog remain in the existing product UI."/>
    <InfraError error={result.error}/>
    <div className="infra-metric-grid compact"><InfraMetric label="Ledger rows" value={rows.length}/><InfraMetric label="Input tokens" value={infraNum(totals.input)}/><InfraMetric label="Output tokens" value={infraNum(totals.output)}/><InfraMetric label="UNKNOWN" value={totals.unknown} tone={totals.unknown?'warn':''}/></div>
    <div className="infra-master-detail">
      <div className="card"><div className="card-head"><h2>Invocation ledger</h2></div><div className="table-wrap"><table className="data-table infra-table"><thead><tr><th>Invocation</th><th>Model</th><th>Mode</th><th>Status</th><th>Usage</th></tr></thead><tbody>{rows.map(row=><tr className={selected?.idempotency_key===row.idempotency_key?'selected':''} key={row.idempotency_key} onClick={()=>setSelectedKey(row.idempotency_key)}><td className="mono">{row.invocation_id}</td><td>{row.model}</td><td>{row.mode}</td><td><span className={'badge '+infraBadge(row.status)}>{row.status}</span></td><td>{infraNum(row.usage_input+row.usage_output)}</td></tr>)}</tbody></table>{!result.loading&&!rows.length&&<InfraEmpty>No model invocation has been recorded for this project.</InfraEmpty>}</div></div>
      <div className="card"><div className="card-head"><h2>Invocation detail</h2></div>{selected?<div className="card-body infra-kv"><span>Idempotency key</span><code>{selected.idempotency_key}</code><span>Invocation ID</span><code>{selected.invocation_id}</code><span>Provider request</span><code>{selected.provider_request_id??'not reported'}</code><span>Status</span><span className={'badge '+infraBadge(selected.status)}>{selected.status}</span><span>Fallback</span><span>{selected.fallback_reason??'none'}</span><span>Usage</span><strong>{selected.usage_input} in · {selected.usage_output} out</strong></div>:<InfraEmpty>Select an invocation.</InfraEmpty>}</div>
    </div>
  </section>;
}
