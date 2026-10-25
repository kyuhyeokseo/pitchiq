"""Convert canonical simulation output into the public world-coordinate contract.

Canonical frame: attacking team attacks +x, action flank at low y.
World transform: mirror y (and swap L/R roles) for a left-flank clip, then
rotate 180 degrees if the attacking team attacks toward x=0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..schemas import MatchEvent, TrackingFrame
from .engine import DT, SimResult
from .roster import MIRROR_ROLE, ROLES, player_id


@dataclass(frozen=True)
class ClipContext:
    match_id: str
    clip_id: str
    start_match_s: float
    period: int
    attacking_team: str
    defending_team: str
    flank: str  # "right" | "left", relative to the attacking team
    attack_dir: int  # +1/-1 for the attacking team

    @property
    def attack_direction(self) -> dict[str, int]:
        return {self.attacking_team: self.attack_dir, self.defending_team: -self.attack_dir}


def _r(v: float, nd: int = 2) -> float:
    return round(float(v), nd) + 0.0  # normalise -0.0


def world_xy(xy: np.ndarray, ctx: ClipContext) -> np.ndarray:
    out = np.array(xy, dtype=float, copy=True)
    if ctx.flank == "left":
        out[..., 1] = 68.0 - out[..., 1]
    if ctx.attack_dir == -1:
        out[..., 0] = 105.0 - out[..., 0]
        out[..., 1] = 68.0 - out[..., 1]
    return out


def world_vel(v: np.ndarray, ctx: ClipContext) -> np.ndarray:
    out = np.array(v, dtype=float, copy=True)
    if ctx.flank == "left":
        out[..., 1] = -out[..., 1]
    if ctx.attack_dir == -1:
        out[..., :] = -out[..., :]
    return out


def actual_role(canonical_index: int, ctx: ClipContext) -> str:
    role = ROLES[canonical_index % 11]
    return MIRROR_ROLE[role] if ctx.flank == "left" else role


def canonical_player_id(canonical_index: int, ctx: ClipContext) -> str:
    team = ctx.attacking_team if canonical_index < 11 else ctx.defending_team
    return player_id(team, actual_role(canonical_index, ctx))


def _vec(xy: np.ndarray) -> dict[str, float]:
    return {"x_m": _r(xy[0]), "y_m": _r(xy[1])}


def export_frames(res: SimResult, ctx: ClipContext, team_order: tuple[str, str]) -> list[dict]:
    # Stable public ordering by team then role, independent of who attacks.
    idx_by_pid = {canonical_player_id(i, ctx): i for i in range(22)}
    order = [player_id(t, r) for t in team_order for r in ROLES]
    out: list[dict] = []
    for f in res.frames:
        wp = world_xy(f.pos, ctx)
        wv = world_vel(f.vel, ctx)
        players = []
        for pid in order:
            i = idx_by_pid[pid]
            role = actual_role(i, ctx)
            players.append(
                {
                    "player_id": pid,
                    "team_id": pid.split("_")[0],
                    "nominal_role": role,
                    "is_goalkeeper": role == "GK",
                    "position": _vec(wp[i]),
                    "velocity": _vec(wv[i]),
                    "on_pitch": True,
                }
            )
        owner = None if f.owner < 0 else canonical_player_id(f.owner, ctx)
        out.append(
            {
                "match_id": ctx.match_id,
                "clip_id": ctx.clip_id,
                "frame_index": f.k,
                "match_time_s": _r(ctx.start_match_s + f.k * DT, 3),
                "clip_time_s": _r(f.k * DT, 3),
                "period": ctx.period,
                "attack_direction": ctx.attack_direction,
                "players": players,
                "ball_position": _vec(world_xy(f.ball, ctx)),
                "ball_owner_id": owner,
                # In-flight passes keep team possession; no turnovers in Family A.
                "possession_team_id": ctx.attacking_team,
            }
        )
    for fr in out:
        TrackingFrame.model_validate(fr)
    return out


def export_events(res: SimResult, ctx: ClipContext) -> list[dict]:
    ids = [f"{ctx.clip_id}_ev{n:03d}" for n in range(len(res.events))]
    out: list[dict] = []
    for n, e in enumerate(res.events):
        team = {"A": ctx.attacking_team, "D": ctx.defending_team, None: None}[e.team]
        ctx_out = dict(e.context)
        if "pass_event_index" in ctx_out:
            ctx_out["pass_event_id"] = ids[ctx_out.pop("pass_event_index")]
        if "start_match_offset_s" in ctx_out:
            ctx_out["start_match_s"] = _r(ctx.start_match_s + ctx_out.pop("start_match_offset_s"), 3)
        ev = {
            "match_id": ctx.match_id,
            "event_id": ids[n],
            "clip_id": ctx.clip_id,
            "match_time_s": _r(ctx.start_match_s + e.k * DT, 3),
            "event_type": e.event_type,
            "team_id": team,
            "actor_id": None if e.actor is None else canonical_player_id(e.actor, ctx),
            "target_id": None if e.target is None else canonical_player_id(e.target, ctx),
            "start": None if e.start is None else _vec(world_xy(e.start, ctx)),
            "end": None if e.end is None else _vec(world_xy(e.end, ctx)),
            "success": e.success,
            "context": ctx_out,
        }
        MatchEvent.model_validate(ev)
        out.append(ev)
    return out
