"""Man-oriented relations (spec 7.4): assignment with an unmatched option, persistence, switches.

Per frame, over the trailing window, each defender-attacker pair gets a cost
``mean_distance + w * mean_relative_speed (+ switch penalty)``. Pairs whose
mean distance exceeds the gate are not candidates. A linear assignment with
one dummy column per defender lets defenders stay unmatched instead of being
forced onto someone. The ball carrier is excluded: the presser-carrier link
is a pressure edge, not a marking edge.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from .config import Config
from .observe import ObservationBuffer

BIG = 1e3


@dataclass(frozen=True)
class MarkEdge:
    defender: int
    attacker: int
    since_k: int
    mean_distance_m: float
    follow_support: float
    status: str  # ok | uncertain

    def duration_s(self, k: int, hz: int) -> float:
        return (k - self.since_k) / hz


@dataclass(frozen=True)
class ZoneStats:
    zone_opponents: tuple[int, ...]
    supported: tuple[int, ...]  # attackers in the zone with a supported marking edge

    @property
    def coverage(self) -> float | None:
        return len(self.supported) / len(self.zone_opponents) if self.zone_opponents else None


class MarkingTracker:
    """Stateful only through the previous assignment (for switch penalties and ``since``)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.prev: dict[tuple[str, int], tuple[int, int]] = {}  # (team, defender) -> (attacker, since_k)

    def reset(self) -> None:
        self.prev.clear()

    def update(self, buf: ObservationBuffer, k: int, defending: str, attacking: str) -> list[MarkEdge]:
        c = self.cfg.marking
        hz = buf.meta.tracking_hz
        w = buf.window(k, self.cfg.window.state_s)
        D = buf.members(defending)
        A = np.array([a for a in buf.members(attacking) if a != buf.owner[k]])
        P = np.array([buf.pos[j] for j in w])
        V = np.array([buf.vel[j] for j in w])
        dist = np.linalg.norm(P[:, D, None, :] - P[:, None, A, :], axis=-1)  # (W, nD, nA)
        mean_d = dist.mean(axis=0)
        rel = np.linalg.norm(V[:, D, None, :] - V[:, None, A, :], axis=-1).mean(axis=0)
        within = dist <= c.max_mean_distance_m
        run = np.zeros(mean_d.shape, dtype=int)  # consecutive frames within the gate, ending at k
        for f in range(within.shape[0] - 1, -1, -1):
            alive = within[f] & (run == within.shape[0] - 1 - f)
            run = run + alive
        cost = mean_d + c.relative_speed_weight_s * rel
        for di, d in enumerate(D):
            prev = self.prev.get((defending, int(d)))
            if prev is not None:
                cost[di] += np.where(A == prev[0], 0.0, c.switch_penalty_m)
        cost = np.where(mean_d <= c.max_mean_distance_m, cost, BIG)
        aug = np.hstack([cost, np.full((len(D), len(D)), c.unmatched_cost_m)])
        rows, cols = linear_sum_assignment(aug)
        edges: list[MarkEdge] = []
        new_prev: dict[tuple[str, int], tuple[int, int]] = {}
        for di, ai in zip(rows, cols):
            if ai >= len(A) or cost[di, ai] >= BIG:
                continue
            d, a = int(D[di]), int(A[ai])
            prev = self.prev.get((defending, d))
            since = prev[1] if prev is not None and prev[0] == a else k - int(run[di, ai]) + 1
            since = min(max(since, 0), k)  # a pair that is apart on this frame starts no earlier than now
            follow = self._follow(V[:, d], V[:, a], float(mean_d[di, ai]))
            dur = (k - since) / hz
            status = "ok" if dur >= c.min_duration_s and follow >= c.follow_ok_score else "uncertain"
            edges.append(MarkEdge(d, a, since, float(mean_d[di, ai]), follow, status))
            new_prev[(defending, d)] = (a, since)
        self.prev = {key: v for key, v in self.prev.items() if key[0] != defending} | new_prev
        return edges

    def _follow(self, vd: np.ndarray, va: np.ndarray, mean_d: float) -> float:
        """Velocity agreement allowing the defender to react up to ``max_lag`` frames late."""
        c = self.cfg.marking
        sa = np.linalg.norm(va, axis=1)
        closeness = 1.0 - 0.5 * min(mean_d / c.max_mean_distance_m, 1.0)
        if sa.mean() < c.moving_attacker_mps:
            # proximity only: a stationary pair is not evidence of following
            return float(np.clip(0.35 * closeness, 0.0, 1.0))
        best = 0.0
        for lag in range(0, int(c.max_follow_lag_frames) + 1):
            a = va[: len(va) - lag] if lag else va
            d = vd[lag:]
            na, nd = np.linalg.norm(a, axis=1), np.linalg.norm(d, axis=1)
            moving = (na > 0.5) & (nd > 0.3)
            if moving.sum() < 3:
                continue
            cos = (a[moving] * d[moving]).sum(axis=1) / (na[moving] * nd[moving])
            best = max(best, float(np.clip(cos, 0.0, 1.0).mean() * moving.mean() ** 0.5))
        return float(np.clip(best * closeness, 0.0, 1.0))


def zone_stats(buf: ObservationBuffer, k: int, attacking: str, edges: list[MarkEdge], cfg: Config) -> ZoneStats:
    ball = buf.ball[k]
    A = [int(a) for a in buf.members(attacking)
         if a != buf.owner[k] and np.linalg.norm(buf.pos[k][a] - ball) <= cfg.marking.zone_radius_m]
    sup = {e.attacker for e in edges if e.status == "ok"}
    return ZoneStats(tuple(A), tuple(a for a in A if a in sup))
