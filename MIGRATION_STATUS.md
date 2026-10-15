# Migration ledger

## Done in this checkpoint

- [x] Preserve original uploaded source as reference (ship `reference/index.original.html`).
- [x] Extract shared responsive/dark styles from HTML into CSS.
- [x] Split all 12 original page sections into individual React TSX components.
- [x] Reconstruct shell, sidebar, dialogs, drawers and mobile navigation in React JSX.
- [x] Retain original demo controller as explicitly named compatibility bridge.
- [x] Introduce strict TypeScript domain models for main MRLC entities.
- [x] Introduce a typed HTTP client and a candidate service per backend-capable domain.
- [x] Add SSE event client with explicit typed event payload.
- [x] Document source locations, expected requests, server constraints and unknowns.
- [x] Parse-check all TS/TSX files (syntax only).

## Not done / do not claim otherwise

- [ ] Full removal of Vanilla JS DOM mutations: current demo still runs them.
- [ ] UI service bindings: API methods exist but buttons still invoke the legacy controller.
- [ ] Full React-managed mutable state, undo/rollback, optimistic updates and caches.
- [ ] Real back-end connectivity (MRLC backend files/schema were not supplied).
- [ ] Install dependencies and run Vite build in this environment (registry connection timed out).
- [ ] Browser visual parity and E2E tests (requires installed dependencies/browser).
- [ ] Response and event schema finalized against actual backend.

## Priority order for continuation

Workspace/messages → Tasks/activity → Projects → Agents/Roles → Artifacts → Approvals → Compute → Infrastructure/Logs → Settings → global navigation/overlays. At each step: replace demo state with typed mock service, add loading/error/empty states and corresponding contract test, then remove old controller branch.


## Phase 2 — React-owned Workspace and Tasks (2026-10-14)

- [x] Workspace channels, messages, search, composer and stage-event rendering are React controlled.
- [x] Tasks list, filters, details, activity and task creation/review are React controlled.
- [x] Replaced legacy initial DOM mutations for `renderChannels`, `renderMessages`, `renderTasks`.
- [x] Added `WorkspaceRepository`, local mock implementation with project-scoped data and localStorage persistence, and an HTTP adapter reusing typed candidate services.
- [x] Updated CSS for new native form controls without replacing the original visual theme.
- [x] All TS/TSX modules parse-check, legacy controller JS syntax-check.
- [ ] Other ten pages and shell still depend on legacy controller: DO NOT remove it yet.
- [ ] Backend API paths are candidate contracts only. No live backend available.
- [ ] npm install/build/browser E2E blocked by registry DNS (`EAI_AGAIN`).
- [ ] Mock repository currently fixes project to `project-1`; switchable multi-project binding remains.
- [ ] Workspace file upload intentionally reports not connected instead of falsely reporting success.
- [ ] Native Workspace/Tasks use local repository now; remaining pages use separate legacy in-memory data. Thus cross-page data consistency is not guaranteed until remaining pages migrate.

## Phase 3 — React-owned Projects, Agents, Roles and shared shell (2026-10-14)

### Completed

- [x] **Source-first API inventory:** original locations (L194–199, 215–222, 239–242, 277–280, 299–308, 322–324, 344) recorded in `organizationRepository.ts` and the detailed Phase 3 section of `API_CATALOG.md`.
- [x] Added typed `OrganizationRepository`, persisted `LocalOrganizationRepository`, and inactive future `httpOrganizationRepository`.
- [x] Migrated **ProjectsPage**: list/search/status filter, create, update name/description, archive while keeping data (not `splice`), navigate to project by ID, expose project-scoped agent counts.
- [x] Migrated **RolesPage**: list/filter, create/update/version, lifecycle transitions, permissions/purpose/model display, usage computed from agent instances; template lifecycle is distinct from instance lifecycle.
- [x] Migrated **AgentsPage**: project-scoped list/detail, assign role instances, park/resume simulation, workload from task assignments, honest session-unavailable state.
- [x] Migrated React shell: project selection, sidebar/topbar/mobile navigation, hash routing, selected-project persistence via AppProvider.
- [x] Bound Workspace/Tasks to selected project; no other-project tasks/channels/messages should appear in the selected project, with empty states for projects without content.
- [x] Connected Workspace team list to project Agent repository; task role IDs and role templates now share a stable key.
- [x] Disabled initialization of old controller for Projects/Agents/Roles and stopped its project selector from hijacking the React-controlled element.
- [x] Rerouted global legacy command palette Create Task into native React form; old nav routes via a frontend-only custom event.
- [x] React Overview binds the selected project's name/status/description. World Model fixture content is not shown for other projects.
- [x] Node syntax check of legacy controller; domain repository TypeScript compilation; 25 Node data contract assertions + 12 HTTP adapter route checks; 25 TS/TSX transpile parse checks.
- [x] TypeScript source checked with `strictNullChecks` using a **temporary minimal React declaration shim**. This is not equivalent to a full build with real React type definitions.

