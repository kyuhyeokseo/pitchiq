# Holdout evaluation holdout-v2-0.4

Generated 2026-10-22T23:06:20Z on `hpc-compute-node`; generator `defense-0.4.0`; analytics config sha256 `2da83f9366f08d6c`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.4: in A, the midfield, the ball-far wide midfielder and the far full back (inside only, at the line's height) drop off a man far from the ball (loose marking, slopes from IDSSE); the striker's marker and the second centre-back stay tight
- generator 0.4: C's pivot slides across, C's midfield screen is released once the ball goes wide, E's free man waits at his height before moving into the space
- analyzer: an outcome revision after a turnover no longer pulls in the other team's state (it used to stop the analysis)

## Overall

- Clips: 144 (143 played out as planned); designed story matched: 143
- Designed claim precision (lower bound): 241/247 (0.98) (target 0.9)
- Restricted recall: 142/143 (0.99); exposed recall: 98/106 (0.92)
- Counter-example clips with a wrong emphasis: 1 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 0/71 (0.00)
- Episodes outside the designed story: 7.77 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 8.0 ms, p95 9.5 ms over 12789 frames (single process, this host)
- Narration plans: 1520/1520 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 6.67 |
| A:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 19/24 (0.79) | 31/31 (1.00) | 0 | 12/12 | 0 | 11.58 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 9.2 |
| B:main | 12 | 11 | 11 | 0.8 | 11/11 | 0/0 | 11/11 (1.00) | 22/22 (1.00) | 33/33 (1.00) | 0 | 11/11 | 0 | 14.36 |
| C:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 22/24 (0.92) | 34/34 (1.00) | 0 | 12/12 | 0 | 6.1 |
| C:support | 12 | 12 | 12 | 0.8 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 1 | 12/12 | 0 | 6.38 |
| D:escape | 12 | 12 | 12 | 3.7 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 10.67 |
| D:main | 12 | 12 | 12 | 2.4 | 0/0 | 0/12 | 11/12 (0.92) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 4.94 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 0 | 12/12 | 0 | 5.24 |
| E:step | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 11/12 (0.92) | 23/25 (0.92) | 0 | 11/12 | 0 | 5.32 |
| G:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 4.16 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 4.63 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:support: accessible_free_receiver 1, behind_space_exposed 1
- E:hold: behind_space_exposed 2
- E:step: behind_space_exposed 2

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
