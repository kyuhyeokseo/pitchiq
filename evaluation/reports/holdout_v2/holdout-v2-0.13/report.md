# Holdout evaluation holdout-v2-0.13

Generated 2026-10-27T06:29:39Z on `hpc-compute-node`; generator `defense-0.7.0`; analytics config sha256 `c37aefdb99b8a728`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- analyzer 0.3: the carrier's room ('nobody closes him down') is not reported while a defender is running at him (would be within the press gate within pressure.unpressed_horizon_s, 1 s)
- generator 0.7: G main's central midfielders screen the passes into their men on the trigger (zonal; the man-to-man middle is G offside's); G offside's striker cuts the pass back to the keeper (the full back already had his marker); C support's holding midfielder holds in front of the back line before he covers the striker (he dropped into the back line)

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 139
- Designed claim precision (lower bound): 219/225 (0.97) (target 0.9)
- Restricted recall: 139/144 (0.97); exposed recall: 78/108 (0.72)
- Counter-example clips with a wrong emphasis: 3 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 20/72 (0.28)
- Episodes outside the designed story: 4.98 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.8 ms, p95 9.5 ms over 11660 frames (single process, this host)
- Narration plans: 1051/1051 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 11 | 2.2 | 0/0 | 0/12 | 11/12 (0.92) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 7.58 |
| A:main | 12 | 12 | 11 | 2.2 | 10/12 | 0/0 | 11/12 (0.92) | 18/24 (0.75) | 29/29 (1.00) | 0 | 10/12 | 2 | 6.56 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 0.81 |
| B:main | 12 | 12 | 12 | 0.8 | 9/12 | 0/0 | 12/12 (1.00) | 18/24 (0.75) | 30/30 (1.00) | 0 | 9/12 | 3 | 10.23 |
| C:main | 12 | 12 | 11 | 1.4 | 10/12 | 0/0 | 11/12 (0.92) | 19/24 (0.79) | 30/30 (1.00) | 0 | 10/12 | 2 | 4.92 |
| C:support | 12 | 12 | 12 | 1.5 | 0/0 | 3/12 | 12/12 (1.00) | 0/0 | 12/15 (0.80) | 3 | 12/12 | 0 | 7.37 |
| D:escape | 12 | 12 | 12 | 4.5 | 6/12 | 0/0 | 12/12 (1.00) | 6/12 (0.50) | 18/18 (1.00) | 0 | 6/12 | 6 | 10.14 |
| D:main | 12 | 12 | 10 | 2.5 | 0/0 | 1/12 | 10/12 (0.83) | 0/0 | 10/11 (0.91) | 0 | 12/12 | 0 | 5.66 |
| E:hold | 12 | 12 | 12 | 0.9 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/13 (0.92) | 0 | 12/12 | 0 | 0.71 |
| E:step | 12 | 12 | 12 | 0.9 | 7/12 | 0/0 | 12/12 (1.00) | 7/12 (0.58) | 19/20 (0.95) | 0 | 7/12 | 5 | 0.73 |
| G:main | 12 | 12 | 12 | 0.8 | 10/12 | 0/0 | 12/12 (1.00) | 10/12 (0.83) | 23/23 (1.00) | 0 | 10/12 | 2 | 0.0 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 13/13 (1.00) | 0 | 11/12 | 0 | 5.08 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:support: accessible_free_receiver 3
- D:main: accessible_free_receiver 1
- E:hold: behind_space_exposed 1
- E:step: behind_space_exposed 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
