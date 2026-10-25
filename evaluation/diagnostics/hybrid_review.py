"""Rule engine vs hybrid generator from the same synthetic starting configurations (offline review).

    PYTHONPATH=backend <ml venv python> evaluation/diagnostics/hybrid_review.py \
        --cases A:main A:cover --seeds 7,100,101,102,103,104 --out docs/diagnostics/hybrid_v0

The same seed gives both engines the same plan and starting positions (they come from the scenario's own random
streams); only how the players move differs. For every clip: the football audit (audit.py) and the serving
analyzer's view (did the designed story appear, complete, used). For the first seed of each case: an unannotated
side-by-side GIF and before/during/after frames of the hybrid clip. Needs the optional torch install.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))

from audit import audit  # noqa: E402
from common import from_rows  # noqa: E402
from render import frames_png, panel_from_clip, story_picks, video_gif  # noqa: E402

import holdout_v2  # noqa: E402
from app.simulator.defense.generate import generate_clip  # noqa: E402

MATCH = {"perturb": None, "matching": {"iou_min": 0.3}}


def one(family: str, variant: str, seed: int, engine: str) -> tuple[dict, object]:
    tpl = {"family": family, "variant": variant, "attacking_team": "blue", "attack_direction": 1, "start_s": 0.0,
           "engine": engine}
    meta, rows, evs, truth = generate_clip(seed, {**tpl, "clip_id": f"{family}_{variant}_{seed}_{engine}"})
    clip = from_rows(f"{family}-{variant} seed {seed} ({engine})", meta, rows, evs, truth)
    rec = holdout_v2.evaluate_clip({**tpl, "clip_id": "clip_h"}, seed, MATCH)
    story = {k: rec.get(k) for k in ("executed", "matched", "complete", "expected_complete", "used", "used_expected",
                                     "restricted_found", "exposed_found", "extra_claims", "wrong_emphasis")}
    a = audit(clip)
    return {"family": family, "variant": variant, "seed": seed, "engine": engine, "story": story,
            "offside_releases": truth["markers_match_s"].get("offside_releases"),
            "audit": {k: a[k] if not isinstance(a[k], list) else len(a[k]) for k in a
                      if k not in ("clip_id", "family", "variant", "seed")}}, clip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="+", default=["A:main", "A:cover"])
    ap.add_argument("--seeds", default="7,100,101,102,103,104")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",")]
    rows = []
    for case in args.cases:
        fam, var = case.split(":")
        for k, seed in enumerate(seeds):
            pair = {}
            for engine in ("rule", "hybrid"):
                rec, clip = one(fam, var, seed, engine)
                rows.append(rec)
                pair[engine] = clip
                print(json.dumps(rec), flush=True)
            if k == 0:
                name = f"{fam}_{var}_seed{seed}"
                video_gif([panel_from_clip(pair["rule"], roles=False), panel_from_clip(pair["hybrid"], roles=False)],
                          args.out / f"{name}_rule_vs_hybrid.gif")
                frames_png(panel_from_clip(pair["hybrid"]), story_picks(pair["hybrid"]),
                           args.out / f"{name}_hybrid_before_during_after.png")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "review.json").write_text(json.dumps(rows, indent=1) + "\n")
    lines = ["| case | seed | engine | story matched / complete / used | frozen players | team-mate clusters | "
             "tightly marked (mean / max) | unpressured stalls | offside receptions | width / depth in possession |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s, a = r["story"], r["audit"]
        lines.append(f"| {r['family']}:{r['variant']} | {r['seed']} | {r['engine']} | {s['matched']} / {s['complete']} / "
                     f"{s['used']} | {a['frozen_players']} | {a['teammate_clusters']} | "
                     f"{a['tightly_marked_share_mean']} / {a['tightly_marked_share_max']} | {a['unpressured_stalls']} | "
                     f"{a['offside_receptions']} | {a['width_in_possession_m']} / {a['depth_in_possession_m']} |")
    (args.out / "review.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
