# Holdout evaluation holdout-v2-0.6

Generated 2026-10-23T03:06:30Z on `hpc-compute-node`; generator `defense-0.5.0`; analytics config sha256 `2da83f9366f08d6c`.
All clips are synthetic. Seeds and reaction/spacing combinations are disjoint from the demo clips and from the seeds looked at while tuning. Deterministic narration only (no model calls).

## Changes from the previous version

- generator 0.5: a pass to a runner is never released offside (the trigger allows for the line stepping up), and a ball played into space rolls on until someone reaches it (no reception from 2 m away)
- generator 0.5: in C support the far winger steps into the lane to the far centre-back (left open, that square ball was the free man the support is meant to take away)
- generator 0.5: C's story players shift their feet where they wait and the carrier keeps the ball moving
- definition: D main's story accepts either restriction label (central routes screened or short options screened), as C has since 0.2; the analyzer names it by whichever it sees first on the carrier

## Overall

- Clips: 144 (144 played out as planned); designed story matched: 144
- Designed claim precision (lower bound): 241/242 (1.00) (target 0.9)
- Restricted recall: 144/144 (1.00); exposed recall: 95/108 (0.88)
- Counter-example clips with a wrong emphasis: 1 of 72 (target 0)
- Abstention (exposure expected, story stayed partial or unmatched): 1/72 (0.01)
- Episodes outside the designed story: 7.93 per clip-minute (upper bound on false alarms; includes real side stories such as goalkeeper phases)
- Analysis step wall time: p50 7.9 ms, p95 9.4 ms over 12669 frames (single process, this host)
- Narration plans: 1510/1510 valid; fallback ratio 1.0; model calls 0

## By family and variant

| variant | clips | played | matched | onset lag med (s) | complete when expected | complete when not | restricted recall | exposed recall | designed precision | wrong emphasis | outcome agrees | abstained | other episodes/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A:cover | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 7.29 |
| A:main | 12 | 12 | 12 | 0.8 | 11/12 | 0/0 | 12/12 (1.00) | 18/24 (0.75) | 30/30 (1.00) | 0 | 11/12 | 1 | 12.03 |
| B:compact | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 9.53 |
| B:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 24/24 (1.00) | 36/36 (1.00) | 0 | 12/12 | 0 | 14.8 |
| C:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 17/24 (0.71) | 29/29 (1.00) | 0 | 11/12 | 0 | 5.13 |
| C:support | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 1 | 12/12 | 0 | 5.13 |
| D:escape | 12 | 12 | 12 | 3.6 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 11.27 |
| D:main | 12 | 12 | 12 | 2.0 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 12/13 (0.92) | 0 | 12/12 | 0 | 4.64 |
| E:hold | 12 | 12 | 12 | 0.8 | 0/0 | 0/12 | 12/12 (1.00) | 0/0 | 12/12 (1.00) | 0 | 12/12 | 0 | 6.01 |
| E:step | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 24/24 (1.00) | 0 | 12/12 | 0 | 6.05 |
| G:main | 12 | 12 | 12 | 0.8 | 12/12 | 0/0 | 12/12 (1.00) | 12/12 (1.00) | 25/25 (1.00) | 0 | 12/12 | 0 | 3.8 |
| G:offside | 12 | 12 | 12 | 0.8 | 0/0 | 1/12 | 12/12 (1.00) | 0/0 | 13/13 (1.00) | 0 | 12/12 | 0 | 4.31 |

## Claims outside the design (counts over clips; may still be geometrically correct)

- D:main: accessible_free_receiver 1

## Definitions

- Story: the scenario's designed situation, from its start marker to the last ball action (private truth). Matching: same defending team, accepted analytics family, IoU >= 0.3, best single episode.
- Designed precision: claims found in the matched episode that the scenario was built to produce (or lists as acceptable) over all restricted/exposed claims found there.
- Wrong emphasis: a claim the counter-example was built to suppress, supported in any episode of the defending team that overlaps the story.
- Outcome agrees: the matched episode reports 'exploited in synthetic play' exactly when the designed exposure was used.
