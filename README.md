# MRLC Control Room — React + TypeScript + FastAPI + Migrated Infra Core

A full-stack MRLC control room. The React frontend renders the existing product UI and sends typed HTTP requests. **FastAPI remains the sole product HTTP entry point, while the recovered MRLC Infra/Runtime core now owns replayable domain events, scheduler leases, consistency primitives, Memory/Bank state and provider invocation records underneath the compatibility API.** Offline browser Mock repositories exist only when explicitly selected for demos.

## Current capability

- **Frontend:** unchanged by the Infra migration; the current React/TypeScript pages and `/api/v1` contracts continue to work through FastAPI.
- **Application backend:** multi-user RBAC, password/dedicated-token sessions, project/task/workspace/artifact/approval APIs, disk-backed SHA-256 artifacts, logs and SSE.
- **Infra core:** authoritative EventStore + replay, Project/Task/Session registries, consistency outbox/inbox/Saga, persistent Scheduler with lease/fencing, History, Memory/Retrieval, Project Bank, Continuity, MCP/Skills and recovery primitives.
- **Runtime/Gateway:** the existing FastAPI job worker now dispatches through the migrated Scheduler and uses the migrated CCR OpenAI-compatible provider adapter; invocation IDs/provider request IDs/usage are written to the Model Gateway ledger.
- **Unknown metrics remain unknown:** no fictitious physical GPU allocations, provider checkpoints or telemetry are invented.

## Local startup

All operator scripts live under `scripts/`. Backend and Vite development listener ports are configured only in `backend/.env`.

First-time initialization:

```bash
./scripts/init.sh
nano backend/.env
```

`init.sh` creates `backend/.env` if it is missing, creates `backend/.venv`, installs Python dependencies, installs frontend dependencies, and prepares the local Artifact directory. It never overwrites an existing `backend/.env`. Replace the example administrator password, administrator URL-login token, and session secret before starting the service. The administrator token is configured only as `MRLC_ADMIN_TOKEN` in `backend/.env`; do not reuse the password.

Start the complete development stack with one command:

```bash
./scripts/run.sh dev
```

This starts the FastAPI backend and the Vite development frontend together. FastAPI listens on `MRLC_HOST:MRLC_PORT`; Vite listens on `MRLC_DEV_HOST:MRLC_DEV_PORT`; all four values come from `backend/.env`. Vite proxies `/api` to the configured backend. `strictPort` is enabled, so an occupied frontend port causes startup to fail instead of silently selecting another port. Ctrl+C stops both processes.

Other launch modes:

```bash
./scripts/run.sh backend   # backend only
./scripts/run-backend.sh  # backend only, direct entry point
npm run build
./scripts/run.sh prod      # serve dist/ from FastAPI
```

The old `backend/run-local.sh` remains only as a deprecated compatibility wrapper and forwards to `scripts/run-backend.sh`. New service files and documentation should use the script under `scripts/`.

Open the Vite development URL using `MRLC_DEV_PORT` (for example `http://localhost:5173`) and sign in. The browser receives a signed HttpOnly cookie. The default is `VITE_DATA_SOURCE=http` and `VITE_API_BASE_URL=/`. The frontend does not silently fall back to Mock when the backend is unavailable.

To preview with no server, explicitly set `VITE_DATA_SOURCE=mock` (for example in `.env.local`) and restart Vite. This is only a demo and **does not execute tasks or model inference**.

For single-origin production hosting, build the frontend first and run `./scripts/run.sh prod`.


> In `./scripts/run.sh dev`, FastAPI static serving is forcibly disabled. A production `MRLC_STATIC_DIR` left in `backend/.env` therefore does not require `dist/` to exist during development.


### Password and URL-token login

MRLC supports two independent login credentials with the same resulting RBAC session authority:

