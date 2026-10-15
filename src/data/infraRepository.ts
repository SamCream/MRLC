import type {
  InfraStatus,InfraOverview,InfraEvent,InfraReplayState,InfraReplayCompare,InfraSchedulerSnapshot,
  InfraMemoryItem,InfraContextView,InfraBankView,InfraA2cmSnapshot,InfraA2cmCandidate,InfraA2cmPrimaryResult,InfraA2cmReviewResult,InfraFrontierSnapshot,InfraFrontierConsultResult,InfraFrontierDelegateResult,InfraFrontierDelegateAction,InfraProvenance,InfraInvocation,
  InfraConsistency,InfraOperations,InfraBackupRun,InfraBackupActionResult,Page
} from '../domain/models';
import type {Services} from '../api/services';

export interface InfraRepository {
  getStatus():Promise<InfraStatus>;
  getOverview(projectId:string):Promise<InfraOverview>;
  getEvents(projectId:string):Promise<Page<InfraEvent>>;
  getReplay(projectId:string,sequence?:number):Promise<InfraReplayState>;
  compareReplay(projectId:string,fromSequence:number,toSequence:number):Promise<InfraReplayCompare>;
  getScheduler(projectId:string):Promise<InfraSchedulerSnapshot>;
  getMemory(projectId:string,roleId?:string):Promise<Page<InfraMemoryItem>>;
  getContext(projectId:string):Promise<InfraContextView>;
  getBank(projectId:string):Promise<InfraBankView>;
  getA2cm(projectId:string):Promise<InfraA2cmSnapshot>;
  a2cmEnsurePrimary(projectId:string):Promise<InfraA2cmPrimaryResult>;
  a2cmObserve(projectId:string,input:{content:string;evidenceEventId:string;confidence:number;source:'fast_tool'|'primary'|'frontier'}):Promise<InfraA2cmCandidate>;
  a2cmReview(projectId:string,candidateId:string,input:{primarySessionId:string;decision:'PROMOTE'|'REJECT'|'DEFER';policyVersion:string;counterevidence:string[];uncertainty:string[];rationale:string}):Promise<InfraA2cmReviewResult>;
  getFrontier(projectId:string):Promise<InfraFrontierSnapshot>;
  frontierConsult(projectId:string,input:{parentTaskId:string;ownerSessionId:string;capability:string;budget:number;operationId:string}):Promise<InfraFrontierConsultResult>;
  frontierDelegate(projectId:string,input:{parentTaskId:string;ownerSessionId:string;capability:string;objective:string;acceptanceCommands:string[];budget:number;operationId:string;confirmation:'RUN_DELEGATE'}):Promise<InfraFrontierDelegateResult>;
  frontierCancel(projectId:string,requestId:string,rationale:string):Promise<InfraFrontierDelegateAction>;
  frontierReview(projectId:string,requestId:string,input:{ownerSessionId:string;decision:'ACCEPT'|'REWORK';rationale:string}):Promise<InfraFrontierDelegateAction>;
  getProvenance(projectId:string):Promise<InfraProvenance>;
  getInvocations(projectId:string):Promise<Page<InfraInvocation>>;
  getConsistency(projectId:string):Promise<InfraConsistency>;
  getOperations(projectId:string):Promise<InfraOperations>;
  getBackups():Promise<Page<InfraBackupRun>>;
  createBackup():Promise<InfraBackupActionResult>;
  verifyBackup(backupId:string):Promise<InfraBackupActionResult>;
  subscribe(listener:()=>void):()=>void;
}

const unavailable=async()=>{throw new Error('Infra Core requires the HTTP backend');};
export const localInfraRepository:InfraRepository={
  getStatus:unavailable,getOverview:unavailable,getEvents:unavailable,getReplay:unavailable,
  compareReplay:unavailable,getScheduler:unavailable,getMemory:unavailable,getContext:unavailable,getBank:unavailable,
  getA2cm:unavailable,a2cmEnsurePrimary:unavailable,a2cmObserve:unavailable,a2cmReview:unavailable,
  getFrontier:unavailable,frontierConsult:unavailable,frontierDelegate:unavailable,
  frontierCancel:unavailable,frontierReview:unavailable,getProvenance:unavailable,getInvocations:unavailable,getConsistency:unavailable,
  getOperations:unavailable,getBackups:unavailable,createBackup:unavailable,verifyBackup:unavailable,subscribe:()=>()=>{},
};

export function httpInfraRepository(services:Services):InfraRepository{
  const listeners=new Set<()=>void>();
  return {
    getStatus:()=>services.infra.status(),
    getOverview:(projectId)=>services.infra.overview(projectId),
    getEvents:(projectId)=>services.infra.events(projectId),
    getReplay:(projectId,sequence)=>services.infra.replay(projectId,sequence),
    compareReplay:(projectId,fromSequence,toSequence)=>services.infra.replayCompare(projectId,fromSequence,toSequence),
    getScheduler:(projectId)=>services.infra.scheduler(projectId),
    getMemory:(projectId,roleId)=>services.infra.memory(projectId,roleId),
    getContext:(projectId)=>services.infra.context(projectId),
    getBank:(projectId)=>services.infra.bank(projectId),
    getA2cm:(projectId)=>services.infra.a2cm(projectId),
    a2cmEnsurePrimary:(projectId)=>services.infra.a2cmEnsurePrimary(projectId),
    a2cmObserve:(projectId,input)=>services.infra.a2cmObserve(projectId,input),
    a2cmReview:(projectId,candidateId,input)=>services.infra.a2cmReview(projectId,candidateId,input),
    getFrontier:(projectId)=>services.infra.frontier(projectId),
    frontierConsult:(projectId,input)=>services.infra.frontierConsult(projectId,input),
    frontierDelegate:(projectId,input)=>services.infra.frontierDelegate(projectId,input),
    frontierCancel:(projectId,requestId,rationale)=>services.infra.frontierCancel(projectId,requestId,rationale),
    frontierReview:(projectId,requestId,input)=>services.infra.frontierReview(projectId,requestId,input),
    getProvenance:(projectId)=>services.infra.provenance(projectId),
    getInvocations:(projectId)=>services.infra.invocations(projectId),
    getConsistency:(projectId)=>services.infra.consistency(projectId),
    getOperations:(projectId)=>services.infra.operations(projectId),
    getBackups:()=>services.infra.backups(),
    createBackup:()=>services.infra.createBackup(),
    verifyBackup:(backupId)=>services.infra.verifyBackup(backupId),
    subscribe:(listener)=>{listeners.add(listener);return()=>listeners.delete(listener);},
  };
}
