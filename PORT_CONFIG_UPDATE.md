# Unified port configuration update

Both development listeners now use `backend/.env` as their single source of truth.

```dotenv
MRLC_HOST=127.0.0.1
MRLC_PORT=8790
MRLC_DEV_HOST=0.0.0.0
MRLC_DEV_PORT=5176
MRLC_ALLOWED_ORIGIN=http://localhost:${MRLC_DEV_PORT}
```

- `MRLC_PORT`: FastAPI backend port.
- `MRLC_DEV_PORT`: Vite development frontend port.
- `MRLC_DEV_HOST`: Vite bind host.
- `strictPort: true`: Vite fails if the configured port is occupied instead of silently incrementing it.
- Vite reads `MRLC_HOST`/`MRLC_PORT` from the same file for `/api` proxying.
- Production does not run Vite; the built frontend is served by FastAPI on `MRLC_PORT`.

Existing `backend/.env` files are intentionally not overwritten. Add the new `MRLC_DEV_HOST` and `MRLC_DEV_PORT` settings manually when upgrading.
