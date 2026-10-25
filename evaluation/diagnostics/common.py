"""Shared loading for the offline football diagnostics (evaluation only: may join private truth, never feeds back).

A clip is the public observation files (or the same rows straight from the generator) plus its private truth. Roles
come from the generator's fixed shirt numbers, which is generator-private knowledge and stays in evaluation/.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.simulator.roster import SHIRT  # noqa: E402

ROLE_BY_SHIRT = {n: r for r, n in SHIRT.items()}
PUBLIC = ROOT / "data" / "public"
TRUTH = ROOT / "evaluation" / "private_truth"
DT = 0.2


@dataclass
class Clip:
    clip_id: str
    meta: dict
    rows: list[dict]  # tracking rows, one per frame
    events: list[dict]
    truth: dict
    pids: list[str]  # fixed player order
    xy: np.ndarray  # (frames, 22, 2) world metres
    ball: np.ndarray  # (frames, 3) x, y, z
    t: np.ndarray  # clip-relative seconds

    @property
    def start_s(self) -> float:
        return float(self.meta["clips"][0]["start_s"])

    def team(self, pid: str) -> str:
        return pid.split("_")[0]

    def role(self, pid: str) -> str:
        return ROLE_BY_SHIRT[int(pid.split("_")[1])]

    def direction(self, team: str) -> int:
        return next(t["attack_direction"] for t in self.meta["teams"] if t["team_id"] == team)

    def u(self, team: str, x: np.ndarray) -> np.ndarray:
        """Distance along the attack direction of ``team`` (attack frame u)."""
        return x if self.direction(team) == 1 else 105.0 - x


def from_rows(clip_id: str, meta: dict, rows: list[dict], events: list[dict], truth: dict) -> Clip:
    pids = sorted(p["player_id"] for p in rows[0]["players"])
    xy = np.zeros((len(rows), len(pids), 2))
    ball = np.zeros((len(rows), 3))
    for k, r in enumerate(rows):
        pos = {p["player_id"]: (p["position"]["x_m"], p["position"]["y_m"]) for p in r["players"]}
        xy[k] = [pos[p] for p in pids]
        b = r["ball"]
        ball[k] = (b["position"]["x_m"], b["position"]["y_m"], b.get("z_m", 0.0))
    t = np.array([r["match_time_s"] for r in rows]) - float(meta["clips"][0]["start_s"])
    return Clip(clip_id, meta, rows, events, truth, pids, xy, ball, t)


def load_committed(clip_id: str) -> Clip:
    d = PUBLIC / clip_id
    meta = json.loads((d / "manifest.json").read_text())
    rows = [json.loads(x) for x in (d / "tracking.jsonl").read_text().splitlines() if x.strip()]
    events = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines() if x.strip()]
    truth = json.loads((TRUTH / f"{clip_id}.json").read_text())
    return from_rows(clip_id, meta, rows, events, truth)


def generate(family: str, variant: str, seed: int, clip_id: str = "clip_diag", attacking_team: str = "blue",
             attack_direction: int = 1) -> Clip:
    """Simulate a clip in memory with the current generator (nothing is written)."""
    from app.simulator.defense.generate import generate_clip

    cfg = {"clip_id": clip_id, "family": family, "variant": variant, "seed": seed, "attacking_team": attacking_team,
           "attack_direction": attack_direction, "start_s": 0.0}
    meta, rows, events, truth = generate_clip(seed, cfg)
    return from_rows(clip_id, meta, rows, events, truth)
