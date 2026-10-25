"""Prefix-only observation buffer and attack-frame normalisation (spec 5.1, 5.2).

The buffer only grows by appending the next observed frame and the events
whose ``available_at_s`` has passed. Every analysis function receives the
buffer and a frame index ``k`` and may read frames ``<= k`` only.

Attack frame for team a with direction d: u = x, v = y (d=+1) or
u = 105 - x, v = 68 - y (d=-1). The defending team's goal is u = 105, so a
defender's own-goal depth is 105 - u.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..contracts import MatchEvent, MatchMeta, ObservationFrame


@dataclass
class ObservationBuffer:
    meta: MatchMeta
    clip_id: str
    pids: tuple[str, ...] = ()
    team: np.ndarray = field(default_factory=lambda: np.array([], dtype=object))
    is_gk: np.ndarray = field(default_factory=lambda: np.array([], dtype=bool))
    t: list[float] = field(default_factory=list)
    pos: list[np.ndarray] = field(default_factory=list)
    vel: list[np.ndarray] = field(default_factory=list)
    ball: list[np.ndarray] = field(default_factory=list)
    z: list[float] = field(default_factory=list)
    owner: list[int] = field(default_factory=list)
    possession: list[str | None] = field(default_factory=list)
    ball_state: list[str] = field(default_factory=list)
    frames: list[ObservationFrame] = field(default_factory=list)
    events: list[MatchEvent] = field(default_factory=list)

    def append(self, frame: ObservationFrame, arrived: list[MatchEvent] = ()) -> int:
        if frame.frame_ref.clip_id != self.clip_id:
            raise ValueError("frame belongs to another clip")
        if self.frames and frame.match_time_s <= self.t[-1]:
            raise ValueError("frames must arrive in increasing match time")
        if self.frames and frame.frame_ref.frame_index != self.frames[-1].frame_ref.frame_index + 1:
            raise ValueError("frames must arrive without gaps")
        ids = tuple(p.player_id for p in frame.players)
        if not self.pids:
            self.pids = ids
            self.team = np.array([p.team_id for p in frame.players], dtype=object)
            self.is_gk = np.array([p.is_goalkeeper for p in frame.players], dtype=bool)
            self._index = {pid: i for i, pid in enumerate(ids)}
        elif ids != self.pids:
            order = [self._index[p] for p in ids]
            if sorted(order) != list(range(22)):
                raise ValueError("player set changed (substitutions are out of P0 scope)")
        pos = np.empty((22, 2))
        vel = np.empty((22, 2))
        for p in frame.players:
            i = self._index[p.player_id]
            pos[i] = (p.position.x_m, p.position.y_m)
            vel[i] = (p.velocity.vx_mps, p.velocity.vy_mps)
        self.t.append(frame.match_time_s)
        self.pos.append(pos)
        self.vel.append(vel)
        self.ball.append(np.array([frame.ball.position.x_m, frame.ball.position.y_m]))
        self.z.append(frame.ball.z_m)
        self.owner.append(self._index[frame.ball.owner_id] if frame.ball.owner_id else -1)
        self.possession.append(frame.possession_team_id)
        self.ball_state.append(frame.ball_state)
        self.frames.append(frame)
        for e in arrived:
            if e.available_at_s > frame.match_time_s + 1e-9:
                raise ValueError(f"event {e.event_id} delivered before it was available")
            self.events.append(e)
        return len(self.t) - 1

    def __len__(self) -> int:
        return len(self.t)

    def idx(self, pid: str) -> int:
        return self._index[pid]

    def team_ids(self) -> list[str]:
        return [t.team_id for t in self.meta.teams]

    def other(self, team_id: str) -> str:
        return next(t for t in self.team_ids() if t != team_id)

    def direction(self, team_id: str) -> int:
        return self.meta.attack_direction(team_id)

    def members(self, team_id: str, outfield: bool = True) -> np.ndarray:
        m = self.team == team_id
        if outfield:
            m = m & ~self.is_gk
        return np.flatnonzero(m)

    def gk(self, team_id: str) -> int:
        return int(np.flatnonzero((self.team == team_id) & self.is_gk)[0])

    def window(self, k: int, seconds: float) -> range:
        n = int(round(seconds * self.meta.tracking_hz))
        return range(max(0, k - n), k + 1)

    # ------------------------------------------------------------ attack frame
    def uv(self, k: int, attacking: str) -> np.ndarray:
        return to_attack_frame(self.pos[k], self.direction(attacking))

    def uv_vel(self, k: int, attacking: str) -> np.ndarray:
        return vel_to_attack_frame(self.vel[k], self.direction(attacking))

    def ball_uv(self, k: int, attacking: str) -> np.ndarray:
        return to_attack_frame(self.ball[k], self.direction(attacking))

    def events_until(self, t: float) -> list[MatchEvent]:
        return [e for e in self.events if e.available_at_s <= t + 1e-9]


def to_attack_frame(xy: np.ndarray, direction: int) -> np.ndarray:
    xy = np.asarray(xy, dtype=float)
    if direction == 1:
        return xy.copy()
    out = np.empty_like(xy)
    out[..., 0] = 105.0 - xy[..., 0]
    out[..., 1] = 68.0 - xy[..., 1]
    return out


def to_world(uv: np.ndarray, direction: int) -> np.ndarray:
    return to_attack_frame(uv, direction)  # the 180-degree rotation is its own inverse


def vel_to_attack_frame(v: np.ndarray, direction: int) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return v.copy() if direction == 1 else -v
