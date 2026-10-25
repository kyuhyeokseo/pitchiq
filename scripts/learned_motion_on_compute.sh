#!/usr/bin/env bash
# Learned movement model: parse IDSSE (if not done) and train + evaluate, on a compute node (never the login node).
#   srun -A <acct> -p <partition> [--gpus-per-node=1] -t 01:00:00 -c 8 --mem=32G scripts/learned_motion_on_compute.sh <run name>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="${PITCHIQ_EXTERNAL:?set PITCHIQ_EXTERNAL to a data/run directory outside the repository}"
PY="${PITCHIQ_ML_PY:-python3}"
RUN="${1:-motion_v0}"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "no GPU"
if [ ! -f "$EXT/idsse/processed/audit.json" ]; then
  "$PY" -m app.simulator.learned.idsse --raw "$EXT/idsse/raw" --out "$EXT/idsse/processed" --jobs 7
fi
"$PY" -m app.simulator.learned.train --processed "$EXT/idsse/processed" --out "$EXT/models/$RUN" "${@:2}"
