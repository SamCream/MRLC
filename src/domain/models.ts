/** Client contracts: proposals until the MRLC backend schema is confirmed. */
export type ID = string;
export type ISODateTime = string;
export type TaskStatus = 'todo' | 'queued' | 'running' | 'blocked' | 'review' | 'accepted' | 'failed' | 'cancelled';
export type AgentStatus = 'idle' | 'working' | 'parked' | 'paused' | 'failed' | 'archived';
export type ApprovalDecision = 'approve' | 'reject' | 'reduce_scope' | 'request_details';
export type EvidenceStatus = 'missing' | 'partial' | 'complete' | 'verified';
export interface Project { id: ID; name: string; description: string; status: 'running' | 'blocked' | 'idle' | 'archived'; progress: number; createdAt?: ISODateTime; }
export interface Task { id: ID; projectId: ID; parentId?: ID | null; title: string; description: string; assigneeAgentId?: ID | null; assigneeRoleId?: ID | null; status: TaskStatus; progress: number; dependencyIds: ID[]; evidenceStatus: EvidenceStatus; acceptanceCriteria: AcceptanceCriterion[]; updatedAt?: ISODateTime; }
export interface AcceptanceCriterion { id: ID; description: string; satisfied: boolean; evidenceArtifactIds?: ID[]; }
export interface RoleTemplate { id: ID; name: string; version: string; purpose: string; capabilities: string[]; allowedPermissions: string[]; preferredModelId?: ID; lifecycle: 'draft' | 'active' | 'parked' | 'archived'; }
export interface Agent { id: ID; projectId: ID; roleTemplateId: ID; name: string; status: AgentStatus; taskId?: ID | null; sessionId?: ID | null; lastSeenAt?: ISODateTime; }
export interface Channel { id: ID; projectId: ID; name: string; purpose: string; contextPolicy: 'relevant' | 'task' | 'project'; }
export type KnowledgeReadiness = 'KNOWN'|'PARTIAL'|'UNKNOWN';
export interface Message { id: ID; channelId: ID; senderAgentId?: ID | null; senderName: string; content: string; createdAt: ISODateTime; artifactIds?: ID[]; responseMode?: 'single'|'multi'; responseGroupId?: ID|null; routeReason?: string|null; knowledgeReadiness?: KnowledgeReadiness|null; knowledgeReadinessReason?: string|null; stageCompletion?: { level: 'low' | 'medium' | 'high'; taskId: ID; evidenceArtifactIds: ID[]; verified: boolean; }; }
export interface ChannelResponseDispatch {
  jobId:ID;jobIds:ID[];groupId:ID;status:string;responseMode:'single'|'multi';
  agents:Array<{agentId:ID;roleId:ID;roleName:string;routeReason:string;order:number}>;
}
export interface ChannelResponseGroup {
  groupId:ID;projectId:ID;channelId:ID;userMessageId:ID;responseMode:'single'|'multi';
  status:string;createdAt:ISODateTime;completedAt:ISODateTime|null;
  members:Array<{order:number;agentId:ID;agentName:string;roleId:ID|null;roleName:string|null;
    jobId:ID;routeReason:string;status:string}>;
}
export interface Artifact { id: ID; projectId: ID; taskId?: ID; name: string; mimeType: string; version: string; status: EvidenceStatus; sizeBytes?: number; checksum?: string; downloadUrl?: string; createdAt: ISODateTime; contentAvailable?: boolean; demoMetadata?: boolean; }
export interface ArtifactVersion { id: ID; artifactId: ID; version: string; sizeBytes?: number; checksum?: string; createdAt: ISODateTime; contentAvailable: boolean; }
export interface EvidenceLink { id: ID; artifactId: ID; kind: 'source' | 'run' | 'task' | 'decision'; referenceId: ID; description: string; createdAt: ISODateTime; }
export interface Approval { id: ID; projectId: ID; requestedByAgentId: ID; requestedByLabel?: string; title: string; type: string; risk: 'low' | 'medium' | 'high'; status: 'pending' | 'resolved'; evidenceArtifactIds: ID[]; createdAt: ISODateTime; description?: string; impact?: string; revision: number; resolution?: ApprovalDecision; resolvedAt?: ISODateTime; }
export interface ApprovalRecord { id: ID; approvalId: ID; decision: ApprovalDecision; reason: string; decidedAt: ISODateTime; revision: number; actor: string; scopeOverride?: Record<string, string | number | boolean>; }
export interface ApprovalDecisionInput { decision: ApprovalDecision; reason?: string; expectedRevision: number; scopeOverride?: Record<string, string | number | boolean>; }
export interface ComputeSlot { id: ID; state: 'free' | 'active' | 'paused' | 'releasing'; agentId?: ID; taskId?: ID; modelId?: ID; decodeTokensPerSecond?: number; prefillTokensPerSecond?: number; contextUsedTokens?: number; contextCapacityTokens?: number; vramUsedBytes?: number; vramCapacityBytes?: number; leaseExpiresAt?: ISODateTime; }
export interface RuntimeQueueItem { id: ID; position: number; projectId: ID; taskId?: ID; title: string; requestedRole: string; modelName?: string; priority: 'low' | 'medium' | 'high'; waitReason: string; }
export interface RuntimeMetrics { sampledAt: ISODateTime; resources: {id: ID; label: string; utilizationPercent?: number; usedBytes?: number; capacityBytes?: number}[]; }
export interface RuntimeBudget { currency: string; spent: number; limit?: number; forecast?: number; sampledAt: ISODateTime; }
export interface RuntimeSession { id: ID; slotId?: ID; projectId: ID; agentId: ID; state: string; startedAt?: ISODateTime; }
export interface SystemHealth { healthy: boolean; checkedAt: ISODateTime; services: { name: string; healthy: boolean; latencyMs?: number }[]; }
export interface AuditLog { id: ID; timestamp: ISODateTime; level: 'debug' | 'info' | 'warn' | 'error'; source: string; message: string; traceId?: ID; projectId?: ID; taskId?: ID; }
export interface AuthAccount { id: ID; username: string; role: string; displayName?: string; permissions?: string[]; projectIds?: string[] | null; }
export interface LoginTokenStatus { managedBy: 'environment'|'account'|'unavailable'; configured: boolean; canManage: boolean; }
export interface LoginTokenResult extends LoginTokenStatus { token: string; generated: boolean; }
export interface Page<T> { items: T[]; nextCursor?: string | null; }
export interface RuntimeEvent { id: ID; type: string; projectId?: ID; occurredAt: ISODateTime; payload: unknown; }
export interface NewTask { projectId: ID; title: string; description?: string; assigneeRoleId?: ID; dependencyIds?: ID[]; acceptanceCriteria?: { description: string }[]; }

