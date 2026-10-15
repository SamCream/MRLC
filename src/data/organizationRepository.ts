import type { Agent, ID, Page, Project, RoleTemplate } from '../domain/models';

/** Phase 3 discovery ledger: reference/index.original.html lines 194-199, 215-222,
 * 239-242, 277-280, 299-308, 322-324, 344. See API_CATALOG.md before modifying a contract.
 */
export type CreateRole = Pick<RoleTemplate, 'name' | 'purpose' | 'capabilities' | 'allowedPermissions'> &
  Partial<Pick<RoleTemplate, 'preferredModelId' | 'lifecycle'>>;
export type UpdateRole = Partial<Pick<RoleTemplate, 'name' | 'purpose' | 'capabilities' | 'allowedPermissions' | 'preferredModelId'>>;
export type RoleTransition = 'activate' | 'park' | 'archive';
export type UpdateProject = Partial<Pick<Project, 'name' | 'description'>>;
export type AgentAssignment = { projectId: ID; roleTemplateId: ID; name?: string };
export interface OrganizationRepository {
  subscribe(listener: () => void): () => void;
  listProjects(): Promise<Page<Project>>;
  createProject(input: Pick<Project, 'name' | 'description'>): Promise<Project>;
  updateProject(projectId: ID, patch: UpdateProject): Promise<Project>;
  archiveProject(projectId: ID, reason?: string): Promise<Project>;
  listRoles(): Promise<Page<RoleTemplate>>;
  createRole(input: CreateRole): Promise<RoleTemplate>;
  updateRole(roleId: ID, patch: UpdateRole): Promise<RoleTemplate>;
  transitionRole(roleId: ID, action: RoleTransition): Promise<RoleTemplate>;
  listAgents(projectId: ID): Promise<Page<Agent>>;
  assignAgent(input: AgentAssignment): Promise<Agent>;
  parkAgent(agentId: ID): Promise<Agent>;
  resumeAgent(agentId: ID): Promise<Agent>;
}

