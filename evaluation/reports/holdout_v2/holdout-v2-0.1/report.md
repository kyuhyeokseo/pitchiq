# Holdout evaluation holdout-v2-0.1

Generated 2026-10-22T03:27:29Z on `hpc-login-node`; generator `defense-0.2.0`; analytics config sha256 `2da83f9366f08d6c`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 131
- Designed claim precision (lower bound): 230/230 (1.00) (target 0.9)
- Restricted recall: 131/144 (0.91); exposed recall: 85/108 (0.79)
- Counter-example clips with a wrong emphasis: 2 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 6/72 (0.08)
- Episodes outside the designed story: 5.9 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.3 ms, p95 10.4 ms over 12645 frames (single process, this host)
- Narration plans: 1178/1178 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 6.25 |
| A:main | 12 | 12 | 12 | 0.8 | 11/12 | 0/0 | 12/12 (1.00) | 17/24 (0.71) | 29/29 (1.00) | 0 | 11/12 | 1 | 10.33 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 0.0 |
| B:main | 12 | 12 | 12 | 0.8 | 11/12 | 0/0 | 12/12 (1.00) | 18/24 (0.75) | 30/30 (1.00) | 0 | 9/12 | 1 | 7.12 |
| C:main | 12 | 12 | 11 | 2.2 | 11/12 | 0/0 | 11/12 (0.92) | 17/24 (0.71) | 28/28 (1.00) | 0 | 11/12 | 1 | 4.94 |
| C:support | 12 | 12 | 0 | None | 0/0 | 0/12 | 0/12 (0.00) | 0/0 | 0/0 | 2 | 12/12 | 0 | 8.87 |
| D:escape | 12 | 12 | 12 | 3.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 11.33 |
| D:main | 12 | 12 | 12 | 2.2 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 4.59 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 3.89 |
| E:step | 12 | 12 | 12 | 0.8 | 9/12 | 0/0 | 12/12 (1.00) | 9/12 (0.75) | 21/21 (1.00) | 0 | 9/12 | 3 | 3.34 |
| G:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 31/31 (1.00) | 0 | 12/12 | 0 | 3.87 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 7/12 | 12/12 (1.00) | 0/0 | 19/19 (1.00) | 0 | 12/12 | 0 | 4.25 |

## Claims outside the design (counts over clips; may still be geometrically correct)


## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
