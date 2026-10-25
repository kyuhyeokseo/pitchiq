"""Offline evaluation of the rule detector + significance engine on a held-out synthetic set.

    PYTHONPATH=backend python evaluation/scripts/eval_rules.py data/generated/eval_s4242 \
        --out evaluation/reports/rules_eval_s4242.json

The live pipeline sees only public files. Private truth is joined AFTERWARDS,
here, for scoring. Matching: a detection counts for a clip when its fullback is
the clip's true fullback (truth actor IDs); detections on any other pair are
false alarms. Results are synthetic-only and say nothing about real football.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from app.analytics.pipeline import Candidate, analyzer_for
from app.analytics.significance import InsightSelector
from app.ingestion.loader import load_scenario

CLASSES = ("overlap", "underlap", "none")


def prf(conf: Counter, cls: str) -> dict[str, float]:
    tp = conf[(cls, cls)]
    fp = sum(v for (t, p), v in conf.items() if p == cls and t != cls)
    fn = sum(v for (t, p), v in conf.items() if t == cls and p != cls)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if prec + rec and not np.isnan(prec + rec) else float("nan")
    return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3), "support": tp + fn}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario_dir", type=Path)
    ap.add_argument("--truth", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    truth_path = args.truth or Path("data/private_truth") / f"{args.scenario_dir.name}.jsonl"
    truth = {r["clip_id"]: r for r in map(json.loads, truth_path.read_text().splitlines())}

    t0 = time.perf_counter()
    sc = load_scenario(args.scenario_dir)
    load_s = time.perf_counter() - t0
    conf: Counter = Counter()
    lags, step_ms = [], []
    by_resp: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    false_alarms = 0
    for cid, clip in sc.clips.items():
        tr = truth[cid]
        an = analyzer_for(sc, cid, InsightSelector())  # fresh selector per clip: no cross-clip cooldown
        cands: list[Candidate] = []
        for k in range(len(clip)):
            ts = time.perf_counter()
            cands.extend(x for x in an.advance_to(k) if isinstance(x, Candidate))
            step_ms.append((time.perf_counter() - ts) * 1000)
        mine = [c for c in cands if c.prediction.actor_player_ids[0] == tr["actors"]["fullback"]]
        others = [c for c in cands if c.prediction.actor_player_ids[0] != tr["actors"]["fullback"]]
        false_alarms += len(others)
        pred = mine[0].prediction.pattern if mine else "none"
        conf[(tr["pattern_truth"], pred)] += 1
        for c in mine:
            if c.prediction.pattern == tr["pattern_truth"] and c.prediction.pattern in ("overlap", "underlap"):
                # Lag from the first frame the fullback is ahead of the winger to detection.
                lags.append(float(clip.t[c.detection.k_detect] - clip.t[c.detection.k_cross]))
            by_resp[(c.prediction.pattern, tr["defender_response"])].append(c)

    minutes = sum(len(c) for c in sc.clips.values()) * 0.2 / 60.0
    report = {
        "dataset": str(args.scenario_dir),
        "clips": len(sc.clips),
        "tracked_minutes": round(minutes, 2),
        "confusion_truth_vs_pred": {f"{t}->{p}": v for (t, p), v in sorted(conf.items())},
        "per_class": {c: prf(conf, c) for c in CLASSES},
        "false_alarms_other_pairs": false_alarms,
        "false_alarms_per_tracked_minute": round(
            (false_alarms + sum(v for (t, p), v in conf.items() if t == "none" and p != "none")) / minutes, 3),
        "detection_lag_s_after_crossing": {
            "median": round(float(np.median(lags)), 2) if lags else None,
            "p95": round(float(np.percentile(lags, 95)), 2) if lags else None,
        },
        "analysis_step_ms": {"p50": round(float(np.median(step_ms)), 3), "p95": round(float(np.percentile(step_ms, 95)), 3)},
        "load_s": round(load_s, 2),
        "significance_by_true_response": {},
    }
    for (pattern, resp), cs in sorted(by_resp.items()):
        g = np.array([c.measurement.gap_delta_m for c in cs])
        report["significance_by_true_response"][f"{pattern}/{resp}"] = {
            "n": len(cs),
            "suggested_rate": round(float(np.mean([c.significance.should_suggest for c in cs])), 3),
            "score_median": round(float(np.median([c.significance.score for c in cs])), 3),
            "gap_delta_m_median": round(float(np.median(g)), 2),
            "gap_delta_m_p25_p75": [round(float(np.percentile(g, 25)), 2), round(float(np.percentile(g, 75)), 2)],
            "new_option_lane_rate": round(float(np.mean([bool(c.measurement.new_lane_receivers) for c in cs])), 3),
        }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")


if __name__ == "__main__":
    main()
