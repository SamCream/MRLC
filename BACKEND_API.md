# MRLC v0.9.1 — executable backend API contract

**Authority:** `backend/app/`. The authoritative OpenAPI document is generated from the live FastAPI app by `backend/scripts/export_openapi.py` and uploaded by CI as the `openapi-snapshot` artifact; no hand-maintained snapshot is authoritative. All paths below are relative to the **same-origin** `/api/v1` root. Business decisions, validation, project isolation, audit and persistence happen on the backend. Browser-side filtering/layout is presentation only.

## Authentication and errors

- `POST /auth/login`, JSON `{"password":"<account password>","username":"<optional username>"}` → signed HttpOnly session cookie. `username` is optional; the password-only UI can still authenticate an enabled account.
- `GET /auth/token` with header `X-MRLC-Token: <dedicated login token>` → the same signed HttpOnly session authority as password login. Passwords and tokens are deliberately **not interchangeable**.
- Browser convenience: opening `/?token=<token>` causes the React `AuthGate` to remove `token` from the address bar immediately and exchange it through `X-MRLC-Token`. The initial URL may still exist in ingress/access logs; remote use requires HTTPS and protected logs.
- Administrator credentials are environment-backed: `MRLC_ADMIN_PASSWORD` and the independent `MRLC_ADMIN_TOKEN` (plus optional `MRLC_ADMIN_USERNAME`). They are never persisted in SQLite.
- `GET /auth/me/token` → `{managedBy,configured,canManage}`. For non-admin users, `POST /auth/me/token` with `{}` generates a random token; `{ "token":"..." }` replaces it with a 24–256 character URL-safe token; `DELETE /auth/me/token` revokes it. Plaintext is returned only by the create/replace call; SQLite stores only a keyed digest. Administrator token changes are rejected by the API and must be made in `backend/.env`.
- `GET /auth/me` → current account or 401; `POST /auth/logout` clears the cookie.
- Optional server-to-server `Authorization: Bearer <MRLC_SERVICE_TOKEN>` remains separate from browser login tokens; **never** embed it in frontend JavaScript.
- Errors are JSON `{"detail":"..."}`: 400 invalid input, 401 unauthenticated, 403 forbidden/unauthorized change, 404 missing, 409 conflicting state/revision/foreign dependency, 413 upload too large, 503 no model execution backend. All business routes require identity; no mock fallback.
- `GET /capabilities` → factual backend flags including model configured, physical GPU control supported, checkpoint support and DB durability.

## Projects, roles and Agent instances

- `GET /projects` → `{items: Project[],nextCursor:null}`; `POST /projects` body `{name:string,description:string}` → 201 Project.
- `GET /projects/{id}`; `PATCH /projects/{id}` body `{name?,description?}`; `POST /projects/{id}/archive` body `{reason?:string}`. Archived projects reject writes, and projects with queued/running jobs cannot archive.
- `GET /roles` → Page<RoleTemplate>; `POST /roles` body `{name,purpose,capabilities:string[],allowedPermissions:string[],preferredModelId?,lifecycle?:"draft"|"active"}`; `PATCH /roles/{id}` permits only corresponding mutable role fields and increments `version`.
- `POST /roles/{id}/activate|park|archive` → RoleTemplate. Active agent references block archival.
- `GET /projects/{id}/agents` → Page<Agent>; `POST /projects/{id}/agents` body `{projectId,roleTemplateId,name?}` → Agent. Role must be active.
- `GET /agents/{id}`; `GET /agents/{id}/sessions` → Page of real job records; `POST /agents/{id}/park|resume` → Agent; active/queued calls are guarded, **no checkpoint is claimed**.
- `GET /agents/{id}/knowledge?taskId=...` → `{status:"KNOWN"|"PARTIAL"|"UNKNOWN",evidenceArtifactIds:string[],reason:string}`. This reflects verified evidence only.

## Channels and messages

- `GET /projects/{id}/channels` → Page<Channel>; `POST /projects/{id}/channels` body `{name,purpose,contextPolicy:"relevant"|"task"|"project"}` → Channel.
- `PATCH /channels/{id}` body `{name?,purpose?,contextPolicy?}`.
- `GET /channels/{id}/messages` → Page<Message>; `POST /channels/{id}/messages` body `{content:string,responseMode:"single"|"multi",artifactIds?:string[]}` → Message. Every artifact ID is checked against the channel's project. Sending a message alone does **not** invoke the model.
- `GET /channels/{id}/search?q=text` → Page<Message>.
- `POST /channels/{id}/respond` body `{agentId:string,artifactIds?:string[]}` → **202** `{jobId,status:"queued"}`. Must have an idle agent with `messages.reply`; only channel history and explicitly authorized artifact bytes enter its prompt. This is an explicit potentially billable action.

## Task orchestration and acceptance