/** Phase 5 system contracts; null data is a supported result for an unconnected runtime. */
export interface SystemTopology {
  sampledAt: ISODateTime;
  nodes: {id: ID; label: string; kind: 'ingress'|'orchestrator'|'persistence'|'other'; status: 'healthy'|'degraded'|'offline'|'unknown'}[];
  edges: {from: ID; to: ID; label?: string}[];
}
export interface InfrastructureNode { id: ID; name: string; scope: 'local'|'remote'; status: 'online'|'offline'|'unknown'; lastSeenAt?: ISODateTime; cpuUtilizationPercent?: number; ramUsedBytes?: number; ramCapacityBytes?: number; storageUsedBytes?: number; storageCapacityBytes?: number; }
export interface Accelerator { id: ID; nodeId: ID; name: string; status: 'online'|'offline'|'unknown'; vramUsedBytes?: number; vramCapacityBytes?: number; powerWatts?: number; temperatureC?: number; sampledAt?: ISODateTime; }
export interface LogFilter { projectId?: ID; cursor?: string; limit?: number; q?: string; level?: AuditLog['level']; source?: string; since?: ISODateTime; until?: ISODateTime; traceId?: ID; resourceId?: ID; }
export interface LogExportRequest { filter: Omit<LogFilter,'cursor'|'limit'>; format: 'jsonl'|'csv'|'audit_bundle'; }
export interface LogExportJob { exportId: ID; downloadUrl: string; expiresAt: ISODateTime; }
export interface LogSavedView { id: ID; name: string; filter: Omit<LogFilter,'cursor'|'limit'>; }
export interface ProjectRuntimeSettings { modelId: string; contextPolicy: 'task'|'project'; sessionPolicy: 'resumable'|'ephemeral'; frontierEscalation: 'ask'|'budget'|'disabled'; revision: number; }
export interface AppearanceSettings { theme: 'strata-dark'|'soft-dark'; motion: 'highlights'|'reduced'; density: 'compact'|'comfortable'; stageAnimation: 'play-once'|'static-gold'; }
export interface ProjectOverviewSnapshot {
  projectId: ID; taskCount: number; activeTasks: number; queuedTasks: number; blockedTasks: number;
  pendingApprovals: number; activeAgents: number; idleAgents: number; parkedAgents: number;
  progressPercent: number | null; stages: {messageId: ID; title: string; verified: boolean; level: 'low'|'medium'|'high'; occurredAt: ISODateTime; taskId: ID}[];
  activeSlots: number; totalSlots: number; budget: RuntimeBudget | null; metrics: RuntimeMetrics | null; health: SystemHealth | null;
}


