"""Claims, trade-off episodes, changes and outcomes (spec 9).

Frame claims are checked against the gates in ``analytics_config.yaml`` and
must hold for a sustain period before they count as supported. Supported
claims open an episode keyed by (defending team, claim family); the episode
keeps the latest supported instance of each claim code, so an exposure that
existed is never deleted because of what happened next. Packets are frozen
only at revision points (activation, claim-set change, observed change,
outcome change, close), each with its own cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..contracts import EvidenceMetric, FrameRef, MatchEvent, TacticalChange, TacticalClaim, Window
from ..contracts.registry import CLAIM_DIMENSION
from .config import Config

METHOD = {
    "lanes": "lanes/0.1", "lines": "lines/0.1", "cover": "cover/0.1", "marking": "marking/0.1",
    "pressure": "pressure/0.1", "regions": "regions/0.1", "changes": "changes/0.1",
}
FAMILY_OF = {
    "short_options_screened": "press_vs_depth", "behind_space_exposed": "press_vs_depth",
    "isolated_1v1": "press_vs_depth",
    "behind_depth_reduced": "line_separation", "press_block_disconnected": "line_separation",
    "between_lines_receiver": "line_separation",
    "central_routes_screened": "central_screen", "wide_outlet_available": "central_screen",
    "accessible_free_receiver": "central_screen", "local_central_overload": "central_screen",
    "weak_side_accessible": "central_screen",
}
FORBIDDEN_ALWAYS = ("coach_intent", "causal_effect", "success_probability", "formation_superiority",
                    "future_outcome")


@dataclass
class FrameClaim:
    """A claim condition evaluated at one frame, with its metrics (not yet sustained)."""

    code: str
    epistemic: str
    metrics: list[EvidenceMetric]
    relation_ids: list[str]
    player_ids: list[str]
    support: float
    targets: tuple[str, ...] = ()  # receivers an exploitation would go to (exposed claims)


@dataclass
class Episode:
    episode_id: str
    family: str
    defending: str
    attacking: str
    start_k: int
    lifecycle: str = "candidate"
    revision: int = 0
    detected_k: int | None = None
    last_support_k: int = 0
    claims: dict[str, TacticalClaim] = field(default_factory=dict)  # latest supported instance per code
    metrics: dict[str, EvidenceMetric] = field(default_factory=dict)
    changes: list[TacticalChange] = field(default_factory=list)
    change_metrics: dict[str, EvidenceMetric] = field(default_factory=dict)
    state_ids: list[str] = field(default_factory=list)
    outcome: dict = field(default_factory=lambda: {"status": "not_yet_observed", "event_ids": [],
                                                   "realized_target_id": None})
    event_ids: list[str] = field(default_factory=list)
    suppressed: set[str] = field(default_factory=set)
    baseline: dict = field(default_factory=dict)
    story_priority: float = 0.0
    closed_reason: str | None = None
    anchor_carrier: int = -1
    targets: set[str] = field(default_factory=set)
    change_marks: dict = field(default_factory=dict)
    anchor_ball_uv: tuple[float, float] = (0.0, 0.0)
    passive: bool = False  # nobody pressed the carrier when the story became active
    exposed_first: dict[str, int] = field(default_factory=dict)  # exposed code -> first frame it was claimed

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self.claims))

    def by_dimension(self, dim: str) -> list[str]:
        return sorted(c.claim_id for c in self.claims.values() if c.dimension == dim)

    def analysis_status(self) -> str:
        if self.by_dimension("restricted") and self.by_dimension("exposed"):
            return "complete_tradeoff"
        return "partial" if self.claims else "insufficient_evidence"


def metric(metric_id: str, key: str, value: float | None, unit: str, window: tuple[float, float],
           players: list[str], frames: list[FrameRef], events: list[str] = (), method: str = "lanes",
           status: str | None = None) -> EvidenceMetric:
    if status is None:
        status = "unavailable" if value is None else "ok"
    if value is not None:
        value = round(float(value), 3) if unit != "count" else float(int(value))
    return EvidenceMetric(metric_id=metric_id, key=key, value=value, unit=unit, status=status,
                          window=Window(start_s=round(window[0], 3), end_s=round(window[1], 3)), player_ids=players,
                          frame_refs=frames, event_ids=list(events), method_version=METHOD[method])


def to_claim(fc: FrameClaim, claim_id: str, window: tuple[float, float]) -> TacticalClaim:
    return TacticalClaim(claim_id=claim_id, code=fc.code, epistemic=fc.epistemic, dimension=CLAIM_DIMENSION[fc.code],
                         metric_ids=[m.metric_id for m in fc.metrics], relation_ids=fc.relation_ids,
                         player_ids=fc.player_ids, support_score=round(float(np.clip(fc.support, 0.0, 1.0)), 3),
                         window=Window(start_s=round(window[0], 3), end_s=round(window[1], 3)))


def story_priority(ep: Episode, ball_relevance: float, novelty: float, cfg: Config) -> float:
    s = cfg.story
    support = float(np.mean([c.support_score for c in ep.claims.values()])) if ep.claims else 0.0
    change = 0.0
    for m in ep.change_metrics.values():
        if m.value is not None:
            change = max(change, min(abs(m.value) / 10.0, 1.0))
    v = s.w_support * support + s.w_change * change + s.w_ball * ball_relevance + s.w_novelty * novelty
    return round(float(np.clip(v, 0.0, 1.0)), 3)


def outcome_from_events(ep: Episode, events: list[MatchEvent], opened_s: float, now_s: float, clip_end_s: float,
                        behind_line_check, cfg: Config, last_opened_s: float | None = None) -> dict:
    """Outcome of the exposures after they were claimed, from events available by ``now_s`` only. The window runs
    from the first exposure's claim to outcome_window_s after the last one's."""
    win_end = (opened_s if last_opened_s is None else last_opened_s) + cfg.episodes.outcome_window_s
    rel = [e for e in events if opened_s - 1e-9 <= e.event_time_s <= win_end + 1e-9]
    for e in rel:
        if e.kind == "pass_receive" and e.target_id and behind_line_check(e):
            release = next((r for r in events if r.event_id == e.related_event_id), None)
            ids = [x.event_id for x in (release, e) if x is not None]
            return {"status": "exploited_in_synthetic_play", "event_ids": ids, "realized_target_id": e.target_id}
        if e.kind in ("interception", "turnover"):
            release = next((r for r in events if r.event_id == e.related_event_id), None)
            ids = [x.event_id for x in (release, e) if x is not None]
            return {"status": "not_exploited_in_window", "event_ids": ids, "realized_target_id": None}
    if now_s >= win_end + 0.5 - 1e-9:  # allow for the feed latency before declaring nothing happened
        return {"status": "not_exploited_in_window", "event_ids": [], "realized_target_id": None}
    if now_s >= clip_end_s - 1e-9:
        return {"status": "inconclusive", "event_ids": [], "realized_target_id": None}
    return {"status": "not_yet_observed", "event_ids": [], "realized_target_id": None}
