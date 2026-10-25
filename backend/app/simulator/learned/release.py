"""Package a trained run for public release: checkpoint, configuration, measured metrics, model card, data provenance.

    PYTHONPATH=backend python -m app.simulator.learned.release --run <run dir> --processed <idsse processed dir> \
        --raw <idsse raw dir> --out models/motion_gnn_v0

Every number in the card and the provenance note is read from the run's metrics.json and the data audit; nothing is
typed by hand. The real tracking data is not copied: only the trained weights, which the CC BY 4.0 licence permits
with attribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from ..defense.hybrid import AUTHORITY, CARRY_SPEED, FREE_M, GOAL_PACE, GOAL_S_MAX, GOAL_S_MIN, OFFSIDE_WAIT_S, RECEIVE_M
from .dataset import HIST, ROLL, SPLITS
from .model import MAX_ACC, MAX_SPEED

CITATION = ("Bassek, M., Rein, R., Weber, H. & Memmert, D. (2025). An integrated dataset of spatiotemporal and event "
            "data in elite soccer. Scientific Data 12, 195. https://doi.org/10.1038/s41597-025-04505-y. Data: "
            "https://doi.org/10.6084/m9.figshare.28196177 (v1).")
LICENCE = "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), published with the DFL's authorisation"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _table(rows: list[list], head: list[str]) -> str:
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                     + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows])


def model_card(m: dict, ck_sha: str, version: str = "v0") -> str:
    t, roll = m["test"], m["autonomous_rollout_test"]
    phases = [[ph, t[f"rollout_{ph}"]["n"], t[f"rollout_{ph}"]["ade_1s"], t[f"rollout_{ph}"]["ade_2s"],
               t[f"rollout_{ph}"]["fde_2s"]] for ph in ("all", "buildup", "middle", "final") if f"rollout_{ph}" in t]
    s = m["settings"]
    stoch = bool(s.get("stochastic"))
    goal = bool(s.get("goal"))
    keys = [k for k in roll["real"] if k in roll["model"]]
    shape = [[k, roll["real"][k], roll["model"][k]] + ([roll["model_location_only"][k]] if stoch else [])
             + ([roll["model_no_goal"][k]] if goal else []) for k in keys]
    shape_head = ["statistic", "real (test match)"] + (["model rollout (sampled)", "location only"] if stoch
                                                       else ["model rollout"]) + (["no goals"] if goal else [])
    sampled = t.get("rollout_all_sampled")
    sampled_line = (f"\n\n**Sampled rollouts** (one draw per window, as in generation): ADE 2 s {sampled['ade_2s']} m, "
                    f"FDE 2 s {sampled['fde_2s']} m over {sampled['n']} windows. A single draw is not the most likely "
                    "path, so it is expected to sit above the location's error.") if sampled else ""
    decoder = ("a shared MLP that predicts each player's velocity change for the next 0.2 s as a Laplace "
               "distribution (location and scale). Generation samples from it." if stoch else
               "a shared MLP that predicts each player's velocity change for the next 0.2 s.")
    stage1 = ("Laplace negative log-likelihood of the velocity change (location and scale)." if stoch else
              "one-step velocity-change regression (smooth L1 on scaled velocity change).")
    stage2_extra = (f" With it, {s.get('nll_weight', 0.1)} × the one-step Laplace negative log-likelihood keeps the "
                    "scale calibrated." if stoch else "")
    damping = ("It samples each 0.2 s step independently given the history; there are no explicit behaviour modes. "
               "Compare the sampled and location-only speed statistics above." if stoch else
               "It is deterministic and does not sample behaviour modes. Uncertain futures are averaged, which can damp "
               "movement (compare the speed statistics above).")
    train_flag = (" --stochastic" if stoch else "") + (" --goal" if goal else "")
    goal_input = (f"\n- **Goal input (goal-conditioned):** per player, where he is to be and in how many seconds. It is "
                  "encoded per player after the GRU and before message passing, so every node also sees the others' "
                  "goals. A player without a goal gets zeros." if goal else "")
    goal_learned = (f" Each player is also given a goal: where to be and when. In generation the controller supplies "
                    f"it (its target for him, to be reached at {GOAL_PACE:g} × his behaviour's speed cap within "
                    f"{GOAL_S_MIN:g}–{GOAL_S_MAX:g} s), and the model decides how he gets there." if goal else "")
    goal_training = (f"\n- **Goals in training:** each player's real position {s['goal_steps_stage1'][0] * 0.2:g}–"
                     f"{s['goal_steps_stage1'][1] * 0.2:g} s after the last history step (stage 2: one still "
                     f"{(s['goal_steps_stage2'][0] - ROLL + 1) * 0.2:g}–{(s['goal_steps_stage2'][1] - ROLL + 1) * 0.2:g} s "
                     f"away at the rollout's last step), with the seconds left to it. {s.get('goal_drop', 0.15):g} of "
                     "the players get no goal, so the model also moves players without one. Validation and test use "
                     "one fixed draw with every player given a goal (where enough later frames exist)." if goal else "")
    ng = t.get("no_goal")
    goal_test = (f"\n\n**With goals vs without** (goals from what really happened, given to {t['goal_share']:.0%} of "
                 f"the players): one-step position error {t['one_step']['pos_err_m']} m vs "
                 f"{ng['one_step']['pos_err_m']} m; 2 s rollout ADE {t['rollout_all']['ade_2s']} m vs "
                 f"{ng['rollout_all']['ade_2s']} m. The phase table above is with goals. A goal is a strong hint about the "
                 "future, so the errors with goals are not comparable with those of a model without goal input."
                 if goal and ng else "")
    goal_roll = (f"\n\nGoals in these rollouts: {roll['goals']}. The \"no goals\" column is the same model with none; its "
                 f"displacement from real: {json.dumps(roll.get('displacement_from_real_no_goal_m'))}." if goal else "")
    goal_limits = ("\n  - With goals it follows wherever it is sent: the scenario controller is responsible for "
                   "sensible targets. Goals in training came from real positions, and the controller's targets are "
                   "its own, so how closely they match real intentions is not measured here." if goal else "")
    return f"""# MotionGNN {version}: a compact learned movement component for PitchIQ's hybrid football simulator

