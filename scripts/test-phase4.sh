#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
tsc --strict --target ES2022 --module commonjs --moduleResolution node --lib ES2022,DOM --outDir "$build" \
  src/domain/models.ts src/data/organizationRepository.ts src/data/workspaceRepository.ts \
  src/data/artifactRepository.ts src/data/approvalRepository.ts src/data/computeRepository.ts
node tests/phase4.cjs "$build"
