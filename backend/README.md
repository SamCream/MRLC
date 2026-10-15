# MRLC Backend v0.8.1 — running the real service

This is a **real HTTP backend**, not the old browser-only mock. It provides SQLite persistence, password sessions, project/role/agent/task/chat/artifact/approval management, business guards, an SSE event feed, and an optional OpenAI-compatible inference worker. It does **not** claim full historical MRLC/CCR/A2CM orchestration compatibility.

## 1. Initialize from the project root

Runtime scripts are centralized in the project-level `scripts/` directory. For a complete local setup:

```bash
./scripts/init.sh
nano backend/.env
```

The initializer creates `backend/.venv`, installs backend and frontend dependencies, creates `backend/.env` only when missing, and prepares `backend/var/artifacts`. It does not overwrite an existing environment file.

## 2. Launch the backend

From the project root:

```bash
./scripts/run-backend.sh
```

The listener is **MRLC_HOST:MRLC_PORT** from `backend/.env`. The example binds to loopback only. Credentials are not printed in the console. Do not expose the backend directly without HTTPS and a trusted reverse proxy.

`backend/run-local.sh` is retained only as a deprecated compatibility wrapper. New deployment units should call `scripts/run-backend.sh`.

## 3. Launch the full development stack or production build

Development:

```bash
./scripts/run.sh dev
```

Production, after building the React frontend:

```bash
npm run build
./scripts/run.sh prod
```

`prod` exports `MRLC_STATIC_DIR` to the built `dist/` directory and starts FastAPI as a single-origin application. All business operations still execute on the server.

## 4. Real model execution (optional)

```bash
export MRLC_MODEL_BASE_URL='http://127.0.0.1:1234/v1'
export MRLC_MODEL_ID='YOUR_EXACT_COMPATIBLE_MODEL_ID'
export MRLC_MODEL_API_KEY='PROVIDER_SECRET_IF_REQUIRED'
export MRLC_MAX_CONCURRENT_JOBS=2
```

Use the actual **OpenAI-compatible** `/v1/chat/completions` endpoint of a system you are permitted to access. Confirm URL, model ID and authorization manually. No specific CCR, Strata, or other runtime is assumed to support this contract.

Workflow: create project → active role template with permission `tasks.execute` → assign an idle agent of that role to the project → create task → press **执行任务（后端）** (or `POST /api/v1/tasks/{id}/dispatch`) → backend queues the job → model output stored as a real Markdown Artifact → task enters Review → attach evidence to each acceptance criterion and accept on the server.

For chat, assign a role with `messages.reply`; send a message, then explicitly choose **请求 Agent 回复（后端）**. Sending chat text alone does **not** secretly spend inference credits. Each agent request gets only its channel history and user-authorized evidence; global context is not crawled automatically.

The worker reports actual transport failure as Failed. Slots are **logical in-flight job leases**, not verified physical GPUs; pause/resume/release of provider computation is not advertised as supported. Agent Park blocks while an execution is queued or active because no model context checkpoint API is integrated.

## 5. Development and testing

```bash
cd backend
python -m pytest -q tests/test_api.py
```

`openapi.json` is exported directly from the live FastAPI route registry; see `BACKEND_API.md` in the project root for request/response examples. The test suite exercises **actual ASGI HTTP requests and SQLite persistence**, plus a loopback test HTTP model-provider endpoint; it does not invoke paid inference.

## Security and operational limitations

- Local RBAC with four built-in roles (administrator/operator/reviewer/viewer). This is not SSO, federation or a hardened team-access solution.
- Passwords and URL login tokens are independent credentials. `POST /api/v1/auth/login` accepts account passwords; `GET /api/v1/auth/token` accepts a dedicated token through `X-MRLC-Token`. Both establish the same signed HttpOnly session and therefore the same server-side RBAC authority. A password is never accepted as a token and a token is never accepted as a password.
- The configured administrator (MRLC_ADMIN_USERNAME, default `owner`) keeps both `MRLC_ADMIN_PASSWORD` and `MRLC_ADMIN_TOKEN` in the environment; neither is written to SQLite. Non-admin passwords use PBKDF2-SHA256. Non-admin login tokens are generated/replaced/revoked at `/api/v1/auth/me/token`; plaintext is returned only by the create/replace response and only a keyed digest is persisted.
- The browser supports `/?token=...`: `AuthGate` removes `token` from the visible URL before calling the token-login endpoint. This reduces browser-history/Referer exposure but cannot prevent the initial URL from appearing in an ingress/reverse-proxy access log. Use HTTPS and protect access logs.
- Sessions use signed HttpOnly cookies with a 12-hour lifetime. Mutations reject unapproved foreign browser origins.
- The SQLite schema uses initial `CREATE TABLE IF NOT EXISTS`; migrations, backup jobs, restore drills, retention policies and network HA are still necessary for production.
- Every accepted business mutation writes durable audit data and SSE events. Audit data is not cryptographically tamper-proof against administrators with DB access.
- Approval records do not automatically grant untyped arbitrary provider, filesystem or system privileges. Execution adapters must separately enforce their own policies.
- A configured provider is not automatically a healthy provider; no false GPU telemetry, uptime or token spend is generated.
- Context checkpoints, actual GPU scheduling, provider usage accounting and CCR/A2CM cross-repo integration are **not yet implemented**.

Development note: `../scripts/run.sh dev` sets `MRLC_DISABLE_STATIC=1`, so any production `MRLC_STATIC_DIR` in `.env` is ignored while Vite serves the frontend.
