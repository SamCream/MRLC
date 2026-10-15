# Historical interface discovery document (Phases 1–6)

**Phase 7 notice:** This file preserves the original candidate API designs and earlier migration status. Those older statements (such as "no backend exists" and "HTTP inactive") are obsolete. For the executable **implemented** service, use **`BACKEND_API.md`**, **`backend/openapi.json`**, and **`BACKEND_INTERFACE_LEDGER.md`**.

---

# MRLC front-end interface inventory and proposed backend contract (v0.4)

**Contract status:** proposed; not confirmed by backend. These paths are not advertised as an existing MRLC server API.

## Inspection of the input prototype

- `index.html:190-237`: in-memory `state`, projects, messages, tasks, roles, approvals, slots and mock runtime resources. No network persistence.
- `index.html:238-257`: direct DOM rendering, navigation and settings helpers.
- `index.html:258-336`: main `handleAction` dispatcher and action mutations.
- `index.html:338-354`: channel navigation, message creation, task/role/approval/compute rendering, final initialization.
- `index.html:109-114`: Workspace; `117-120`: Tasks; `123-126`: Artifacts; `129-135`: Projects/Agents/Roles/Approvals; `137-149`: Compute/Infrastructure; `152-158`: Logs/Settings.

### Migration invariant

For each operation, **source location → extracted service → typed inputs/outputs** is listed below. The typed API surface exists. Eight pages are React-controlled via local repositories; real HTTP adapters remain inactive pending backend schema negotiation. A back-end must validate every write, independent of any front-end rules.

| Operation and source | New client method | HTTP endpoint (candidate) | Data contract / considerations |
|---|---|---|---|
| Project switch / `switchProject`, 240 | `projects.list/get` | `GET /api/v1/projects`, `GET /api/v1/projects/:id` | `Page<Project>` and `Project`; project ID, not array index |
| Create / 277–278 | `projects.create` | `POST /api/v1/projects` | `{name: string, description: string}` → `Project` |
| Rename / 304–305 | `projects.update` | `PATCH /api/v1/projects/:id` | `Partial<Project>` → `Project`; server-side field allowlist required |
| Archive / 307–308 | `projects.archive` | `POST /api/v1/projects/:id/archive` | `{reason?: string}` → archived `Project`; archive is not array splice or deletion |
| Task list & filter / 343 | `tasks.list/get` | `GET /api/v1/projects/:id/tasks`, `GET /api/v1/tasks/:id` | `Page<Task>` (cursor), `Task`; stable ID and dependencies |
| Task create / 275–276 | `tasks.create` | `POST /api/v1/projects/:id/tasks` | `NewTask` → `Task`; acceptance criteria and dependency IDs preserved |
| Task update / 343 | `tasks.update` | `PATCH /api/v1/tasks/:id` | `Partial<Task>` → `Task`; forbidden state transitions return conflict |
| Review task / 343 | `tasks.review` | `POST /api/v1/tasks/:id/review` | `Task`; test acceptance/evidence readiness on backend |
| Task activity / 120,328 | `tasks.activity` | `GET /api/v1/tasks/:id/activity` | `Page<AuditLog>`; bounded scroll and cursor |
| Role templates / 215–222,344 | `roles.list` | `GET /api/v1/roles` | `Page<RoleTemplate>` |
| Create role / 279–280 | `roles.create` | `POST /api/v1/roles` | `Omit<RoleTemplate,'id'>` → `RoleTemplate` |
| Edit role / 299–300 | `roles.update` | `PATCH /api/v1/roles/:id` | `Partial<RoleTemplate>` → `RoleTemplate` |
| Lifecycle / 302–303 | `roles.lifecycle` | `POST /api/v1/roles/:id/{park,activate,archive}` | validated transition → `RoleTemplate`; role template vs instance distinct |
| Agent panel / 242 | `agents.list/get` | `GET /api/v1/projects/:id/agents`, `GET /api/v1/agents/:id` | `Page<Agent>` / `Agent` |
| Inspect/resume session / 322–324 | `agents.sessions/resume` | `GET /api/v1/agents/:id/sessions`, `POST /api/v1/agents/:id/resume` | sessions TBD; resume must be idempotent and permission-checked |
| Park agent / 301–303 | `agents.park` | `POST /api/v1/agents/:id/park` | `Agent`; do not mistake template park for agent park |
| Channels / 206,269–270 | `channels.list/create` | `GET/POST /api/v1/projects/:id/channels` | `Channel`; create `{name,purpose,contextPolicy}` |
| Channel settings / 273–274 | `channels.update` | `PATCH /api/v1/channels/:id` | `Partial<Channel>` → `Channel` |
| Messages / 200–205,341–342 | `channels.messages/send` | `GET/POST /api/v1/channels/:id/messages` | `Page<Message>`; send `{content,responseMode}`; persist message + visibility policy |
| Search messages / 271–272 | `channels.search` | `GET /api/v1/channels/:id/search?q=...` | `Page<Message>`; enforce channel authorization |
| Artifact listing / 123–126,243 | `artifacts.list/get` | `GET /api/v1/projects/:id/artifacts`, `GET /api/v1/artifacts/:id` | `Page<Artifact>` / `Artifact` |
| Upload / 293–294 | `artifacts.upload` | `POST /api/v1/projects/:id/artifacts` | multipart `{file: binary,taskId?:string}` → `Artifact`; uploaded bytes not just file name |
| History / 284 | `artifacts.versions` | `GET /api/v1/artifacts/:id/versions` | `Page<ArtifactVersion>` with immutable version IDs |
| Evidence / 283,289 | `artifacts.evidence` | `GET /api/v1/artifacts/:id/evidence` | `Page<EvidenceLink>`; link IDs, evidence provenance (not self-verification) |
| Approval listing / 224–228,345 | `approvals.list/get` | `GET /api/v1/approvals`, `GET /api/v1/approvals/:id` | `Page<Approval>` / `Approval` |
| Approval actions / 347–348 | `approvals.decide` | `POST /api/v1/approvals/:id/decisions` | `ApprovalDecisionInput` with `decision`, `expectedRevision:number`, optional `reason` and `scopeOverride` for reduction; audit + authorization + CAS required |
| Slot metrics / 230–234,349 | `compute.slots/metrics` | `GET /api/v1/runtime/slots`, `GET /api/v1/runtime/metrics` | `Page<ComputeSlot>` / metrics TBD; never fabricate live telemetry |
| Pause/resume/release / 332–333 | `compute.pause/resume/release` | `POST /api/v1/runtime/slots/:id/{pause,resume,release}` | `ComputeSlot` state transition (including free on release); backend state authoritative |
| Scheduler / 235,349 | `compute.queue` | `GET /api/v1/runtime/queue` | `Page<RuntimeQueueItem>` priority, wait reason, dependencies TBD |
| Budget / 142 | `compute.budget` | `GET /api/v1/runtime/budget` | `RuntimeBudget|null`; currency, spend, timestamp, configured limit |
| Health & topology / 326–327 | `system.health/topology` | `GET /api/v1/system/health`, `GET /api/v1/system/topology` | `SystemHealth` / topology TBD; actual probes on server |
| Logs / 244,287–288 | `system.logs` | `GET /api/v1/system/logs` | `Page<AuditLog>`; export is presently local JSONL generated from seven demo rows |
| Settings / 254–256,334–335 | `system.settings/updateSettings` | `GET/PATCH /api/v1/settings` | **schema TBD**; separate user appearance and backend runtime defaults |
| Live subscriptions / no implementation | `subscribeEvents` | `GET /api/v1/events` (SSE proposal) | `RuntimeEvent` `{id,type,projectId?,occurredAt,payload}`; last-event replay and auth TBD |

