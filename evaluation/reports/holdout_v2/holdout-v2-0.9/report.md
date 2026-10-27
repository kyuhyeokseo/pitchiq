# Holdout evaluation holdout-v2-0.9

Generated 2026-10-27T02:19:41Z on `hpc-compute-node`; generator `defense-0.6.0`; analytics config sha256 `2fe49bbcccbad120`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.6: the keeper rule applies to the defending keeper only (the keeper of the team building up stands where the plan puts him, an option at the back); in 0.7/0.8 it also pulled him to 3 m off his line, lengthening the build-up passes
- generator 0.6: D's left centre-back starts where he did before 0.7 (a wider start lengthened the back pass, so the escape came after the free man's outcome window), and in D escape the keeper plays the free centre-back 0.8-1.1 s after receiving (was 1.2-1.5 s)

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 140
- Designed claim precision (lower bound): 228/236 (0.97) (target 0.9)
- Restricted recall: 140/144 (0.97); exposed recall: 84/108 (0.78)
- Counter-example clips with a wrong emphasis: 1 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 14/72 (0.19)
- Episodes outside the designed story: 7.11 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.8 ms, p95 9.4 ms over 11238 frames (single process, this host)
- Narration plans: 1220/1220 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 2.0 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 8.24 |
| A:main | 12 | 12 | 10 | 1.3 | 8/12 | 0/0 | 10/12 (0.83) | 14/24 (0.58) | 24/24 (1.00) | 0 | 8/12 | 4 | 9.96 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 6.93 |
| B:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 24/24 (1.00) | 36/36 (1.00) | 0 | 12/12 | 0 | 11.85 |
| C:main | 12 | 12 | 12 | 1.4 | 12/12 | 0/0 | 12/12 (1.00) | 20/24 (0.83) | 32/33 (0.97) | 0 | 11/12 | 0 | 4.97 |
| C:support | 12 | 12 | 12 | 1.4 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 1 | 12/12 | 0 | 6.69 |
| D:escape | 12 | 12 | 12 | 5.0 | 5/12 | 0/0 | 12/12 (1.00) | 5/12 (0.42) | 17/17 (1.00) | 0 | 5/12 | 7 | 10.8 |
| D:main | 12 | 12 | 10 | 2.4 | 0/0 | 1/12 | 10/12 (0.83) | 0/0 | 10/11 (0.91) | 0 | 12/12 | 0 | 5.4 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 0 | 12/12 | 0 | 1.42 |
| E:step | 12 | 12 | 12 | 0.8 | 9/12 | 0/0 | 12/12 (1.00) | 9/12 (0.75) | 21/23 (0.91) | 0 | 9/12 | 3 | 4.46 |
| G:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 26/26 (1.00) | 0 | 12/12 | 0 | 4.99 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 14/14 (1.00) | 0 | 12/12 | 0 | 5.95 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:main: behind_space_exposed 1
- C:support: accessible_free_receiver 1, behind_space_exposed 1
- D:main: accessible_free_receiver 1
- E:hold: behind_space_exposed 1, isolated_1v1 1
- E:step: behind_space_exposed 1, isolated_1v1 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