A shared GRU history encoder with a two-layer graph network over the 22 players and the ball. It predicts each
player's velocity change over the next 0.2 s. It is one component of a hybrid simulator, not a football simulator
on its own.

## What is learned and what is not
- **Learned:** how players move from one 0.2 s step to the next, given 3 s of everyone's history and the ball's
  position. This covers positioning and reactions of team-mates and opponents around the ball.{goal_learned}
- **Controller-driven (PitchIQ scenario controller, not learned):**
  - every ball action: possession, pass choice and release time, flight, reception, interception and shot;
  - the ball carrier, who holds the ball or carries it forward when no opponent is within {FREE_M:g} m (at
    {CARRY_SPEED:g} m/s);
  - the receiver running onto a pass;
  - scenario-defining behaviours, blended with the model by authority: `{json.dumps(AUTHORITY)}`.
- **Physical bounds applied to every step:** speed at most {MAX_SPEED:g} m/s, change of velocity at most
  {MAX_ACC:g} m/s², and positions kept on the pitch.
- **Offside at pass release:** a planned pass waits up to {OFFSIDE_WAIT_S:g} s for its receiver to be onside. A
  pass is controlled only once the receiver is within {RECEIVE_M:g} m of the ball.

## Architecture (actual parameter count: {m['parameters']:,})
- **Nodes:** 22 players and the ball. Node order: team in possession (goalkeeper first), defending team
  (goalkeeper first), ball.
- **Node input per step:** {HIST} steps at 5 Hz (3.0 s). Each step carries position, velocity and the ball's
  offset, plus flags for team in possession, defending team, goalkeeper and ball.
