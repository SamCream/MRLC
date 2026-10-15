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
export interface Message { id: ID; channelId: ID; senderAgentId?: ID | null; senderName: string; content: string; createdAt: ISODateTime; artifactIds?: ID[]; stageCompletion?: { level: 'low' | 'medium' | 'high'; taskId: ID; evidenceArtifactIds: ID[]; verified: boolean; }; }
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