## Original UI actions not equivalent to a backend operation

- Navigation (`navigate`, page/tab switch), search/filter/sort of **already loaded** local data, theme/motion/density, modal/drawer/toast and message copying remain frontend-only.
- Scenario switches are **demo fixtures** and must be disabled or isolated from the real backend.
- `run-health-check` previously returned a prewritten “all checks passed”, `inspect-role-session` only displayed a toast, artifact preview displayed placeholder text, and `release-slot` mutated a local array. None of these may be presented as real infrastructure operations.
- `attach-file-submit` previously only recorded a filename; file bytes were not uploaded.
- `resolveApproval` previously deleted a row from the `approvals` array; no decision record or atomic server transaction existed.
- Several UI counts are inconsistent with the source arrays (e.g. six tasks but label says twelve). Replace with selector-derived counts during React state conversion.

## Backend protocol questions blocking final integration

1. Authentication: session cookies vs bearer tokens, CSRF and CORS policy.
2. Authorization: project membership, role/agent capability boundary, approval authorization.
3. IDs and timing: UUID/ULID; stable cursors; ISO timestamps; `ETag`/revision controls for races.
4. Event transport: SSE vs WebSocket; replay cursor/sequence; reconnect; deduplication; event ordering.
5. File lifecycle: upload streaming, max size, storage adapter, MIME validation, checksum, signed downloads.
6. Task graph and lifecycle: transition matrix, dependency cycles, project-scope invariants, acceptance and evidence sealing.
7. Runtime semantics: lease ownership, park vs pause, slot occupancy, scheduler preemption and idempotency keys.

All responses marked **TBD** should be replaced only after inspecting the actual MRLC backend, not guessed from the prototype.


## Phase 2 binding audit (source → adapter → payload)

| Source UI component and operation | Local adapter method | HTTP candidate | Input / response | Status |
|---|---|---|---|---|
| `src/pages/WorkspacePage.tsx` channel listing | `listChannels(projectId)` | `GET /api/v1/projects/{projectId}/channels` | `projectId: string` → `Page<Channel>` | React-bound, local mock |
| `WorkspacePage.tsx` create channel | `createChannel(projectId,input)` | `POST /api/v1/projects/{projectId}/channels` | `{name:string,purpose:string,contextPolicy:'relevant'\|'task'\|'project'}` → `Channel` | React-bound, local mock |
| `WorkspacePage.tsx` messages | `listMessages(channelId)` | `GET /api/v1/channels/{channelId}/messages` | `channelId:string` → `Page<Message>` | React-bound, local mock |
| `WorkspacePage.tsx` send message | `sendMessage(channelId,content,responseMode)` | `POST /api/v1/channels/{channelId}/messages` | `{content:string,responseMode:'single'\|'multi'}` → `Message` | React-bound, local mock |
| `TasksPage.tsx` task list | `listTasks(projectId)` | `GET /api/v1/projects/{projectId}/tasks` | `projectId:string` → `Page<Task>` | React-bound, local mock |
| `TasksPage.tsx` create task | `createTask(input)` | `POST /api/v1/projects/{projectId}/tasks` | `NewTask { projectId,title,description?,assigneeRoleId?,dependencyIds?,acceptanceCriteria? }` → `Task` | React-bound, local mock |
| `TasksPage.tsx` request review | `reviewTask(taskId)` | `POST /api/v1/tasks/{taskId}/review` | `taskId:string` → `Task` | React-bound, local mock |
| `TasksPage.tsx` task activity | `activity(taskId)` | `GET /api/v1/tasks/{taskId}/activity` | `taskId:string` → `Page<AuditLog>` | React-bound, local mock |

