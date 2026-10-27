# Holdout evaluation holdout-v2-0.14

Generated 2026-10-27T20:39:17Z on `hpc-compute-node`; generator `defense-0.8.0`; analytics config sha256 `aedcdbe5737c1b3f`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.8: a presser stands 3 m off the man on the ball (was 1.0-1.8 m, the two players drawn on top of each other), on the line of the pass he cuts; a plan's longer stand-off keeps its extra distance; a defender engaging a free carrier near goal stands 3 m goal-side of him (was 1.3 m)
- analyzer 0.4: pressure.contact_distance_m 3.5 (was 2.0), so a press holds while the presser stands off the carrier; a story already open on the carrier, even before its claims are supported, keeps its family when a defender steps onto him (a marker 3 m off now counts as a presser a moment earlier)
- generator 0.8: E step's midfielder steps out in front of the carrier on his outside, away from the pass to the man behind him (3 m off, straight in front, he still reached that pass)
- display (not scored): an unmarked player has no opponent within 10 m (was 8 m)

## Overall

- Clips: 144 (142 played out as planned); designed story matched: 140
- Designed claim precision (lower bound): 229/238 (0.96) (target 0.9)
- Restricted recall: 140/142 (0.99); exposed recall: 82/106 (0.77)
- Counter-example clips with a wrong emphasis: 4 of 71 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 8/71 (0.11)
- Episodes outside the designed story: 5.1 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.7 ms, p95 9.3 ms over 11501 frames (single process, this host)
- Narration plans: 1030/1030 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 11 | 11 | 0.8 | 0/0 | 1/11 | 11/11 (1.00) | 0/0 | 12/13 (0.92) | 1 | 11/11 | 0 | 7.02 |
| A:main | 12 | 11 | 11 | 0.8 | 11/11 | 0/0 | 11/11 (1.00) | 20/22 (0.91) | 31/31 (1.00) | 0 | 10/11 | 0 | 6.07 |
| B:compact | 12 | 12 | 11 | 0.8 | 0/0 | 0/12 | 11/12 (0.92) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 1.21 |
| B:main | 12 | 12 | 12 | 0.8 | 10/12 | 0/0 | 12/12 (1.00) | 19/24 (0.79) | 31/31 (1.00) | 0 | 10/12 | 2 | 11.53 |
| C:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 17/24 (0.71) | 29/29 (1.00) | 0 | 4/12 | 0 | 5.63 |
| C:support | 12 | 12 | 12 | 0.8 | 0/0 | 3/12 | 12/12 (1.00) | 0/0 | 12/15 (0.80) | 3 | 12/12 | 0 | 6.65 |
| D:escape | 12 | 12 | 11 | 4.8 | 8/12 | 0/0 | 11/12 (0.92) | 5/12 (0.42) | 16/20 (0.80) | 0 | 4/12 | 4 | 10.84 |
| D:main | 12 | 12 | 12 | 2.4 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/13 (0.92) | 0 | 12/12 | 0 | 5.89 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 0.71 |
| E:step | 12 | 12 | 12 | 0.8 | 10/12 | 0/0 | 12/12 (1.00) | 10/12 (0.83) | 22/22 (1.00) | 0 | 10/12 | 2 | 0.37 |
| G:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 11/12 (0.92) | 26/26 (1.00) | 0 | 11/12 | 0 | 0.0 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 3/12 | 12/12 (1.00) | 0/0 | 15/15 (1.00) | 0 | 9/12 | 0 | 5.49 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- A:cover: behind_space_exposed 1
- C:support: accessible_free_receiver 3
- D:escape: behind_space_exposed 1, isolated_1v1 2, wide_outlet_available 1
- D:main: accessible_free_receiver 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
