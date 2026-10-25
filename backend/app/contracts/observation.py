"""Public observation contracts: match metadata, tracking frames, events (spec 5, 11)."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import (
    ID,
    OUTFIELD_PER_TEAM,
    PLAYERS_PER_TEAM,
    FrameRef,
    StrictModel,
    Velocity,
    XY,
    _finite,
)


class Pitch(StrictModel):
    length_m: Literal[105]
    width_m: Literal[68]


class TeamMeta(StrictModel):
    team_id: ID
    display_name: str = Field(min_length=1, max_length=64)
    attack_direction: Literal[1, -1]


class RosterEntry(StrictModel):
    player_id: ID
    team_id: ID
    jersey: int = Field(ge=1, le=99)
    is_goalkeeper: bool


class ClipMeta(StrictModel):
    clip_id: ID
    start_s: float
    end_s: float
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _ordered(self) -> ClipMeta:
        if not (_finite(self.start_s) < _finite(self.end_s)):
            raise ValueError("clip start_s must be < end_s")
        return self


class MatchMeta(StrictModel):
    schema_version: Literal["2.0"]
    match_id: ID
    period: Literal[1, 2]
    pitch: Pitch
    tracking_hz: Literal[5]
    teams: list[TeamMeta]
    roster: list[RosterEntry]
    clips: list[ClipMeta]

    @model_validator(mode="after")
    def _consistent(self) -> MatchMeta:
        if len(self.teams) != 2 or len({t.team_id for t in self.teams}) != 2:
            raise ValueError("exactly two distinct teams are required")
        if {t.attack_direction for t in self.teams} != {1, -1}:
            raise ValueError("teams must attack in opposite directions")
        ids = [r.player_id for r in self.roster]
        if len(ids) != len(set(ids)):
            raise ValueError("roster player_id values must be unique")
        team_ids = {t.team_id for t in self.teams}
        for team in team_ids:
            members = [r for r in self.roster if r.team_id == team]
            if len(members) != PLAYERS_PER_TEAM or sum(r.is_goalkeeper for r in members) != 1:
                raise ValueError(f"team {team} needs 11 roster players with exactly one goalkeeper")
        if any(r.team_id not in team_ids for r in self.roster):
            raise ValueError("roster references an unknown team")
        if len({c.clip_id for c in self.clips}) != len(self.clips):
            raise ValueError("clip_id values must be unique")
        return self

    def attack_direction(self, team_id: str) -> int:
        return next(t.attack_direction for t in self.teams if t.team_id == team_id)


class PlayerObs(StrictModel):
    player_id: ID
    team_id: ID
    is_goalkeeper: bool
    position: XY
    velocity: Velocity


class BallObs(StrictModel):
    position: XY
    z_m: float = Field(ge=0.0, le=40.0)
    owner_id: ID | None


class ObservationFrame(StrictModel):
    schema_version: Literal["2.0"]
    match_id: ID
    frame_ref: FrameRef
    period: Literal[1, 2]
    match_time_s: float
    players: list[PlayerObs]
    ball: BallObs
    possession_team_id: ID | None
    ball_state: Literal["controlled", "in_flight", "loose", "dead"]

    @field_validator("match_time_s")
    @classmethod
    def _t(cls, v: float) -> float:
        return _finite(v)

    @model_validator(mode="after")
    def _invariants(self) -> ObservationFrame:
        ids = [p.player_id for p in self.players]
        if len(self.players) != 2 * PLAYERS_PER_TEAM or len(set(ids)) != len(ids):
            raise ValueError("a frame must contain exactly 22 unique players")
        per_team = Counter(p.team_id for p in self.players)
        if len(per_team) != 2 or set(per_team.values()) != {PLAYERS_PER_TEAM}:
            raise ValueError("a frame must contain 11 players per team")
        for team in per_team:
            if sum(p.is_goalkeeper for p in self.players if p.team_id == team) != 1:
                raise ValueError(f"team {team} must have exactly one goalkeeper on the pitch")
        for p in self.players:
            if not p.position.in_pitch():
                raise ValueError(f"player {p.player_id} is outside the pitch")
        if not self.ball.position.in_pitch():
            raise ValueError("ball is outside the pitch")
        owner = self.ball.owner_id
        if owner is not None:
            team_of = {p.player_id: p.team_id for p in self.players}
            if owner not in team_of:
                raise ValueError("ball owner is not on the pitch")
            if self.possession_team_id != team_of[owner]:
                raise ValueError("ball owner must belong to possession_team_id")
            if self.ball_state != "controlled":
                raise ValueError("a ball with an owner must be 'controlled'")
        elif self.ball_state == "controlled":
            raise ValueError("a 'controlled' ball needs an owner")
        if self.possession_team_id is not None and self.possession_team_id not in per_team:
            raise ValueError("possession_team_id is not a team on the pitch")
        return self

    def outfield_count(self, team_id: str) -> int:
        n = sum(1 for p in self.players if p.team_id == team_id and not p.is_goalkeeper)
        assert n == OUTFIELD_PER_TEAM
        return n


EventKind = Literal["pass_release", "pass_receive", "interception", "carry", "duel", "turnover", "shot", "goal",
                    "restart"]


class MatchEvent(StrictModel):
    event_id: ID
    match_id: ID
    clip_id: ID
    event_time_s: float
    available_at_s: float
    kind: EventKind
    actor_id: ID | None
    target_id: ID | None
    related_event_id: ID | None
    position: XY | None

    @model_validator(mode="after")
    def _times(self) -> MatchEvent:
        _finite(self.event_time_s)
        _finite(self.available_at_s)
        if self.available_at_s < self.event_time_s - 1e-9:
            raise ValueError("available_at_s cannot precede event_time_s")
        if self.kind == "pass_release" and self.target_id is not None:
            raise ValueError("a pass_release must not name its (future) receiver")
        return self
