#!/usr/bin/env bash
# Build the React frontend into frontend/dist.
# FRONTEND_BUILD_DIR lets the npm workspace live on another filesystem (useful where a shared filesystem has
# intermittent small-file write failures); only dist/ is copied back.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/frontend"
WORK="${FRONTEND_BUILD_DIR:-$SRC}"
if [ "$WORK" != "$SRC" ]; then
  mkdir -p "$WORK"
  rm -rf "$WORK/src"
  cp -r "$SRC/src" "$SRC/package.json" "$SRC/package-lock.json" "$SRC/index.html" "$SRC/vite.config.ts" "$SRC/tsconfig.json" "$WORK/"
fi
cd "$WORK"
[ -d node_modules ] || npm ci --no-audit --no-fund
npx tsc --noEmit
npx vite build
if [ "$WORK" != "$SRC" ]; then
  rm -rf "$SRC/dist"
  cp -r "$WORK/dist" "$SRC/dist"
fi
echo "built $SRC/dist"
