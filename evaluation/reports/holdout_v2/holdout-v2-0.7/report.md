# Holdout evaluation holdout-v2-0.7

Generated 2026-10-27T01:43:33Z on `hpc-compute-node`; generator `defense-0.6.0`; analytics config sha256 `14c6a1b2bc959692`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.6: keepers stay within about 7 m of their line (8 m for a ball in behind); a clip ends 1.6 s after the defending team wins the ball, 3.5 s after the last planned action otherwise
- generator 0.6: the defending team keeps a leash to its formation spots (moved with the ball) and its spacing, the back line holds the edge of its box unless the ball or its man is deeper, the nearest defender engages a free carrier in a dangerous spot (goal-distance danger), a screen skips a man a team-mate already has, markers stand at least 2.4 m off their man
- generator 0.6: linking passes wait (up to 2 s) for a likely lane; pressers mark goal-side until their man has the ball; B's holding midfielder follows the pivot; C's screens stand apart and pick up their men once the ball goes wide, and in C support the centre-back goes back to the keeper; E hold switches over the screen to the far full back; G's near centre-back chases the run and the striker shoots
- analyzer 0.2: a pass more than 3 m back toward the carrier's own goal is not a short option (screened or open)

## Overall

- Clips: 144 (143 played out as planned); designed story matched: 128
- Designed claim precision (lower bound): 193/207 (0.93) (target 0.9)
- Restricted recall: 128/143 (0.90); exposed recall: 65/106 (0.61)
- Counter-example clips with a wrong emphasis: 2 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 14/71 (0.20)
- Episodes outside the designed story: 5.55 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.6 ms, p95 9.2 ms over 11361 frames (single process, this host)
- Narration plans: 1060/1060 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 1.9 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 10.06 |
| A:main | 12 | 12 | 8 | 0.8 | 7/12 | 0/0 | 8/12 (0.67) | 11/24 (0.46) | 19/19 (1.00) | 0 | 7/12 | 5 | 11.44 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 5.7 |
| B:main | 12 | 11 | 11 | 0.8 | 8/11 | 0/0 | 11/11 (1.00) | 8/22 (0.36) | 19/19 (1.00) | 0 | 8/11 | 3 | 9.39 |
| C:main | 12 | 12 | 12 | 1.5 | 12/12 | 0/0 | 12/12 (1.00) | 19/24 (0.79) | 31/31 (1.00) | 0 | 12/12 | 0 | 0.7 |
| C:support | 12 | 12 | 12 | 1.4 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 2 | 12/12 | 0 | 4.18 |
| D:escape | 12 | 12 | 10 | 4.0 | 9/12 | 0/0 | 10/12 (0.83) | 6/12 (0.50) | 16/21 (0.76) | 0 | 4/12 | 3 | 6.62 |
| D:main | 12 | 12 | 5 | -3.0 | 0/0 | 5/12 | 5/12 (0.42) | 0/0 | 5/10 (0.50) | 0 | 12/12 | 0 | 0.89 |
| E:hold | 12 | 12 | 12 | 1.0 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/13 (0.92) | 0 | 12/12 | 0 | 1.05 |
| E:step | 12 | 12 | 12 | 1.0 | 10/12 | 0/0 | 12/12 (1.00) | 10/12 (0.83) | 22/23 (0.96) | 0 | 10/12 | 2 | 1.09 |
| G:main | 12 | 12 | 11 | 0.8 | 11/12 | 0/0 | 11/12 (0.92) | 11/12 (0.92) | 22/22 (1.00) | 0 | 11/12 | 1 | 4.35 |
| G:offside | 12 | 12 | 11 | 0.8 | 0/0 | 0/12 | 11/12 (0.92) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 5.16 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:support: accessible_free_receiver 2
- D:escape: behind_space_exposed 2, isolated_1v1 2, wide_outlet_available 1
- D:main: accessible_free_receiver 5
- E:hold: behind_space_exposed 1
- E:step: behind_space_exposed 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
