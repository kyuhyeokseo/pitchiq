"""Public tracking contract: 22 players + ball per frame at 5 Hz."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    PLAYERS_PER_TEAM,
    AttackDirection,
    StrictModel,
    Vec2,
    in_pitch,
)

NominalRole = Literal["GK", "RB", "RCB", "LCB", "LB", "DM", "RCM", "LCM", "RW", "ST", "LW"]


class PlayerFrame(StrictModel):
    player_id: str
    team_id: str
    nominal_role: NominalRole
    is_goalkeeper: bool
    position: Vec2
    velocity: Vec2
    on_pitch: bool = True

    @model_validator(mode="after")
    def _check(self) -> PlayerFrame:
        if self.is_goalkeeper != (self.nominal_role == "GK"):
            raise ValueError(f"{self.player_id}: is_goalkeeper must match nominal_role GK")
        if not in_pitch(self.position):
            raise ValueError(f"{self.player_id}: position outside pitch {self.position}")
        return self


class TrackingFrame(StrictModel):
    match_id: str
    clip_id: str
    frame_index: int = Field(ge=0)
    match_time_s: float = Field(ge=0)
    clip_time_s: float = Field(ge=0)
    period: int = Field(ge=1, le=2)
    # +1 attacks toward x=105, -1 toward x=0. Observable match metadata,
    # carried per frame so streaming consumers can normalise without lookups.
    attack_direction: dict[str, AttackDirection]
    players: list[PlayerFrame]
    ball_position: Vec2
    ball_owner_id: str | None
    possession_team_id: str | None

    @model_validator(mode="after")
    def _check(self) -> TrackingFrame:
        ids = [p.player_id for p in self.players]
        if len(ids) != 2 * PLAYERS_PER_TEAM:
            raise ValueError(f"frame {self.frame_index}: expected 22 players, got {len(ids)}")
        if len(set(ids)) != len(ids):
            raise ValueError(f"frame {self.frame_index}: duplicate player ids")
        per_team = Counter(p.team_id for p in self.players)
        if len(per_team) != 2 or any(n != PLAYERS_PER_TEAM for n in per_team.values()):
            raise ValueError(f"frame {self.frame_index}: need exactly 11 players per team, got {dict(per_team)}")
        gks = Counter(p.team_id for p in self.players if p.is_goalkeeper)
        if any(gks.get(t, 0) != 1 for t in per_team):
            raise ValueError(f"frame {self.frame_index}: need exactly one goalkeeper per team, got {dict(gks)}")
        if set(self.attack_direction) != set(per_team):
            raise ValueError("attack_direction must list both teams")
        if set(self.attack_direction.values()) != {1, -1}:
            raise ValueError("teams must attack in opposite directions")
        if not in_pitch(self.ball_position):
            raise ValueError(f"frame {self.frame_index}: ball outside pitch")
        if self.ball_owner_id is not None:
            owner = next((p for p in self.players if p.player_id == self.ball_owner_id), None)
            if owner is None:
                raise ValueError(f"frame {self.frame_index}: unknown ball owner {self.ball_owner_id}")
            if self.possession_team_id != owner.team_id:
                raise ValueError(f"frame {self.frame_index}: possession team does not match ball owner")
        if self.possession_team_id is not None and self.possession_team_id not in per_team:
            raise ValueError(f"frame {self.frame_index}: unknown possession team")
        return self
