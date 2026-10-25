"""Offline generator sanity report (uses generator-private parameters; never part of the app).

    PYTHONPATH=backend python evaluation/scripts/generator_sanity.py --seeds 40

For each recipe, simulates clips in the canonical frame and reports observable
geometry: whether the fullback ends ahead of the winger, his lateral side at the
crossing, the opposing fullback-to-centreback gap change, and the final ball
receiver. Overlap recipes are also replayed with every defender response on the
same attack seed, to check that the response (not the run) drives the gap change.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict

import numpy as np

from app.simulator.engine import A, D, simulate
from app.simulator.recipes import clip_streams, load_recipes, sample_clip_params
from app.simulator.roster import ROLES


def clip_stats(res) -> dict:
    F = np.array([f.pos[A("RB")] for f in res.frames])
    W = np.array([f.pos[A("RW")] for f in res.frames])
    LB = np.array([f.pos[D("LB")] for f in res.frames])
    LCB = np.array([f.pos[D("LCB")] for f in res.frames])
    ahead = F[:, 0] - W[:, 0]
    cross = np.flatnonzero(ahead > 0)
    side = None
    if cross.size:
        k = cross[0]
        side = float(abs(F[k, 1] - 34) - abs(W[k, 1] - 34))  # >0 outside, <0 inside
    gap = np.linalg.norm(LB - LCB, axis=1)
    # "Before" = when defenders first react to the run (generator marker), else
    # the run start. Two "after" windows: the next 5 s (gapΔ5s), and only while
    # the winger still has the ball (gapΔflank), so a later central recycle of
    # the ball does not count as an opening.
    t0 = res.markers.get("threat_t") or res.markers.get("run_start_t") or 0.0
    k0 = min(int(round(t0 / 0.2)), len(gap) - 1)
    t1 = min(t0 + 5.0, (res.markers.get("decision_t") or 1e9) + 0.4)
    k1 = max(k0 + 1, min(int(round(t1 / 0.2)) + 1, len(gap)))
    recv = res.markers.get("final_receiver_index")
    return {
        "max_ahead": float(ahead.max()),
        "side_at_cross": side,
        "gap_before": float(gap[k0]),
        "gap_delta": float(gap[k0:k0 + 26].max() - gap[k0]),
        "gap_delta_flank": float(gap[k0:k1].max() - gap[k0]),
        "final": None if recv is None else ROLES[int(recv) % 11],
        "frames": len(res.frames),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=40)
    args = ap.parse_args()
    cfg = load_recipes()
    print(f"{'recipe':16s} {'n':>3s} {'ahead>1m':>8s} {'outside@x':>9s} {'inside@x':>8s} "
          f"{'gapΔ5s':>6s} {'gapΔflank':>9s} {'frames':>11s}  final receivers")
    for name in cfg["recipes"]:
        rows = []
        for s in range(args.seeds):
            a, d, m = clip_streams(1000 + s, 0)
            rows.append(clip_stats(simulate(sample_clip_params(cfg, name, a, d), m)))
        ahead = sum(r["max_ahead"] > 1.0 for r in rows)
        outside = sum(r["side_at_cross"] is not None and r["side_at_cross"] > 1.0 for r in rows)
        inside = sum(r["side_at_cross"] is not None and r["side_at_cross"] < -1.0 for r in rows)
        fr = [r["frames"] for r in rows]
        print(f"{name:16s} {len(rows):3d} {ahead:8d} {outside:9d} {inside:8d} "
              f"{np.median([r['gap_delta'] for r in rows]):6.1f} {np.median([r['gap_delta_flank'] for r in rows]):9.1f} "
              f"{min(fr):4d}-{max(fr):<4d}  "
              f"{dict(Counter(r['final'] for r in rows))}")

    print("\nSame attack seed, defender response swapped (recipe overlap_follow attack params):")
    by_resp = defaultdict(list)
    for s in range(args.seeds):
        for resp in ("mark_runner", "switch_mark", "hold_line"):
            a, d, m = clip_streams(5000 + s, 0)
            by_resp[resp].append(clip_stats(simulate(sample_clip_params(cfg, "overlap_follow", a, d, resp), m)))
    for resp, rows in by_resp.items():
        line = f"  {resp:12s}"
        for key in ("gap_delta", "gap_delta_flank"):
            g = np.array([r[key] for r in rows])
            line += (f" {key}: median={np.median(g):4.1f} p10={np.percentile(g, 10):4.1f} "
                     f"p90={np.percentile(g, 90):4.1f} |")
        print(line + f" final={dict(Counter(r['final'] for r in rows))}")
    for key in ("gap_delta", "gap_delta_flank"):
        wins = sum(f[key] > h[key] for f, h in zip(by_resp["mark_runner"], by_resp["hold_line"]))
        print(f"  {key}: mark_runner > hold_line in {wins}/{args.seeds} paired seeds")


if __name__ == "__main__":
    main()
