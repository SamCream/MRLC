import {useState} from 'react';
import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import type {
  InfraEvidenceChain,InfraFrontierConsultResult,InfraFrontierDelegateRun
} from '../../domain/models';
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
  const activeSessions=(frontier.data?.sessions??[]).filter(x=>x.lifecycle==='ACTIVE');
  const selectedTask=parentTask||frontier.data?.tasks[0]?.taskId||'';
  const selectedSession=ownerSession||activeSessions[0]?.sessionId||'';
  const consultCapability=frontier.data?.capabilities.consult;
  const delegateCapability=frontier.data?.capabilities.delegate;
  const budgetLimit=consultCapability?.budgetLimit??1000;
  const effectiveBudget=Math.max(1,Math.min(budget,budgetLimit));

  const [consulting,setConsulting]=useState(false);
  const [consultError,setConsultError]=useState<string|null>(null);
  const [consultResult,setConsultResult]=useState<InfraFrontierConsultResult|null>(null);

  const [objective,setObjective]=useState('');
  const [commands,setCommands]=useState('');
  const [delegateConfirmed,setDelegateConfirmed]=useState(false);
  const [delegating,setDelegating]=useState(false);
  const [delegateError,setDelegateError]=useState<string|null>(null);
  const [delegateStatus,setDelegateStatus]=useState<string|null>(null);
  const [selectedRunId,setSelectedRunId]=useState<string|null>(null);
  const [rationale,setRationale]=useState('');
  const selectedRun=(frontier.data?.delegateRuns??[]).find(run=>run.requestId===selectedRunId)
    ??frontier.data?.delegateRuns?.[0]??null;

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

  async function delegate(){
    const acceptanceCommands=commands.split('\n').map(x=>x.trim()).filter(Boolean);
    if(!selectedTask||!selectedSession||!capability.trim()||!objective.trim()||
       !acceptanceCommands.length||!delegateConfirmed)return;
    setDelegating(true);setDelegateError(null);setDelegateStatus(null);
    try{
      const output=await infra.frontierDelegate(projectId,{
        parentTaskId:selectedTask,ownerSessionId:selectedSession,
        capability:capability.trim(),objective:objective.trim(),
        acceptanceCommands,budget:effectiveBudget,operationId:operationId(),
        confirmation:'RUN_DELEGATE',
      });
      setSelectedRunId(output.requestId);
      setDelegateStatus('Delegation '+output.requestId+' queued. Child execution remains isolated until owner review.');
      setDelegateConfirmed(false);
    }catch(e){setDelegateError(e instanceof Error?e.message:String(e));}
    finally{setDelegating(false);}
  }

  async function cancel(run:InfraFrontierDelegateRun){
    if(!rationale.trim())return;
    setDelegating(true);setDelegateError(null);setDelegateStatus(null);
    try{
      const output=await infra.frontierCancel(projectId,run.requestId,rationale.trim());
      setDelegateStatus(run.requestId+' → '+output.state);
      setRationale('');
    }catch(e){setDelegateError(e instanceof Error?e.message:String(e));}
    finally{setDelegating(false);}
  }

  async function review(run:InfraFrontierDelegateRun,decision:'ACCEPT'|'REWORK'){
    if(!rationale.trim())return;
    setDelegating(true);setDelegateError(null);setDelegateStatus(null);
    try{
      const output=await infra.frontierReview(projectId,run.requestId,{
        ownerSessionId:run.ownerSessionId,decision,rationale:rationale.trim(),
      });
      setDelegateStatus(run.requestId+' → '+output.state+(output.mergeSha?' · merge '+output.mergeSha:''));
      setRationale('');
    }catch(e){setDelegateError(e instanceof Error?e.message:String(e));}
    finally{setDelegating(false);}
  }

  return <section className="page infra-page">
    <InfraPageHead title="Provenance" description="Machine-verifiable lineage plus capability-gated Frontier CONSULT and isolated child delegation."/>
    <InfraError error={result.error||frontier.error||consultError||delegateError}/>
    {delegateStatus&&<div className="infra-success" role="status">{delegateStatus}</div>}

    <div className="card infra-frontier-card">
      <div className="card-head">
        <h2>Frontier CONSULT</h2>
        <span className={'badge push '+(consultCapability?.available?'green':'amber')}>{consultCapability?.available?'Available':'Unavailable'}</span>
      </div>
      {!consultCapability?.available?<InfraEmpty>{consultCapability?.reason??'Frontier capability status is loading.'}</InfraEmpty>:<>
        <div className="card-body infra-frontier-form">
          <label><span>Parent task ID</span><select value={selectedTask} onChange={e=>setParentTask(e.target.value)}>{(frontier.data?.tasks??[]).map(task=><option key={task.taskId} value={task.taskId}>{task.taskId} · {task.state}</option>)}</select></label>
          <label><span>Owner session ID</span><select value={selectedSession} onChange={e=>setOwnerSession(e.target.value)}>{activeSessions.map(session=><option key={session.sessionId} value={session.sessionId}>{session.sessionId} · {session.roleId}</option>)}</select></label>
          <label className="grow"><span>Capability / question</span><input value={capability} maxLength={500} onChange={e=>setCapability(e.target.value)} placeholder="e.g. review the mathematical risk in this task"/></label>
          <label><span>Budget</span><input type="number" min={1} max={budgetLimit} value={effectiveBudget} onChange={e=>setBudget(Number(e.target.value)||1)}/></label>
          <button className="btn primary" disabled={consulting||!selectedTask||!selectedSession||!capability.trim()} onClick={()=>void consult()}>{consulting?'Consulting…':'Run CONSULT'}</button>
        </div>
        {consultResult&&<div className="infra-frontier-result"><div><code>{consultResult.requestId}</code><span className={'badge '+infraBadge(consultResult.reviewState)}>{consultResult.reviewState}</span><span>{consultResult.budgetUsed} budget</span></div><p>{consultResult.advice}</p></div>}
      </>}
      <div className="infra-frontier-history">
        {(frontier.data?.requests??[]).filter(request=>request.mode==='CONSULT').map(request=><div className="infra-list-row" key={request.requestId}><code>{request.requestId}</code><span>{request.mode} · {request.budgetUsed}</span><span className={'badge '+infraBadge(request.reviewState)}>{request.reviewState}</span></div>)}
        {!frontier.loading&&!(frontier.data?.requests??[]).some(request=>request.mode==='CONSULT')&&<InfraEmpty>No Frontier consultation has been persisted for this project.</InfraEmpty>}
      </div>
    </div>

    <div className="card infra-frontier-card">
      <div className="card-head">
        <h2>Frontier DELEGATE</h2>
        <span className={'badge push '+(delegateCapability?.available?'green':'amber')}>{delegateCapability?.available?'Available':'Unavailable'}</span>
        {delegateCapability?.worker&&<span className="badge">{delegateCapability.worker}</span>}
        {delegateCapability?.permissionMode&&<span className="badge">{delegateCapability.permissionMode}</span>}
      </div>
      {!delegateCapability?.available?<InfraEmpty>{delegateCapability?.reason??'Delegation capability status is loading.'}</InfraEmpty>:<>
        <div className="card-body infra-delegate-form">
          <div className="infra-delegate-grid">
            <label><span>Parent task ID</span><select value={selectedTask} onChange={e=>setParentTask(e.target.value)}>{(frontier.data?.tasks??[]).map(task=><option key={task.taskId} value={task.taskId}>{task.taskId} · {task.state}</option>)}</select></label>
            <label><span>Owner session ID</span><select value={selectedSession} onChange={e=>setOwnerSession(e.target.value)}>{activeSessions.map(session=><option key={session.sessionId} value={session.sessionId}>{session.sessionId} · {session.roleId}</option>)}</select></label>
            <label><span>Budget</span><input type="number" min={1} max={budgetLimit} value={effectiveBudget} onChange={e=>setBudget(Number(e.target.value)||1)}/></label>
          </div>
          <label><span>Capability</span><input value={capability} maxLength={500} onChange={e=>setCapability(e.target.value)} placeholder="Implement or investigate a bounded child task"/></label>
          <label><span>Objective</span><textarea value={objective} maxLength={8000} onChange={e=>setObjective(e.target.value)} placeholder="Exact child objective; the child cannot merge into main by itself."/></label>
          <label><span>Acceptance commands · one shell command per line</span><textarea value={commands} onChange={e=>setCommands(e.target.value)} placeholder={"python -m pytest -q tests/test_target.py\nnpm run check"}/></label>
          <label className="infra-danger-confirm"><input type="checkbox" checked={delegateConfirmed} onChange={e=>setDelegateConfirmed(e.target.checked)}/><span>I understand this launches an isolated Codex/DSH worker under the configured budget and permission mode. Integration still requires owner review.</span></label>
          <div className="infra-actions"><button className="btn primary" disabled={delegating||!delegateConfirmed||!selectedTask||!selectedSession||!capability.trim()||!objective.trim()||!commands.trim()} onClick={()=>void delegate()}>{delegating?'Submitting…':'RUN DELEGATE'}</button></div>
        </div>
      </>}

      <div className="infra-delegate-runs">
        <div className="card-head"><h3>Delegate runs</h3><span className="badge push">{frontier.data?.delegateRuns.length??0}</span></div>
        <div className="infra-master-detail compact-detail">
          <div className="infra-list">
            {(frontier.data?.delegateRuns??[]).map(run=><button className={'infra-chain-row '+(selectedRun?.requestId===run.requestId?'active':'')} key={run.requestId} onClick={()=>setSelectedRunId(run.requestId)}><code>{run.requestId}</code><span>{run.childTaskId}</span><span className={'badge '+infraBadge(run.state)}>{run.state}</span></button>)}
            {!frontier.loading&&!frontier.data?.delegateRuns.length&&<InfraEmpty>No child delegation has been submitted for this project.</InfraEmpty>}
          </div>
          <div className="infra-delegate-detail">
            {selectedRun?<><div className="infra-kv"><span>Relation</span><code>{selectedRun.relationId}</code><span>Owner</span><code>{selectedRun.ownerSessionId}</code><span>Child session</span><code>{selectedRun.childSessionId}</code><span>Child task</span><code>{selectedRun.childTaskId}</code><span>Branch</span><code>{selectedRun.branch??'not created yet'}</code><span>Commit</span><code>{selectedRun.commitSha??'—'}</code><span>Merge</span><code>{selectedRun.mergeSha??'—'}</code><span>Error</span><span>{selectedRun.error??'—'}</span></div>
              {(selectedRun.state==='QUEUED'||selectedRun.state==='RUNNING'||selectedRun.state==='WAIT_OWNER_REVIEW')&&<label className="infra-rationale"><span>Operator rationale</span><textarea value={rationale} onChange={e=>setRationale(e.target.value)} placeholder="Required for cancel / ACCEPT / REWORK"/></label>}
              <div className="infra-actions">
                {(selectedRun.state==='QUEUED'||selectedRun.state==='RUNNING')&&<button className="btn" disabled={delegating||!rationale.trim()} onClick={()=>void cancel(selectedRun)}>Cancel child</button>}
                {selectedRun.state==='WAIT_OWNER_REVIEW'&&<><button className="btn" disabled={delegating||!rationale.trim()} onClick={()=>void review(selectedRun,'REWORK')}>Request rework</button><button className="btn primary" disabled={delegating||!rationale.trim()} onClick={()=>void review(selectedRun,'ACCEPT')}>Accept & merge</button></>}
              </div>
            </>:<InfraEmpty>Select a delegate run to inspect its internal lifecycle.</InfraEmpty>}
          </div>
        </div>
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
