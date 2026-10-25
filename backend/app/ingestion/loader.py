"""Load PUBLIC scenario files into validated models and numpy arrays.

This is the only way live code reads fixtures. It reads only the public
manifest, events and tracking files, never generator-side data or code.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..schemas import MatchEvent, ScenarioManifest, TrackingFrame


def read_jsonl_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass(frozen=True)
class ClipData:
    """One clip as arrays. Player order is fixed by the first frame."""

    match_id: str
    clip_id: str
    period: int
    attack_direction: dict[str, int]
    frame_index: np.ndarray  # (T,)
    t: np.ndarray  # (T,) match_time_s
    player_ids: tuple[str, ...]  # (22,)
    team_ids: tuple[str, ...]
    roles: tuple[str, ...]
    pos: np.ndarray  # (T, 22, 2)
    vel: np.ndarray  # (T, 22, 2)
    ball: np.ndarray  # (T, 2)
    owner: np.ndarray  # (T,) player index or -1
    possession: tuple[str | None, ...]  # (T,)
    _index: dict[str, int] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index.update({pid: i for i, pid in enumerate(self.player_ids)})

    def __len__(self) -> int:
        return len(self.t)

    def idx(self, player_id: str) -> int:
        return self._index[player_id]

    def find(self, team_id: str, role: str) -> int | None:
        for i, (t, r) in enumerate(zip(self.team_ids, self.roles)):
            if t == team_id and r == role:
                return i
        return None

    def teams(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.team_ids)))

    def until(self, k: int) -> ClipData:
        """Prefix containing frames 0..k inclusive (what a live consumer had seen)."""
        s = slice(0, k + 1)
        return ClipData(self.match_id, self.clip_id, self.period, self.attack_direction, self.frame_index[s],
                        self.t[s], self.player_ids, self.team_ids, self.roles, self.pos[s], self.vel[s],
                        self.ball[s], self.owner[s], self.possession[s])


def clip_from_frames(frames: list[TrackingFrame]) -> ClipData:
    if not frames:
        raise ValueError("a clip needs at least one frame")
    first = frames[0]
    pids = tuple(p.player_id for p in first.players)
    index = {pid: i for i, pid in enumerate(pids)}
    T = len(frames)
    pos = np.empty((T, 22, 2))
    vel = np.empty((T, 22, 2))
    ball = np.empty((T, 2))
    owner = np.full(T, -1, dtype=int)
    for k, f in enumerate(frames):
        if f.clip_id != first.clip_id or f.attack_direction != first.attack_direction:
            raise ValueError("frames from different clips/periods cannot be combined")
        for p in f.players:
            i = index[p.player_id]
            pos[k, i] = (p.position.x_m, p.position.y_m)
            vel[k, i] = (p.velocity.x_m, p.velocity.y_m)
        ball[k] = (f.ball_position.x_m, f.ball_position.y_m)
        if f.ball_owner_id is not None:
            owner[k] = index[f.ball_owner_id]
    return ClipData(
        match_id=first.match_id,
        clip_id=first.clip_id,
        period=first.period,
        attack_direction=dict(first.attack_direction),
        frame_index=np.array([f.frame_index for f in frames]),
        t=np.array([f.match_time_s for f in frames]),
        player_ids=pids,
        team_ids=tuple(p.team_id for p in first.players),
        roles=tuple(p.nominal_role for p in first.players),
        pos=pos,
        vel=vel,
        ball=ball,
        owner=owner,
        possession=tuple(f.possession_team_id for f in frames),
    )


@dataclass
class Scenario:
    root: Path
    manifest: ScenarioManifest
    events: list[MatchEvent]
    clips: dict[str, ClipData]
    frames: dict[str, list[TrackingFrame]]

    def clip_events(self, clip_id: str) -> list[MatchEvent]:
        return [e for e in self.events if e.clip_id == clip_id]


def load_scenario(scenario_dir: Path | str) -> Scenario:
    root = Path(scenario_dir)
    manifest = ScenarioManifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
    events = [MatchEvent.model_validate(e) for e in read_jsonl(root / manifest.events_file)]
    frames: dict[str, list[TrackingFrame]] = {}
    clips: dict[str, ClipData] = {}
    for c in manifest.clips:
        fr = [TrackingFrame.model_validate(d) for d in read_jsonl_gz(root / c.tracking_file)]
        frames[c.clip_id] = fr
        clips[c.clip_id] = clip_from_frames(fr)
    return Scenario(root, manifest, events, clips, frames)
