"""Tactical state contracts: lines, relations, regions (spec 7, 8, 11)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import ID, OUTFIELD_PER_TEAM, EvidenceStatus, FrameRef, StrictModel, Velocity, Window, XY, _finite, \
    check_unit_interval


class LineGroup(StrictModel):
    group: Literal["back", "mid", "front"]
    player_ids: list[ID] = Field(min_length=1)
    own_goal_depth_m: float
    spread_m: float = Field(ge=0.0)
    support_score: float

    @field_validator("support_score")
    @classmethod
    def _s(cls, v: float) -> float:
        return check_unit_interval(v, "support_score")


class MarkingRelation(StrictModel):
    relation_id: ID
    defender_id: ID
    attacker_id: ID
    since_s: float
    mean_distance_m: float = Field(ge=0.0)
    follow_support_score: float
    status: EvidenceStatus

    @field_validator("follow_support_score")
    @classmethod
    def _s(cls, v: float) -> float:
        return check_unit_interval(v, "follow_support_score")


class LaneRelation(StrictModel):
    relation_id: ID
    carrier_id: ID
    receiver_id: ID
    start: XY
    end: XY
    eligibility: Literal["onside_geometry", "offside_geometry", "unknown"]
    availability: Literal["open", "screened", "uncertain"]
    min_arrival_margin_s: float | None
    screening_player_ids: list[ID]


class PressureRelation(StrictModel):
    relation_id: ID
    presser_id: ID
    carrier_id: ID
    distance_m: float = Field(ge=0.0)
    closing_speed_mps: float
    approach_vector: Velocity
    funnel: Literal["left", "right", "none", "uncertain"]
    likely_outlet_ids: list[ID]
    support_score: float

    @field_validator("support_score")
    @classmethod
    def _s(cls, v: float) -> float:
        return check_unit_interval(v, "support_score")


class CoverRelation(StrictModel):
    relation_id: ID
    attacker_id: ID
    primary_defender_id: ID | None
    cover_player_id: ID | None
    target: XY
    attacker_arrival_s: float | None
    cover_arrival_s: float | None
    cover_margin_s: float | None  # cover arrival - attacker arrival
    status: EvidenceStatus

    @model_validator(mode="after")
    def _margin(self) -> CoverRelation:
        if self.status == "unavailable":
            return self
        if None in (self.attacker_arrival_s, self.cover_arrival_s, self.cover_margin_s):
            raise ValueError("an available cover relation needs both arrivals and the margin")
        if abs(self.cover_arrival_s - self.attacker_arrival_s - self.cover_margin_s) > 1e-6:
            raise ValueError("cover_margin_s must equal cover_arrival_s - attacker_arrival_s")
        return self


class LocalRegion(StrictModel):
    region_id: ID
    polygon: list[XY] = Field(min_length=3)
    attacking_player_ids: list[ID]
    defending_player_ids: list[ID]
    accessible_receiver_ids: list[ID]

    @model_validator(mode="after")
    def _ids(self) -> LocalRegion:
        for name in ("attacking_player_ids", "defending_player_ids", "accessible_receiver_ids"):
            ids = getattr(self, name)
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} must not repeat a player")
        if not set(self.accessible_receiver_ids) <= set(self.attacking_player_ids):
            raise ValueError("accessible receivers must be attacking players inside the region")
        return self


ShapeLabel = Literal["approx_442", "approx_433", "approx_325", "ambiguous", "transition", "unclassified"]
PossessionPhase = Literal["buildup", "progression", "transition", "unknown"]


class TacticalState(StrictModel):
    state_id: ID
    analyzed_until_s: float
    frame_ref: FrameRef
    window: Window
    attacking_team_id: ID | None
    defending_team_id: ID | None
    possession_phase: PossessionPhase
    active_press: bool
    shape_label: ShapeLabel
    line_groups: list[LineGroup]
    markings: list[MarkingRelation]
    pressures: list[PressureRelation]
    lanes: list[LaneRelation]
    covers: list[CoverRelation]
    regions: list[LocalRegion]
    quality_flags: list[str]

    @model_validator(mode="after")
    def _consistent(self) -> TacticalState:
        _finite(self.analyzed_until_s)
        if self.window.end_s > self.analyzed_until_s + 1e-9:
            raise ValueError("state window cannot extend past analyzed_until_s")
        rel_ids = self.relation_ids()
        if len(rel_ids) != len(set(rel_ids)):
            raise ValueError("relation_id values must be unique within a state")
        members = [pid for g in self.line_groups for pid in g.player_ids]
        if len(members) != len(set(members)):
            raise ValueError("a player can belong to only one line group")
        if len({g.group for g in self.line_groups}) != len(self.line_groups):
            raise ValueError("each line group may appear once")
        full = len(members) == OUTFIELD_PER_TEAM and len(self.line_groups) == 3
        if self.line_groups and not full and "partial_line_groups" not in self.quality_flags:
            raise ValueError("line groups that do not cover all 10 outfield players must be flagged partial")
        if self.shape_label.startswith("approx_") and not full:
            raise ValueError("a named shape needs three line groups covering 10 outfield players")
        if (self.attacking_team_id is None) != (self.defending_team_id is None):
            raise ValueError("attacking and defending team must both be known or both unknown")
        if self.attacking_team_id is not None and self.attacking_team_id == self.defending_team_id:
            raise ValueError("attacking and defending team must differ")
        return self

    def relation_ids(self) -> list[str]:
        return ([m.relation_id for m in self.markings] + [p.relation_id for p in self.pressures]
                + [lane.relation_id for lane in self.lanes] + [c.relation_id for c in self.covers])
