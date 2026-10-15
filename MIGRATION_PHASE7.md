# MRLC Phase 7 Migration Record — Real Backend

Source: Phase 6 complete React + TypeScript project (`src/`, `reference/`, `API_CATALOG.md`). Nothing in old HTML was removed from `reference/`.

New modules:

- `backend/app/storage.py`: persistent record store, append-only event/audit tables, durable job table, artifact version mapping.
- `backend/app/security.py`: operator login, authenticated session cookies, optional backend service token.
- `backend/app/executor.py`: OpenAI-compatible network executor.
- `backend/app/main.py`: process startup/stop, middleware, router composition, async worker.
- `backend/app/routes_organization.py`, `routes_workspace.py`, `routes_artifacts.py`, `routes_approvals.py`, `routes_runtime.py`, `routes_system.py`: server-owned business endpoints.
- `backend/tests/test_api.py`: actual ASGI/SQLite integration and model transport tests.
- `backend/openapi.json`, `BACKEND_API.md`, `BACKEND_INTERFACE_LEDGER.md`: route schemas and original-code traceability.
- `src/app/AuthGate.tsx`: session UX; `src/pages/{Tasks,Workspace}Page.tsx`: thin backend actions; `src/data/overviewRepository.ts`: server-authoritative summary; existing repositories now point at the real backend by default.

Verification on this run: 10 Python integration tests passed; original frontend phases 1–6 domain/HTTP tests passed; TypeScript shim check passed; Uvicorn was started in a separate process and served real HTTP login/create-project/overview/OpenAPI requests. Full npm/Vite build and real-browser UI tests remain unverified; no real CCR/Strata endpoint was configured.

Interface workflow: identify in `reference/index.original.html` and `src/api/services.ts`; implement server route and adapter; document input, response and error conditions in `BACKEND_API.md`; retain the original source→route map in `BACKEND_INTERFACE_LEDGER.md`.
