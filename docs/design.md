# PitchIQ design summary

This is an English summary of the internal implementation spec, `PitchIQ_Defensive_Intelligence_Implementation_Spec_v2.md` (v2.0, dated 2026-10-21, written in Korean). That spec is the design baseline for this repository.

The spec's numbers are starting defaults for a synthetic demo, not validated football thresholds. The values in use are in `backend/app/analytics/analytics_config.yaml`. Where the build differs from the spec, [As built](#as-built-2026-10-25) says so.

## 1. Product

PitchIQ estimates how a defence presses, marks, covers and holds its lines, using synthetic tracking and event streams. It explains, side by side, which attacking options that structure **restricts** and which space it **exposes**. It then follows how the picture changes after the opponent responds. The output is pitch overlays linked to their evidence, plus broadcast narration.

The central question: what does the defence close, what does it give away, and what changed after the response?

**Users:**
- **Broadcast and streaming producers, and the people who explain tactics on air.** Analysts see the evidence and the numbers. Fans see a short explanation of the same moment.
- A producer picks a suggested moment during replay and checks restricted options, exposed space and the outcome observed so far. They click any element to see its players, frames and values, then approve it for the broadcast surface.
- Decision support for coaches is a possible long-term use, not a goal.

## 2. Design principles

1. **Four kinds of statement, never mixed:**
   - a *computed observation*, such as a back-line depth of 45 m, linked to its unit, frames and formula;
   - a *heuristic inference*, such as a man-oriented relation, marked `heuristic_inference` with its support;
   - a *structural exposure*, such as space behind or late cover: whether it exists and how large it is, never turned into a goal probability;
   - a *realized outcome*, an event observed later and recorded separately.
2. **No intent.** Tracking cannot show what a coach meant.
   - Wording says "central options are restricted and the left option is relatively open", not "they forced play left".
   - The contracts use `likely_outlet_ids`, never `forced_target`. The actual receiver is recorded separately as `realized_target_id`.
3. **Forbidden automatic conclusions:**
   - Man-marking does not mean everyone is isolated 1v1; cover, including the goalkeeper, is computed.
   - A high line is not dangerous by itself.
   - A deep line does not always open the space between the lines.
   - Formation names (4-4-2 against 4-3-3) never produce a midfield count; the players in a region are counted.
   - An unmarked player is not a free man until lane access and reception are checked.
   - Open space is not a completed pass, a successful press is not a good policy, and a sequence of events is not causation.
4. **Scores are heuristics.**
   - `support_score` and `story_priority` lie in [0, 1]. They are shown as "heuristic evidence strength", never as a percentage.
   - There is no single good/bad score for a defence. Restricted and exposed are separate axes.
5. **Causality fence.**
   - Frame t is analysed from `[t - W, t]` only.
   - An event enters the analysis only when both `event_time_s` and `available_at_s` are at or before the cursor.
   - Centred smoothing, future frames, whole-clip normalisation and later pass results are forbidden.
   - A pass-release event never names its future receiver.
6. **Public observations, private truth.**
   - The generator's recipe, seed, intent, true phase, behaviour parameters and expected changes stay private.
   - Analytics never import them, and the serving image leaves them out.
   - Clip ids are opaque (`clip_001`), and the serving catalogue holds no tactical answers. Test and evaluation material (`evaluation/`, the browser walkthrough) does name the set-up behind each clip, because it checks them.
   - Only the evaluation runner combines observations with truth.
7. **Code computes, models choose.**
   - Positions, counts, lane access, cover and every displayed number come from code.
   - Language models only choose among validated claims and templates.
   - Invalid output falls back to a deterministic plan.
8. **The past is not rewritten.**
   - An outcome arrives as a new revision.
   - An earlier exposure is not deleted because the attack did not use it, and it is not upgraded to a "prediction" because the attack did.

## 3. Architecture

Synthetic generator → public observations (truth goes only to the offline evaluator) → validation and replay cursor → phase, defensive state and spatial relations → trade-off episodes and evidence store → two branches that meet again:

- a deterministic overlay builder;
- Foundry Analyst → Producer → code validation, with fallback.

Both feed the producer preview and approval → pitch, broadcast view and personalisation.

