# MRLC Control Room — React + TypeScript + FastAPI (Phase 8.1)

A full-stack **single-operator MRLC foundation**. The React frontend renders the control room and sends typed HTTP requests. **The FastAPI backend owns business validation, durable state, permissions, lifecycle decisions, evidence checks, audit and model requests.** Offline browser Mock repositories exist only when explicitly selected for demos.

## Current capability

- **Frontend:** 12 React/TypeScript pages preserving the original Strata-inspired dark design; global shell, stage completion cards, Tasks/Workspace actions, optional mock mode.
- **Backend:** authenticated FastAPI, transactional SQLite, disk-backed artifact bytes + SHA-256/version history, task dependency and evidence workflow, approvals, project/role/Agent CRUD, project and UI settings, logs, SSE, logical inference jobs.
- **Inference:** optional OpenAI-compatible provider (`/v1/chat/completions`). Once a real provider is configured, Task Execute and explicit Agent Reply are real backend requests, not browser simulations.
- **Unknown metrics remain unknown:** no fictitious GPU statistics, model session checkpoints, token spend or runtime availability.

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

The model executor is deliberately **not** connected to any assumed CCR, A2CM or Strata port. Supply an endpoint that actually implements the authorized OpenAI-compatible Chat Completions protocol:

```bash
export MRLC_MODEL_BASE_URL='http://127.0.0.1:1234/v1'
export MRLC_MODEL_ID='EXACT_SUPPORTED_MODEL_ID'
export MRLC_MODEL_API_KEY='ONLY_IF_YOUR_ENDPOINT_REQUIRES_IT'
```

Without this configuration, task dispatch and explicit Agent replies return 503; they do not create fake output. See backend README for permission and evidence requirements.

## Structure

```text
backend/
  app/
    main.py                  app composition and asynchronous job worker
    storage.py               transactional SQLite and event/audit records
    security.py              authentication/session logic
    executor.py              real OpenAI-compatible HTTP transport
    common.py                validation, scope and state checks
    routes_organization.py   projects, roles, agents
    routes_workspace.py      channels, messages, tasks and acceptance
    routes_artifacts.py      byte storage, versions and evidence
    routes_approvals.py      revisioned decisions
    routes_runtime.py        jobs, dispatch, logical slots
    routes_system.py         overview, health, logs and settings
  tests/test_api.py           integration/authorization/persistence tests
  openapi.json                exported FastAPI HTTP route schema
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
npm run backend:test  # 15 Python integration tests
npm run test:all      # historical frontend domain/adapter tests + shim
npm run check:shim    # React shim guard
npm run build         # TypeScript + production Vite build
```

Backend integration tests, frontend regression suites, TypeScript typecheck and the production Vite build pass in the verified source tree. Browser E2E under a deployed HTTPS ingress remains unverified.

## Important scope limits

This is **not the full historical MRLC PRD** and not production ready. Remaining backend integrations include full CCR/A2CM scheduling and coordination, real provider checkpoint/restore, physical GPU control, model usage metering, distributed workers, multi-user RBAC and mature database migrations, backups and security review. Server-side approval history does not automatically authorize arbitrary external actions. See `BACKEND_STATUS.md`.

## Phase 8 backend authority

Phase 8 adds multi-user RBAC, project memberships, persistent agent sessions/context snapshots, scheduler cancel/retry/priority/lease metadata, typed approval effects, audit chain verification/signed bundles, provider usage accounting/budget guards, deterministic coordination, and server-generated reports. See `PHASE8_BACKEND.md` and `backend/PHASE8_API_ADDITIONS.md`.
