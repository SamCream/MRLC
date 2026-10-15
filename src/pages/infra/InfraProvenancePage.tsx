import {useState} from 'react';
import {repositories} from '../../data/repositories';
import {useAppShell} from '../../app/AppContext';
import {useRepositoryQuery} from '../../hooks/useRepositoryQuery';
import type {InfraEvidenceChain} from '../../domain/models';
import {InfraEmpty,InfraError,InfraPageHead,infraBadge} from './InfraShared';

const infra=repositories.infra;

export default function InfraProvenancePage(){
  const {projectId}=useAppShell();
  const result=useRepositoryQuery(()=>infra.getProvenance(projectId),[projectId],infra);
  const chains=result.data?.chains??[];
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const selected=chains.find(x=>x.request_id===selectedId)??chains[0]??null;
  return <section className="page infra-page">
    <InfraPageHead title="Provenance" description="Machine-verifiable requirement → task → delegation → invocation → artifact → tests → review → commit lineage."/>
    <InfraError error={result.error}/>
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
