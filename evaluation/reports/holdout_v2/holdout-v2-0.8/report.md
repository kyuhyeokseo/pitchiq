# Holdout evaluation holdout-v2-0.8

Generated 2026-10-27T02:06:48Z on `hpc-compute-node`; generator `defense-0.6.0`; analytics config sha256 `2fe49bbcccbad120`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- analyzer 0.2: the backward limit for a short option is 8 m (0.7 used 3 m, which also dropped square passes between the centre-backs of a back three and removed D's lane-cut trap on the demo clip; found by a unit test on the demo clips, not by holdout results)

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 138
- Designed claim precision (lower bound): 226/237 (0.95) (target 0.9)
- Restricted recall: 138/144 (0.96); exposed recall: 86/108 (0.80)
- Counter-example clips with a wrong emphasis: 2 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 8/72 (0.11)
- Episodes outside the designed story: 6.7 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.8 ms, p95 9.4 ms over 11344 frames (single process, this host)
- Narration plans: 1218/1218 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 11 | 2.2 | 0/0 | 0/12 | 11/12 (0.92) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 9.73 |
| A:main | 12 | 12 | 9 | 2.2 | 9/12 | 0/0 | 9/12 (0.75) | 13/24 (0.54) | 22/22 (1.00) | 0 | 9/12 | 3 | 10.98 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 6.65 |
| B:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 23/24 (0.96) | 35/35 (1.00) | 0 | 11/12 | 0 | 12.16 |
| C:main | 12 | 12 | 12 | 1.4 | 12/12 | 0/0 | 12/12 (1.00) | 22/24 (0.92) | 34/34 (1.00) | 0 | 12/12 | 0 | 3.21 |
| C:support | 12 | 12 | 12 | 1.4 | 0/0 | 3/12 | 12/12 (1.00) | 0/0 | 12/15 (0.80) | 2 | 12/12 | 0 | 4.56 |
| D:escape | 12 | 12 | 10 | 5.1 | 8/12 | 0/0 | 10/12 (0.83) | 5/12 (0.42) | 15/21 (0.71) | 0 | 3/12 | 4 | 8.0 |
| D:main | 12 | 12 | 12 | 2.3 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 0 | 12/12 | 0 | 3.12 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 1.07 |
| E:step | 12 | 12 | 12 | 0.8 | 11/12 | 0/0 | 12/12 (1.00) | 11/12 (0.92) | 23/23 (1.00) | 0 | 11/12 | 1 | 5.2 |
| G:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 25/25 (1.00) | 0 | 12/12 | 0 | 4.67 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 13/13 (1.00) | 0 | 12/12 | 0 | 5.59 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:support: accessible_free_receiver 2, wide_outlet_available 1
- D:escape: behind_space_exposed 3, isolated_1v1 3
- D:main: accessible_free_receiver 2

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
