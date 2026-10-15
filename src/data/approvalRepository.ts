import type { Approval, ApprovalDecisionInput, ApprovalRecord, Page } from '../domain/models';
import { localOrganizationRepository, type OrganizationRepository } from './organizationRepository';
/** Discovery: original HTML L224-228, L291, L345-348; destructive splice has been replaced by audit-preserving decisions. */
export interface ApprovalRepository {
  listApprovals(projectId?:string):Promise<Page<Approval>>;
  getApproval(id:string):Promise<Approval>;
  listDecisions(id:string):Promise<Page<ApprovalRecord>>;
  decide(id:string,input:ApprovalDecisionInput):Promise<Approval>;
  subscribe(listener:()=>void):()=>void;
}
type Db={schemaVersion:4;approvals:Approval[];history:ApprovalRecord[]};
const at='2026-10-14T00:00:00+08:00';
const seed:Db={schemaVersion:4,history:[],approvals:[
  {id:'approval-1',projectId:'project-1',requestedByAgentId:'agent-reviewer-1',requestedByLabel:'Reviewer',title:'Reviewer validation',type:'Task acceptance',risk:'medium',status:'pending',evidenceArtifactIds:[],createdAt:at,revision:1,description:'Request formal reviewer validation for the current proof and evidence.',impact:'Advance the task to review after the reviewer checks the evidence.'},
  {id:'approval-2',projectId:'project-1',requestedByAgentId:'agent-engineer-1',requestedByLabel:'Engineer',title:'High-memory compute request',type:'Permission',risk:'high',status:'pending',evidenceArtifactIds:[],createdAt:at,revision:1,description:'Request temporary access to high-memory compute resources.',impact:'Elevated compute permission for the approved scope only.'},
  {id:'approval-3',projectId:'project-2',requestedByAgentId:'agent-reviewer-2',requestedByLabel:'Reviewer',title:'Frontier model escalation',type:'Budget',risk:'medium',status:'pending',evidenceArtifactIds:[],createdAt:at,revision:1,description:'Request external-model budget for conflicting technical claims.',impact:'External compute billing; amount must be confirmed by backend.'},
  {id:'approval-4',projectId:'project-3',requestedByAgentId:'agent-engineer-3',requestedByLabel:'Engineer',title:'Archive inactive role',type:'Role lifecycle',risk:'low',status:'pending',evidenceArtifactIds:[],createdAt:at,revision:1,description:'Request archival of a temporary role after completion.',impact:'Revoke future use of the role template after dependent agents are parked.'},
]};
const clone=<T,>(x:T):T=>JSON.parse(JSON.stringify(x)) as T;
function load():Db{try{const raw=localStorage.getItem('mrlc.approvals.demo.v4');if(raw){const x=JSON.parse(raw) as Db;if(x.schemaVersion===4&&Array.isArray(x.approvals)&&Array.isArray(x.history))return x;}}catch{/* memory-only */}return clone(seed);}
const makeId=()=>typeof crypto!=='undefined'&&typeof crypto.randomUUID==='function'?crypto.randomUUID():`approval-event-${Date.now()}-${Math.random().toString(36).slice(2)}`;
export class LocalApprovalRepository implements ApprovalRepository {
  private readonly db=load();private readonly listeners=new Set<()=>void>();
  constructor(private readonly organization:OrganizationRepository=localOrganizationRepository){}
  subscribe=(f:()=>void)=>{this.listeners.add(f);return ()=>{this.listeners.delete(f);};};
  private changed(){try{localStorage.setItem('mrlc.approvals.demo.v4',JSON.stringify(this.db));}catch{/* memory-only */}this.listeners.forEach(f=>f());}
  listApprovals=async(projectId?:string)=>({items:clone(this.db.approvals.filter(a=>!projectId||a.projectId===projectId))});
  getApproval=async(id:string)=>{const a=this.db.approvals.find(x=>x.id===id);if(!a)throw new Error('Approval not found');return clone(a);};
  listDecisions=async(id:string)=>({items:clone(this.db.history.filter(h=>h.approvalId===id))});
  decide=async(id:string,input:ApprovalDecisionInput)=>{
    const a=this.db.approvals.find(x=>x.id===id);if(!a)throw new Error('Approval not found');
    if(a.status!=='pending')throw new Error('Decision conflict: approval already resolved');
    if(!Number.isInteger(input.expectedRevision)||a.revision!==input.expectedRevision)throw new Error('Decision conflict: stale revision');
    if(!['approve','reject','reduce_scope','request_details'].includes(input.decision))throw new Error('Invalid decision');
    const reason=input.reason?.trim()??'';
    if(input.decision==='reduce_scope'){
      const scope=input.scopeOverride;
      if(!scope||!Object.keys(scope).length||Object.values(scope).some(v=>!['string','boolean','number'].includes(typeof v)))
        throw new Error('Reduced scope requires a nonempty structured scopeOverride');
    }
    if(['reduce_scope','request_details','reject'].includes(input.decision)&&!reason)throw new Error('This decision requires a reason');
    const project=(await this.organization.listProjects()).items.find(p=>p.id===a.projectId);
    if(!project||project.status==='archived')throw new Error('Cannot decide requests for an archived project');
    const now=new Date().toISOString();a.revision+=1;
    this.db.history.push({id:makeId(),approvalId:id,decision:input.decision,reason,decidedAt:now,revision:a.revision,actor:'Demo user',scopeOverride:input.decision==='reduce_scope'?clone(input.scopeOverride):undefined});
    if(input.decision!=='request_details'){a.status='resolved';a.resolution=input.decision;a.resolvedAt=now;}
    this.changed();return clone(a);
  };
}
export const localApprovalRepository=new LocalApprovalRepository();