`httpWorkspaceRepository()` is deliberately **not switched on** because there is no confirmed server schema. The local repository has persistence, but no multi-process transaction, access-control enforcement, upload storage, SSE replay or backend validation.

## Phase 3 — Projects, role templates, agent instances and shared project selection

**Discovery rule:** each item below records **(1) original source location and relevant operation, (2) opened React Repository method and HTTP candidate, (3) payload + expected response**. Both local adapters run by default; HTTP adapter is intentionally not activated without an actual MRLC backend.

All original locations refer to `reference/index.original.html` (1-based source line numbers). `src/data/organizationRepository.ts` contains the new `OrganizationRepository` contract and local implementation. `src/data/httpOrganizationRepository.ts` maps that contract to `src/api/services.ts`. The shell binds project IDs in `src/app/AppContext.tsx`, and navigation belongs to React rather than the legacy DOM renderer.

| Legacy code location / discovered operation | New UI → repository → REST candidate | Request shape | Expected response | Migration status |
|---|---|---|---|---|
| L194–199, L239–240, L245–250: project list + switch by *array index* | `AppContext`, `ProjectsPage` → `listProjects()` → `GET /api/v1/projects` | no body | `Page<Project>`, each `{id,name,description,status,progress,createdAt?}` | React bound; stable IDs; mock |
| L277–278: new project modal + push | `ProjectsPage` → `createProject({name,description})` → `POST /api/v1/projects` | `{name:string,description:string}` | Created `Project` with stable `id` | React bound; mock |
| L304–305: project edit settings | `ProjectsPage` → `updateProject(projectId,patch)` → `PATCH /api/v1/projects/{id}` | `UpdateProject`: optional `name?:string,description?:string` only | Updated `Project` | React bound; allowlist |
| L307–308: project archive (previously destructive array splice) | `ProjectsPage` → `archiveProject(projectId,reason?)` → `POST /api/v1/projects/{id}/archive` | `{reason?:string}` | Archived `Project` (`status:'archived'`), **not** HTTP 204 in the new candidate | React bound; mock preserves data |
| L215–222, L344: role list/templates | `RolesPage` → `listRoles()` → `GET /api/v1/roles` | no body | `Page<RoleTemplate>` | React bound; mock |
| L279–280: create reusable role | `RolesPage` → `createRole(input)` → `POST /api/v1/roles` | `CreateRole`: `name:string,purpose:string,capabilities:string[],allowedPermissions:string[],preferredModelId?:string,lifecycle?:'draft'\|'active'` | `RoleTemplate` including `id`, `version:'v1'` | React bound; mock |
| L299–300: edit role | `RolesPage` → `updateRole(roleId,patch)` → `PATCH /api/v1/roles/{id}` | `UpdateRole` optional `name,purpose,capabilities,allowedPermissions,preferredModelId` | Updated `RoleTemplate`, version increased in mock | React bound; mock |
| L302–303: template park/archive | `RolesPage` → `transitionRole(roleId,action)` → `POST /api/v1/roles/{id}/{action}` | `action='activate'\|'park'\|'archive'`; no body | Updated `RoleTemplate` with `lifecycle` | React bound; mock, preserves distinction from Agent status |
| L199, L242, L301, L322–324: agent instances by project | `AgentsPage` → `listAgents(projectId)` → `GET /api/v1/projects/{id}/agents` | project ID in path | `Page<Agent>` with `roleTemplateId`, `status`, optional `taskId`, `sessionId` | React bound; mock |
| Agent assignment (new legitimate affordance; original page only displayed instances, L131) | `AgentsPage` → `assignAgent({projectId,roleTemplateId,name?})` → `POST /api/v1/projects/{id}/agents` | `{projectId:string,roleTemplateId:string,name?:string}` | New `Agent` with `status:'idle'` | React bound; mock only (does not start model) |
| Original L302–303 conflated template and Agent parking | `AgentsPage` → `parkAgent(agentId)` → `POST /api/v1/agents/{id}/park` | agent ID in path; no body | Updated `Agent` (`status:'parked'`) | React bound; simulated |
| L322–324 resume/session handling (previously only toast) | `AgentsPage` → `resumeAgent(agentId)` → `POST /api/v1/agents/{id}/resume` | agent ID in path; no body | Updated `Agent`; local mock switches to `idle`, **not** a restored compute session | React bound; simulated |
| L65–81, L173–188, L245–247: navigation | `App.tsx` → `AppContext.navigate(PageId)` | Browser hash `#page-id`, no backend request | React route and mobile/sidebar tab state | React bound, frontend-only |
| L109–120: Workspace and Tasks always using first project | `WorkspacePage`/`TasksPage` → project from `AppContext` | stable `projectId` passed into existing repository methods | Project-scoped channels/messages/tasks, empty states for new project | React bound; local mock |
| L265: global command palette “Create task” still routed into old HTML modal | `legacy-controller.js` compatibility event `mrlc:new-task` → `TasksPage` form | frontend-only event | Opens React-owned Create Task form | React bound; old branch disabled |

### Detailed TypeScript contract

```ts
interface Project {
  id: string;
  name: string;
  description: string;
  status: 'running' | 'blocked' | 'idle' | 'archived';
  progress: number;
  createdAt?: string; // ISO 8601
}
interface RoleTemplate {
  id: string;
  name: string;
  version: string;
  purpose: string;
  capabilities: string[];
  allowedPermissions: string[];
  preferredModelId?: string;
  lifecycle: 'draft' | 'active' | 'parked' | 'archived';
}
interface Agent {
  id: string;
  projectId: string;
  roleTemplateId: string;
  name: string;
  status: 'idle'|'working'|'parked'|'paused'|'failed'|'archived';
  taskId?: string|null;
  sessionId?: string|null;
  lastSeenAt?: string;
}
```

