# Holdout evaluation holdout-v2-0.10

Generated 2026-10-27T02:24:27Z on `hpc-compute-node`; generator `defense-0.6.0`; analytics config sha256 `2fe49bbcccbad120`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.6: in A the striker curves in from outside to cut the pass to the full back, showing the centre-back inside onto the markers (cutting the pass into the pivot made the press read as a central screen, so the space behind could not join its story); a man-marker's leash is 18 m (was 12 m)

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 137
- Designed claim precision (lower bound): 221/234 (0.94) (target 0.9)
- Restricted recall: 137/144 (0.95); exposed recall: 82/108 (0.76)
- Counter-example clips with a wrong emphasis: 3 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 15/72 (0.21)
- Episodes outside the designed story: 6.93 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.8 ms, p95 9.3 ms over 11349 frames (single process, this host)
- Narration plans: 1244/1244 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 1.9 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 7.27 |
| A:main | 12 | 12 | 11 | 1.8 | 10/12 | 0/0 | 11/12 (0.92) | 15/24 (0.62) | 26/26 (1.00) | 0 | 10/12 | 2 | 8.97 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 7.08 |
| B:main | 12 | 12 | 12 | 0.8 | 10/12 | 0/0 | 12/12 (1.00) | 20/24 (0.83) | 32/32 (1.00) | 0 | 10/12 | 2 | 11.53 |
| C:main | 12 | 12 | 12 | 1.4 | 12/12 | 0/0 | 12/12 (1.00) | 24/24 (1.00) | 36/37 (0.97) | 0 | 12/12 | 0 | 5.29 |
| C:support | 12 | 12 | 12 | 1.4 | 0/0 | 4/12 | 12/12 (1.00) | 0/0 | 12/16 (0.75) | 3 | 12/12 | 0 | 6.66 |
| D:escape | 12 | 12 | 10 | 3.8 | 8/12 | 0/0 | 10/12 (0.83) | 7/12 (0.58) | 17/19 (0.89) | 0 | 7/12 | 4 | 11.49 |
| D:main | 12 | 12 | 12 | 2.0 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 0 | 12/12 | 0 | 5.19 |
| E:hold | 12 | 12 | 12 | 0.9 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 0 | 12/12 | 0 | 0.7 |
| E:step | 12 | 12 | 12 | 0.9 | 7/12 | 0/0 | 12/12 (1.00) | 6/12 (0.50) | 18/20 (0.90) | 0 | 6/12 | 5 | 3.67 |
| G:main | 12 | 12 | 10 | 0.8 | 10/12 | 0/0 | 10/12 (0.83) | 10/12 (0.83) | 21/21 (1.00) | 0 | 10/12 | 2 | 5.64 |
| G:offside | 12 | 12 | 10 | 0.8 | 0/0 | 1/12 | 10/12 (0.83) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 6.71 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:main: behind_space_exposed 1
- C:support: accessible_free_receiver 3, behind_space_exposed 1
- D:escape: behind_space_exposed 1, isolated_1v1 1
- D:main: accessible_free_receiver 2
- E:hold: behind_space_exposed 1, isolated_1v1 1
- E:step: behind_space_exposed 1, isolated_1v1 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
