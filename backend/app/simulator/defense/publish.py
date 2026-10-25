"""Write a simulated clip as PUBLIC v2 files plus a separate PRIVATE truth record.

Public:  data/public/<clip_id>/{manifest.json, tracking.jsonl, events.jsonl}
Private: evaluation/private_truth/<clip_id>.json

The world transform maps canonical (u, v) to world (x, y): identity when the
attacking team attacks +x, a 180 degree rotation otherwise (spec 5.1). An
optional ``mirror`` flips y for invariance tests. Files are byte-deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...contracts import MatchEvent, MatchMeta, ObservationFrame
from ..roster import ROLES, SHIRT, TEAMS
from .engine import EVENT_LATENCY_S, SimEvent, SimFrame
from .physics import DT

PUBLIC_DIR = Path("data/public")
PRIVATE_TRUTH_DIR = Path("evaluation/private_truth")


@dataclass(frozen=True)
class ClipSpec:
    clip_id: str
    match_id: str
    attacking_team: str  # team id of canonical side A
    attack_direction: int  # world direction of the attacking team
    start_s: float
    period: int = 1
    mirror: bool = False

    @property
    def defending_team(self) -> str:
        return "red" if self.attacking_team == "blue" else "blue"


def _r(v: float, nd: int = 2) -> float:
    return round(float(v), nd) + 0.0


def to_world(uv: np.ndarray, spec: ClipSpec) -> np.ndarray:
    out = np.array(uv, dtype=float, copy=True)
    if spec.mirror:
        out[..., 1] = 68.0 - out[..., 1]
    if spec.attack_direction == -1:
        out[..., 0] = 105.0 - out[..., 0]
        out[..., 1] = 68.0 - out[..., 1]
    return out


def vel_to_world(v: np.ndarray, spec: ClipSpec) -> np.ndarray:
    out = np.array(v, dtype=float, copy=True)
    if spec.mirror:
        out[..., 1] = -out[..., 1]
    if spec.attack_direction == -1:
        out = -out
    return out


def player_ids(spec: ClipSpec) -> list[str]:
    teams = [spec.attacking_team] * 11 + [spec.defending_team] * 11
    return [f"{teams[i]}_{SHIRT[ROLES[i % 11]]:02d}" for i in range(22)]


def build_public(frames: list[SimFrame], events: list[SimEvent], spec: ClipSpec) -> tuple[dict, list[dict], list[dict]]:
    pids = player_ids(spec)
    teams = [spec.attacking_team] * 11 + [spec.defending_team] * 11
    team_of_side = {0: spec.attacking_team, 1: spec.defending_team}
    obs: list[dict] = []
    for k, f in enumerate(frames):
        pos = to_world(f.pos, spec)
        vel = vel_to_world(f.vel, spec)
        ball = to_world(f.ball, spec)
        obs.append({
            "schema_version": "2.0",
            "match_id": spec.match_id,
            "frame_ref": {"clip_id": spec.clip_id, "frame_index": k},
            "period": spec.period,
            "match_time_s": _r(spec.start_s + k * DT, 1),
            "players": [
                {"player_id": pids[i], "team_id": teams[i], "is_goalkeeper": i % 11 == 0,
                 "position": {"x_m": _r(pos[i, 0]), "y_m": _r(pos[i, 1])},
                 "velocity": {"vx_mps": _r(vel[i, 0]), "vy_mps": _r(vel[i, 1])}}
                for i in range(22)
            ],
            "ball": {"position": {"x_m": _r(ball[0]), "y_m": _r(ball[1])}, "z_m": _r(f.z),
                     "owner_id": pids[f.owner] if f.owner >= 0 else None},
            "possession_team_id": None if f.possession is None else team_of_side[f.possession],
            "ball_state": f.ball_state,
        })
    evs: list[dict] = []
    for n, e in enumerate(events):
        t = _r(spec.start_s + e.t, 1)
        pos = None if e.pos is None else to_world(np.array(e.pos), spec)
        evs.append({
            "event_id": f"{spec.clip_id}_e{n:03d}",
            "match_id": spec.match_id,
            "clip_id": spec.clip_id,
            "event_time_s": t,
            "available_at_s": _r(t + EVENT_LATENCY_S, 1),
            "kind": e.kind,
            "actor_id": None if e.actor is None else pids[e.actor],
            "target_id": None if e.target is None else pids[e.target],
            "related_event_id": None if e.related is None else f"{spec.clip_id}_e{e.related:03d}",
            "position": None if pos is None else {"x_m": _r(pos[0]), "y_m": _r(pos[1])},
        })
    teams_meta = [
        {"team_id": spec.attacking_team, "display_name": TEAMS[spec.attacking_team].name,
         "attack_direction": spec.attack_direction},
        {"team_id": spec.defending_team, "display_name": TEAMS[spec.defending_team].name,
         "attack_direction": -spec.attack_direction},
    ]
    roster = [{"player_id": pids[i], "team_id": teams[i], "jersey": SHIRT[ROLES[i % 11]], "is_goalkeeper": i % 11 == 0}
              for i in range(22)]
    meta = {
        "schema_version": "2.0", "match_id": spec.match_id, "period": spec.period,
        "pitch": {"length_m": 105, "width_m": 68}, "tracking_hz": 5,
        "teams": sorted(teams_meta, key=lambda t: t["team_id"]),
        "roster": sorted(roster, key=lambda r: r["player_id"]),
        "clips": [{"clip_id": spec.clip_id, "start_s": obs[0]["match_time_s"], "end_s": obs[-1]["match_time_s"],
                   "checksum": ""}],
    }
    return meta, obs, evs


def _jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


def clip_checksum(tracking: bytes, events: bytes) -> str:
    h = hashlib.sha256()
    h.update(tracking)
    h.update(b"\n--events--\n")
    h.update(events)
    return "sha256:" + h.hexdigest()


def write_clip(meta: dict, obs: list[dict], evs: list[dict], truth: dict, public_root: Path,
               truth_root: Path | None) -> Path:
    """Validate against the contracts, then write public files (and the private truth, if a root is given)."""
    for row in obs:
        ObservationFrame.model_validate(row)
    for row in evs:
        MatchEvent.model_validate(row)
    tracking, events = _jsonl(obs), _jsonl(evs)
    meta = json.loads(json.dumps(meta))
    meta["clips"][0]["checksum"] = clip_checksum(tracking, events)
    MatchMeta.model_validate(meta)
    clip_id = meta["clips"][0]["clip_id"]
    out = Path(public_root) / clip_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "tracking.jsonl").write_bytes(tracking)
    (out / "events.jsonl").write_bytes(events)
    if truth_root is not None:
        Path(truth_root).mkdir(parents=True, exist_ok=True)
        (Path(truth_root) / f"{clip_id}.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n",
                                                          encoding="utf-8")
    return out