`RoleTemplate` declares reusable capabilities and permissions; `Agent` is an instantiated role assigned to a **specific project**. Parking a template **does not** silently rewrite already existing instances; archiving a template with active, idle, or paused agents fails in the local mock; parked references are permitted (backend must decide policy explicitly). Agent `resume` can only simulate an `idle` state, and the frontend must not display a fabricated session ID or running lease. Project `archive` preserves project, channels, tasks, artifacts, logs and audit references; local mock parks attached agents. Server-side cascade, permissions, audit entry, idempotency and possible conflicts all remain **to be confirmed**.

### Error expectations and missing backend guarantees

`400` malformed payload (e.g. empty names), `401` unauthenticated, `403` insufficient permission, `404` unknown ID, `409` forbidden lifecycle transition, `422` invalid dependency / assignment; actual status mappings must be agreed with the backend. Production must validate role permissions and project membership independently of the client. Any successful server-side archive, role activation, or agent lifecycle transition must emit an immutable audit record, ideally through a replayable SSE/WebSocket stream. New CRUD endpoints and their types are **proposals**, not verified server implementations.

### Data/schema migration note

- Shared selected project is persisted under `mrlc.selectedProject.v3`.
- Organization mock data resides at `mrlc.organization.demo.v3` and preserves stable IDs.
- Workspace/Tasks retain the `mrlc.workspace.demo.v2` store; older role-name assignments are normalized on load to canonical role IDs (`role-engineer`, etc.).
- Both repository instances broadcast synchronous invalidation to their own subscribers. They do **not** implement cross-tab coordination or server event replay.
- `src/data/httpOrganizationRepository.ts` and `src/data/httpWorkspaceRepository.ts` are present as swappable adapters but are **not** activated while backend contracts are unverified.

**Phase 3 integrity addendum:** `LocalWorkspaceRepository` accepts an injected `OrganizationRepository` and refuses `createTask`, `createChannel`, `sendMessage`, and `reviewTask` mutations against missing/archived projects. This is only defense-in-depth for the demo: authorization and atomic archival boundaries must be enforced on the backend.

**Candidate HTTP route verification:** `scripts/test-contracts.sh` compiles the typed HTTP adapter and asserts 12 Phase 3 routes, request bodies, encoded path IDs and Bearer headers against a synthetic `fetch` response. This is **client contract testing**, not MRLC backend integration.

---

## Phase 4 — Artifacts / Approvals / Compute: implemented client-to-repository contracts (2026-10-14)

This section supersedes the placeholder result types for these three domains in the old summary table at the start of this document. Source-first discovery is recorded **before implementation** in `INTERFACE_DISCOVERY_PHASE4.md`. Implementation and HTTP mapping are now in:

- `src/data/artifactRepository.ts` and `src/pages/ArtifactsPage.tsx`
- `src/data/approvalRepository.ts` and `src/pages/ApprovalsPage.tsx`
- `src/data/computeRepository.ts` and `src/pages/ComputePage.tsx`
- `src/data/httpPhase4Repositories.ts`, `src/api/services.ts`, `src/api/client.ts`

**Switching:** Local repositories are **active**; HTTP repositories are **defined but not enabled**. The paths and response schemas below are proposed, not descriptions of existing backend functionality. The demo uses IndexedDB for real artifact blobs when available, localStorage for approvals/slots, and no real GPU/billing/session data.

### ART-01 list artifacts / ART-03 metadata

| Attribute | Contract |
|---|---|
| Legacy | L123–126, L243 (`filterArtifacts`), L281 (`open-artifact`) |
| React → Repo | `repo.listArtifacts(projectId)` → `Promise<Page<Artifact>>`; `repo.getArtifact(artifactId)` → `Promise<Artifact>` |
| Candidate API | `GET /api/v1/projects/{projectId}/artifacts`, `GET /api/v1/artifacts/{artifactId}` |
| Request | URI IDs: nonempty strings, encoded with `encodeURIComponent`; no request body |
| Response | `{items: Artifact[],nextCursor?:string|null}` and `Artifact` (`id`, `projectId`, `name`, `mimeType`, `version`, `status`, `createdAt`, optional `taskId`, `sizeBytes`, `checksum`, `contentAvailable`, `demoMetadata`) |
| Failure | 401/403/404 per server, authorization project-scoped; local missing ID throws |

### ART-02 upload actual bytes and versioning

| Attribute | Contract |
|---|---|
| Legacy | L293–294 `attach-file-submit` stored just `file.name`, never content |
| React → Repo | `uploadArtifact(projectId: string,file: File,taskId?:string):Promise<Artifact>` |
| Candidate API | `POST /api/v1/projects/{projectId}/artifacts` |
| Request | `Content-Type: multipart/form-data` **set by browser**; `file` (binary Blob/File, required), `taskId` (string, optional). Bearer authorization when configured |
| Response | `Artifact`: stable logical `id`, incremented `version`, real `sizeBytes`, optional computed SHA-256 lowercase hexadecimal `checksum`, `contentAvailable:true`, default `status:'partial'` pending external verification |
| Local mock | Browser IndexedDB `mrlc.artifacts.demo.v4`, store `artifacts`: `{artifact, versions, content:Blob,oldContents,evidence}` in one object-store transaction; same project+filename creates next version; 25 MiB demo upload limit; project archived/missing or mismatched task ID rejected; memory-only fallback if IndexedDB unavailable |
| Backend must validate | file size/type/malware, project-task membership, permission, storage quota, content hash, immutable version IDs, concurrent version numbering, content-addressed deduplication, audit event; a successful UI event is not evidence of a backend upload |

