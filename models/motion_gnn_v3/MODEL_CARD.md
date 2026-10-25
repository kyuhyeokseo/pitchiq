# MotionGNN v3: a compact learned movement component for PitchIQ's hybrid football simulator

A shared GRU history encoder with a two-layer graph network over the 22 players and the ball. It predicts each
player's velocity change over the next 0.2 s. It is one component of a hybrid simulator, not a football simulator
on its own.

## What is learned and what is not
- **Learned:** how players move from one 0.2 s step to the next, given 3 s of everyone's history and the ball's
  position. This covers positioning and reactions of team-mates and opponents around the ball. Each player is also given a goal: where to be and when. In generation the controller supplies it (its target for him, to be reached at 0.75 × his behaviour's speed cap within 1–4 s), and the model decides how he gets there.
- **Controller-driven (PitchIQ scenario controller, not learned):**
  - every ball action: possession, pass choice and release time, flight, reception, interception and shot;
  - the ball carrier, who holds the ball or carries it forward when no opponent is within 8 m (at
    4 m/s);
  - the receiver running onto a pass;
  - scenario-defining behaviours, blended with the model by authority: `{"run": 1.0, "carry": 1.0, "attack_ball": 1.0, "sweep": 1.0, "press": 0.85, "chase": 0.85, "press_mark": 0.6, "recover": 0.5, "screen": 0.5, "mark": 0.4, "cover": 0.35, "gk": 0.3, "hold": 0.0, "check": 0.0}`.
- **Physical bounds applied to every step:** speed at most 10 m/s, change of velocity at most
  8 m/s², and positions kept on the pitch.
- **Offside at pass release:** a planned pass waits up to 2 s for its receiver to be onside. A
  pass is controlled only once the receiver is within 1 m of the ball.

## Architecture (actual parameter count: 133,956)
- **Nodes:** 22 players and the ball. Node order: team in possession (goalkeeper first), defending team
  (goalkeeper first), ball.
- **Node input per step:** 15 steps at 5 Hz (3.0 s). Each step carries position, velocity and the ball's
  offset, plus flags for team in possession, defending team, goalkeeper and ball.
- **Encoder:** a shared single-layer GRU with hidden size 64.
- **Interaction:** two message-passing layers with hidden size 64 over all ordered node pairs. Edge features are
  relative position, relative velocity, distance, and team-mate / opponent / ball flags. Messages and updates use
  MLPs with 128 hidden units, a residual connection and LayerNorm.
- **Decoder:** a shared MLP that predicts each player's velocity change for the next 0.2 s as a Laplace distribution (location and scale). Generation samples from it.
- **Goal input (goal-conditioned):** per player, where he is to be and in how many seconds. It is encoded per player after the GRU and before message passing, so every node also sees the others' goals. A player without a goal gets zeros.
- **Not inputs:** player names, shirt numbers, match ids and outcomes.

## Data
- **Source:** Bassek, M., Rein, R., Weber, H. & Memmert, D. (2025). An integrated dataset of spatiotemporal and event data in elite soccer. Scientific Data 12, 195. https://doi.org/10.1038/s41597-025-04505-y. Data: https://doi.org/10.6084/m9.figshare.28196177 (v1).
- **Licence:** CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), published with the DFL's authorisation.
- **Matches:** 7, split by complete match before any window was cut.
  - Train: DFL-MAT-J03WMX, DFL-MAT-J03WN1, DFL-MAT-J03WPY, DFL-MAT-J03WQQ, DFL-MAT-J03WR9
  - Validation: DFL-MAT-J03WOH
  - Test: DFL-MAT-J03WOY
- **Preprocessing:**
  - 25 Hz resampled to 5 Hz;
  - pitch moved to a 105 × 68 m frame with the origin bottom-left;
  - rotated so that the team in possession attacks +x;
  - segments cut at stoppages (BallStatus 0), possession changes and roster changes;
  - only segments with exactly 11 tracked players per team and one tracked goalkeeper each are kept.
- **Feature scales:** fitted on the training matches only.
- **Windows:** {"train": {"one_step": 44688, "rollout_2s": 9657}, "val": {"one_step": 4828, "rollout_2s": 1614}, "test": {"one_step": 4970, "rollout_2s": 1652}}

## Training
- **Stage 1:** Laplace negative log-likelihood of the velocity change (location and scale).
- **Stage 2:** multi-step rollout training. The model runs 10 steps (2 s) on its own player outputs, with the
  observed ball as the context node, and the loss is the mean plus half the final position error. With it, 0.1 × the one-step Laplace negative log-likelihood keeps the scale calibrated.
- **Goals in training:** each player's real position 1–4 s after the last history step (stage 2: one still 1.2–3.2 s away at the rollout's last step), with the seconds left to it. 0.15 of the players get no goal, so the model also moves players without one. Validation and test use one fixed draw with every player given a goal (where enough later frames exist).
- **Optimiser:** AdamW, learning rate 0.001, weight decay 0.0001, batch 512. Stage 2
  uses 0.3 × the learning rate.
