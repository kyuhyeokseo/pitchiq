"""Server-side update latency for the v2 app (spec 17.2): per frame, one analysis step plus the live overlay request
the UI makes for the selected episode. In-process (FastAPI TestClient): no network, no browser rendering.

    PYTHONPATH=backend python evaluation/scripts/api_latency_v2.py --out evaluation/reports/latency_v2
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("LLM_MODE", "offline")
os.environ.setdefault("PITCHIQ_DOTENV", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import STORE, app  # noqa: E402
from app.replay.catalog import list_clips  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    client = TestClient(app)
    step_ms, overlay_ms, update_ms = [], [], []
    per_clip = {}
    for cid in list_clips():
        r = client.post("/api/replays", json={"clip_id": cid}).json()
        rid, tok = r["replay_id"], r["session_token"]
        s = STORE.get(rid)
        n = len(s.clip.frames)
        c_upd = []
        for k in range(1, n):
            t0 = time.perf_counter()
            with s.lock:
                s._advance_to(k)
            t1 = time.perf_counter()
            eps = s.analyzer.packets
            ms_ov = 0.0
            if eps:
                eid = max(eps, key=lambda e: eps[e][-1].episode.detected_at_s)
                resp = client.get(f"/api/replays/{rid}/overlays", headers={"x-session-token": tok},
                                  params={"episode_id": eid, "display_mode": "live", "audience": "analyst"})
                assert resp.status_code == 200, resp.text
                ms_ov = (time.perf_counter() - t1) * 1000.0
                overlay_ms.append(ms_ov)
            ms_step = (t1 - t0) * 1000.0
            step_ms.append(ms_step)
            update_ms.append(ms_step + ms_ov)
            c_upd.append(ms_step + ms_ov)
        per_clip[cid] = {"frames": n - 1, "update_ms_p50": round(float(np.percentile(c_upd, 50)), 2),
                         "update_ms_p95": round(float(np.percentile(c_upd, 95)), 2)}

    def pct(xs):
        return {"p50": round(float(np.percentile(xs, 50)), 2), "p95": round(float(np.percentile(xs, 95)), 2),
                "max": round(float(max(xs)), 2), "n": len(xs)}
    report = {"host": os.environ.get("PITCHIQ_HOST_LABEL") or ("hpc-compute-node" if os.environ.get("SLURM_JOB_ID") else "workstation"), "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "what": "in-process: analysis step + live overlay request per frame (no network, no rendering)",
              "analysis_step_ms": pct(step_ms), "live_overlay_request_ms": pct(overlay_ms),
              "per_frame_update_ms": pct(update_ms), "targets": {"analysis_p95_ms": 200, "ui_update_p95_ms": 300},
              "per_clip": per_clip}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("host", "analysis_step_ms", "live_overlay_request_ms",
                                             "per_frame_update_ms")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