**Deployment shape:**
- One repository and one container.
- FastAPI serves the API and the built React/TypeScript/SVG frontend.
- Sessions live in memory, with one replica and one worker.

**Timing:**
- Analysis runs at 5 Hz and story selection at 1 Hz. Browser interpolation runs separately: while the replay plays, the pitch glides from the frame it shows to each new one over one frame interval (one frame behind), and a seek, a jump or a still frame is drawn as it is. The WebSocket loop checks for new frames every 0.05 s.
- The model is called only for selected episodes. The pitch and the evidence never wait for it.

## 4. Data contracts (schema 2.0)

### Coordinates and inputs

- **Coordinates:**
  - The world pitch is 105 × 68 m with its origin at the bottom left.
  - For the team in possession with attack direction a: when a = +1, u = x and v = y; when a = -1, u = 105 - x and v = 68 - y.
  - The defending goal is at u = 105, and own-goal depth is d = 105 - u.
  - World values are stored; normalised values are used only in computation.
- **Inputs:**
  - `MatchMeta`: teams with their attack direction; a roster of id, team, jersey number and goalkeeper flag only (no roles or formations); clips with checksums.
  - `ObservationFrame`: 5 Hz; 22 players with position and velocity; the ball with height and owner; the team in possession; the ball state.
  - `MatchEvent`: kind, `event_time_s`, `available_at_s`, actor, and a target only once it has been observed.
- **Invariants:**
  - 22 players, 11 per team and one goalkeeper each; unique ids; finite values; time that only moves forward; valid references; an owner that matches the team in possession.
  - Development caps of 9 m/s, 5 m/s² and a 35 m/s ball, which are not real-world rules.
  - One period per clip.

### Evidence

- `EvidenceMetric`: an allowlisted key; a value, or null with a status; unit, window, player ids, frame refs, event ids and method version.
- `TacticalClaim`: a code; an epistemic class; a dimension (`restricted`, `exposed` or `context`); supporting metric, relation and player ids; a support score; a window.
- `TacticalChange`: `attacker_drop`, `defender_step`, `line_retreat`, `block_shift`, `mark_switch`, `lane_change` or `count_change`, with the relation `observed_next` or `consistent_with_response`.
- `TradeoffEpisode`: lifecycle, analysis status, claims, changes, outcome and priority.
- `EvidencePacket`: the episode with its states, metrics, claims, changes and events, plus `forbidden_inferences`. Its `evidence_hash` is a sha256 hash of canonical JSON, leaving out the hash field itself.

### Overlay and narrative

- `OverlaySnapshot` carries:
  - frame ref, valid-until time, epoch, episode revision and evidence hash;
  - display mode (`live` or `retrospective_replay`), surface (`inspector_preview` or `broadcast`), audience and language.
- Its elements sit on ten layers: `structure`, `marking`, `pressure`, `screened_lanes`, `outlets`, `exposure`, `cover`, `numbers`, `changes` and `caption`.
- Each element has a player or world anchor and one geometry: ring, connection, arrow (observed or inferred), polygon, polyline, or label (a template key plus metric ids).
- Styling uses an allowlisted style token. Raw CSS, SVG or HTML is never allowed.
- `AnalystPlan` holds ids only: focus, restricted, exposed, ordered changes, omitted claims and caveat codes.
- `SentencePlan` is a template key plus claim, change, metric and event ids.
- `ProducerPlan` has exactly four variants: fan/analyst × en/ko.
- `InsightRevision` carries `source_mode` (`foundry_live`, `foundry_cached` or `deterministic_fallback`), `review_status` and a trace id.

### Checks beyond types

- References must resolve.
- `complete_tradeoff` needs at least one restricted and one exposed claim.
- An unavailable metric is null, never 0. Ratios lie in [0, 1], and counts are derived from id lists.
- Overlay anchors must exist in that frame, and geometry must stay inside the pitch.
- A live packet never contains an event that becomes available after its cutoff.

## 5. Analytics (computed in code)

- **Phase:** `buildup`, `progression`, `transition` or `unknown`, with the ball state as a separate field. Phase counts as transition for 2 s after possession changes.
- **Lines:** a 3 s window of own-goal depths smoothed with a temporal median, then candidate line partitions.
  - The analysis abstains (ambiguous or unclassified) when spread or membership is unstable.
  - Outputs: back-line depth, mid-back gap and front-back span.
