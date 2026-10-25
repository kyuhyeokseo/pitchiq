"""Defensive line groups from own-goal depth (spec 7.2).

Depth of a defender is ``d = 105 - u`` in the attacking team's frame. Over
the trailing window each defender's depth is median-smoothed, the ten
outfield depths are split into three contiguous groups by minimum within-group
variance (natural breaks), and each group is checked for spread and for
membership stability across the window. Groups that fail stay out (partial),
and the shape label is only named when all three groups hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np

from .config import Config
from .observe import ObservationBuffer

NAMED = {(4, 4, 2): "approx_442", (4, 3, 3): "approx_433", (3, 2, 5): "approx_325"}
GROUPS = ("back", "mid", "front")


@dataclass(frozen=True)
class LineGroupResult:
    group: str
    idx: tuple[int, ...]
    depth_m: float
    spread_m: float
    support: float


@dataclass(frozen=True)
class LinesResult:
    groups: tuple[LineGroupResult, ...]  # only the groups that passed the gates
    shape_label: str
    full: bool
    window: tuple[int, int]

    def get(self, name: str) -> LineGroupResult | None:
        return next((g for g in self.groups if g.group == name), None)


def _best_split(d: np.ndarray, min_size: int) -> tuple[tuple[int, int], float]:
    n = len(d)
    best, best_cost = None, np.inf
    for i, j in combinations(range(1, n), 2):
        if i < min_size or j - i < min_size or n - j < min_size:
            continue
        cost = sum(float(((g - g.mean()) ** 2).sum()) for g in (d[:i], d[i:j], d[j:]))
        if cost < best_cost:
            best, best_cost = (i, j), cost
    return best, best_cost


def estimate_lines(buf: ObservationBuffer, k: int, defending: str, attacking: str, cfg: Config) -> LinesResult:
    c = cfg.lines
    w = buf.window(k, cfg.window.state_s)
    idx = buf.members(defending)
    depths = np.array([105.0 - buf.uv(j, attacking)[idx, 0] for j in w])  # (W, 10)
    med = np.median(depths, axis=0)
    order = np.argsort(med, kind="stable")
    split, _ = _best_split(med[order], c.min_group_size)
    labels = np.empty(len(idx), dtype=int)
    for gi, sl in enumerate((slice(0, split[0]), slice(split[0], split[1]), slice(split[1], None))):
        labels[order[sl]] = gi
    # membership stability: same split recomputed on every frame of the window
    agree = []
    for row in depths:
        o = np.argsort(row, kind="stable")
        s, _ = _best_split(row[o], c.min_group_size)
        lab = np.empty(len(idx), dtype=int)
        for gi, sl in enumerate((slice(0, s[0]), slice(s[0], s[1]), slice(s[1], None))):
            lab[o[sl]] = gi
        agree.append(lab == labels)
    agree = np.array(agree)
    groups = []
    for gi, name in enumerate(GROUPS):
        members = np.flatnonzero(labels == gi)
        gd = med[members]
        spread = float(gd.max() - gd.min())
        stability = float(agree[:, members].mean())
        if spread <= c.max_spread_m and stability >= c.min_stability:
            support = float(np.clip(stability * (1.0 - 0.5 * spread / c.max_spread_m), 0.0, 1.0))
            groups.append(LineGroupResult(name, tuple(int(idx[m]) for m in members), float(np.median(gd)), spread,
                                          support))
    full = len(groups) == 3
    sizes = tuple(int((labels == gi).sum()) for gi in range(3))
    label = NAMED.get(sizes, "unclassified") if full else "ambiguous"
    return LinesResult(tuple(groups), label, full, (w.start, w.stop - 1))