/** Infra Console contracts. These expose backend mechanisms, not duplicate product views. */
export interface InfraStatus {
  mode:string; database:string; projection:string; uiEvents:string; domainEvents:string;
  counts:Record<string,number|null>;
}
export interface InfraEvent {
  sequence:number; eventId:string; type:string; occurredAt:string; actorId?:string|null;
  sessionId?:string|null; taskId?:string|null; correlationId?:string|null;
  causationId?:string|null; payload:Record<string,unknown>;
}
export interface InfraOverview {
  events:{count:number;firstSequence:number|null;lastSequence:number|null;sequenceGapCount:number};
  scheduler:{capacity:number;active:number;queued:number;parked:number};
  memory:{total:number;active:number;superseded:number};
  bank:{openIssues:number;decisions:number;lessons:number;progress:number};
  invocations:{total:number;unknown:number;failed:number;inputTokens:number;outputTokens:number};
  consistency:{outboxTotal:number;outboxPending:number;deadLetters:number};
  replay:{healthy:boolean;error:string|null};
}
export interface InfraSchedulerSession {
  sessionId:string; priority:number; taskState:string; slotState:string; yieldability:string;
  fencingToken:number; queuedAt:number|null; waitSeconds:number|null; leaseStarted:number|null;
  leaseAgeSeconds:number|null; parkRequested:boolean; parkCount:number; cooldownUntil:number|null;
  familyParent:string|null;
}
export interface InfraSchedulerSnapshot {
  capacity:number; active:number; queued:number; parked:number; sessions:InfraSchedulerSession[];
  queue:string[]; priorityAdjustments:Array<Record<string,unknown>>;
}
export interface InfraReplayState {
  projects:Record<string,string>; tasks:Record<string,string>; sessions:Record<string,string>;
  memories:Record<string,string>; beliefs?:Record<string,string>; issues:Record<string,string>; decisions:Record<string,string>;
  artifacts:Record<string,string>; delegations?:Record<string,string>; leases?:Record<string,number>;
  dependencies?:Array<[string,string]>; [key:string]:unknown;
}
export interface InfraReplayCompare {
  fromSequence:number;toSequence:number;changedSections:number;
  changes:Record<string,Array<{id:string;from:unknown;to:unknown}>|{from:unknown;to:unknown}>;
}
export interface InfraMemoryItem {
  memory_id:string; project_id:string; role_scope:string; kind:string; content:string;
  source_event_refs:string[]; valid_from:string; confidence:number; status:string; valid_to:string|null;
}
export interface InfraContextSnapshot {
  id:string; agentId?:string|null; taskId?:string|null; channelId?:string|null;
  projection?:string|null; artifactCount:number; taskRevision?:string|null; createdAt?:string|null;
  providerCheckpointId?:string|null; memoryBundleId?:string|null;memorySourceRefs:string[];
  memoryTokenCount?:number|null;contextAssemblyRunId?:string|null;
  responseGroupId?:string|null;responseOrder?:number|null;routeReason?:string|null;
  knowledgeReadiness?:KnowledgeReadiness|null;knowledgeReadinessReason?:string|null;
  knowledgeEvidenceArtifactIds?:string[];
}
export interface InfraContextAssemblyRun {
  id:string;project_id:string;job_id:string|null;role_id:string;profile_id:string;
  query_excerpt:string;token_budget:number;total_tokens:number;included:string[];
  excluded:Record<string,string>;candidates:Array<{sourceRef:string;memoryId:string;
    confidence:number;roleScope:string;retrieval:string;priority?:number}>;
  created_at:string;
}
export interface InfraContextView {
  snapshots:InfraContextSnapshot[];
  historySessions:Array<{session_id:string;entry_count:number;first_at:string;last_at:string}>;
  assemblyRuns:InfraContextAssemblyRun[];memoryRoleScopes:string[];
}
export interface InfraBankIssue {
  issue_id:string;project_id:string;title:string;blocking:string;decision_owner:string;
  auto_decidable:number|boolean;reversible:number|boolean;status:string;resolved_by:string|null;
}
export interface InfraBankDecision {
  decision_id:string;project_id:string;title:string;authority:string;risk:string;
  rationale:string;supersedes:string|null;status:string;
}
export interface InfraBankLesson { lesson_id:string;project_id:string;title:string;source_refs:string[]; }
export interface InfraBankProgress { progress_id:string;project_id:string;milestone:string;status:string;artifact_refs:string[]; }
export interface InfraBankView {
  issues:InfraBankIssue[]; decisions:InfraBankDecision[]; lessons:InfraBankLesson[]; progress:InfraBankProgress[];
}
export interface InfraA2cmCandidate {
  candidateId:string;actorId:string;statement:string;evidenceRefs:string[];
  confidence:number;status:string;reviewer:string|null;policyVersion:string|null;
  counterevidence:string[];uncertainty:string[];
}
export interface InfraA2cmSnapshot {
  capabilities:{review:{available:boolean;reason:string|null}};
  primarySessions:Array<{sessionId:string;lifecycle:string}>;
  activePrimarySessionId:string|null;
  candidates:InfraA2cmCandidate[];
}
export interface InfraA2cmPrimaryResult {sessionId:string;lifecycle:string;created:boolean}
export interface InfraA2cmReviewResult {
  candidate_id:string;previous_status:string;status:string;reviewer:string;
  policy_version:string;confidence:number;counterevidence_count:number;
  uncertainty_count:number;primary_session_id:string;rationale:string;
}
export interface InfraEvidenceNode {
  stage:string;status:string;ref?:string|null;summary?:string|null;source?:string|null;
}
export interface InfraEvidenceChain {
  request_id:string;relation_id:string;run_state:string;chain_state:string;nodes:InfraEvidenceNode[];
}
export interface InfraFrontierRequest {
  requestId:string;mode:string;ownerSessionId:string;taskOwnerSessionId:string;
  childSessionId:string|null;advice:string|null;returnContract:string|null;
  reviewState:string;budgetUsed:number;
}
export interface InfraFrontierDelegateRun {
  requestId:string;relationId:string;parentTaskId:string;ownerSessionId:string;
  childSessionId:string;childTaskId:string;capability:string;state:string;
  branch:string|null;commitSha:string|null;mergeSha:string|null;error:string|null;
  createdAt:number;
}
export interface InfraFrontierSnapshot {
  capabilities:{
    consult:{available:boolean;budgetLimit:number;reason:string|null};
    delegate:{available:boolean;reason:string|null;worker?:string|null;permissionMode?:string|null};
  };
  sessions:Array<{sessionId:string;roleId:string;lifecycle:string}>;
  tasks:Array<{taskId:string;state:string}>;
  requests:InfraFrontierRequest[];
  delegateRuns:InfraFrontierDelegateRun[];
}
export interface InfraFrontierConsultResult {
  requestId:string;projectId:string;mode:string;ownerSessionId:string;advice:string|null;
  reviewState:string;budgetUsed:number;operationId:string;replayed:boolean;
}
export interface InfraFrontierDelegateResult {
  requestId:string;relationId:string;projectId:string;parentTaskId:string;ownerSessionId:string;
  childSessionId:string;childTaskId:string;capability:string;state:string;
  branch:string|null;commitSha:string|null;mergeSha:string|null;error:string|null;
  operationId:string|null;replayed:boolean;
}
export interface InfraFrontierDelegateAction {
  requestId:string;state:string;workerSignal?:string|null;mergeSha?:string|null;
}
export interface InfraProvenance {
  project_id:string;stages:string[];chains:InfraEvidenceChain[];
}
export interface InfraInvocation {
  idempotency_key:string;model:string;mode:string;invocation_id:string;
  fallback_reason:string|null;status:string;provider_request_id:string|null;
  usage_input:number;usage_output:number;
}
export interface InfraConsistency {
  aggregates:Array<{aggregate_type:string;aggregate_id:string;state:string;version:number}>;
  outbox:Array<{message_id:string;aggregate_type:string;aggregate_id:string;aggregate_version:number;
    event_type:string;attempt_count:number;published_at:number|null;last_error:string|null;
    dead_letter_at:number|null;dead_letter_reason:string|null}>;
  inboxCount:number;
  leases:Array<{resource_id:string;holder_id:string;fencing_token:number;expires_at:number}>;
  sagas:Array<{sagaId:string;sagaType:string;state:string;payload:Record<string,unknown>}>;
}
export interface InfraBackupRun {
  id:string;kind:'BACKUP'|'VERIFY'|string;status:string;backup_id:string|null;storage_ref:string|null;
  file_count:number|null;checksum_status:string|null;error:string|null;
  created_at:string;completed_at:string|null;
}
export interface InfraBackupActionResult {
  id:string;backupId:string;status:string;fileCount:number;checksumStatus:string;
  createdAt:string;completedAt:string;
}
export interface InfraOperations {
  continuity:null|{checkpointId:string;sessionId:string;client:string;gitBranch:string|null;
    gitHeadSha:string|null;gitDirty:boolean;verificationStatus:string|null;blockers:string[];
    nextSteps:string[];createdAt:string};
  mcp:{tools:Array<{toolId:string;name:string;version:string;credentialConfigured:boolean}>;
    accessAudit:Array<{toolId:string;roleId:string;result:string;recordedAt:string}>};
  skills:Array<{skillId:string;version:string;capabilities:string[]}>;
  workers:Array<{handle:string;adapter:string;session_id:string;task_id:string;fencing_token:number;
    status:string;checkpoint_ref:string|null}>;
  backup:{tracked:boolean;reason?:string|null;latestBackup?:InfraBackupRun|null;latestVerify?:InfraBackupRun|null};
}