- **Regularisation:** a random left-right flip.
- **Early stopping:** on the validation 2 s rollout error, patience 3.
- **Seed and hardware:** seed 0, trained on cpu.
- **Elapsed:** 5280.6 s.

## Held-out test match (measured)
**One-step:** position error 0.0254 m and velocity-change error
0.1242 m/s over 4970 windows.

**Rollouts on the model's own outputs** (ball path observed; errors in metres):

| phase (ball zone) | windows | ADE 1 s | ADE 2 s | FDE 2 s |
|---|---|---|---|---|
| all | 1652 | 0.179 | 0.434 | 0.873 |
| buildup | 591 | 0.165 | 0.395 | 0.788 |
| middle | 805 | 0.181 | 0.437 | 0.875 |
| final | 256 | 0.202 | 0.512 | 1.066 |

**Sampled rollouts** (one draw per window, as in generation): ADE 2 s 0.619 m, FDE 2 s 1.23 m over 1652 windows. A single draw is not the most likely path, so it is expected to sit above the location's error.

**With goals vs without** (goals from what really happened, given to 90% of the players): one-step position error 0.0254 m vs 0.0298 m; 2 s rollout ADE 0.434 m vs 0.654 m. The phase table above is with goals. A goal is a strong hint about the future, so the errors with goals are not comparable with those of a model without goal input.

**Autonomous 15 s rollouts** from 126 real starting states. The model's own outputs
are fed back, and the observed ball path is supplied as the context node.

Mean displacement from what really happened: {"at_2s": 0.71, "at_5s": 0.68, "at_end": 0.71}. Non-finite values:
False.

Movement statistics, real versus model:

| statistic | real (test match) | model rollout (sampled) | location only | no goals |
|---|---|---|---|---|
| speed_mean | 2.36 | 2.28 | 2.19 | 1.78 |
| speed_p50 | 2.12 | 2.06 | 1.97 | 1.55 |
| speed_p95 | 4.89 | 4.72 | 4.56 | 3.96 |
| speed_max | 8.93 | 9.62 | 8.99 | 8.67 |
| acc_p50 | 1.06 | 1.16 | 0.67 | 1.08 |
| acc_p95 | 3.72 | 3.67 | 2.27 | 3.4 |
| share_below_0_5mps | 0.041 | 0.046 | 0.055 | 0.084 |
| width_in_possession | 55.6 | 55.7 | 55.7 | 47.3 |
| width_defending | 40.3 | 40.3 | 40.3 | 36.4 |
| depth_in_possession | 35.9 | 35.9 | 35.9 | 36.3 |
| depth_defending | 30.2 | 30.3 | 30.2 | 31.3 |
| defending_backline_to_midfield_m | 9.6 | 9.6 | 9.6 | 10.0 |
| teammate_pairs_within_2m_for_2s | 9/11340 | 7/11340 | 6/11340 | 132/11340 |

Goals in these rollouts: every 2 s: the real position 3 s later; none in the last 1 s. The "no goals" column is the same model with none; its displacement from real: {"at_2s": 2.12, "at_5s": 7.38, "at_end": 15.21}.

These are two separate checks: prediction accuracy over 1–2 s, and whether long rollouts keep plausible movement
statistics. No other architecture was trained, so no comparative claim is made.

## Intended use and limitations
- **Intended use:** off-ball movement inside PitchIQ's hybrid generator, producing synthetic demonstration clips
  for the defensive-analysis pipeline. It is not intended for analysing real players or matches.
- **Limitations:**
  - It samples each 0.2 s step independently given the history; there are no explicit behaviour modes. Compare the sampled and location-only speed statistics above.
  - It was trained on 5 matches from one season, from two German divisions.
  - The ball is not modelled, so the controller's ball plan is part of every generated clip.
  - Possession transitions are cut out of training, so transition movement comes from the controller.
  - With goals it follows wherever it is sent: the scenario controller is responsible for sensible targets. Goals in training came from real positions, and the controller's targets are its own, so how closely they match real intentions is not measured here.
- **Checkpoint:** `motion_gnn.pt`, sha256 `429509520dec9ae35673df1eef4a27516b3b1fa888814ae921baa96418f5dff3`.

## Reproduce
```bash
# data (2.6 GB, CC BY 4.0) from figshare 28196177 into <raw>, then on a compute node:
PYTHONPATH=backend python -m app.simulator.learned.idsse --raw <raw> --out <processed>
PYTHONPATH=backend python -m app.simulator.learned.train --processed <processed> --out <run> --stochastic --goal
PYTHONPATH=backend python -m app.simulator.learned.release --run <run> --processed <processed> --raw <raw> --out models/motion_gnn_v3
# generate a hybrid clip (needs torch): private config {"engine": "hybrid", ...} in app.simulator.defense.generate
```
