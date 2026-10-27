# Holdout evaluation holdout-v2-0.11

Generated 2026-10-27T02:59:41Z on `hpc-compute-node`; generator `defense-0.6.0`; analytics config sha256 `eb973e7d7f0a069e`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.6: a defender the ball has gone past by 10 m (was 15 m) gets back goal-side of it; the nearest defender engages a free carrier from about 32 m out (danger 0.15, was 0.20); markers stand at least 3.0 m off their man (was 2.4 m); a presser does not cut the pass to a man a team-mate already has; B compact's midfield starts about 10 m in front of its back four (was level with it); G's far centre-back sprints back 26 m toward his box when the striker is through (was 16 m)
- analyzer 0.2: the backward limit for a short option is 6 m (was 8 m: diagonal passes back to a centre-back 7-8 m behind still counted)

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 139
- Designed claim precision (lower bound): 224/230 (0.97) (target 0.9)
- Restricted recall: 139/144 (0.97); exposed recall: 85/108 (0.79)
- Counter-example clips with a wrong emphasis: 1 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 11/72 (0.15)
- Episodes outside the designed story: 6.96 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.8 ms, p95 9.3 ms over 11265 frames (single process, this host)
- Narration plans: 1207/1207 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 11 | 1.8 | 0/0 | 0/12 | 11/12 (0.92) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 8.43 |
| A:main | 12 | 12 | 11 | 1.8 | 11/12 | 0/0 | 11/12 (0.92) | 19/24 (0.79) | 30/30 (1.00) | 0 | 11/12 | 1 | 10.16 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 8.78 |
| B:main | 12 | 12 | 12 | 0.8 | 10/12 | 0/0 | 12/12 (1.00) | 19/24 (0.79) | 31/31 (1.00) | 0 | 9/12 | 2 | 10.45 |
| C:main | 12 | 12 | 12 | 1.4 | 12/12 | 0/0 | 12/12 (1.00) | 22/24 (0.92) | 34/34 (1.00) | 0 | 12/12 | 0 | 5.68 |
| C:support | 12 | 12 | 12 | 1.4 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 1 | 12/12 | 0 | 6.32 |
| D:escape | 12 | 12 | 11 | 4.0 | 10/12 | 0/0 | 11/12 (0.92) | 7/12 (0.58) | 18/23 (0.78) | 0 | 7/12 | 2 | 11.24 |
| D:main | 12 | 12 | 12 | 1.7 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/13 (0.92) | 0 | 12/12 | 0 | 5.39 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 0.0 |
| E:step | 12 | 12 | 12 | 0.8 | 9/12 | 0/0 | 12/12 (1.00) | 9/12 (0.75) | 21/21 (1.00) | 0 | 9/12 | 3 | 3.33 |
| G:main | 12 | 12 | 11 | 0.8 | 9/12 | 0/0 | 11/12 (0.92) | 9/12 (0.75) | 20/20 (1.00) | 0 | 9/12 | 3 | 4.31 |
| G:offside | 12 | 12 | 11 | 0.8 | 0/0 | 0/12 | 11/12 (0.92) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 5.16 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- D:escape: behind_space_exposed 3, isolated_1v1 2
- D:main: accessible_free_receiver 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
