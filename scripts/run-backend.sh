#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
ENV_FILE="${MRLC_ENV_FILE:-${BACKEND_DIR}/.env}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
else
  echo "MRLC backend environment file not found: ${ENV_FILE}" >&2
  echo "Run ./scripts/init.sh or copy backend/.env.example to backend/.env first." >&2
  exit 2
fi

: "${MRLC_ADMIN_PASSWORD:?Set MRLC_ADMIN_PASSWORD (at least 12 characters) in backend/.env or the environment}"
: "${MRLC_SESSION_SECRET:?Set MRLC_SESSION_SECRET (at least 32 characters) in backend/.env or the environment}"
: "${MRLC_HOST:?Set MRLC_HOST in backend/.env or the environment}"
: "${MRLC_PORT:?Set MRLC_PORT in backend/.env or the environment}"

if (( ${#MRLC_ADMIN_PASSWORD} < 12 )); then
  echo "MRLC_ADMIN_PASSWORD must be at least 12 characters." >&2
  exit 2
fi
if (( ${#MRLC_SESSION_SECRET} < 32 )); then
  echo "MRLC_SESSION_SECRET must be at least 32 characters." >&2
  exit 2
fi
if [[ "${MRLC_ADMIN_PASSWORD}" == change-me-* || "${MRLC_SESSION_SECRET}" == change-me-* ]]; then
  echo "Replace the example MRLC password/session secret in backend/.env before starting." >&2
  exit 2
fi

# Runtime mode has the final say over static frontend serving. The environment
# file may contain a production MRLC_STATIC_DIR, but development mode passes
# MRLC_DISABLE_STATIC=1 so FastAPI does not require dist/ to exist.
if [[ "${MRLC_DISABLE_STATIC:-0}" == "1" ]]; then
  unset MRLC_STATIC_DIR
elif [[ -n "${MRLC_STATIC_DIR_OVERRIDE:-}" ]]; then
  export MRLC_STATIC_DIR="${MRLC_STATIC_DIR_OVERRIDE}"
fi

if ! [[ "${MRLC_PORT}" =~ ^[0-9]+$ ]] || (( MRLC_PORT < 1 || MRLC_PORT > 65535 )); then
  echo "MRLC_PORT must be an integer from 1 to 65535; got: ${MRLC_PORT}" >&2
  exit 2
fi

if [[ -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${BACKEND_DIR}/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || {
    echo "Python not found: ${PYTHON_BIN}. Run ./scripts/init.sh first." >&2
    exit 2
  }
fi

cd "${BACKEND_DIR}"
exec "${PYTHON_BIN}" -m uvicorn app.main:app --host "${MRLC_HOST}" --port "${MRLC_PORT}"
