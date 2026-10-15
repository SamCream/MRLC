#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() { printf '[MRLC init] %s\n' "$*"; }
die() { printf '[MRLC init] ERROR: %s\n' "$*" >&2; exit 1; }

command -v "${PYTHON_BIN}" >/dev/null 2>&1 || die "Python not found: ${PYTHON_BIN}"

if [[ ! -f "${BACKEND_DIR}/.env" ]]; then
  cp "${BACKEND_DIR}/.env.example" "${BACKEND_DIR}/.env"
  log "Created backend/.env from backend/.env.example"
  log "IMPORTANT: edit backend/.env and replace the example password/secret before running MRLC."
else
  log "backend/.env already exists; leaving it unchanged."
fi

mkdir -p "${BACKEND_DIR}/var/artifacts"

if [[ ! -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
  log "Creating Python virtual environment at backend/.venv"
  "${PYTHON_BIN}" -m venv "${BACKEND_DIR}/.venv"
else
  log "Python virtual environment already exists."
fi

log "Installing backend dependencies"
"${BACKEND_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${BACKEND_DIR}/.venv/bin/python" -m pip install -r "${BACKEND_DIR}/requirements-dev.txt"

if [[ -f "${ROOT_DIR}/package.json" ]]; then
  command -v npm >/dev/null 2>&1 || die "npm not found. Install Node.js 20+ and npm first."
  log "Installing frontend dependencies"
  cd "${ROOT_DIR}"
  npm install
else
  log "No package.json found; skipping frontend dependency installation (backend-only package)."
fi

log "Initialization complete."
if [[ -f "${ROOT_DIR}/package.json" ]]; then
  log "Next: edit backend/.env, then run ./scripts/run.sh dev"
else
  log "Next: edit backend/.env, then run ./scripts/run-backend.sh"
fi
