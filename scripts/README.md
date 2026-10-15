# MRLC operator scripts

All executable operator entry points live here.

- `./scripts/init.sh` — first-time local initialization: create `backend/.env` if absent, create the Python venv, install backend dependencies, install frontend dependencies when a frontend package is present, and prepare local storage directories.
- `./scripts/run.sh dev` — start backend + Vite development frontend; Ctrl+C stops both.
- `./scripts/run.sh prod` — require an existing `dist/` build and serve it from FastAPI.
- `./scripts/run.sh backend` — backend only.
- `./scripts/run-backend.sh` — direct backend entry point used by systemd and other process supervisors.

All local listener configuration belongs only in `backend/.env`:

```dotenv
MRLC_HOST=127.0.0.1
MRLC_PORT=<choose-a-free-backend-port>
MRLC_DEV_HOST=0.0.0.0
MRLC_DEV_PORT=<choose-a-free-vite-port>
```

Do not duplicate either port in scripts or service units. `vite.config.ts` reads the same backend environment file for both its own development listener and the `/api` proxy target. `strictPort` is enabled: if `MRLC_DEV_PORT` is occupied, startup fails instead of silently switching to another port.

### Development vs production static files

`./scripts/run.sh dev` always disables FastAPI static-file mounting, even if
`backend/.env` contains `MRLC_STATIC_DIR` for production. Vite serves the
frontend in development. `./scripts/run.sh prod` requires `dist/index.html`
and explicitly mounts the built `dist/` directory.


### Development access through a hostname / reverse proxy

Configure browser origins in `backend/.env`; Vite derives its allowed hostnames from them:

```dotenv
MRLC_ALLOWED_ORIGINS=http://localhost:${MRLC_DEV_PORT},https://mrlc.home.example.com
```

If a reverse-proxy hostname is not itself a browser origin, add it with `MRLC_DEV_ALLOWED_HOSTS=host1,host2`.
