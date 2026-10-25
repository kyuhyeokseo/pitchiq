"""Offline checks that generator-private labels agree with OBSERVABLE geometry.

These tests may read private truth (they are evaluation code, not the live
system). They verify that every "overlap" clip really shows the fullback
passing the winger on the outside, every "underlap" on the inside, and that the
hard negatives contain forward fullback movement WITHOUT any positional
exchange. Geometry is computed from world coordinates with attack-direction
normalisation and centre-distance width, so it is flank- and direction-free.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.simulator.engine import A, D, simulate
from app.simulator.export import ClipContext, world_xy
from app.simulator.generate import build_clip
from app.simulator.recipes import clip_streams, sample_clip_params
from app.simulator.roster import MIRROR_ROLE, ROLES

from .helpers import clip_frames, series, truth_rows, width, x_attacking

HARD_NEGATIVES = ("support_no_pass", "parallel_run", "aborted_overlap")


def _actor_geometry(d, row):
    frames = clip_frames(d, row["clip_id"])
    direction = frames[0]["attack_direction"][row["attacking_team_id"]]
    F = series(frames, row["actors"]["fullback"])
    W = series(frames, row["actors"]["winger"])
    return x_attacking(F, direction), x_attacking(W, direction), width(F), width(W)


def test_positive_labels_are_realised_in_tracking(matrix):
    d, truth = matrix
    n = 0
    for row in truth_rows(truth).values():
        if row["pattern_truth"] not in ("overlap", "underlap"):
            continue
        fx, wx, fw, ww = _actor_geometry(d, row)
        ahead = fx - wx
        assert ahead[0] < -5.0, row["clip_id"]  # fullback starts deeper than the winger
        assert ahead.max() > 1.0, row["clip_id"]  # ...and ends up beyond him
        k = int(np.flatnonzero(ahead > 0)[0])  # first frame he is level/ahead
        side = fw[k] - ww[k]
        if row["pattern_truth"] == "overlap":
            assert side > 1.0, (row["clip_id"], side)  # passes on the OUTSIDE
        else:
            assert side < -1.0, (row["clip_id"], side)  # passes on the INSIDE
        n += 1
    assert n == 32  # 4 positive recipes x 8 flank/team/direction combinations


def test_hard_negatives_move_forward_without_passing_the_winger(matrix):
    d, truth = matrix
    n = 0
    for row in truth_rows(truth).values():
        if row["recipe_name"] not in HARD_NEGATIVES:
            continue
        assert row["pattern_truth"] == "none"
        fx, wx, _, _ = _actor_geometry(d, row)
        assert (fx - wx).max() < -0.5, row["clip_id"]  # never level with / beyond the winger
        assert fx.max() - fx[0] > 5.0, row["clip_id"]  # but genuinely advances (near-positive)
        if row["recipe_name"] == "aborted_overlap":
            # The run is abandoned: he drops back from its peak, or he checks it
            # to take a pass behind the winger (and may then carry the ball on).
            took_pass = row["markers_match_s"]["final_receiver_id"] == row["actors"]["fullback"]
            k_peak = int(fx.argmax())
            assert took_pass or fx[k_peak] - fx[k_peak:].min() > 2.0, row["clip_id"]
        n += 1
    assert n == 24


@pytest.mark.parametrize("recipe", ["overlap_follow", "overlap_hold", "underlap", *HARD_NEGATIVES, "circulation"])
def test_label_geometry_holds_across_many_seeds(cfg, recipe):
    """Canonical-frame sweep (fast): 30 seeds per recipe."""
    truth = cfg["recipes"][recipe]["pattern_truth"]
    for s in range(30):
        a, dr, m = clip_streams(9000 + s, 0)
        res = simulate(sample_clip_params(cfg, recipe, a, dr), m)
        F = np.array([f.pos[A("RB")] for f in res.frames])
        W = np.array([f.pos[A("RW")] for f in res.frames])
        ahead = F[:, 0] - W[:, 0]
        if truth == "none":
            assert ahead.max() < -0.5, (recipe, s)
        else:
            k = int(np.flatnonzero(ahead > 0)[0])
            outside = abs(F[k, 1] - 34) - abs(W[k, 1] - 34)
            assert ahead.max() > 1.0 and (outside > 1.0 if truth == "overlap" else outside < -1.0), (recipe, s)


def test_flank_mirror_and_direction_flip_are_exact(cfg):
    base = {"clip_id": "x", "recipe": "overlap_follow", "attacking_team": "blue", "flank": "right",
            "start_match_s": 100.0, "scenario_id": "t"}
    right, _, _ = build_clip(cfg, base, "m", 5, 0, blue_dir_p1=1)
    left, _, _ = build_clip(cfg, {**base, "flank": "left"}, "m", 5, 0, blue_dir_p1=1)
    flipped, _, _ = build_clip(cfg, {**base, "start_match_s": 3000.0}, "m", 5, 0, blue_dir_p1=1)  # period 2
    red, _, _ = build_clip(cfg, {**base, "attacking_team": "red"}, "m", 5, 0, blue_dir_p1=1)
    assert flipped[0]["attack_direction"] == {"blue": -1, "red": 1}

    def pos(frame, team, role):
        p = next(p for p in frame["players"] if p["team_id"] == team and p["nominal_role"] == role)
        return p["position"]["x_m"], p["position"]["y_m"]

    for fr, fl, ff, fd in zip(right, left, flipped, red):
        for team in ("blue", "red"):
            other = "red" if team == "blue" else "blue"
            for role in ROLES:
                x, y = pos(fr, team, role)
                xl, yl = pos(fl, team, MIRROR_ROLE[role])
                assert (xl, yl) == pytest.approx((x, 68.0 - y), abs=0.011)
                xf, yf = pos(ff, team, role)
                assert (xf, yf) == pytest.approx((105.0 - x, 68.0 - y), abs=0.011)
                # Swapping which team attacks (red attacks toward x=0 in period 1).
                xd, yd = pos(fd, other, role)
                assert (xd, yd) == pytest.approx((105.0 - x, 68.0 - y), abs=0.011)


def test_world_transform_round_trip():
    pts = np.array([[10.0, 5.0], [80.0, 60.0]])
    for flank in ("right", "left"):
        for d in (1, -1):
            ctx = ClipContext("m", "c", 0.0, 1, "blue", "red", flank, d)
            assert np.allclose(world_xy(world_xy(pts, ctx), ctx), pts)


def _gap_delta(res) -> float:
    LB = np.array([f.pos[D("LB")] for f in res.frames])
    LCB = np.array([f.pos[D("LCB")] for f in res.frames])
    gap = np.linalg.norm(LB - LCB, axis=1)
    k0 = int(round((res.markers["threat_t"] or res.markers["run_start_t"]) / 0.2))
    return float(gap[k0:k0 + 26].max() - gap[k0])


def test_defender_response_alone_changes_spacing_for_an_identical_attack(cfg):
    follow, hold = [], []
    for s in range(30):
        runs = {}
        for resp in ("mark_runner", "hold_line"):
            a, dr, m = clip_streams(7000 + s, 0)
            p = sample_clip_params(cfg, "overlap_follow", a, dr, response_override=resp)
            runs[resp] = (p, simulate(p, m))
        pf, ph = runs["mark_runner"][0], runs["hold_line"][0]
        # Identical attacking plan; only the defensive response differs.
        for field in ("fullback_start", "winger_start", "run_speed_mps", "run_beyond_m", "lane_y_m", "run_delay_s"):
            assert getattr(pf, field) == getattr(ph, field)
        follow.append(_gap_delta(runs["mark_runner"][1]))
        hold.append(_gap_delta(runs["hold_line"][1]))
    follow, hold = np.array(follow), np.array(hold)
    assert (follow > hold).mean() >= 0.8
    assert np.median(follow) - np.median(hold) >= 2.0
