"""Broadcast insight + typed overlay contract."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .common import StrictModel, Vec2
from .predictions import Pattern


class PlayerHighlight(StrictModel):
    type: Literal["player_highlight"] = "player_highlight"
    player_id: str
    style: Literal["primary", "secondary", "defender"] = "primary"


class MovementArrow(StrictModel):
    type: Literal["movement_arrow"] = "movement_arrow"
    player_id: str
    path: list[Vec2] = Field(min_length=2)
    start_s: float
    end_s: float
    style: Literal["highlight", "muted"] = "highlight"


class SpaceZone(StrictModel):
    type: Literal["space_zone"] = "space_zone"
    polygon: list[Vec2] = Field(min_length=2)
    kind: Literal["defender_gap", "passing_lane"]
    player_ids: list[str] = Field(default_factory=list)


class CaptionCard(StrictModel):
    type: Literal["caption_card"] = "caption_card"
    anchor: Vec2 | None = None
    text_key: Literal["title", "fan", "analyst"] = "title"


OverlayElement = Annotated[
    Union[PlayerHighlight, MovementArrow, SpaceZone, CaptionCard], Field(discriminator="type")
]


class OverlaySpec(StrictModel):
    sync_match_s: float
    ttl_s: float = Field(gt=0)
    source_prediction_id: str
    elements: list[OverlayElement]


class BroadcastInsight(StrictModel):
    insight_id: str
    match_id: str
    clip_id: str
    prediction_id: str
    detected_at_match_s: float
    display_at_match_s: float
    expires_at_match_s: float
    evidence_frame_indices: list[int]
    evidence_event_ids: list[str]
    actor_player_ids: list[str]
    pattern: Pattern
    importance: Literal["low", "medium", "high"]
    title: str
    fan_en: str
    analyst_en: str
    fan_ko: str
    analyst_ko: str
    numeric_facts: dict[str, float | int | bool | str | None]
    overlay: OverlaySpec
    production_status: Literal["suggested", "approved", "rejected", "published"]
    source_mode: Literal["foundry_live", "foundry_cached", "deterministic_fallback"]

    @model_validator(mode="after")
    def _check(self) -> BroadcastInsight:
        # Late output may never be backdated into the live timeline.
        if self.display_at_match_s < self.detected_at_match_s:
            raise ValueError("display time cannot precede detection time")
        if self.expires_at_match_s <= self.display_at_match_s:
            raise ValueError("insight must expire after it is displayed")
        return self
