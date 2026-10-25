"""Shared constants and primitive types for the public PitchIQ data contract.

World coordinates: x = 0 (left goal line) .. 105 m (right goal line),
y = 0 (bottom touchline) .. 68 m (top touchline). Always metres.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0
PITCH_CENTER_Y_M = PITCH_WIDTH_M / 2.0
SAMPLE_RATE_HZ = 5.0
FRAME_DT_S = 1.0 / SAMPLE_RATE_HZ
PLAYERS_PER_TEAM = 11
OUTFIELD_PER_TEAM = 10

# Development caps for plausibility checks on finite differences. These are
# simplified engineering limits, not measured physical laws.
MAX_PLAYER_SPEED_MPS = 9.0
MAX_BALL_SPEED_MPS = 35.0

AttackDirection = Literal[1, -1]


class StrictModel(BaseModel):
    """Base for public contract models: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Vec2(StrictModel):
    """A named 2D vector in metres (position) or metres/second (velocity).

    Pitch-bound checks live on the models that hold positions, because the
    same type is also used for velocities, which may be negative.
    """

    x_m: float
    y_m: float

    @field_validator("x_m", "y_m")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("vector components must be finite")
        return v


def in_pitch(p: Vec2, tol: float = 1e-6) -> bool:
    return (-tol <= p.x_m <= PITCH_LENGTH_M + tol) and (-tol <= p.y_m <= PITCH_WIDTH_M + tol)
