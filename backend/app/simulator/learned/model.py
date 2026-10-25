"""Compact learned movement component: shared GRU history encoder + 2-layer GNN + shared player decoder.

Nodes: the 22 players and the ball. The ball is a context node only: the scenario controller owns ball actions
(possession, carries, pass release, flight, reception), and the model predicts how the 22 players move around it.
Every player uses the same weights; no names, shirt numbers or match ids are inputs.

Per node and history step (3 s at 5 Hz): position, velocity, ball position relative to the node, and static flags
(team in possession, defending team, goalkeeper, ball). Edges (all ordered pairs, last history step): relative
position, relative velocity, distance, team-mate / opponent / ball flags. Output: each player's velocity change over
the next 0.2 s, turned into a bounded next position. Feature scales are fitted on the training matches only.

A stochastic model (``stochastic=True``) also predicts a per-player Laplace scale for that change; generation samples
from it instead of taking the location, so uncertain futures are not averaged into slow movement.

A goal-conditioned model (``goal=True``) also takes, per player, a goal: where he is to be and in how many seconds
(``goal`` rows ``[x, y, seconds, on]``; ``on`` = 0 means no goal). In training the goal is where the player really was
1-4 s later; in generation it is where the scenario controller wants him. The model decides how he gets there, so the
controller keeps the shape of the scenario without moving the player itself. Encoded per player after the history
encoder and before message passing, so team-mates and opponents see each other's goals.
"""

from __future__ import annotations

import torch
from torch import nn

from .dataset import HIST, L, W

DT = 0.2
MAX_SPEED = 10.0  # m/s
MAX_ACC = 8.0  # m/s^2, so |dv| <= 1.6 m/s per step
N_NODES = 23
# static node flags: team in possession, defending team, goalkeeper, ball
FLAGS = torch.zeros(N_NODES, 4)
FLAGS[:11, 0] = 1
FLAGS[11:22, 1] = 1
FLAGS[[0, 11], 2] = 1
FLAGS[22, 3] = 1
GOAL_FEATURES = 6  # offset to the goal, the velocity that reaches it on time, seconds left, on
_I, _J = torch.where(~torch.eye(N_NODES, dtype=torch.bool))  # all ordered pairs, no self-loops
SAME_TEAM = ((FLAGS[_I, 0] * FLAGS[_J, 0] + FLAGS[_I, 1] * FLAGS[_J, 1]) > 0).float()
OPPONENT = ((FLAGS[_I, 0] * FLAGS[_J, 1] + FLAGS[_I, 1] * FLAGS[_J, 0]) > 0).float()
WITH_BALL = ((FLAGS[_I, 3] + FLAGS[_J, 3]) > 0).float()


