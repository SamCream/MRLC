# Backend Verification Record (0.9.1 Infra Runtime Integration)

Executed against the submitted source tree:

- Backend CI runs `python -m compileall -q app infra runtime gateway adapters` plus the complete `python -m pytest -q tests` suite, including migrated legacy-core regressions and Infra runtime integration tests.
- The suite uses real FastAPI ASGI requests and SQLite transactions; provider transport is exercised against a controlled loopback HTTP endpoint and does not invoke paid inference.
- Authentication coverage now includes independent password/token credentials, environment-backed administrator token, non-admin self-service token generation/replacement/revocation, RBAC/session equivalence after login, and no plaintext token persistence.
- Frontend CI runs TypeScript check, historical domain/HTTP adapter suites and a production Vite build. The typed Infra HTTP contract covers replay compare, A2CM, Frontier CONSULT/DELEGATE/cancel/review and managed recovery routes.
- A separate browser gate starts the real FastAPI backend and Vite frontend and drives Chromium through login, project creation, SSE-driven refresh, Control Room ⇄ Infra navigation, point-in-time replay diff, backup creation and isolated restore verification.

Remaining limitations are architectural/external integration items (provider KV checkpoints, physical GPU control, consolidation of legacy module-local Infra schemas into the central migration ledger, distributed scheduling/consensus and production identity/key-management). The legacy Dev ConversationService is intentionally not a productization target because the current Workspace path supersedes it. Raw independent Codex/DSH process launch is intentionally not a separate product path; the adapters execute through governed DELEGATE. These limits are not mocked as complete.