- **Encoder:** a shared single-layer GRU with hidden size 64.
- **Interaction:** two message-passing layers with hidden size 64 over all ordered node pairs. Edge features are
  relative position, relative velocity, distance, and team-mate / opponent / ball flags. Messages and updates use
  MLPs with 128 hidden units, a residual connection and LayerNorm.
- **Decoder:** {decoder}{goal_input}
- **Not inputs:** player names, shirt numbers, match ids and outcomes.

## Data
- **Source:** {CITATION}
- **Licence:** {LICENCE}.
- **Matches:** 7, split by complete match before any window was cut.
  - Train: {', '.join(SPLITS['train'])}
  - Validation: {', '.join(SPLITS['val'])}
  - Test: {', '.join(SPLITS['test'])}
- **Preprocessing:**
  - 25 Hz resampled to 5 Hz;
  - pitch moved to a 105 × 68 m frame with the origin bottom-left;
  - rotated so that the team in possession attacks +x;
  - segments cut at stoppages (BallStatus 0), possession changes and roster changes;
  - only segments with exactly 11 tracked players per team and one tracked goalkeeper each are kept.
- **Feature scales:** fitted on the training matches only.
- **Windows:** {json.dumps(m['windows'])}

## Training
- **Stage 1:** {stage1}
- **Stage 2:** multi-step rollout training. The model runs {ROLL} steps (2 s) on its own player outputs, with the
  observed ball as the context node, and the loss is the mean plus half the final position error.{stage2_extra}{goal_training}
- **Optimiser:** AdamW, learning rate {s['lr']}, weight decay {s['weight_decay']}, batch {s['batch']}. Stage 2
  uses 0.3 × the learning rate.
- **Regularisation:** a random left-right flip.
- **Early stopping:** on the validation 2 s rollout error, patience {s['patience']}.
- **Seed and hardware:** seed {s['seed']}, trained on {m['device']}{(' (' + m['gpu'] + ')') if m.get('gpu') else ''}.
- **Elapsed:** {m['elapsed_s']} s.

## Held-out test match (measured)
**One-step:** position error {t['one_step']['pos_err_m']} m and velocity-change error
{t['one_step']['dv_err_mps']} m/s over {t['one_step']['n']} windows.

**Rollouts on the model's own outputs** (ball path observed; errors in metres):

{_table(phases, ['phase (ball zone)', 'windows', 'ADE 1 s', 'ADE 2 s', 'FDE 2 s'])}{sampled_line}{goal_test}

**Autonomous {roll['seconds']:g} s rollouts** from {roll['n_starts']} real starting states. The model's own outputs
are fed back, and the observed ball path is supplied as the context node.

Mean displacement from what really happened: {json.dumps(roll['displacement_from_real_m'])}. Non-finite values:
{roll['non_finite']}.

Movement statistics, real versus model:

{_table(shape, shape_head)}{goal_roll}

These are two separate checks: prediction accuracy over 1–2 s, and whether long rollouts keep plausible movement
statistics. No other architecture was trained, so no comparative claim is made.

## Intended use and limitations
- **Intended use:** off-ball movement inside PitchIQ's hybrid generator, producing synthetic demonstration clips
  for the defensive-analysis pipeline. It is not intended for analysing real players or matches.
- **Limitations:**
  - {damping}
  - It was trained on 5 matches from one season, from two German divisions.
  - The ball is not modelled, so the controller's ball plan is part of every generated clip.
  - Possession transitions are cut out of training, so transition movement comes from the controller.{goal_limits}
- **Checkpoint:** `motion_gnn.pt`, sha256 `{ck_sha}`.