### ART-04 versions / ART-05 evidence / ART-03 content

| Method | API | Request | Response |
|---|---|---|---|
| `listVersions(artifactId)` | `GET /api/v1/artifacts/{id}/versions` | path ID | `{items:ArtifactVersion[]}`; each `{id,artifactId,version,createdAt,sizeBytes?,checksum?,contentAvailable}` |
| `listEvidence(artifactId)` | `GET /api/v1/artifacts/{id}/evidence` | path ID | `{items:EvidenceLink[]}`; each `{id,artifactId,kind:'source'|'run'|'task'|'decision',referenceId,description,createdAt}` |
| `getContent(artifactId,versionId?)` | `GET /api/v1/artifacts/{id}/content?versionId=...` | optional version ID as query parameter | **Binary Blob** (`application/octet-stream` or content MIME); `204/404` treated as no content by candidate frontend |

The demo's four legacy artifact names are metadata-only placeholders, marked `demoMetadata:true` and `contentAvailable:false`; there are no fabricated bytes, hashes, verification records, or URLs. Prior content versions are stored with Blob data; the first real uploaded version is `v1.0`. A new upload does **not** become verified merely because bytes were saved. The current demo has no evidence-link creation UI or real verifier; reading the evidence list returns empty records unless an actual source is wired later.

### APR-01 list / APR-02 detail and immutable history

| Attribute | Contract |
|---|---|
| Legacy | L224–228, L291, L345–347 |
| React → Repo | `listApprovals(projectId?:string):Promise<Page<Approval>>`, `getApproval(id):Promise<Approval>`, `listDecisions(id):Promise<Page<ApprovalRecord>>` |
| Candidate API | `GET /api/v1/approvals?projectId=...`, `GET /api/v1/approvals/{id}`, `GET /api/v1/approvals/{id}/decisions` |
| Approval response | `id,projectId,requestedByAgentId,title,type,risk,status,evidenceArtifactIds,createdAt,revision`, optional `description,impact,requestedByLabel,resolution,resolvedAt` |
| History response | `{items:[{id,approvalId,decision,reason,decidedAt,revision,actor}]}`; append-only records in demo |

### APR-03 decide with optimistic concurrency

| Attribute | Contract |
|---|---|
| Legacy | L347–348 `resolveApproval`: the old implementation unconditionally deleted the selected entry via `splice()` |
| React → Repo | `decide(id: string, input: ApprovalDecisionInput):Promise<Approval>` |
| Candidate API | `POST /api/v1/approvals/{id}/decisions` |
| JSON input | `{ "decision": "approve" | "reject" | "reduce_scope" | "request_details", "reason"?: string, "expectedRevision": number, "scopeOverride"?: { [key: string]: string | number | boolean } }` |
| Response | Updated `Approval`, status `pending` for `request_details` (new revision), otherwise `resolved` with `resolution` and `resolvedAt`; corresponding new `ApprovalRecord` returned via listDecisions |
| Error expectations | 400/422 malformed decision; 401/403 no authority; 404 missing; **409** already resolved, stale revision, invalid transition or changed evidence; failed write must leave history and approval unchanged |

Local repository `mrlc.approvals.demo.v4` verifies project is not archived, checks revision before appending a decision, requires a reason for reject/reduce_scope/request_details and nonempty scalar `scopeOverride` for reduce_scope, and preserves resolved requests. This is a UI demo, not a security, transactionality, or immutable audit guarantee. Production requires authenticated actor identity, server-side CAS transaction, idempotency key and immutable audit sink; history must never be trustable from browser storage.

### CMP-01 slots, metrics, queue/sessions/budget

| Method | Candidate API | Request | Response |
|---|---|---|---|
| `listSlots()` | `GET /api/v1/runtime/slots` | none | `Page<ComputeSlot>` with typed `id,state,agentId?,taskId?,modelId?,decodeTokensPerSecond?,prefillTokensPerSecond?,contextUsedTokens?,contextCapacityTokens?,vramUsedBytes?,vramCapacityBytes?,leaseExpiresAt?` |
| `listQueue()` | `GET /api/v1/runtime/queue` | none | `Page<RuntimeQueueItem>` with `{id,position,projectId,title,requestedRole,priority,waitReason,taskId?,modelName?}` |
| `listSessions()` | `GET /api/v1/runtime/sessions` | none | `Page<RuntimeSession>` with `id,projectId,agentId,state,slotId?,startedAt?` |
| `getMetrics()` | `GET /api/v1/runtime/metrics` | none | `RuntimeMetrics|null`: `sampledAt`, `resources:[{id,label,utilizationPercent?,usedBytes?,capacityBytes?}]` |
| `getBudget()` | `GET /api/v1/runtime/budget` | none | `RuntimeBudget|null`: `currency,spent,limit?,forecast?,sampledAt` |

Original L230–235/L349 values are *synthetic* (not provider telemetry). Local `listSlots` and `listQueue` supply visibly labeled examples. Local `getMetrics` and `getBudget` return `null`, while `listSessions` returns empty. A simulated active slot must never be presented as an authenticated running inference session. No real costs, GPU telemetry or provider health are asserted by this page.

### CMP-02 / CMP-03 state transitions

| Method | Candidate API | Request | Expected response / preconditions |
|---|---|---|---|
| `pauseSlot(id)` | `POST /api/v1/runtime/slots/{id}/pause` | path ID; no body | `ComputeSlot` (`active` → `paused`), other states conflict |
| `resumeSlot(id)` | `POST /api/v1/runtime/slots/{id}/resume` | path ID; no body | `ComputeSlot` (`paused` → `active`), other states conflict |
| `releaseSlot(id)` | `POST /api/v1/runtime/slots/{id}/release` | path ID; no body | `ComputeSlot` (`active/paused` → `free`), model/lease fields cleared, slot record remains; already free idempotent in mock |