def kinematics(xy: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """xy (B, 23, T, 2) -> positions and velocities (m/s), the first velocity copied from the second."""
    v = torch.diff(xy, dim=2) / DT
    return xy, torch.cat([v[:, :, :1], v], dim=2)


def node_features(xy: torch.Tensor) -> torch.Tensor:
    """(B, 23, T, 2) -> (B, 23, T, 6): position, velocity, ball offset (raw units; scaled by the model)."""
    p, v = kinematics(xy)
    return torch.cat([p, v, xy[:, 22:23] - p], dim=-1)


def edge_features(xy: torch.Tensor) -> torch.Tensor:
    """Last-step edge features (B, E, 5 + 3): relative position, relative velocity, distance, pair flags."""
    p, v = kinematics(xy[:, :, -2:])
    p, v = p[:, :, -1], v[:, :, -1]
    rp = p[:, _J] - p[:, _I]
    rv = v[:, _J] - v[:, _I]
    d = torch.linalg.norm(rp, dim=-1, keepdim=True)
    B = xy.shape[0]
    flags = torch.stack([SAME_TEAM, OPPONENT, WITH_BALL], dim=-1).to(xy.device).expand(B, -1, -1)
    return torch.cat([rp, rv, d, flags], dim=-1)


class GNNLayer(nn.Module):
    def __init__(self, d: int, e: int, hidden: int):
        super().__init__()
        self.msg = nn.Sequential(nn.Linear(2 * d + e, hidden), nn.SiLU(), nn.Linear(hidden, d))
        self.upd = nn.Sequential(nn.Linear(2 * d, hidden), nn.SiLU(), nn.Linear(hidden, d))
        self.norm = nn.LayerNorm(d)

    def forward(self, h: torch.Tensor, e: torch.Tensor) -> torch.Tensor:
        m = self.msg(torch.cat([h[:, _I], h[:, _J], e], dim=-1))  # (B, E, d) message j -> i
        agg = torch.zeros_like(h).index_add_(1, _I.to(h.device), m) / (N_NODES - 1)
        return self.norm(h + self.upd(torch.cat([h, agg], dim=-1)))


def goal_features(hist: torch.Tensor, goal: torch.Tensor) -> torch.Tensor:
    """hist (B, 23, T, 2), goal (B, 22, 4) -> (B, 22, GOAL_FEATURES), all zero for a player without a goal."""
    on = goal[..., 3:4] > 0.5
    off = goal[..., :2] - hist[:, :22, -1]
    tau = goal[..., 2:3].clamp_min(DT)
    f = torch.cat([off / 10.0, off / tau / 5.0, tau / 4.0, torch.ones_like(tau)], dim=-1)
    return torch.where(on, f, torch.zeros_like(f))


class MotionGNN(nn.Module):
    def __init__(self, d: int = 64, mlp: int = 128, stochastic: bool = False, goal: bool = False):
        super().__init__()
        self.d = d
        self.stochastic = stochastic
        self.goal = goal
        self.gru = nn.GRU(6 + 4, d, num_layers=1, batch_first=True)
        if goal:
            self.goal_enc = nn.Sequential(nn.Linear(GOAL_FEATURES, mlp), nn.SiLU(), nn.Linear(mlp, d))
        self.layers = nn.ModuleList([GNNLayer(d, 8, mlp) for _ in range(2)])
        self.dec = nn.Sequential(nn.Linear(d, mlp), nn.SiLU(), nn.Linear(mlp, 4 if stochastic else 2))
        # feature scales, fitted on the training matches (set_scales) and saved with the weights
        self.register_buffer("node_mean", torch.zeros(6))
        self.register_buffer("node_std", torch.ones(6))
        self.register_buffer("edge_mean", torch.zeros(8))
        self.register_buffer("edge_std", torch.ones(8))
        self.register_buffer("dv_std", torch.ones(2))

    def set_scales(self, xy: torch.Tensor) -> None:
        """Fit feature scales on training windows (B, 23, T, 2) with T >= HIST + 1."""
        with torch.no_grad():
            nf = node_features(xy[:, :, :HIST]).reshape(-1, 6)
            self.node_mean.copy_(nf.mean(0))
            self.node_std.copy_(nf.std(0).clamp_min(1e-3))
            ef = edge_features(xy[:, :, :HIST]).reshape(-1, 8)
            self.edge_mean.copy_(ef.mean(0))
            self.edge_std.copy_(ef.std(0).clamp_min(1e-3))
            self.edge_mean[5:] = 0.0  # binary flags stay as they are
            self.edge_std[5:] = 1.0
            # three positions give two velocities (with two, kinematics copies the one velocity and dv is always 0:
            # v0 and v1 were trained with that, so their saved dv_std is the 1e-3 floor; their outputs are unaffected)
            _, v = kinematics(xy[:, :22, HIST - 2:HIST + 1])
            self.dv_std.copy_((v[:, :, 2] - v[:, :, 1]).reshape(-1, 2).std(0).clamp_min(1e-3))

    def forward(self, hist: torch.Tensor, goal: torch.Tensor | None = None) -> torch.Tensor:
        """hist (B, 23, HIST, 2) metres -> predicted velocity change (B, 22, 2) in m/s over the next step (the
        location; a stochastic model's samples come from ``sample``)."""
        return self.dist(hist, goal)[0]

    def dist(self, hist: torch.Tensor, goal: torch.Tensor | None = None) -> tuple[torch.Tensor, torch.Tensor | None]:
        """Location and Laplace scale (B, 22, 2) of the velocity change in m/s; the scale is None if deterministic.
        ``goal`` (B, 22, 4) is used by a goal-conditioned model only (None = no goals)."""
        B = hist.shape[0]
        nf = (node_features(hist) - self.node_mean) / self.node_std
        flags = FLAGS.to(hist.device).view(1, N_NODES, 1, 4).expand(B, -1, HIST, -1)
        _, h = self.gru(torch.cat([nf, flags], dim=-1).reshape(B * N_NODES, HIST, 10))
        h = h[-1].reshape(B, N_NODES, self.d)
        if self.goal:
            g = goal if goal is not None else torch.zeros(B, 22, 4, device=hist.device)
            h = h + torch.cat([self.goal_enc(goal_features(hist, g)), h.new_zeros(B, 1, self.d)], dim=1)
        e = (edge_features(hist) - self.edge_mean) / self.edge_std
        for layer in self.layers:
            h = layer(h, e)
        out = self.dec(h[:, :22])
        loc = out[..., :2] * self.dv_std
        if not self.stochastic:
            return loc, None
        return loc, torch.exp(out[..., 2:].clamp(-4.0, 2.0)) * self.dv_std

    def sample(self, hist: torch.Tensor, generator: torch.Generator | None = None,
               goal: torch.Tensor | None = None) -> torch.Tensor:
        """A velocity change drawn from the predicted Laplace distribution (the location if deterministic)."""
        loc, scale = self.dist(hist, goal)
        if scale is None:
            return loc
        u = torch.rand(loc.shape, generator=generator, device=loc.device) * (1.0 - 2e-6) + 1e-6 - 0.5
        return loc - scale * torch.sign(u) * torch.log1p(-2.0 * u.abs())


def step(hist: torch.Tensor, dv: torch.Tensor) -> torch.Tensor:
    """Next player positions (B, 22, 2) from the last velocity plus a bounded change; speed and pitch bounded."""
    p, v = hist[:, :22, -1], (hist[:, :22, -1] - hist[:, :22, -2]) / DT
    n = torch.linalg.norm(dv, dim=-1, keepdim=True).clamp_min(1e-6)
    dv = dv * torch.clamp(MAX_ACC * DT / n, max=1.0)
    v2 = v + dv
    s = torch.linalg.norm(v2, dim=-1, keepdim=True).clamp_min(1e-6)
    v2 = v2 * torch.clamp(MAX_SPEED / s, max=1.0)
    q = p + v2 * DT
    return torch.stack([q[..., 0].clamp(0.0, L), q[..., 1].clamp(0.0, W)], dim=-1)


def rollout(model: MotionGNN, start: torch.Tensor, ball: torch.Tensor, steps: int, sample: bool = False,
            generator: torch.Generator | None = None, goals: torch.Tensor | None = None) -> torch.Tensor:
    """Autonomous rollout: player states come only from the model's previous outputs; the ball path is supplied
    (by the controller in generation). start (B, 23, HIST, 2); ball (B, HIST + steps, 2). Returns (B, 22, steps, 2).
    With ``sample`` a stochastic model draws each step's velocity change instead of taking its location. ``goals``
    (B, 22, steps, 4) gives a goal-conditioned model each step's goals (seconds counted from that step)."""
    traj = start
    out = []
    for k in range(steps):
        hist = traj[:, :, -HIST:]
        g = goals[:, :, k] if goals is not None else None
        nxt = step(hist, model.sample(hist, generator, g) if sample else model(hist, g))
        out.append(nxt)
        traj = torch.cat([traj, torch.cat([nxt, ball[:, HIST + k].unsqueeze(1)], dim=1).unsqueeze(2)], dim=2)
    return torch.stack(out, dim=2)
