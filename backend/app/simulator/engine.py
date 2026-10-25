"""Behaviour-based flank-play simulator (generator-private).

The simulation runs in a canonical frame: the attacking team "A" attacks
toward +x and the action flank is the low-y touchline (A's right side). The
exporter mirrors/rotates into world coordinates. Players steer toward
role/phase targets with capped speed and acceleration, temporally correlated
noise, separation and pitch bounds. Defenders react to *lagged observations*
of attackers according to a response profile, so trajectories (and therefore
any spatial consequences) emerge from behaviour rather than being animated
from a label. Ball decisions at the end of a move use observable lane
geometry, not the recipe label.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .recipes import ClipParams
from .roster import ROLE_INDEX

DT = 0.2
PITCH_L, PITCH_W = 105.0, 68.0
MARGIN = 0.5
TARGET_MARGIN = 1.2
N_PLAYERS = 22
ACCEL_MPS2 = 4.5
ARRIVE_DECEL = 0.8 * ACCEL_MPS2
SEPARATION_M = 1.4
LANE_BLOCK_RADIUS_M = 1.8
MAX_FRAMES = 125
LB_MAX_STEP_M = 4.0  # a marking fullback stays within this distance in front of his line
MID_MAX_STEP_M = 2.0  # a marking midfielder stays within this distance in front of his zone
FORWARD_GAIN_M = 3.0  # an early release must gain at least this much ground
THREAT_RANGE_M = 15.0
ANTICIPATE_S = 0.6  # man-markers aim where their attacker is heading, not where he was  # defenders react to a fast runner closing within this distance behind the winger
FORWARD_LOOK_AFTER_S = 1.6
RUN_LOOKAHEAD_M = 5.0  # pure-pursuit distance along a curved run
CARRY_SPEED_MPS = 5.0  # a receiver driving at goal with the ball
SETTLE_SPEED_MPS = 1.8  # any other ball owner moving the ball on
FINAL_THIRD_X = 75.0
WINGER_AFTER_PASS_STEP = np.array([1.5, 4.0])  # mostly inside (+y in the canonical frame), a little forward
WINGER_AFTER_PASS_MPS = 2.5
# The winger releases early only into a local flank combination; the striker
# and recycling options are considered at the decision timeout. Identical for
# every flank recipe so ball timing does not encode the recipe.
FLANK_EARLY_OPTIONS = ["RB", "RCM"]
FLANK_FORWARD_OPTIONS = ["RB", "RCM", "ST"]
FLANK_FALLBACK_OPTIONS = ["DM", "RB", "RCB"]


def A(role: str) -> int:
    return ROLE_INDEX[role]


def D(role: str) -> int:
    return 11 + ROLE_INDEX[role]


# Base slots in the canonical frame with the ball at (70, 34).
# Order follows roster.ROLES: GK RB RCB LCB LB DM RCM LCM RW ST LW.
_A_BASE = np.array(
    [(16, 34), (60, 9), (44, 26), (44, 42), (58, 59), (54, 34), (64, 25), (66, 43), (77, 6), (82, 34), (78, 62)],
    dtype=float,
)
_A_DX = np.array([0.15] + [0.7] * 10)
_A_DY = np.array([0.1, 0.15, 0.3, 0.3, 0.15, 0.35, 0.35, 0.35, 0.1, 0.3, 0.1])
# Defending team protects the goal at x=105. Its left side faces A's right flank.
_D_BASE = np.array(
    [(101, 34), (84, 54), (85, 40), (85, 28), (84, 14), (78, 34), (74, 43), (74, 25), (67, 52), (59, 34), (67, 16)],
    dtype=float,
)
_D_DX = np.array([0.04] + [0.4] * 10)
_D_DY = np.array([0.12] + [0.45] * 4 + [0.35] * 3 + [0.3] * 3)


def slot_targets(ball: np.ndarray) -> np.ndarray:
    bx, by = float(ball[0]), float(ball[1])
    out = np.empty((N_PLAYERS, 2))
    out[:11, 0] = _A_BASE[:, 0] + _A_DX * (bx - 70.0)
    out[:11, 1] = _A_BASE[:, 1] + _A_DY * (by - 34.0)
    out[11:, 0] = _D_BASE[:, 0] + _D_DX * (bx - 70.0)
    out[11:, 1] = _D_BASE[:, 1] + _D_DY * (by - 34.0)
    out[A("GK"), 0] = np.clip(out[A("GK"), 0], 4.0, 24.0)
    out[D("GK"), 0] = np.clip(out[D("GK"), 0], 99.0, 103.0)
    out[1:11, 0] = np.minimum(out[1:11, 0], 100.0)
    out[12:, 0] = np.minimum(out[12:, 0], 97.0)
    out[:, 0] = np.clip(out[:, 0], MARGIN, PITCH_L - MARGIN)
    out[:, 1] = np.clip(out[:, 1], MARGIN, PITCH_W - MARGIN)
    return out


@dataclass
class SimFrame:
    k: int
    pos: np.ndarray
    vel: np.ndarray
    ball: np.ndarray
    owner: int  # -1 = nobody (ball in flight)


@dataclass
class SimEvent:
    k: int
    event_type: str
    team: str | None  # "A", "D" or None
    actor: int | None
    target: int | None
    start: np.ndarray | None
    end: np.ndarray | None
    success: bool | None
    context: dict = field(default_factory=dict)


@dataclass
class SimResult:
    frames: list[SimFrame]
    events: list[SimEvent]
    markers: dict[str, float | int | str | None]


@dataclass
class _Flight:
    start: np.ndarray
    end: np.ndarray
    k0: int
    k1: int
    passer: int
    receiver: int
    event_index: int


# Circulation graph (canonical roles) for central possession recycling.
_CIRCULATION = {
    "RCM": ["DM", "LCM"],
    "DM": ["RCB", "LCB", "LCM", "RCM"],
    "LCM": ["DM", "LB", "LW"],
    "RCB": ["DM", "LCB", "RB"],
    "LCB": ["DM", "RCB", "LB"],
    "LB": ["LCM", "LCB", "LW"],
    "LW": ["LCM", "LB"],
    "RB": ["RCB", "RCM"],
}


class FlankSimulation:
    def __init__(self, p: ClipParams, rng: np.random.Generator):
        self.p = p
        self.rng = rng
        self.k = 0
        carrier = np.array(p.carrier_start)
        pos = slot_targets(carrier + (0.5, 0.0)) + rng.normal(0.0, 0.8, (N_PLAYERS, 2))
        pos[A("RCM")] = carrier
        pos[A("RW")] = p.winger_start
        pos[A("RB")] = p.fullback_start
        pos[D("LB")] = np.array(p.winger_start) + (p.mark_dx_m + 4.0, p.mark_dy_m + 2.0)
        pos[D("LW")] = np.array(p.fullback_start) + (rng.uniform(5.0, 8.0), rng.uniform(4.0, 7.0))
        self.pos = self._clip(pos)
        vel = rng.normal(0.0, 0.3, (N_PLAYERS, 2))
        vel[:11, 0] += 1.0
        self.vel = vel
        self.sigma = np.full(N_PLAYERS, 0.8)
        for i in (A("RB"), A("RW"), A("RCM"), D("LB"), D("LW"), D("LCM")):
            self.sigma[i] = 0.3
        self.noise = rng.normal(0.0, 1.0, (N_PLAYERS, 2)) * self.sigma[:, None]
        self.pos_hist: list[np.ndarray] = [self.pos.copy()]
        self.ball_hist: list[np.ndarray] = []

        self.owner = A("RCM")
        self.ball = self.pos[self.owner] + (0.5, 0.0)
        self.flight: _Flight | None = None
        self.owner_since_k = 0
        self.owner_since_pos = self.ball.copy()

        self.stage = "buildup"
        self.done = False
        self.run_start_t: float | None = None
        self.w_ref: np.ndarray | None = None
        self.w_recv_t: float | None = None
        self.w_recv_pos: np.ndarray | None = None
        self.pass_in_t: float | None = None
        self.parallel_decide_t: float | None = None
        self.aborted = False
        self.retreat: np.ndarray | None = None
        self.run_path: np.ndarray | None = None
        self.run_path_i = 0
        self.brake = np.full(N_PLAYERS, np.nan)
        self.threat = False
        self.threat_x0 = 0.0
        self.handed_over = False
        self.tracker_engaged = False
        self.passes_done = 0
        self.next_pass_t = 0.0
        self.tail_end: float | None = None
        self.pressure_pairs: set[tuple[int, int]] = set()

        self.frames: list[SimFrame] = []
        self.events: list[SimEvent] = []
        self.markers: dict[str, float | int | str | None] = {
            "pass_in_t": None, "winger_receive_t": None, "run_start_t": None, "abort_t": None,
            "threat_t": None, "handover_t": None, "decision_t": None, "final_receiver_index": None,
        }
        self._record()

    # ------------------------------------------------------------------ utils
    @property
    def t(self) -> float:
        return self.k * DT

    @staticmethod
    def _clip(pos: np.ndarray) -> np.ndarray:
        out = pos.copy()
        out[..., 0] = np.clip(out[..., 0], MARGIN, PITCH_L - MARGIN)
        out[..., 1] = np.clip(out[..., 1], MARGIN, PITCH_W - MARGIN)
        return out

    def _lagged(self, i: int, lag_s: float) -> np.ndarray:
        j = max(0, len(self.pos_hist) - 1 - int(round(lag_s / DT)))
        return self.pos_hist[j][i]

    def _lagged_vel(self, i: int, lag_s: float) -> np.ndarray:
        return (self._lagged(i, lag_s) - self._lagged(i, lag_s + 0.4)) / 0.4

    def _predicted(self, i: int, lag_s: float, horizon_s: float = ANTICIPATE_S) -> np.ndarray:
        """Where a defender expects player i to be: lagged observation + lagged velocity."""
        return self._lagged(i, lag_s) + self._lagged_vel(i, lag_s) * horizon_s

    def _ball_ref(self) -> np.ndarray:
        if len(self.ball_hist) <= 2:
            return self.ball_hist[0]
        return self.ball_hist[-3]

    def _record(self) -> None:
        self.ball_hist.append(self.ball.copy())
        self.frames.append(SimFrame(self.k, self.pos.copy(), self.vel.copy(), self.ball.copy(), self.owner))

    def _emit(self, event_type: str, team: str | None, actor: int | None, target: int | None,
              start: np.ndarray | None, end: np.ndarray | None, success: bool | None,
              context: dict | None = None) -> int:
        self.events.append(SimEvent(self.k, event_type, team, actor, target,
                                    None if start is None else start.copy(),
                                    None if end is None else end.copy(), success, context or {}))
        return len(self.events) - 1

    def lane_open(self, passer: int, receiver: int) -> bool:
        a = self.ball if passer == self.owner else self.pos[passer]
        b = self.pos[receiver]
        ab = b - a
        L2 = float(ab @ ab)
        if L2 < 1e-9:
            return False
        for j in range(11, N_PLAYERS):
            s = float(np.clip(((self.pos[j] - a) @ ab) / L2, 0.05, 1.0))
            if np.linalg.norm(a + s * ab - self.pos[j]) < LANE_BLOCK_RADIUS_M:
                return False
        return True

    def _open_forward(self, candidates: list[str]) -> int | None:
        passer = self.owner
        px = self.pos[passer, 0]
        fwd = [
            A(r) for r in candidates
            if A(r) != passer
            and self.pos[A(r), 0] >= px + FORWARD_GAIN_M
            and np.linalg.norm(self.pos[A(r)] - self.pos[passer]) >= 4.0
            and self.lane_open(passer, A(r))
        ]
        return max(fwd, key=lambda i: self.pos[i, 0]) if fwd else None

    def _choose(self, candidates: list[str], fallback: list[str]) -> int:
        passer = self.owner
        fwd = self._open_forward(candidates)
        if fwd is not None:
            return fwd
        for r in fallback:
            if A(r) != passer and self.lane_open(passer, A(r)):
                return A(r)
        return A(fallback[0])

    # ------------------------------------------------------------- ball flow
    def _release(self, receiver: int) -> None:
        p = self.p
        passer = self.owner
        start = self.ball.copy()
        moved = float(np.linalg.norm(start - self.owner_since_pos))
        if moved >= 3.0:
            self._emit("carry", "A", passer, None, self.owner_since_pos, start, True,
                       {"start_match_offset_s": round(self.owner_since_k * DT, 3), "distance_m": round(moved, 1)})
        t_est = float(np.linalg.norm(self.pos[receiver] - start)) / p.pass_speed_mps
        landing = self.pos[receiver] + self.vel[receiver] * t_est * 0.7
        landing = self._clip(landing[None, :])[0]
        landing[0] = np.clip(landing[0], 1.0, PITCH_L - 1.0)
        landing[1] = np.clip(landing[1], 1.0, PITCH_W - 1.0)
        dist = float(np.linalg.norm(landing - start))
        n = max(2, math.ceil(dist / p.pass_speed_mps / DT))
        ev = self._emit("pass", "A", passer, receiver, start, landing, None, {"length_m": round(dist, 1)})
        self.flight = _Flight(start, landing, self.k, self.k + n, passer, receiver, ev)
        self.owner = -1

    def _arrive(self) -> None:
        f = self.flight
        assert f is not None
        self.ball = f.end.copy()
        self.owner = f.receiver
        self.owner_since_k = self.k
        self.owner_since_pos = self.ball.copy()
        self.flight = None
        self._emit("receive", "A", f.receiver, f.passer, None, self.ball, True,
                   {"pass_event_index": f.event_index})
        if self.stage == "to_winger" and f.receiver == A("RW"):
            self.stage = "winger_ball"
            self.w_recv_t = self.t
            self.w_recv_pos = self.pos[A("RW")].copy()
            self.markers["winger_receive_t"] = self.t
        elif self.stage == "final_flight":
            self._start_tail()
        elif self.stage == "circulate":
            self.next_pass_t = self.t + float(self.rng.uniform(1.2, 2.2))
            if self.passes_done >= self.p.n_passes:
                self._start_tail()

    def _start_tail(self) -> None:
        self.stage = "tail"
        self.tail_end = max(self.t + self.p.tail_s, self.p.min_duration_s)

    def _run_resolved(self) -> bool:
        """The off-ball run has played out, so the clip may end without truncating it."""
        p, t = self.p, self.t
        if p.run_mode == "none":
            return True
        if self.run_start_t is None or t < self.run_start_t:
            return False
        if p.run_mode in ("overlap", "underlap"):
            arrived = np.linalg.norm(self.pos[A("RB")] - self._run_target()) < 2.0
            return bool(arrived) or t >= self.run_start_t + 7.0
        if p.run_mode == "aborted":
            return self.markers["abort_t"] is not None and t >= float(self.markers["abort_t"]) + 2.0
        return t >= self.run_start_t + 4.0

    # ---------------------------------------------------------------- script
    def _update_script(self) -> None:
        p, t = self.p, self.t
        if self.stage == "buildup" and t >= p.t_pass_in_s:
            self.pass_in_t = t
            self.markers["pass_in_t"] = t
            if p.run_mode in ("overlap", "underlap", "support", "aborted"):
                self._release(A("RW"))
                assert self.flight is not None
                self.w_ref = self.flight.end.copy()
                self.stage = "to_winger"
                self.run_start_t = t + p.run_delay_s
            elif p.run_mode == "parallel":
                self.w_ref = np.array(p.winger_start)
                self.stage = "parallel"
                self.run_start_t = t
                self.parallel_decide_t = t + p.decide_after_s + 0.8
            else:
                self.stage = "circulate"
                self.next_pass_t = t
        if self.run_start_t is not None and self.markers["run_start_t"] is None and t >= self.run_start_t:
            self.markers["run_start_t"] = t

        if self.stage == "winger_ball" and self.owner == A("RW"):
            assert self.w_recv_t is not None
            # Same rule for every flank recipe: release forward as soon as a
            # forward option is observably open, otherwise recycle on timeout.
            if t >= self.w_recv_t + FORWARD_LOOK_AFTER_S:
                fwd = self._open_forward(FLANK_EARLY_OPTIONS)
                if fwd is not None:
                    self._final_pass(fwd)
            if self.stage == "winger_ball" and t >= self.w_recv_t + p.decide_after_s:
                self._final_pass(self._choose(FLANK_FORWARD_OPTIONS, FLANK_FALLBACK_OPTIONS))
        elif self.stage == "parallel" and self.owner == A("RCM"):
            assert self.parallel_decide_t is not None
            if t >= self.parallel_decide_t:
                self._final_pass(self._choose(["RW", "ST", "LCM"], ["DM"]))
        elif self.stage == "circulate" and self.owner >= 0 and t >= self.next_pass_t:
            if self.passes_done < p.n_passes:
                role = _role_of(self.owner)
                options = [r for r in _CIRCULATION.get(role, ["DM"]) if self.lane_open(self.owner, A(r))]
                options = options or _CIRCULATION.get(role, ["DM"])
                self.passes_done += 1
                self._release(A(options[int(self.rng.integers(0, len(options)))]))

        if self.stage == "tail" and self.tail_end is not None and t >= self.tail_end - 1e-9 and self._run_resolved():
            self.done = True
        if self.k >= MAX_FRAMES - 1 or t >= p.max_duration_s - 1e-9:
            self.done = True

    def _final_pass(self, recv: int) -> None:
        self.markers["decision_t"] = self.t
        self.markers["final_receiver_index"] = recv
        self.stage = "final_flight"
        self._release(recv)

    def _run_target(self) -> np.ndarray:
        p = self.p
        assert self.w_ref is not None
        if p.run_mode == "underlap":
            return np.array([self.w_ref[0] + p.run_beyond_m, self.w_ref[1] + p.inside_offset_m])
        return np.array([self.w_ref[0] + p.run_beyond_m, p.lane_y_m])

    def _run_path(self) -> np.ndarray:
        """Curved route of the fullback's run, fixed when the run starts.

        A quadratic Bezier whose control point sits at the target's width part
        of the way up: overlaps swing out to the touchline lane and then run
        straight down the line, underlaps cut into the half-space and then run
        forward.
        """
        if self.run_path is None:
            p0, p2 = self.pos[A("RB")].copy(), self._run_target()
            p1 = np.array([p0[0] + float(self.rng.uniform(0.25, 0.45)) * (p2[0] - p0[0]), p2[1]])
            s = np.linspace(0.0, 1.0, 41)[:, None]
            self.run_path = (1 - s) ** 2 * p0 + 2 * s * (1 - s) * p1 + s**2 * p2
        return self.run_path

    def _follow_path(self, i: int, path: np.ndarray) -> tuple[np.ndarray, float]:
        """Pure-pursuit point on ``path`` and the route length left (used for arrival braking)."""
        pos = self.pos[i]
        self.run_path_i += int(np.argmin(np.linalg.norm(path[self.run_path_i:] - pos, axis=1)))
        j = self.run_path_i
        while j < len(path) - 1 and np.linalg.norm(path[j] - pos) < RUN_LOOKAHEAD_M:
            j += 1
        left = float(np.linalg.norm(path[j] - pos)) + float(np.linalg.norm(np.diff(path[j:], axis=0), axis=1).sum())
        return path[j], left

    def _carry_goal(self, i: int) -> np.ndarray:
        """Where a receiver in the final third drives the ball: the box corner from wide, else the penalty spot area."""
        return np.array([100.0, 12.0]) if i == A("RB") else np.array([94.0, 30.0])

    # --------------------------------------------------------------- targets
    def _targets(self) -> tuple[np.ndarray, np.ndarray]:
        p, t, pos = self.p, self.t, self.pos
        tgt = slot_targets(self._ball_ref()) + self.noise
        cap = np.full(N_PLAYERS, 5.5)
        cap[[A("GK"), D("GK")]] = 4.0
        self.brake[:] = np.nan
        W, F, C = A("RW"), A("RB"), A("RCM")
        nz = self.noise

        # Winger
        if p.run_mode == "parallel" and self.run_start_t is not None:
            tgt[W] = np.array(p.winger_start) + (p.winger_run_m, 0.0) + nz[W]
            cap[W] = p.run_speed_mps
        elif self.stage == "winger_ball" and self.w_recv_pos is not None:
            tgt[W] = self.w_recv_pos + np.array(p.winger_drive) + nz[W]
            cap[W] = p.winger_drive_speed_mps
        elif self.w_recv_pos is not None:
            # Pass and move: after releasing, the winger drifts inside toward the box.
            tgt[W] = pos[W] + WINGER_AFTER_PASS_STEP
            cap[W] = WINGER_AFTER_PASS_MPS
        elif p.run_mode != "none":
            tgt[W] = np.array(p.winger_start) + nz[W]
            cap[W] = 3.0

        # Fullback
        run_active = self.run_start_t is not None and t >= self.run_start_t
        if p.run_mode == "none":
            pass
        elif not run_active:
            tgt[F] = np.array(p.fullback_start) + (0.5 * t, 0.0) + nz[F]
            cap[F] = 2.5
        elif p.run_mode in ("overlap", "underlap", "aborted"):
            ran_s = t - float(self.run_start_t)
            if p.run_mode == "aborted" and not self.aborted and ran_s >= 0.8 and pos[W, 0] - pos[F, 0] < p.abort_gap_m:
                self.aborted = True
                self.markers["abort_t"] = t
                self.retreat = pos[F] + (-float(self.rng.uniform(4.0, 7.0)), float(self.rng.uniform(3.0, 5.0)))
            if self.aborted:
                assert self.retreat is not None
                tgt[F], cap[F] = self.retreat, 4.0
            else:
                rt = self._run_target()
                if np.linalg.norm(pos[F] - rt) < 1.5:
                    tgt[F], cap[F] = rt, 3.0
                else:
                    carrot, self.brake[F] = self._follow_path(F, self._run_path())
                    tgt[F], cap[F] = carrot + nz[F], p.run_speed_mps
        elif p.run_mode == "support":
            tgt[F] = np.array([pos[W, 0] - p.support_gap_m, p.lane_y_m])
            cap[F] = 6.5
        elif p.run_mode == "parallel":
            tgt[F] = np.array(p.winger_start) + (p.winger_run_m - p.parallel_gap_m, p.inside_offset_m)
            cap[F] = 0.95 * p.run_speed_mps

        # Third-man run by the original carrier into the half-space
        third_man = p.third_man_run and self.pass_in_t is not None and t >= self.pass_in_t + 0.8
        if third_man and self.w_ref is not None and self.owner != C:
            tgt[C] = self.w_ref + np.array(p.third_man_offset)
            cap[C] = 6.0

        # A runner who receives mid-run carries the ball on along his run. The
        # final receiver of a flank move in the final third drives at goal,
        # slowing when a defender is right in front. Any other owner (build-up
        # carrier, recycling pass) moves the ball on at a walking pace.
        runner_carrying = (
            self.owner == F and run_active and not self.aborted and p.run_mode in ("overlap", "underlap")
            and np.linalg.norm(pos[F] - self._run_target()) >= 1.5
        )
        o = self.owner
        if runner_carrying:
            cap[F] = min(p.run_speed_mps, 6.5)
        elif o >= 0 and o != W:
            # A fullback who never ran past the winger (support, aborted) does not
            # drive past him afterwards either; that would turn a negative clip into an overlap.
            fb_ran = p.run_mode in ("overlap", "underlap") and not self.aborted
            final_drive = (self.stage == "tail" and p.run_mode != "none" and (o != F or fb_ran)
                           and o == self.markers["final_receiver_index"] and pos[o, 0] >= FINAL_THIRD_X)
            if final_drive:
                tgt[o] = self._carry_goal(o)
                ahead = pos[11:] - pos[o]
                blocked = np.any((ahead[:, 0] > 0.0) & (np.linalg.norm(ahead, axis=1) < 2.5))
                cap[o] = 3.0 if blocked else CARRY_SPEED_MPS
            else:
                tgt[o] = pos[o] + (1.5, 0.0)
                cap[o] = SETTLE_SPEED_MPS

        if self.flight is not None:
            tgt[self.flight.receiver] = self.flight.end
            cap[self.flight.receiver] = 7.0

        self._defend(tgt, cap, third_man)
        return tgt, cap

    def _defend(self, tgt: np.ndarray, cap: np.ndarray, third_man: bool) -> None:
        p, t = self.p, self.t
        W, F, C = A("RW"), A("RB"), A("RCM")
        LB, LW, LCM = D("LB"), D("LW"), D("LCM")
        mark = np.array([p.mark_dx_m, p.mark_dy_m])
        # Zonal depths before any man-marking overrides (the line never steps up as a whole here).
        line_x = float(tgt[D("LCB"), 0])
        lcm_zone_x = float(tgt[LCM, 0])

        w_obs = self._lagged(W, p.react_lag_s)
        f_obs = self._lagged(F, p.react_lag_s)
        f_vel = self._lagged_vel(F, p.react_lag_s)
        if not self.threat and f_obs[0] > w_obs[0] - THREAT_RANGE_M and f_vel[0] > 4.0:
            self.threat = True
            self.threat_x0 = float(f_obs[0])
            self.markers["threat_t"] = t

        # Fullback marks the winger only while play is on this flank; with the
        # ball far away he stays in the zonal slot (tucked in with the line).
        if np.linalg.norm(self._ball_ref() - w_obs) < 25.0:
            tgt[LB] = w_obs + mark
            cap[LB] = 6.5

        # Following the runner: shift into his lane, never retreating behind
        # the winger's depth, and stay goal-side once he passes.
        f_pred = self._predicted(F, p.react_lag_s)
        follow = np.array([max(f_pred[0] + 0.8 * p.mark_dx_m, w_obs[0]), f_pred[1] + p.mark_dy_m])
        if self.threat and p.defender_response == "mark_runner":
            tgt[LB] = follow
            cap[LB] = p.lb_speed_mps
        elif self.threat and p.defender_response == "switch_mark":
            if not self.handed_over and f_obs[0] > self.threat_x0 + p.handover_dx_m:
                self.handed_over = True
                self.markers["handover_t"] = t
            if not self.handed_over:
                tgt[LB] = follow
                cap[LB] = p.lb_speed_mps
            else:
                cap[LB] = 7.0

        tgt[LB, 0] = max(float(tgt[LB, 0]), line_x - LB_MAX_STEP_M)

        f_trk = self._lagged(F, p.tracker_lag_s)
        if not self.tracker_engaged and self._lagged_vel(F, p.tracker_lag_s)[0] > 3.0:
            self.tracker_engaged = True
        if self.tracker_engaged:
            if p.defender_response == "hold_line" or (p.defender_response == "switch_mark" and self.handed_over):
                # Drop into the runner's lane around the winger's depth and wait
                # goal-side, then run with him.
                f_pred = self._predicted(F, p.tracker_lag_s)
                tgt[LW] = (max(f_pred[0] + 1.5, w_obs[0] - 2.0), f_pred[1] + 1.0)
                cap[LW] = p.tracker_speed_mps
            elif p.defender_response in ("mark_runner", "switch_mark"):
                tgt[LW] = 0.5 * (tgt[LW] + f_trk)
                cap[LW] = p.tracker_speed_mps

        # The far-side centre-back marks the striker goal-side without stepping
        # more than 2 m out of the defensive line.
        st_obs = self._predicted(A("ST"), p.react_lag_s)
        rcb = D("RCB")
        tgt[rcb] = (max(st_obs[0] + 1.6, tgt[rcb, 0] - 2.0), st_obs[1] + 0.4 * float(np.sign(34.0 - st_obs[1])))
        cap[rcb] = 6.5

        if third_man and p.cover_tracks_third_man:
            c_obs = self._predicted(C, p.lcm_lag_s)
            tgt[LCM] = (max(c_obs[0] + 1.0, lcm_zone_x - MID_MAX_STEP_M), c_obs[1] + 0.6)
            cap[LCM] = p.lcm_speed_mps

    # -------------------------------------------------------------- physics
    def _integrate(self, tgt: np.ndarray, cap: np.ndarray) -> None:
        pos, vel = self.pos, self.vel
        # Targets stay inside the pitch so steering decelerates smoothly instead
        # of being stopped by the hard bound (which would spike acceleration).
        tgt = tgt.copy()
        tgt[:, 0] = np.clip(tgt[:, 0], TARGET_MARGIN, PITCH_L - TARGET_MARGIN)
        tgt[:, 1] = np.clip(tgt[:, 1], TARGET_MARGIN, PITCH_W - TARGET_MARGIN)
        delta = tgt - pos
        dist = np.linalg.norm(delta, axis=1)
        # Path followers brake for the route length left, not for the pursuit point.
        brake = np.where(np.isnan(self.brake), dist, self.brake)
        speed = np.minimum.reduce([cap, np.sqrt(2.0 * ARRIVE_DECEL * brake), brake / 0.5])
        with np.errstate(invalid="ignore", divide="ignore"):
            desired = np.where(dist[:, None] > 1e-6, delta / dist[:, None], 0.0) * speed[:, None]

        diff = pos[:, None, :] - pos[None, :, :]
        d = np.linalg.norm(diff, axis=2)
        close = (d < SEPARATION_M) & (d > 1e-6)
        if close.any():
            w = np.where(close, (SEPARATION_M - d) / np.where(d > 1e-6, d, 1.0), 0.0)
            desired += 2.0 * (w[:, :, None] * diff).sum(axis=1)

        dv = desired - vel
        dvn = np.linalg.norm(dv, axis=1)
        lim = ACCEL_MPS2 * DT
        scale = np.where(dvn > lim, lim / np.maximum(dvn, 1e-9), 1.0)
        new_vel = vel + dv * scale[:, None]
        sp = np.linalg.norm(new_vel, axis=1)
        new_vel *= np.where(sp > 8.8, 8.8 / np.maximum(sp, 1e-9), 1.0)[:, None]
        new_pos = self._clip(pos + new_vel * DT)
        self.vel = (new_pos - pos) / DT
        self.pos = new_pos
        self.pos_hist.append(self.pos.copy())

        theta = 2.5
        self.noise = (
            self.noise * (1.0 - DT / theta)
            + self.rng.normal(0.0, 1.0, (N_PLAYERS, 2)) * (self.sigma[:, None] * math.sqrt(2.0 * DT / theta))
        )

    def _update_ball(self) -> None:
        if self.flight is not None:
            f = self.flight
            if self.k >= f.k1:
                self._arrive()
            else:
                frac = (self.k - f.k0) / (f.k1 - f.k0)
                self.ball = f.start + frac * (f.end - f.start)
        elif self.owner >= 0:
            v = self.vel[self.owner]
            sp = float(np.linalg.norm(v))
            dirn = v / sp if sp > 0.5 else np.array([1.0, 0.0])
            self.ball = self.pos[self.owner] + 0.5 * dirn

    def _detect_pressure(self) -> None:
        if self.owner < 0 or self.owner >= 11:
            self.pressure_pairs.clear()
            return
        d = np.linalg.norm(self.pos[11:] - self.pos[self.owner], axis=1)
        for j in range(11):
            pair = (11 + j, self.owner)
            if d[j] < 2.5 and pair not in self.pressure_pairs:
                self.pressure_pairs.add(pair)
                self._emit("pressure", "D", 11 + j, self.owner, self.pos[11 + j], None, None,
                           {"distance_m": round(float(d[j]), 1)})
            elif d[j] > 4.0:
                self.pressure_pairs.discard(pair)
        self.pressure_pairs = {pr for pr in self.pressure_pairs if pr[1] == self.owner}

    # ------------------------------------------------------------------ run
    def run(self) -> SimResult:
        while not self.done:
            self._update_script()
            if self.done:
                break
            tgt, cap = self._targets()
            self._integrate(tgt, cap)
            self.k += 1
            self._update_ball()
            self._detect_pressure()
            self._record()
        if self.flight is not None:
            # A pass still travelling at the cut: keep the event but mark it unresolved.
            self.events[self.flight.event_index].context["unresolved_at_clip_end"] = True
        self.markers["end_t"] = self.t
        return SimResult(self.frames, self.events, self.markers)


def _role_of(i: int) -> str:
    from .roster import ROLES

    return ROLES[i % 11]


def simulate(p: ClipParams, motion_rng: np.random.Generator) -> SimResult:
    return FlankSimulation(p, motion_rng).run()
