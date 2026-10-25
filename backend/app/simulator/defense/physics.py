"""Steering physics shared by the v2 scenarios (generator-private).

Order per spec 6.3: desired velocity -> acceleration clamp -> speed clamp ->
integration, then pitch bounds. Same model as the v1 flank engine (arrive
braking, separation, Ornstein-Uhlenbeck target noise), re-implemented here
so the v1 fixtures stay byte-identical.
"""

from __future__ import annotations

import math

import numpy as np

DT = 0.2
PITCH_L, PITCH_W = 105.0, 68.0
MARGIN = 0.5
TARGET_MARGIN = 1.0
ACCEL_MPS2 = 4.5  # below the 5 m/s^2 development cap
SPEED_CAP_MPS = 8.6  # below the 9 m/s development cap
ARRIVE_DECEL = 0.8 * ACCEL_MPS2
SEPARATION_M = 1.3
NOISE_THETA_S = 2.5


def clip_to_pitch(p: np.ndarray, margin: float = MARGIN) -> np.ndarray:
    out = np.array(p, dtype=float, copy=True)
    out[..., 0] = np.clip(out[..., 0], margin, PITCH_L - margin)
    out[..., 1] = np.clip(out[..., 1], margin, PITCH_W - margin)
    return out


def step(pos: np.ndarray, vel: np.ndarray, tgt: np.ndarray, cap: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """One integration step for all players. Returns (new_pos, new_vel)."""
    tgt = clip_to_pitch(tgt, TARGET_MARGIN)
    delta = tgt - pos
    dist = np.linalg.norm(delta, axis=1)
    speed = np.minimum.reduce([cap, np.sqrt(2.0 * ARRIVE_DECEL * dist), dist / 0.5])
    with np.errstate(invalid="ignore", divide="ignore"):
        desired = np.where(dist[:, None] > 1e-6, delta / np.maximum(dist, 1e-9)[:, None], 0.0) * speed[:, None]
    diff = pos[:, None, :] - pos[None, :, :]
    d = np.linalg.norm(diff, axis=2)
    close = (d < SEPARATION_M) & (d > 1e-6)
    if close.any():
        w = np.where(close, (SEPARATION_M - d) / np.where(d > 1e-6, d, 1.0), 0.0)
        desired += 1.5 * (w[:, :, None] * diff).sum(axis=1)
    dv = desired - vel
    dvn = np.linalg.norm(dv, axis=1)
    lim = ACCEL_MPS2 * DT
    new_vel = vel + dv * np.where(dvn > lim, lim / np.maximum(dvn, 1e-9), 1.0)[:, None]
    sp = np.linalg.norm(new_vel, axis=1)
    new_vel *= np.where(sp > SPEED_CAP_MPS, SPEED_CAP_MPS / np.maximum(sp, 1e-9), 1.0)[:, None]
    new_pos = clip_to_pitch(pos + new_vel * DT)
    return new_pos, (new_pos - pos) / DT


def ou_step(noise: np.ndarray, sigma: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return noise * (1.0 - DT / NOISE_THETA_S) + rng.normal(0.0, 1.0, noise.shape) * (
        sigma[:, None] * math.sqrt(2.0 * DT / NOISE_THETA_S))
