"""Cover for forward runners and isolation (spec 8.3).

A runner is an onside outfield attacker ahead of the ball with observed
forward speed; a stationary attacker is never extrapolated into a run. His
short projected point is his position plus one second of current velocity
(clamped to the pitch). Every defender other than his marker who is not
already behind him (chasing from behind is recovery, not cover), and the
goalkeeper separately, gets an arrival time from the lane model; the cover
margin is cover arrival minus attacker arrival (positive = cover late).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config
from .lanes import eligibility, offside_line_u
from .marking import MarkEdge
from .observe import ObservationBuffer


@dataclass(frozen=True)
class CoverResult:
    attacker: int
    marker: int | None
    marker_edge: MarkEdge | None
    target_uv: tuple[float, float]
    forward_speed_mps: float
    attacker_arrival_s: float
    outfield_cover: int | None
    outfield_arrival_s: float | None
    gk: int
    gk_arrival_s: float
    eligibility: str

    @property
    def outfield_margin_s(self) -> float | None:
        return None if self.outfield_arrival_s is None else self.outfield_arrival_s - self.attacker_arrival_s

    @property
    def gk_margin_s(self) -> float:
        return self.gk_arrival_s - self.attacker_arrival_s

    @property
    def min_margin_s(self) -> float:
        m = self.outfield_margin_s
        return self.gk_margin_s if m is None else min(m, self.gk_margin_s)


def runners(buf: ObservationBuffer, k: int, attacking: str, defending: str, cfg: Config) -> list[tuple[int, str]]:
    c = cfg.cover
    uv = buf.uv(k, attacking)
    vuv = buf.uv_vel(k, attacking)
    ball_u = float(buf.ball_uv(k, attacking)[0])
    line_u = offside_line_u(uv, buf, defending, ball_u)
    out = []
    for a in buf.members(attacking):
        if a == buf.owner[k]:
            continue
        if vuv[a, 0] < c.runner_min_forward_mps:
            continue
        if not (uv[a, 0] >= ball_u + c.runner_min_ahead_of_ball_m and abs(uv[a, 0] - ball_u) <= c.runner_max_from_ball_m):
            continue
        if uv[a, 0] < line_u - c.runner_max_behind_line_m:
            continue
        out.append((int(a), eligibility(float(uv[a, 0]), line_u, cfg.lanes.offside_tolerance_m)))
    return out


def estimate_cover(buf: ObservationBuffer, k: int, attacking: str, defending: str, attacker: int, elig: str,
                   edges: list[MarkEdge], cfg: Config) -> CoverResult:
    c, lc = cfg.cover, cfg.lanes
    uv = buf.uv(k, attacking)
    vuv = buf.uv_vel(k, attacking)
    target = np.clip(uv[attacker] + vuv[attacker] * c.horizon_s, [0.0, 0.0], [105.0, 68.0])
    speed = float(np.linalg.norm(vuv[attacker]))
    att_arr = float(np.linalg.norm(target - uv[attacker]) / speed) if speed > 1e-6 else c.horizon_s
    edge = next((e for e in edges if e.attacker == attacker), None)
    marker = edge.defender if edge is not None else None
    best, best_t = None, None
    for d in buf.members(defending):
        if d == marker:
            continue
        if uv[d, 0] < uv[attacker, 0] - c.goal_side_tolerance_m:  # chasing from behind: recovery, not cover
            continue
        t = lc.reaction_s + float(np.linalg.norm(uv[d] - target)) / lc.defender_speed_mps
        if best_t is None or t < best_t:
            best, best_t = int(d), t
    gk = buf.gk(defending)
    gk_t = lc.reaction_s + float(np.linalg.norm(uv[gk] - target)) / lc.defender_speed_mps
    return CoverResult(attacker, marker, edge, (float(target[0]), float(target[1])), float(vuv[attacker, 0]), att_arr,
                       best, best_t, gk, gk_t, elig)
