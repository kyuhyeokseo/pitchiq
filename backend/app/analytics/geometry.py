"""Attack-direction and flank-invariant geometry helpers (metres)."""

from __future__ import annotations

import numpy as np

PITCH_L, PITCH_W, CENTER_Y = 105.0, 68.0, 34.0

# Heuristic passing-lane definition (development thresholds, not a pass-success model).
LANE_BLOCK_RADIUS_M = 1.8
LANE_MIN_LENGTH_M = 4.0
FORWARD_GAIN_M = 3.0


def x_att(x: np.ndarray | float, direction: int) -> np.ndarray | float:
    """Longitudinal progress toward the opponent goal for a team attacking in ``direction``."""
    return x if direction == 1 else PITCH_L - x


def width(y: np.ndarray | float) -> np.ndarray | float:
    """Distance from the centre line: larger means wider (more 'outside')."""
    return np.abs(np.asarray(y) - CENTER_Y)


def flank_of(role: str) -> str | None:
    """'right'/'left' for side-specific nominal roles, None for central roles."""
    if role in ("RB", "RW", "RCB", "RCM"):
        return "right"
    if role in ("LB", "LW", "LCB", "LCM"):
        return "left"
    return None


def opposite_side_role(role: str) -> str:
    """The opponent role that faces ``role`` on the pitch (their left faces our right)."""
    swap = {"R": "L", "L": "R"}
    return swap[role[0]] + role[1:] if role[0] in swap else role


def point_segment_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray, s_min: float = 0.05) -> np.ndarray:
    """Distance from points p (N,2) to segment a->b, ignoring the first s_min of the segment."""
    ab = b - a
    L2 = float(ab @ ab)
    if L2 < 1e-12:
        return np.linalg.norm(p - a, axis=-1)
    s = np.clip(((p - a) @ ab) / L2, s_min, 1.0)
    return np.linalg.norm(a + s[..., None] * ab - p, axis=-1)


def open_forward_lanes(origin: np.ndarray, teammates: np.ndarray, opponents: np.ndarray, direction: int) -> list[int]:
    """Indices (into ``teammates``) reachable along an unobstructed straight lane that gains ground.

    A lane is open when no opponent is within LANE_BLOCK_RADIUS_M of the segment
    (excluding the first 5% next to the passer) and the receiver is at least
    LANE_MIN_LENGTH_M away and FORWARD_GAIN_M further forward.
    """
    out = []
    ox = x_att(origin[0], direction)
    for j, q in enumerate(teammates):
        if np.linalg.norm(q - origin) < LANE_MIN_LENGTH_M or x_att(q[0], direction) < ox + FORWARD_GAIN_M:
            continue
        if point_segment_distance(opponents, origin, q).min() >= LANE_BLOCK_RADIUS_M:
            out.append(j)
    return out
