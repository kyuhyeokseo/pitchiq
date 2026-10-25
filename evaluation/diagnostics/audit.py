"""Football-plausibility audit of generated clips (offline; reads public rows plus private truth).

    PYTHONPATH=backend python evaluation/diagnostics/audit.py --committed clip_001 clip_002 clip_003
    PYTHONPATH=backend python evaluation/diagnostics/audit.py --generate A:main B:main C:main --seeds 100-119

Every number is measured from the tracking and event rows. The checks are screens for review, not proof of
plausibility: a clip that passes them still needs to be watched.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from common import DT, Clip, generate, load_committed

OUTFIELD_PAIR_M = 3.0  # team-mates this close for long are clustering, unless pressing together near the ball
PAIR_HOLD_S = 2.0
MARK_M = 2.5  # an opponent this close counts as a tight marker
STALL_FREE_M = 8.0  # a carrier with no opponent this close is unpressured
STALL_S = 2.0
FROZEN_S = 4.0
RECEIVE_M = 1.0  # a receiver farther than this from the ball on the reception frame did not really control it


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) frame index pairs of consecutive True values."""
    out, start = [], None
    for k, v in enumerate(mask):
        if v and start is None:
            start = k
        if not v and start is not None:
            out.append((start, k))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def audit(c: Clip) -> dict:
    teams = sorted({c.team(p) for p in c.pids})
    idx = {tm: [i for i, p in enumerate(c.pids) if c.team(p) == tm] for tm in teams}
    gk = {tm: next(i for i in idx[tm] if c.role(c.pids[i]) == "GK") for tm in teams}
    out: dict = {"clip_id": c.clip_id, "family": c.truth.get("family"), "variant": c.truth.get("variant"),
                 "seed": c.truth.get("seed"), "duration_s": round(float(c.t[-1]), 1)}

    # 1. attack direction: a team's goalkeeper stands at the goal it defends
    out["direction_ok"] = all((c.xy[:, gk[tm], 0].mean() < 52.5) == (c.direction(tm) == 1) for tm in teams)

    # 2. possession: a controlled ball is nearest to a player of the team said to have it
    bad = 0
    for k, r in enumerate(c.rows):
        if r["ball_state"] != "controlled" or not r["possession_team_id"]:
            continue
        d = np.linalg.norm(c.xy[k] - c.ball[k, :2], axis=1)
        if c.team(c.pids[int(np.argmin(d))]) != r["possession_team_id"]:
            bad += 1
    out["possession_mismatch_frames"] = bad

    # 3. offside at pass release (receiver from the pass_receive that answers the release)
    by_id = {e["event_id"]: e for e in c.events}
    offside = []
    for e in c.events:
        if e["kind"] != "pass_receive":
            continue
        rel = by_id.get(e["related_event_id"])
        if rel is None:
            continue
        k = int(round((rel["event_time_s"] - c.start_s) / DT))
        att = c.team(e["target_id"])
        opp = idx[teams[1] if att == teams[0] else teams[0]]
        u_def = np.sort(c.u(att, c.xy[k, opp, 0]))
        line = max(float(u_def[-2]), float(c.u(att, c.ball[k, 0])))
        u_recv = float(c.u(att, c.xy[k, c.pids.index(e["target_id"]), 0]))
        if u_recv > 52.5 and u_recv > line + 0.2:
            offside.append({"t": round(float(c.t[k]), 1), "receiver": e["target_id"], "beyond_m": round(u_recv - line, 1)})
    out["offside_receptions"] = offside

    # 3b. the receiver's distance from the ball on the reception frame, and how far the ball jumps on the next one
    gaps = []
    for e in c.events:
        if e["kind"] != "pass_receive":
            continue
        k = int(round((e["event_time_s"] - c.start_s) / DT))
        i = c.pids.index(e["target_id"])
        gap = float(np.linalg.norm(c.xy[k, i] - c.ball[k, :2]))
        jump = float(np.linalg.norm(c.ball[k + 1, :2] - c.ball[k, :2])) if k + 1 < len(c.rows) else 0.0
        gaps.append({"t": round(float(c.t[k]), 1), "receiver": e["target_id"], "gap_m": round(gap, 2),
                     "next_ball_step_m": round(jump, 2)})
    out["receptions"] = gaps
    out["far_receptions"] = [g for g in gaps if g["gap_m"] > RECEIVE_M]

    # 4. clustering: outfield team-mates within 3 m for 2 s or more, away from the ball
    clusters = []
    for tm in teams:
        of = [i for i in idx[tm] if i != gk[tm]]
        for a in range(len(of)):
            for b in range(a + 1, len(of)):
                i, j = of[a], of[b]
                d = np.linalg.norm(c.xy[:, i] - c.xy[:, j], axis=1)
                near_ball = np.minimum(np.linalg.norm(c.xy[:, i] - c.ball[:, :2], axis=1),
                                       np.linalg.norm(c.xy[:, j] - c.ball[:, :2], axis=1)) < 8.0
                for s, e_ in _runs((d < OUTFIELD_PAIR_M) & ~near_ball):
                    if (e_ - s) * DT >= PAIR_HOLD_S:
                        clusters.append({"pair": f"{c.pids[i]}({c.role(c.pids[i])})+{c.pids[j]}({c.role(c.pids[j])})",
                                         "from_s": round(float(c.t[s]), 1), "for_s": round((e_ - s) * DT, 1)})
    out["teammate_clusters"] = clusters

    # 5. man-marking density: share of outfield players of the team in possession with an opponent within 2.5 m
    share = []
    for k, r in enumerate(c.rows):
        tm = r["possession_team_id"]
        if not tm:
            continue
        att = [i for i in idx[tm] if i != gk[tm]]
        opp = [i for i in idx[teams[0] if tm == teams[1] else teams[1]]]
        d = np.linalg.norm(c.xy[k, att][:, None] - c.xy[k, opp][None], axis=2).min(axis=1)
        share.append(float((d < MARK_M).mean()))
    out["tightly_marked_share_mean"] = round(float(np.mean(share)), 2) if share else None
    out["tightly_marked_share_max"] = round(float(np.max(share)), 2) if share else None

    # 6. an unpressured carrier who stands still
    stalls = []
    owner = [r["ball"].get("owner_id") if r["ball_state"] == "controlled" else None for r in c.rows]
    speed = np.zeros(len(c.rows))
    free = np.zeros(len(c.rows), dtype=bool)
    for k, o in enumerate(owner):
        if o is None:
            continue
        i = c.pids.index(o)
        opp = [j for j in range(len(c.pids)) if c.team(c.pids[j]) != c.team(o)]
        free[k] = np.linalg.norm(c.xy[k, opp] - c.xy[k, i], axis=1).min() > STALL_FREE_M
        if k > 0:
            speed[k] = float(np.linalg.norm(c.xy[k, i] - c.xy[k - 1, i])) / DT
    for s, e_ in _runs(free & (speed < 1.0) & np.array([o is not None and c.role(o) != "GK" for o in owner])):
        if (e_ - s) * DT >= STALL_S:
            stalls.append({"carrier": owner[s], "from_s": round(float(c.t[s]), 1), "for_s": round((e_ - s) * DT, 1)})
    out["unpressured_stalls"] = stalls

    # 7. frozen players: an outfield player barely moving for 4 s while the ball travels 15 m or more
    frozen = []
    w = int(FROZEN_S / DT)
    for i, p in enumerate(c.pids):
        if c.role(p) == "GK":
            continue
        for k in range(0, len(c.rows) - w, 5):
            moved = float(np.linalg.norm(c.xy[k + w, i] - c.xy[k, i]))
            ball_moved = float(np.linalg.norm(c.ball[k + w, :2] - c.ball[k, :2]))
            if moved < 1.0 and ball_moved >= 15.0:
                frozen.append({"player": f"{p}({c.role(p)})", "from_s": round(float(c.t[k]), 1)})
                break
    out["frozen_players"] = frozen

    # 8. shape in possession: width and depth of the outfield team in possession
    width, depth = [], []
    for k, r in enumerate(c.rows):
        tm = r["possession_team_id"]
        if not tm:
            continue
        of = [i for i in idx[tm] if i != gk[tm]]
        width.append(float(np.ptp(c.xy[k, of, 1])))
        depth.append(float(np.ptp(c.xy[k, of, 0])))
    out["width_in_possession_m"] = round(float(np.median(width)), 1) if width else None
    out["depth_in_possession_m"] = round(float(np.median(depth)), 1) if depth else None
    return out


