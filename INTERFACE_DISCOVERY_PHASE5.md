# Phase 5 — discovery recorded BEFORE opening interfaces

Source of truth: `reference/index.original.html` (the originally supplied 358-line HTML). All API paths here are **proposals**, not known backend routes. Discovery order: (1) original location + related code → (2) typed repository/HTTP adapter → (3) request and response documentation in `API_CATALOG.md`.

## I01. Infrastructure health
- Original: L145–149 static `Nodes online`, GPU/memory/storage and provider cards; L326 `handleAction('run-health-check')` immediately opens a **fabricated** all-green result; L266 topbar system status.
- Proposed `GET /api/v1/system/health` → `SystemHealth | null`; `POST /api/v1/system/health-checks` → `HealthCheckResult` (authorized real check). `null` means no provider connected; **never** infer a healthy status from empty data.
- Implement: `SystemRepository.getHealth()/runHealthCheck()`; `InfrastructurePage` and system-status surface; local mock returns unavailable, not a fake success.

## I02. Infrastructure topology and node/accelerator inventory
- Original: L147–149 static topology, host and accelerator details; L327 `inspect-topology` displays fixed node names and pings; L249 `infrastructure-node` DOM filter.
- Proposed `GET /api/v1/system/topology` → `SystemTopology | null`; `GET /api/v1/system/nodes?scope=local|remote` → `Page<InfrastructureNode>`; `GET /api/v1/system/accelerators` → `Page<Accelerator>`.
- Implement: type-safe inventory, no fabricated live latency/VRAM/power usage; React filters and inspection.

## I03. Log list/search/page
- Original: L152–155 hard-coded seven `log-row` entries and counters; L244 `filterLogs()` only filters fixed DOM rows; L319–321 time/trace/resource filters.
- Proposed `GET /api/v1/system/logs?projectId=&cursor=&limit=&q=&level=&source=&since=&until=&traceId=&resourceId=` → `Page<AuditLog>`; cursor must be opaque.
- Implement: `SystemRepository.listLogs()`; local mock derives only actual recorded Workspace task activity; no claim of global/audit coverage or live stream.

## I04. Log export and saved views
- Original: L287–288 selects range/CSV/audit but always exports current DOM lines as JSONL; L317–318 saved views just toast text; L153 button.
- Proposed `POST /api/v1/system/logs/export` with filter+format and result `{downloadUrl,expiresAt,exportId}` or binary as negotiated; saved views `GET/POST/PATCH/DELETE /api/v1/users/me/log-views`.
- Implement: local export limited to currently loaded/filtered records; supports actual JSONL and CSV with proper escaping and format labeling; presets are UI-only, not falsely persisted.

## I05. Settings read/update/reset
- Original: L158 HTML selects; L192–193 defaults; L254–256 read/write/apply; L334–335 save/reset; L267 theme toggle.
- Proposed `GET/PATCH /api/v1/projects/{projectId}/settings` → `ProjectRuntimeSettings` (+ `revision` for optimistic concurrency), `POST /api/v1/projects/{projectId}/settings/reset` for reset; global UI preferences **local only** by default, optionally `/api/v1/users/me/preferences` when authorized backend exists.
- Implement: `SettingsRepository` mock stored by project ID; per-project runtime settings distinct from global appearance settings; preview/Save/Reset and cross-page theme effect; archived project cannot update runtime defaults in local demo.

## I06. Overview aggregated project summary
- Original: L84–107 fixed counters, stage banner, attention, role workload, queue ETAs, VRAM/budget/health. The figures do not change as the Phase 2–4 repositories change; original L268 milestone details are hard-coded.
- Proposed `GET /api/v1/projects/{projectId}/overview` → `ProjectOverviewSnapshot` as preferred production aggregate, or client-side read-only aggregation over typed repositories in this mock: Tasks, Agents, Approvals, Channels/Messages, Artifacts and Compute. Backend-sourced runtime/health fields nullable.
- Implement: React selectors cross-project scoped; status based on loaded data; stage events from verified stage-completion messages only; no invented timestamps, ETAs, verified proof claims or health status.

## I07. Overview actions / global controller leftovers
- Original: L245–253 hash nav/modal and L258–336 action dispatcher; L265 command palette, L266 health, L267 theme, L268 milestone; `data-page`/`data-action` markup throughout.
- Intent: navigation/theme/dialog are **UI operations not backend API**. Command palette uses React navigation; evidence/status actions route to existing repository-backed pages; sample scenario mutations MUST NOT silently modify backend.
- Implement global overlays in React in a later compatibility-removal segment; Phase 5 first migrates the four remaining pages and settings/theme linkage.