### Remaining risks / deliberately unfinished

- [ ] 7 pages (Overview summary/detail, Artifacts, Approvals, Compute, Infrastructure, Logs, Settings) still contain legacy/static mock components. Overview project identity only is migrated.
- [ ] The React app still loads `public/legacy-controller.js` for unmatched demo actions, dialogs, and older pages. Eliminating it requires migrating all remaining operations and overlays.
- [ ] Live MRLC backend and authorization not supplied. The HTTP adapter compiles in principle but has not been exercised against any real endpoints. SSE revalidation is still not implemented.
- [ ] No npm registry access / no official Vite build, true React type check, screenshot comparison or browser E2E completed in this environment.
- [ ] The `mrlc.organization.demo.v3` localStorage data is a demo cache, not a transactionally durable database. Mock park/resume does not call model runtimes or move GPU leases.
- [ ] Static Overview and remaining legacy views may not yet reflect updated Agent/Task/Project data. Do not treat their displayed counters as live data.
- [ ] Full cross-tab coherence, abortable requests, optimistic rollback, live event buffering, and access-control tests remain for backend integration.

### Suggested next segment

Artifacts with real file bytes and version/evidence metadata → Approvals state machine (no deletion-as-approval) → Compute slots & metrics (no fabricated telemetry) → Infrastructure/Logs/Settings → Overview final binding → remove legacy controller → real backend contract and browser E2E verification.

### Phase 3 hardening checkpoint

- [x] Archived-project write guard shared by Workspace/Tasks local mock via injected OrganizationRepository (not trusted as server authorization).
- [x] Template archive allowed only after all attached agents are parked or archived; test covers blocking with active agents.
- [x] 25 organization + Workspace mock assertions and 12 HTTP adapter route checks pass after shared-repository injection.

## Phase 4 — React-owned Artifacts, Approvals, Compute (2026-10-14)

### Delivered

- [x] **Source-first discovery**: `INTERFACE_DISCOVERY_PHASE4.md` records each candidate backend interface with original file line numbers **before** it was implemented.
- [x] Artifacts native React UI: project-scoped search/filter/list/detail, true local file upload (25 MiB demo cap), download actual Blob content, version history, linked task selector, evidence state. No fake previews or hashes.
- [x] `LocalArtifactRepository`: persists metadata + File/Blob bytes and previous versions in a single IndexedDB object store/transaction (browser), memory-only fallback (Node); create/update project validation, archived write guard, cross-project task ownership check. Four original file names remain marked **metadata-only** placeholders.
- [x] `ArtifactRepository` and HTTP adapter: list/get/upload/versions/evidence/download Blob. TypeScript `ArtifactVersion` and `EvidenceLink` added; SHA-256 is computed only when Web Crypto supports it.
- [x] Approvals native React UI: project/risk/status filters, details, explanation, reason form, decision history, resolved records and revision guard.
- [x] `LocalApprovalRepository`: pending vs resolved state machine, append decision record on every permitted action, `request_details` keeps pending; explicit reason for reject/reduce_scope/request_details plus structured scope reduction; revision conflicts fail before mutation. No more `splice` on decision.
- [x] Compute native React UI: visual simulated slots, inspect, pause/resume, release confirmation, queue/sessions/budget tabs, honest unavailable telemetry/billing.
- [x] `LocalComputeRepository`: transition guards; released slot is `free` instead of removed. Runtime examples are explicitly labeled **simulation**. No fake real GPU, session, budget or metrics data.
- [x] Tasks Deliverables panel now reads the same Artifacts Repository rather than four isolated hard-coded names.
- [x] Old DOM initializer no longer renders approvals/compute/artifacts; compatibility script still serves other pages/overlays.
- [x] Compiled TypeScript domain+HTTP repositories; 34+ Node assertions and 18 HTTP route/payload tests passing; earlier 25 + 12 tests continue passing.
- [x] All 29 TS/TSX files parsed. `strictNullChecks` with an **isolated temporary React type shim** passed; cannot substitute for npm TypeScript build.

