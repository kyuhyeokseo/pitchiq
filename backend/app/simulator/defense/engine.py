"""Behaviour-driven 11v11 scenario engine for the defensive families (generator-private).

Canonical frame: the attacking team "A" attacks toward +u (u=105 is the
defending team's goal) and v is the lateral axis. Players 0..10 are A and
11..21 are D, both ordered by ``roster.ROLES``. A private ``Plan`` gives each
player a time-ordered list of behaviour segments (hold, mark, press, run, ...)
and the ball a list of steps (passes, shot). Markers react to lagged, slightly
extrapolated observations, so relations emerge from movement instead of
being painted from a label. Whether a pass is received or intercepted is
decided from geometry at the landing point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..roster import ROLES
from .physics import DT, PITCH_L, PITCH_W, clip_to_pitch, ou_step, step

N = 22
EVENT_LATENCY_S = 0.4  # simulated feed delay between an event and its availability
# A long ball moves the whole game: players who were holding a slot when it was played (not the ones the plan
# sends after it) shift toward where it goes - the passing team to support, the other team to get back.
LONG_BALL_M = 30.0
TRANSITION_SHARE = 0.5  # share of the ball's forward travel each team shifts by
TRANSITION_SPEED = 6.0
SLOT_KINDS = frozenset({"hold", "check", "screen", "press", "carry", "run"})
# The game also stays compact when the ball moves on through short passes: a player of the team in possession left
# far behind the ball moves up, and a defender the ball has gone past gets back goal-side of it.
TRAIL_MAX_M = 35.0
BYPASSED_M = 15.0
RECOVER_SPEED = 6.5
TAIL_S = 5.0  # a clip ends this long after the last planned ball action has been resolved
# Off-ball players follow the ball and never stand still. Slopes measured on real 11-v-11 play (IDSSE, 7 matches,
# 4 s windows, least squares of a player's displacement on the ball's): along u they depend on where the player is
# relative to the ball (metres nearer the goal the team in possession attacks) and on whether the ball went forward
# or back - players behind the ball push up with it but do not drop, forwards hold their height when it goes forward
# and come back when it goes back, a back line drops a little and steps up more. Players also move about 8 m (rms)
# in 4 s beyond that slope, so slots wander smoothly around their anchor instead of orbiting it on a fixed period.
FOLLOW_AHEAD_M = (-20.0, -10.0, 0.0, 10.0, 20.0)
FOLLOW_U = {  # (his team has the ball, ball went forward) -> slope at each FOLLOW_AHEAD_M
    (True, True): (0.47, 0.42, 0.40, 0.30, 0.13), (True, False): (0.0, 0.13, 0.21, 0.19, 0.28),
    (False, True): (0.50, 0.51, 0.45, 0.34, 0.17), (False, False): (0.0, 0.03, 0.27, 0.30, 0.33),
}
FOLLOW_V = {True: (0.12, 0.16, 0.19, 0.16, 0.16), False: (0.10, 0.15, 0.24, 0.27, 0.25)}
HOLD_WANDER_M = 1.0  # default wander of a held slot (a check wanders by its own amplitude)
PINNED_WANDER_M = 0.4  # a hold whose shift the plan sets is a designed spot: it only fidgets around it (and is
# not pushed off team-mates, since the plan set those distances)
CHECK_WANDER = 0.6  # share of a check's amplitude used as its wander
ARRIVED_M = 1.5  # a run, recovery or carry within this of its end becomes a slot that follows the ball
ANCHOR_KINDS = frozenset({"run", "recover", "carry"})
# A loose marker (mark with "loose") follows his man tightly near the ball and drops off him away from it. Real play
# (IDSSE): an attacker's nearest defender is a median 4.1 m away within 10 m of the ball and 8.6 m beyond 45 m,
# and away from the ball he stands more central (0.8 m -> 5.4 m) - he covers instead of following. Only 16% of
# outfield attackers have a defender within 2.5 m (30% at the 90th percentile).
LOOSE_FROM_M, LOOSE_SPAN_M = 15.0, 30.0  # his man's distance from the ball where he starts to drop / is fully off
LOOSE_GS_M, LOOSE_INSIDE_M = 2.5, 5.0  # extra goal-side and inside cushion when fully off
# Team-mates give each other room: away from the ball an outfield player's nearest team-mate is under 5.5 m only
# 10% of the time in real play (median 10.5 m, IDSSE), so a slot moves off a team-mate closer than this.
SPACING_M = 6.0
# A pass is received when the ball is at the receiver's feet. A ball played into space lands ahead of its runner and
# rolls on, slowing, until he (or an opponent) reaches it, instead of stopping where it landed.
RECEIVE_M = 1.0
ROLL_SHARE, ROLL_MAX_MPS = 0.3, 5.0  # rolling speed after it lands: a share of its flight speed, capped
ROLL_DECEL = 3.0  # m/s^2
ROLL_MAX_S = 2.0
ONSIDE_M = 0.3  # a pass to a runner is never released with him less than this onside


def key_index(key: str) -> int:
    side, role = key.split(":")
    return (0 if side == "A" else 11) + ROLES.index(role)


@dataclass
class Seg:
    """A behaviour from time ``t`` (seconds) or from a marker ``(name, offset_s)`` once it has happened."""

    t: float | tuple[str, float]
    kind: str
    p: dict = field(default_factory=dict)


@dataclass
class Plan:
    duration_s: float
    init: dict[str, tuple[float, float]]
    segs: dict[str, list[Seg]]
    ball_steps: list[dict]
    first_owner: str
    sigma: dict[str, float] = field(default_factory=dict)
    lag_s: float = 0.4
    steady: tuple[str, ...] = ()  # the story's players: their slots follow the ball but do not wander, unless a
    # segment sets its own "wander" (a waiting player who must not drift out of his spot still shifts his feet)


@dataclass
class SimEvent:
    t: float
    kind: str
    team: int | None  # 0 = A, 1 = D
    actor: int | None
    target: int | None
    related: int | None  # index into the event list
    pos: tuple[float, float] | None


@dataclass
class SimFrame:
    pos: np.ndarray
    vel: np.ndarray
    ball: np.ndarray  # (u, v)
    z: float
    owner: int
    possession: int | None  # 0 / 1 / None
    ball_state: str


@dataclass
class _Flight:
    start: np.ndarray
    end: np.ndarray
    k0: int
    k1: int
    passer: int
    receiver: int | None
    peak_z: float
    event: int
    kind: str  # pass | shot
    through: bool = False  # played into space ahead of a runner
    roll: float | None = None  # rolling speed once it has landed with nobody on it
    roll_dir: np.ndarray | None = None


class DefenseSimulation:
    def __init__(self, plan: Plan, rng: np.random.Generator):
        self.plan = plan
        self.rng = rng
        self.k = 0
        pos = np.zeros((N, 2))
        for key, (u, v) in plan.init.items():
            pos[key_index(key)] = (u, v)
        self.pos = clip_to_pitch(pos)
        self.vel = np.zeros((N, 2))
        self.vel[:11, 0] = 0.3
        self.sigma = np.full(N, 0.35)
        for key, s in plan.sigma.items():
            self.sigma[key_index(key)] = s
        self.sigma[[0, 11]] = np.minimum(self.sigma[[0, 11]], 0.2)
        self.noise = rng.normal(0.0, 1.0, (N, 2)) * self.sigma[:, None]
        # slot wander has its own stream (spawned, so the target noise sequence is unchanged)
        self.wander_rng = np.random.default_rng(rng.bit_generator.seed_seq.spawn(1)[0])
        self.wander = self.wander_rng.normal(0.0, 1.0, (N, 2))  # unit-variance OU, scaled per behaviour
        self.steady = [key_index(k) for k in plan.steady]
        self.wander[self.steady] = 0.0
        self.fidget_rng = np.random.default_rng(rng.bit_generator.seed_seq.spawn(1)[0])
        self.fidget = self.fidget_rng.normal(0.0, 1.0, (N, 2))  # the steady players' own wander (opt-in)
        self.arrived: dict[int, tuple[tuple[int, float], np.ndarray, np.ndarray]] = {}  # seg key, end point, ball
        self.hist: list[np.ndarray] = [self.pos.copy()]
        self.owner = key_index(plan.first_owner)
        self.possession: int | None = 0 if self.owner < 11 else 1
        self.ball = self.pos[self.owner] + (0.6, 0.0)
        self.z = 0.0
        self.flight: _Flight | None = None
        self.step_i = 0
        self.last_receive_t = 0.0
        self.owner_since_pos = self.ball.copy()
        self.seg_start: dict[int, tuple[float, np.ndarray, np.ndarray]] = {}
        self.transition: tuple[float, float] | None = None  # (release time, shift along u) after a long ball
        self.done_t: float | None = None  # when the last planned ball action was resolved
        self.transition_anchor = np.zeros((N, 2))
        self.transition_ball = np.zeros(2)  # where the long ball lands
        self.events: list[SimEvent] = []
        self.frames: list[SimFrame] = []
        self.markers: dict[str, float | int | None] = {}
        self.cur_seg: list[Seg | None] = [None] * N  # each player's active behaviour on the current step
        self._record()

    # ------------------------------------------------------------- helpers
    @property
    def t(self) -> float:
        return self.k * DT

    def _lagged(self, i: int, lag_s: float) -> np.ndarray:
        j = max(0, len(self.hist) - 1 - int(round(lag_s / DT)))
        return self.hist[j][i]

    def _predicted(self, i: int, lag_s: float, horizon_s: float) -> np.ndarray:
        a = self._lagged(i, lag_s)
        b = self._lagged(i, lag_s + 0.4)
        return a + (a - b) / 0.4 * horizon_s

    def _carrier(self) -> int | None:
        return self.owner if self.owner >= 0 else None

    def _record(self) -> None:
        state = "controlled" if self.owner >= 0 else ("in_flight" if self.flight else "loose")
        self.frames.append(SimFrame(self.pos.copy(), self.vel.copy(), self.ball.copy(), float(self.z), self.owner,
                                    self.possession, state))

    def _emit(self, kind: str, team: int | None, actor: int | None, target: int | None, related: int | None,
              pos) -> int:
        self.events.append(SimEvent(round(self.t, 3), kind, team, actor, target, related,
                                    None if pos is None else (float(pos[0]), float(pos[1]))))
        return len(self.events) - 1

    def _seg_time(self, s: Seg) -> float | None:
        if isinstance(s.t, tuple):
            name, offset = s.t
            m = self.markers.get(f"{name}_t")
            return None if m is None else float(m) + offset
        return float(s.t)

    def _active_seg(self, i: int, key: str) -> Seg | None:
        cur, cur_t = None, -1.0
        for s in self.plan.segs.get(key, []):
            ts = self._seg_time(s)
            if ts is not None and ts <= self.t + 1e-9 and ts >= cur_t:
                cur, cur_t = s, ts
        if cur is not None:
            st = self.seg_start.get(i)
            if st is None or st[0] != (id(cur), cur_t):
                self.seg_start[i] = ((id(cur), cur_t), self.pos[i].copy(), self.ball.copy())
        return cur

    # ------------------------------------------------------------- behaviours
    def _target(self, i: int, seg: Seg | None) -> tuple[np.ndarray, float]:
        pos = self.pos[i]
        if seg is None:
            return pos.copy(), 2.0
        p, kind = seg.p, seg.kind
        lag = p.get("lag", self.plan.lag_s)
        if kind in ("hold", "check"):  # a slot that follows the play; a check also shows for the ball around it
            _, _, ball0 = self.seg_start[i]
            at = np.array(p["at"], dtype=float)
            follow = np.array(p["shift"]) * (self.ball - ball0) if "shift" in p else self._follow(i, ball0)
            if kind == "check":
                wander = CHECK_WANDER * p.get("amp", 2.5)
            else:
                wander = p.get("wander", PINNED_WANDER_M if "shift" in p else HOLD_WANDER_M)
            speed = p.get("speed", 4.0 if kind == "hold" else 5.0)
            own = self.fidget[i] if i in self.steady and "wander" in p else self.wander[i]
            tg = at + follow + p.get("wander", wander) * own
            # a spot the plan pins (an explicit shift) keeps its designed distances to team-mates
            return (clip_to_pitch(tg, 2.0) if "shift" in p else self._spread(i, tg)), speed
        if kind == "mark":
            j = key_index(p["who"])
            pred = self._predicted(j, lag, p.get("antic", 0.5))
            side = 1.0 if i >= 11 else -1.0  # goal-side: toward own goal
            gs, inside = p.get("gs", 1.8), p.get("inside", 0.6)
            if p.get("loose"):  # man-oriented near the ball, covering away from it (LOOSE_FROM_M)
                far = float(np.clip((np.linalg.norm(pred - self.ball) - LOOSE_FROM_M) / LOOSE_SPAN_M, 0.0, 1.0))
                # a defender in the back line can drop off inside only, holding the line's height (loose_gs 0)
                gs, inside = gs + p.get("loose_gs", LOOSE_GS_M) * far, inside + LOOSE_INSIDE_M * far
            return pred + np.array([side * gs, inside * np.sign(34.0 - pred[1])]), p.get("speed", 7.0)
        if kind == "press_mark":  # press the assigned man while he has the ball, otherwise mark him
            if self._carrier() == key_index(p["who"]):
                return self._target(i, Seg(seg.t, "press", p))
            return self._target(i, Seg(seg.t, "mark", p))
        if kind == "press":
            c = self._carrier()
            if c is None or (c < 11) == (i < 11):
                return self.pos[i].copy(), 3.0
            cut = self.pos[key_index(p["cut"])] if isinstance(p.get("cut"), str) else np.array(p["cut"], float)
            d = cut - self.pos[c]
            n = np.linalg.norm(d)
            off = d / n * p.get("stop", 1.4) if n > 1e-6 else np.zeros(2)
            return self.pos[c] + off, p.get("speed", 7.0)
        if kind == "run":
            return np.array(p["to"], dtype=float), p.get("speed", 7.5)
        if kind == "carry":
            return np.array(p["to"], dtype=float), p.get("speed", 3.0)
        if kind == "recover":  # retreat toward own goal while staying near an attacker's lane
            _, start, _ = self.seg_start[i]
            side = 1.0 if i >= 11 else -1.0
            tgt = np.array([start[0] + side * p.get("du", 15.0), start[1]])
            if "who" in p:
                j = key_index(p["who"])
                pred = self._predicted(j, p.get("lag", 0.2), 0.6)
                tgt = np.array([max(tgt[0], pred[0] + 1.0) if side > 0 else min(tgt[0], pred[0] - 1.0),
                                0.5 * (tgt[1] + pred[1])])
            return tgt, p.get("speed", 7.0)
        if kind == "chase":
            j = key_index(p["who"])
            pred = self._predicted(j, p.get("lag", 0.2), p.get("antic", 0.6))
            side = 1.0 if i >= 11 else -1.0
            return pred + np.array([side * p.get("gs", 0.8), 0.0]), p.get("speed", 8.2)
        if kind == "cover":  # spare defender: stay goal-side of the most advanced threat in his zone
            at = np.array(p["at"], dtype=float)
            j = key_index(p["track"])
            pred = self._predicted(j, lag, 0.6)
            k = p.get("k", 0.6)
            u = max(at[0], pred[0] + p.get("depth", 5.0))
            return np.array([u, (1 - k) * at[1] + k * pred[1]]), p.get("speed", 6.5)
        if kind == "screen":  # stand on the passing line between the ball and an opponent (a cover shadow)
            j = key_index(p["who"])
            src = self.ball if p.get("from", "ball") == "ball" else self.pos[key_index(p["from"])]
            tgt_pos = self._predicted(j, lag, 0.3)
            return src + p.get("frac", 0.5) * (tgt_pos - src), p.get("speed", 6.5)
        if kind == "attack_ball":  # go for the ball: the landing point while it travels, else the ball
            side = 1.0 if i >= 11 else -1.0  # meet it from the goal side
            if self.flight is None and self.owner >= 0 and (self.owner < 11) == (i < 11):
                # his team has won it: the winner turns away from his own goal with it for a few metres, the others
                # stop chasing
                if self.owner == i:
                    return self.owner_since_pos + np.array([-side * 6.0, 0.0]), 2.5
                return pos.copy(), 2.0
            aim = self.flight.end if self.flight is not None else self.ball
            return aim + np.array([side * p.get("goal_side_m", 1.0), 0.0]), p.get("speed", 8.0)
        if kind == "gk":
            at = np.array(p["at"], dtype=float)
            return np.array([at[0], 34.0 + p.get("track", 0.15) * (self.ball[1] - 34.0)]), p.get("speed", 4.0)
        if kind == "sweep":  # goalkeeper advances toward the ball's landing point, staying in his area
            aim = self.flight.end if self.flight is not None else self.ball
            lo, hi = p.get("u_range", (88.5, 103.0))
            return np.array([np.clip(aim[0], lo, hi), np.clip(aim[1], 20.0, 48.0)]), p.get("speed", 6.0)
        raise ValueError(f"unknown behaviour {kind}")

    def _follow(self, i: int, ball0: np.ndarray) -> np.ndarray:
        """How far player ``i``'s slot has moved with the ball since it was at ``ball0`` (slopes: FOLLOW_U)."""
        s = 1.0 if self.possession != 1 else -1.0  # direction the team in possession attacks along u
        ahead = (float(self.pos[i, 0]) - float(self.ball[0])) * s
        own = (i < 11) == (self.possession != 1)
        d = self.ball - ball0
        ku = np.interp(ahead, FOLLOW_AHEAD_M, FOLLOW_U[(own, float(d[0]) * s >= 0.0)])
        kv = np.interp(ahead, FOLLOW_AHEAD_M, FOLLOW_V[own])
        return np.array([ku, kv]) * d

    def _settle(self, i: int, seg: Seg | None, tg: np.ndarray, cap: float) -> tuple[np.ndarray, float]:
        """At the end of a run, recovery or carry a player does not stop dead: he holds a slot there that follows
        the ball (a recovery that tracks an opponent keeps tracking him, and the man on the ball is left alone)."""
        if seg is None or seg.kind not in ANCHOR_KINDS or "who" in seg.p or i == self.owner or i in self.steady:
            return tg, cap  # the story's players end their runs exactly where the plan put them
        key = self.seg_start[i][0]
        arr = self.arrived.get(i)
        if arr is None or arr[0] != key:
            if float(np.linalg.norm(self.pos[i] - tg)) > ARRIVED_M:
                return tg, cap
            arr = self.arrived[i] = (key, tg.copy(), self.ball.copy())
        _, end, ball0 = arr
        if "shift" in seg.p:  # the plan pins where this run ends, like a hold with an explicit shift
            tg = end + np.array(seg.p["shift"]) * (self.ball - ball0) + PINNED_WANDER_M * self.wander[i]
            return clip_to_pitch(tg, 2.0), min(cap, 5.0)
        return self._spread(i, end + self._follow(i, ball0) + HOLD_WANDER_M * self.wander[i]), min(cap, 5.0)

    def _spread(self, i: int, tg: np.ndarray) -> np.ndarray:
        """Move a slot off outfield team-mates closer than SPACING_M, so players offer angles instead of bunching,
        and keep it a couple of metres inside the lines (a full back does not run into the touchline)."""
        if i in self.steady:
            return tg
        mates = self.pos[1:11] if i < 11 else self.pos[12:22]
        d = tg - mates
        n = np.linalg.norm(d, axis=1)
        close = (n < SPACING_M) & (np.linalg.norm(mates - self.pos[i], axis=1) > 1e-9)
        if close.any():
            unit = d[close] / np.maximum(n[close], 1e-6)[:, None]
            tg = tg + (unit * (SPACING_M - n[close])[:, None]).sum(axis=0)
        return clip_to_pitch(tg, 2.0)

    def _after_long_ball(self, i: int, seg: Seg | None, tg: np.ndarray, cap: float) -> tuple[np.ndarray, float]:
        """Players still holding a slot from before a long ball shift with the play instead of standing still."""
        if self.transition is None or seg is None or seg.kind not in SLOT_KINDS or i == self.owner:
            return tg, cap
        t0, du = self.transition
        st = self._seg_time(seg)
        if st is None or st >= t0 - 1e-9:
            return tg, cap  # the plan gave this player something to do after the ball was played
        a = self.transition_anchor[i]
        # from where the long ball lands the slot follows the play on, like any other
        tg = np.array([a[0] + du, a[1]]) + self._follow(i, self.transition_ball) + HOLD_WANDER_M * self.wander[i]
        return clip_to_pitch(tg, 2.0), TRANSITION_SPEED

    def _keep_compact(self, i: int, seg: Seg | None, tg: np.ndarray, cap: float) -> tuple[np.ndarray, float]:
        """No one stays out of the game: trailing team-mates of the ball's team move up, bypassed defenders recover."""
        if seg is None or i in (0, 11) or i == self.owner or self.possession is None:
            return tg, cap
        if self.flight is not None and i == self.flight.receiver:
            return tg, cap
        s = 1.0 if self.possession == 0 else -1.0  # direction the team in possession attacks along u
        behind = (float(self.ball[0]) - float(self.pos[i, 0])) * s  # > 0: the player is on his own-goal side of it
        on_ball_team = (i < 11) == (self.possession == 0)
        if on_ball_team and seg.kind in SLOT_KINDS and behind > TRAIL_MAX_M:
            return np.array([float(self.ball[0]) - s * (TRAIL_MAX_M - 5.0), float(tg[1])]), max(cap, 5.5)
        if not on_ball_team and seg.kind in SLOT_KINDS | {"mark", "press_mark"} and behind > BYPASSED_M:
            v = 0.7 * float(self.pos[i, 1]) + 0.3 * 34.0  # get back goal-side of the ball, tucking in a little
            return clip_to_pitch(np.array([float(self.ball[0]) + s * 3.0, v]), 2.0), RECOVER_SPEED
        return tg, cap

    # ------------------------------------------------------------- ball
    def _release(self, step_cfg: dict) -> None:
        passer = self.owner
        kind = step_cfg["kind"]
        start = self.ball.copy()
        moved = float(np.linalg.norm(start - self.owner_since_pos))
        team = 0 if passer < 11 else 1
        if moved >= 3.0:
            self._emit("carry", team, passer, None, None, start)
        if kind == "shot":
            gk = 11 if passer < 11 else 0
            end = self.pos[gk] + np.array([-0.4, 0.3])
            receiver = None
            speed = step_cfg.get("speed", 24.0)
            peak = 0.6
            ev = self._emit("shot", team, passer, None, None, start)
        else:
            receiver = key_index(step_cfg["to"])
            speed = step_cfg.get("speed", 15.0)
            lead = step_cfg.get("lead", 0.6)
            rv = self.vel[receiver]
            if "run_speed" in step_cfg:  # pass into space: lead the runner at his running speed
                n = float(np.linalg.norm(rv))
                rv = rv / n * step_cfg["run_speed"] if n > 0.5 else np.array([step_cfg["run_speed"], 0.0])
            end = self.pos[receiver].copy()
            for _ in range(4):  # landing point where the receiver will be when the ball arrives
                t_est = float(np.linalg.norm(end - start)) / speed
                end = self.pos[receiver] + rv * t_est * lead
            end = end + np.array(step_cfg.get("aim", (0.0, 0.0)))
            peak = 0.0
            if step_cfg.get("style") == "lofted":
                peak = min(12.0, 0.12 * float(np.linalg.norm(end - start)))
            ev = self._emit("pass_release", team, passer, None, None, start)
        end = clip_to_pitch(end, 1.0)
        dist = float(np.linalg.norm(end - start))
        n = max(2, math.ceil(dist / speed / DT))
        self.flight = _Flight(start, end, self.k, self.k + n, passer, receiver, peak, ev, kind,
                              through="run_speed" in step_cfg)
        if kind != "shot" and dist >= LONG_BALL_M:
            self.transition = (self.t, TRANSITION_SHARE * float(end[0] - start[0]))
            self.transition_ball = end.copy()
            self.transition_anchor = self.pos.copy()
        self.owner = -1
        self.markers[f"release_{self.step_i}_t"] = self.t
        if step_cfg.get("name"):
            self.markers[f"{step_cfg['name']}_release_t"] = self.t

    def _arrive(self) -> None:
        f = self.flight
        assert f is not None
        self.ball = f.end.copy()
        self.z = 0.0
        self.flight = None
        passer_team = 0 if f.passer < 11 else 1
        if f.kind == "shot":
            gk = 11 if f.passer < 11 else 0
            self.owner = gk
            self.possession = 1 - passer_team
            self._emit("interception", self.possession, gk, None, f.event, self.ball)
        else:
            opp = range(11, N) if passer_team == 0 else range(0, 11)
            d_recv = float(np.linalg.norm(self.pos[f.receiver] - f.end))
            d_opp = [(float(np.linalg.norm(self.pos[j] - f.end)), j) for j in opp]
            best_d, best_j = min(d_opp)
            # A defender wins the ball if he is clearly closer, or if he contests it from the goal side.
            toward_own_goal = 1.0 if passer_team == 0 else -1.0
            goal_side = (self.pos[best_j, 0] - self.pos[f.receiver, 0]) * toward_own_goal > 0.0
            aerial = f.peak_z > 0.0  # a lofted ball: the goal-side defender facing it wins a close duel
            if ((best_d < 2.5 and best_d < d_recv - 0.3) or (best_d < 1.6 and goal_side and best_d < d_recv + 0.5)
                    or (aerial and goal_side and best_d < 1.6)):
                self.owner = best_j
                self.possession = 1 - passer_team
                self._emit("interception", self.possession, best_j, None, f.event, self.ball)
                self.markers["interception_t"] = self.t
            else:
                self.owner = f.receiver
                self.possession = passer_team
                self._emit("pass_receive", passer_team, f.passer, f.receiver, f.event, self.ball)
        self.owner_since_pos = self.ball.copy()
        self.last_receive_t = self.t
        self.markers[f"arrive_{self.step_i}_t"] = self.t
        name = self.plan.ball_steps[self.step_i].get("name")
        if name:
            self.markers[f"{name}_arrive_t"] = self.t
        self.step_i += 1

    def _roll(self, f: _Flight) -> bool:
        """A pass that lands with nobody on it rolls on along its line, slowing. True while it is still rolling."""
        if f.kind == "shot" or f.receiver is None:
            return False
        if f.roll is None:
            self.ball, self.z = f.end.copy(), 0.0
            d = f.end - f.start
            n = float(np.linalg.norm(d))
            if n < 1e-6:
                return False
            f.roll = min(ROLL_SHARE * n / ((f.k1 - f.k0) * DT), ROLL_MAX_MPS)
            f.roll_dir = d / n
        else:
            f.roll = max(f.roll - ROLL_DECEL * DT, 0.0)
            self.ball = clip_to_pitch(self.ball + f.roll_dir * f.roll * DT, 0.5)
            f.end = self.ball.copy()
        opp = slice(11, N) if f.passer < 11 else slice(0, 11)
        near = min(float(np.linalg.norm(self.pos[f.receiver] - self.ball)),
                   float(np.min(np.linalg.norm(self.pos[opp] - self.ball, axis=1))))
        return near > RECEIVE_M and (self.k - f.k1) * DT < ROLL_MAX_S - 1e-9

    def _trigger_ok(self, cfg: dict) -> bool:
        t = self.t
        if self.owner < 0 or self.owner != key_index(cfg["from"]):
            return False
        if t < cfg.get("at", 0.0) - 1e-9:
            return False
        if t < self.last_receive_t + cfg.get("after_receive_s", 0.0) - 1e-9:
            return False
        trig = cfg.get("trigger")
        if trig is None:
            return True
        late = t >= trig.get("latest", 1e9) - 1e-9
        if late and trig["kind"] != "onside_line":
            return True
        if trig["kind"] == "onside_window":
            runner, marker = key_index(trig["runner"]), key_index(trig["marker"])
            fwd = float(self.vel[runner, 0])
            return fwd >= trig.get("min_speed", 5.5) and self.pos[runner, 0] >= self.pos[marker, 0] - trig.get(
                "margin_m", 1.5)
        if trig["kind"] == "onside_line":  # runner at speed, approaching but not past the last-but-one defender
            runner = key_index(trig["runner"])
            side = slice(11, N) if runner < 11 else slice(0, 11)
            sgn = 1.0 if runner < 11 else -1.0
            u = self.pos[side, 0] if runner < 11 else 105.0 - self.pos[side, 0]
            j = int(np.argsort(u)[-2])  # the last-but-one defender
            ball_u = float(self.ball[0] if runner < 11 else 105.0 - self.ball[0])
            line = max(float(u[j]), ball_u)
            line_v = sgn * float(self.vel[side][j, 0]) if u[j] >= ball_u else 0.0
            ru = self.pos[runner, 0] if runner < 11 else 105.0 - self.pos[runner, 0]
            fwd = sgn * float(self.vel[runner, 0])
            if late:  # the passer has waited long enough, but he never plays a man who is already offside
                return ru <= line - ONSIDE_M
            # in the window (widened by half a frame of closing), or about to run through it before the next frame:
            # a runner at 8 m/s meeting a line that steps up closes 2 m per frame, more than the window is wide
            closing = max(fwd - line_v, 0.0)
            near = ru >= line - trig.get("margin_m", 2.0) - 0.5 * closing * DT or ru + closing * DT > line - 0.6
            return fwd >= trig.get("min_speed", 5.0) and near and ru <= line - 0.6
        raise ValueError(trig["kind"])

    def _update_ball(self) -> None:
        if self.flight is not None:
            f = self.flight
            if self.k >= f.k1:
                if not self._roll(f):
                    self._arrive()
            else:
                s = (self.k - f.k0) / (f.k1 - f.k0)
                self.ball = f.start + s * (f.end - f.start)
                self.z = 4.0 * f.peak_z * s * (1.0 - s)
        elif self.owner >= 0:
            v = self.vel[self.owner]
            sp = float(np.linalg.norm(v))
            fwd = np.array([1.0, 0.0]) if self.owner < 11 else np.array([-1.0, 0.0])
            dirn = v / sp if sp > 0.6 else fwd
            self.ball = clip_to_pitch(self.pos[self.owner] + 0.6 * dirn)

    # ------------------------------------------------------------- loop
    def _move(self, tgt: np.ndarray, cap: np.ndarray) -> None:
        """Advance every player one step toward the behaviour targets (the hybrid engine overrides this)."""
        self.pos, self.vel = step(self.pos, self.vel, tgt, cap)

    def run(self) -> tuple[list[SimFrame], list[SimEvent], dict]:
        keys = [("A:" if i < 11 else "D:") + ROLES[i % 11] for i in range(N)]
        n_frames = int(round(self.plan.duration_s / DT)) + 1
        while len(self.frames) < n_frames:
            if self.flight is None and self.step_i < len(self.plan.ball_steps):
                cfg = self.plan.ball_steps[self.step_i]
                if self._trigger_ok(cfg):
                    self._release(cfg)
            tgt = np.empty((N, 2))
            cap = np.empty(N)
            for i, key in enumerate(keys):
                seg = self._active_seg(i, key)
                self.cur_seg[i] = seg
                tg, cp = self._target(i, seg)
                tg, cp = self._settle(i, seg, tg, cp)
                tg, cp = self._after_long_ball(i, seg, tg, cp)
                tg, cp = self._keep_compact(i, seg, tg, cp)
                tgt[i], cap[i] = tg + self.noise[i], cp
            if self.flight is not None and self.flight.receiver is not None:
                r, f = self.flight.receiver, self.flight
                # run onto the ball: aim a little past the landing point so the receiver arrives at speed
                d = f.end - self.pos[r]
                n = float(np.linalg.norm(d))
                if f.roll is not None:  # chase the rolling ball through it
                    tgt[r] = self.ball + f.roll_dir * 2.5
                elif f.through:  # a runner does not brake for a ball played ahead of him
                    fd = (f.end - f.start) / max(float(np.linalg.norm(f.end - f.start)), 1e-6)
                    tgt[r] = f.end + (d / n * 3.0 if n > 1.0 else fd * 3.0)
                else:
                    tgt[r] = f.end + (d / n * 3.0 if n > 4.0 else 0.0)
                cap[r] = max(cap[r], 6.5)
            self._move(tgt, cap)
            self.hist.append(self.pos.copy())
            self.noise = ou_step(self.noise, self.sigma, self.rng)
            self.wander = ou_step(self.wander, np.ones(N), self.wander_rng)
            self.wander[self.steady] = 0.0
            self.fidget = ou_step(self.fidget, np.ones(N), self.fidget_rng)
            self.k += 1
            self._update_ball()
            self._record()
            if self.done_t is None and self.step_i >= len(self.plan.ball_steps) and self.flight is None:
                self.done_t = self.t
            if self.done_t is not None and self.t >= self.done_t + TAIL_S - 1e-9:
                break
        if self.flight is not None:
            self.markers["unresolved_flight"] = True
        self.markers["end_t"] = self.t
        return self.frames, self.events, self.markers


def in_pitch(p: np.ndarray) -> bool:
    return bool(0.0 <= p[0] <= PITCH_L and 0.0 <= p[1] <= PITCH_W)
