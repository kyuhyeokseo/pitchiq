"""Offline motion-realism report on PUBLIC tracking + events (no private truth).

    PYTHONPATH=backend python evaluation/scripts/motion_realism.py data/generated/test_s9001

Measures three things a viewer notices in a replay:
- run curvature: for each detected overlap/underlap, the largest distance of the
  fullback's path from the straight chord between run start and detection;
- receiver momentum: speed of the last attacking receiver in the final third
  1 s after the ball arrives (a "freeze" is < 1.5 m/s);
- pass and move: a winger's speed 1 s after he passes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from app.analytics.geometry import x_att
from app.analytics.patterns import detect_clip
from app.ingestion.loader import load_scenario

FREEZE_MPS = 1.5


def chord_deviation(path: np.ndarray) -> float:
    a, b = path[0], path[-1]
    ab = b - a
    L = float(np.linalg.norm(ab))
    if L < 1e-6:
        return 0.0
    n = np.array([-ab[1], ab[0]]) / L
    return float(np.abs((path - a) @ n).max())


def speed(clip, k: int, i: int) -> float:
    k = min(k, len(clip) - 1)
    return float(np.linalg.norm(clip.vel[k, i]))


def frame_of(clip, t: float) -> int:
    return int(np.argmin(np.abs(clip.t - t)))


def report(scenario_dir: Path) -> dict:
    sc = load_scenario(scenario_dir)
    dev, recv_after, wing_after = [], [], []
    for cid, clip in sc.clips.items():
        for det in detect_clip(clip):
            if det.prediction.pattern in ("overlap", "underlap"):
                dev.append(chord_deviation(clip.pos[det.k_start:det.k_detect + 1, det.pair.fullback]))
        events = sc.clip_events(cid)
        recvs = [e for e in events if e.event_type == "receive"]
        if recvs:
            e = recvs[-1]
            i, k = clip.idx(e.actor_id), frame_of(clip, e.match_time_s)
            team = clip.team_ids[i]
            if x_att(clip.pos[k, i, 0], clip.attack_direction[team]) >= 75.0 and k + 5 < len(clip):
                recv_after.append(speed(clip, k + 5, i))
        for e in events:
            if e.event_type == "pass" and clip.roles[clip.idx(e.actor_id)] in ("RW", "LW"):
                k = frame_of(clip, e.match_time_s)
                if k + 5 < len(clip):
                    wing_after.append(speed(clip, k + 5, clip.idx(e.actor_id)))

    def summ(v: list[float]) -> dict:
        a = np.array(v)
        return {"n": len(v), "median": round(float(np.median(a)), 2), "p10": round(float(np.percentile(a, 10)), 2),
                "p90": round(float(np.percentile(a, 90)), 2)} if v else {"n": 0}

    return {
        "scenario_dir": str(scenario_dir),
        "run_chord_deviation_m": summ(dev),
        "final_third_receiver_speed_1s_after_mps": summ(recv_after),
        "final_third_receiver_freeze_rate": round(float(np.mean(np.array(recv_after) < FREEZE_MPS)), 3) if recv_after else None,
        "winger_speed_1s_after_pass_mps": summ(wing_after),
        "winger_freeze_rate": round(float(np.mean(np.array(wing_after) < FREEZE_MPS)), 3) if wing_after else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario_dir", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    rep = report(args.scenario_dir)
    text = json.dumps(rep, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")


if __name__ == "__main__":
    main()
