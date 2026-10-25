#!/usr/bin/env bash
# Serve the real app (FastAPI + built frontend) on a compute node for a hands-on check.
# PITCHIQ_SRUN_ARGS sets the allocation and partition, e.g. PITCHIQ_SRUN_ARGS="-p <partition> -c 4".
#
#   scripts/dev_on_compute.sh            # 60 min on the default partition
#   MINUTES=30 scripts/dev_on_compute.sh
#
# It prints the node name. In VSCode open the "Ports" panel -> "Forward a Port" -> enter
# <node>:8000 (for example cn061:8000) and open the forwarded address in your browser.
# Stop with Ctrl+C (the allocation ends with it). Never run the server on the login node.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MINUTES="${MINUTES:-60}"
PORT="${PORT:-8000}"
SRUN_ARGS="${PITCHIQ_SRUN_ARGS:?set PITCHIQ_SRUN_ARGS, e.g. -p <partition> -c 4}"
# shellcheck disable=SC2086
exec srun $SRUN_ARGS -t "00:${MINUTES}:00" -N 1 -n 1 --job-name=pitchiq-dev \
  bash -c "echo; echo \"PitchIQ is starting on \$(hostname -s):${PORT}  ->  VSCode Ports: forward \$(hostname -s):${PORT}\"; echo; \
           cd '${ROOT}' && PYTHONPATH=backend exec .venv/bin/python -m uvicorn app.api.app:app --host 0.0.0.0 \
           --port ${PORT} --workers 1 --log-level warning"
