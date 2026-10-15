#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
tsc --strict --target ES2022 --module commonjs --moduleResolution node --lib ES2022,DOM --outDir "$build" \
  src/domain/models.ts src/data/{organizationRepository,workspaceRepository,artifactRepository,approvalRepository,computeRepository,systemRepository,settingsRepository,overviewRepository,httpSystemRepository,logExport}.ts src/api/{client,services}.ts
node tests/phase5.cjs "$build"
node tests/phase5-http.cjs "$build"
node tests/phase5-export.cjs "$build"
