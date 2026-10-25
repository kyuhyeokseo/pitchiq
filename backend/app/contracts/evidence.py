"""Evidence contracts: metrics, claims, changes, episodes, packets (spec 9, 11)."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import ID, Epistemic, EvidenceStatus, FrameRef, StrictModel, Window, _finite, check_unit_interval
from .observation import MatchEvent
from .registry import CLAIM_CODES, CLAIM_DIMENSION, METRIC_UNITS
from .state import TacticalState

ClaimCode = Literal[
    "short_options_screened", "central_routes_screened", "behind_space_exposed", "behind_depth_reduced",
    "press_block_disconnected", "between_lines_receiver", "isolated_1v1", "wide_outlet_available",
    "weak_side_accessible", "local_central_overload", "accessible_free_receiver", "second_ball_support_gap",
]
assert set(ClaimCode.__args__) == set(CLAIM_CODES)


class EvidenceMetric(StrictModel):
    metric_id: ID
    key: str
    value: float | None
    unit: Literal["m", "s", "mps", "count", "ratio", "score"]
    status: EvidenceStatus
    window: Window
    player_ids: list[ID]
    frame_refs: list[FrameRef]
    event_ids: list[ID]
    method_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _value(self) -> EvidenceMetric:
        if self.key not in METRIC_UNITS:
            raise ValueError(f"metric key {self.key!r} is not in the registry")
        if METRIC_UNITS[self.key] != self.unit:
            raise ValueError(f"metric {self.key!r} must use unit {METRIC_UNITS[self.key]!r}")
        if self.status == "unavailable":
            if self.value is not None:
                raise ValueError("an unavailable metric must have value=null")
            return self
        if self.value is None or not math.isfinite(self.value):
            raise ValueError("an available metric needs a finite value")
        if self.unit in ("ratio", "score"):
            check_unit_interval(self.value, self.key)
        if self.unit == "count" and (self.value < 0 or self.value != int(self.value)):
            raise ValueError("a count must be a non-negative integer")
        return self


class TacticalClaim(StrictModel):
    claim_id: ID
    code: ClaimCode
    epistemic: Epistemic
    dimension: Literal["restricted", "exposed", "context"]
    metric_ids: list[ID] = Field(min_length=1)
    relation_ids: list[ID]
    player_ids: list[ID] = Field(min_length=1)
    support_score: float
    window: Window

    @field_validator("support_score")
    @classmethod
    def _s(cls, v: float) -> float:
        return check_unit_interval(v, "support_score")

    @model_validator(mode="after")
    def _dimension(self) -> TacticalClaim:
        if CLAIM_DIMENSION[self.code] != self.dimension:
            raise ValueError(f"claim {self.code} must have dimension {CLAIM_DIMENSION[self.code]}")
        return self


ChangeCode = Literal["attacker_drop", "defender_step", "line_retreat", "block_shift", "mark_switch", "lane_change",
                     "count_change"]


class TacticalChange(StrictModel):
    change_id: ID
    before_state_id: ID
    after_state_id: ID
    actor_team_id: ID
    actor_player_ids: list[ID] = Field(min_length=1)
    code: ChangeCode
    relation: Literal["observed_next", "consistent_with_response"]
    metric_ids: list[ID]
    observed_at_s: float


OutcomeStatus = Literal["not_yet_observed", "exploited_in_synthetic_play", "not_exploited_in_window", "inconclusive"]


class Outcome(StrictModel):
    status: OutcomeStatus
    observed_until_s: float
    event_ids: list[ID]
    realized_target_id: ID | None


class TradeoffEpisode(StrictModel):
    episode_id: ID
    revision: int = Field(ge=1)
    match_id: ID
    clip_id: ID
    lifecycle: Literal["candidate", "active", "cooling", "closed"]
    analysis_status: Literal["complete_tradeoff", "partial", "insufficient_evidence"]
    defending_team_id: ID
    analyzed_until_s: float
    detected_at_s: float
    window: Window
    state_ids: list[ID]
    restricted_claim_ids: list[ID]
    exposed_claim_ids: list[ID]
    context_claim_ids: list[ID]
    change_ids: list[ID]
    story_priority: float
    outcome: Outcome

    @model_validator(mode="after")
    def _consistent(self) -> TradeoffEpisode:
        check_unit_interval(self.story_priority, "story_priority")
        _finite(self.analyzed_until_s)
        if self.window.end_s > self.analyzed_until_s + 1e-9:
            raise ValueError("episode window cannot extend past analyzed_until_s")
        if self.detected_at_s > self.analyzed_until_s + 1e-9:
            raise ValueError("an episode cannot be detected after its analysis cutoff")
        if self.analysis_status == "complete_tradeoff" and not (self.restricted_claim_ids and self.exposed_claim_ids):
            raise ValueError("a complete trade-off needs at least one restricted and one exposed claim")
        if self.outcome.observed_until_s > self.analyzed_until_s + 1e-9:
            raise ValueError("outcome cannot be observed past the analysis cutoff")
        return self

    def claim_ids(self) -> list[str]:
        return self.restricted_claim_ids + self.exposed_claim_ids + self.context_claim_ids


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def compute_evidence_hash(episode: TradeoffEpisode, states, metrics, claims, changes, events,
                          forbidden_inferences) -> str:
    """sha256 over canonical JSON of the packet content (the hash field itself is excluded)."""
    payload = {
        "episode": episode.model_dump(mode="json"),
        "states": sorted((s.model_dump(mode="json") for s in states), key=lambda d: d["state_id"]),
        "metrics": sorted((m.model_dump(mode="json") for m in metrics), key=lambda d: d["metric_id"]),
        "claims": sorted((c.model_dump(mode="json") for c in claims), key=lambda d: d["claim_id"]),
        "changes": sorted((c.model_dump(mode="json") for c in changes), key=lambda d: d["change_id"]),
        "events": sorted((e.model_dump(mode="json") for e in events), key=lambda d: d["event_id"]),
        "forbidden_inferences": sorted(forbidden_inferences),
    }
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class EvidencePacket(StrictModel):
    schema_version: Literal["2.0"]
    evidence_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    episode: TradeoffEpisode
    states: list[TacticalState]
    metrics: list[EvidenceMetric]
    claims: list[TacticalClaim]
    changes: list[TacticalChange]
    events: list[MatchEvent]
    forbidden_inferences: list[str]

    @model_validator(mode="after")
    def _integrity(self) -> EvidencePacket:
        ep = self.episode
        cutoff = ep.analyzed_until_s
        state_ids = {s.state_id for s in self.states}
        metric_ids = {m.metric_id for m in self.metrics}
        claim_by_id = {c.claim_id: c for c in self.claims}
        change_ids = {c.change_id for c in self.changes}
        event_ids = {e.event_id for e in self.events}
        for name, ids in (("state", state_ids), ("metric", metric_ids), ("claim", set(claim_by_id)),
                          ("change", change_ids), ("event", event_ids)):
            n = {"state": self.states, "metric": self.metrics, "claim": self.claims, "change": self.changes,
                 "event": self.events}[name]
            if len(ids) != len(n):
                raise ValueError(f"duplicate {name} ids in packet")
        if not set(ep.state_ids) <= state_ids:
            raise ValueError("episode references a state that is not in the packet")
        relation_ids = {r for s in self.states for r in s.relation_ids()} | {
            g.region_id for s in self.states for g in s.regions}
        player_ids = {p for s in self.states for p in _state_players(s)}
        for dim, ids in (("restricted", ep.restricted_claim_ids), ("exposed", ep.exposed_claim_ids),
                         ("context", ep.context_claim_ids)):
            for cid in ids:
                if cid not in claim_by_id:
                    raise ValueError(f"episode references unknown claim {cid}")
                if claim_by_id[cid].dimension != dim:
                    raise ValueError(f"claim {cid} is listed as {dim} but has dimension {claim_by_id[cid].dimension}")
        if not set(ep.change_ids) <= change_ids:
            raise ValueError("episode references an unknown change")
        for c in self.claims:
            if not set(c.metric_ids) <= metric_ids:
                raise ValueError(f"claim {c.claim_id} references an unknown metric")
            if not set(c.relation_ids) <= relation_ids:
                raise ValueError(f"claim {c.claim_id} references an unknown relation")
            if c.window.end_s > cutoff + 1e-9:
                raise ValueError(f"claim {c.claim_id} window passes the analysis cutoff")
            if player_ids and not set(c.player_ids) <= player_ids:
                raise ValueError(f"claim {c.claim_id} names a player absent from its states")
        for ch in self.changes:
            if ch.before_state_id not in state_ids or ch.after_state_id not in state_ids:
                raise ValueError(f"change {ch.change_id} references a state outside the packet")
            if not set(ch.metric_ids) <= metric_ids:
                raise ValueError(f"change {ch.change_id} references an unknown metric")
            if ch.observed_at_s > cutoff + 1e-9:
                raise ValueError(f"change {ch.change_id} is observed after the cutoff")
        for m in self.metrics:
            if m.window.end_s > cutoff + 1e-9:
                raise ValueError(f"metric {m.metric_id} window passes the analysis cutoff")
            if not set(m.event_ids) <= event_ids:
                raise ValueError(f"metric {m.metric_id} references an unknown event")
        for s in self.states:
            if s.analyzed_until_s > cutoff + 1e-9:
                raise ValueError(f"state {s.state_id} is after the packet cutoff")
            if s.defending_team_id is not None and s.defending_team_id != ep.defending_team_id:
                raise ValueError(f"state {s.state_id} has a different defending team")
        for e in self.events:
            if e.available_at_s > cutoff + 1e-9:
                raise ValueError(f"event {e.event_id} was not available at the cutoff")
            if e.clip_id != ep.clip_id or e.match_id != ep.match_id:
                raise ValueError(f"event {e.event_id} belongs to another clip")
        if not set(ep.outcome.event_ids) <= event_ids:
            raise ValueError("outcome references an event outside the packet")
        expected = compute_evidence_hash(ep, self.states, self.metrics, self.claims, self.changes, self.events,
                                         self.forbidden_inferences)
        if expected != self.evidence_hash:
            raise ValueError("evidence_hash does not match the packet content")
        return self

    def metric(self, metric_id: str) -> EvidenceMetric:
        return next(m for m in self.metrics if m.metric_id == metric_id)

    def claim(self, claim_id: str) -> TacticalClaim:
        return next(c for c in self.claims if c.claim_id == claim_id)


def _state_players(s: TacticalState) -> set[str]:
    out: set[str] = set()
    for g in s.line_groups:
        out.update(g.player_ids)
    for m in s.markings:
        out.update((m.defender_id, m.attacker_id))
    for p in s.pressures:
        out.update((p.presser_id, p.carrier_id, *p.likely_outlet_ids))
    for lane in s.lanes:
        out.update((lane.carrier_id, lane.receiver_id, *lane.screening_player_ids))
    for c in s.covers:
        out.update(x for x in (c.attacker_id, c.primary_defender_id, c.cover_player_id) if x)
    for r in s.regions:
        out.update((*r.attacking_player_ids, *r.defending_player_ids))
    return out
