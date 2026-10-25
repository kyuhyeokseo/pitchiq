# Holdout evaluation holdout-v2-0.2

Generated 2026-10-22T04:36:01Z on `hpc-login-node`; generator `defense-0.2.0`; analytics config sha256 `2da83f9366f08d6c`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator C: lane screens are set on the centre-back before he receives (they waited for the ball before)
- evaluation C: the restriction counts under either label, central routes screened or short options screened

## Overall

- Clips: 144 (143 played out as planned); designed story matched: 142
- Designed claim precision (lower bound): 249/251 (0.99) (target 0.9)
- Restricted recall: 142/143 (0.99); exposed recall: 93/107 (0.87)
- Counter-example clips with a wrong emphasis: 2 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 2/71 (0.03)
- Episodes outside the designed story: 5.87 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.0 ms, p95 10.7 ms over 12656 frames (single process, this host)
- Narration plans: 1196/1196 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 11 | 0.8 | 0/0 | 0/12 | 11/12 (0.92) | 0/0 | 11/11 (1.00) | 0 | 12/12 | 0 | 6.67 |
| A:main | 12 | 12 | 12 | 0.8 | 10/12 | 0/0 | 12/12 (1.00) | 16/24 (0.67) | 28/28 (1.00) | 0 | 10/12 | 2 | 9.49 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 0.0 |
| B:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 21/24 (0.88) | 33/33 (1.00) | 0 | 11/12 | 0 | 6.61 |
| C:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 21/24 (0.88) | 33/33 (1.00) | 0 | 12/12 | 0 | 5.23 |
| C:support | 12 | 12 | 12 | 0.8 | 0/0 | 2/12 | 12/12 (1.00) | 0/0 | 12/14 (0.86) | 2 | 12/12 | 0 | 5.88 |
| D:escape | 12 | 12 | 12 | 3.6 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 10.87 |
| D:main | 12 | 12 | 12 | 2.3 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 4.64 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 5.51 |
| E:step | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 5.73 |
| G:main | 12 | 11 | 11 | 0.8 | 11/11 | 0/0 | 11/11 (1.00) | 11/11 (1.00) | 29/29 (1.00) | 0 | 11/11 | 0 | 3.83 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 7/12 | 12/12 (1.00) | 0/0 | 19/19 (1.00) | 0 | 12/12 | 0 | 4.29 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- C:support: accessible_free_receiver 2

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
