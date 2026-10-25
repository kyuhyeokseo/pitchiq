# Holdout evaluation holdout-v2-0.3

Generated 2026-10-22T21:50:56Z on `hpc-compute-node`; generator `defense-0.3.0`; analytics config sha256 `2da83f9366f08d6c`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.3: off-ball slots follow the ball by slopes measured on real play (IDSSE), wander instead of orbiting on a fixed period, keep their spacing, and keep following the ball after a run or a long ball instead of stopping
- generator 0.3: scenario holds for players the stories rely on (pivots in C, E and G, the far centre-back in C, the central centre-back in D, the full backs in G, the dropping midfielder in C) and the G back line shuffles across in the offside variant

## Overall

- Clips: 144 (143 played out as planned); designed story matched: 142
- Designed claim precision (lower bound): 243/247 (0.98) (target 0.9)
- Restricted recall: 141/143 (0.99); exposed recall: 93/106 (0.88)
- Counter-example clips with a wrong emphasis: 4 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 4/71 (0.06)
- Episodes outside the designed story: 7.73 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.9 ms, p95 9.6 ms over 12701 frames (single process, this host)
- Narration plans: 1466/1466 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 6.67 |
| A:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 20/24 (0.83) | 32/32 (1.00) | 0 | 12/12 | 0 | 11.59 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 9.78 |
| B:main | 12 | 11 | 11 | 0.8 | 11/11 | 0/0 | 11/11 (1.00) | 22/22 (1.00) | 33/33 (1.00) | 0 | 11/11 | 0 | 15.59 |
| C:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 19/24 (0.79) | 31/31 (1.00) | 0 | 11/12 | 0 | 5.14 |
| C:support | 12 | 12 | 12 | 0.8 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 4 | 12/12 | 0 | 5.47 |
| D:escape | 12 | 12 | 12 | 3.6 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 10.71 |
| D:main | 12 | 12 | 11 | 2.4 | 0/0 | 0/12 | 10/12 (0.83) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 5.0 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/13 (0.92) | 0 | 12/12 | 0 | 5.01 |
| E:step | 12 | 12 | 12 | 0.8 | 8/12 | 0/0 | 12/12 (1.00) | 8/12 (0.67) | 20/21 (0.95) | 0 | 8/12 | 4 | 5.06 |
| G:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 28/28 (1.00) | 0 | 12/12 | 0 | 3.87 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 4/12 | 12/12 (1.00) | 0/0 | 16/16 (1.00) | 0 | 12/12 | 0 | 4.31 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:support: accessible_free_receiver 2
- E:hold: behind_space_exposed 1
- E:step: behind_space_exposed 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
