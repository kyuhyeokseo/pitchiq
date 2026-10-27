# Holdout evaluation holdout-v2-0.12

Generated 2026-10-27T06:04:06Z on `hpc-compute-node`; generator `defense-0.7.0`; analytics config sha256 `0315482c3a0972f3`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.7: A cover's far full back marks the left winger (with the second centre-back spare, the winger was left alone) and leaves the far full back free and deep
- generator 0.7: B compact keeps its 4-3-3: the two eights take the opposing eights from in front of the back four, the holding midfielder stands in front of the striker between the centre-backs (he used to jump out at the ten); the ball goes round the back unpressed instead of into the ten
- generator 0.7: in C the striker cuts the pass to the other centre-back as he presses (was the pass into the pivot), the far centre-back stands within a short pass, and the ten drops into the half-space apart from him; C support marks the pivot man to man, the holding midfielder follows the ten's drop, and the far winger tucks into the middle instead of screening the far centre-back
- generator 0.7: G main sends the right winger in behind as well (the left back turns with him); G offside goes three on three in central midfield, tight, from the start, the striker presses from outside and the ball goes to the free far centre-back instead of back to the keeper (the striker then shifts onto him)
- generator 0.7 (after an AI football-analyst review of the 0.7 draft clips): C support has one man each in midfield (the tucked-in winger takes the dropping ten, the holding midfielder is the spare in front of the back line) and presses the keeper on the back pass; E step's free man turns and runs at the back four; B compact's ten keeps moving
- analyzer 0.3: no 'space behind taken away' (behind_depth_reduced) while the ball is behind the back line: a line chasing a ball in behind is recovering (claims.recovery_margin_m 3 m)

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 141
- Designed claim precision (lower bound): 225/231 (0.97) (target 0.9)
- Restricted recall: 141/144 (0.98); exposed recall: 82/108 (0.76)
- Counter-example clips with a wrong emphasis: 3 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 15/72 (0.21)
- Episodes outside the designed story: 4.99 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.7 ms, p95 9.3 ms over 11617 frames (single process, this host)
- Narration plans: 1036/1036 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 2.0 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 7.59 |
| A:main | 12 | 12 | 12 | 2.0 | 11/12 | 0/0 | 12/12 (1.00) | 18/24 (0.75) | 30/30 (1.00) | 0 | 11/12 | 1 | 6.75 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 0.82 |
| B:main | 12 | 12 | 12 | 0.8 | 10/12 | 0/0 | 12/12 (1.00) | 18/24 (0.75) | 30/30 (1.00) | 0 | 8/12 | 2 | 9.41 |
| C:main | 12 | 12 | 12 | 1.4 | 11/12 | 0/0 | 12/12 (1.00) | 21/24 (0.88) | 33/34 (0.97) | 0 | 11/12 | 1 | 5.98 |
| C:support | 12 | 12 | 12 | 1.1 | 0/0 | 3/12 | 12/12 (1.00) | 0/0 | 12/15 (0.80) | 3 | 12/12 | 0 | 8.05 |
| D:escape | 12 | 12 | 11 | 5.0 | 5/12 | 0/0 | 11/12 (0.92) | 5/12 (0.42) | 16/16 (1.00) | 0 | 5/12 | 7 | 10.5 |
| D:main | 12 | 12 | 10 | 1.9 | 0/0 | 2/12 | 10/12 (0.83) | 0/0 | 10/12 (0.83) | 0 | 12/12 | 0 | 4.62 |
| E:hold | 12 | 12 | 12 | 0.9 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 0.71 |
| E:step | 12 | 12 | 12 | 0.9 | 11/12 | 0/0 | 12/12 (1.00) | 11/12 (0.92) | 23/23 (1.00) | 0 | 11/12 | 1 | 0.37 |
| G:main | 12 | 12 | 12 | 0.8 | 9/12 | 0/0 | 12/12 (1.00) | 9/12 (0.75) | 22/22 (1.00) | 0 | 9/12 | 3 | 0.0 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 13/13 (1.00) | 0 | 11/12 | 0 | 5.07 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:main: behind_space_exposed 1
- C:support: accessible_free_receiver 3
- D:main: accessible_free_receiver 2

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
