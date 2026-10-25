"""Fictional teams, roster and nominal formation line structures."""

from __future__ import annotations

from dataclasses import dataclass

ROLES: tuple[str, ...] = ("GK", "RB", "RCB", "LCB", "LB", "DM", "RCM", "LCM", "RW", "ST", "LW")
ROLE_INDEX = {r: i for i, r in enumerate(ROLES)}
SHIRT = {"GK": 1, "RB": 2, "LB": 3, "LCB": 4, "RCB": 5, "DM": 6, "RW": 7, "RCM": 8, "ST": 9, "LCM": 10, "LW": 11}
MIRROR_ROLE = {
    "GK": "GK", "RB": "LB", "LB": "RB", "RCB": "LCB", "LCB": "RCB", "DM": "DM",
    "RCM": "LCM", "LCM": "RCM", "RW": "LW", "LW": "RW", "ST": "ST",
}

# Outfield line structures (goalkeeper excluded; each must total 10).
# 4-4-2-like and 3-2-5 are described in terms of the nominal 4-3-3 roles that
# typically occupy each line; they are reference structures, not frame labels.
FORMATION_LINES: dict[str, tuple[tuple[str, ...], ...]] = {
    "4-3-3": (("RB", "RCB", "LCB", "LB"), ("DM", "RCM", "LCM"), ("RW", "ST", "LW")),
    "4-4-2": (("RB", "RCB", "LCB", "LB"), ("RW", "DM", "LCM", "LW"), ("RCM", "ST")),
    "3-2-5": (("RCB", "LCB", "LB"), ("RB", "DM"), ("RW", "RCM", "ST", "LCM", "LW")),
}


@dataclass(frozen=True)
class TeamSpec:
    team_id: str
    name: str
    short_name: str
    color: str
    nominal_formation: str = "4-3-3"


TEAMS: dict[str, TeamSpec] = {
    "blue": TeamSpec("blue", "Blue FC", "BLU", "#2f6fde"),
    "red": TeamSpec("red", "Red United", "RED", "#d6453d"),
}


def player_id(team_id: str, role: str) -> str:
    return f"{team_id}_{SHIRT[role]:02d}"


def roster(team_id: str) -> list[dict]:
    t = TEAMS[team_id]
    return [
        {
            "player_id": player_id(team_id, r),
            "team_id": team_id,
            "shirt_number": SHIRT[r],
            "nominal_role": r,
            "display_name": f"{t.short_name} #{SHIRT[r]}",
        }
        for r in ROLES
    ]


def formation_counts(name: str) -> tuple[int, ...]:
    return tuple(len(line) for line in FORMATION_LINES[name])