- **Pressure:** a 1 s window. The gate is within 6 m of the carrier and closing at 1 m/s or more for 0.6 s, or staying within 2 m.
  - The arrow shows the observed approach, never body orientation.
  - Cover shadow is approximate only.
- **Marking:** pair statistics over 3 s, needing 5 m or less for at least 2 s.
  - Linear assignment includes an "unmatched" option, and mark switches are kept.
  - "Man-oriented" needs at least 4 supported pairs and at least 60% of the attackers in the ball zone for 2 s.
- **Lanes:** candidate receivers 5-25 m from the carrier, sampled every 2 m along the pass. A receiver more than 6 m back toward the carrier's own goal (the keeper, a deep centre-back) is not a short option (analytics 0.2): a defence is credited for the forward and square passes it takes away, not for back passes.
  - The ball travels at 14 m/s. A defender arrives after 0.2 s plus distance at 7 m/s.
  - A margin of 0.1 s or less means screened; 0.4 s or more means open; anything in between is uncertain.
  - This is a ground-pass access heuristic, not a pass probability.
- **Offside:** approximate. The line is the further of the second-last defender (goalkeeper included) and the ball. Offside players are not counted as open options.
- **Cover and isolation:** time for the other defenders, the goalkeeper included, to reach the attacker's position projected 1 s ahead.
  - `isolated_1v1` needs a supported marking pair, an onside and relevant attacker, and cover at least about 0.5 s late.
- **Regions:** the central region is `[u_ball + 5, u_ball + 25] × [20, 48]`, and counts leave out goalkeepers.
  - A free receiver needs marking support below 0.4, the nearest defender at least 4 m away, an open eligible lane, and a reception margin of at least 0.3 s.

The spec defines twelve claim codes, each with a minimum evidence gate. Ten are computed today; the two marked "spec only" are in the contract allowlist but are not computed yet.

| Claim | Starts when |
|---|---|
| `short_options_screened` | at least 3 eligible short options, at least 2 screened, for 1 s |
| `central_routes_screened` | at least 2 of the central eligible options screened, with a region and presser |
| `behind_space_exposed` | back line at least 40 m deep, an onside runner going forward, late cover |
| `behind_depth_reduced` | back depth down 8 m or more against a baseline from the same episode |
| `press_block_disconnected` | active press with a front-back span of 35 m or more, or a mid-back gap of 20 m or more |
| `between_lines_receiver` | an attacker between identified lines, with lane access and reception supported |
| `isolated_1v1` | as above |
| `wide_outlet_available` | central screening and an open wide eligible lane in the same window |
| `weak_side_accessible` | a far-side candidate with a valid ground switch (spec only) |
| `local_central_overload` | attackers minus defenders at least 1 in the fixed region, for 1 s |
| `accessible_free_receiver` | as above |
| `second_ball_support_gap` | only when a landing point is observed or can be estimated (spec only) |

## 6. Episodes and revisions

- **Key and lifecycle:**
  - An episode is keyed by defending team, phase, region and claim family.
  - Its lifecycle runs `candidate` → `active` (condition held for 1 s) → `cooling` (condition gone for 1.5 s) → `closed` (gone for 3 s, or possession changes).
- **Trade-off bundles:**
  - A: screened short options + space behind or isolation.
  - B: reduced depth + a disconnected block or a receiver between the lines.
  - C: screened central routes + a wide outlet or a free receiver.
  - With only one side, the episode is `partial` and is never presented as complete.
- **Change chain:** initial state → attacking response observed → defensive adjustment observed → updated state. Each step is `observed_next` or `consistent_with_response`, and never states intent or cause.
- **Outcome:**
  - The status is one of `not_yet_observed`, `exploited_in_synthetic_play`, `not_exploited_in_window` or `inconclusive`.
  - Outcome events are linked within a 6 s window.
  - A result that arrives after the evidence cutoff becomes a new revision.
- **Story priority:**
  - The formula is 0.35·support + 0.25·change magnitude + 0.25·ball relevance + 0.15·novelty.
  - The spec's gates (support ≥ 0.65, priority ≥ 0.6) are not applied in the build: priority only ranks moments, and the UI follows the highest-priority complete trade-off.
  - An outcome never changes the priority of the past.

