"""Holdout evaluation for the v2 defensive families (spec 16 and 17.2).

    PYTHONPATH=backend python evaluation/holdout_v2.py                      # full run, writes the report
    PYTHONPATH=backend python evaluation/holdout_v2.py --seeds 5000 5000    # quick smoke run

Offline only. Clips are generated in memory from the private generator. The analyzer sees only the public
observations (manifest, frames, events with their availability times). This script alone joins its output with the
generator's private story; nothing here flows back into analytics.

Per clip, the designed story is a time window (from its start marker to the last ball action) plus the claims the
scenario was built to produce. The best-overlapping episode of the defending team in an accepted family is matched to
it (IoU >= iou_min, one episode per story). Claims outside the design are counted separately: they may still be
geometrically right, so "designed precision" is a lower bound, not a false-alarm rate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml

from app.agents.fallback import analyst_plan, producer_plan
from app.agents.validate import PlanError, validate_analyst_plan, validate_producer_plan
from app.analytics.engine import TacticalAnalyzer
from app.contracts import MatchEvent, MatchMeta, ObservationFrame
from app.simulator.defense.generate import FIXTURES, GENERATOR_VERSION, generate_clip

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "evaluation" / "holdout_v2.yaml"
ANALYTICS_CONFIG = ROOT / "backend" / "app" / "analytics" / "analytics_config.yaml"
REPORTS = ROOT / "evaluation" / "reports" / "holdout_v2"


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 1e-9 else 0.0


def analyze(meta: MatchMeta, frames: list[ObservationFrame], events: list[MatchEvent]):
    """Run the serving analyzer on public observations only; events enter when they become available."""
    an = TacticalAnalyzer(meta, frames[0].frame_ref.clip_id)
    seen: set[str] = set()
    wall = []
    for f in frames:
        arrived = [e for e in events if e.available_at_s <= f.match_time_s + 1e-9 and e.event_id not in seen]
        seen |= {e.event_id for e in arrived}
        t0 = time.perf_counter()
        an.step(f, arrived)
        wall.append(time.perf_counter() - t0)
    return an, wall


def episode_summaries(an: TacticalAnalyzer, team: str) -> list[dict]:
    out = []
    for eid, revs in an.packets.items():
        last = revs[-1]
        ep = last.episode
        if ep.defending_team_id != team:
            continue
        codes: dict[str, set[str]] = defaultdict(set)
        for p in revs:
            for c in p.claims:
                codes[c.dimension].add(c.code)
        out.append({
            "episode_id": eid, "family": eid.split(f"_{team}_", 1)[1].rsplit("_f", 1)[0],
            "detected_s": ep.detected_at_s, "window": (ep.window.start_s, ep.window.end_s),
            "complete": any(p.episode.analysis_status == "complete_tradeoff" for p in revs),
            "restricted": sorted(codes["restricted"]), "exposed": sorted(codes["exposed"]),
            "outcome": ep.outcome.status, "realized_target": ep.outcome.realized_target_id,
            "changes": sorted({c.code for p in revs for c in p.changes}), "revisions": len(revs),
        })
    return out


def plan_checks(an: TacticalAnalyzer) -> Counter:
    """Deterministic Analyst -> Producer plans for every revision, through the same validators the API uses."""
    c: Counter = Counter()
    for revs in an.packets.values():
        for p in revs:
            c["revisions"] += 1
            try:
                a = analyst_plan(p)
                validate_analyst_plan(a, p)
                validate_producer_plan(producer_plan(p, a), p, a)
                c["valid"] += 1
            except PlanError as e:
                c[f"invalid:{e.code}"] += 1
            if p.episode.analysis_status == "complete_tradeoff":
                c["complete_revisions"] += 1  # the validator rejects a plan that drops either side
    return c


def evaluate_clip(template: dict, seed: int, cfg: dict) -> dict:
    meta_d, obs, evs, truth = generate_clip(seed, {**template, "clip_id": "clip_h", "perturb": cfg["perturb"]})
    meta_d["clips"][0]["checksum"] = meta_d["clips"][0]["checksum"] or "sha256:" + "0" * 64
    meta = MatchMeta.model_validate(meta_d)
    frames = [ObservationFrame.model_validate(o) for o in obs]
    events = [MatchEvent.model_validate(e) for e in evs]
    an, wall = analyze(meta, frames, events)
    start = frames[0].match_time_s
    duration = frames[-1].match_time_s - start
    team = truth["defending_team_id"]
    story = truth["story"]
    exp = truth["expected"]
    restricted = story.get("restricted", exp["restricted"])
    restricted_any = story.get("restricted_any")  # one of these labels names the same restriction
    exposed = story.get("exposed", exp["exposed"])
    suppressed = exp.get("suppressed", [])
    optional = story.get("optional", [])
    eps = episode_summaries(an, team)
    rec = {"family": truth["family"], "variant": truth["variant"], "seed": seed, "perturbation": truth.get("perturbation"),
           "executed": story["executed"], "duration_s": round(duration, 2), "episodes": len(eps),
           "frame_wall_s": wall, "plans": dict(plan_checks(an))}
    if not story["executed"] or story["window_clip_s"] is None:
        return rec
    win = (start + story["window_clip_s"][0], start + story["window_clip_s"][1])
    cands = [(iou(win, e["window"]), e) for e in eps if e["family"] in story["families"]]
    cands = [x for x in cands if x[0] >= cfg["matching"]["iou_min"]]
    best = max(cands, key=lambda x: x[0], default=(0.0, None))
    m = best[1]
    found = set(m["restricted"]) | set(m["exposed"]) if m else set()
    designed = set(restricted_any or restricted) | set(exposed)
    r_expected = 1 if restricted_any else len(restricted)
    r_found = int(bool(set(restricted_any) & found)) if restricted_any else len(set(restricted) & found)
    overlapping = [e for e in eps if e["window"][0] <= win[1] and e["window"][1] >= win[0]]
    wrong = sorted({c for e in overlapping for c in (e["restricted"] + e["exposed"]) if c in suppressed})
    rec.update({
        "story_window_s": [round(win[0] - start, 2), round(win[1] - start, 2)],
        "matched": m is not None, "iou": round(best[0], 3),
        "onset_lag_s": round(m["detected_s"] - win[0], 2) if m else None,
        "expected_complete": bool(exposed), "complete": bool(m and m["complete"]),
        "restricted_expected": r_expected, "restricted_found": r_found,
        "exposed_expected": len(exposed), "exposed_found": len(set(exposed) & found),
        "claims_found": len(found), "claims_designed": len(found & (designed | set(optional))),
        "extra_claims": sorted(found - designed - set(optional)),
        "wrong_emphasis": wrong,
        "used_expected": bool(story["used"]),
        "used": bool(m and m["outcome"] == "exploited_in_synthetic_play"),
        "unmatched_episodes": len(eps) - (1 if m else 0),
        "matched_episode": {k: m[k] for k in ("family", "restricted", "exposed", "changes", "outcome")} if m else None,
    })
    return rec


def _pct(xs: list[float], q: float) -> float | None:
    return round(float(np.percentile(xs, q)), 4) if xs else None


def summarize(records: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[f"{r['family']}:{r['variant']}"].append(r)
    by_variant = {}
    for key, rs in sorted(groups.items()):
        ok = [r for r in rs if r["executed"]]
        matched = [r for r in ok if r.get("matched")]
        lags = [r["onset_lag_s"] for r in matched]
        exp_complete = [r for r in ok if r.get("expected_complete")]
        by_variant[key] = {
            "clips": len(rs), "executed": len(ok),
            "story_matched": len(matched),
            "onset_lag_s_median": round(statistics.median(lags), 2) if lags else None,
            "onset_lag_s_max": max(lags) if lags else None,
            "complete_when_expected": f"{sum(r['complete'] for r in exp_complete)}/{len(exp_complete)}",
            "complete_when_not_expected": f"{sum(r['complete'] for r in ok if not r.get('expected_complete'))}/"
                                          f"{sum(1 for r in ok if not r.get('expected_complete'))}",
            "restricted_recall": _ratio(ok, "restricted_found", "restricted_expected"),
            "exposed_recall": _ratio(ok, "exposed_found", "exposed_expected"),
            "designed_precision": _ratio(ok, "claims_designed", "claims_found"),
            "extra_claims": dict(Counter(c for r in ok for c in r.get("extra_claims", []))),
            "wrong_emphasis_clips": sum(1 for r in ok if r.get("wrong_emphasis")),
            "outcome_agreement": f"{sum(r.get('used') == r.get('used_expected') for r in ok)}/{len(ok)}",
            "abstained": sum(1 for r in ok if r.get("expected_complete") and not r.get("complete")),
            "unmatched_episodes_per_min": round(sum(r.get("unmatched_episodes", 0) for r in ok)
                                                / max(sum(r["duration_s"] for r in ok) / 60.0, 1e-9), 2),
        }
    ok = [r for r in records if r["executed"]]
    counter = [r for r in ok if not r.get("expected_complete")]
    wall = [w for r in records for w in r["frame_wall_s"]]
    plans = Counter()
    for r in records:
        plans.update(r["plans"])
    return {
        "by_variant": by_variant,
        "overall": {
            "clips": len(records), "executed": len(ok),
            "story_matched": sum(1 for r in ok if r.get("matched")),
            "designed_claim_precision": _ratio(ok, "claims_designed", "claims_found"),
            "restricted_recall": _ratio(ok, "restricted_found", "restricted_expected"),
            "exposed_recall": _ratio(ok, "exposed_found", "exposed_expected"),
            "counter_example_clips": len(counter),
            "counter_example_wrong_emphasis_clips": sum(1 for r in counter if r.get("wrong_emphasis")),
            "abstention_rate": _ratio_count(sum(1 for r in ok if r.get("expected_complete") and not r.get("complete")),
                                            sum(1 for r in ok if r.get("expected_complete"))),
            "unmatched_episodes_per_min": round(sum(r.get("unmatched_episodes", 0) for r in ok)
                                                / max(sum(r["duration_s"] for r in ok) / 60.0, 1e-9), 2),
            "analysis_step_wall_ms": {"p50": _ms(_pct(wall, 50)), "p95": _ms(_pct(wall, 95)),
                                      "max": _ms(max(wall) if wall else None), "frames": len(wall)},
            "plans": {"revisions": plans["revisions"], "valid": plans["valid"],
                      "invalid": {k: v for k, v in plans.items() if k.startswith("invalid:")},
                      "fallback_ratio": 1.0 if plans["revisions"] else None, "model_calls": 0},
        },
    }


def _ratio(rs: list[dict], num: str, den: str) -> str:
    n, d = sum(r.get(num, 0) for r in rs), sum(r.get(den, 0) for r in rs)
    return f"{n}/{d}" + (f" ({n / d:.2f})" if d else "")


def _ratio_count(n: int, d: int) -> str:
    return f"{n}/{d}" + (f" ({n / d:.2f})" if d else "")


def _ms(x: float | None) -> float | None:
    return None if x is None else round(x * 1000.0, 2)


def markdown(report: dict) -> str:
    o = report["overall"]
    lines = [
        f"# Holdout evaluation {report['config']['version']}",
        "",
        f"Generated {report['run']['time_utc']} on `{report['run']['host']}`; generator `{report['generator_version']}`;"
        f" analytics config sha256 `{report['analytics_config_sha256'][:16]}`.",
        "All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from"
        " the seeds looked at while tuning. Deterministic narration only (no model calls).",
        "",
        *(["## Changes from the previous version", ""] + [f"- {c}" for c in report["config"]["changes_from_previous"]]
          + [""] if report["config"].get("changes_from_previous") else []),
        "## Overall",
        "",
        f"- Clips: {o['clips']} ({o['executed']} played out as planned); designed story matched: {o['story_matched']}",
        f"- Designed claim precision (lower bound): {o['designed_claim_precision']}"
        f" (target {report['config']['targets']['supported_claim_precision']})",
        f"- Restricted recall: {o['restricted_recall']}; exposed recall: {o['exposed_recall']}",
        f"- Counter-example clips with a wrong emphasis: {o['counter_example_wrong_emphasis_clips']}"
        f" of {o['counter_example_clips']} (target {report['config']['targets']['counter_example_wrong_emphasis']})",
        f"- Abstention (exposure expected, story stayed partial or unmatched): {o['abstention_rate']}",
        f"- Episodes outside the designed story: {o['unmatched_episodes_per_min']} per clip-minute"
        " (upper bound on false alarms; includes real side stories such as goalkeeper phases)",
        f"- Analysis step wall time: p50 {o['analysis_step_wall_ms']['p50']} ms, p95 {o['analysis_step_wall_ms']['p95']} ms"
        f" over {o['analysis_step_wall_ms']['frames']} frames (single process, this host)",
        f"- Narration plans: {o['plans']['valid']}/{o['plans']['revisions']} valid; fallback ratio"
        f" {o['plans']['fallback_ratio']}; model calls {o['plans']['model_calls']}",
        "",
        "## By family and variant",
        "",
        "| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not |"
        " restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained |"
        " other episodes/min |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k, v in report["by_variant"].items():
        lines.append(f"| {k} | {v['clips']} | {v['executed']} | {v['story_matched']} | {v['onset_lag_s_median']} |"
                     f" {v['complete_when_expected']} | {v['complete_when_not_expected']} | {v['restricted_recall']} |"
                     f" {v['exposed_recall']} | {v['designed_precision']} | {v['wrong_emphasis_clips']} |"
                     f" {v['outcome_agreement']} | {v['abstained']} | {v['unmatched_episodes_per_min']} |")
    lines += ["", "## Claims outside the design (counts over clips; may still be geometrically correct)", ""]
    for k, v in report["by_variant"].items():
        if v["extra_claims"]:
            lines.append(f"- {k}: " + ", ".join(f"{c} {n}" for c, n in sorted(v["extra_claims"].items())))
    lines += ["", "## Definitions", "",
              f"- Story: the scenario's designed situation, from its start marker to the last ball action (private"
              f" truth). Matching: same defending team, accepted analytics family, IoU >= {report['config']['matching']['iou_min']},"
              " best single episode.",
              "- Designed precision: claims found in the matched episode that the scenario was built to produce (or"
              " lists as acceptable) over all restricted/exposed claims found there.",
              "- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the"
              " defending team that overlaps the story.",
              "- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed"
              " exposure was used.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--seeds", type=int, nargs=2, help="override the inclusive seed range (smoke runs)")
    ap.add_argument("--out", type=Path, help="report directory (default evaluation/reports/holdout_v2/<version>)")
    ap.add_argument("--render", type=Path, help="only rewrite report.md from an existing report.json")
    args = ap.parse_args(argv)
    if args.render:
        report = json.loads((args.render / "report.json").read_text())
        (args.render / "report.md").write_text(markdown(report))
        return 0
    cfg = yaml.safe_load(args.config.read_text())
    lo, hi = args.seeds or cfg["seeds"]
    t_start = time.time()
    records = []
    for template in FIXTURES:
        for seed in range(lo, hi + 1):
            records.append(evaluate_clip(template, seed, cfg))
        print(f"[holdout] {template['family']}:{template['variant']} seeds {lo}-{hi} done", flush=True)
    report = {
        "config": cfg, "generator_version": GENERATOR_VERSION,
        "analytics_config_sha256": hashlib.sha256(ANALYTICS_CONFIG.read_bytes()).hexdigest(),
        "run": {"host": os.environ.get("PITCHIQ_HOST_LABEL") or ("hpc-compute-node" if os.environ.get("SLURM_JOB_ID") else "workstation"), "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "wall_s": round(time.time() - t_start, 1), "seeds": [lo, hi]},
        **summarize(records),
        "clips": [{k: v for k, v in r.items() if k != "frame_wall_s"} for r in records],
    }
    out = args.out or REPORTS / cfg["version"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    (out / "report.md").write_text(markdown(report))
    print(json.dumps({"out": str(out), **{k: report["overall"][k] for k in ("clips", "story_matched",
                                                                           "designed_claim_precision",
                                                                           "counter_example_wrong_emphasis_clips")}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
