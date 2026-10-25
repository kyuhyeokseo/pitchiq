"""Hand-built clips for detector/significance unit tests (independent of the generator).

Scenes are written for "blue attacks +x on its right flank (low y)" and can be
mirrored to the left flank and/or rotated to attack -x. Unlisted players stand
still in a plausible 4-3-3 vs 4-3-3 layout.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from app.ingestion.loader import ClipData
from app.simulator.roster import MIRROR_ROLE, ROLES, player_id

BLUE = {"GK": (10, 34), "RB": (50, 8), "RCB": (40, 26), "LCB": (40, 42), "LB": (50, 60), "DM": (50, 34),
        "RCM": (60, 24), "LCM": (60, 44), "RW": (75, 10), "ST": (80, 34), "LW": (75, 60)}
RED = {"GK": (100, 34), "LB": (86, 12), "LCB": (88, 24), "RCB": (88, 40), "RB": (86, 56), "DM": (80, 34),
       "LCM": (76, 24), "RCM": (76, 44), "LW": (70, 16), "ST": (62, 34), "RW": (70, 52)}

Traj = Callable[[float], tuple[float, float]]


def lerp(a: tuple[float, float], b: tuple[float, float], t0: float, t1: float) -> Traj:
    def f(t: float) -> tuple[float, float]:
        u = min(1.0, max(0.0, (t - t0) / (t1 - t0)))
        return (a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1]))
    return f


def chain(*segs: tuple[float, Traj]) -> Traj:
    """Piecewise trajectory: [(t_end, traj), ...]."""
    def f(t: float) -> tuple[float, float]:
        for t_end, g in segs:
            if t <= t_end:
                return g(t)
        return segs[-1][1](t)
    return f


def make_clip(moves: dict[tuple[str, str], Traj], *, seconds: float = 14.0, mirror: bool = False,
              direction: int = 1, ball_holder: tuple[str, str] | None = ("blue", "RW"),
              ball_at: Traj | None = None, possession: str | None = "blue", clip_id: str = "t01") -> ClipData:
    T = int(round(seconds / 0.2)) + 1
    t = np.arange(T) * 0.2
    pids, teams, roles = [], [], []
    pos = np.zeros((T, 22, 2))
    i = 0
    for team, base in (("blue", BLUE), ("red", RED)):
        for role in ROLES:
            canon = role
            actual = MIRROR_ROLE[role] if mirror else role
            pids.append(player_id(team, actual))
            teams.append(team)
            roles.append(actual)
            traj = moves.get((team, canon))
            for k in range(T):
                pos[k, i] = traj(t[k]) if traj else base[canon]
            i += 1
    canon_index = {(teams[j], (MIRROR_ROLE[roles[j]] if mirror else roles[j])): j for j in range(22)}
    ball = np.zeros((T, 2))
    owner = np.full(T, -1)
    for k in range(T):
        if ball_at is not None:
            ball[k] = ball_at(t[k])
        elif ball_holder is not None:
            j = canon_index[ball_holder]
            ball[k] = pos[k, j] + (0.5, 0.0)
            owner[k] = j
    if mirror:
        pos[..., 1] = 68.0 - pos[..., 1]
        ball[:, 1] = 68.0 - ball[:, 1]
    if direction == -1:
        pos = np.stack([105.0 - pos[..., 0], 68.0 - pos[..., 1]], axis=-1)
        ball = np.stack([105.0 - ball[:, 0], 68.0 - ball[:, 1]], axis=-1)
    vel = np.diff(pos, axis=0, prepend=pos[:1]) / 0.2
    return ClipData(
        match_id="unit", clip_id=clip_id, period=1,
        attack_direction={"blue": direction, "red": -direction},
        frame_index=np.arange(T), t=1000.0 + t, player_ids=tuple(pids), team_ids=tuple(teams), roles=tuple(roles),
        pos=pos, vel=vel, ball=ball, owner=owner, possession=tuple([possession] * T),
    )


# Canonical moves (blue right flank, attacking +x). Times in clip seconds.
WINGER_HOLD = lerp((75, 10), (78, 14), 2.0, 6.0)


def overlap_moves(lane_y: float = 4.0) -> dict:
    return {("blue", "RB"): lerp((50, 8), (88, lane_y), 3.0, 8.5), ("blue", "RW"): WINGER_HOLD}


def underlap_moves() -> dict:
    return {("blue", "RB"): lerp((50, 10), (88, 20), 3.0, 8.5), ("blue", "RW"): lerp((75, 5), (78, 5), 2.0, 6.0)}
