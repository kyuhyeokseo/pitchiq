"""Pressure on the ball carrier and approach direction (spec 7.3).

The approach arrow is the presser's OBSERVED velocity, not a body orientation.
A funnel is only inferred when, over the funnel window, central options are
mostly screened, one outside exit stays open, and the presser keeps the
matching position (on the carrier's central side).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config
from .lanes import Lane
from .observe import ObservationBuffer


@dataclass(frozen=True)
class Press:
    presser: int
    carrier: int
    since_k: int
    distance_m: float
    closing_mps: float
    approach: tuple[float, float]  # world velocity of the presser
    funnel: str  # left | right | none | uncertain
    likely_outlets: tuple[int, ...]
    support: float


def infer_pressure(buf: ObservationBuffer, k: int, attacking: str, defending: str, lane_hist: list[list[Lane]],
                   cfg: Config) -> list[Press]:
    c = cfg.pressure
    carrier = buf.owner[k]
    if carrier < 0 or buf.team[carrier] != attacking:
        return []
    hz = buf.meta.tracking_hz
    w = [j for j in buf.window(k, cfg.window.pressure_s) if buf.owner[j] == carrier]
    if len(w) < 2:
        return []
    need = max(2, int(round(c.gate_duration_s * hz)))
    out = []
    for d in buf.members(defending):
        dist = np.array([np.linalg.norm(buf.pos[j][d] - buf.pos[j][carrier]) for j in w])
        closing = -np.diff(dist) * hz  # m/s, positive when closing in
        approach_ok = (dist[1:] <= c.gate_distance_m) & (closing >= c.gate_closing_mps)
        contact_ok = dist[1:] <= c.contact_distance_m
        ok = approach_ok | contact_ok
        streak = 0
        for v in ok[::-1]:
            if not v:
                break
            streak += 1
        if streak < need or dist[-1] > c.gate_distance_m:
            continue
        cl = float(closing[-need:].mean())
        support = float(np.clip(0.55 * (1.0 - dist[-1] / c.gate_distance_m) + 0.45 * min(max(cl, 0.0) / 4.0, 1.0)
                                + (0.25 if dist[-1] <= c.contact_distance_m else 0.0), 0.0, 1.0))
        out.append(Press(int(d), int(carrier), int(w[-1 - streak]) if streak < len(w) else int(w[0]),
                         float(dist[-1]), cl, (float(buf.vel[k][d, 0]), float(buf.vel[k][d, 1])), "uncertain", (),
                         support))
    if not out:
        return []
    out.sort(key=lambda p: p.distance_m)
    primary = out[0]
    funnel, outlets = _funnel(buf, k, attacking, primary, lane_hist, cfg)
    return [Press(primary.presser, primary.carrier, primary.since_k, primary.distance_m, primary.closing_mps,
                  primary.approach, funnel, outlets, primary.support)] + out[1:]


def _funnel(buf: ObservationBuffer, k: int, attacking: str, press: Press, lane_hist: list[list[Lane]],
            cfg: Config) -> tuple[str, tuple[int, ...]]:
    n = int(round(cfg.window.funnel_s * buf.meta.tracking_hz))
    hist = [lanes for lanes in lane_hist[-n:] if lanes]
    if len(hist) < n // 2:
        return "uncertain", ()
    def frac(side: str, avail: str) -> float:
        vals = []
        for lanes in hist:
            s = [lane for lane in lanes if lane.side == side and lane.eligibility != "offside_geometry"]
            if s:
                vals.append(np.mean([lane.availability == avail for lane in s]))
        return float(np.mean(vals)) if len(vals) >= len(hist) // 2 else 0.0
    central_closed = frac("central", "screened") >= 0.6
    left_open, right_open = frac("left", "open") >= 0.6, frac("right", "open") >= 0.6
    uv = buf.uv(k, attacking)
    presser_central_side = abs(uv[press.presser, 1] - 34.0) < abs(uv[press.carrier, 1] - 34.0)
    current = lane_hist[-1] if lane_hist else []
    if central_closed and presser_central_side and left_open != right_open:
        side = "left" if left_open else "right"
        return side, tuple(lane.receiver for lane in current if lane.side == side and lane.availability == "open")
    if not central_closed and not (left_open or right_open):
        return "none", ()
    return "uncertain", tuple(lane.receiver for lane in current if lane.availability == "open")
