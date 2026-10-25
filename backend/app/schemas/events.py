"""Public match event contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from ..privacy import assert_public
from .common import StrictModel, Vec2

EventType = Literal[
    "pass", "receive", "carry", "tackle", "turnover", "pressure", "shot", "goal", "restart"
]


class MatchEvent(StrictModel):
    match_id: str
    event_id: str
    clip_id: str | None = None
    match_time_s: float = Field(ge=0)
    event_type: EventType
    team_id: str | None
    actor_id: str | None
    target_id: str | None
    start: Vec2 | None
    end: Vec2 | None
    success: bool | None
    # Observable context only (e.g. pass length). Never hidden labels.
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def _no_private(cls, v: dict[str, Any]) -> dict[str, Any]:
        assert_public(v)
        return v
