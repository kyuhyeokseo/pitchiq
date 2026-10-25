"""Rule-based overlap / underlap detector over observable tracking.

Streaming by construction: ``OverlapDetector.step(k)`` may only read frames
0..k of the clip, so the same code serves offline evaluation and live replay.

Geometry is attack-direction and flank invariant:
  d(t) = x_att(fullback) - x_att(winger)     (>0: fullback ahead of winger)
  o(t) = width(fullback) - width(winger)     (>0: fullback wider / outside)
A detection needs, for a same-side fullback/winger pair of the team in
possession: d <= -ARM_BEHIND_M (fullback behind), then d >= CROSS_AHEAD_M for
PERSIST_FRAMES consecutive frames, after a genuine forward run (progress and
peak speed). The sign of o around the crossing separates overlap (outside) from
underlap (inside); a small |o| is reported as "uncertain".

All thresholds are documented development heuristics, not validated values.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..ingestion.loader import ClipData
from ..schemas import TacticalPrediction
from .geometry import width, x_att

ARM_BEHIND_M = 3.0
CROSS_AHEAD_M = 1.0
PERSIST_FRAMES = 3
MAX_ARM_TO_CROSS_S = 6.0
MAX_RUN_LOOKBACK_S = 8.0
RUN_START_SPEED_MPS = 2.0
MIN_PROGRESS_M = 6.0
MIN_PEAK_SPEED_MPS = 4.5
LATERAL_MARGIN_M = 1.0
MIN_POSSESSION_SHARE = 0.5

PAIRS = {"right": ("RB", "RW"), "left": ("LB", "LW")}


@dataclass(frozen=True)
class PairSpec:
    team_id: str
    side: str
    fullback: int
    winger: int


@dataclass
class Detection:
    prediction: TacticalPrediction
    pair: PairSpec
    k_start: int
    k_cross: int
    k_detect: int
    features: dict[str, float]


@dataclass
class _PairState:
    armed_k: int | None = None
    ahead: int = 0
    latched: bool = False


def clip_pairs(clip: ClipData) -> list[PairSpec]:
    pairs = []
    for team in clip.teams():
        for side, (fb_role, w_role) in PAIRS.items():
            fb, w = clip.find(team, fb_role), clip.find(team, w_role)
            if fb is not None and w is not None:
                pairs.append(PairSpec(team, side, fb, w))
    return pairs


def _clip01(v: float) -> float:
    return float(min(1.0, max(0.0, v)))


@dataclass
class OverlapDetector:
    clip: ClipData
    pairs: list[PairSpec] = field(init=False)
    _state: dict[PairSpec, _PairState] = field(init=False)
    _n: int = field(init=False, default=0)
    _last_k: int = field(init=False, default=-1)

    def __post_init__(self) -> None:
        self.pairs = clip_pairs(self.clip)
        self._state = {p: _PairState() for p in self.pairs}

    def _d_o(self, pair: PairSpec, k: int | slice) -> tuple[np.ndarray, np.ndarray]:
        c = self.clip
        direction = c.attack_direction[pair.team_id]
        f, w = c.pos[k, pair.fullback], c.pos[k, pair.winger]
        d = x_att(f[..., 0], direction) - x_att(w[..., 0], direction)
        o = width(f[..., 1]) - width(w[..., 1])
        return np.asarray(d), np.asarray(o)

    def step(self, k: int) -> list[Detection]:
        """Consume frame k (frames must arrive in order). Reads only frames <= k."""
        if k != self._last_k + 1:
            raise ValueError(f"frames must be fed in order: expected {self._last_k + 1}, got {k}")
        self._last_k = k
        out = []
        t = self.clip.t
        for pair in self.pairs:
            st = self._state[pair]
            d, _ = self._d_o(pair, k)
            d = float(d)
            if d <= -ARM_BEHIND_M:
                st.armed_k, st.ahead, st.latched = k, 0, False
                continue
            if st.latched or st.armed_k is None:
                continue
            if d >= CROSS_AHEAD_M and t[k] - t[st.armed_k] <= MAX_ARM_TO_CROSS_S + PERSIST_FRAMES * 0.2:
                st.ahead += 1
                if st.ahead >= PERSIST_FRAMES:
                    st.latched = True
                    det = self._evaluate(pair, k - PERSIST_FRAMES + 1, k)
                    if det is not None:
                        out.append(det)
            else:
                st.ahead = 0
        return out

    def _evaluate(self, pair: PairSpec, k_cross: int, k: int) -> Detection | None:
        c = self.clip
        direction = c.attack_direction[pair.team_id]
        k_lo = max(0, int(np.searchsorted(c.t, c.t[k_cross] - MAX_RUN_LOOKBACK_S)))
        fx = x_att(c.pos[k_lo:k + 1, pair.fullback, 0], direction)
        vx = np.diff(fx, prepend=fx[0]) / 0.2
        rel_cross = k_cross - k_lo
        slow = np.flatnonzero(vx[: rel_cross + 1] < RUN_START_SPEED_MPS)
        k_start = k_lo + (int(slow[-1]) if slow.size else 0)
        progress = float(x_att(c.pos[k_cross, pair.fullback, 0], direction)
                         - x_att(c.pos[k_start, pair.fullback, 0], direction))
        speed = np.linalg.norm(np.diff(c.pos[k_start:k + 1, pair.fullback], axis=0), axis=1) / 0.2
        peak = float(speed.max()) if speed.size else 0.0
        poss = c.possession[k_start:k + 1]
        share = sum(1 for p in poss if p == pair.team_id) / max(1, len(poss))
        y_f, y_w = c.pos[k_cross, pair.fullback, 1], c.pos[k_cross, pair.winger, 1]
        same_half = (y_f - 34.0) * (y_w - 34.0) > 0
        if progress < MIN_PROGRESS_M or peak < MIN_PEAK_SPEED_MPS or share < MIN_POSSESSION_SHARE or not same_half:
            return None

        d_win, o_win = self._d_o(pair, slice(max(0, k_cross - 2), k + 1))
        lateral = float(o_win.mean())
        if lateral >= LATERAL_MARGIN_M:
            pattern = "overlap"
        elif lateral <= -LATERAL_MARGIN_M:
            pattern = "underlap"
        else:
            pattern = "uncertain"
        # Heuristic strength in [0,1]: how clearly each criterion is exceeded.
        parts = {
            "longitudinal": _clip01(float(d_win.max()) / 6.0),
            "lateral": _clip01(abs(lateral) / 8.0),
            "progress": _clip01(progress / 25.0),
            "speed": _clip01((peak - 4.0) / 5.0),
        }
        confidence = round(float(np.mean(list(parts.values()))), 3)
        self._n += 1
        pred = TacticalPrediction(
            prediction_id=f"{c.clip_id}-p{self._n:02d}",
            match_id=c.match_id,
            clip_id=c.clip_id,
            start_s=round(float(c.t[k_start]), 3),
            end_s=round(float(c.t[k]), 3),
            pattern=pattern,
            actor_player_ids=[c.player_ids[pair.fullback], c.player_ids[pair.winger]],
            confidence=confidence,
            confidence_kind="heuristic_score",
            evidence_frame_indices=[int(c.frame_index[k_start]), int(c.frame_index[k_cross]), int(c.frame_index[k])],
        )
        feats = {"progress_m": round(progress, 2), "peak_speed_mps": round(peak, 2),
                 "lateral_offset_m": round(lateral, 2), "possession_share": round(share, 2),
                 **{f"strength_{k_}": round(v, 3) for k_, v in parts.items()}}
        return Detection(pred, pair, k_start, k_cross, k, feats)


def detect_clip(clip: ClipData) -> list[Detection]:
    det = OverlapDetector(clip)
    out: list[Detection] = []
    for k in range(len(clip)):
        out.extend(det.step(k))
    return out
