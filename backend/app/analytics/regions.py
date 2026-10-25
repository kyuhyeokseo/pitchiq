"""Fixed central analysis region and local counts (spec 8.4).

The region is ``[u_ball + 5, u_ball + 25] x [20, 48]`` in the attacking
frame, clipped to the pitch. Counts are the ids of outfield players inside
the polygon at the same frame (goalkeepers excluded); the number shown is
always derived from the id lists. A free receiver additionally needs an
eligible open lane, a reception-time advantage, weak marking and space.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config
from .lanes import Lane
from .marking import MarkEdge
from .observe import ObservationBuffer, to_world


@dataclass(frozen=True)
class Region:
    polygon_uv: tuple[tuple[float, float], ...]
    polygon_world: tuple[tuple[float, float], ...]
    attackers: tuple[int, ...]
    defenders: tuple[int, ...]
    accessible: tuple[int, ...]  # free receivers inside the region
    receiver_checks: dict  # attacker -> dict of the gate values (for evidence)
    free: tuple[int, ...] = ()  # free receivers anywhere ahead of the ball (the claim may name these)


def central_region(buf: ObservationBuffer, k: int, attacking: str, defending: str, lanes: list[Lane],
                   edges: list[MarkEdge], cfg: Config) -> Region | None:
    c = cfg.regions
    if buf.ball_state[k] in ("dead",):
        return None
    ball_u = float(buf.ball_uv(k, attacking)[0])
    u0, u1 = min(ball_u + c.ahead_from_m, 105.0), min(ball_u + c.ahead_to_m, 105.0)
    if u1 - u0 < 5.0:
        return None
    poly = ((u0, c.v_min_m), (u1, c.v_min_m), (u1, c.v_max_m), (u0, c.v_max_m))
    uv = buf.uv(k, attacking)

    def inside(i: int) -> bool:
        return u0 <= uv[i, 0] <= u1 and c.v_min_m <= uv[i, 1] <= c.v_max_m

    att = tuple(int(i) for i in buf.members(attacking) if inside(i))
    dfn = tuple(int(i) for i in buf.members(defending) if inside(i))
    lane_by = {lane.receiver: lane for lane in lanes}
    mark_by = {e.attacker: e for e in edges}
    w = buf.window(k, cfg.window.pressure_s)
    checks, free = {}, []
    for a in (int(i) for i in buf.members(attacking)):  # outfield only: the goalkeeper is never a "free man"
        lane = lane_by.get(a)
        if lane is None or uv[a, 0] < ball_u - 3.0:  # no lane, or a pass backwards
            continue
        if lane.side != "central":  # wide receivers are reported as wide outlets, not as a central free man
            continue
        e = mark_by.get(a)
        near = min(float(np.min(np.linalg.norm(buf.pos[j][buf.members(defending)] - buf.pos[j][a], axis=1)))
                   for j in w)
        ch = {"lane": lane.availability, "eligible": lane.eligibility, "receiver_margin_s": lane.reception_margin_s,
              "marking_support": 0.0 if e is None else e.follow_support, "nearest_defender_m": near}
        checks[a] = ch
        if (lane.availability == "open" and lane.eligibility == "onside_geometry"
                and lane.reception_margin_s >= c.receiver_margin_s and ch["marking_support"] < c.low_marking_support
                and near >= c.nearest_defender_m):
            free.append(a)
    acc = [a for a in free if a in att]
    world = to_world(np.array(poly), buf.direction(attacking))
    return Region(poly, tuple((float(x), float(y)) for x, y in world), att, dfn, tuple(acc), checks, tuple(free))