- `GET /projects/{id}/tasks` → Page<Task>.
- `POST /projects/{id}/tasks` body `{projectId,title,description?,assigneeRoleId?,dependencyIds?:string[],acceptanceCriteria?:{description:string}[]}` → Task.
- `PATCH /tasks/{id}` allows only title, description, assigneeRoleId, dependencyIds and acceptance criterion *descriptions*. It **rejects direct writes** to task state, progress, evidence status and satisfaction bits. Dependencies must be project-local and acyclic.
- `GET /tasks/{id}` → Task; `GET /tasks/{id}/activity` → Page<AuditLog>.
- `POST /tasks/{id}/dispatch` → **202** `{jobId,status:"queued"}` when a provider is configured, assigned role is active and has `tasks.execute`, and an idle Agent is available. Dependencies must be accepted. Worker writes output to real Artifact and moves task to Review, not Accepted.
- `POST /projects/{id}/schedule` → `{queued:{jobId,status}[],skipped:{taskId,reason}[]}`; evaluates runnable tasks on the server. **Operator-triggered**, not automatic spending.
- `POST /tasks/{id}/criteria/{criterionId}/verify` body `{evidenceArtifactIds:string[]}` → Task. Only in-scope artifacts with stored bytes can satisfy evidence obligations.
- `POST /tasks/{id}/review` → Task; requires completed criteria and accepted dependencies. A model-produced draft may already be Review but is **not** equivalent to accepted proof.
- `POST /tasks/{id}/accept` body `{reason:string}` → Accepted Task; demands Review, verified criteria and accepted dependencies. Server creates a verified milestone message in the project's Report channel and marks accepted evidence accordingly.

## Artifacts and evidence

- `GET /projects/{id}/artifacts` → Page<Artifact>; `POST /projects/{id}/artifacts` **multipart/form-data** with `file` required, `taskId?`, `artifactId?` for a new version. File cap **32 MiB**. Bytes are stored on disk under SHA-256 key and metadata in SQLite; request never includes a fake file path.
- `GET /artifacts/{id}` → Artifact, `GET /artifacts/{id}/versions` → Page<ArtifactVersion>, `GET /artifacts/{id}/content?versionId=...` → actual Blob/file stream; 404 if bytes absent.
- `GET /artifacts/{id}/evidence` → Page<EvidenceLink>; `POST /artifacts/{id}/evidence` body `{kind:"source"|"run"|"task"|"decision",referenceId,description?}`.

## Approvals

- `GET /approvals?projectId=...` → Page<Approval>; `POST /approvals` body `{projectId,requestedByAgentId,title,type?,risk?,evidenceArtifactIds?,description?,impact?}` → Approval.
- `GET /approvals/{id}`; `GET /approvals/{id}/decisions` → Page<ApprovalRecord>.
- `POST /approvals/{id}/decisions` body `{decision:"approve"|"reject"|"reduce_scope"|"request_details",expectedRevision:number,reason?:string,scopeOverride?:Record<string,string|number|boolean>}` → Approval. Conflicting revisions return **409**, reduced scope requires structured override. Audit/history are retained. Approval does not bypass independent runtime policy.

## Compute, settings, observability

- `GET /runtime/slots` → Page<ComputeSlot> of **logical in-flight provider jobs** only; `GET /runtime/queue`, `/runtime/sessions`, `/runtime/jobs`. No invented physical GPU metrics or model billing.
- `POST /runtime/slots/{id}/pause|resume|release` currently returns **409** for unsupported active-job physical lifecycle operations. An already free logical slot is idempotently releasable.
- `GET /runtime/metrics` and `/runtime/budget` → `null` until a genuine adapter/monitor is installed.
- `GET /projects/{id}/overview` → ProjectOverviewSnapshot whose counts and progress are server-computed. `/projects` returns project status/progress derived on the server.
- `GET /projects/{id}/settings` → `{modelId,contextPolicy,sessionPolicy,frontierEscalation,revision}`; `PATCH` requires full fields plus `expectedRevision`; `POST /settings/reset` requires `{expectedRevision}`. Stale writes return 409.
- `GET /users/me/appearance` → `{theme,motion,density,stageAnimation}`; `PATCH /users/me/appearance` saves full settings; `POST /users/me/appearance/reset` restores defaults.
- `GET /system/health`, `POST /system/health-checks`, `GET /system/topology`, `/system/nodes`, `/system/accelerators`. Data absent or unknown is represented explicitly, not fabricated.
- `GET /system/logs?projectId=...&q=...&level=...&source=...&since=...&until=...&cursor=...&limit=...` → `{items:AuditLog[],nextCursor:string|null}`. `GET/POST /users/me/log-views`, `PATCH/DELETE /users/me/log-views/{id}` store views on the server.
- `POST /system/logs/export` body `{filter:{...},format:"jsonl"|"csv"}` → `{exportId,downloadUrl,expiresAt}`, authenticated download via `GET /system/logs/exports/{exportId}`. Audit bundle format explicitly not implemented (501).
- `GET /events` → cookie-authenticated `text/event-stream`; SSE event `data:` JSON `{id,type,projectId?,occurredAt,payload}` and replay via `Last-Event-ID`.

## Infra Core console read models

These endpoints back the separate Infra navigation domain. They intentionally expose mechanism-level state rather than duplicating Control Room task/chat/compute/artifact views.

