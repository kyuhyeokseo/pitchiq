"""Public scenario manifest: teams, roster, nominal formation, clip intervals.

Contains no pre-solved tactical answers. The UI must never read a pattern
label from here (there is none to read).
"""

from __future__ import annotations

from pydantic import Field, model_validator

from ..privacy import assert_public
from .common import AttackDirection, StrictModel
from .tracking import NominalRole


class RosterEntry(StrictModel):
    player_id: str
    team_id: str
    shirt_number: int = Field(ge=1, le=99)
    nominal_role: NominalRole
    display_name: str


class TeamInfo(StrictModel):
    team_id: str
    name: str
    short_name: str
    color: str
    nominal_formation: str


class ClipInfo(StrictModel):
    clip_id: str
    period: int = Field(ge=1, le=2)
    start_match_s: float
    end_match_s: float
    n_frames: int = Field(ge=2)
    sample_rate_hz: float
    attack_direction: dict[str, AttackDirection]
    tracking_file: str
    tracking_sha256: str

    @model_validator(mode="after")
    def _check(self) -> ClipInfo:
        if self.end_match_s <= self.start_match_s:
            raise ValueError("clip end must follow start")
        return self


class ScenarioManifest(StrictModel):
    schema_version: str
    generator_version: str
    scenario_id: str
    match_id: str
    title: str
    synthetic: bool = True
    data_notice: str
    teams: list[TeamInfo]
    roster: list[RosterEntry]
    clips: list[ClipInfo]
    events_file: str
    events_sha256: str
    tracking_available: str

    @model_validator(mode="after")
    def _check(self) -> ScenarioManifest:
        if not self.synthetic:
            raise ValueError("PitchIQ fixtures are synthetic only")
        assert_public(self.model_dump())
        ids = [r.player_id for r in self.roster]
        if len(ids) != len(set(ids)) or len(ids) != 22:
            raise ValueError("roster must list 22 unique players")
        return self
