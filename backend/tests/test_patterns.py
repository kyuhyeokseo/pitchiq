"""Rule detector tests: hand-built scenes (flank/direction invariance, hard negatives,
hysteresis, streaming) plus agreement with the generated matrix."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.analytics.patterns import OverlapDetector, detect_clip
from app.ingestion.loader import clip_from_frames, load_scenario
from app.schemas import TrackingFrame

from .helpers import truth_rows
from .synth import WINGER_HOLD, chain, lerp, make_clip, overlap_moves, underlap_moves


def _patterns(clip):
    return [(d.prediction.pattern, tuple(d.prediction.actor_player_ids)) for d in detect_clip(clip)]


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("direction", [1, -1])
def test_overlap_detected_on_both_flanks_and_directions(mirror, direction):
    got = _patterns(make_clip(overlap_moves(), mirror=mirror, direction=direction))
    fb, w = ("blue_03", "blue_11") if mirror else ("blue_02", "blue_07")
    assert got == [("overlap", (fb, w))]


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("direction", [1, -1])
def test_underlap_detected_on_both_flanks_and_directions(mirror, direction):
    got = _patterns(make_clip(underlap_moves(), mirror=mirror, direction=direction))
    assert [p for p, _ in got] == ["underlap"]


def test_parallel_advance_without_exchange_is_not_a_pattern():
    moves = {("blue", "RB"): lerp((50, 12), (70, 12), 3.0, 7.0), ("blue", "RW"): lerp((65, 6), (85, 6), 3.0, 7.0)}
    assert _patterns(make_clip(moves)) == []


def test_aborted_run_is_not_a_pattern():
    rb = chain((6.0, lerp((50, 8), (70, 5), 3.0, 6.0)), (99.0, lerp((70, 5), (62, 9), 6.0, 9.0)))
    assert _patterns(make_clip({("blue", "RB"): rb, ("blue", "RW"): WINGER_HOLD})) == []


def test_fullback_already_ahead_is_not_an_overlap():
    moves = {("blue", "RB"): lerp((80, 5), (84, 5), 0.0, 10.0), ("blue", "RW"): lerp((70, 10), (72, 12), 0.0, 10.0)}
    assert _patterns(make_clip(moves)) == []


def test_winger_dropping_past_a_static_fullback_is_not_an_overlap():
    moves = {("blue", "RB"): lambda t: (60.0, 8.0), ("blue", "RW"): lerp((75, 10), (50, 12), 3.0, 8.0)}
    assert _patterns(make_clip(moves)) == []


def test_run_by_team_without_possession_is_ignored():
    assert _patterns(make_clip(overlap_moves(), possession="red", ball_holder=("red", "LB"))) == []


def test_fullback_level_in_width_is_reported_uncertain():
    moves = {("blue", "RB"): lerp((50, 10), (88, 10), 3.0, 8.5), ("blue", "RW"): lerp((75, 10), (78, 10), 2.0, 6.0)}
    assert [p for p, _ in _patterns(make_clip(moves))] == ["uncertain"]


def test_hysteresis_one_detection_for_a_wobbling_crossing():
    # Fullback clearly overtakes, then wobbles across the crossing threshold
    # (d = +1.6 / -1.0 m) without dropping far enough behind to re-arm.
    rb = chain((8.0, lerp((50, 5), (80, 5), 3.0, 8.0)),
               (99.0, lambda t: (77.6 if int(round(t * 5)) % 2 else 75.0, 5.0)))
    moves = {("blue", "RB"): rb, ("blue", "RW"): lambda t: (76.0, 12.0)}
    assert len(_patterns(make_clip(moves))) == 1


def test_detection_is_identical_on_the_live_prefix():
    clip = make_clip(overlap_moves())
    (det,) = detect_clip(clip)
    (det_live,) = detect_clip(clip.until(det.k_detect))
    assert det_live.prediction == det.prediction
    assert detect_clip(clip.until(det.k_detect - 1)) == []  # nothing before persistence completes


def test_frames_must_be_streamed_in_order():
    d = OverlapDetector(make_clip(overlap_moves()))
    d.step(0)
    with pytest.raises(ValueError):
        d.step(2)


def test_prediction_is_heuristic_and_cites_existing_frames():
    clip = make_clip(overlap_moves())
    (det,) = detect_clip(clip)
    p = det.prediction
    assert p.confidence_kind == "heuristic_score" and 0.0 < p.confidence <= 1.0
    assert all(0 <= k < len(clip) for k in p.evidence_frame_indices)
    assert p.start_s < p.end_s


def test_missing_roles_yield_no_pairs_instead_of_crashing():
    clip = make_clip(overlap_moves())
    roles = tuple("DM" if r in ("RB", "LB") else r for r in clip.roles)
    broken = type(clip)(**{**{f: getattr(clip, f) for f in clip.__dataclass_fields__ if f != "_index"}, "roles": roles})
    assert detect_clip(broken) == []


def test_invalid_frames_are_rejected_before_analysis(demo):
    sc = load_scenario(demo[0])
    good = sc.frames["c01"][0].model_dump()
    bad = {**good, "players": good["players"][:21]}
    with pytest.raises(ValidationError):
        clip_from_frames([TrackingFrame.model_validate(bad)])


def test_detector_matches_generator_truth_on_matrix(matrix):
    d, truth_path = matrix
    sc = load_scenario(d)
    for cid, row in truth_rows(truth_path).items():
        dets = detect_clip(sc.clips[cid])
        mine = [x.prediction.pattern for x in dets if x.prediction.actor_player_ids[0] == row["actors"]["fullback"]]
        others = [x for x in dets if x.prediction.actor_player_ids[0] != row["actors"]["fullback"]]
        expected = [] if row["pattern_truth"] == "none" else [row["pattern_truth"]]
        assert mine == expected, (cid, row["recipe_name"], mine)
        assert not others, (cid, [o.prediction for o in others])
