"""Tracking/event invariants (spec 12.1), checked independently of the validator,
plus tests that the validator itself catches corrupted data."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from app.schemas import MAX_BALL_SPEED_MPS, MAX_PLAYER_SPEED_MPS
from app.simulator.validate import validate_scenario_dir

from .helpers import clip_frames, events, manifest

# Engine acceleration cap is 4.5 m/s^2; positions are rounded to 1 cm, which adds
# up to ~0.7 m/s^2 of finite-difference noise.
MAX_ACCEL_MPS2 = 5.5


def _all_frames(d: Path):
    for c in manifest(d)["clips"]:
        yield c, clip_frames(d, c["clip_id"])


@pytest.mark.parametrize("which", ["matrix", "dataset", "demo"])
def test_generated_scenarios_pass_validator(which, request, cfg):
    d, truth = request.getfixturevalue(which)
    rep = validate_scenario_dir(d, truth_path=truth, private_vocab_cfg=cfg)
    assert rep.ok, rep.errors[:10]
    assert rep.stats["frames"] > 0 and rep.stats["events"] > 0


def test_matrix_covers_every_recipe_flank_team_and_direction(matrix, cfg):
    d, truth_path = matrix
    from .helpers import truth_rows

    rows = truth_rows(truth_path).values()
    combos = Counter((r["recipe_name"], r["flank_side"], r["attacking_team_id"],
                      r["attack_direction"][r["attacking_team_id"]]) for r in rows)
    assert len(combos) == len(cfg["recipes"]) * 2 * 2 * 2


def test_every_frame_has_22_unique_players_11_per_team_one_gk_and_a_ball(matrix):
    d, _ = matrix
    for _, frames in _all_frames(d):
        for f in frames:
            ids = [p["player_id"] for p in f["players"]]
            assert len(ids) == 22 and len(set(ids)) == 22
            assert Counter(p["team_id"] for p in f["players"]) == {"blue": 11, "red": 11}
            assert Counter(p["team_id"] for p in f["players"] if p["is_goalkeeper"]) == {"blue": 1, "red": 1}
            assert sum(1 for p in f["players"] if not p["is_goalkeeper"]) == 20  # 10 outfield per team
            b = f["ball_position"]
            assert 0 <= b["x_m"] <= 105 and 0 <= b["y_m"] <= 68
            for p in f["players"]:
                x, y = p["position"]["x_m"], p["position"]["y_m"]
                assert math.isfinite(x) and math.isfinite(y) and 0 <= x <= 105 and 0 <= y <= 68


def test_clips_are_5hz_monotonic_and_12_to_25_seconds(matrix):
    d, _ = matrix
    for c, frames in _all_frames(d):
        t = np.array([f["match_time_s"] for f in frames])
        assert np.allclose(np.diff(t), 0.2, atol=1e-6)
        assert [f["frame_index"] for f in frames] == list(range(len(frames)))
        assert 60 <= len(frames) <= 125
        assert 12.0 <= t[-1] - t[0] <= 25.0
        assert t[0] == pytest.approx(c["start_match_s"])


def test_speeds_and_accelerations_are_bounded(matrix):
    d, _ = matrix
    for _, frames in _all_frames(d):
        P = np.array([[[p["position"]["x_m"], p["position"]["y_m"]] for p in f["players"]] for f in frames])
        V = np.diff(P, axis=0) / 0.2
        assert np.linalg.norm(V, axis=2).max() <= MAX_PLAYER_SPEED_MPS
        assert (np.linalg.norm(np.diff(V, axis=0), axis=2) / 0.2).max() <= MAX_ACCEL_MPS2
        B = np.array([[f["ball_position"]["x_m"], f["ball_position"]["y_m"]] for f in frames])
        assert (np.linalg.norm(np.diff(B, axis=0), axis=1) / 0.2).max() <= MAX_BALL_SPEED_MPS


def test_reported_velocity_matches_finite_difference(matrix):
    d, _ = matrix
    _, frames = next(_all_frames(d))
    for a, b in zip(frames, frames[1:]):
        for pa, pb in zip(a["players"], b["players"]):
            vx = (pb["position"]["x_m"] - pa["position"]["x_m"]) / 0.2
            assert pb["velocity"]["x_m"] == pytest.approx(vx, abs=0.15)


def test_every_resolved_pass_has_a_matching_receive(dataset):
    d, _ = dataset
    evs = events(d)
    receives = {e["context"]["pass_event_id"]: e for e in evs if e["event_type"] == "receive"}
    passes = [e for e in evs if e["event_type"] == "pass"]
    assert passes
    for p in passes:
        if p["context"].get("unresolved_at_clip_end"):
            continue
        r = receives[p["event_id"]]
        assert r["actor_id"] == p["target_id"] and r["match_time_s"] > p["match_time_s"]
        assert p["success"] is None  # outcome is not known at release time (no future leakage)


# ----------------------------------------------------------- validator negatives
def _copy(demo_dir: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "scenario"
    shutil.copytree(demo_dir, dst)
    return dst


def _rewrite_clip(d: Path, mutate) -> None:
    m = manifest(d)
    c = m["clips"][0]
    frames = clip_frames(d, c["clip_id"])
    mutate(frames)
    raw = "".join(json.dumps(f) + "\n" for f in frames).encode()
    blob = gzip.compress(raw, mtime=0)
    (d / c["tracking_file"]).write_bytes(blob)
    c["tracking_sha256"] = hashlib.sha256(blob).hexdigest()
    (d / "manifest.json").write_text(json.dumps(m))


def _rewrite_events(d: Path, mutate) -> None:
    m = manifest(d)
    evs = events(d)
    mutate(evs, m)
    text = "".join(json.dumps(e) + "\n" for e in evs)
    (d / "events.jsonl").write_text(text)
    m["events_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    (d / "manifest.json").write_text(json.dumps(m))


def _errors(d: Path, cfg) -> str:
    rep = validate_scenario_dir(d, private_vocab_cfg=cfg)
    assert not rep.ok
    return "\n".join(rep.errors)


def test_validator_detects_teleporting_player(demo, tmp_path, cfg):
    d = _copy(demo[0], tmp_path)
    _rewrite_clip(d, lambda fr: fr[20]["players"][5]["position"].update(x_m=fr[20]["players"][5]["position"]["x_m"] + 4.0))
    assert "speed" in _errors(d, cfg)


def test_validator_detects_missing_player(demo, tmp_path, cfg):
    d = _copy(demo[0], tmp_path)
    _rewrite_clip(d, lambda fr: fr[3]["players"].pop())
    assert "22 players" in _errors(d, cfg)


def test_validator_detects_event_after_final_frame(demo, tmp_path, cfg):
    d = _copy(demo[0], tmp_path)

    def late(evs, m):
        e = dict(evs[-1], event_id="late_ev", match_time_s=m["clips"][-1]["end_match_s"] + 0.2)
        evs.append(e)

    _rewrite_events(d, late)
    assert "outside its clip interval" in _errors(d, cfg)


def test_validator_detects_unknown_actor(demo, tmp_path, cfg):
    d = _copy(demo[0], tmp_path)
    _rewrite_events(d, lambda evs, m: evs[0].update(actor_id="blue_99"))
    assert "unknown actor_id" in _errors(d, cfg)


def test_validator_detects_private_label_in_event_context(demo, tmp_path, cfg):
    d = _copy(demo[0], tmp_path)
    _rewrite_events(d, lambda evs, m: evs[0]["context"].update(pattern_truth="overlap"))
    assert "private" in _errors(d, cfg)


def test_validator_detects_recipe_name_anywhere_in_public_text(demo, tmp_path, cfg):
    d = _copy(demo[0], tmp_path)
    m = manifest(d)
    m["title"] = "clip with overlap_follow"
    (d / "manifest.json").write_text(json.dumps(m))
    assert "private vocabulary" in _errors(d, cfg)


def test_validator_detects_tampered_checksum(demo, tmp_path, cfg):
    d = _copy(demo[0], tmp_path)
    m = manifest(d)
    (d / m["clips"][0]["tracking_file"]).write_bytes(gzip.compress(b"", mtime=0))
    assert "checksum" in _errors(d, cfg)
