"""Short passing-lane accessibility and approximate offside eligibility (spec 8.1, 8.2).

Ground-pass heuristic with fixed speeds: for interior points q of the
carrier->receiver segment (2 m apart, 1 m excluded at both ends)
``ball_time(q) = |q - carrier| / v_ball`` and
``def_time(q) = reaction + |defender - q| / v_def``. The lane margin is
``min(def_time - ball_time)``. Reception competition is checked separately at
the receiver's position. A lane is screened if either margin is <= 0.1 s,
open if both are >= 0.4 s, and uncertain otherwise. This is accessibility of a
ground pass under fixed assumptions, not a pass success probability.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config
from .observe import ObservationBuffer


@dataclass(frozen=True)
class Lane:
    carrier: int
    receiver: int
    length_m: float
    eligibility: str  # onside_geometry | offside_geometry | unknown
    availability: str  # open | screened | uncertain
    interior_margin_s: float | None
    reception_margin_s: float
    min_margin_s: float
    screeners: tuple[int, ...]
    side: str  # central | left | right (attack frame, from the attacker's viewpoint)


def offside_line_u(uv: np.ndarray, buf: ObservationBuffer, defending: str, ball_u: float) -> float:
    """Second-highest defender u (goalkeeper included) or the ball, whichever is further forward."""
    d_all = np.sort(uv[buf.members(defending, outfield=False), 0])
    return float(max(d_all[-2], ball_u))


def eligibility(u: float, line_u: float, tol: float) -> str:
    if u <= 52.5 + 1e-9:
        return "onside_geometry"  # own half
    if u <= line_u - tol:
        return "onside_geometry"
    if u >= line_u + tol:
        return "offside_geometry"
    return "unknown"


def lateral_side(v: float, cfg: Config) -> str:
    if abs(v - 34.0) <= cfg.lanes.central_half_width_m:
        return "central"
    return "left" if v > 34.0 else "right"  # attacking toward +u: higher v is the attacker's left


def lane_margins(start: np.ndarray, end: np.ndarray, defenders: np.ndarray, cfg: Config) -> tuple[
        float | None, float, np.ndarray, np.ndarray]:
    c = cfg.lanes
    seg = end - start
    L = float(np.linalg.norm(seg))
    s_vals = np.arange(c.end_exclusion_m, L - c.end_exclusion_m + 1e-9, c.sample_step_m)
    if len(s_vals) and s_vals[-1] < L - c.end_exclusion_m - 1e-6:
        s_vals = np.append(s_vals, L - c.end_exclusion_m)
    per_def_interior = np.full(len(defenders), np.inf)
    if len(s_vals):
        q = start + np.outer(s_vals / L, seg)  # (S, 2)
        ball_t = s_vals / c.ball_speed_mps
        def_t = c.reaction_s + np.linalg.norm(defenders[:, None, :] - q[None, :, :], axis=-1) / c.defender_speed_mps
        per_def_interior = (def_t - ball_t[None, :]).min(axis=1)
    per_def_recv = c.reaction_s + np.linalg.norm(defenders - end, axis=1) / c.defender_speed_mps - L / c.ball_speed_mps
    interior = float(per_def_interior.min()) if len(s_vals) else None
    return interior, float(per_def_recv.min()), per_def_interior, per_def_recv


def screen_pass_options(buf: ObservationBuffer, k: int, attacking: str, defending: str, cfg: Config) -> list[Lane]:
    c = cfg.lanes
    carrier = buf.owner[k]
    if carrier < 0 or buf.team[carrier] != attacking or buf.ball_state[k] != "controlled":
        return []
    uv = buf.uv(k, attacking)
    ball_uv = buf.ball_uv(k, attacking)
    line_u = offside_line_u(uv, buf, defending, float(ball_uv[0]))
    defenders = buf.members(defending, outfield=False)
    d_uv = uv[defenders]
    # spec 8.1: a defender right at the ball is release pressure (see the pressure relation), not a lane screen
    at_ball = np.linalg.norm(d_uv - ball_uv, axis=1) <= c.release_radius_m
    lanes = []
    for r in buf.members(attacking, outfield=False):
        if r == carrier:
            continue
        # only forward and square passes are options a defence takes away; a pass back toward the carrier's own goal
        # (to a centre-back behind him or the goalkeeper) is not counted, screened or not
        if float(uv[r, 0]) < float(uv[carrier, 0]) - c.max_backward_m:
            continue
        L = float(np.linalg.norm(uv[r] - ball_uv))
        if not (c.min_length_m <= L <= c.max_length_m):
            continue
        interior, recv, per_int, per_recv = lane_margins(ball_uv, uv[r], d_uv, cfg)
        if at_ball.any():
            # a defender at the ball blocks only the lanes inside his shadow (pressing while cutting that lane);
            # for every other lane he is release pressure. No body orientation: an approximate screen.
            # the shadow is measured from the passer's body, not from the ball at his feet: at 1-2 m a
            # half-metre ball offset would swing the angle by tens of degrees
            rel = d_uv - uv[carrier]
            to_r = uv[r] - uv[carrier]
            cos = (rel @ (to_r / max(float(np.linalg.norm(to_r)), 1e-6))) / np.maximum(np.linalg.norm(rel, axis=1), 1e-6)
            in_shadow = cos >= np.cos(np.radians(c.shadow_angle_deg))
            # a presser already standing in the line does not have to react to the pass: without this, a body
            # within ~1.4 m of the ball on the line itself could never screen it
            per_int = np.where(at_ball & in_shadow, per_int - c.reaction_s, per_int)
            per_int = np.where(at_ball & ~in_shadow, np.inf, per_int)
            interior = float(per_int.min()) if np.isfinite(per_int).any() else None
        m = recv if interior is None else min(interior, recv)
        if m <= c.screened_margin_s:
            avail = "screened"
        elif m >= c.open_margin_s:
            avail = "open"
        else:
            avail = "uncertain"
        thr = c.screened_margin_s if avail == "screened" else c.open_margin_s
        scr = tuple(int(defenders[i]) for i in np.flatnonzero(np.minimum(per_int, per_recv) < thr))
        lanes.append(Lane(int(carrier), int(r), L, eligibility(float(uv[r, 0]), line_u, c.offside_tolerance_m), avail,
                          interior, recv, m, scr, lateral_side(float(uv[r, 1]), cfg)))
    return lanes
