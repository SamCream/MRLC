import type {Agent,Approval,Artifact,Channel,ComputeSlot,Message,ProjectOverviewSnapshot,RuntimeQueueItem,Task} from '../domain/models';

/** Original HTML L84–107 and L268. Backend aggregate route proposed in API_CATALOG.md. */
export interface OverviewDetails extends ProjectOverviewSnapshot {
  tasks:Task[]; agents:Agent[]; approvals:Approval[]; artifacts:Artifact[]; queue:RuntimeQueueItem[]; slots:ComputeSlot[];
}
import {repositories,runtimeConfig} from './repositories';
import {ApiClient} from '../api/client';
import {createServices} from '../api/services';
const {organization,workspace,approvals,artifacts,compute}=repositories;
export const overviewSubscriptions={subscribe(listener:()=>void){
  const callbacks=[organization,workspace,approvals,artifacts,compute].map(r=>r.subscribe(listener));
  return ()=>callbacks.forEach(unsub=>unsub());
}};

export function projectOverview(projectId:string,input:{tasks:Task[];agents:Agent[];approvals:Approval[];artifacts:Artifact[];queue:RuntimeQueueItem[];slots:ComputeSlot[];channels:Channel[];messages:Message[]}):OverviewDetails {
  // Avoid cross-project leakage even if a backend accidentally returns broad lists.
  const agents=input.agents.filter(x=>x.projectId===projectId);
  const agentIds=new Set(agents.map(x=>x.id));
  const tasks=input.tasks.filter(x=>x.projectId===projectId);
  const approvals=input.approvals.filter(x=>x.projectId===projectId);
  const artifacts=input.artifacts.filter(x=>x.projectId===projectId);
  const queue=input.queue.filter(x=>x.projectId===projectId);
  const slots=input.slots.filter(x=>x.agentId&&agentIds.has(x.agentId));
  const channelIds=new Set(input.channels.filter(x=>x.projectId===projectId).map(x=>x.id));
  const stages=input.messages.filter(x=>channelIds.has(x.channelId)&&x.stageCompletion?.verified).map(x=>({messageId:x.id,title:x.content,verified:true,level:x.stageCompletion!.level,occurredAt:x.createdAt,taskId:x.stageCompletion!.taskId})).sort((a,b)=>b.occurredAt.localeCompare(a.occurredAt));
  const pct=tasks.length?Math.round(tasks.reduce((sum,t)=>sum+t.progress,0)/tasks.length):null;
  return {projectId,taskCount:tasks.length,activeTasks:tasks.filter(t=>t.status==='running').length,
    queuedTasks:queue.length,blockedTasks:tasks.filter(t=>t.status==='blocked').length,
    pendingApprovals:approvals.filter(a=>a.status==='pending').length,
    activeAgents:agents.filter(a=>a.status==='working').length,
    idleAgents:agents.filter(a=>a.status==='idle').length,
    parkedAgents:agents.filter(a=>a.status==='parked').length,
    progressPercent:pct,stages,activeSlots:slots.filter(s=>s.state==='active').length,
    totalSlots:slots.length,budget:null,metrics:null,health:null,
    agents,tasks,approvals,artifacts,queue,slots};
}
export async function getProjectOverview(projectId:string):Promise<OverviewDetails>{
  if(!projectId)return projectOverview('',{tasks:[],agents:[],approvals:[],artifacts:[],queue:[],slots:[],channels:[],messages:[]});
  if(runtimeConfig.mode==='http'){
    // Backend owns all counts, progress, status and milestone verification.
    // Detail collections are display material only; no business aggregate is recalculated here.
    const server=createServices(new ApiClient({baseUrl:runtimeConfig.baseUrl}));
    const [snapshot,t,a,ap,ar,q,s]=await Promise.all([
      server.system.overview(projectId),workspace.listTasks(projectId),organization.listAgents(projectId),
      approvals.listApprovals(projectId),artifacts.listArtifacts(projectId),compute.listQueue(),compute.listSlots(),
    ]);
    return {...snapshot,tasks:t.items,agents:a.items,approvals:ap.items,artifacts:ar.items,
      queue:q.items.filter(x=>x.projectId===projectId),
      slots:s.items.filter(x=>a.items.some(y=>y.id===x.agentId))};
  }
  const [t,a,ap,ar,q,s,ch]=await Promise.all([
    workspace.listTasks(projectId),organization.listAgents(projectId),approvals.listApprovals(projectId),
    artifacts.listArtifacts(projectId),compute.listQueue(),compute.listSlots(),workspace.listChannels(projectId),
  ]);
  const messages=await Promise.all(ch.items.map(c=>workspace.listMessages(c.id)));
  return projectOverview(projectId,{tasks:t.items,agents:a.items,approvals:ap.items,artifacts:ar.items,
    queue:q.items,slots:s.items,channels:ch.items,messages:messages.flatMap(p=>p.items)});
}

/** Compatibility alias for existing domain tests; both modes use the selected repository. */
export const getLocalProjectOverview=getProjectOverview;
