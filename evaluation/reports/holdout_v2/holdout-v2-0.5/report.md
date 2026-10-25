# Holdout evaluation holdout-v2-0.5

Generated 2026-10-22T23:25:48Z on `hpc-compute-node`; generator `defense-0.4.1`; analytics config sha256 `2da83f9366f08d6c`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.4.1: D's double pivot holds its height and slides across, so its markers stay in the build-up zone (the D trap's 4 v 5 count)
- frontend: overlapping episodes are stacked on separate timeline rows (one bar could cover another and could not be clicked)

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 142
- Designed claim precision (lower bound): 238/243 (0.98) (target 0.9)
- Restricted recall: 139/144 (0.97); exposed recall: 94/108 (0.87)
- Counter-example clips with a wrong emphasis: 3 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 4/72 (0.06)
- Episodes outside the designed story: 7.84 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.9 ms, p95 9.4 ms over 12699 frames (single process, this host)
- Narration plans: 1476/1476 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 7.08 |
| A:main | 12 | 12 | 12 | 0.8 | 11/12 | 0/0 | 12/12 (1.00) | 18/24 (0.75) | 30/30 (1.00) | 0 | 11/12 | 1 | 11.19 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 8.53 |
| B:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 24/24 (1.00) | 36/36 (1.00) | 0 | 12/12 | 0 | 14.27 |
| C:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 19/24 (0.79) | 31/31 (1.00) | 0 | 11/12 | 0 | 6.44 |
| C:support | 12 | 12 | 12 | 0.8 | 0/0 | 3/12 | 12/12 (1.00) | 0/0 | 12/15 (0.80) | 3 | 12/12 | 0 | 6.47 |
| D:escape | 12 | 12 | 12 | 3.6 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 11.12 |
| D:main | 12 | 12 | 12 | 2.2 | 0/0 | 0/12 | 9/12 (0.75) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 4.58 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/13 (0.92) | 0 | 12/12 | 0 | 5.6 |
| E:step | 12 | 12 | 12 | 0.8 | 10/12 | 0/0 | 12/12 (1.00) | 10/12 (0.83) | 22/23 (0.96) | 0 | 10/12 | 2 | 5.67 |
| G:main | 12 | 12 | 11 | 0.8 | 11/12 | 0/0 | 11/12 (0.92) | 11/12 (0.92) | 23/23 (1.00) | 0 | 11/12 | 1 | 4.16 |
| G:offside | 12 | 12 | 11 | 0.8 | 0/0 | 1/12 | 11/12 (0.92) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 4.63 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:support: accessible_free_receiver 3
- E:hold: behind_space_exposed 1
- E:step: behind_space_exposed 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
