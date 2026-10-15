#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
tsc --strict --target ES2022 --module commonjs --moduleResolution node --lib ES2022,DOM --outDir "$build" \
  src/domain/models.ts src/data/organizationRepository.ts src/data/workspaceRepository.ts \
  src/data/artifactRepository.ts src/data/approvalRepository.ts src/data/computeRepository.ts \
  src/api/client.ts src/api/services.ts src/data/httpPhase4Repositories.ts
node tests/phase4-http.cjs "$build"