### Still missing

- [ ] No verified npm dependency install; Vite build and actual browser interaction/visual parity **not executed**.
- [ ] Browser IndexedDB quota, persistence across reload, large-file performance and cross-tab concurrency not tested; no atomic cross-repository transaction.
- [ ] Backend MRLC code/routes/authorization unavailable: HTTP interfaces are proposals, not verified server contracts; no live GPU/approval consequences.
- [ ] Evidence links and artifact verification writes, artifact signed download endpoints, approval conflict/authorization with server, slot lease ownership, SSE updates, persistent audit backend not yet integrated.
- [ ] Remaining pages: Overview dynamic dashboards, Infrastructure, Logs and Settings. Global legacy overlays/controller still present, and Overview still renders its original demo metrics for project-1.

### Next segment

Migrate Infrastructure / Logs / Settings as typed React pages, replace Overview static counters with project-scoped repositories, implement global native overlays and remove `legacy-controller.js`, then dependency-backed build, browser E2E and real backend contract reconciliation.

Browser test attempt: the installed Chromium 144 binary was invoked headless with a minimal data URL and a locally served IndexedDB test page. Chromium did **not** complete startup within the imposed 8–18 second limits in this container (timeout 124); therefore **no browser IndexedDB persistence PASS is claimed**. Node Blob storage tests pass, but IndexedDB reload testing must be repeated in a working browser environment.

## Phase 5 — Complete React ownership of all pages and shell (2026-10-14)

> This section **supersedes prior phases' “remaining pages / compatibility controller still loaded” statements** above; earlier sections remain as the chronological migration ledger rather than retroactively edited history.

### Completed

- [x] Before implementation, identified seven families of candidate interfaces with exact original HTML source locations and related behavior in `INTERFACE_DISCOVERY_PHASE5.md`; after implementation documented all input/output contracts in `API_CATALOG.md`.
- [x] Infrastructure now React-owned. Added `SystemRepository` types for health checks, topology, nodes and accelerators. Missing monitoring is reported as **unknown**, not as "healthy" or arbitrary GPU load.
- [x] Logs now React-owned. Local log feed contains only recorded task activity; scoped search/filters/pagination, saved views (browser-local), JSONL and CSV exports. CSV quote escaping and spreadsheet-formula mitigation tested. No fake "Live", audit coverage or complete server export claims.
- [x] Settings now React-owned. `SettingsRepository` has project-specific runtime policies with `revision` check, archive guard, reset; global browser appearance with persisted theme/motion/density/stage animation. Shell/theme toggler share the same repository.
- [x] Overview now **computed from project-scoped local repositories** (tasks, Agent instances, queue, approvals, artifacts, verified stage-event fixtures and compute slot assignment). No static queue ETA, fake health, VRAM or spending.
- [x] All 12 pages and global shell now use native React state/render. Command palette, system drawer, theme toggle, mobile + sidebar navigation, hash navigation are native.
- [x] Removed old script injection from `src/main.tsx`; moved old controller from `public/` to `reference/legacy-controller.original.js`, no longer shipped as publicly served executable asset. Removed leftover legacy navigation events and JSX `data-page/data-action` markers.
- [x] Fixed hidden-page display issue: page class formerly required old `.active` DOM mutation; now visible routes explicitly render. Mount **only the active page**, keyed by project/page, preventing hidden-page background queries and cross-project stale UI during project switching.
- [x] Built candidate HTTP implementations for Infrastructure/Logs/Settings + project Overview aggregate; URL encoding, Bearer forwarding and CAS payload assertions included.
- [x] Previous domain tests (25), previous HTTP route tests (12), Phase 4 Blob/approval/compute tests (34+), Phase 4 HTTP tests (18), Phase 5 repository and HTTP tests (14 candidate routes), CSV/JSONL serializer tests passed. All 15 TSX files parse; strict-null check with a minimal **test-only React declaration shim** passed.

### Remaining limitations (do NOT claim complete production migration)

