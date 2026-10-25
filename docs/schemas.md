# PitchIQ data contract (schema v1.0)

All data is **synthetic**. Teams (Blue FC, Red United) and players are fictional.
The Pydantic models live in `backend/app/schemas/`. The examples below are real
excerpts from `data/fixtures/scenario_a`.

## Coordinates and time
- World pitch is 105 x 68 m. x=0 is the left goal line and x=105 the right one; y=0 is the bottom touchline and y=68 the top.
- `attack_direction[team]` is +1 when the team attacks toward x=105. It flips at half time (`period` 2).
- Attack-normalised progress: `x_att = x` when direction is +1, else `105 - x`.
- Width / "outside": distance from the centre line, `|y - 34|`. Larger means wider. This works on either flank and in either direction.
- For a team attacking +x, its right flank is the low-y side.
- Tracking is sampled at 5 Hz. `match_time_s` is the canonical, monotonic timebase. `clip_time_s = frame_index * 0.2`.
- Tracking exists only inside clip intervals of 12-25 s (61-125 frames). The manifest says so in `tracking_available`.

## Public files per scenario
```
data/fixtures/<scenario_id>/manifest.json            teams, roster, nominal formation, clip intervals, checksums
data/fixtures/<scenario_id>/events.jsonl             MatchEvent per line, sorted by match_time_s
data/fixtures/<scenario_id>/tracking/<clip>.jsonl.gz TrackingFrame per line
```

### TrackingFrame (22 players + ball, every frame)
```json
{
  "match_id": "synthetic_blue_red_01", "clip_id": "c02", "frame_index": 30,
  "match_time_s": 1934.0, "clip_time_s": 6.0, "period": 1,
  "attack_direction": {"blue": 1, "red": -1},
  "players": [
    {"player_id": "blue_02", "team_id": "blue", "nominal_role": "RB", "is_goalkeeper": false, "position": {"x_m": 60.96, "y_m": 8.12}, "velocity": {"x_m": 2.18, "y_m": -0.57}, "on_pitch": true},
    "... 21 more ..."
  ],
  "ball_position": {"x_m": 75.85, "y_m": 11.93},
  "ball_owner_id": "blue_07",
  "possession_team_id": "blue"
}
```
The schema validator rejects any frame that breaks one of these rules:
- exactly 22 unique players, 11 per team, 1 goalkeeper per team
- positions inside the pitch and finite
- the ball owner exists and belongs to `possession_team_id`
- opposite attack directions for the two teams
- no extra fields

`ball_owner_id` is `null` while a pass is in flight. Team possession continues during the flight.

Fields added beyond spec §4: `clip_time_s`, `period`, `attack_direction` and `on_pitch`. They are all observable match metadata.

### MatchEvent
```json
{"match_id": "synthetic_blue_red_01", "event_id": "c02_ev000", "clip_id": "c02", "match_time_s": 1931.4, "event_type": "carry", "team_id": "blue", "actor_id": "blue_08", "target_id": null, "start": {"x_m": 59.94, "y_m": 22.79}, "end": {"x_m": 66.06, "y_m": 22.79}, "success": true, "context": {"distance_m": 6.1, "start_match_s": 1928.0}}
```
- `pass` is stamped at release, with `success: null`: the outcome is not knowable then.
- A later `receive` event links back through `context.pass_event_id`.
- `carry` is stamped when it ends (its start is in `context.start_match_s`), so no event reveals future positions.
- `pressure` fires when an opponent comes within 2.5 m of the ball owner.
- The validator checks event/tracking agreement: the pass actor owns the ball at release, the receiver owns it at receive time, and no event falls outside its clip.

### Manifest clip entry
```json
{"clip_id": "c02", "period": 1, "start_match_s": 1928.0, "end_match_s": 1940.0, "n_frames": 61, "sample_rate_hz": 5.0, "attack_direction": {"blue": 1, "red": -1}, "tracking_file": "tracking/c02.jsonl.gz", "tracking_sha256": "..."}
```

## Prediction / insight models (consumed from Phase 2 on)
- `TacticalPrediction`: `confidence` plus `confidence_kind`, which is `heuristic_score` unless calibrated.
- `SignificanceResult`: metrics, per-signal `contributions`, `reason_codes` and `should_suggest`.
- `BroadcastInsight`:
  - The spec's `overlay: dict` is typed here as `OverlaySpec`: `sync_match_s`, `ttl_s` and `elements[]`.
  - `elements[]` is a discriminated union of `player_highlight`, `movement_arrow`, `space_zone` and `caption_card`.
  - The validator rejects `display_at_match_s < detected_at_match_s`, so late output cannot be backdated.

## Generator-private truth (OFFLINE ONLY)
`data/private_truth/<scenario_id>.jsonl` holds one record per clip:
- labels: `recipe_name`, `pattern_truth`, `generator_intent`, `defender_response`, `phase_truth`
- seeds, actor IDs and timeline markers
- every sampled parameter

Never serve these, never use them as features and never pass them to agents. `app/privacy.py` lists the forbidden keys. `tests/test_privacy.py` checks two things: public files contain none of these keys or recipe vocabulary, and live modules never reference them.
