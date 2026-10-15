import type {
  InfraStatus,InfraOverview,InfraEvent,InfraReplayState,InfraSchedulerSnapshot,
  InfraMemoryItem,InfraContextView,InfraBankView,InfraProvenance,InfraInvocation,
  InfraConsistency,InfraOperations,Page
} from '../domain/models';
import type {Services} from '../api/services';

export interface InfraRepository {
  getStatus():Promise<InfraStatus>;
  getOverview(projectId:string):Promise<InfraOverview>;
  getEvents(projectId:string):Promise<Page<InfraEvent>>;
  getReplay(projectId:string):Promise<InfraReplayState>;
  getScheduler(projectId:string):Promise<InfraSchedulerSnapshot>;
  getMemory(projectId:string,roleId?:string):Promise<Page<InfraMemoryItem>>;
  getContext(projectId:string):Promise<InfraContextView>;
  getBank(projectId:string):Promise<InfraBankView>;
  getProvenance(projectId:string):Promise<InfraProvenance>;
  getInvocations(projectId:string):Promise<Page<InfraInvocation>>;
  getConsistency(projectId:string):Promise<InfraConsistency>;
  getOperations(projectId:string):Promise<InfraOperations>;
  subscribe(listener:()=>void):()=>void;
}

const unavailable=async()=>{throw new Error('Infra Core requires the HTTP backend');};
export const localInfraRepository:InfraRepository={
  getStatus:unavailable,getOverview:unavailable,getEvents:unavailable,getReplay:unavailable,
  getScheduler:unavailable,getMemory:unavailable,getContext:unavailable,getBank:unavailable,
  getProvenance:unavailable,getInvocations:unavailable,getConsistency:unavailable,
  getOperations:unavailable,subscribe:()=>()=>{},
};

export function httpInfraRepository(services:Services):InfraRepository{
  const listeners=new Set<()=>void>();
  return {
    getStatus:()=>services.infra.status(),
    getOverview:(projectId)=>services.infra.overview(projectId),
    getEvents:(projectId)=>services.infra.events(projectId),
    getReplay:(projectId)=>services.infra.replay(projectId),
    getScheduler:(projectId)=>services.infra.scheduler(projectId),
    getMemory:(projectId,roleId)=>services.infra.memory(projectId,roleId),
    getContext:(projectId)=>services.infra.context(projectId),
    getBank:(projectId)=>services.infra.bank(projectId),
    getProvenance:(projectId)=>services.infra.provenance(projectId),
    getInvocations:(projectId)=>services.infra.invocations(projectId),
    getConsistency:(projectId)=>services.infra.consistency(projectId),
    getOperations:(projectId)=>services.infra.operations(projectId),
    subscribe:(listener)=>{listeners.add(listener);return()=>listeners.delete(listener);},
  };
}
