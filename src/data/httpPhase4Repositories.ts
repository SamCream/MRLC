import type { ArtifactRepository } from './artifactRepository';
import type { ApprovalRepository } from './approvalRepository';
import type { ComputeRepository } from './computeRepository';
import type { Services } from '../api/services';
/** Candidate backend bindings, disabled until backend compatibility is verified. */
const noLiveEvents=()=>()=>{};
export const httpArtifactRepository=(services:Services):ArtifactRepository=>({
  subscribe:noLiveEvents,
  listArtifacts:services.artifacts.list,
  getArtifact:services.artifacts.get,
  uploadArtifact:services.artifacts.upload,
  listVersions:services.artifacts.versions,
  listEvidence:services.artifacts.evidence,
  getContent:services.artifacts.content,
});
export const httpApprovalRepository=(services:Services):ApprovalRepository=>({
  subscribe:noLiveEvents,
  listApprovals:services.approvals.list,
  getApproval:services.approvals.get,
  listDecisions:services.approvals.history,
  decide:services.approvals.decide,
});
export const httpComputeRepository=(services:Services):ComputeRepository=>({
  subscribe:noLiveEvents,
  listSlots:services.compute.slots,
  pauseSlot:services.compute.pause,
  resumeSlot:services.compute.resume,
  releaseSlot:services.compute.release,
  listQueue:services.compute.queue,
  listSessions:services.compute.sessions,
  getMetrics:services.compute.metrics,
  getBudget:services.compute.budget,
});