- `GET /infra/status` → `{mode,database,projection,uiEvents,domainEvents,counts}`. Requires authenticated `system.read`; `database` is a filename only, never an absolute server path.
- `GET /projects/{id}/infra/overview` → summary of EventStore sequence health, replay status, scheduler lease counts, Memory counts, Project Bank counts, Gateway invocation counts and consistency outbox/dead-letter counts.
- `GET /projects/{id}/infra/events` → `Page<InfraEvent>`; every item contains `sequence,eventId,type,occurredAt,actorId,sessionId,taskId,correlationId,causationId,payload`.
- `GET /projects/{id}/infra/replay?sequence=N` → deterministic projection at the requested domain-event sequence (or current state when omitted). `GET /projects/{id}/infra/replay/compare?fromSequence=A&toSequence=B` → changed authoritative sections/IDs between two reconstructed states.
- `GET /projects/{id}/infra/scheduler` → `{capacity,active,queued,parked,sessions,queue,priorityAdjustments}`; session rows expose arbitration metadata such as priority, slot/task state, fencing token, queue/lease age and park state. It does **not** return task titles, GPU usage or token throughput.
- `GET /projects/{id}/infra/context` → `{snapshots,historySessions,assemblyRuns,memoryRoleScopes}`. Assembly runs record the real Memory candidates, retrieval hit/fallback/miss decision, ContextAssembler exclusions, included source refs and token budget used by provider execution. Raw Workspace chat content is deliberately not mirrored.
- `GET /projects/{id}/infra/bank` → raw ISS/DEC/LES/PRG record metadata: `{issues,decisions,lessons,progress}`.
- `GET /projects/{id}/infra/a2cm` → candidate/review state. `POST /projects/{id}/infra/a2cm/primary-session` ensures one ACTIVE `a2cm.primary` reviewer session. `POST /projects/{id}/infra/a2cm/observations` body `{content,evidenceEventId,confidence,source}` creates a candidate only when `evidenceEventId` exists in this project's Domain EventStore. `POST /projects/{id}/infra/a2cm/candidates/{candidateId}/review` body `{primarySessionId,decision:"PROMOTE"|"REJECT"|"DEFER",policyVersion,counterevidence,uncertainty,rationale}` performs Primary review and writes replayable state.
- `GET /projects/{id}/infra/frontier` → factual CONSULT/DELEGATE capability state, eligible session/task IDs, persisted request history and child-run state. `POST /projects/{id}/infra/frontier/consult` requires `tasks.execute` and body `{parentTaskId,ownerSessionId,capability,budget,operationId}`; it returns 503 when CCR is not configured and an operation ID prevents duplicate paid execution.
- `POST /projects/{id}/infra/frontier/delegate` requires explicit server enablement plus an installed Codex/DSH binary and body `{parentTaskId,ownerSessionId,capability,objective,acceptanceCommands,budget,operationId,confirmation:"RUN_DELEGATE"}`. The child is created in an isolated Git worktree and cannot merge itself. `POST /projects/{id}/infra/frontier/delegations/{requestId}/cancel` requests/carries out cancellation; `POST .../{requestId}/review` with `ACCEPT|REWORK` is the only integration gate. ACCEPT performs the reviewed merge.
- `GET /projects/{id}/infra/provenance` → Evidence Graph `{project_id,stages,chains}`, where each chain reports persisted requirement/task/delegation/invocation/artifact/tests/review/commit nodes and `PRESENT/PENDING/MISSING/FAILED/NOT_REACHED` state.
- `GET /projects/{id}/infra/invocations` → `Page<InfraInvocation>` from the Model Gateway ledger, including idempotency key, invocation/provider request IDs, model/mode, status, fallback reason and reported token usage.
- `GET /projects/{id}/infra/consistency` → project-scoped aggregates, transactional outbox, linked inbox receipt count, project scheduler-linked leases and project-tagged Sagas.
- `GET /projects/{id}/infra/operations` → continuity checkpoint, project-eligible MCP tools/access audit, Skills, WorkerRun metadata and latest managed backup/restore-drill state. `GET /infra/backups` lists sanitized backup-run metadata; `POST /infra/backups` creates a server-managed SQLite/artifact backup with SHA-256 manifest; `POST /infra/backups/{backupId}/verify` restores it into isolated staging and verifies checksums without replacing the live database. Absolute server paths are never returned.

Existing write routes for evidence-backed Memory and Project Bank remain documented in `backend/INFRA_MIGRATION.md`. Project-scoped Infra routes inherit the normal project permission boundary.

## What is still outside this version

This is a functioning **backend foundation**, not a claim that the entire historical MRLC PRD is implemented. A2CM candidate/Primary review, Frontier CONSULT, governed Frontier DELEGATE (Codex/DSH) and managed backup verification are connected. Provider-specific KV checkpoint restoration, physical GPU control, distributed multi-node scheduling/consensus, SSO/federation, production key management and a complete cross-module versioned migration framework remain outside this version. Raw independent Codex/DSH process launch is intentionally not a product API; the adapters are exposed through governed DELEGATE. Exact gaps are tracked in `BACKEND_STATUS.md`.
