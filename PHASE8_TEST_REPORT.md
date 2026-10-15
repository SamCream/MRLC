# Phase 8.1 test report

Verification after the independent URL-login-token patch:

- Backend FastAPI/SQLite integration suite: **15 passed**.
- New authentication regression covers password login, independent administrator token, non-admin token generation/replacement/revocation, token-hash-only persistence, and immediate invalidation of rotated/revoked tokens.
- Frontend/domain regression: **25 assertions passed**.
- HTTP adapter contract suite: **16 routes passed**, including login-token management service paths.
- Phase 4 regression: **34+ assertions passed**; Phase 4 HTTP contract test passed separately.
- Phase 5 repository/HTTP/export regression: passed.
- Phase 6 fail-closed HTTP/SSE runtime regression: passed.
- TypeScript typecheck and production Vite build: passed.
- OpenAPI regenerated from the live FastAPI route registry.

Security packaging audit: runtime `backend/.env`, `backend/.venv`, root `node_modules`, `dist`, caches, SQLite state and `*.tsbuildinfo` are excluded from the Git commit.
