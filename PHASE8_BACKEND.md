# MRLC Phase 8 — backend authority expansion

Phase 8 continues the server-authoritative architecture. React is still an operator surface; business decisions remain in FastAPI/SQLite.

## Implemented

- Multi-user RBAC with PBKDF2 password hashes and signed HttpOnly sessions.
- Built-in roles: administrator, operator, reviewer, viewer; per-user allow/deny overrides.
- Project memberships with immediate revocation; project-scoped list filtering and server middleware authorization.
- Persistent AgentSession records. Park/resume modifies server session state; no provider checkpoint is claimed.
- Durable ContextSnapshot records attached to jobs.
- Scheduler priority, attempts, cancellation flags, leases, parent jobs, retry from an existing context snapshot.
- Retry explicitly returns `providerCheckpointRestored: false` unless a future runtime adapter implements it.
- OpenAI-compatible provider usage accounting. Token counts are recorded only when the provider reports them.
- Optional per-million-token prices and project monthly budget policies; dispatch is rejected by the server when a configured budget is exhausted.
- Typed approval effects executed atomically with the approval decision: grant role permission, archive role, set project budget, update project setting.
- Audit SHA-256 chain verification and HMAC-signed audit bundle export.
- Host memory/load sampling and `nvidia-smi` accelerator telemetry when available. Missing telemetry remains empty rather than synthetic.
- Deterministic Coordinator diagnostics from persisted dependencies/agents/approvals.
- Reporter generates a real Markdown Artifact and posts it to the Report Channel.

## Still adapter-dependent / not falsely implemented

- CCR/A2CM/Strata private lifecycle protocols.
- Provider KV-cache/checkpoint restore.
- Physical GPU pause/resume/release unless a runtime adapter exposes those operations.
- Distributed multi-node leases/consensus. SQLite is durable on one backend host, not a distributed database.
- Exact cost accounting when a provider omits token usage or prices are not configured.
- External identity providers/SSO.

These capabilities should be implemented as runtime/provider adapters without moving authority back into React.
