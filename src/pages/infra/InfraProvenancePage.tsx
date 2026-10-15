import {useState} from 'react';
import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import type {InfraEvidenceChain,InfraFrontierConsultResult} from '../../domain/models';
import {InfraEmpty,InfraError,InfraPageHead,infraBadge} from './InfraShared';

const infra=repositories.infra;

function operationId(){
  try{return 'frontier-'+crypto.randomUUID();}
  catch{return 'frontier-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);}
}

export default function InfraProvenancePage(){
  const {projectId}=useAppShell();
  const result=useRepositoryQuery(()=>infra.getProvenance(projectId),[projectId],infra);
  const frontier=useRepositoryQuery(()=>infra.getFrontier(projectId),[projectId],infra);
  const chains=result.data?.chains??[];
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const selected=chains.find(x=>x.request_id===selectedId)??chains[0]??null;
  const [parentTask,setParentTask]=useState('');
  const [ownerSession,setOwnerSession]=useState('');
  const [capability,setCapability]=useState('');
  const [budget,setBudget]=useState(100);
  const [consulting,setConsulting]=useState(false);
  const [consultError,setConsultError]=useState<string|null>(null);
  const [consultResult,setConsultResult]=useState<InfraFrontierConsultResult|null>(null);
  const activeSessions=(frontier.data?.sessions??[]).filter(x=>x.lifecycle==='ACTIVE');
  const selectedTask=parentTask||frontier.data?.tasks[0]?.taskId||'';
  const selectedSession=ownerSession||activeSessions[0]?.sessionId||'';
  const consultCapability=frontier.data?.capabilities.consult;
  const budgetLimit=consultCapability?.budgetLimit??1000;
  const effectiveBudget=Math.max(1,Math.min(budget,budgetLimit));

  async function consult(){
    if(!selectedTask||!selectedSession||!capability.trim())return;
    setConsulting(true);setConsultError(null);setConsultResult(null);
    try{
      const output=await infra.frontierConsult(projectId,{
        parentTaskId:selectedTask,ownerSessionId:selectedSession,
        capability:capability.trim(),budget:effectiveBudget,operationId:operationId(),
      });
      setConsultResult(output);
    }catch(e){setConsultError(e instanceof Error?e.message:String(e));}
    finally{setConsulting(false);}
  }

  return <section className="page infra-page">
    <InfraPageHead title="Provenance" description="Machine-verifiable lineage plus capability-gated Frontier consultation. Child delegation remains disabled until its WorkerRun authority is unified."/>
    <InfraError error={result.error||frontier.error||consultError}/>

    <div className="card infra-frontier-card">
      <div className="card-head">
        <h2>Frontier consultation</h2>
        <span className={'badge push '+(consultCapability?.available?'green':'amber')}>{consultCapability?.available?'CONSULT available':'CONSULT unavailable'}</span>
        <span className="badge">DELEGATE disabled</span>
      </div>
      {!consultCapability?.available?<InfraEmpty>{consultCapability?.reason??'Frontier capability status is loading.'}</InfraEmpty>:<>
        <div className="card-body infra-frontier-form">
          <label><span>Parent task</span><select value={selectedTask} onChange={e=>setParentTask(e.target.value)}>{(frontier.data?.tasks??[]).map(task=><option key={task.taskId} value={task.taskId}>{task.taskId} · {task.state}</option>)}</select></label>
          <label><span>Owner session</span><select value={selectedSession} onChange={e=>setOwnerSession(e.target.value)}>{activeSessions.map(session=><option key={session.sessionId} value={session.sessionId}>{session.sessionId} · {session.roleId}</option>)}</select></label>
          <label className="grow"><span>Capability / question</span><input value={capability} maxLength={500} onChange={e=>setCapability(e.target.value)} placeholder="e.g. review the mathematical risk in this task"/></label>
          <label><span>Budget</span><input type="number" min={1} max={budgetLimit} value={effectiveBudget} onChange={e=>setBudget(Number(e.target.value)||1)}/></label>
          <button className="btn primary" disabled={consulting||!selectedTask||!selectedSession||!capability.trim()} onClick={()=>void consult()}>{consulting?'Consulting…':'Run CONSULT'}</button>
        </div>
        {consultResult&&<div className="infra-frontier-result"><div><code>{consultResult.requestId}</code><span className={'badge '+infraBadge(consultResult.reviewState)}>{consultResult.reviewState}</span><span>{consultResult.budgetUsed} budget</span></div><p>{consultResult.advice}</p></div>}
      </>}
      <div className="infra-frontier-history">
        {(frontier.data?.requests??[]).map(request=><div className="infra-list-row" key={request.requestId}><code>{request.requestId}</code><span>{request.mode} · {request.budgetUsed}</span><span className={'badge '+infraBadge(request.reviewState)}>{request.reviewState}</span></div>)}
        {!frontier.loading&&!frontier.data?.requests.length&&<InfraEmpty>No Frontier consultation has been persisted for this project.</InfraEmpty>}
      </div>
    </div>

    <div className="card">
      <div className="card-head"><h2>Evidence-chain schema</h2><span className="badge push">{chains.length} chains</span></div>
      <div className="infra-chain-summary">{(result.data?.stages??[]).map(stage=><span key={stage}>{stage}</span>)}</div>
    </div>
    <div className="infra-master-detail">
      <div className="card"><div className="card-head"><h2>Chains</h2></div><div className="infra-list">{chains.map(chain=><button className={'infra-chain-row '+(selected?.request_id===chain.request_id?'active':'')} key={chain.request_id} onClick={()=>setSelectedId(chain.request_id)}><code>{chain.request_id}</code><span>{chain.run_state}</span><span className={'badge '+infraBadge(chain.chain_state)}>{chain.chain_state}</span></button>)}{!result.loading&&!chains.length&&<InfraEmpty>No Frontier/delegation evidence chain exists for this project.</InfraEmpty>}</div></div>
      <div className="card"><div className="card-head"><h2>Chain detail</h2>{selected&&<span className={'badge push '+infraBadge(selected.chain_state)}>{selected.chain_state}</span>}</div>{selected?<EvidenceChain chain={selected}/>:<InfraEmpty>Select a chain to inspect authoritative links.</InfraEmpty>}</div>
    </div>
  </section>;
}
function EvidenceChain({chain}:{chain:InfraEvidenceChain}){
  return <div className="infra-evidence-chain">{chain.nodes.map((node,index)=><div className="infra-evidence-node" key={node.stage}><div className={'infra-evidence-dot '+infraBadge(node.status)}>{index+1}</div><div><strong>{node.stage}</strong><span className={'badge '+infraBadge(node.status)}>{node.status}</span><p>{node.summary??'No persisted record for this stage.'}</p>{node.ref&&<code>{node.ref}</code>}{node.source&&<small>{node.source}</small>}</div></div>)}</div>;
}
