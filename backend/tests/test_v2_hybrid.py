"""Hybrid generator (learned movement around the scenario controller): determinism, physical bounds, ball/event
consistency and offside at pass release. Needs the optional torch install; skipped without it. A seeded, untrained
model is enough here: these tests check the integration, not the learned behaviour."""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from app.simulator.defense import generate as G  # noqa: E402
from app.simulator.defense.engine import key_index  # noqa: E402
from app.simulator.defense.hybrid import OFFSIDE_WAIT_S, HybridSimulation  # noqa: E402
from app.simulator.defense.scenarios import family_a  # noqa: E402
from app.simulator.learned.model import MotionGNN  # noqa: E402

CFG = {"clip_id": "clip_h01", "family": "A", "variant": "main", "seed": 7, "attacking_team": "blue",
       "attack_direction": 1, "start_s": 0.0, "engine": "hybrid"}


@pytest.fixture(autouse=True)
def seeded_model(monkeypatch):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    m = MotionGNN()
    m.eval()
    monkeypatch.setattr(G, "_MODEL", m)


def _xy(rows):
    return np.array([[(p["position"]["x_m"], p["position"]["y_m"]) for p in r["players"]] for r in rows])


def test_hybrid_is_reproducible_bounded_and_marked_in_truth():
    _, rows1, evs1, truth = G.generate_clip(7, CFG)
    _, rows2, evs2, _ = G.generate_clip(7, CFG)
    assert rows1 == rows2 and evs1 == evs2
    assert truth["engine"] == "hybrid"
    xy = _xy(rows1)
    assert (xy[..., 0] >= 0).all() and (xy[..., 0] <= 105).all() and (xy[..., 1] >= 0).all() and (xy[..., 1] <= 68).all()
    speed = np.linalg.norm(np.diff(xy, axis=0), axis=-1) / 0.2
    assert speed.max() <= 10.0 + 0.1  # published positions are rounded to 1 cm


def test_hybrid_ball_and_events_stay_consistent():
    _, rows, evs, _ = G.generate_clip(7, CFG)
    by_id = {e["event_id"]: e for e in evs}
    for e in evs:
        if e["kind"] in ("pass_receive", "interception") and e["related_event_id"]:
            assert by_id[e["related_event_id"]]["event_time_s"] < e["event_time_s"]
    for r in rows:  # a controlled ball is at its owner's feet
        if r["ball_state"] == "controlled":
            own = next(p for p in r["players"] if p["player_id"] == r["ball"]["owner_id"])
            d = np.hypot(own["position"]["x_m"] - r["ball"]["position"]["x_m"],
                         own["position"]["y_m"] - r["ball"]["position"]["y_m"])
            assert d < 1.0


def test_a_pass_waits_for_its_receiver_to_be_onside():
    plan, _ = family_a(np.random.default_rng(1), np.random.default_rng(2), "main")
    sim = HybridSimulation(plan, np.random.default_rng(3), G._MODEL)
    r = key_index("A:ST")
    sim.pos[11:, 0] = 60.0  # the defending line at u = 60
    sim.pos[r] = (70.0, 30.0)  # the receiver 10 m beyond it, ball far behind
    sim.owner, sim.ball = key_index("A:RCB"), np.array([30.0, 34.0])
    cfg = {"kind": "pass", "from": "A:RCB", "to": "A:ST"}
    assert sim._offside(r)
    assert not sim._trigger_ok(cfg)  # held while he is offside
    sim.k += int(round(OFFSIDE_WAIT_S / 0.2))
    assert sim._trigger_ok(cfg) and sim.markers["offside_releases"] == 1  # released, but flagged
    sim.pos[r] = (58.0, 30.0)
    assert not sim._offside(r)


def test_a_goal_conditioned_stochastic_model_runs_reproducibly_within_bounds(monkeypatch):
    torch.manual_seed(1)
    m = MotionGNN(stochastic=True, goal=True)
    m.eval()
    monkeypatch.setattr(G, "_MODEL", m)
    _, rows1, evs1, _ = G.generate_clip(7, CFG)
    _, rows2, evs2, _ = G.generate_clip(7, CFG)
    assert rows1 == rows2 and evs1 == evs2
    xy = _xy(rows1)
    assert (xy[..., 0] >= 0).all() and (xy[..., 0] <= 105).all() and (xy[..., 1] >= 0).all() and (xy[..., 1] <= 68).all()
    assert (np.linalg.norm(np.diff(xy, axis=0), axis=-1) / 0.2).max() <= 10.0 + 0.1


def test_hybrid_goals_point_at_the_controller_target_within_reach():
    from app.simulator.defense.hybrid import GOAL_PACE, GOAL_S_MAX, GOAL_S_MIN

    plan, _ = family_a(np.random.default_rng(1), np.random.default_rng(2), "main")
    sim = HybridSimulation(plan, np.random.default_rng(3), G._MODEL)
    tgt = sim.pos.copy()
    tgt[1] += (0.5, 0.0)  # close: the shortest horizon
    tgt[2] += (0.0, 40.0)  # far: cut to what the pace reaches in the longest horizon
    cap = np.full(len(tgt), 4.0)
    g = sim._goals(tgt, cap)
    assert np.allclose(g[1], [*tgt[1], GOAL_S_MIN, 1.0])
    assert g[2, 2] == GOAL_S_MAX and np.isclose(np.linalg.norm(g[2, :2] - sim.pos[2]), GOAL_PACE * 4.0 * GOAL_S_MAX)


def test_training_goals_come_from_frames_that_exist():
    from app.simulator.learned.dataset import HIST
    from app.simulator.learned.train import draw_goals

    T = HIST + 20
    xf = torch.arange(T, dtype=torch.float32).view(1, 1, T, 1).expand(3, 23, T, 2).clone()  # x = y = frame index
    avail = torch.tensor([20, 7, 3])
    g = draw_goals(xf, avail, 5, 20, 0.0, steps=3, generator=torch.Generator().manual_seed(0))
    h = g[..., 0, 0] - (HIST - 1)  # frames after the last history step
    assert (g[:2, :, :, 3] == 1).all() and (g[2] == 0).all()  # fewer than 5 later frames: no goal
    assert (h[0] >= 5).all() and (h[0] <= 20).all() and (h[1] >= 5).all() and (h[1] <= 7).all()
    assert torch.allclose(g[:2, :, 0, 2], h[:2] * 0.2) and torch.allclose(g[:2, :, 2, 2], (h[:2] - 2) * 0.2)