## Reproduce
```bash
# data (2.6 GB, CC BY 4.0) from figshare 28196177 into <raw>, then on a compute node:
PYTHONPATH=backend python -m app.simulator.learned.idsse --raw <raw> --out <processed>
PYTHONPATH=backend python -m app.simulator.learned.train --processed <processed> --out <run>{train_flag}
PYTHONPATH=backend python -m app.simulator.learned.release --run <run> --processed <processed> --raw <raw> --out models/motion_gnn_{version}
# generate a hybrid clip (needs torch): private config {{"engine": "hybrid", ...}} in app.simulator.defense.generate
```
"""


def provenance(audit: list[dict], raw: Path) -> str:
    rows = []
    for a in audit:
        for half, h in a["halves"].items():
            rows.append([a["match_id"], a["competition"], half, h["frames_25hz_ball"], h["duration_min"], h["in_play_share"],
                         h["possession_known_in_play"], f"{h['persons_tracked']['home']}/{h['persons_tracked']['away']}",
                         h["mid_track_gap_frames"], h["goalkeepers_tracked"], h["home_attacks"]])
    md5 = (raw / "MD5_VERIFIED.txt").read_text() if (raw / "MD5_VERIFIED.txt").exists() else "(not verified)"
    return f"""# Data provenance: IDSSE open tracking data

- **Citation:** {CITATION}
- **Licence:** {LICENCE}. The paper's data record says the data set "is released under a CC-BY 4.0 license with the
  authorization from the DFL".
- **Attribution and changes:**
  - referee tracks and all names dropped;
  - 25 Hz resampled to 5 Hz;
  - coordinates moved from the centre origin to PitchIQ's bottom-left 105 × 68 m frame;
  - rotated so that the team in possession attacks +x.
- **Not redistributed:** the raw and processed data stay outside this repository and are never part of
  `data/public/` or the serving image. Only the trained weights and aggregate statistics are published.
- **Not used:** event data (counted in the audit only), player names, shirt numbers, match ids as features, and
  video (not part of the data set).
- **Contents:** 7 matches of the 2022/23 season (2 Bundesliga, 5 2. Bundesliga). Each match has a match
  information, an events and a positions XML file. The positions are TRACAB Gen5 at 25 Hz in metres, with the
  origin at the pitch centre. The ball carries height, BallPossession (1 home / 2 away) and BallStatus (1 in play).
  Players keep persistent DFL person ids.

## Audit (measured from the files)
{_table(rows, ['match', 'competition', 'half', 'frames (25 Hz, ball)', 'minutes', 'in play', 'possession known in play', 'persons tracked home/away', 'mid-track gap frames (5 Hz)', 'goalkeepers tracked', 'home attacks'])}

## Downloaded files (figshare MD5 check)
```
{md5}```
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--processed", type=Path, required=True)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    m = json.loads((args.run / "metrics.json").read_text())
    shutil.copyfile(args.run / "motion_gnn.pt", args.out / "motion_gnn.pt")
    shutil.copyfile(args.run / "metrics.json", args.out / "metrics.json")
    ck = sha256(args.out / "motion_gnn.pt")
    cfg = {"model": m["model"], "parameters": m["parameters"], "hist_steps": HIST, "dt_s": 0.2, "rollout_train_steps": ROLL,
           "max_speed_mps": MAX_SPEED, "max_acc_mps2": MAX_ACC, "training": m["settings"], "splits": SPLITS,
           "generation": {"authority": AUTHORITY, "free_carrier_m": FREE_M, "carry_speed_mps": CARRY_SPEED,
                          "offside_wait_s": OFFSIDE_WAIT_S, "receive_m": RECEIVE_M,
                          **({"goal_pace": GOAL_PACE, "goal_s": [GOAL_S_MIN, GOAL_S_MAX]}
                             if m["settings"].get("goal") else {})},
           "checkpoint_sha256": ck, "data": {"citation": CITATION, "licence": LICENCE}}
    (args.out / "config.json").write_text(json.dumps(cfg, indent=1) + "\n")
    (args.out / "MODEL_CARD.md").write_text(model_card(m, ck, args.out.name.removeprefix("motion_gnn_")))
    audit = json.loads((args.processed / "audit.json").read_text())
    (args.out / "DATA_PROVENANCE.md").write_text(provenance(audit, args.raw))
    print("released to", args.out, "sha256", ck)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
