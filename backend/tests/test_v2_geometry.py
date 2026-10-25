"""Hand-checkable geometry for the relation modules (spec 7, 8, 17.2)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.analytics.config import load_config
from app.analytics.cover import estimate_cover, runners
from app.analytics.lanes import lane_margins, screen_pass_options
from app.analytics.lines import estimate_lines
from app.analytics.marking import MarkingTracker
from app.analytics.observe import ObservationBuffer
from app.analytics.pressure import infer_pressure
from app.analytics.regions import central_region

from .v2_helpers import base_positions, frames_from

CFG = load_config()


def buffer(positions, vels=None, **kw):
    meta, frames = frames_from(positions, vels, **kw)
    buf = ObservationBuffer(meta, frames[0].frame_ref.clip_id)
    for f in frames:
        buf.append(f, [])
    return buf


def test_lane_margin_matches_hand_calculation():
    start, end = np.array([20.0, 34.0]), np.array([35.0, 34.0])
    d = np.array([[27.0, 35.0]])  # 1 m off the segment, 7 m along it
    interior, recv, *_ = lane_margins(start, end, d, CFG)
    # closest interior sample is q=(27,34): ball 7/14 = 0.5 s, defender 0.2 + 1/7 s
    assert interior == pytest.approx(0.2 + 1 / 7 - 0.5, abs=1e-9)
    assert recv == pytest.approx(0.2 + math.hypot(8, 1) / 7 - 15 / 14, abs=1e-9)


def test_screened_open_and_reception_competition():
    pos = base_positions()
    pos["blue_06"] = (35, 34)  # receiver 15 m ahead of the carrier blue_05 at (20, 24)? use a clean geometry below
    pos["blue_05"] = (20, 34)
    pos["blue_06"] = (35, 34)
    pos["red_09"] = (27.5, 34.4)  # standing in the lane
    buf = buffer([pos])
    lane = next(lane for lane in screen_pass_options(buf, 0, "blue", "red", CFG) if buf.pids[lane.receiver] == "blue_06")
    assert lane.availability == "screened" and "red_09" in [buf.pids[i] for i in lane.screeners]
    pos["red_09"] = (27.5, 50.0)  # far from the lane and the receiver
    pos["red_06"] = (60, 60)
    buf = buffer([pos])
    lane = next(lane for lane in screen_pass_options(buf, 0, "blue", "red", CFG) if buf.pids[lane.receiver] == "blue_06")
    assert lane.availability == "open" and lane.min_margin_s >= 0.4
    pos["red_06"] = (36.5, 34.0)  # tight marker on the receiver: reception is contested
    buf = buffer([pos])
    lane = next(lane for lane in screen_pass_options(buf, 0, "blue", "red", CFG) if buf.pids[lane.receiver] == "blue_06")
    assert lane.availability == "screened" and lane.reception_margin_s < 0.1


def test_offside_geometry_uses_second_last_defender_including_gk():
    pos = base_positions()
    pos["blue_05"] = (55, 34)  # carrier in the opponent half
    pos["blue_09"] = (74, 34)  # beyond the deepest outfield defender (72) -> offside geometry
    pos["blue_07"] = (66, 20)  # behind that line -> onside
    buf = buffer([pos])
    lanes = {buf.pids[lane.receiver]: lane for lane in screen_pass_options(buf, 0, "blue", "red", CFG)}
    assert lanes["blue_09"].eligibility == "offside_geometry"
    assert lanes["blue_07"].eligibility == "onside_geometry"


def test_cover_margin_matches_hand_calculation():
    pos = base_positions()
    pos["blue_09"] = (62.0, 34.0)
    vel = {"blue_09": (7.0, 0.0)}
    pos["red_05"] = (70.0, 44.0)  # cover candidate, goal-side
    pos["red_04"] = (72.0, 10.0)
    pos["red_02"] = (72.0, 60.0)
    pos["red_06"] = (50.0, 34.0)  # already behind the runner: recovery, not cover
    buf = buffer([pos], [vel])
    rs = dict(runners(buf, 0, "blue", "red", CFG))
    assert "blue_09" in [buf.pids[i] for i in rs]
    a = buf.idx("blue_09")
    c = estimate_cover(buf, 0, "blue", "red", a, rs[a], [], CFG)
    target = np.array([69.0, 34.0])  # one second of current velocity
    expect = 0.2 + float(np.linalg.norm(np.array([70.0, 44.0]) - target)) / 7.0 - 1.0
    assert buf.pids[c.outfield_cover] == "red_05"
    assert c.outfield_margin_s == pytest.approx(expect, abs=1e-6)


def test_stationary_attacker_is_not_a_runner():
    pos = base_positions()
    pos["blue_09"] = (62.0, 34.0)
    buf = buffer([pos], [{"blue_09": (0.3, 0.0)}])
    assert buf.idx("blue_09") not in dict(runners(buf, 0, "blue", "red", CFG))


def test_region_counts_come_from_ids_and_exclude_goalkeepers():
    pos = base_positions()
    pos["blue_05"] = (40, 34)  # ball carrier: region u in [45, 65], v in [20, 48]
    buf = buffer([pos])
    r = central_region(buf, 0, "blue", "red", screen_pass_options(buf, 0, "blue", "red", CFG), [], CFG)
    inside = lambda p: 45 <= p[0] <= 65 and 20 <= p[1] <= 48  # noqa: E731
    exp_a = {p for p, xy in pos.items() if p.startswith("blue") and not p.endswith("_01") and inside(xy)}
    exp_d = {p for p, xy in pos.items() if p.startswith("red") and not p.endswith("_01") and inside(xy)}
    assert {buf.pids[i] for i in r.attackers} == exp_a and {buf.pids[i] for i in r.defenders} == exp_d


def test_lines_name_a_clean_442_and_abstain_on_spread():
    pos = base_positions()  # red: back 4 at x 70-72, mid 4 at 58-60, front 2 at 45
    pos["red_07"] = (60, 10)  # make it a 4-4-2: move one forward into midfield
    buf = buffer([pos] * 16)
    res = estimate_lines(buf, 15, "red", "blue", CFG)
    assert res.full and res.shape_label == "approx_442"
    back = res.get("back")
    assert back.depth_m == pytest.approx(105 - 71.0, abs=1.01)
    pos["red_02"] = (90, 58)  # 18+ m of spread in the back group
    res = estimate_lines(buffer([pos] * 16), 15, "red", "blue", CFG)
    assert not res.full and res.shape_label == "ambiguous"


def test_pressure_needs_persistence_and_one_frame_does_not_count():
    seq = []
    for k in range(6):
        p = base_positions()
        p["blue_05"] = (20, 24)
        p["red_09"] = (20 + (1.5 if k == 5 else 12), 24)  # appears next to the carrier only on the last frame
        seq.append(p)
    buf = buffer(seq)
    assert infer_pressure(buf, 5, "blue", "red", [[]] * 6, CFG) == []
    seq = []
    for k in range(6):
        p = base_positions()
        p["blue_05"] = (20, 24)
        p["red_09"] = (21.5, 24)  # sustained contact distance
        seq.append(p)
    buf = buffer(seq)
    press = infer_pressure(buf, 5, "blue", "red", [[]] * 6, CFG)
    assert press and buf.pids[press[0].presser] == "red_09"


def test_marking_follow_needs_movement_not_just_proximity():
    stationary, moving, vs, vm = [], [], [], []
    for k in range(16):
        p = base_positions()
        p["blue_08"] = (40, 22)
        p["red_10"] = (42, 22)
        stationary.append(p)
        vs.append({})
        q = base_positions()
        x = 40 + 0.8 * k
        q["blue_08"] = (x, 22)
        q["red_10"] = (x + 2.0 - (0.8 if k else 0.0), 22)  # follows one frame late
        moving.append(q)
        vm.append({"blue_08": (4.0, 0.0), "red_10": (4.0 if k else 0.0, 0.0)})
    mt = MarkingTracker(CFG)
    buf = buffer(stationary, vs)
    for k in range(16):
        edges = mt.update(buf, k, "red", "blue")
    e = next(e for e in edges if buf.pids[e.attacker] == "blue_08")
    assert e.status == "uncertain" and e.follow_support <= 0.35
    mt = MarkingTracker(CFG)
    buf = buffer(moving, vm)
    for k in range(16):
        edges = mt.update(buf, k, "red", "blue")
    e = next(e for e in edges if buf.pids[e.attacker] == "blue_08")
    assert e.status == "ok" and e.follow_support >= 0.6
