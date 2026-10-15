import type {
  InfraStatus,InfraOverview,InfraEvent,InfraReplayState,InfraReplayCompare,InfraSchedulerSnapshot,
  InfraMemoryItem,InfraContextView,InfraBankView,InfraFrontierSnapshot,InfraFrontierConsultResult,InfraProvenance,InfraInvocation,
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
  getFrontier(projectId:string):Promise<InfraFrontierSnapshot>;
  frontierConsult(projectId:string,input:{parentTaskId:string;ownerSessionId:string;capability:string;budget:number;operationId:string}):Promise<InfraFrontierConsultResult>;
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
  getProvenance:unavailable,getInvocations:unavailable,getConsistency:unavailable,
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
    getFrontier:(projectId)=>services.infra.frontier(projectId),
    frontierConsult:(projectId,input)=>services.infra.frontierConsult(projectId,input),
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
