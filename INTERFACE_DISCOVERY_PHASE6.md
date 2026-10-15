# Phase 6 — Interface discovery before implementation (2026-10-14)

## Objective / verified boundary
The Phase 5 code uses 12 React pages, but every page imports a `local*Repository` directly. HTTP adapters in `src/data/http*Repository.ts` exist but are **uninstantiated**. No actual MRLC backend implementation, schema, auth configuration, or running endpoints were provided with this artifact. This phase will make the existing API boundary switchable **by explicit opt-in**, without asserting the backend exists or automatically falling back to demo data.

## Discover an operation -> record original source location and proposed interface BEFORE opening implementation

| # | Original prototype source / related code | Candidate backend API (already documented in API_CATALOG.md) | Planned frontend change |
|---|---|---|---|
| 1 | `reference/index.original.html` L239-242, L277-280, L304-308, L344 | `GET/POST/PATCH /api/v1/projects`, role/agent routes | Replace page/shell direct local organization imports with a selectable repository composition root; no backend implementation inferred |
| 2 | L200-213, L269-276, L339-343 | Project channels, messages, tasks, task review and activity | Route Workspace/Tasks through selected repository; revalidate after confirmed remote mutations |
| 3 | L120-126, L281-295 | Artifact upload/download, version/evidence routes | Route file-backed UI via selected ArtifactRepository, keep demo/remote storage clearly differentiated |
| 4 | L224-228, L291, L345-348 | Approval list, decisions, history | Route UI through selected ApprovalRepository and enforce server-only authority |
| 5 | L230-236, L292, L332-333, L349-350 | Slot list and transitions, queue, sessions, metrics, budget | Route Compute through selected backend adapter; label mock actions as simulations only |
| 6 | L145-158, L244, L287-288, L319-327, L334-335 | Health, nodes, logs, log views, settings | Route system settings/log UI via selected adapters; theme remains browser-local |
| 7 | L84-107, L268 and Phase 5 `src/data/overviewRepository.ts` | `GET /api/v1/projects/:id/overview` (candidate) + detail repositories | Derive Overview from the same selected repository family so HTTP mode does not quietly show demo data |
| 8 | L200-204, L230-236, L338-343 and `src/api/events.ts` | `GET /api/v1/events` (SSE proposal) | Add shared, optional replay/dedup capable client-side invalidation bridge; it cannot guarantee event delivery or order until server contracts are defined |
| 9 | No original backend connectivity UI; introduced as explicit Phase 6 integration affordance | `GET /api/v1/system/health` candidate | Display selected source `Demo` vs `Experimental HTTP`, and do not interpret mock values as actual runtime status |

### Decision and expected exchange shape
- `VITE_DATA_SOURCE=mock` is default. `VITE_DATA_SOURCE=http` must be set explicitly; `VITE_API_BASE_URL` is required and is the base URL, not a live-endpoint guarantee. No transparent fallback from HTTP errors to demo data.
- Existing typed REST requests use domain types in `src/domain/models.ts`; response lists use `{items: T[],nextCursor?: string|null}`. Mutations return the corresponding entity and must not be treated as executed in MRLC unless server confirms them.
- Proposed SSE payload: `{id:string,type:string,projectId?:string,occurredAt:string,payload:unknown}`. Auth/session, event names, replay cursor and permissions must be reconciled with a real backend. Missing/invalid messages must not replace valid UI state.
- Local appearance is intentionally kept in browser storage even in HTTP data mode. Runtime settings are not.
| 10 | L112, L200–204, L293–294, L339–343 and Phase 5 `WorkspacePage.tsx` file button | `POST /api/v1/projects/:projectId/artifacts` (`multipart/form-data`), then `POST /api/v1/channels/:channelId/messages` (`{content,responseMode,artifactIds?:string[]}`) | Complete file attachment and message-to-artifact reference; local adapter validates linked artifact project ownership, but cross-resource atomicity/cleanup remains a server requirement |

### Attachment request / response to finalize after implementation
- Upload: `multipart/form-data` `file` required (binary), `taskId` optional; returns `Artifact` with ID, project, byte availability, version.
- Send: JSON `{content:string,responseMode:'single'|'multi',artifactIds?:string[]}`; returns `Message` with `artifactIds` and timestamp; no automatic agent invocation implied by this API.
- If upload succeeds and posting message fails, file stays in Artifact library (explicitly show this and allow reuse via library). Backend should eventually support attachment staging/rollback or orphan cleanup.
