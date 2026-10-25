"""Shared helpers for the v2 (defensive intelligence) tests."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.analytics.engine import TacticalAnalyzer
from app.contracts import MatchEvent, MatchMeta, ObservationFrame

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "data" / "public"
TRUTH = ROOT / "evaluation" / "private_truth"


def load_public(clip_dir: Path):
    meta = MatchMeta.model_validate_json((clip_dir / "manifest.json").read_text())
    frames = [ObservationFrame.model_validate_json(x) for x in (clip_dir / "tracking.jsonl").read_text().splitlines()]
    events = [MatchEvent.model_validate_json(x) for x in (clip_dir / "events.jsonl").read_text().splitlines()]
    return meta, frames, events


def run_analyzer(meta, frames, events, upto: int | None = None, cfg=None) -> TacticalAnalyzer:
    an = TacticalAnalyzer(meta, frames[0].frame_ref.clip_id, cfg)
    seen: set[str] = set()
    for f in frames[: (upto + 1) if upto is not None else None]:
        arrived = [e for e in events if e.available_at_s <= f.match_time_s + 1e-9 and e.event_id not in seen]
        seen |= {e.event_id for e in arrived}
        an.step(f, arrived)
    return an


def write_public(meta: dict, obs: list[dict], evs: list[dict], root: Path) -> Path:
    """Write public files the way the generator does (used to build hand-made clips)."""
    from app.simulator.defense.publish import write_clip
    return write_clip(meta, obs, evs, {}, root, None)


# ---------------------------------------------------------------- hand-built frames
ROSTER_A = [f"blue_{i:02d}" for i in range(1, 12)]
ROSTER_D = [f"red_{i:02d}" for i in range(1, 12)]


def meta_dict(clip_id: str = "clip_t", attack_dir: int = 1, start: float = 0.0, end: float = 10.0) -> dict:
    roster = [{"player_id": p, "team_id": "blue", "jersey": int(p[-2:]), "is_goalkeeper": p.endswith("_01")}
              for p in ROSTER_A]
    roster += [{"player_id": p, "team_id": "red", "jersey": int(p[-2:]), "is_goalkeeper": p.endswith("_01")}
               for p in ROSTER_D]
    return {"schema_version": "2.0", "match_id": "m_t", "period": 1, "pitch": {"length_m": 105, "width_m": 68},
            "tracking_hz": 5, "teams": [{"team_id": "blue", "display_name": "Blue FC", "attack_direction": attack_dir},
                                        {"team_id": "red", "display_name": "Red United",
                                         "attack_direction": -attack_dir}],
            "roster": roster, "clips": [{"clip_id": clip_id, "start_s": start, "end_s": end,
                                         "checksum": "sha256:" + "0" * 64}]}


def frame_dict(k: int, pos: dict[str, tuple[float, float]], vel: dict[str, tuple[float, float]] | None = None,
               owner: str | None = "blue_05", clip_id: str = "clip_t", ball=None, possession: str | None = "blue",
               state: str | None = None) -> dict:
    vel = vel or {}
    players = []
    for p in ROSTER_A + ROSTER_D:
        x, y = pos[p]
        vx, vy = vel.get(p, (0.0, 0.0))
        players.append({"player_id": p, "team_id": p.split("_")[0], "is_goalkeeper": p.endswith("_01"),
                        "position": {"x_m": float(x), "y_m": float(y)}, "velocity": {"vx_mps": vx, "vy_mps": vy}})
    if ball is None:
        ball = pos[owner] if owner else (52.5, 34.0)
    return {"schema_version": "2.0", "match_id": "m_t", "frame_ref": {"clip_id": clip_id, "frame_index": k},
            "period": 1, "match_time_s": round(k * 0.2, 1), "players": players,
            "ball": {"position": {"x_m": float(ball[0]), "y_m": float(ball[1])}, "z_m": 0.0, "owner_id": owner},
            "possession_team_id": possession, "ball_state": state or ("controlled" if owner else "in_flight")}


def base_positions() -> dict[str, tuple[float, float]]:
    """A neutral 4-4-2 versus 4-4-2 picture: blue attacks +x, red defends x=105."""
    blue = {"blue_01": (5, 34), "blue_02": (25, 8), "blue_05": (20, 24), "blue_04": (20, 44), "blue_03": (25, 60),
            "blue_06": (35, 34), "blue_08": (40, 22), "blue_10": (40, 46), "blue_07": (55, 10),
            "blue_09": (58, 34), "blue_11": (55, 58)}
    red = {"red_01": (100, 34), "red_02": (70, 58), "red_05": (72, 40), "red_04": (72, 28), "red_03": (70, 10),
           "red_06": (60, 34), "red_08": (58, 46), "red_10": (58, 22), "red_07": (45, 10), "red_09": (45, 34),
           "red_11": (45, 58)}
    return {**blue, **red}


def frames_from(positions: list[dict], vels: list[dict] | None = None, **kw) -> tuple[MatchMeta, list[ObservationFrame]]:
    vels = vels or [{} for _ in positions]
    meta = MatchMeta.model_validate(meta_dict(end=round((len(positions) - 1) * 0.2, 1) or 0.2))
    frames = [ObservationFrame.model_validate(frame_dict(k, p, v, **kw)) for k, (p, v) in enumerate(zip(positions, vels))]
    return meta, frames


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def finite_diff_speed(a: np.ndarray, b: np.ndarray, dt: float = 0.2) -> float:
    return float(np.linalg.norm(b - a) / dt)
