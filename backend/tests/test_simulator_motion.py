"""Motion realism regressions: what a viewer notices in a replay.

Generator v0.1.0 ran every overlap as a straight line (median 0.14 m off the
chord) and froze ~92% of final-third receivers and wingers within 1 s of the
ball arriving or leaving. These tests keep those fixes from regressing.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.analytics.geometry import x_att
from app.analytics.patterns import detect_clip
from app.ingestion.loader import load_scenario

FREEZE_MPS = 1.5


@pytest.fixture(scope="module")
def scenario(dataset):
    return load_scenario(dataset[0])


def _chord_deviation(path: np.ndarray) -> float:
    ab = path[-1] - path[0]
    n = np.array([-ab[1], ab[0]]) / np.linalg.norm(ab)
    return float(np.abs((path - path[0]) @ n).max())


def _frame(clip, t: float) -> int:
    return int(np.argmin(np.abs(clip.t - t)))


def test_overlap_and_underlap_runs_are_curved_not_straight(scenario):
    devs = [
        _chord_deviation(clip.pos[det.k_start:det.k_detect + 1, det.pair.fullback])
        for clip in scenario.clips.values()
        for det in detect_clip(clip)
        if det.prediction.pattern in ("overlap", "underlap")
    ]
    assert len(devs) >= 10
    assert np.median(devs) >= 1.0, np.round(devs, 2)


def test_final_third_receivers_keep_the_ball_moving(scenario):
    after = []
    for cid, clip in scenario.clips.items():
        recvs = [e for e in scenario.clip_events(cid) if e.event_type == "receive"]
        if not recvs:
            continue
        e = recvs[-1]
        i, k = clip.idx(e.actor_id), _frame(clip, e.match_time_s)
        team = clip.team_ids[i]
        if x_att(clip.pos[k, i, 0], clip.attack_direction[team]) >= 75.0 and k + 5 < len(clip):
            after.append(float(np.linalg.norm(clip.vel[k + 5, i])))
    assert len(after) >= 10
    assert np.mean(np.array(after) < FREEZE_MPS) <= 0.25, np.round(after, 2)


def test_wingers_move_on_after_passing(scenario):
    after = []
    for cid, clip in scenario.clips.items():
        for e in scenario.clip_events(cid):
            i = clip.idx(e.actor_id) if e.actor_id else None
            if e.event_type == "pass" and i is not None and clip.roles[i] in ("RW", "LW"):
                k = _frame(clip, e.match_time_s)
                if k + 5 < len(clip):
                    after.append(float(np.linalg.norm(clip.vel[k + 5, i])))
    assert len(after) >= 10
    assert np.mean(np.array(after) >= FREEZE_MPS) >= 0.9, np.round(after, 2)
