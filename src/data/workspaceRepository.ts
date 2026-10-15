import type { Channel, Message, ChannelResponseDispatch, Task, NewTask, AcceptanceCriterion, AuditLog, Page } from '../domain/models';
import { localOrganizationRepository, type OrganizationRepository } from './organizationRepository';

/** Local demo adapter. Data is namespaced by project, persistent and observable. */
export interface WorkspaceRepository {
  listChannels(projectId: string): Promise<Page<Channel>>;
  createChannel(projectId: string, input: Pick<Channel, 'name' | 'purpose' | 'contextPolicy'>): Promise<Channel>;
  listMessages(channelId: string): Promise<Page<Message>>;
  sendMessage(channelId: string, content: string, responseMode: 'single' | 'multi', artifactIds?: string[]): Promise<Message>;
  listTasks(projectId: string): Promise<Page<Task>>;
  createTask(input: NewTask): Promise<Task>;
  reviewTask(taskId: string): Promise<Task>;
  verifyCriterion(taskId:string,criterionId:string,artifactIds:string[]):Promise<Task>;
  acceptTask(taskId:string,reason:string):Promise<Task>;
  dispatchTask(taskId:string):Promise<{jobId:string;status:string}>;
  respondToChannel(channelId:string,agentId?:string,artifactIds?:string[],taskId?:string):Promise<ChannelResponseDispatch>;
  activity(taskId: string): Promise<Page<AuditLog>>;
  subscribe(listener: () => void): () => void;
}

