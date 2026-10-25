"""Contract tests for the public Pydantic schemas."""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest
from pydantic import ValidationError

from app.schemas import BroadcastInsight, MatchEvent, TacticalPrediction, TrackingFrame, Vec2
from app.simulator.engine import slot_targets
from app.simulator.roster import FORMATION_LINES, ROLES, formation_counts, player_id


def make_frame() -> dict:
    pos = slot_targets(np.array([70.0, 34.0]))
    players = []
    for t_i, team in enumerate(("blue", "red")):
        for r_i, role in enumerate(ROLES):
            x, y = pos[t_i * 11 + r_i]
            players.append({
                "player_id": player_id(team, role), "team_id": team, "nominal_role": role,
                "is_goalkeeper": role == "GK", "position": {"x_m": float(x), "y_m": float(y)},
                "velocity": {"x_m": 0.0, "y_m": -1.5},
            })
    return {
        "match_id": "m", "clip_id": "c", "frame_index": 0, "match_time_s": 10.0, "clip_time_s": 0.0,
        "period": 1, "attack_direction": {"blue": 1, "red": -1}, "players": players,
        "ball_position": {"x_m": 70.0, "y_m": 34.0}, "ball_owner_id": "blue_08", "possession_team_id": "blue",
    }


def test_valid_frame_and_negative_velocity_allowed():
    f = TrackingFrame.model_validate(make_frame())
    assert len(f.players) == 22
    assert f.players[0].velocity.y_m < 0  # velocities are not pitch-bounded


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda f: f["players"].pop(), "22 players"),
        (lambda f: f["players"][3].update(player_id=f["players"][2]["player_id"]), "duplicate"),
        (lambda f: f["players"][12].update(team_id="blue", player_id="blue_99"), "11 players per team"),
        (lambda f: f["players"][5].update(nominal_role="GK", is_goalkeeper=True), "one goalkeeper"),
        (lambda f: f["players"][4].update(is_goalkeeper=True), "is_goalkeeper"),
        (lambda f: f["players"][7]["position"].update(x_m=106.0), "outside pitch"),
        (lambda f: f.update(ball_position={"x_m": 50.0, "y_m": 70.0}), "ball outside"),
        (lambda f: f.update(ball_owner_id="blue_42"), "unknown ball owner"),
        (lambda f: f.update(possession_team_id="red"), "possession team"),
        (lambda f: f.update(attack_direction={"blue": 1, "red": 1}), "opposite directions"),
        (lambda f: f.update(secret="x"), "Extra inputs"),
    ],
)
def test_frame_rejects_invalid(mutate, msg):
    f = make_frame()
    mutate(f)
    with pytest.raises(ValidationError, match=msg):
        TrackingFrame.model_validate(f)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_vec2_rejects_non_finite(bad):
    with pytest.raises(ValidationError):
        Vec2(x_m=bad, y_m=1.0)


def _event(**kw) -> dict:
    e = {"match_id": "m", "event_id": "e1", "clip_id": "c", "match_time_s": 1.0, "event_type": "pass",
         "team_id": "blue", "actor_id": "blue_08", "target_id": "blue_07", "start": {"x_m": 1, "y_m": 1},
         "end": {"x_m": 2, "y_m": 2}, "success": None, "context": {"length_m": 1.4}}
    e.update(kw)
    return e


def test_event_valid_and_rejects_private_context():
    MatchEvent.model_validate(_event())
    for key in ("pattern_truth", "generator_intent", "recipe_name", "defender_response", "seed"):
        with pytest.raises(ValidationError, match="private generator fields"):
            MatchEvent.model_validate(_event(context={"nested": {key: "x"}}))
    with pytest.raises(ValidationError):
        MatchEvent.model_validate(_event(event_type="teleport"))


def test_prediction_bounds():
    base = dict(prediction_id="p", match_id="m", clip_id="c", start_s=1.0, end_s=2.0, pattern="overlap",
                actor_player_ids=["blue_02"], confidence=0.7, evidence_frame_indices=[0, 5])
    p = TacticalPrediction.model_validate(base)
    assert p.confidence_kind == "heuristic_score"
    with pytest.raises(ValidationError):
        TacticalPrediction.model_validate({**base, "confidence": 1.2})
    with pytest.raises(ValidationError):
        TacticalPrediction.model_validate({**base, "end_s": 0.5})


def _insight(**kw) -> dict:
    d = dict(
        insight_id="i", match_id="m", clip_id="c", prediction_id="p", detected_at_match_s=10.0,
        display_at_match_s=10.2, expires_at_match_s=16.0, evidence_frame_indices=[0], evidence_event_ids=["e1"],
        actor_player_ids=["blue_02"], pattern="overlap", importance="high", title="t", fan_en="a",
        analyst_en="b", fan_ko="c", analyst_ko="d", numeric_facts={"gap_delta_m": 3.1},
        overlay={"sync_match_s": 10.2, "ttl_s": 6.0, "source_prediction_id": "p", "elements": [
            {"type": "player_highlight", "player_id": "blue_02"},
            {"type": "movement_arrow", "player_id": "blue_02", "path": [{"x_m": 1, "y_m": 1}, {"x_m": 5, "y_m": 2}],
             "start_s": 5.0, "end_s": 10.0},
        ]},
        production_status="suggested", source_mode="deterministic_fallback",
    )
    d.update(kw)
    return d


def test_insight_overlay_union_and_no_backdating():
    ins = BroadcastInsight.model_validate(_insight())
    assert [e.type for e in ins.overlay.elements] == ["player_highlight", "movement_arrow"]
    with pytest.raises(ValidationError, match="precede detection"):
        BroadcastInsight.model_validate(_insight(display_at_match_s=9.0))
    with pytest.raises(ValidationError):
        BroadcastInsight.model_validate(_insight(source_mode="foundry_maybe"))
    bad = copy.deepcopy(_insight())
    bad["overlay"]["elements"].append({"type": "laser", "player_id": "x"})
    with pytest.raises(ValidationError):
        BroadcastInsight.model_validate(bad)


def test_formation_lines_sum_to_ten_without_goalkeeper():
    assert formation_counts("4-3-3") == (4, 3, 3)
    assert formation_counts("4-4-2") == (4, 4, 2)
    assert formation_counts("3-2-5") == (3, 2, 5)
    for name, lines in FORMATION_LINES.items():
        flat = [r for line in lines for r in line]
        assert len(flat) == 10 and len(set(flat)) == 10 and "GK" not in flat, name
        assert set(flat) == set(ROLES) - {"GK"}, name