def summary_line(a: dict) -> str:
    return (f"{a['clip_id']:<10} {a['family']}:{a['variant']:<8} seed {a['seed']:<4} dir_ok={a['direction_ok']} "
            f"poss_mismatch={a['possession_mismatch_frames']:<3} offside={len(a['offside_receptions'])} "
            f"far_recv={len(a['far_receptions'])} "
            f"clusters={len(a['teammate_clusters'])} marked={a['tightly_marked_share_mean']}/"
            f"{a['tightly_marked_share_max']} stalls={len(a['unpressured_stalls'])} "
            f"frozen={len(a['frozen_players'])} width={a['width_in_possession_m']} depth={a['depth_in_possession_m']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--committed", nargs="*", default=[])
    ap.add_argument("--generate", nargs="*", default=[], help="FAMILY:VARIANT")
    ap.add_argument("--seeds", default="100-119")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    lo, hi = (int(x) for x in args.seeds.split("-"))
    results = [audit(load_committed(cid)) for cid in args.committed]
    for fv in args.generate:
        fam, var = fv.split(":")
        results += [audit(generate(fam, var, s, clip_id=f"{fam}_{var}_{s}")) for s in range(lo, hi + 1)]
    for a in results:
        print(summary_line(a))
    if args.generate:
        agg = Counter()
        for a in results:
            agg["clips"] += 1
            agg["with_offside_reception"] += bool(a["offside_receptions"])
            agg["receptions"] += len(a["receptions"])
            agg["far_receptions"] += len(a["far_receptions"])
            agg["with_clusters"] += bool(a["teammate_clusters"])
            agg["with_unpressured_stall"] += bool(a["unpressured_stalls"])
            agg["with_frozen_player"] += bool(a["frozen_players"])
        print(dict(agg))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