## 7. Overlay and approval

- **Presets:**
  - Fan: at most 6 display units and 2 labels. A unit is a group of shapes that say one thing together (the screened lanes of a press; the presser arrow and carrier ring; the exposed space and its runner; an open outlet and its target; late cover and its link). A complete trade-off keeps at least one unit from each side (amended 2026-10-24: counting single shapes dropped one side from the broadcast pitch).
  - Analyst: at most 20 elements and 5 labels.
  - The producer inspector has every toggle plus the JSON.
- **Legibility:**
  - A focus claim limits what is drawn.
  - Meaning is shown with line style, hatching and the legend as well as colour.
  - An allowed path is never drawn like an actual pass.
- **Timing:**
  - A live overlay expires after 0.6 s.
  - Before and After are two frozen snapshots, each drawn on its own frame.
  - There are no "ghosts" of future positions, and old numbers are never drawn over current positions.
- **Broadcast path:** live suggestion → approval → retrospective replay of that moment, labelled "REPLAY · analysed through t" and never backdated.
- **Approval states:**
  - `review_status` (suggested, approved, rejected) belongs to the insight revision. `broadcast_visibility` (hidden, scheduled, showing, expired) belongs to the session.
  - An approval carries the revision and its evidence hash, and stale evidence returns 409.
  - Approved content is immutable, and reanalysis creates a new suggested revision.

## 8. Agent contract

| Role | Does | Must not |
|---|---|---|
| Analyst agent | Pairs restricted and exposed claims; chooses order, relevance and caveats; decides what to leave out | Compute new numbers from positions; invent players, intent or outcomes |
| Producer agent | Builds Fan/Analyst × EN/KO sentence plans and layer focus from the Analyst plan and the same packet | Produce coordinates; add unsupported claims; tell a success story before an outcome is observed |
| Code validator | Checks schema, references, cutoff, hash, allowed templates and metrics, and role conditions | Treat the model's own confidence as fact |
| Template renderer | Fills registry sentences with the packet's real metric values | Take numbers from free text |

- **Sentences:**
  - Each sentence is an agent-chosen plan plus validated bilingual templates.
  - Writing free text with numbers and then checking it with regular expressions is explicitly rejected.
  - A complete trade-off that keeps only one side is refused.
- **Tools:**
  - Tools are read-only and scoped to the packet.
  - No tool reaches the generator, future events, web search or arbitrary code.
- **Limits:**
  - 20 s per pipeline, 8 s per stage, and at most 3 model requests including one repair.
  - At most 4 jobs pending per replay and 2 concurrent pipelines.
  - Important episodes go first, and duplicate work is merged.
- **Cache key:** the evidence hash, the prompt, template and analytics versions, and the model deployment.
- **Provenance:**
  - The source mode is always shown.
  - "Configured" is reported separately from "last call succeeded".
  - Traces hold stage times, the deployment, retries, validation codes and usage, and never secrets.
- **Late results** are checked against the current epoch and cutoff. If they no longer fit, they go to history only.
- **No unmeasured claims:** the spec makes no claim that two agents beat one prompt until that is measured.
- **SDK path:**
  - Agents are app-owned: `Agent` running on `agent_framework.foundry.FoundryChatClient` (`agent-framework-foundry`).
  - Only `gateway.py` (the agents) and `recap_workflow.py` (the Recap workflow) import the Agent Framework SDK.
  - Authentication uses the Azure CLI credential locally and managed identity on Azure.
  - Access is confirmed only by a real call.

## 9. API and sessions

- **REST:**
  - `health` and `clips`.
  - Replay create and control: play, pause, seek, set speed, with the expected epoch.
  - `snapshot`, `episodes`, `evidence`, `overlays`, `insights`, `decision`, `recap` and `export` (approved revisions only).
- **WebSocket envelope:** `schema_version`, `replay_id`, `analysis_epoch`, `seq`, `type` (from an allowlist) and `match_time_s`, deduplicated on (replay, epoch, seq).
- **Errors:**
  - 404 unknown, 422 invalid, 409 stale epoch or revision, 410 session ended, 429 over limit.
  - A cloud failure never becomes a 500 for the replay.
