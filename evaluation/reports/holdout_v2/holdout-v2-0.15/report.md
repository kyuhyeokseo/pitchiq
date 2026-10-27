# Holdout evaluation holdout-v2-0.15

Generated 2026-10-27T22:11:44Z on `hpc-compute-node`; generator `defense-0.8.1`; analytics config sha256 `aedcdbe5737c1b3f`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.8.1: A cover's centre-backs keep their shape: the left one holds his zone at the line's height instead of marking the striker, and the spare leans toward the striker less (k 0.5, was 0.85)
- display (not scored): the breakdown's step 1 draws every marking pair plus loose goal-side pairs within 8 m; step 2 names who blocks which pass; up to six unmarked players

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 143
- Designed claim precision (lower bound): 227/234 (0.97) (target 0.9)
- Restricted recall: 143/144 (0.99); exposed recall: 84/108 (0.78)
- Counter-example clips with a wrong emphasis: 3 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 11/72 (0.15)
- Episodes outside the designed story: 5.2 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.8 ms, p95 9.4 ms over 11680 frames (single process, this host)
- Narration plans: 1006/1006 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 7.94 |
| A:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 21/24 (0.88) | 33/33 (1.00) | 0 | 12/12 | 0 | 7.03 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 0.81 |
| B:main | 12 | 12 | 12 | 0.8 | 9/12 | 0/0 | 12/12 (1.00) | 18/24 (0.75) | 30/30 (1.00) | 0 | 9/12 | 3 | 9.11 |
| C:main | 12 | 12 | 12 | 0.8 | 11/12 | 0/0 | 12/12 (1.00) | 16/24 (0.67) | 28/29 (0.97) | 0 | 7/12 | 1 | 5.28 |
| C:support | 12 | 12 | 12 | 0.8 | 0/0 | 4/12 | 12/12 (1.00) | 0/0 | 12/16 (0.75) | 3 | 12/12 | 0 | 6.62 |
| D:escape | 12 | 12 | 12 | 3.7 | 8/12 | 0/0 | 12/12 (1.00) | 8/12 (0.67) | 20/20 (1.00) | 0 | 8/12 | 4 | 13.18 |
| D:main | 12 | 12 | 11 | 2.0 | 0/0 | 2/12 | 11/12 (0.92) | 0/0 | 11/13 (0.85) | 0 | 12/12 | 0 | 5.5 |
| E:hold | 12 | 12 | 12 | 0.9 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 1.06 |
| E:step | 12 | 12 | 12 | 0.9 | 9/12 | 0/0 | 12/12 (1.00) | 9/12 (0.75) | 21/21 (1.00) | 0 | 9/12 | 3 | 0.73 |
| G:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 0.0 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 5.43 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:main: behind_space_exposed 1
- C:support: accessible_free_receiver 3, behind_space_exposed 1
- D:main: accessible_free_receiver 1, behind_space_exposed 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
