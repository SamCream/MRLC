# Phase 7 — interface discovery → implementation → contract ledger

The original `reference/index.original.html` and Phase 6 `src/api/services.ts` were inspected before implementing the server routes. This table preserves the old source locations and identifies which former UI operations now have server ownership. The corresponding implementation is in `backend/app/routes_*.py`; its request/response contract appears in `BACKEND_API.md` and auto-generated `backend/openapi.json`.

| Original HTML location | Candidate client entrypoint / source | Implemented server responsibility | Source file |
|---|---|---|---|
| L84–107, L194–199, L239–241 | `projects.list/get/create/update/archive` | Durable projects, writable/archived policy, server-derived progress | `routes_organization.py` |
| L129–133, L215–222, L279–280, L299–308, L344 | `roles.list/create/update/lifecycle` | Role permissions, version increments, template lifecycle | `routes_organization.py` |
| L131–133, L242, L301–303, L322–324 | `agents.list/assign/get/park/resume/sessions` | Instance assignment, concurrency guard, no fake checkpointing | `routes_organization.py` |
| L109–114, L200–206, L269–274, L339–342 | `channels.list/create/messages/send/search` | Project-isolated channels, messages, attachment access, optional explicit agent response | `routes_workspace.py`, `routes_runtime.py` |
| L117–121, L207–213, L275–276, L343 | `tasks.list/create/get/update/review/activity` | Dependency graph validation, server-owned state, acceptance criteria and audit; added dispatch/verify/accept/schedule routes | `routes_workspace.py`, `routes_runtime.py` |
| L123–127, L281–295 | `artifacts.list/get/upload/content/versions/evidence` | Real file bytes, SHA-256, version history, task/evidence authorization | `routes_artifacts.py` |
| L135–136, L224–228, L291, L345–348 | `approvals.list/get/history/decide` | Conflict-safe decision records, scope override validation, durable evidence and history | `routes_approvals.py` |
| L137–142, L230–235, L292, L332–333, L349–350 | `compute.slots/queue/sessions/metrics/budget/pause/resume/release` | Actual worker leases and queue; unsupported physical GPU controls return explicit errors | `routes_runtime.py` |
| L145–149, L326–327 | `system.health/topology/nodes/accelerators` | Backend status and topology; absent GPU monitoring never fabricated | `routes_system.py` |
| L152–155, L287–288, L317–321 | `system.logs/logViews/exportLogs` | Server filtering, paged SQLite audit, saved log views, JSONL/CSV exports | `routes_system.py` |
| L158, L192–193, L254–256, L334–335 | `system.runtimeSettings` and local appearance | Server-validated project settings with revision CAS; server-stored appearance | `routes_system.py` |
| L84–107 and overview JS | `getProjectOverview` + `system.overview` | **Server computes authoritative counts, progress and completed stages** | `routes_system.py` |
| Entire legacy controller L190–355 | legacy page actions and global DOM controller | Absent at runtime; React dispatches HTTP requests only; no business mock fallback in HTTP mode | `src/data/repositories.ts` |

### Implementation order and result

1. Original source and candidate client methods identified as above.
2. Authoritative storage layer: `backend/app/storage.py` with transactional SQLite, event journal and audit journal.
3. Permission, dependency and state checks implemented by business modules; actual inference delegated to `backend/app/executor.py`.
4. Frontend `src/api/services.ts` and HTTP repositories bound to implemented routes. Login/session UI added in `src/app/AuthGate.tsx`.
5. Request/response, conflict/error semantics and limits documented in `BACKEND_API.md`. Integration tests exercise both HTTP and persistent state.

### Known non-equivalences

- Existing phase-6 `api/services.ts` was a **candidate contract**. Phase 7 now implements its used routes, plus new dispatch/accept/evidence/knowledge/session routes. The historical PRD still contains broader runtime requirements outside this implementation.
- Physical `Pause/Resume/Release` of a model process is not available via a generic OpenAI-compatible inference API. Unsupported actions fail with 409; the frontend does not fake them.
- No fake foundational research or verified knowledge is seeded. `knowledge` reports only evidence presence from server-verified task artifacts, not that the model actually understands an idea.
- This version is single-operator. Future multi-user ACLs, external provider budget gates, approvals that trigger typed privileged operations and actual agent checkpointing require further backend development.
