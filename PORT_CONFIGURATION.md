# MRLC port configuration

All local listener settings now have **one source of truth**: `backend/.env`.

```bash
cd backend
cp .env.example .env
nano .env
```

Configure both servers there:

```dotenv
# FastAPI
MRLC_HOST=127.0.0.1
MRLC_PORT=8790

# Vite development server
MRLC_DEV_HOST=0.0.0.0
MRLC_DEV_PORT=5176
```

`./scripts/run-backend.sh` loads `MRLC_HOST` / `MRLC_PORT`. `vite.config.ts` loads the same `backend/.env`, uses `MRLC_DEV_HOST` / `MRLC_DEV_PORT` for the development frontend, and proxies `/api` to the configured FastAPI address. No source edit is required when either port changes.

Vite uses `strictPort: true`. If `MRLC_DEV_PORT` is already occupied, startup fails with a port-in-use error; it will **not** silently increment from 5173 to 5174/5175/etc.

## Development origin / CSRF guard

The backend checks the browser `Origin` on mutations. The example config keeps localhost in sync automatically:

```dotenv
MRLC_ALLOWED_ORIGIN=http://localhost:${MRLC_DEV_PORT}
```

Because `backend/.env` is sourced by the launcher, changing `MRLC_DEV_PORT` also changes this localhost origin. If you open MRLC through a LAN address instead of localhost, set the exact browser origin, for example:

```dotenv
MRLC_ALLOWED_ORIGIN=http://192.168.50.2:${MRLC_DEV_PORT}
```

## Remote backend override

For development against a backend on another host, the frontend-only root `.env.local` may override only the proxy destination:

```dotenv
VITE_BACKEND_URL=http://192.168.1.10:9000
```

The Vite listener itself still comes from `backend/.env`.

## systemd

Use the same backend environment file and call `scripts/run-backend.sh`, so the backend port is not duplicated in the unit:

```ini
[Service]
WorkingDirectory=/opt/mrlc
EnvironmentFile=/opt/mrlc/backend/.env
ExecStart=/opt/mrlc/scripts/run-backend.sh
```

## Caddy

Caddy still needs an upstream address because it is a separate process. Prefer a Caddy environment placeholder instead of a literal port:

```caddyfile
mrlc.example.com {
    reverse_proxy {$MRLC_UPSTREAM}
}
```

Set `MRLC_UPSTREAM=127.0.0.1:8790` in Caddy's service environment.

## Static frontend mode

The Vite development port is used only by `./scripts/run.sh dev`. In development, the script forces `MRLC_DISABLE_STATIC=1`: Vite serves the frontend and FastAPI serves only `/api`.

In production:

```bash
npm run build
./scripts/run.sh prod
```

Vite is not running. FastAPI serves the built `dist/` files and `/api` on `MRLC_PORT`, so only the backend port matters internally.


## Reverse-proxy / custom hostname in development

Vite validates the HTTP `Host` header. Do not hard-code deployment domains in `vite.config.ts`. Put browser origins in `backend/.env`:

```dotenv
MRLC_ALLOWED_ORIGINS=http://localhost:${MRLC_DEV_PORT},http://192.168.50.2:${MRLC_DEV_PORT},https://mrlc.home.example.com
```

`vite.config.ts` extracts the hostnames from these origins and passes them to `server.allowedHosts`. Additional hostnames that are not browser origins can be supplied explicitly:

```dotenv
MRLC_DEV_ALLOWED_HOSTS=extra-proxy.example.com
```

`MRLC_ALLOWED_ORIGINS` controls FastAPI mutation-origin checks. `MRLC_DEV_ALLOWED_HOSTS` controls only Vite's development Host-header allowlist. Never use `allowedHosts: true` on an exposed development server.