- Account password: enter it on the login screen. The configured administrator password comes from `MRLC_ADMIN_PASSWORD`; other users have PBKDF2 password hashes in SQLite.
- Login token: open `/?token=<token>`. The frontend removes the query parameter immediately and exchanges the token for the normal signed HttpOnly session cookie. The configured administrator token comes from `MRLC_ADMIN_TOKEN`; non-admin users can generate, replace, or revoke their own token under **Settings → 账户登录 Token**. Only a keyed digest is stored for non-admin tokens and plaintext is returned only when a new token is created.

Treat URL tokens as secrets. HTTPS is required for remote use, and the initial page request containing `?token=` may still appear in reverse-proxy/access logs before the frontend removes it.

## Real provider configuration

The FastAPI executor now uses the migrated CCR OpenAI-compatible provider adapter, but endpoint selection remains explicit. Supply the authorized OpenAI-compatible endpoint you actually run:

```bash
export MRLC_MODEL_BASE_URL='http://127.0.0.1:1234/v1'
export MRLC_MODEL_ID='EXACT_SUPPORTED_MODEL_ID'
export MRLC_MODEL_API_KEY='ONLY_IF_YOUR_ENDPOINT_REQUIRES_IT'
```

Without this configuration, task dispatch and explicit Agent replies return 503; they do not create fake output. See backend README for permission and evidence requirements.

## Structure

```text
backend/
  app/                       FastAPI application/compatibility layer
  infra/                     migrated authoritative Infra core
  runtime/                   conversation/frontier/rollover runtime
  gateway/ccr/               provider client + budget/provider adapters
  adapters/                  Codex/DSH/Git worker adapters
  plugins/dev/profile/       migrated Dev runtime profile
  tests/                     FastAPI + migration + legacy-core regressions
  INFRA_MIGRATION.md         migration authority/boundary record
  openapi.json               exported FastAPI HTTP route schema
src/
  app/                       App, AuthGate and global shell
  pages/                     12 React/TSX pages
  api/                       typed service paths, HTTP transport, SSE
  data/                      API repositories and explicit offline Mock repos
  domain/                    request/response TypeScript interfaces
reference/                    original HTML and archived Phase 6 README
```

## Documentation and verification

- [`BACKEND_API.md`](BACKEND_API.md): implemented API contract, request bodies and errors.
- [`BACKEND_INTERFACE_LEDGER.md`](BACKEND_INTERFACE_LEDGER.md): old HTML code locations mapped to new backend modules.
- [`BACKEND_STATUS.md`](BACKEND_STATUS.md): precise remaining capabilities, **no claims of full PRD completion**.
- [`BACKEND_TEST_REPORT.md`](BACKEND_TEST_REPORT.md): executed tests, limitations and environment blockers.
- [`MIGRATION_PHASE7.md`](MIGRATION_PHASE7.md): changed files and migration record.
- [`API_CATALOG.md`](API_CATALOG.md) and `INTERFACE_DISCOVERY_PHASE*.md`: historical pre-backend interface discovery documents.

```bash
npm run backend:test  # FastAPI + migration + migrated core regressions
npm run test:all      # historical frontend domain/adapter tests + shim
npm run check:shim    # React shim guard
npm run build         # TypeScript + production Vite build
```

Backend integration tests, frontend regression suites, TypeScript typecheck and the production Vite build pass in the verified source tree. Browser E2E under a deployed HTTPS ingress remains unverified.

## Important scope limits

This is still **not production ready**. Mature legacy Infra/Runtime/Gateway code has been migrated, but the old Console server is intentionally not restored and Dev/A2CM/Frontier/Codex/DSH product controls are not yet activated through the current React UI. Provider-specific KV checkpoint/restore, physical GPU control, distributed leader election, a complete versioned migration framework, SSO/federation and production key management remain open. See `BACKEND_STATUS.md` and `backend/INFRA_MIGRATION.md`.

## Phase 8 backend authority

Phase 8 adds multi-user RBAC, project memberships, persistent agent sessions/context snapshots, scheduler cancel/retry/priority/lease metadata, typed approval effects, audit chain verification/signed bundles, provider usage accounting/budget guards, deterministic coordination, and server-generated reports. See `PHASE8_BACKEND.md` and `backend/PHASE8_API_ADDITIONS.md`.
