#!/usr/bin/env bash
# Whole learned-motion pipeline as one batch job (independent of any terminal session):
# parse + audit IDSSE -> train -> release package -> rollout videos -> rule vs hybrid review. Compute node only.
#   sbatch -A <acct> -p <partition> [--gpus-per-node=1] -t 01:00:00 -c 32 --mem=64G scripts/learned_motion_pipeline.sh
set -euo pipefail
# Set PITCHIQ_EXTERNAL (data/runs, outside the repo), and optionally PITCHIQ_ROOT (default: this checkout) and
# PITCHIQ_ML_PY (a Python with torch, numpy, pydantic, pyyaml, scipy, matplotlib). Missing raw data is fetched.
# PITCHIQ_MAX_MINUTES caps training (default 32; raise it on small CPU allocations). PITCHIQ_MOTION_VERSION (default
# v0) names the run, models/motion_gnn_<version> and docs/diagnostics/motion_gnn_<version>. PITCHIQ_TRAIN_ARGS adds
# train.py options, e.g. "--epochs1 40 --epochs2 30" so both stages end by early stopping, not by an epoch count.
ROOT="${PITCHIQ_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
EXT="${PITCHIQ_EXTERNAL:?set PITCHIQ_EXTERNAL to a data/run directory outside the repository}"
PY="${PITCHIQ_ML_PY:-python3}"
mkdir -p "$EXT"
VER="${PITCHIQ_MOTION_VERSION:-v0}"
RUN=motion_$VER
# only one copy runs: a later job exits while the lock holder is still queued/running or the run is done, and takes
# over a lock left by a job that died (e.g. preempted)
LOCK="$EXT/$RUN.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  holder=$(cat "$LOCK/job" 2>/dev/null || true)
  if [ -f "$EXT/$RUN.done" ] || { [ -n "$holder" ] && squeue -h -j "$holder" -t R 2>/dev/null | grep -q .; }; then
    echo "run done or held by running job $holder - exiting"; exit 0
  fi
  echo "stale lock from job $holder - taking over"
fi
echo "${SLURM_JOB_ID:-?}" > "$LOCK/job"
echo "job ${SLURM_JOB_ID:-?} on $(hostname) at $(date)"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "no GPU"
if [ ! -f "$EXT/idsse/processed/audit.json" ]; then
  FETCH=""; [ -f "$EXT/idsse/raw/MD5_VERIFIED.txt" ] || FETCH="--fetch"
  "$PY" -m app.simulator.learned.idsse $FETCH --raw "$EXT/idsse/raw" --out "$EXT/idsse/processed" --jobs 7
fi
"$PY" -m app.simulator.learned.train --processed "$EXT/idsse/processed" --out "$EXT/models/$RUN" \
  --max-minutes "${PITCHIQ_MAX_MINUTES:-32}" --roll-stride 4 ${PITCHIQ_TRAIN_ARGS:-}
"$PY" -m app.simulator.learned.release --run "$EXT/models/$RUN" --processed "$EXT/idsse/processed" \
  --raw "$EXT/idsse/raw" --out "$ROOT/models/motion_gnn_$VER"
cd "$ROOT/evaluation/diagnostics"
PYTHONPATH="$ROOT/backend" "$PY" render.py rollouts "$EXT/models/$RUN/rollouts_test.npz" \
  --out "$ROOT/docs/diagnostics/motion_gnn_$VER/rollouts" --n 4
PITCHIQ_MOTION_CHECKPOINT="$ROOT/models/motion_gnn_$VER/motion_gnn.pt" \
  PYTHONPATH="$ROOT/backend" "$PY" hybrid_review.py --cases A:main A:cover B:main C:main --seeds 7,100,101,102 \
  --out "$ROOT/docs/diagnostics/motion_gnn_$VER/hybrid_review"
touch "$EXT/$RUN.done"
echo "finished at $(date)"
