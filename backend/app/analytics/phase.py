"""Possession phase (spec 7.1): decided first, from observations only."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .observe import ObservationBuffer


@dataclass(frozen=True)
class PhaseInfo:
    attacking: str | None
    defending: str | None
    possession_phase: str  # buildup | progression | transition | unknown
    ball_state: str
    flags: tuple[str, ...]


def estimate_phase(buf: ObservationBuffer, k: int, cfg: Config) -> PhaseInfo:
    poss = buf.possession[k]
    state = buf.ball_state[k]
    if poss is None:
        return PhaseInfo(None, None, "unknown", state, ("no_possession",))
    attacking, defending = poss, buf.other(poss)
    if state in ("loose", "dead"):
        return PhaseInfo(attacking, defending, "unknown", state, (f"ball_{state}",))
    for j in buf.window(k, cfg.window.transition_s):
        if buf.possession[j] is not None and buf.possession[j] != poss:
            return PhaseInfo(attacking, defending, "transition", state, ("recent_possession_change",))
    u_ball = float(buf.ball_uv(k, attacking)[0])
    phase = "buildup" if u_ball < cfg.phase.buildup_max_u_m else "progression"
    return PhaseInfo(attacking, defending, phase, state, ())
