#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# StrictNullChecks checks TypeScript across React pages against an intentionally permissive
# stub. It cannot validate actual JSX/React DOM types. `npm run build` is the real gate.
tsc --noEmit --strictNullChecks --strictFunctionTypes --noImplicitAny false --skipLibCheck --target ES2022 --module esnext \
  --moduleResolution bundler --jsx react-jsx --lib ES2022,DOM,DOM.Iterable \
  tests/react-type-shim.d.ts src/main.tsx src/app/*.tsx src/pages/*.tsx src/hooks/*.ts
