# MRLC Backend Status — Phase 7 (2026-10-14)

**Changed from Phase 6:** The repository now contains a real `backend/` service. The frontend defaults to `http` mode and communicates through same-origin `/api`. There is no business-logic-only browser substitute for real deployments.

## Implemented and tested

1. **Backend core** — FastAPI server, SQLite WAL persistence, artifact content-addressed files, signed HttpOnly operator sessions, disallowed cross-origin mutation, per-mutation audit records and replayable SSE events.
2. **Projects, role templates and Agent instances** — Real create/edit/archive/assignment, role permission checks and lifecycle guards. Park is rejected on active/queued execution without a checkpoint adapter.
3. **Tasks** — project-scoped tasks with dependency acyclicity checks, criteria evidence linking, controlled state transitions, server verification, recorded acceptance and milestone event generation.
4. **Workspace** — channel/message persistence, attachment project validation and explicit authorized model response requests.
5. **Artifacts** — actual uploaded file content, SHA-256, version history, task association, byte download, project-scope rules.
6. **Approvals** — durable decision history, optimistic revision conflict detection and structured reduced scope.
7. **Execution** — backend task queue and bounded worker with a *real* OpenAI-compatible HTTP call adapter; job status/outputs persisted, failures visible. Logical slots and sessions reflect jobs actually running, not fake GPU allocations.
8. **System** — server-derived Overview, project runtime settings and appearance persistence, logged audit reads/exports, topology/health with missing metrics represented truthfully.
9. **Frontend linkage** — AuthGate, Vite proxy, API adapters and SSE revalidation. Backend-only Task Execute, evidence verify, Accept and explicit Agent reply buttons.

## Not implemented (do not label complete)

- Actual MRLC/CCR/A2CM backend repository integration: source/configuration of those systems was **not provided** in this task. The model adapter works only with a confirmed OpenAI-compatible endpoint.
- Full historical MRLC PRD implementation (Coordinator/Reporter orchestration, knowledge projections beyond explicit evidence, research/code-development workflow plugins, autonomous typed approval effects, run recovery and complex scheduling policies).
- Real model context snapshot/restore, provider-specific Park/Resume, running-job preemption or physical GPU lease controller. Unsupported runtime actions return errors.
- Authentic GPU/NVML metrics, context prefill/decode rates, provider budget metering, model billing reconciliation.
- Multi-user RBAC, SSO, encryption-at-rest, strong cryptographic audit integrity, migration versioning, backup and recovery automation, distributed worker/leader election.
- Production React/Vite build and browser UI E2E verification on this machine: npm dependencies could not be installed from the registry in the prior frontend phases. The frontend static TypeScript shim, historical contract suite, backend tests and real HTTP startup have been tested separately.

**Interpretation:** The service is a working single-operator backend foundation and can run actual model jobs when you configure a compatible provider. It is **not production-ready** and is not a drop-in implementation of every MRLC research orchestration requirement.
