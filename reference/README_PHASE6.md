# MRLC Control Room — React + TypeScript (Phase 6)

The original self-contained HTML control-room prototype has been migrated into a **Vite + React + TypeScript** frontend. The twelve pages and global navigation/overlays are React-rendered; the old imperative DOM controller is **not loaded**. The Strata-inspired dark UI remains in `src/styles/original.css`.

**Default mode is an offline local demo; Phase 6 adds explicitly opt-in experimental HTTP mode, but it has NOT been connected to a real MRLC backend.** Endpoint paths and payloads in `API_CATALOG.md` are typed proposals until they are reconciled with an actual backend. Slot actions modify simulated state only, stage-complete fixtures do not prove backend verification, no real GPU statistics, cost, monitoring health, or server audit log is fabricated.

## Start locally (when npm Registry is available)

```bash
npm install
npm run dev
```

Open `http://localhost:5173`. Build after dependencies install:

```bash
npm run build
npm run preview
```

**Experimental HTTP mode:** copy `.env.example` to `.env.local`, set `VITE_DATA_SOURCE=http` and a real `VITE_API_BASE_URL`, restart Vite. All React views then use the HTTP repositories, without silent fallback. This does **not** build or start a backend; API contracts remain provisional. Same-origin session cookies require a suitable reverse proxy; cross-origin deployments require explicit CORS/auth design. `VITE_*` variables are public, so never place tokens here.

`npm run build` is the **required real TS/Vite check** and browser E2E is required before production readiness. During this phase, `npm install` failed with DNS `EAI_AGAIN`, so no official build or screenshot/E2E success is claimed.

## Test offline (no node_modules required)

```bash
npm run test:all
```

This compiles business repositories and mocked HTTP adapters using the installed TypeScript CLI, then runs the earlier Phase 3/4 data + HTTP contract tests and new Phase 5 and Phase 6 tests. `check:shim` checks TSX against a **minimal permissive React declaration shim** (not actual `@types/react`); passing it does not prove a full frontend build. 12 page components and shell TSX transpilation also passed syntax checks during development.

## Features & capabilities

| Area | Current frontend behavior | Backend status |
|---|---|---|
| Overview | Dynamic local task, Agent, approval, queue and simulated slot aggregates; scoped stage messages | Candidate `/projects/{id}/overview`; never fake health/budget |
| Workspace | Project-scoped channels/messages/stage banners, true file upload + attachment reference in composer | Default mock persistence; experimental HTTP adapter; real model dispatch not connected |
| Tasks | Task creation, filters, dependencies, acceptance criteria, review activity | Mock persistence; provider orchestration not connected |
| Projects | Create, edit, archive (non-destructive), switch projects | Mock persistence |
| Agents & Roles | Distinct instance/template models, park/resume, role lifecycle | Mock-only scheduling |
| Artifacts | File/Blob bytes + versions, task links, download | Local IndexedDB by default; HTTP candidate when explicitly configured |
| Approvals | Revision-aware decisions, reasons, preserved history | Browser mock; no server authorization/audit |
| Compute | Slot transitions and queue simulation; unavailable telemetry correctly marked | No GPU/backend actions |
| Infrastructure | Typed health/topology/node/GPU views and health-check affordance | Unconnected yields `unknown`/empty, not fake Healthy |
| Logs | Locally recorded task activity; search/filter/page; saved local views; CSV & JSONL export | No live event stream or complete audit system |
| Settings | Project-specific runtime defaults with revision conflict and global persisted appearance | Browser local, server contracts proposed |
| Shell | React navigation, Ctrl+K command palette, health drawer, theme toggle, mobile menu | Legacy script removed |

## Repository structure

```text
src/
  app/         App, AppContext, global appearance
  pages/       12 TSX pages
  data/        Typed repositories + explicit source selector, mock and HTTP adapters, remote invalidation
  domain/      Typed entities and response contracts
  api/         HTTP client, route services, SSE candidate
  hooks/       Observable repository query helper
  styles/      Preserved original visual system
reference/
  index.original.html              original HTML source, not loaded
  legacy-controller.original.js    former script, archived and not served
scripts/, tests/                   offline Node/compiler regression tests
INTERFACE_DISCOVERY_PHASE4.md       discovery-first phase 4 API inventory
INTERFACE_DISCOVERY_PHASE5.md       discovery-first phase 5 API inventory
INTERFACE_DISCOVERY_PHASE6.md       discovery-first phase 6 integration and attachments inventory
BACKEND_STATUS.md                  explicit remaining backend requirements
API_CATALOG.md                     source → route → input/output documentation
MIGRATION_STATUS.md                history and remaining risks
```

## Important engineering constraints

- `localStorage` and local IndexedDB are **demo persistence**, not an authenticated durable multi-user database. IndexedDB reload/transaction/quota checks have not passed in an actual browser in this environment.
- Remote adapters are **inactive by default**. Enabling `VITE_DATA_SOURCE=http` performs real requests to a manually configured service; missing/failed endpoints do not fall back to mock. Optional SSE invalidation and local post-mutation refresh exist, but authenticated event replay is not proven. A REST endpoint description is not evidence that the backend exists.
- Before production: set up provider authorization/permissions, per-project tenant scoping, task/approval immutable audit, idempotency and revision handling, runtime lease ownership, file integrity/storage verification, reconnection+SSE, real schema validation, and robust error states.
- Native command palette navigates pages; richer global commands such as create-task are available via the respective page. Previous source prototypes with static/pseudo-actions are kept solely as references.
- Interface and parameter mapping is in `API_CATALOG.md`; implementation ordering is recorded in `INTERFACE_DISCOVERY_PHASE4.md`, `INTERFACE_DISCOVERY_PHASE5.md`, and `INTERFACE_DISCOVERY_PHASE6.md`. See `BACKEND_STATUS.md` before claiming the backend is integrated.

---

## Phase 7 update — real backend

This project now has a real Python/FastAPI + SQLite backend. **The default frontend configuration is HTTP**, not a Mock; a failed backend call will not silently display demo results. Refer to [backend/README.md](backend/README.md) for installation and running, [BACKEND_API.md](BACKEND_API.md) for the executable API contract, and [BACKEND_STATUS.md](BACKEND_STATUS.md) for precise supported/unsupported capabilities.

The original HTML prototype remains in `reference/`; browser-only Mock repositories are kept exclusively for explicit offline demo mode. Single-operator authentication and backend task/evidence/approval controls are in place; this is not yet the entire historical MRLC orchestration platform.