- [ ] **npm install/build blocked**: `registry.npmjs.org` returns DNS `EAI_AGAIN`; official `tsc -b && vite build`, real JSX React typing, rendered screenshot and browser E2E remain unverified.
- [ ] No MRLC backend code or running backend provided; all REST paths are proposed contracts. Local mock data is not authoritative and has no server-side privilege enforcement. HTTP adapters must not be enabled blindly.
- [ ] No real SSE connection/revalidation, reliable multi-client consistency, authenticated sessions, production error/retry state, transactionally consistent project-wide snapshot, cryptographic audit, role permission enforcement or real runtime control.
- [ ] Browser IndexedDB quota and reload-persistence tests were not verified. Validate file bytes and version metadata across refresh, and backend evidence association once connected.
- [ ] Some prototype features are expressly nonfunctional without backend integration (runtime metrics, cost, health provider checks, sessions, model execution, verified stage events, server audit export).
- [ ] Command palette currently navigates pages; creating entities is performed in each native page, not via a universal global action registry.
- [ ] React dependencies and browser behavior must be tested in a normal local environment. Perform visual comparison with `reference/index.original.html`, responsive sizing, keyboard interaction, boundary data/empty states and file handling.

### Next step

Real dependency-backed build & browser visual/E2E checks → fix actual React or responsive regressions → map against *actual* backend schemas/auth → implement typed adapters with SSE + revision-aware mutations and permission tests → verify real runtime/artifact/approval flows end to end. No invented endpoint claim or production approval is implied.


## Phase 6 — Explicit HTTP mode, remote refresh and Workspace file attachments (2026-10-14)

> This section supersedes Phase 5 claims that the HTTP adapters are entirely inactive/uninstantiated. They remain **inactive by default**, but can now be explicitly selected. No actual MRLC backend was supplied, deployed or verified.

### Delivered

- [x] `INTERFACE_DISCOVERY_PHASE6.md` captured original source locations and candidate calls before implementation, including previously non-functional Workspace attachment flow.
- [x] Replaced direct local repository imports across 12 pages, AppContext, shell and appearance with `src/data/repositories.ts`, a singleton composition root.
- [x] Browser default is `VITE_DATA_SOURCE=mock`; explicit `http` mode uses all typed HTTP adapters and a required `VITE_API_BASE_URL`. No network error silently falls back to mock. Public config emitted only through Vite define; never include credentials.
- [x] Added `InvalidationBus`: subscribed HTTP writes cause immediate refresh on success, not on failure; optional one-stream SSE invalidation with payload guards, 512-ID dedup and close on last listener. No claim of server replay/auth compatibility.
- [x] Overview and shell now use the same selected repository family. All UI mode labels distinguish browser demo from experimental HTTP storage/monitoring.
- [x] `useRepositoryQuery` prevents old/out-of-order HTTP responses from replacing newer data after refetch and clears previous scoped data before refresh.
- [x] Workspace file button now uploads actual bytes to Artifact repository, sends `artifactIds` in a message, displays attached file links, permits file-only content, and reports partial failure if upload succeeded but message send failed.
- [x] Local message repository checks that attached Artifact IDs belong to the message channel's project. This is demo-level validation only; server must implement real ACLs and atomicity.
- [x] Added `BACKEND_STATUS.md`, updated `.env.example`, `API_CATALOG.md` and README.
- [x] Existing Phases 3–5 tests plus Phase 6 Node+HTTP fixture tests and shimmed TypeScript checks pass. Phase 6 HTTP fixture verifies real request/response, write invalidation and nonfallback. Attachment test verifies binary content and cross-project refusal.

### Remaining / important limitations

- [ ] **No MRLC orchestration backend**: no real database, task/agent orchestration, model sessions, API auth, durable audit, scheduler, provider integration or GPU operations. HTTP endpoints are still candidate contracts.
- [ ] `VITE_DATA_SOURCE=http` is **experimental** and requires an already-running compatible backend. Frontend does not start one; no backend artifact was supplied for contract reconciliation.
- [ ] True `npm install && npm run build`, React real type definitions, browser E2E and IndexedDB reload persistence remain unverified. During this phase `npm install` again could not complete in the container. Tests use built-in TypeScript CLI and local HTTP fixtures, not real React rendering.
- [ ] End-to-end backend authorization, cookie/CORS policy, event replay, idempotency, multi-client consistency, upload+message transactional handling, and async runtime semantics require actual server work.
- [ ] No server-provided trustworthy health/telemetry until monitored service is connected; `http` mode displays errors rather than invented data.
- [ ] Remaining prototypes such as `@Role` and `/task` insert text into chat but do not invoke real Agent execution. Authentic semantic commands must be specified on the backend.

### Next work

Provide and audit actual MRLC backend code and endpoint schemas; reconcile API catalog line-by-line, implement backend omissions and authorization; perform official browser build + E2E and real provider/lease/approval tests. See `BACKEND_STATUS.md`.
