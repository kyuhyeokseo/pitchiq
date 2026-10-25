"""Load scenario recipes and sample generator-private clip parameters.

Each clip uses three independent RNG streams derived from one SeedSequence:
attack parameters, defense parameters and motion noise. Keeping attack and
defense separate lets tests replay an identical attacking run against a
different defender response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RECIPES_PATH = REPO_ROOT / "data" / "config" / "scenario_recipes.yaml"

RUN_MODES = ("overlap", "underlap", "support", "parallel", "aborted", "none")
DEFENDER_RESPONSES = ("mark_runner", "hold_line", "switch_mark")


def load_recipes(path: Path | str = DEFAULT_RECIPES_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for name, r in cfg["recipes"].items():
        if r["run_mode"] not in RUN_MODES:
            raise ValueError(f"recipe {name}: unknown run_mode {r['run_mode']}")
        for resp in r["defender_response"]:
            if resp not in DEFENDER_RESPONSES:
                raise ValueError(f"recipe {name}: unknown defender response {resp}")
    return cfg


@dataclass(frozen=True)
class ClipParams:
    """Generator-private parameters for one clip (never serialized publicly)."""

    recipe_name: str
    run_mode: str
    pattern_truth: str
    generator_intent: str
    defender_response: str
    min_duration_s: float
    max_duration_s: float
    # attack
    carrier_start: tuple[float, float]
    winger_start: tuple[float, float]
    fullback_start: tuple[float, float]
    t_pass_in_s: float
    pass_speed_mps: float
    run_delay_s: float
    run_speed_mps: float
    decide_after_s: float
    tail_s: float
    third_man_run: bool
    third_man_offset: tuple[float, float]
    run_beyond_m: float = 0.0
    lane_y_m: float = 0.0
    inside_offset_m: float = 0.0
    winger_drive: tuple[float, float] = (0.0, 0.0)
    winger_drive_speed_mps: float = 2.5
    support_gap_m: float = 0.0
    winger_run_m: float = 0.0
    parallel_gap_m: float = 0.0
    abort_gap_m: float = 0.0
    n_passes: int = 0
    # defense
    react_lag_s: float = 0.6
    mark_dx_m: float = 1.8
    mark_dy_m: float = 1.0
    lb_speed_mps: float = 7.0
    tracker_lag_s: float = 0.5
    tracker_speed_mps: float = 7.0
    cover_tracks_third_man: bool = True
    lcm_lag_s: float = 0.5
    lcm_speed_mps: float = 6.5
    handover_dx_m: float = 8.0
    extras: dict[str, Any] = field(default_factory=dict)


def _u(rng: np.random.Generator, rng_spec: list[float]) -> float:
    lo, hi = rng_spec
    return float(rng.uniform(lo, hi))


def _xy(rng: np.random.Generator, spec: dict[str, list[float]]) -> tuple[float, float]:
    return (_u(rng, spec["x"]), _u(rng, spec["y"]))


def clip_streams(base_seed: int, clip_index: int) -> tuple[np.random.Generator, ...]:
    ss = np.random.SeedSequence([int(base_seed), int(clip_index)])
    return tuple(np.random.default_rng(s) for s in ss.spawn(3))


def sample_clip_params(
    cfg: dict[str, Any],
    recipe_name: str,
    attack_rng: np.random.Generator,
    defense_rng: np.random.Generator,
    response_override: str | None = None,
) -> ClipParams:
    recipe = cfg["recipes"][recipe_name]
    a = cfg["attack"]
    rp = recipe["params"]

    # Attack stream: fixed draw order so identical seeds give identical runs.
    attack = dict(
        carrier_start=_xy(attack_rng, a["carrier_start"]),
        winger_start=_xy(attack_rng, a["winger_start"]),
        fullback_start=_xy(attack_rng, a["fullback_start"]),
        t_pass_in_s=_u(attack_rng, a["t_pass_in_s"]),
        pass_speed_mps=_u(attack_rng, a["pass_speed_mps"]),
        run_delay_s=_u(attack_rng, a["run_delay_after_release_s"]),
        run_speed_mps=_u(attack_rng, a["run_speed_mps"]),
        decide_after_s=_u(attack_rng, a["decide_after_receive_s"]),
        tail_s=_u(attack_rng, a["tail_s"]),
        third_man_offset=(_u(attack_rng, [9.0, 13.0]), _u(attack_rng, [3.0, 7.0])),
    )
    for key in sorted(rp):
        spec = rp[key]
        if key == "third_man_run":
            attack["third_man_run"] = bool(spec)
        elif key == "winger_drive_m":
            attack["winger_drive"] = _xy(attack_rng, spec)
        elif key == "n_passes":
            attack["n_passes"] = int(attack_rng.integers(spec[0], spec[1] + 1))
        else:
            attack[key] = _u(attack_rng, spec)
    attack.setdefault("third_man_run", False)

    d = cfg["defense"]
    options = recipe["defender_response"]
    response = options[int(defense_rng.integers(0, len(options)))]
    if response_override is not None:
        if response_override not in DEFENDER_RESPONSES:
            raise ValueError(f"unknown defender response {response_override}")
        response = response_override
    prof = d["profiles"][response]
    defense = dict(
        react_lag_s=_u(defense_rng, d["react_lag_s"]),
        mark_dx_m=_u(defense_rng, d["mark_dx_m"]),
        mark_dy_m=_u(defense_rng, d["mark_dy_m"]),
        lb_speed_mps=_u(defense_rng, prof["lb_speed_mps"]),
        tracker_lag_s=_u(defense_rng, prof["tracker_lag_s"]),
        tracker_speed_mps=_u(defense_rng, prof["tracker_speed_mps"]),
        cover_tracks_third_man=bool(prof["cover_tracks_third_man"]),
    )
    if defense["cover_tracks_third_man"]:
        defense["lcm_lag_s"] = _u(defense_rng, prof["lcm_lag_s"])
        defense["lcm_speed_mps"] = _u(defense_rng, prof["lcm_speed_mps"])
    if "handover_dx_m" in prof:
        defense["handover_dx_m"] = _u(defense_rng, prof["handover_dx_m"])

    return ClipParams(
        recipe_name=recipe_name,
        run_mode=recipe["run_mode"],
        pattern_truth=recipe["pattern_truth"],
        generator_intent=recipe["generator_intent"],
        defender_response=response,
        min_duration_s=float(cfg["clip"]["min_duration_s"]),
        max_duration_s=float(cfg["clip"]["max_duration_s"]),
        **attack,
        **defense,
    )
