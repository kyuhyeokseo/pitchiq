"""Detector and significance outputs (produced from observable data only)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

Pattern = Literal["overlap", "underlap", "role_rotation", "shape_shift", "none", "uncertain"]
# A raw detector score is not a probability unless it has been calibrated.
ConfidenceKind = Literal["heuristic_score", "calibrated_probability"]


class TacticalPrediction(StrictModel):
    prediction_id: str
    match_id: str
    clip_id: str
    start_s: float
    end_s: float
    pattern: Pattern
    actor_player_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_kind: ConfidenceKind = "heuristic_score"
    inferred_shape_before: str | None = None
    inferred_shape_after: str | None = None
    evidence_frame_indices: list[int]

    @model_validator(mode="after")
    def _check(self) -> TacticalPrediction:
        if self.end_s < self.start_s:
            raise ValueError("prediction interval end before start")
        return self


class SignificanceResult(StrictModel):
    prediction_id: str
    metrics: dict[str, float | None]
    # Per-signal weighted contributions; weights are product heuristics,
    # not statistically validated football value.
    contributions: dict[str, float] = Field(default_factory=dict)
    score: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str]
    should_suggest: bool
