# Phase 4 interface discovery ledger — source snapshot before implementation

**Evidence source:** `reference/index.original.html`, immutable original, line numbers are 1-based. The legacy UI is a prototype: none of its in-memory mutations imply backend contracts exist. Each row was recorded before implementation; the proposed wire protocol is *candidate only*.

| ID | Original source location and representative original code | Proposed front-end operation → candidate API | Caveat |
|---|---|---|---|
| ART-01 | L123–126 static `#page-artifacts`, artifact library and filters; L243 `filterArtifacts()` | `listArtifacts(projectId)` → `GET /api/v1/projects/:projectId/artifacts` | Project-scoped list; no static counters |
| ART-02 | L293–294 `attach-file-submit` uses `file.name`, inserts HTML **without persisting bytes** | `uploadArtifact(projectId,file,taskId?)` → `POST /api/v1/projects/:projectId/artifacts` multipart | Preserve real File bytes in demo browser IndexedDB, reject failure |
| ART-03 | L281 `open-artifact` returns static metadata regardless of selected artifact; L295 static fake preview | `getArtifact(id)` / `downloadArtifact(id)` → `GET /api/v1/artifacts/:id` / `GET /api/v1/artifacts/:id/content` | Never synthesize file content, hashes or download URLs |
| ART-04 | L282, L284 `browse-artifacts`/`open-artifact-history` hard-code versions | `listVersions(id)` → `GET /api/v1/artifacts/:id/versions` | Distinguish logical ID and version ID |
| ART-05 | L283, L286, L289, L290 static evidence displays | `listEvidence(id)` → `GET /api/v1/artifacts/:id/evidence` | Absence of verified evidence must display as missing |
| APR-01 | L224–228 global in-memory `approvals`; L345–346 `renderApprovals()` filtered in browser | `listApprovals(projectId?)` → `GET /api/v1/approvals?projectId=...` | Preserve stable ID and project association |
| APR-02 | L291 `open-approval-evidence`, L347 `renderApprovalDetail()` | `getApproval(id)` / `listDecisions(id)` → `GET /api/v1/approvals/:id` and `/decisions` | Explicit audit history, not just a badge |
| APR-03 | L347–348 `resolveApproval()` does `approvals.splice(...)` for all outcomes | `decideApproval(id, {decision,reason,expectedRevision})` → `POST /api/v1/approvals/:id/decisions` | Immutable decision history; pending for request_details, no hard delete; 409 stale revision |
| CMP-01 | L230–235 demo `slots`,`resources`,`queue`; L349 `renderCompute()` fabricates live metrics | `listSlots()` → `GET /api/v1/runtime/slots`; `getMetrics()` → `GET /api/v1/runtime/metrics` | UI must distinguish simulation vs telemetry; unknown = — |
| CMP-02 | L292 and L332 `inspect-slot`/`pause-slot` | `pauseSlot(id)` and `resumeSlot(id)` → `POST /api/v1/runtime/slots/:id/pause|resume` | State transitions mock only; server enforces permissions and lease ownership |
| CMP-03 | L333 `release-slot` does `slots.splice(index,1)` | `releaseSlot(id)` → `POST /api/v1/runtime/slots/:id/release` | Released slot remains visible as free (not deleted) |
| CMP-04 | L140–142 static scheduler/sessions/budget | `listQueue`, `listSessions`, `getBudget` → `GET /api/v1/runtime/queue|sessions|budget` | Not connected = explicit empty/unavailable; avoid fabricated spend |

**Implementation sequence:** update repository contract/schema → local mock adapter & HTTP adapter → React native component → tests → fill `API_CATALOG.md` with exact types/payloads/results and status. Server-side permissions, actual SSE runtime events, token management and backend shape are still unspecified.