- **Seek:**
  - A seek starts a new epoch and invalidates pending cloud work.
  - The analysis is rebuilt deterministically without any cloud call.
  - Approvals are not carried into the new epoch.
- **Speed:** playback speed changes only the match clock. Cloud timeouts are measured in wall time.
- **Spec demo defaults:** 30 min idle TTL, at most 20 sessions, one worker, one replica.

Sections 9 and 11 describe the spec. Where the build differs (seek and approvals, ended sessions, the session limit), see [As built](#as-built-2026-10-25).

## 10. Evaluation plan

- **Fixtures:**
  - Small geometry fixtures that can be checked by hand.
  - A holdout reporting, per family and counter-example: claim precision and recall, false episodes per minute, onset lag and abstention.
- **Matching:** IoU ≥ 0.3 with one-to-one matching.
- **Targets:**
  - Claim precision ≥ 0.90 and zero wrong emphasis on the mandatory counter-examples.
  - Recall and abstention are published too, so saying nothing does not count as success.
- **Performance:** p95 under 200 ms per 5 Hz analysis step on CPU, and p95 under 300 ms for a geometry UI update.
- **Agents:** plan validity, fallback ratio, reference errors, how often both sides are kept, calls, time and usage.

Results: [`docs/evaluation.md`](evaluation.md).

## 11. Scope and limits

- **P0 (spec):**
  - reproducible synthetic clips with counter-examples;
  - causal analytics and evidence;
  - trade-off episodes with at least one response and re-evaluation;
  - a synced SVG pitch with an inspector, Before/After and a timeline;
  - Foundry Analyst → Producer with validation and deterministic fallback;
  - producer approval, a broadcast view, Fan/Analyst × EN/KO and a short session recap;
  - a JSON API, an Azure demo deployment, public code and a video under 2 minutes.
- **P1:** restoring the v1 overlap/underlap analysis, more defensive schemes, grouped tactical changes, lightweight ML comparison, player focus, longer synthetic matches.
- **P2:** real video or real match data, learned pitch control or xG, causal counterfactuals, Nash or game-theory optimisation, a full 11-v-11 match engine, distributed serving.
- **Deployment boundary (spec 19):**
  - One Azure Container App, with secrets on the server only.
  - Planned safeguards: demo session tokens, input limits, session TTL, an origin check, and a cloud call cap.
  - In the open demo, approval controls the broadcast preview inside one session. It is not a real broadcast permission system.
  - Login, role-based access and distributed state come later.

## As built (2026-10-25)

| Spec | Build |
|---|---|
| Families A, B and C with counter-examples; clips of 24-36 s | Six set-ups (A, B, C, D, E, G; the letters are design labels, and there is no F), each with a counter-example; 12 clips of about 14-24 s (13.8-24.0 s). The set-ups are described in [`evaluation.md`](evaluation.md#set-ups). |
| Twelve claim codes | Ten are computed. `weak_side_accessible` and `second_ball_support_gap` are in the contract allowlist but not computed yet. |
| Analyst and Producer read the packet through per-item tools (`get_claim`, `get_metric`, ...) | The packet is in the prompt, and Analyst and Producer have no tools. The Producer chooses sentence-option ids per audience, shared by both languages (`prompts-0.2.0`). |
| Two agents | Plus Ask PitchIQ, a question-answering agent with three read-only tools bound to the question time. Plus a Recap agent inside an Agent Framework workflow, and an MCP server with five read-only tools (mounted at `/mcp` in the web app and live on the public demo). |
| Outcome window from episode activation | Each exposed claim gets its own outcome window, starting from when it was first claimed |
| Cloud call cap | One daily cap (`FOUNDRY_DAILY_LIMIT`) shared by narration runs, Ask questions and recaps, plus a one-question-or-recap-at-a-time limit per replay |
| Evidence store module `evidence/store.py` | Hashing lives in `backend/app/contracts/evidence.py` |
| Rebuilt without any cloud call | The rebuilt revisions go to the cloud pipeline again under the usual rules (one job per episode; a newer revision waits and only the latest is sent). A plan cached for the same evidence hash comes back at once and attaches only if its revision is still the latest; otherwise a new Foundry run starts and counts against the daily cap. |
| Approvals are not carried into the new epoch | A seek resets broadcast visibility to hidden, but an approval stays valid for the exact revision and evidence hash it named. A rebuilt revision with the same number and hash is still approved after a seek, so the broadcast view and export can serve it again. A newer revision is suggested again. |
| 410 when a session has ended | A deleted or evicted replay returns 404 on REST; its WebSocket closes with code 4410 |
| At most 20 sessions; 429 over the limit | At most 20 replays. A new one closes the least recently used instead of being refused. Replays close after 30 min without a client action, or after 10 min without one if no WebSocket is open. The client sends `DELETE /api/replays/{id}` when it leaves a replay, and the WebSocket sends a heartbeat every 15 s. |
| Personalisation (end of the architecture) | Fan/Analyst × EN/KO; My club (moments marked as your club defending or attacking) and Follow a player (ring, measured speed, timeline filter), computed in the UI from the episode summaries. |
| The producer inspector and the broadcast view (section 7) | Plus a guided breakdown of one moment (`GET /api/replays/{id}/breakdown`, "Break down the defence" in the UI): the press with who follows whom, what it takes away, what it leaves open and what happened. Each step uses the revision that first had it (its frame, its overlay layers only, the code-rendered fan sentences of that revision), names its players from the relations and events (presser, carrier, runner, cover, passer, receiver), and adds the sustained marking pairs or the ball's path from the frames between the outcome's pass and its reception. When the analysis recorded no outcome, the last step shows the next pass or shot the replay has seen (received, intercepted or stopped, with its path and tags) as an observation; it does not say whether that pass used what was left open. If nobody plays the ball on before the clip ends, the step says so. A press step whose narration has no sentence about the press gives the player on the ball and the nearest defender with the distance, measured on the step's frame, and the ball carrier is tagged even when nobody presses. The panel lists the clip's moments, and any of them can be broken down. A step the analysis has not reached says so. No model writes any of it; the last step is the usual approval and broadcast view. |
| Synthetic fixtures (section 10) | Generator 0.6 (after a review of the clips as a football fan): around each staged set-up the defending team keeps its shape. Each player stays within a leash of his formation spot (the start spot moved by half the ball's movement along the pitch and a third across it), apart from team-mates, and the back line holds the edge of its box unless the ball, or the man it follows, is deeper. The nearest outfield player engages a free carrier in a dangerous spot (goal-distance danger, an expected-threat-like surface: about 0.7 at 11 m central, 0.37 at 20 m, 0.18 at 30 m) from about 32 m out, and a defender the ball has gone past by 10 m gets back goal-side of it. A screen or a press cut skips a man a team-mate already has, markers stand at least 3 m off their man, the defending keeper stays within about 7 m of his line (8 m for a ball in behind), linking passes wait up to 2 s for a likely lane (pass success from the pass length and the defenders who can reach its line first), and a clip ends 1.6 s after the defence wins the ball. The UI rings the unmarked players of the team on the ball (no defender within 8 m, kept until one comes within 6.5 m, and no defender following him; the three with the most room within 60 m of the ball). Generator 0.7 (a third fan review): A "cover" keeps the left winger marked, B "compact" keeps its 4-3-3 while the ball goes round the back, C's striker cuts the pass to the other centre-back as he presses, C "support" marks the pivot man to man and presses the keeper on the back pass, G "main" sends a second runner in behind, G "offside" goes three on three in central midfield and finds the free centre-back. |
| A short session recap (P0) | A deterministic recap endpoint (`GET /api/replays/{id}/recap`), and a Recap card in the UI built by the Recap agent in an Agent Framework workflow (`POST /api/replays/{id}/recap/agent`), with the deterministic recap as its fallback. |
| Origin check (spec 19) | Not implemented. Session tokens, TTL, input limits (questions up to 280 characters, speed 0.25-4×, seek inside the clip) and the cap are in place. |
| Azure Container Apps deployment | Deployed 2026-10-24 (one replica, system-assigned managed identity, Sweden Central); commands in `infra/README.md`. CI (`.github/workflows/ci.yml`) builds, smoke-tests and pushes the image to GitHub Container Registry. |
| Video under 2 minutes (P0) | Not recorded yet when this was written (2026-10-25); the README links it once it is uploaded |