Backend must guard lease ownership, scheduler reconciliation, cancellation acknowledgement, GPU cleanup and retry races; the local browser action does **not** act on a real GPU. Real backend may require asynchronous accepted responses instead of immediate slot state changes; proposal must be reconciled before use.

### Phase 4 verification and known exclusions

- `scripts/test-phase4.sh`: Node domain type checking plus state/isolation/audit/Blob/version tests.
- `scripts/test-phase4-http.sh`: **18 candidate routes** including multipart binary bytes, SHA-independent upload path, encoded IDs, `expectedRevision` and `scopeOverride` JSON, Bearer authorization and binary download.
- `scripts/test-domain.sh`, `scripts/test-contracts.sh` remain passing for Phases 2–3.
- 29 TS/TSX files were syntax parsed and checked with `strictNullChecks` via a temporary React declaration shim. This is **not** an npm-backed build or browser E2E.
- Binary uploads rely on browser IndexedDB; browser persistence, quota behavior and reload/recovery have not been verified in browser tests.
- Neither the current `Workspace` composer nor earlier task acceptance criteria attach real evidence links; this awaits schema/UX integration.
- The candidate HTTP adapters are **not enabled**; SSE/WS revalidation, authentication policy, robust permissions, cross-tab consistency and production transactionality remain.

---

## Phase 5 discovery gate (pre-implementation)

The interface candidates I01–I07 were recorded first in `INTERFACE_DISCOVERY_PHASE5.md`, anchored to original HTML lines L84–107, L145–158, L192–193, L244, L254–256, L266–268, L287–288, L317–321, L326–327 and L334–335. In Phase 5 these will be implemented incrementally with typed schemas and mock behavior. Do not treat the candidate URLs as existing MRLC backend endpoints.

## Phase 5 completed — Infrastructure, Logs, Settings, Overview (2026-10-14)

**Discovery-first record:** `INTERFACE_DISCOVERY_PHASE5.md` I01–I07 was written before implementation. These are forward-compatible **candidate contracts**; the supplied artifact includes no MRLC backend.

### I01 — System health / user-triggered checks

| Source | Implementation | Candidate endpoint | Request | Response / behavior |
|---|---|---|---|---|
| Original L145–149, L266, L326 | `src/data/systemRepository.ts` `getHealth()` / `src/pages/InfrastructurePage.tsx` / `src/app/App.tsx` | `GET /api/v1/system/health` | Bearer authorization; no body | `SystemHealth \| null`: `{healthy:boolean,checkedAt:string,services:{name,healthy,latencyMs?}[]}`. `null` = unconnected, not healthy |
| Original L326 | `runHealthCheck()` | `POST /api/v1/system/health-checks` | Authenticated POST, `{}` or no body | `SystemHealth \| null` after a true remote health-check. **Permission / rate limit** must be server enforced |

The local repository intentionally returns `null`, so the status bar reads **Status unknown**, and clicking Run health check never reports a fake pass.

### I02 — Topology / hardware inventory

| Source | Implementation | Candidate endpoint | Request | Response |
|---|---|---|---|---|
| Original L147–149, L327 | `SystemRepository.getTopology` | `GET /api/v1/system/topology` | No body | `SystemTopology \| null`: `{sampledAt,nodes:[{id,label,kind,status}],edges:[{from,to,label?}]}` |
| Original L147–149, L249 | `SystemRepository.listNodes` | `GET /api/v1/system/nodes?scope=local\|remote` | Optional `scope` | `Page<InfrastructureNode>`, node ID, name, scope, status and optional timestamps/CPU/RAM/storage fields |
| Original L149 | `SystemRepository.listAccelerators` | `GET /api/v1/system/accelerators` | No body | `Page<Accelerator>`, accelerator ID, node ID, status, optional VRAM bytes, power watts, temperature °C, sample timestamp |

Backend must supply device data from an authenticated monitoring source. Local mock returns **empty inventory** and `null` topology; UI does not present source prototype GPU stats as readings.

### I03 — Logs and filtering/pagination

| Source | Implementation | Candidate endpoint | Query | Response |
|---|---|---|---|---|
| Original L152–155, L244, L319–321 | `SystemRepository.listLogs(filter)`; `LogsPage` | `GET /api/v1/system/logs` | `projectId?:string`, `cursor?:string` (opaque), `limit?:number` (1–200), `q?:string`, `level?:debug\|info\|warn\|error`, `source?:string`, `since?:ISO8601`, `until?:ISO8601`, `traceId?:string`, `resourceId?:string` | `Page<AuditLog>` `{items:[{id,timestamp,level,source,message,traceId?,projectId?,taskId?}],nextCursor?:string\|null}` |

In local mode this repository aggregates **recorded task activity from Workspace**, not a complete system/audit log. `projectId` filter and task scoping are enforced locally. The local cursor uses an offset solely for demo purposes; **a real server must support stable opaque cursors**, consistent ordering, and access-controlled log scopes. There is no SSE stream in local mode.

### I04 — Log export and saved views