type Database = { channels: Channel[]; messages: Message[]; tasks: Task[]; logs: AuditLog[] };
const projectId = 'project-1';
const criteria: AcceptanceCriterion[] = [
  { id: 'criterion-1', description: 'Precise definitions and assumptions', satisfied: true },
  { id: 'criterion-2', description: 'Core lemmas implemented', satisfied: true },
  { id: 'criterion-3', description: 'No critical verification errors', satisfied: true },
  { id: 'criterion-4', description: 'Machine-checked artifacts generated', satisfied: true },
  { id: 'criterion-5', description: 'Reproducible evidence bundle attached', satisfied: false },
];
const date = '2026-10-14T13:40:00+08:00';
const seed: Database = {
  channels: [
    { id:'report',projectId,name:'Report channel',purpose:'Final reports and milestones',contextPolicy:'task' },
    { id:'research',projectId,name:'Research collaboration',purpose:'Literature, evidence, and discussion',contextPolicy:'relevant' },
    { id:'engineering',projectId,name:'Engineering',purpose:'Implementation and evaluation',contextPolicy:'relevant' },
    { id:'reviews',projectId,name:'Reviews',purpose:'Feedback and approvals',contextPolicy:'relevant' },
  ],
  messages: [
    {id:'msg-1',channelId:'research',senderName:'Project Manager',content:'Please confirm coverage on existence, uniqueness, and numerical methods before we freeze the related-work set.',createdAt:date},
    {id:'msg-2',channelId:'research',senderName:'Researcher',content:'I verified 37 papers covering NHIM theory and computation. Claim-to-source mappings are attached.',createdAt:date},
    {id:'msg-3',channelId:'research',senderName:'Engineer',content:'The formalization dependencies build successfully. The core theorem definitions are now in the library.',createdAt:date},
    {id:'msg-4',channelId:'research',senderName:'STAGE',content:'Stage complete — related work verified. 37 papers analyzed with complete claim mappings.',createdAt:date,stageCompletion:{level:'high',taskId:'task-4',evidenceArtifactIds:[],verified:true}},
    {id:'msg-5',channelId:'research',senderName:'Researcher',content:'I will start drafting the formalization plan from these results.',createdAt:date},
    {id:'msg-6',channelId:'report',senderName:'Project Manager',content:'Milestone 3 is at 60%. Reviewer validation is queued.',createdAt:date},
    {id:'msg-7',channelId:'engineering',senderName:'Engineer',content:'Proof skeleton compiles. One evidence artifact is still missing.',createdAt:date},
    {id:'msg-8',channelId:'reviews',senderName:'Reviewer',content:'Please make invariant-manifold assumptions explicit.',createdAt:date},
  ],
  tasks: [
    {id:'task-1',projectId,title:'1.1 Formalize and verify NHIM theorem components',description:'Formalize the core theorem components and verify key lemmas.',assigneeRoleId:'role-engineer',status:'running',progress:80,dependencyIds:[],evidenceStatus:'partial',acceptanceCriteria:criteria},
    {id:'task-2',projectId,title:'1.2 Implement proof in Coq/Lean',description:'Translate theorem skeleton into machine-checkable proof.',assigneeRoleId:'role-engineer',status:'todo',progress:0,dependencyIds:['task-1'],evidenceStatus:'missing',acceptanceCriteria:[]},
    {id:'task-3',projectId,title:'1.3 Verify core lemmas',description:'Run formal verification and record proof obligations.',assigneeRoleId:'role-engineer',status:'todo',progress:0,dependencyIds:['task-2'],evidenceStatus:'missing',acceptanceCriteria:[]},
    {id:'task-4',projectId,title:'2. Related-work verification',description:'Verify related work and map claims to sources.',assigneeRoleId:'role-researcher',status:'running',progress:80,dependencyIds:[],evidenceStatus:'complete',acceptanceCriteria:[]},
    {id:'task-5',projectId,title:'3. Controlled rollout experiment',description:'Evaluate long-horizon rollout stability.',assigneeRoleId:'role-engineer',status:'running',progress:60,dependencyIds:['task-4'],evidenceStatus:'partial',acceptanceCriteria:[]},
    {id:'task-6',projectId,title:'4. Reviewer validation',description:'Review evidence and reproducibility.',assigneeRoleId:'role-reviewer',status:'todo',progress:0,dependencyIds:['task-5'],evidenceStatus:'partial',acceptanceCriteria:[]},
  ],
  logs: [
    {id:'log-1',timestamp:date,level:'info',source:'Engineer',message:'Engineer updated proof — Resolved type error',taskId:'task-1',projectId},
    {id:'log-2',timestamp:date,level:'info',source:'Reviewer',message:'Reviewer left feedback — Invariant manifold definition',taskId:'task-1',projectId},
  ],
};
const STORAGE_KEY = 'mrlc.workspace.demo.v2';
function load(): Database {
  try {
    const data = localStorage.getItem(STORAGE_KEY);
    if(data) {
      const raw = JSON.parse(data) as Database;
      if(Array.isArray(raw.channels) && Array.isArray(raw.tasks) && Array.isArray(raw.messages) && Array.isArray(raw.logs)) {
        const legacyRoleIds:Record<string,string>={Engineer:'role-engineer',Researcher:'role-researcher',Reviewer:'role-reviewer','Project Manager':'role-pm'};
        raw.tasks.forEach(task=>{if(task.assigneeRoleId && legacyRoleIds[task.assigneeRoleId])task.assigneeRoleId=legacyRoleIds[task.assigneeRoleId];});
        return raw;
      }
    }
  } catch { /* disabled storage or stale demo data */ }
  return structuredClone(seed);
}
function newId() { return crypto.randomUUID(); }
export class LocalWorkspaceRepository implements WorkspaceRepository {
  constructor(private readonly organization: OrganizationRepository = localOrganizationRepository) {}
  private readonly listeners = new Set<() => void>();
  private readonly db = load();
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private changed() { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(this.db)); } catch { /* volatile fallback */ } this.listeners.forEach(listener => listener()); }
  private assertWritable = async (id:string) => {const {items}=await this.organization.listProjects();if(!items.some(p=>p.id===id && p.status!=='archived'))throw new Error('Project is missing or archived');};
  listChannels = async (id: string) => ({ items: this.db.channels.filter(c => c.projectId===id) });
  createChannel = async (id: string, input: Pick<Channel,'name'|'purpose'|'contextPolicy'>) => {
    await this.assertWritable(id);
    const channel: Channel = { id:newId(), projectId:id, ...input }; this.db.channels.push(channel); this.changed(); return channel;
  };
  listMessages = async (id: string) => ({ items: this.db.messages.filter(m=>m.channelId===id) });
  sendMessage = async (channelId: string, content: string, _responseMode: 'single'|'multi', artifactIds: string[] = []) => {
    const channel=this.db.channels.find(c=>c.id===channelId);
    if(!channel) throw new Error('Channel does not exist');
    await this.assertWritable(channel.projectId);
    if(!content.trim())throw new Error('Message must not be empty');
    if(!Array.isArray(artifactIds)||artifactIds.some(id=>typeof id!=='string'||!id))throw new Error('Invalid artifact IDs');
    if(artifactIds.length){
      const {localArtifactRepository}=await import('./artifactRepository');
      for(const artifactId of new Set(artifactIds)){
        const artifact=await localArtifactRepository.getArtifact(artifactId);
        if(artifact.projectId!==channel.projectId)throw new Error('Artifact belongs to another project');
      }
    }
    const item: Message = {id:newId(),channelId,senderName:'You',content,createdAt:new Date().toISOString(),artifactIds:[...new Set(artifactIds)],responseMode:_responseMode};
    this.db.messages.push(item);this.changed();return item;
  };
  listTasks = async (id: string) => ({ items:this.db.tasks.filter(t=>t.projectId===id) });
  createTask = async (input: NewTask) => {
    if(!input.title.trim()) throw new Error('Task title is required');
    await this.assertWritable(input.projectId);
    const deps=input.dependencyIds||[];
    if(deps.some(dep=>!this.db.tasks.some(t=>t.id===dep&&t.projectId===input.projectId))) throw new Error('Every dependency must be a task in the same project');
    const item: Task={id:newId(),projectId:input.projectId,title:input.title,description:input.description||'',assigneeRoleId:input.assigneeRoleId,status:'todo',progress:0,dependencyIds:input.dependencyIds||[],evidenceStatus:'missing',acceptanceCriteria:(input.acceptanceCriteria||[]).map(c=>({id:newId(),description:c.description,satisfied:false}))};
    this.db.tasks.push(item);this.changed();return item;
  };
  reviewTask = async (id: string) => {
    const task=this.db.tasks.find(t=>t.id===id);if(!task)throw new Error('Task not found');
    await this.assertWritable(task.projectId);
    task.status='review';task.updatedAt=new Date().toISOString();this.db.logs.unshift({id:newId(),timestamp:task.updatedAt,level:'info',source:'You',message:'Review requested',taskId:id,projectId:task.projectId});this.changed();return task;
  };
  verifyCriterion = async (_taskId:string,_criterionId:string,_artifactIds:string[]):Promise<Task> => {throw new Error('Backend-only evidence verification');};
  acceptTask = async (_taskId:string,_reason:string):Promise<Task> => {throw new Error('Backend-only task acceptance');};
  dispatchTask = async (_taskId:string):Promise<{jobId:string;status:string}> => {throw new Error('Mock repository cannot run a real model');};
  respondToChannel = async (_channelId:string,_agentId?:string,_artifactIds:string[]=[],_taskId?:string):Promise<ChannelResponseDispatch> => {throw new Error('Mock repository cannot invoke a real agent');};
  activity = async (id: string) => ({items:this.db.logs.filter(x=>x.taskId===id)});
}
export const localWorkspaceRepository = new LocalWorkspaceRepository();
