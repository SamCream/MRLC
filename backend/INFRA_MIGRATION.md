# Backend Infra Migration — Phase 8.1 → 0.9 Infra Core

This document records the backend-only migration from the Phase 8.1 FastAPI backend to the mature Infra/Runtime core recovered from `Samera2022/MRLC@feat/epic-0-infra-foundation`.

The existing Control Room pages remain unchanged. A separate **Infra Core console** now lives beside them and uses additive read-model routes; existing `/api/v1` product routes remain the compatibility/application API.

## Authority model

```text
React (unchanged)
        |
        v
FastAPI application / compatibility routes
        |
        +---- UI projection: objects + ui_events
        |
        v
Migrated Infra core
  projects / tasks / sessions
  authoritative domain events + replay
  consistency / outbox / inbox / saga
  scheduler / lease / fencing
  history / memory / project bank
  artifact/evidence domain facts
  model invocation ledger
        |
        +---- Runtime / Gateway / Worker adapters
```

The old Phase 8 `events` table conflicted with the authoritative legacy EventStore schema. On startup, an existing Phase 8 UI-event table is migrated to `ui_events`; the canonical `events` table is then owned by the migrated EventStore.

## Migrated and connected

- EventStore and deterministic replay.
- Project, Task and Session registries.
- Task dependencies and lifecycle projection.
- Consistency primitives: optimistic versions, outbox/inbox, leases/fencing, saga state and dead-letter metadata.
- Persistent Scheduler. Existing FastAPI dispatch now enqueues the migrated Scheduler; the worker claims through Scheduler dispatch and records lease events.
- Model Gateway ledger.
- Existing FastAPI OpenAI-compatible execution now uses the migrated CCR OpenAI-compatible provider adapter.
- History: new FastAPI channel messages are persisted into Infra history as well as the UI projection.
- Artifact/evidence: content remains on the newer disk-backed SHA-256 store; authoritative `ARTIFACT_RECORDED` / `EVIDENCE_LINKED` domain facts are emitted for replay/provenance.
- Project Bank: resolved FastAPI approvals are promoted to decisions; backend-only Issue/Decision/Lesson/Progress endpoints are available.
- Memory: evidence-backed, temporal, role-scoped storage and retrieval plus backend-only create/supersede endpoints.
- Continuity, MCP, Skills, Context projection/retrieval, WorkerRunStore, recovery backup primitives and audit/risk modules are migrated into `backend/infra`.
- Runtime modules (conversation, routing, rollover, frontier advisor/delegate) and Codex/DSH/Git adapters are present under `backend/runtime` and `backend/adapters`.
- Dev conversation baseline profile is migrated to `backend/plugins/dev/profile/conversation.json`.
- The stale repository-local DSH Harness fallback was removed: DSH delegation requires an explicit `MRLC_DELEGATE_DSH_BIN`.

## Deliberate compatibility choices

### Authentication

The newer FastAPI account/RBAC system is retained. It is safer than the old Console `ui-identities.json` plaintext-token map.

- administrator password: environment-backed;
- administrator URL token: environment-backed;
- non-admin login tokens: keyed digest only;
- browser receives the existing HttpOnly signed session.

### Artifacts

The Phase 8.1 content-addressed disk store is retained instead of regressing artifact bytes to SQLite BLOB storage. Infra receives replayable artifact/evidence facts and checksums.

### UI events vs domain events

- `ui_events`: compatibility SSE/revalidation stream used by the current React application.
- `events`: authoritative domain EventStore used for replay, provenance and Infra consumers.

They intentionally remain separate because they have different contracts.

## Additive Infra APIs

These routes support the separate Infra Core console without changing existing product-page contracts:

- `GET /api/v1/infra/status`
- `GET /api/v1/projects/{projectId}/infra/events`
- `GET /api/v1/projects/{projectId}/infra/replay`
- `GET /api/v1/projects/{projectId}/infra/overview`
- `GET /api/v1/projects/{projectId}/infra/scheduler`
- `GET /api/v1/projects/{projectId}/infra/context`
- `GET /api/v1/projects/{projectId}/infra/bank`
- `GET /api/v1/projects/{projectId}/infra/provenance`
- `GET /api/v1/projects/{projectId}/infra/invocations`
- `GET /api/v1/projects/{projectId}/infra/consistency`
- `GET /api/v1/projects/{projectId}/infra/operations`
- `GET/POST /api/v1/projects/{projectId}/infra/memory`
- `POST /api/v1/projects/{projectId}/infra/memory/{memoryId}/supersede`
- `GET/POST /api/v1/projects/{projectId}/infra/bank/issues`
- `POST /api/v1/projects/{projectId}/infra/bank/decisions`
- `POST /api/v1/projects/{projectId}/infra/bank/issues/{issueId}/resolve`
- `POST /api/v1/projects/{projectId}/infra/bank/lessons`
- `POST /api/v1/projects/{projectId}/infra/bank/progress`

Project-scoped writes pass through the existing FastAPI authorization middleware.

## Infra Console scope

The React application now has a dedicated Infra navigation domain with Overview, Events & Replay, Scheduler & Leases, Memory & Context, Provenance, Invocations, and Consistency & Recovery. These pages intentionally avoid duplicating Control Room task, chat, artifact, GPU and provider-health views.

The following mature legacy backend modules remain intentionally **not** wired into the current React interaction model:

- legacy Console command API and Console web server;
- Dev group-chat ConversationService as a replacement for the current channel UI;
- Frontier Delegate UI controls;
- direct Codex/DSH worker launch controls;
- A2CM product-specific views/workflows.

The code is kept as backend capability, but the new FastAPI remains the only product HTTP entry point. Future work should expose typed FastAPI routes after their UI/API contracts are chosen rather than reintroducing the old Console server.

## Verification

The backend-only GitHub Actions workflow runs:

```bash
python -m compileall -q app infra runtime gateway adapters
python -m pytest -q tests
```

The suite contains both the existing FastAPI integration tests and ported legacy core regressions for EventStore, EventedInfra, consistency, scheduler, replay, memory, retrieval, Project Bank, Model Gateway, provider transport, backup/restore and Evidence Graph.

The migration is not considered complete unless this workflow is green.
