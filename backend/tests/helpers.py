"""Test helpers that read public files (and, for offline checks, private truth)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.simulator.validate import load_events, load_tracking


def manifest(scenario_dir: Path) -> dict:
    return json.loads((scenario_dir / "manifest.json").read_text())


def clip_frames(scenario_dir: Path, clip_id: str) -> list[dict]:
    m = manifest(scenario_dir)
    clip = next(c for c in m["clips"] if c["clip_id"] == clip_id)
    return load_tracking(scenario_dir / clip["tracking_file"])


def events(scenario_dir: Path) -> list[dict]:
    return load_events(scenario_dir / manifest(scenario_dir)["events_file"])


def truth_rows(path: Path) -> dict[str, dict]:
    return {r["clip_id"]: r for r in (json.loads(l) for l in path.read_text().splitlines() if l.strip())}


def series(frames: list[dict], pid: str) -> np.ndarray:
    out = np.empty((len(frames), 2))
    for i, f in enumerate(frames):
        p = next(p for p in f["players"] if p["player_id"] == pid)
        out[i] = (p["position"]["x_m"], p["position"]["y_m"])
    return out


def x_attacking(xy: np.ndarray, direction: int) -> np.ndarray:
    """Longitudinal progress toward the opponent goal for a team attacking in ``direction``."""
    return xy[:, 0] if direction == 1 else 105.0 - xy[:, 0]


def width(xy: np.ndarray) -> np.ndarray:
    """Distance from the pitch centre line y=34 (larger = wider / more outside)."""
    return np.abs(xy[:, 1] - 34.0)
