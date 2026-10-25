"""Hybrid generator: the scenario controller keeps the macro set-up and every ball action, the learned movement
model (shared GRU + GNN, trained on IDSSE open tracking data) moves the players around it (generator-private).

Controller-driven: the plan's ball steps (who passes to whom and when, flight, reception, interception, the shot),
the ball carrier (holds, or carries forward when nobody is near him), the receiver running onto a pass, and the
scenario-defining behaviours in proportion to their authority below (a pressing run, a planned run in behind, a
goalkeeper sweeping). Learned: every other movement, i.e. how team-mates and opponents position and react around
the ball and the controlled players. Each step blends the model's next position with the controller's by the
player's authority, which changes smoothly (no jumps when a behaviour starts or ends).

A goal-conditioned model is also told, for every player, where the controller wants him and in how many seconds (the
distance at GOAL_PACE x his behaviour's speed cap, GOAL_S_MIN to GOAL_S_MAX s; a farther target is cut to what that
pace reaches in GOAL_S_MAX s). The model then moves him there itself, so the scenario keeps its shape while the
movement is learned.

Offside is evaluated at pass release: a planned pass waits (up to OFFSIDE_WAIT_S) for its receiver to be onside, and
a release that is still offside is recorded in the private markers. Runs beyond the line after release are legal.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .engine import N, DefenseSimulation, Plan, Seg, key_index
from .physics import DT, PITCH_L, PITCH_W, clip_to_pitch, step

# share of a player's movement the controller decides while a behaviour is active (the model decides the rest)
AUTHORITY = {"run": 1.0, "carry": 1.0, "attack_ball": 1.0, "sweep": 1.0, "press": 0.85, "chase": 0.85,
             "press_mark": 0.6, "recover": 0.5, "screen": 0.5, "mark": 0.4, "cover": 0.35, "gk": 0.3,
             "hold": 0.0, "check": 0.0}
AUTHORITY_RATE = 2.5  # per second: a behaviour takes over (or lets go) within about 0.4 s
FREE_M = 8.0  # a carrier with no opponent this close is unpressured and carries the ball forward
CARRY_SPEED = 4.0
OFFSIDE_WAIT_S = 2.0
RECEIVE_M = 1.0  # the ball is controlled only once the receiver is this close to it
ROLL_ON_STEPS = 10  # a ball that lands short of the player taking it runs on toward him for up to 2 s
ROLL_ON_M = 2.5  # per step (12.5 m/s): faster than any player (10 m/s), so it always closes in
GOAL_PACE = 0.75  # a goal-conditioned model's player is asked to reach his target at this share of his speed cap
GOAL_S_MIN, GOAL_S_MAX = 1.0, 4.0  # the goal horizons the model was trained on
# PITCHIQ_MOTION_CHECKPOINT points the hybrid engine at another release (e.g. models/motion_gnn_v1) for review runs
DEFAULT_CHECKPOINT = Path(os.environ.get("PITCHIQ_MOTION_CHECKPOINT")
                          or Path(__file__).resolve().parents[4] / "models" / "motion_gnn_v0" / "motion_gnn.pt")


def load_motion_model(path: Path = DEFAULT_CHECKPOINT):
    import torch

    from ..learned.model import MotionGNN

    torch.set_num_threads(1)  # one 23-node step at a time: more threads only add overhead
    ck = torch.load(path, map_location="cpu", weights_only=True)
    m = MotionGNN(d=ck["config"]["d"], mlp=ck["config"]["mlp"], stochastic=ck["config"].get("stochastic", False),
                  goal=ck["config"].get("goal", False))
    m.load_state_dict(ck["state_dict"])
    m.eval()
    return m


class HybridSimulation(DefenseSimulation):
    def __init__(self, plan: Plan, rng: np.random.Generator, model):
        super().__init__(plan, rng)
        import torch

        from ..learned.dataset import HIST
        from ..learned.model import step as model_step

        self._torch, self._hist_n, self._model_step = torch, HIST, model_step
        self.model = model
        self._gen = None
        if getattr(model, "stochastic", False):  # its own spawned stream: the controller's random streams are unchanged
            seed = int(rng.bit_generator.seed_seq.spawn(1)[0].generate_state(1)[0])
            self._gen = torch.Generator().manual_seed(seed)
        self.w = np.zeros(N)
        self.last_possession = self.possession if self.possession is not None else 0
        self._wait: dict[int, float] = {}
        self._roll_on = 0
        self.markers["offside_releases"] = 0

    # ------------------------------------------------------------- controller
    def _target(self, i: int, seg: Seg | None):
        tg, cap = super()._target(i, seg)
        if i == self.owner and i not in (0, 11) and (seg is None or seg.kind not in ("carry", "run")):
            opp = slice(11, N) if i < 11 else slice(0, 11)
            if float(np.linalg.norm(self.pos[opp] - self.pos[i], axis=1).min()) > FREE_M:
                fwd = 1.0 if i < 11 else -1.0  # unpressured: carry it forward and make someone come out
                return clip_to_pitch(self.pos[i] + np.array([fwd * 5.0, 0.0]), 3.0), CARRY_SPEED
        return tg, cap

    def _authority(self) -> np.ndarray:
        w = np.array([AUTHORITY.get(s.kind, 0.5) if s is not None else 0.0 for s in self.cur_seg])
        if self.owner >= 0:
            w[self.owner] = 1.0
        if self.flight is not None and self.flight.receiver is not None:
            w[self.flight.receiver] = 1.0
        return w

    def _offside(self, r: int) -> bool:
        u = self.pos[:, 0] if r < 11 else PITCH_L - self.pos[:, 0]
        ball_u = float(self.ball[0] if r < 11 else PITCH_L - self.ball[0])
        opp = slice(11, N) if r < 11 else slice(0, 11)
        line = max(float(np.sort(u[opp])[-2]), ball_u)
        return bool(u[r] > PITCH_L / 2 and u[r] > line + 0.1)

    def _trigger_ok(self, cfg: dict) -> bool:
        ok = super()._trigger_ok(cfg)
        if not ok or cfg["kind"] == "shot" or cfg.get("allow_offside"):
            return ok
        r = key_index(cfg["to"])
        if self._offside(r):
            since = self._wait.setdefault(self.step_i, self.t)
            if self.t - since < OFFSIDE_WAIT_S - 1e-9:
                return False  # hold the pass until the run is onside
            self.markers["offside_releases"] += 1  # released offside anyway: flagged for evaluation
        return True

    def _update_ball(self) -> None:
        f = self.flight
        if f is not None and self.k >= f.k1:
            to = f.receiver if f.receiver is not None else (11 if f.passer < 11 else 0)  # a shot: the goalkeeper
            d = self.pos[to] - f.end
            n = float(np.linalg.norm(d))
            if n > RECEIVE_M and self._roll_on < ROLL_ON_STEPS:
                # landed short of (or past) the player taking it: it keeps rolling to him instead of snapping
                self._roll_on += 1
                f.end = f.end + d / n * min(n, ROLL_ON_M)
                f.k1 = self.k + 1
                self.ball, self.z = f.end.copy(), 0.0
                return
            self._roll_on = 0
        super()._update_ball()

    # ------------------------------------------------------------- learned movement
    def _goals(self, tgt: np.ndarray, cap: np.ndarray) -> np.ndarray:
        """Per player [x, y, seconds, 1]: the controller's target and when to reach it at GOAL_PACE x his cap."""
        d = tgt - self.pos
        n = np.linalg.norm(d, axis=1)
        pace = np.maximum(GOAL_PACE * cap, 1.0)
        tau = np.clip(n / pace, GOAL_S_MIN, GOAL_S_MAX)
        reach = np.minimum(n, pace * GOAL_S_MAX)
        g = self.pos + d * (reach / np.maximum(n, 1e-6))[:, None]
        return np.column_stack([g, tau, np.ones(N)])

    def _model_next(self, goals: np.ndarray | None = None) -> np.ndarray:
        """Next positions of all 22 players from the model, in the engine's frame and order. ``goals`` (N, 4) are
        given to a goal-conditioned model."""
        torch = self._torch
        h = self._hist_n
        poss = self.possession if self.possession is not None else self.last_possession
        self.last_possession = poss
        players = np.stack(self.hist[-h:])
        balls = np.stack([f.ball for f in self.frames[-h:]])
        if len(players) < h:  # before 3 s of history exist: the first observed state, held
            players = np.concatenate([np.repeat(players[:1], h - len(players), 0), players])
            balls = np.concatenate([np.repeat(balls[:1], h - len(balls), 0), balls])
        order = list(range(N)) if poss == 0 else list(range(11, N)) + list(range(11))
        xy = np.concatenate([players[:, order], balls[:, None]], axis=1)  # (h, 23, 2)
        g = None
        if goals is not None and getattr(self.model, "goal", False):
            g = goals[order].copy()
        if poss == 1:  # the model sees the team in possession attacking +x
            xy = np.array([PITCH_L, PITCH_W]) - xy
            if g is not None:
                g[:, :2] = np.array([PITCH_L, PITCH_W]) - g[:, :2]
        x = torch.from_numpy(xy.transpose(1, 0, 2)[None].astype(np.float32))
        gt = None if g is None else torch.from_numpy(g[None].astype(np.float32))
        with torch.no_grad():
            dv = self.model.sample(x, self._gen, gt) if self._gen is not None else self.model(x, gt)
            nxt = self._model_step(x, dv)[0].numpy().astype(float)
        if poss == 1:
            nxt = np.array([PITCH_L, PITCH_W]) - nxt
        out = np.empty((N, 2))
        out[order] = nxt
        return out

    def _move(self, tgt: np.ndarray, cap: np.ndarray) -> None:
        rule_pos, _ = step(self.pos, self.vel, tgt, cap)
        model_pos = self._model_next(self._goals(tgt, cap))
        goal = self._authority()
        self.w += np.clip(goal - self.w, -AUTHORITY_RATE * DT, AUTHORITY_RATE * DT)
        new = clip_to_pitch(model_pos + self.w[:, None] * (rule_pos - model_pos))
        self.vel = (new - self.pos) / DT
        self.pos = new
