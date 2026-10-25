#!/usr/bin/env bash
# One-time environment for a fresh cluster workspace (installs only; fine on a login node).
#   PYBIN=<python 3.11+> PITCHIQ_TOOLS=<dir outside the repo> scripts/setup_cluster_env.sh
# Creates .venv (pinned app deps, dev tools, the Foundry extra and CPU PyTorch for the learned movement model),
# Node 20 for the frontend build and Chromium for the walkthrough. Prints the exports to add to your shell.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="${PITCHIQ_TOOLS:-$(dirname "$ROOT")/pitchiq-tools}"
PYBIN="${PYBIN:-python3.12}"
"$PYBIN" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' || { echo "need Python 3.11+ (set PYBIN)"; exit 1; }
mkdir -p "$TOOLS"
"$PYBIN" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip
"$ROOT/.venv/bin/python" -m pip install --quiet -e "$ROOT[dev,foundry]"
"$ROOT/.venv/bin/python" -m pip install --quiet torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
[ -d "$TOOLS/node-v20.18.1-linux-x64" ] || curl -sL https://nodejs.org/dist/v20.18.1/node-v20.18.1-linux-x64.tar.xz | tar xJ -C "$TOOLS"
PLAYWRIGHT_BROWSERS_PATH="$TOOLS/ms-playwright" "$ROOT/.venv/bin/python" -m playwright install chromium
cat <<EOF
done. Add to your shell (e.g. ~/.bashrc):
  export PATH=$TOOLS/node-v20.18.1-linux-x64/bin:\$PATH
  export PLAYWRIGHT_BROWSERS_PATH=$TOOLS/ms-playwright
  export PITCHIQ_ROOT=$ROOT PITCHIQ_EXTERNAL=$(dirname "$ROOT")/$(basename "$ROOT")-external PITCHIQ_ML_PY=$ROOT/.venv/bin/python
EOF
