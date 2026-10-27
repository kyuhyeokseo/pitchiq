"""Generate the v2 defensive-scenario clips (generator-private CLI).

    python -m app.simulator.defense.generate --fixtures            # committed demo clips

Holdout clips are generated in memory by ``evaluation/holdout_v2.py`` (``make evaluate``).

``generate_clip(seed, private_config)`` is the single entry point (spec 16).
The same seed and config always give byte-identical files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .engine import DefenseSimulation
from .publish import PRIVATE_TRUTH_DIR, PUBLIC_DIR, ClipSpec, build_public, write_clip
from .scenarios import family_a, family_b, family_c, family_d, family_e, family_g

GENERATOR_VERSION = "defense-0.8.0"  # 0.2: the whole game shifts after a long ball; 0.3: off-ball slots follow the
# ball by slopes measured on real play, wander instead of orbiting, keep their spacing and do not stop dead;
# 0.4: A's markers drop off men far from the ball (loose marking), C and E hold the players their stories rely on;
# 0.4.1: D's double pivot holds its height (its markers stay in the build-up zone);
# 0.5: a pass to a runner is never released offside, a ball into space rolls on until someone reaches it, C's far
# winger cuts the switch in "support", C's story players shift their feet, D's story accepts either restriction label
# 0.6: team shape after a review as a football fan - keepers stay home, the clip ends once the defence wins the ball,
# defenders keep a leash to their formation spot and their spacing, the back line holds the edge of its box, the
# nearest man engages a free carrier in a dangerous spot (goal-distance danger), screens skip a man a team-mate has,
# ground passes wait for a clear lane; B's holding midfielder is drawn onto the pivot, C's screens stand apart and
# pick up their men once the ball goes wide, E's switch goes over the screen, G's near centre-back chases the run
# 0.7: a second fan review - A "cover" keeps the left winger marked (the far full back tucks in), B "compact" keeps
# its 4-3-3 (no midfielder jumps out; the holding midfielder cages the striker) while the ball goes round the back,
# C's striker cuts the pass to the other centre-back as he presses and "support" marks the pivot man to man, G "main"
# sends the right winger in behind too and G "offside" goes three on three in midfield and finds the free centre-back
# 0.8: a presser stands 3 m off the man on the ball (PRESS_STOP_M in engine.py), on the line of the pass he cuts
BUILDERS = {"A": family_a, "B": family_b, "C": family_c, "D": family_d, "E": family_e, "G": family_g}

# Demo fixtures. Opaque public ids; the family/variant mapping is private.
# DISCLOSURE: every fixture uses seed 7. The scenario parameters were tuned on
# this seed while the analytics gates were being written, so these clips show
# the intended behaviour by construction; holdout seeds measure how often it
# holds (evaluation/reports).
FIXTURES: list[dict] = [
    {"clip_id": "clip_001", "family": "A", "variant": "main", "seed": 7, "attacking_team": "blue",
     "attack_direction": 1, "start_s": 612.0},
    {"clip_id": "clip_002", "family": "B", "variant": "main", "seed": 7, "attacking_team": "blue",
     "attack_direction": -1, "start_s": 2955.0, "period": 2},
    {"clip_id": "clip_003", "family": "C", "variant": "main", "seed": 7, "attacking_team": "red",
     "attack_direction": 1, "start_s": 1324.0},
    {"clip_id": "clip_004", "family": "A", "variant": "cover", "seed": 7, "attacking_team": "blue",
     "attack_direction": 1, "start_s": 1846.0},
    {"clip_id": "clip_005", "family": "B", "variant": "compact", "seed": 7, "attacking_team": "blue",
     "attack_direction": -1, "start_s": 3611.0, "period": 2},
    {"clip_id": "clip_006", "family": "C", "variant": "support", "seed": 7, "attacking_team": "red",
     "attack_direction": 1, "start_s": 2402.0},
    {"clip_id": "clip_007", "family": "D", "variant": "main", "seed": 7, "attacking_team": "blue",
     "attack_direction": 1, "start_s": 415.0},
    {"clip_id": "clip_008", "family": "D", "variant": "escape", "seed": 7, "attacking_team": "blue",
     "attack_direction": 1, "start_s": 2188.0},
    {"clip_id": "clip_009", "family": "E", "variant": "step", "seed": 7, "attacking_team": "red",
     "attack_direction": -1, "start_s": 3020.0, "period": 2},
    {"clip_id": "clip_010", "family": "E", "variant": "hold", "seed": 7, "attacking_team": "red",
     "attack_direction": -1, "start_s": 3384.0, "period": 2},
    {"clip_id": "clip_011", "family": "G", "variant": "main", "seed": 7, "attacking_team": "blue",
     "attack_direction": -1, "start_s": 1210.0},
    {"clip_id": "clip_012", "family": "G", "variant": "offside", "seed": 7, "attacking_team": "blue",
     "attack_direction": -1, "start_s": 2730.0},
]


def streams(seed: int, index: int = 0) -> tuple[np.random.Generator, ...]:
    ss = np.random.SeedSequence([int(seed), int(index)])
    return tuple(np.random.default_rng(s) for s in ss.spawn(3))


def perturb_plan(plan, rng: np.random.Generator, ranges: dict) -> dict:
    """Holdout variation outside the development settings (spec 16): reaction lags, marking cushions, running
    speeds, pass timings and start spots, each drawn once per clip. Returns the values drawn."""
    drawn = {k: float(rng.uniform(*ranges[k])) for k in ("lag_scale", "cushion_scale", "speed_scale", "timing_scale")}
    plan.lag_s *= drawn["lag_scale"]
    for segs in plan.segs.values():
        for s in segs:
            p = dict(s.p)
            if "lag" in p:
                p["lag"] *= drawn["lag_scale"]
            if "gs" in p:
                p["gs"] *= drawn["cushion_scale"]
            if "speed" in p:
                p["speed"] *= drawn["speed_scale"]
            s.p = p
    for step in plan.ball_steps:
        if "after_receive_s" in step:
            step["after_receive_s"] *= drawn["timing_scale"]
    jit = float(ranges.get("start_jitter_m", 0.0))
    for key, (u, v) in list(plan.init.items()):
        if not key.endswith(":GK"):
            plan.init[key] = (u + float(rng.normal(0.0, jit)), v + float(rng.normal(0.0, jit)))
    drawn["start_jitter_m"] = jit
    return {k: round(v, 4) for k, v in drawn.items()}


def story_window(truth: dict, markers: dict, n_steps: int) -> dict | None:
    """When the designed story was on the pitch (private, evaluation only): from its start marker to the last
    ball action, and whether the planned ball actions were all played out (or ended by an interception)."""
    story = truth.get("story")
    if not story:
        return None
    t0 = markers.get(f"{story['start']}_t")
    ends = [v for k, v in markers.items() if isinstance(v, float) and (k.endswith("_arrive_t") or k == "interception_t")]
    executed = "interception_t" in markers or f"arrive_{n_steps - 1}_t" in markers
    if t0 is None or not ends:
        return {**story, "window_clip_s": None, "executed": False}
    return {**story, "window_clip_s": [round(t0, 3), round(max(ends), 3)], "executed": bool(executed)}


_MODEL = None


def _motion_model():
    global _MODEL
    if _MODEL is None:
        from .hybrid import load_motion_model

        _MODEL = load_motion_model()
    return _MODEL


def generate_clip(seed: int, private_config: dict):
    """Simulate one clip. Returns (meta, frames, events, truth) as plain dicts/lists, not yet written."""
    attack_rng, defense_rng, motion_rng = streams(seed, private_config.get("index", 0))
    plan, truth = BUILDERS[private_config["family"]](attack_rng, defense_rng, private_config["variant"])
    perturbation = None
    if private_config.get("perturb"):
        # its own stream, so clips without a perturbation are unchanged
        p_rng = np.random.default_rng(np.random.SeedSequence([int(seed), int(private_config.get("index", 0)), 77]))
        perturbation = perturb_plan(plan, p_rng, private_config["perturb"])
    engine = private_config.get("engine", "rule")
    if engine == "hybrid":  # learned movement around the scenario controller (needs the optional torch install)
        from .hybrid import HybridSimulation

        frames, events, markers = HybridSimulation(plan, motion_rng, _motion_model()).run()
    else:
        frames, events, markers = DefenseSimulation(plan, motion_rng).run()
    story = story_window(truth, markers, len(plan.ball_steps))
    if story is not None:
        truth = {**truth, "story": story}
    if perturbation is not None:
        truth = {**truth, "perturbation": perturbation}
    if engine != "rule":  # rule-engine truth stays exactly as it was
        truth = {**truth, "engine": engine}
    spec = ClipSpec(clip_id=private_config["clip_id"], match_id=private_config.get("match_id", "synthetic_"
                                                                                    + private_config["clip_id"][-3:]),
                    attacking_team=private_config["attacking_team"],
                    attack_direction=private_config["attack_direction"], start_s=private_config["start_s"],
                    period=private_config.get("period", 1), mirror=private_config.get("mirror", False))
    meta, obs, evs = build_public(frames, events, spec)
    truth = {
        "clip_id": spec.clip_id, "generator_version": GENERATOR_VERSION, "seed": seed,
        "private_config": {k: v for k, v in private_config.items() if k != "clip_id"},
        **truth,
        "markers_match_s": {k: (round(spec.start_s + v, 3) if isinstance(v, float) and k.endswith("_t") else v)
                            for k, v in markers.items()},
        "attacking_team_id": spec.attacking_team, "defending_team_id": spec.defending_team,
    }
    return meta, obs, evs, truth


def generate_fixtures(public_root: Path = PUBLIC_DIR, truth_root: Path = PRIVATE_TRUTH_DIR) -> list[Path]:
    out = []
    for cfg in FIXTURES:
        meta, obs, evs, truth = generate_clip(cfg["seed"], cfg)
        out.append(write_clip(meta, obs, evs, truth, public_root, truth_root))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--public", type=Path, default=PUBLIC_DIR)
    ap.add_argument("--truth", type=Path, default=PRIVATE_TRUTH_DIR)
    args = ap.parse_args(argv)
    if args.fixtures:
        for p in generate_fixtures(args.public, args.truth):
            meta = json.loads((p / "manifest.json").read_text())
            n = sum(1 for _ in open(p / "tracking.jsonl"))
            print(f"[fixtures] {p}  frames={n}  {meta['clips'][0]['checksum'][:23]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