| Source | Implementation | Candidate endpoint | Request | Response |
|---|---|---|---|---|
| Original L287–288 | `LogsPage.serializeLogs` / `downloadText` (local); `services.system.exportLogs` (candidate) | `POST /api/v1/system/logs/export` | `{filter: Omit<LogFilter,"cursor"\|"limit">, format:"jsonl"\|"csv"\|"audit_bundle"}` | `{exportId,downloadUrl,expiresAt}`. A signed URL must be short-lived and tenant-scoped |
| Original L317–318 | `SystemRepository.listLogViews/saveLogView/deleteLogView`; HTTP adapter | `GET /api/v1/users/me/log-views`, `POST /api/v1/users/me/log-views`, `PATCH/DELETE /api/v1/users/me/log-views/{viewId}` | `POST {name:string, filter:Omit<LogFilter,"cursor"\|"limit">}`; `PATCH {name?,filter?}` | `Page<LogSavedView>` or one `{id,name,filter}`; DELETE should return 204 |

Local CSV/JSONL export serializes **loaded local rows only**, not all server rows. CSV uses quote escaping and mitigates formula injection. `audit_bundle` is *not offered in offline UI* because it cannot produce authenticated audit evidence. Saved views persist locally in `localStorage` until remote preferences are implemented.

### I05 — Runtime settings and device-local appearance

| Source | Implementation | Candidate endpoint | Request | Response |
|---|---|---|---|---|
| Original L158, L254–256, L334–335 | `SettingsRepository.getRuntimeSettings` | `GET /api/v1/projects/{encodedProjectId}/settings` | Bearer auth | `ProjectRuntimeSettings`: `{modelId:string, contextPolicy:"task"\|"project",sessionPolicy:"resumable"\|"ephemeral",frontierEscalation:"ask"\|"budget"\|"disabled",revision:number}` |
| Same | `SettingsRepository.updateRuntimeSettings` | `PATCH /api/v1/projects/{projectId}/settings` | `{modelId,contextPolicy,sessionPolicy,frontierEscalation,expectedRevision:number}` | Updated `ProjectRuntimeSettings`; server must return `409` on stale revision, `403` on unauthorized mutation |
| Same | `SettingsRepository.resetRuntimeSettings` | `POST /api/v1/projects/{projectId}/settings/reset` | `{expectedRevision:number}` | Updated defaults and incremented revision; server owns the real default settings |
| Original L158, L267 | `SettingsRepository.getAppearance/saveAppearance/resetAppearance` | **Local UI behavior; no backend route required** | `{theme:"strata-dark"\|"soft-dark", motion:"highlights"\|"reduced",density:"compact"\|"comfortable", stageAnimation:"play-once"\|"static-gold"}` | Saved to `localStorage` and applied to body classes via `useAppearance` |

The local repository rejects writes to archived projects and protects updates with a revision check. The project settings HTTP implementation is a **candidate adapter**, not activated against an absent server.

### I06 — Aggregate Overview

| Source | Implementation | Candidate endpoint | Request | Response |
|---|---|---|---|---|
| Original L84–107, L268 | `overviewRepository.ts` (`projectOverview/getLocalProjectOverview`); `OverviewPage.tsx` | `GET /api/v1/projects/{projectId}/overview` | Authorized project ID | `ProjectOverviewSnapshot`: `{projectId,taskCount,activeTasks,queuedTasks,blockedTasks,pendingApprovals,activeAgents,idleAgents,parkedAgents,progressPercent:number\|null,stages:[{messageId,title,verified,level,occurredAt,taskId}],activeSlots,totalSlots,budget:RuntimeBudget\|null,metrics:RuntimeMetrics\|null,health:SystemHealth\|null}` |

Local snapshot computes the average *task* progress, not milestone completion; filters every task, approval, agent, queue item, stage message and slot by project ID. Stage events are displayed only when a message has a `stageCompletion.verified` field; fixture claims are not server-verified. VRAM, provider health, cost and ETAs remain unavailable without live sources.

### I07 — Native shell / former DOM action dispatcher

Original L245–253, L258–336; replaced in `src/app/App.tsx`, `src/app/useAppearance.ts`, `src/main.tsx`. Route navigation, Ctrl+K commands, theme change and overlay close are **local UI actions, not backend REST calls**. React routes all 12 pages and the legacy controller is no longer loaded. File `public/legacy-controller.js` is retained only as an historical reference for regression investigation; no source references it at runtime.

**Verification:** `scripts/test-phase5.sh` compiles the repository/API layer and asserts local isolation, CAS, no fake health, log filter/pagination/views, Overview derivation, and 14 candidate HTTP routes with percent-encoding and auth header forwarding. The tests do not prove a functioning MRLC backend, a successful Vite build or browser E2E.

---

## Phase 6 — Actual frontend data-source wiring and attachments (2026-10-14)

**Discovery-first gate:** `INTERFACE_DISCOVERY_PHASE6.md` was written before Phase 6 repository migration and before adding message attachments. No real MRLC backend source or production endpoint was provided. The following endpoints are **candidate contracts only**. A separate local HTTP fixture was used to test network requests; it is **not** the MRLC orchestration backend.

### Runtime data-source boundary

| Setting | Format | Default | Result |
|---|---|---|---|
| `VITE_DATA_SOURCE` | `mock` or `http` | `mock` | `mock` reads browser-local repositories; `http` uses all typed HTTP adapters, without silent fallback |
| `VITE_API_BASE_URL` | absolute `http(s)://...` or same-origin `/prefix` | unused in mock | Required with `http`; `ApiClient` appends `/api/v1/...` paths |
| `VITE_EVENTS_URL` | absolute `http(s)://...` or `/...` | empty | Optional server event stream. When omitted, successful remote mutations still invalidate subscribed reads; cross-client push is unavailable |

`src/data/repositories.ts` is the **single source** of selected repositories for all pages, shell, settings and Overview. `src/data/remoteInvalidation.ts` owns optional one-connection SSE invalidation and fanout. Vite exposes only the three public variables at bundle time (`vite.config.ts`). **Never put API keys, access tokens or secrets in `VITE_*` variables.**

