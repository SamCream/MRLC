#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
tsc --strict --target ES2022 --module commonjs --moduleResolution node --lib ES2022,DOM --outDir "$build" \
  src/data/{repositories,remoteInvalidation,overviewRepository}.ts src/domain/models.ts
node tests/phase6.cjs "$build"
