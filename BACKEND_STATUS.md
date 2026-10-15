# MRLC Backend Status — 0.9.1 Infra Runtime Integration (2026-10-15)

The current backend keeps the existing Control Room React/FastAPI contract while running the mature legacy MRLC Infra/Runtime implementation underneath it. A separate Infra Core console has now been added without changing the existing Control Room pages.

## Implemented and connected

1. **FastAPI application layer** — current authenticated `/api/v1` routes, multi-user RBAC, project membership, URL-token/password sessions, origin guards and HttpOnly cookies remain intact.
2. **Shared durable database** — the FastAPI compatibility projection and migrated Infra core use the same SQLite database.
3. **Authoritative domain EventStore** — project-local sequence, idempotency keys, actor/session/task/correlation/causation metadata, subscriptions and deterministic replay.
4. **Compatibility event migration** — the old Phase 8 UI/SSE `events` table upgrades in-place to `ui_events`; `events` is now reserved for authoritative Infra domain events.
5. **Projects / Tasks / Sessions** — current FastAPI mutations are synchronized transactionally into the migrated registries. Task dependencies and accepted/completed evidence are replayable.
6. **Consistency primitives** — CAS aggregates, transactional outbox/inbox, lease fencing, sagas, retry/dead-letter metadata and crash-recovery primitives are migrated.
7. **Scheduler** — current task/chat jobs are admitted into the migrated persistent Scheduler. Worker selection uses Scheduler dispatch; lease acquisition/release is recorded as domain facts and reconciled after restart.
8. **Provider layer** — FastAPI model execution uses the migrated CCR OpenAI-compatible Provider adapter. Successful calls are recorded in the migrated Model Gateway ledger with provider request IDs and measured usage when reported.
9. **History** — new channel messages are written to the Infra History store while the current UI message projection remains unchanged.
10. **Memory / runtime context** — evidence-backed, role/project-scoped, temporal Memory and deterministic retrieval are migrated **and consumed by real task/chat provider calls**. Every assembly persists included/excluded sources, token budget and a `CONTEXT_ASSEMBLED` domain event; ordinary chat is still not silently promoted into long-term memory.
11. **Project Bank** — ISS/DEC/LES/PRG storage is migrated. Resolved current FastAPI approvals become Bank decisions; backend-only Bank write routes are available.
12. **Artifacts / evidence** — the newer disk-backed SHA-256 artifact byte store is retained, while Infra receives replayable `ARTIFACT_RECORDED` / `EVIDENCE_LINKED` facts.
13. **Runtime / adapters** — legacy Conversation, routing, rollover, Frontier advisor/delegate, Codex, DSH, Git worktree and WorkerRunStore implementations are present. Frontier **CONSULT** is exposed through the current FastAPI/Infra UI only when an authorized CCR advisor is configured. Frontier **DELEGATE** is capability-gated and disabled by default; when enabled it shares the authoritative WorkerRunStore, uses a hard CCR budget meter, isolated worktrees, independent acceptance commands and Parent Review before merge. Paid/externally executing submissions are operation-id guarded.
14. **A2CM** — evidence-backed observations can enter the candidate layer only with a project-local Domain Event reference; a dedicated ACTIVE `a2cm.primary` session performs policy-versioned PROMOTE/REJECT/DEFER review with counterevidence and uncertainty retained. Candidate state is replayable.
15. **Workspace routing / readiness** — user-message `responseMode` is durable. Channel replies can omit `agentId`; the backend owns @mention/responsibility routing. Multi-role replies are persisted as response groups and execute serially through Scheduler. Each real provider call records evidence-derived `KNOWN/PARTIAL/UNKNOWN` readiness and applies a corresponding no-context-crawl/no-unsupported-claim system constraint.
16. **Recovery / security primitives** — managed database/artifact backups, SHA-256 manifests, persistent backup-run registry and isolated restore verification drills are connected to FastAPI/Infra UI. Continuity checkpoints, MCP/Skill ACL registries, risk policy/audit, credential-file validation and secret redaction remain connected/migrated.

See `backend/INFRA_MIGRATION.md` for the authority boundaries and compatibility decisions.

## Deliberately retained from the newer backend

- The new RBAC/account/token implementation. The legacy Console plaintext identity-token file is not restored.
- The disk-backed content-addressed Artifact store rather than storing artifact bytes as SQLite BLOBs.
- The current FastAPI server as the only product HTTP entry point.
- Existing Control Room React/API contracts and UI behavior; Infra Core is a separate navigation domain.

## Migrated but not activated as Control Room workflows

These capabilities exist in backend code but are not silently substituted for current UI workflows:

- legacy Dev group-chat ConversationService remains separate from the current typed Workspace flow;
- raw direct Codex/DSH process-launch controls are intentionally not exposed separately; Codex/DSH execute through governed Frontier DELEGATE;
- legacy Console command/web server.

They should be exposed through typed FastAPI endpoints when their product/API contract is selected. Re-starting the old Console server would create a second control plane and is intentionally avoided.

## Still not production-complete

- Database upgrades remain additive/in-place rather than a complete cross-module versioned migration framework.
- Provider-specific model KV checkpoint/restore and generic pause/resume are not claimed.
- Physical GPU lease/control is not claimed; scheduler leases are logical runtime leases.
- Distributed multi-node leader election and external transactional database support are not implemented.
- SSO/federation, encryption-at-rest and production key-management integration remain outside the current scope.
- Real-browser E2E is part of the Infra CI gate (Chromium + Vite + FastAPI) and covers login, SSE invalidation, navigation, replay diff and recovery drill; deployed reverse-proxy/HTTPS behavior remains environment-specific.

## Verification

The migration branch has a backend-only GitHub Actions gate that compiles `app/infra/runtime/gateway/adapters` and runs the complete backend test tree. The suite includes the original FastAPI integration tests, migration-specific tests and ported legacy Infra core regressions.

A green backend workflow is required before merge.
