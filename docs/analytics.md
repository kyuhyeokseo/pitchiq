# Tactical intelligence: detection and significance (Phase 2)

Everything here consumes **public** tracking and events only. Live modules
(`analytics/`, `ingestion/`, `broadcast/`) cannot reference generator code or
private truth; `tests/test_privacy.py` enforces this with a source scan.

## Geometry (direction- and flank-invariant)
- `x_att = x` if the team attacks +x, else `105 - x`.
- `width = |y - 34|`. Larger means wider, i.e. "outside", on either flank.
- Pairs are same-side nominal roles: (RB, RW) and (LB, LW) per team. Roles come from the public lineup.
  - Known limitation: if a fullback and winger swap roles, the pair is missed.

## Rule detector (`analytics/patterns.py`)
The detector streams frames and reads only frames `<= k`. For each pair of the team in possession it tracks `d = x_att(FB) - x_att(W)` and `o = width(FB) - width(W)`.

| Step | Condition (development heuristic) |
|---|---|
| Arm | `d <= -3.0 m` (fullback behind the winger) |
| Cross | `d >= +1.0 m` for 3 consecutive frames (0.6 s), within 6 s of arming |
| Run check | progress >= 6 m from run start, peak speed >= 4.5 m/s, team possession >= 50% |
| Classify | mean `o` around the crossing: `>= +1 m` overlap, `<= -1 m` underlap, otherwise `uncertain` |

The detector latches until the fullback drops back behind the winger again, so a wobbling crossing gives one detection.

`confidence` is a **heuristic strength** (`confidence_kind = heuristic_score`), not a calibrated probability.

## Significance engine (`analytics/significance.py`)
Significance is evaluated a fixed **2.0 s after detection**, using frames and events up to that point only.

| Signal | Measurement | Weight |
|---|---|---|
| spacing | opposing fullback to nearest centre-back distance. Before = mean at run start. After = max from 1 s before crossing until evaluation. Saturates at +4 m. | 0.40 |
| lanes | new forward option lanes from the ball to the runner or to a teammate in the fullback/centre-back channel. A lane counts if it stays open >= 0.6 s while the ball is within 15 m of the winger, or if a forward pass is actually played into it. | 0.20 |
| ball relevance | mean ball distance to the pair during the run (1 at <= 8 m, 0 at >= 30 m) | 0.20 |
| coordination | fullback-winger distance at crossing (1 at <= 6 m, 0 at >= 20 m) | 0.10 |
| detector strength | heuristic strength above | 0.10 |

A detection is **suggested** only when all of these hold:
- the pattern is overlap or underlap
- there is an observable spatial gain: the gap widens by >= 2.5 m, or a new option lane appears
- score >= 0.55, detector strength >= 0.35 and ball relevance >= 0.3
- no cooldown applies (same team, flank and pattern within 45 s)
- the rate limit allows it (one suggestion per 20 s unless the score is >= 0.85)

Reason codes explain every outcome. Suppressed detections stay visible with their reasons.

Weights and thresholds are product heuristics, not statistically validated football value. The engine reports observed association ("the gap grew by X m"), never causation.

## Candidate insight (`broadcast/candidate.py`)
- A suggested detection becomes a `BroadcastInsight` with `production_status = suggested` and `source_mode = deterministic_fallback`.
- Text comes from templates: fan and analyst variants in English and Korean. Every number in the text comes from `numeric_facts`.
- Typed overlay elements: player highlights, the fullback's movement arrow, the defender-gap line and, if found, the new passing lane.
- `display_at` is the evaluation time and is never backdated.

## Measured results (synthetic only)
Reports are in `evaluation/reports/`. Reproduce them with `make eval`.

- **Dev set** `eval_s4242` (200 clips): used while designing the significance thresholds, so its numbers are optimistic.
- **Test set** `test_s9001` (200 clips): never used for tuning. Thresholds were frozen before it was first evaluated.

The numbers below come from generator **v0.2.0**. That version curves the runs and keeps receivers and wingers moving; see "Generator motion" below. Changing the generator regenerates the same seeds into different clips, so the test set was re-evaluated. Thresholds and weights were not changed. The v0.1.0 reports are kept in `evaluation/reports/generator_v0.1.0/`.

| Test set `test_s9001`, generator v0.2.0 | Result |
|---|---|
| Detection precision / recall / F1 | overlap 1.0 / 1.0 / 1.0 (n=75), underlap 1.0 / 1.0 / 1.0 (n=35), none 1.0 / 1.0 / 1.0 (n=90) |
| False alarms | 0 in 46.33 tracked minutes |
| Detection lag after the fullback first gets ahead | 0.4 s (median and p95) |
| Analysis step time | p50 0.031 ms, p95 0.044 ms per frame (login-node CPU) |
| Overlap suggested rate by generator defender response | follow 94% (gap Δ median +9.1 m), switch 40%, hold 4% (gap Δ median -2.1 m) |
| Underlap suggested rate | hold 38%, switch 36%, follow 0% (the defender tracks the runner inside, so the gap narrows) |

Under v0.1.0 the overlap suggested rates were follow 94%, switch 53% and hold 30%. Hold dropped mainly because the winger now drifts inside after passing. A marking fullback goes with him, which narrows the fullback-to-centre-back gap.

**Read these numbers honestly.**
- Perfect detection reflects a generator whose patterns are cleanly separated and whose geometry was designed alongside the rules. It says nothing about real tracking data.
- The clean follow/hold split is a property of the generator's defender heuristics. It is not evidence about how real defences behave.

## Generator motion (v0.2.0)
`evaluation/scripts/motion_realism.py` measures this on public tracking only. The test set results are below.

| Metric | v0.1.0 | v0.2.0 |
|---|---|---|
| Fullback run: largest distance from the straight chord, run start to detection (median) | 0.14 m | 1.74 m |
| Final-third receiver speed 1 s after receiving (median) | 1.0 m/s | 2.95 m/s |
| ...share that froze (< 1.5 m/s) | 92% | 8% |
| Winger speed 1 s after passing (median) | 0.6 m/s | 2.5 m/s |

What changed in the engine:
- Overlap and underlap runs follow a curved route, a quadratic Bezier followed by pure pursuit. Overlaps swing out to the touchline and then run down the line. Underlaps cut into the half-space and then run forward.
- The final receiver of a flank move in the final third drives at goal, slowing when a defender is right in front. Other ball owners move the ball on at walking pace instead of stopping.
- After passing, the winger drifts inside toward the box.
- A fullback who did not run past the winger (support or aborted runs) never drives past him later, so hard negatives stay negative.