When `VITE_DATA_SOURCE=http`, `GET`/`POST` failures remain visible; they do **not** revert to demo data. A real backend must enforce project scopes, role permissions, revision checks, event authentication, upload limits and resource ownership. The frontend's opt-in switch is not proof of server compatibility. Runtime appearance stays browser-local; runtime policies go to the server.

### HTTP repository wiring by subsystem

| Prototype/source | React entry point | HTTP adapter / candidate requests | Expected result |
|---|---|---|---|
| Original HTML L194–199, L239–242, L277–280, L304–308 | `ProjectsPage`, `RolesPage`, `AgentsPage`, `AppContext` | `httpOrganizationRepository`; project/role/agent operations in Phases 2/3 | `Page<Project>`, `Page<Agent>`, `Page<RoleTemplate>` or updated entity |
| L200–213, L269–276, L339–343 | `WorkspacePage`, `TasksPage` | `httpWorkspaceRepository`; project channels/tasks and per-channel messages | `Page<Channel>`, `Page<Task>`, `Page<Message>`, updated entity |
| L123–126, L281–295 | `ArtifactsPage`, Tasks deliverables | `httpArtifactRepository`; multipart upload, Blob download, versions/evidence | `Artifact`, `Blob`, `Page<ArtifactVersion>`, `Page<EvidenceLink>` |
| L224–228, L291, L345–348 | `ApprovalsPage` | `httpApprovalRepository`; decision/history | `Approval`, `Page<ApprovalRecord>` |
| L230–236, L292, L332–333, L349 | `ComputePage` | `httpComputeRepository`; slots/pause/resume/release/queue/session/metrics/budget | `ComputeSlot`, `Page<ComputeSlot>` or nullable metrics/budget |
| L145–158, L244, L287–288, L319–327 | `InfrastructurePage`, `LogsPage`, `SettingsPage` | `httpSystemRepository`, `httpSettingsRepository` | Health/topology/logs, project settings and appearance |
| L84–107, L268 | `OverviewPage` | `getProjectOverview`: aggregates selected repository reads; existing candidate snapshot endpoint remains optional | Computed `OverviewDetails`, filtered by project ID |

### SSE proposal and revalidation

- Candidate `GET /api/v1/events`, delivered as `text/event-stream` with **unnamed/default message events** carrying JSON `{id:string,type:string,projectId?:string,occurredAt:string,payload:unknown}`.
- Client validates required fields, retains 512 recent event IDs to deduplicate and asks React queries to refresh. A single stream is shared among subscribers; it is closed when no listeners remain. The native `EventSource` reconnection mechanism is used.
- **Auth caveat:** Native EventSource cannot set an arbitrary `Authorization: Bearer` header. Same-origin session-cookie authentication is the suggested transport unless the backend provides an explicitly authorized alternative. Replay/resume cursor, ordering, 401/403 handling and actual event delivery are not yet proven.
- After a successful local HTTP write, the corresponding repository invalidates queries immediately even without SSE. A rejected mutation produces **no** success invalidation. This is UI revalidation, not immutable server audit.

### Workspace attachment interface (newly completed prototype behavior)

**Original:** L112, L293–294, L339–343. **Frontend:** `src/pages/WorkspacePage.tsx`, `src/data/workspaceRepository.ts`, `src/api/services.ts`.

1. `POST /api/v1/projects/{projectId}/artifacts` `Content-Type: multipart/form-data`; form fields: `file` (required actual binary File), optional `taskId` (string). Response `Artifact` with `{id,projectId,name,mimeType,version,status,createdAt,contentAvailable,...}`. In local mode, Blob bytes are stored in IndexedDB where supported; memory-only otherwise.
2. `POST /api/v1/channels/{channelId}/messages` `Content-Type: application/json`; request `{content:string,responseMode:"single"|"multi",artifactIds?:string[]}`. The IDs must already exist and belong to the channel's project. Response `Message` `{id,channelId,senderName,content,createdAt,artifactIds?:string[]}`. Empty-content + file submission gets a descriptive human-readable content string.
3. Messages render linked file names; selecting one navigates to the Artifact library for download/version inspection. Local mock rejects cross-project attachment IDs, and no model invocation occurs merely by posting a message.

**Non-atomic boundary:** If file upload succeeds and message creation fails, the new Artifact remains in storage. The UI reports this rather than claiming all-or-nothing success. Production backend must implement staged attachments, idempotency/compensating deletion or orphan cleanup, and authorized file ownership checks; do not assume client-side validation is sufficient.

### Phase 6 verification

`npm run test:phase6` starts a **local HTTP fixture** (not an MRLC backend) and checks actual `GET/POST` requests, returned server-only data, server HTTP errors, no silent mock fallback, immediate mutation invalidation, SSE ID deduplication/cleanup, binary attachment association and cross-project attachment rejection. `npm run test:all` runs Phase 3–6 checks. This is not browser E2E, authentication verification, production integration or runtime execution testing.


## Phase 8.1 authentication additions

| Interface | Method | Purpose | Credential storage |
|---|---|---|---|
| `/api/v1/auth/token` | GET + `X-MRLC-Token` | Exchange a dedicated URL login token for the normal HttpOnly session | Admin token: environment; user token: digest only |
| `/api/v1/auth/me/token` | GET | Inspect current account token-management state | No plaintext returned |
| `/api/v1/auth/me/token` | POST | Generate or replace current non-admin token | Plaintext returned once; keyed digest persisted |
| `/api/v1/auth/me/token` | DELETE | Revoke current non-admin token | Digest removed |

The frontend accepts `/?token=...`, strips the query parameter immediately, then calls the token-exchange endpoint with a header. Password login remains a separate credential path.