type OrganizationDb = { schemaVersion: 3; projects: Project[]; roles: RoleTemplate[]; agents: Agent[]; archiveReasons: Record<ID, string> };
const seed: OrganizationDb = {
  schemaVersion: 3,
  projects: [
    { id: 'project-1', name: 'World Model Research', description: 'NHIM verification and controlled rollout', status: 'running', progress: 60 },
    { id: 'project-2', name: 'Operator Learning', description: 'Koopman latent dynamics benchmark', status: 'blocked', progress: 35 },
    { id: 'project-3', name: 'PDE Benchmarking', description: 'Long-horizon stability evaluation', status: 'idle', progress: 82 },
  ],
  roles: [
    {id:'role-pm',name:'Project Manager',version:'v2',purpose:'Coordinate milestones, dependencies, decisions and project reporting.',capabilities:['Plan','Track','Analyze'],preferredModelId:'Claude 3.5',allowedPermissions:['artifacts.read','artifacts.draft','tasks.plan'],lifecycle:'active'},
    {id:'role-engineer',name:'Engineer',version:'v3',purpose:'Formalize mathematical statements, implement proofs, verify components and build artifacts.',capabilities:['Formalize','Verify','Implement'],preferredModelId:'Claude 3.5',allowedPermissions:['artifacts.read','artifacts.draft','tasks.execute'],lifecycle:'active'},
    {id:'role-researcher',name:'Researcher',version:'v2',purpose:'Verify sources and construct evidence-backed research syntheses.',capabilities:['Search','Analyze','Synthesize'],preferredModelId:'GPT-4o',allowedPermissions:['artifacts.read','artifacts.draft'],lifecycle:'active'},
    {id:'role-reviewer',name:'Reviewer',version:'v1',purpose:'Validate evidence, critique reasoning and issue acceptance recommendations.',capabilities:['Review','Critique','Verify'],preferredModelId:'GPT-4o',allowedPermissions:['artifacts.read','tasks.review'],lifecycle:'active'},
    {id:'role-reporter',name:'Reporter',version:'v1',purpose:'Produce traceable milestone reports from structured project events.',capabilities:['Summarize','Write','Cite'],preferredModelId:'Claude 3.5',allowedPermissions:['artifacts.read','artifacts.draft'],lifecycle:'active'},
    {id:'role-coordinator',name:'Coordinator',version:'v1',purpose:'Detect dependencies, conflicts and omissions in parallel work.',capabilities:['Orchestrate','Monitor'],preferredModelId:'GPT-4o',allowedPermissions:['tasks.read'],lifecycle:'draft'},
    {id:'role-cuda',name:'CUDA Performance Analyst',version:'v1',purpose:'Profile GPU workloads and diagnose inference bottlenecks.',capabilities:['Profile','Analyze'],preferredModelId:'Local 27B',allowedPermissions:['compute.read'],lifecycle:'parked'},
  ],
  agents: [
    {id:'agent-pm-1',projectId:'project-1',roleTemplateId:'role-pm',name:'Project Manager',status:'idle'},
    {id:'agent-engineer-1',projectId:'project-1',roleTemplateId:'role-engineer',name:'Engineer',status:'working',taskId:'task-1'},
    {id:'agent-researcher-1',projectId:'project-1',roleTemplateId:'role-researcher',name:'Researcher',status:'working',taskId:'task-4'},
    {id:'agent-reviewer-1',projectId:'project-1',roleTemplateId:'role-reviewer',name:'Reviewer',status:'idle'},
    {id:'agent-pm-2',projectId:'project-2',roleTemplateId:'role-pm',name:'Project Manager',status:'idle'},
    {id:'agent-engineer-2',projectId:'project-2',roleTemplateId:'role-engineer',name:'Engineer',status:'paused'},
    {id:'agent-reviewer-2',projectId:'project-2',roleTemplateId:'role-reviewer',name:'Reviewer',status:'idle'},
    {id:'agent-engineer-3',projectId:'project-3',roleTemplateId:'role-engineer',name:'Engineer',status:'idle'},
    {id:'agent-researcher-3',projectId:'project-3',roleTemplateId:'role-researcher',name:'Researcher',status:'idle'},
  ],
  archiveReasons: {},
};
const STORAGE_KEY = 'mrlc.organization.demo.v3';
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
function load(): OrganizationDb {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const data: unknown = JSON.parse(raw);
      if (data && typeof data === 'object' && 'schemaVersion' in data && data.schemaVersion === 3 &&
          'projects' in data && Array.isArray(data.projects) && 'roles' in data && Array.isArray(data.roles) &&
          'agents' in data && Array.isArray(data.agents)) return data as OrganizationDb;
    }
  } catch { /* no storage / bad demo state */ }
  return clone(seed);
}
const id = () => typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `mrlc-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const required = (value: string, label: string) => { if (!value.trim()) throw new Error(`${label} is required`); return value.trim(); };
const roleVersion = (version: string) => `v${Number(version.replace(/^v/, '')) + 1 || 1}`;

export class LocalOrganizationRepository implements OrganizationRepository {
  private readonly listeners = new Set<() => void>();
  private readonly db = load();
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private changed() { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(this.db)); } catch { /* volatile fallback */ } for (const fn of this.listeners) fn(); }
  listProjects = async () => ({ items: clone(this.db.projects) });
  createProject = async (input: Pick<Project, 'name' | 'description'>) => {
    const item: Project = {id:id(),name:required(input.name,'Project name'),description:input.description.trim(),status:'idle',progress:0,createdAt:new Date().toISOString()};
    this.db.projects.push(item);this.changed();return clone(item);
  };
  updateProject = async (projectId: ID, patch: UpdateProject) => {
    const project = this.db.projects.find(p=>p.id===projectId);if(!project)throw new Error('Project not found');
    if(project.status==='archived')throw new Error('Archived project is read-only');
    if(patch.name !== undefined)project.name=required(patch.name,'Project name');
    if(patch.description !== undefined)project.description=patch.description.trim();
    this.changed();return clone(project);
  };
  archiveProject = async (projectId: ID, reason?: string) => {
    const project=this.db.projects.find(p=>p.id===projectId);if(!project)throw new Error('Project not found');
    project.status='archived';this.db.archiveReasons[projectId]=reason||'';
    this.db.agents.filter(a=>a.projectId===projectId && ['working','idle','paused'].includes(a.status)).forEach(a=>{a.status='parked';});
    this.changed();return clone(project);
  };
  listRoles = async () => ({items:clone(this.db.roles)});
  createRole = async (input: CreateRole) => {
    const item: RoleTemplate={id:id(),name:required(input.name,'Role name'),version:'v1',purpose:input.purpose.trim(),capabilities:[...new Set(input.capabilities.map(x=>x.trim()).filter(Boolean))],allowedPermissions:input.allowedPermissions,preferredModelId:input.preferredModelId,lifecycle:input.lifecycle??'draft'};
    this.db.roles.push(item);this.changed();return clone(item);
  };
  updateRole = async (roleId: ID, patch: UpdateRole) => {
    const role=this.db.roles.find(r=>r.id===roleId);if(!role)throw new Error('Role not found');
    if(role.lifecycle==='archived')throw new Error('Archived role is read-only');
    if(patch.name!==undefined)role.name=required(patch.name,'Role name');
    if(patch.purpose!==undefined)role.purpose=patch.purpose.trim();
    if(patch.capabilities!==undefined)role.capabilities=[...new Set(patch.capabilities.map(x=>x.trim()).filter(Boolean))];
    if(patch.allowedPermissions!==undefined)role.allowedPermissions=[...patch.allowedPermissions];
    if(patch.preferredModelId!==undefined)role.preferredModelId=patch.preferredModelId;
    role.version=roleVersion(role.version);this.changed();return clone(role);
  };
  transitionRole = async (roleId: ID, action: RoleTransition) => {
    const role=this.db.roles.find(r=>r.id===roleId);if(!role)throw new Error('Role not found');
    if(role.lifecycle==='archived')throw new Error('Archived role cannot be reactivated');
    if(action==='archive' && this.db.agents.some(a=>a.roleTemplateId===roleId && !['archived','parked'].includes(a.status))) throw new Error('Role has active agents: park them before archiving the template');
    role.lifecycle=action==='activate'?'active':action==='park'?'parked':'archived';this.changed();return clone(role);
  };
  listAgents = async (projectId: ID) => ({items:clone(this.db.agents.filter(a=>a.projectId===projectId))});
  assignAgent = async (input: AgentAssignment) => {
    const project=this.db.projects.find(p=>p.id===input.projectId);if(!project || project.status==='archived')throw new Error('Project cannot accept new agents');
    const role=this.db.roles.find(r=>r.id===input.roleTemplateId);if(!role || role.lifecycle!=='active')throw new Error('Role template must be active');
    const item: Agent={id:id(),projectId:input.projectId,roleTemplateId:role.id,name:input.name?.trim()||role.name,status:'idle'};
    this.db.agents.push(item);this.changed();return clone(item);
  };
  parkAgent = async (agentId: ID) => {
    const agent=this.db.agents.find(a=>a.id===agentId);if(!agent)throw new Error('Agent not found');
    if(agent.status==='archived')throw new Error('Archived agent cannot be parked');
    agent.status='parked';this.changed();return clone(agent);
  };
  resumeAgent = async (agentId: ID) => {
    const agent=this.db.agents.find(a=>a.id===agentId);if(!agent)throw new Error('Agent not found');
    const project=this.db.projects.find(p=>p.id===agent.projectId);
    const role=this.db.roles.find(r=>r.id===agent.roleTemplateId);
    if(project?.status==='archived'||role?.lifecycle!=='active'||agent.status==='archived')throw new Error('Agent cannot resume in an archived project or with an inactive role template');
    // This is a UI simulation only; real backend must restore a checkpoint / lease.
    agent.status='idle';this.changed();return clone(agent);
  };
}
export const localOrganizationRepository = new LocalOrganizationRepository();
