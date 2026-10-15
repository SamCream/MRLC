#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-dev}"

usage() {
  cat <<'USAGE'
Usage: ./scripts/run.sh [dev|prod|backend]

  dev      Start FastAPI backend and Vite development frontend together.
  prod     Serve an existing dist/ build from FastAPI (single-origin mode).
  backend  Start only the FastAPI backend.
USAGE
}

case "${MODE}" in
  backend)
    exec "${ROOT_DIR}/scripts/run-backend.sh"
    ;;
  prod)
    if [[ ! -f "${ROOT_DIR}/package.json" ]]; then
      echo "Frontend files are not present in this backend-only package." >&2
      exit 2
    fi
    if [[ ! -f "${ROOT_DIR}/dist/index.html" ]]; then
      echo "Production frontend build not found at dist/index.html." >&2
      echo "Run 'npm run build' first." >&2
      exit 2
    fi
    export MRLC_STATIC_DIR_OVERRIDE="${MRLC_STATIC_DIR:-${ROOT_DIR}/dist}"
    exec "${ROOT_DIR}/scripts/run-backend.sh"
    ;;
  dev)
    if [[ ! -f "${ROOT_DIR}/package.json" ]]; then
      echo "Frontend files are not present in this backend-only package; use './scripts/run.sh backend'." >&2
      exit 2
    fi
    command -v npm >/dev/null 2>&1 || {
      echo "npm not found. Run ./scripts/init.sh after installing Node.js 20+." >&2
      exit 2
    }

    backend_pid=""
    frontend_pid=""
    cleanup() {
      if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" 2>/dev/null; then
        kill "${backend_pid}" 2>/dev/null || true
        wait "${backend_pid}" 2>/dev/null || true
      fi
      if [[ -n "${frontend_pid}" ]] && kill -0 "${frontend_pid}" 2>/dev/null; then
        kill "${frontend_pid}" 2>/dev/null || true
        wait "${frontend_pid}" 2>/dev/null || true
      fi
    }
    trap cleanup EXIT INT TERM

    # Development frontend is served by Vite. Force-disable FastAPI static
    # mounting even if backend/.env contains a production MRLC_STATIC_DIR.
    MRLC_DISABLE_STATIC=1 "${ROOT_DIR}/scripts/run-backend.sh" &
    backend_pid=$!

    cd "${ROOT_DIR}"
    npm run dev &
    frontend_pid=$!

    # Stop the stack if either side exits. This prevents a dead backend from
    # leaving a misleading frontend running (or vice versa).
    set +e
    wait -n "${backend_pid}" "${frontend_pid}"
    status=$?
    set -e
    if kill -0 "${frontend_pid}" 2>/dev/null; then
      kill "${frontend_pid}" 2>/dev/null || true
      wait "${frontend_pid}" 2>/dev/null || true
    fi
    cleanup
    exit "${status}"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
