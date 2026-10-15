import type { ComputeSlot, RuntimeQueueItem, RuntimeMetrics, RuntimeBudget, RuntimeSession, Page } from '../domain/models';
/** Discovery: original HTML L230–235, L292, L332–333, L349–350. ALL local slot values are a simulation. */
export interface ComputeRepository {
  listSlots():Promise<Page<ComputeSlot>>;
  pauseSlot(id:string):Promise<ComputeSlot>;
  resumeSlot(id:string):Promise<ComputeSlot>;
  releaseSlot(id:string):Promise<ComputeSlot>;
  listQueue():Promise<Page<RuntimeQueueItem>>;
  listSessions():Promise<Page<RuntimeSession>>;
  getMetrics():Promise<RuntimeMetrics|null>;
  getBudget():Promise<RuntimeBudget|null>;
  subscribe(listener:()=>void):()=>void;
}
type Db={version:4;slots:ComputeSlot[];queue:RuntimeQueueItem[]};
const seed:Db={version:4,slots:[
  {id:'slot-01',state:'active',agentId:'agent-engineer-1',taskId:'task-1',modelId:'demo-local-model',decodeTokensPerSecond:38.6,prefillTokensPerSecond:36.9,contextUsedTokens:136000,contextCapacityTokens:200000},
  {id:'slot-02',state:'active',agentId:'agent-researcher-1',taskId:'task-4',modelId:'demo-local-model',decodeTokensPerSecond:42.1,prefillTokensPerSecond:30.4,contextUsedTokens:67000,contextCapacityTokens:128000},
],queue:[
  {id:'queue-1',position:1,projectId:'project-1',title:'Reviewer validation',requestedRole:'Reviewer',priority:'high',waitReason:'Resources busy'},
  {id:'queue-2',position:2,projectId:'project-1',title:'Final report generation',requestedRole:'Researcher',priority:'medium',waitReason:'Dependency pending'},
  {id:'queue-3',position:3,projectId:'project-1',title:'Integration & testing',requestedRole:'Engineer',priority:'low',waitReason:'Queue position'},
]};
const clone=<T,>(x:T):T=>JSON.parse(JSON.stringify(x)) as T;
function load():Db{try{const raw=localStorage.getItem('mrlc.compute.demo.v4');if(raw){const x=JSON.parse(raw) as Db;if(x.version===4&&Array.isArray(x.slots)&&Array.isArray(x.queue))return x;}}catch{/* memory-only */}return clone(seed);}
export class LocalComputeRepository implements ComputeRepository {
  private readonly db=load();private readonly listeners=new Set<()=>void>();
  subscribe=(f:()=>void)=>{this.listeners.add(f);return ()=>{this.listeners.delete(f);};};
  private changed(){try{localStorage.setItem('mrlc.compute.demo.v4',JSON.stringify(this.db));}catch{/* memory-only */}this.listeners.forEach(f=>f());}
  listSlots=async()=>({items:clone(this.db.slots)});
  pauseSlot=async(id:string)=>this.transition(id,'active','paused');
  resumeSlot=async(id:string)=>this.transition(id,'paused','active');
  releaseSlot=async(id:string)=>{
    const s=this.db.slots.find(x=>x.id===id);if(!s)throw new Error('Slot not found');
    if(s.state==='free')return clone(s);
    if(s.state!=='active'&&s.state!=='paused')throw new Error('Slot cannot be released from current state');
    s.state='free';delete s.agentId;delete s.taskId;delete s.modelId;delete s.leaseExpiresAt;
    delete s.decodeTokensPerSecond;delete s.prefillTokensPerSecond;delete s.contextUsedTokens;delete s.contextCapacityTokens;delete s.vramUsedBytes;delete s.vramCapacityBytes;
    this.changed();return clone(s);
  };
  private async transition(id:string,from:ComputeSlot['state'],to:ComputeSlot['state']){
    const s=this.db.slots.find(x=>x.id===id);if(!s)throw new Error('Slot not found');
    if(s.state!==from)throw new Error(`Slot transition rejected: expected ${from}, got ${s.state}`);
    s.state=to;this.changed();return clone(s);
  }
  listQueue=async()=>({items:clone(this.db.queue)});
  listSessions=async ():Promise<Page<RuntimeSession>>=>({items:[]}); // No real session controller or checkpoint data in the uploaded source.
  getMetrics=async ():Promise<RuntimeMetrics|null>=>null; // No provider sampling; do not represent fixed HTML numbers as live telemetry.
  getBudget=async ():Promise<RuntimeBudget|null>=>null;
}
export const localComputeRepository=new LocalComputeRepository();
