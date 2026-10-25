"""Tactical Significance Engine: is a detected run worth a broadcast graphic?

Everything is measured from OBSERVABLE tracking before/after the run. This is
observable spatial association, not causal impact: we report "as the fullback
advanced, the opposing fullback-centre-back gap grew by X m", never "the run
caused ...". Normalisations and weights are product heuristics, not
statistically validated football value.

Windows (frame indices from the Detection):
  before = [k_start - 3, k_start]                 (run start)
  after  = [k_cross - 5, k_eval]                  (around the crossing until evaluation)
  k_eval = k_detect + EVAL_DELAY_S (clipped to the clip) - the engine waits a
           short, fixed delay for consequences and never reads past k_eval.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from ..ingestion.loader import ClipData
from ..schemas import MatchEvent, SignificanceResult
from .geometry import FORWARD_GAIN_M, open_forward_lanes, opposite_side_role, width, x_att
from .patterns import Detection

EVAL_DELAY_S = 2.0
BEFORE_FRAMES = 3
AFTER_LEAD_FRAMES = 5
GAP_FULL_M = 4.0  # widening that saturates the spacing signal
GAP_GAIN_M = 2.5  # minimum widening counted as an observable spatial gain
BALL_NEAR_M, BALL_FAR_M = 8.0, 30.0
COORD_NEAR_M, COORD_FAR_M = 6.0, 20.0
CHANNEL_DEPTH_M = 8.0
LANE_PERSIST_FRAMES = 3  # 0.6 s
FLANK_BALL_RADIUS_M = 15.0  # lanes count only while the ball is this close to the winger
WEIGHTS = {"spacing": 0.40, "lanes": 0.20, "ball_relevance": 0.20, "coordination": 0.10, "detector_strength": 0.10}
SUGGEST_THRESHOLD = 0.55
MIN_DETECTOR_STRENGTH = 0.35
MIN_BALL_RELEVANCE = 0.3
COOLDOWN_NOVELTY = 0.3


def _ramp(v: float, good: float, bad: float) -> float:
    """1 at/below ``good``, 0 at/above ``bad`` (linear in between)."""
    return float(np.clip((bad - v) / (bad - good), 0.0, 1.0))


@dataclass
class Measurement:
    """Raw observable facts for one detection (all metres / counts / frame indices)."""

    attacking_team: str
    defending_fullback: str
    defending_centreback: str
    gap_before_m: float
    gap_after_m: float
    k_gap_before: int
    k_gap_after: int
    new_lane_receivers: list[str]
    k_new_lane: int | None
    lanes_before: int
    lanes_after: int
    ball_distance_m: float
    actor_distance_m: float
    k_eval: int

    @property
    def gap_delta_m(self) -> float:
        return self.gap_after_m - self.gap_before_m


def eval_frame(det: Detection, clip_len: int) -> int:
    return min(det.k_detect + int(round(EVAL_DELAY_S / 0.2)), clip_len - 1)


def measure(det: Detection, clip: ClipData, k_eval: int, events: Sequence[MatchEvent] = ()) -> Measurement:
    """Compute before/after facts using frames and events at or before frame k_eval only."""
    c = clip.until(k_eval)
    pair = det.pair
    team = pair.team_id
    opp = next(t for t in c.teams() if t != team)
    direction = c.attack_direction[team]
    ks, kc = det.k_start, det.k_cross

    dfb = c.find(opp, opposite_side_role(c.roles[pair.fullback]))
    cbs = [j for j in (c.find(opp, "LCB"), c.find(opp, "RCB")) if j is not None]
    if dfb is None or not cbs:
        raise ValueError("defending fullback / centre-backs not found in lineup")
    dcb = min(cbs, key=lambda j: float(np.linalg.norm(c.pos[ks, j] - c.pos[ks, dfb])))
    gap = np.linalg.norm(c.pos[:, dfb] - c.pos[:, dcb], axis=1)
    b_lo = max(0, ks - BEFORE_FRAMES)
    a_lo = max(ks, kc - AFTER_LEAD_FRAMES)
    gap_before = float(gap[b_lo:ks + 1].mean())
    k_gap_after = a_lo + int(np.argmax(gap[a_lo:k_eval + 1]))

    mates = [i for i in range(22) if c.team_ids[i] == team and c.roles[i] != "GK"]
    opps = [i for i in range(22) if c.team_ids[i] == opp]

    def in_channel(k: int, i: int) -> bool:
        lo, hi = sorted((float(width(c.pos[k, dfb, 1])), float(width(c.pos[k, dcb, 1]))))
        line_x = min(x_att(c.pos[k, dfb, 0], direction), x_att(c.pos[k, dcb, 0], direction))
        return lo - 1.0 <= width(c.pos[k, i, 1]) <= hi + 1.0 and x_att(c.pos[k, i, 0], direction) >= line_x - CHANNEL_DEPTH_M

    def option_lanes(k: int) -> set[int]:
        """Open forward lanes to the runner or to a teammate in the fullback-centre-back channel,
        counted only while the ball is in the flank move (near the winger)."""
        if c.possession[k] != team or np.linalg.norm(c.ball[k] - c.pos[k, pair.winger]) > FLANK_BALL_RADIUS_M:
            return set()
        # The pair's own winger is the usual passer, not an option created by the run.
        cand = [i for i in mates if i != c.owner[k] and i != pair.winger]
        open_ = {cand[j] for j in open_forward_lanes(c.ball[k], c.pos[k, cand], c.pos[k, opps], direction)}
        return {i for i in open_ if i == pair.fullback or in_channel(k, i)}

    before: set[int] = set()
    for k in range(b_lo, ks + 1):
        before |= option_lanes(k)
    # A new option counts only if its lane stays open for LANE_PERSIST_FRAMES
    # consecutive frames (long enough to be usable), and was not open before.
    after: set[int] = set()
    k_new = None
    streak: dict[int, int] = {}
    for k in range(a_lo, k_eval + 1):
        now = option_lanes(k)
        streak = {i: streak.get(i, 0) + 1 for i in now}
        for i, n in streak.items():
            if n >= LANE_PERSIST_FRAMES and i not in before and i not in after:
                after.add(i)
                if k_new is None:
                    k_new = k - LANE_PERSIST_FRAMES + 1
    # A forward pass from the winger to the runner / channel that was actually
    # played (public event stream) shows that option was available too.
    t_lo, t_hi = float(c.t[a_lo]), float(c.t[k_eval])
    winger_id = c.player_ids[pair.winger]
    for e in events:
        if (e.clip_id != c.clip_id or e.event_type != "pass" or e.actor_id != winger_id
                or e.target_id is None or not t_lo <= e.match_time_s <= t_hi):
            continue
        k = int(np.argmin(np.abs(c.t - e.match_time_s)))
        r = c.idx(e.target_id)
        gains = x_att(c.pos[k, r, 0], direction) >= x_att(c.ball[k, 0], direction) + FORWARD_GAIN_M
        if gains and (r == pair.fullback or in_channel(k, r)) and r not in before:
            after.add(r)
            k_new = k if k_new is None else min(k_new, k)

    mid = 0.5 * (c.pos[ks:kc + 1, pair.fullback] + c.pos[ks:kc + 1, pair.winger])
    return Measurement(
        attacking_team=team,
        defending_fullback=c.player_ids[dfb],
        defending_centreback=c.player_ids[dcb],
        gap_before_m=round(gap_before, 2),
        gap_after_m=round(float(gap[k_gap_after]), 2),
        k_gap_before=ks,
        k_gap_after=k_gap_after,
        new_lane_receivers=sorted(c.player_ids[i] for i in after),
        k_new_lane=k_new,
        lanes_before=len(before),
        lanes_after=len(before | after),
        ball_distance_m=round(float(np.linalg.norm(c.ball[ks:kc + 1] - mid, axis=1).mean()), 2),
        actor_distance_m=round(float(np.linalg.norm(c.pos[kc, pair.fullback] - c.pos[kc, pair.winger])), 2),
        k_eval=k_eval,
    )


def score(det: Detection, m: Measurement, novelty: float = 1.0) -> SignificanceResult:
    signals = {
        "spacing": float(np.clip(m.gap_delta_m / GAP_FULL_M, 0.0, 1.0)),
        "lanes": 1.0 if m.new_lane_receivers else 0.0,
        "ball_relevance": _ramp(m.ball_distance_m, BALL_NEAR_M, BALL_FAR_M),
        "coordination": _ramp(m.actor_distance_m, COORD_NEAR_M, COORD_FAR_M),
        "detector_strength": det.prediction.confidence,
    }
    contributions = {k: round(WEIGHTS[k] * v, 4) for k, v in signals.items()}
    total = round(float(sum(contributions.values())) * novelty, 4)

    reasons = []
    spatial_gain = m.gap_delta_m >= GAP_GAIN_M or bool(m.new_lane_receivers)
    reasons.append("DEFENDER_GAP_WIDENED" if m.gap_delta_m >= GAP_GAIN_M else "DEFENDER_GAP_HELD")
    reasons.append("NEW_OPTION_LANE" if m.new_lane_receivers else "NO_NEW_OPTION_LANE")
    reasons.append("BALL_NEAR" if signals["ball_relevance"] >= MIN_BALL_RELEVANCE else "BALL_FAR")
    pattern_ok = det.prediction.pattern in ("overlap", "underlap")
    if not pattern_ok:
        reasons.append("PATTERN_UNCERTAIN")
    if det.prediction.confidence < MIN_DETECTOR_STRENGTH:
        reasons.append("LOW_DETECTOR_STRENGTH")
    if novelty < 1.0:
        reasons.append("COOLDOWN_SIMILAR_STORY")
    if not spatial_gain:
        reasons.append("NO_OBSERVABLE_SPATIAL_GAIN")
    should = (
        pattern_ok and spatial_gain and total >= SUGGEST_THRESHOLD and novelty >= 1.0
        and det.prediction.confidence >= MIN_DETECTOR_STRENGTH and signals["ball_relevance"] >= MIN_BALL_RELEVANCE
    )
    metrics: dict[str, float | None] = {
        "defender_gap_before_m": m.gap_before_m,
        "defender_gap_after_m": m.gap_after_m,
        "defender_gap_delta_m": round(m.gap_delta_m, 2),
        "option_lanes_before": float(m.lanes_before),
        "option_lanes_after": float(m.lanes_after),
        "new_option_lanes": float(len(m.new_lane_receivers)),
        "ball_distance_m": m.ball_distance_m,
        "actor_distance_m": m.actor_distance_m,
        "detector_strength": det.prediction.confidence,
        "novelty": novelty,
        **{f"signal_{k}": round(v, 3) for k, v in signals.items()},
    }
    return SignificanceResult(
        prediction_id=det.prediction.prediction_id,
        metrics=metrics,
        contributions=contributions,
        score=float(np.clip(total, 0.0, 1.0)),
        reason_codes=reasons,
        should_suggest=bool(should),
    )


@dataclass
class InsightSelector:
    """Session-level novelty cooldown and suggestion rate limit (match-time based)."""

    cooldown_s: float = 45.0  # same team + flank + pattern story
    min_interval_s: float = 20.0  # between any two suggestions
    exceptional_score: float = 0.85  # may break the interval limit (not the cooldown)
    history: list[tuple[float, str, str, str]] = field(default_factory=list)

    def novelty(self, t: float, team: str, side: str, pattern: str) -> float:
        for (t0, tm, sd, pt) in self.history:
            if tm == team and sd == side and pt == pattern and 0.0 <= t - t0 < self.cooldown_s:
                return COOLDOWN_NOVELTY
        return 1.0

    def admit(self, t: float, sig: SignificanceResult) -> SignificanceResult:
        if not sig.should_suggest:
            return sig
        recent = [t0 for (t0, *_rest) in self.history if 0.0 <= t - t0 < self.min_interval_s]
        if recent and sig.score < self.exceptional_score:
            return sig.model_copy(update={"should_suggest": False,
                                          "reason_codes": [*sig.reason_codes, "RATE_LIMITED"]})
        return sig

    def record(self, t: float, team: str, side: str, pattern: str) -> None:
        self.history.append((t, team, side, pattern))

    def reset(self) -> None:
        self.history.clear()
