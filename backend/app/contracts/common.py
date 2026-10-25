"""Primitive types shared by every v2 contract (spec section 11).

World pitch: 105 m x 68 m, origin bottom-left, x to the right, y up. All
models reject unknown fields and are immutable once built.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "2.0"
PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0
TRACKING_HZ = 5
FRAME_DT_S = 1.0 / TRACKING_HZ
PLAYERS_PER_TEAM = 11
OUTFIELD_PER_TEAM = 10

# Development caps used by validators (spec 5.3); not measured physical laws.
MAX_PLAYER_SPEED_MPS = 9.0
MAX_PLAYER_ACCEL_MPS2 = 5.0
MAX_BALL_SPEED_MPS = 35.0

ID = Annotated[str, Field(min_length=1, max_length=128)]
EvidenceStatus = Literal["ok", "uncertain", "unavailable"]
SourceMode = Literal["foundry_live", "foundry_cached", "deterministic_fallback"]
Epistemic = Literal["computed_observation", "heuristic_inference", "realized_event"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _finite(v: float) -> float:
    if not math.isfinite(v):
        raise ValueError("value must be finite")
    return v


class XY(StrictModel):
    x_m: float
    y_m: float

    @field_validator("x_m", "y_m")
    @classmethod
    def _check(cls, v: float) -> float:
        return _finite(v)

    def in_pitch(self, tol: float = 1e-6) -> bool:
        return -tol <= self.x_m <= PITCH_LENGTH_M + tol and -tol <= self.y_m <= PITCH_WIDTH_M + tol


class Velocity(StrictModel):
    vx_mps: float
    vy_mps: float

    @field_validator("vx_mps", "vy_mps")
    @classmethod
    def _check(cls, v: float) -> float:
        return _finite(v)


class FrameRef(StrictModel):
    clip_id: ID
    frame_index: int = Field(ge=0)


class Window(StrictModel):
    start_s: float
    end_s: float

    @field_validator("start_s", "end_s")
    @classmethod
    def _check(cls, v: float) -> float:
        return _finite(v)

    @model_validator(mode="after")
    def _ordered(self) -> Window:
        if self.start_s > self.end_s + 1e-9:
            raise ValueError("window start_s must be <= end_s")
        return self


def check_unit_interval(v: float, name: str) -> float:
    _finite(v)
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return v
