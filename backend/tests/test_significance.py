"""Significance engine + candidate insight tests (spec 12.3 / parts of 12.4)."""

from __future__ import annotations

import dataclasses
import re

import numpy as np
import pytest

from app.analytics.patterns import detect_clip
from app.analytics.pipeline import Candidate, analyze_scenario, analyzer_for
from app.analytics.significance import InsightSelector, eval_frame, measure, score
from app.ingestion.loader import load_scenario
from app.privacy import find_private_tokens
from app.simulator.generate import build_clip, write_scenario
from app.simulator.validate import private_vocabulary

from .synth import make_clip, overlap_moves

HANGUL = re.compile(r"[가-힣]")


@pytest.fixture(scope="module")
def demo_candidates(demo):
    sc = load_scenario(demo[0])
    return sc, {c.prediction.clip_id: c for c in analyze_scenario(sc)}


def test_demo_meaningful_overlap_is_suggested_and_defended_one_suppressed(demo_candidates):
    _, by_clip = demo_candidates
    follow, hold = by_clip["c02"], by_clip["c01"]
    assert follow.prediction.pattern == hold.prediction.pattern == "overlap"
    assert follow.significance.should_suggest and follow.insight is not None
    assert not hold.significance.should_suggest and hold.insight is None
    assert "NO_OBSERVABLE_SPATIAL_GAIN" in hold.significance.reason_codes
    assert follow.significance.score > hold.significance.score
    assert follow.measurement.gap_delta_m >= 2.5 > hold.measurement.gap_delta_m
    assert "c03" not in by_clip  # aborted overlap: nothing detected


@pytest.fixture(scope="module")
def paired(tmp_path_factory, cfg):
    """Identical attacking runs (same seed) against two defender responses."""
    root = tmp_path_factory.mktemp("paired")
    out = {}
    for resp in ("mark_runner", "hold_line"):
        clips = [build_clip(cfg, {"clip_id": f"p{i:02d}", "recipe": "overlap_follow", "attacking_team": "blue",
                                  "flank": "right", "start_match_s": 10.0 + 30.0 * i, "scenario_id": resp},
                            "m", 6100 + i, 0, blue_dir_p1=1, response_override=resp) for i in range(16)]
        write_scenario(root / resp, root / f"{resp}.jsonl", scenario_id=resp, match_id="m", title="paired",
                       generator_version="t", clips=clips)
        sc = load_scenario(root / resp)
        res = {}
        for cid in sc.clips:
            cands = [x for x in analyzer_for(sc, cid, InsightSelector()).advance_to(10_000) if isinstance(x, Candidate)]
            res[cid] = cands[0] if cands else None
        out[resp] = res
    return out


def test_defender_following_ranks_above_defender_holding_for_identical_runs(paired):
    f, h = paired["mark_runner"], paired["hold_line"]
    assert all(c is not None and c.prediction.pattern == "overlap" for c in [*f.values(), *h.values()])
    wins = [f[k].significance.score > h[k].significance.score for k in f]
    assert np.mean(wins) >= 0.75
    f_rate = np.mean([c.significance.should_suggest for c in f.values()])
    h_rate = np.mean([c.significance.should_suggest for c in h.values()])
    assert f_rate >= 0.7 and h_rate <= 0.4, (f_rate, h_rate)


def test_cooldown_suppresses_a_near_duplicate_story(demo_candidates):
    sc, by_clip = demo_candidates
    c = by_clip["c02"]
    t = c.evaluated_at_match_s
    sel = InsightSelector(cooldown_s=45.0)
    sel.record(t - 10.0, "blue", "right", "overlap")
    assert sel.novelty(t, "blue", "right", "overlap") < 1.0
    assert sel.novelty(t, "blue", "left", "overlap") == 1.0
    assert sel.novelty(t + 40.0, "blue", "right", "overlap") == 1.0
    again = score(c.detection, c.measurement, novelty=sel.novelty(t, "blue", "right", "overlap"))
    assert not again.should_suggest and "COOLDOWN_SIMILAR_STORY" in again.reason_codes


def test_rate_limit_allows_only_exceptional_stories_inside_interval(demo_candidates):
    _, by_clip = demo_candidates
    sig = by_clip["c02"].significance
    sel = InsightSelector(min_interval_s=20.0, exceptional_score=0.99)
    t = by_clip["c02"].evaluated_at_match_s
    sel.record(t - 5.0, "red", "left", "underlap")
    limited = sel.admit(t, sig)
    assert not limited.should_suggest and "RATE_LIMITED" in limited.reason_codes
    assert InsightSelector(min_interval_s=20.0, exceptional_score=0.5).admit(t, sig).should_suggest


def test_ball_far_from_the_run_lowers_significance():
    near = make_clip(overlap_moves())
    far = make_clip(overlap_moves(), ball_holder=("blue", "LW"))  # ball on the opposite flank
    results = []
    for clip in (near, far):
        (det,) = detect_clip(clip)
        ke = eval_frame(det, len(clip))
        results.append(score(det, measure(det, clip, ke)))
    assert results[1].metrics["signal_ball_relevance"] < results[0].metrics["signal_ball_relevance"]
    assert results[1].score < results[0].score
    assert "BALL_FAR" in results[1].reason_codes and not results[1].should_suggest


def test_significance_never_reads_beyond_its_evaluation_frame(demo_candidates):
    sc, by_clip = demo_candidates
    c = by_clip["c02"]
    clip = sc.clips["c02"]
    k = c.measurement.k_eval
    rng = np.random.default_rng(0)
    tampered_pos = clip.pos.copy()
    tampered_pos[k + 1:] += rng.normal(0, 10, tampered_pos[k + 1:].shape)
    tampered = dataclasses.replace(clip, pos=np.clip(tampered_pos, 0, [105, 68]), _index={})
    m2 = measure(c.detection, tampered, k, sc.clip_events("c02"))
    assert m2 == c.measurement


def test_insight_cites_real_evidence_and_only_known_numbers(demo_candidates):
    sc, by_clip = demo_candidates
    ins = by_clip["c02"].insight
    clip = sc.clips["c02"]
    events = {e.event_id: e for e in sc.events}
    assert ins.production_status == "suggested" and ins.source_mode == "deterministic_fallback"
    assert ins.detected_at_match_s <= ins.display_at_match_s < ins.expires_at_match_s
    assert all(0 <= k < len(clip) for k in ins.evidence_frame_indices)
    assert ins.evidence_event_ids and all(events[e].match_time_s <= ins.display_at_match_s for e in ins.evidence_event_ids)
    facts = {f"{v:.1f}" for v in ins.numeric_facts.values() if isinstance(v, float)}
    facts |= {f"{v:+.1f}" for v in ins.numeric_facts.values() if isinstance(v, float)}
    for text in (ins.fan_en, ins.analyst_en, ins.fan_ko, ins.analyst_ko):
        for num in re.findall(r"[+-]?\d+\.\d+(?=\s?m)", text):
            assert num in facts, (num, text)
    assert ins.numeric_facts["defender_gap_after_m"] == round(by_clip["c02"].measurement.gap_after_m, 1)


def test_fan_and_analyst_texts_differ_and_languages_are_real(demo_candidates):
    ins = demo_candidates[1]["c02"].insight
    assert ins.fan_en != ins.analyst_en and ins.fan_ko != ins.analyst_ko
    assert len(ins.analyst_en) > len(ins.fan_en) and "not a causal estimate" in ins.analyst_en
    assert HANGUL.search(ins.fan_ko) and HANGUL.search(ins.analyst_ko)
    assert not HANGUL.search(ins.fan_en + ins.analyst_en)
    for text in (ins.fan_en, ins.analyst_en):
        assert not re.search(r"\bcaus(ed|es)\b|\bbecause\b", text, re.I)


def test_insight_and_overlay_contain_no_private_vocabulary(demo_candidates, cfg):
    ins = demo_candidates[1]["c02"].insight
    blob = ins.model_dump_json()
    # "overlap" is a legitimate *prediction*; only generator-specific tokens are banned here.
    banned = private_vocabulary(cfg) - {"overlap", "underlap"}
    assert not find_private_tokens(blob, banned)
    kinds = [e.type for e in ins.overlay.elements]
    assert {"player_highlight", "movement_arrow", "space_zone", "caption_card"} <= set(kinds)
    gap = next(e for e in ins.overlay.elements if e.type == "space_zone" and e.kind == "defender_gap")
    assert gap.player_ids == [demo_candidates[1]["c02"].measurement.defending_fullback,
                              demo_candidates[1]["c02"].measurement.defending_centreback]
