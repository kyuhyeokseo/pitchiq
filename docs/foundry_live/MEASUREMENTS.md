# Live Microsoft Foundry measurements (2026-10-24)

Real calls from the development machine to a Microsoft Foundry project (Azure for Students subscription,
Sweden Central). Deployment: `gpt-5-mini`, Global Standard. Agents: Microsoft Agent Framework
(`agent_framework.Agent` on `agent_framework.foundry.FoundryChatClient`), Azure CLI credential.
Numbers are wall time on the client and the token counts the service reported. Costs are estimates from the
published per-token prices ($0.25 per 1M input, $2.00 per 1M output tokens), not billing records.

## Analyst -> Producer narration (`python -m app.agents.smoke --clip <clip>`)

Spec limits unchanged: 8 s per stage, 20 s per pipeline, one repair, three model calls at most.

| Setting | Runs | Validated | End to end | Tokens per run (in / out) | Cost per run |
|---|---|---|---|---|---|
| first attempt, default reasoning, long Producer output | 1 | 0 (Producer timed out at 8 s) | - | 845 / 534 (Analyst only) | - |
| reasoning effort `low`, short option ids | 4 (4 clips) | 4 (1 after a repair) | 7.3-14.7 s | 2.3k-4.9k / 0.5k-1.2k | ~0.2-0.4 cent |
| reasoning effort `minimal`, before the option-id format constraint | 4 (4 clips) | 3 (clip_001: the model merged three ids into one string, twice) | 5.6-7.3 s | 2.3k-4.9k / 0.2k-0.3k | ~0.1-0.2 cent |
| reasoning effort `minimal`, option ids constrained to `s<number>`, at most 6 (default) | 8 (7 clips) | 8 (1 after a repair) | 4.6-7.7 s | 2.3k-4.4k / 0.2k-0.3k | ~0.1-0.2 cent |

What changed between the first attempt and the default: the Producer now picks short sentence option ids
(`s1`, `s2`...) that code has already bound to the evidence, instead of writing template keys and ids
(output fell from about 1,500 to about 100-300 tokens), and `FOUNDRY_REASONING_EFFORT=minimal`.

## Ask PitchIQ (`python -m app.agents.smoke --clip clip_001 --ask "<question>"`)

| Question | Result | Agent time | Tool calls | Tokens (in / out) |
|---|---|---|---|---|
| What did the defence shut down, and what did it cost them? | moment, 3 validated sentences | 7.4 s | 3 | 10.6k / 168 |
| Why did the coach want to press so high? (before the intent note existed) | moment without a note, after its single repair (unknown option id) | 16.5 s (2 runs) | 5 | 17.5k / 304 |
| What did the defence shut down? | moment, validated | 6.3 s | not recorded | not recorded |
| Why did the coach want to press so high? (two runs) | intent note, then 3 sentences | 7.6 s, 8.1 s | 2 (first run) | 7.0k / 136 (first run) |
| Why did the coach set it up like that? | intent note, then sentences | 7.8 s | not recorded | not recorded |
| 감독은 왜 그렇게 세웠어? (Korean) | intent note in Korean, then sentences | 7.6 s | not recorded | not recorded |
| Will Blue score from this? | declined: not yet known | 5.8 s | 2 | 6.2k / 132 |
| 그 압박의 대가는 뭐였어? (Korean) | moment, Korean sentences | 9.8 s | 2 | 7.5k / 133 |

About 0.2-0.3 cent per question where tokens were recorded (6.2k-10.6k input, 132-168 output). Intent questions
open with the fixed note "PitchIQ does not read minds" in 3/3 runs after the instruction was made explicit (one
earlier run answered without the note).

## Recap workflow (`python -m app.agents.smoke --clip clip_001 --recap ...`)

Microsoft Agent Framework workflow: gather (code) -> recap agent (Foundry, read-only tools) -> code check -> render, or
the deterministic recap.

| Profile | Result | Total | Tool calls | Tokens (in / out) |
|---|---|---|---|---|
| fan, EN, my club red, following blue_09 (run 1) | fallback: transient service error (`ChatClientException`) | 10.1 s | 0 | - |
| fan, KO, my club blue | 2 moments, Korean sentences, validated | 15.5 s | 2 | 7.1k / 147 |
| fan, EN, my club red, following blue_09 (runs 2-3) | 3 moments, validated | 14.9 s, 11.5 s | 3 | 8.0k / 179-207 |

3 of 4 runs validated; the failed run showed the deterministic recap and said so. About 0.2 cent per recap.

## Live replay at 1x (clip_001, 23.8 s of play, 7 episodes)

Correction, found later the same evening: while a browser was connected, the replay clock ran about 1.8x fast (each
sync moved the clock up to the nearest frame, and the WebSocket loop syncs every 0.1 s). These replays were therefore
shorter in wall time than a true 1x, which gave the agents less time, not more. Fixed with a test
(`test_playback_keeps_wall_time_when_the_socket_syncs_often`).

| Version | Episodes whose final revision got a Foundry plan | Pipelines | Settled after the clip ended |
|---|---|---|---|
| before per-episode coalescing | 2/7 (main moment skipped: queue full of soon-stale jobs) | 7 | - |
| one job per episode, latest revision only, priority retry | 6/7 (1 kept the validated fallback after a failed repair) | 11 | 13.5 s |

About 1 cent per clip. The deterministic narration is on screen from the first frame in every case; a Foundry plan
arrives later as a new, labelled revision.

## Browser check with Foundry on

The real app on a compute node, Foundry enabled, clip_001 played at 1x, then questions asked in the UI (five sessions
that evening, two questions each) and, in the last three sessions, one recap each.

| Session | Ask: browser time (agent time) | Recap |
|---|---|---|
| 1 | 12.5 s (5.0 s), 6.5 s (6.4 s) | - |
| 2 | 17.4 s (10.5 s), 6.9 s (6.9 s) | - |
| 3 | 17.4 s (16.6 s), 5.7 s (5.7 s) | 5.0 s, validated |
| 4 | 6.6 s (6.5 s), 7.8 s (7.7 s) | 8.3 s, validated |
| 5 | 34.4 s (25.0 s), 10.6 s (10.5 s, one repair: the first choice used an unknown option id) | 11.2 s, validated |

- In session 2 the first question waited behind background narration jobs for the shared concurrency slots, so
  questions then got their own slots (`Limits.ask_concurrency`); sessions 3-5 ran with that change.
- Agent time varied a lot between sessions (5.0-25.0 s for the same two questions); the 45 s Ask deadline was never
  reached. The gap between browser and agent time in session 5 (9.4 s) was not investigated.
- Each answer shows its tool calls and the code check in the UI. Screenshots: `ask_and_narration_live.png` (session 5)
  and, combined from the same session, `../images/foundry_agents_live.png`.

## Deployed app (Azure Container Apps, managed identity)

The image CI built for commit `a0cbfea`, on Azure Container Apps in Sweden Central (0.5 vCPU, 1 GiB, one replica),
signing in to Foundry with its system-assigned managed identity (role: Foundry User on the Foundry resource). Calls from
the development machine to the public URL, 2026-10-24 (about 23:05-23:10 US Eastern).

| Call | Result |
|---|---|
| `/api/health` before any call | `foundry_configured: true`, `last_call_succeeded: null` |
| Ask before the first moment | declined (`no_moment_yet`) by the live agent, 5.1 s in the browser-less client, `foundry_live`; then `last_call_succeeded: true` |
| `POST /mcp` `tools/list` over HTTPS | 200, 5 tools |
| 1x replay of `clip_001` over `wss://` | streamed to the clip-end snapshot, but in 13.4 s for 23.8 s of play: the clock bug above, found here |
| Ask after the replay: "What did the defence shut down, and what did it cost them?" | a moment, 3 validated sentences, 5.9 s, 2 tool calls, no repair |
| Recap after the replay (fan, EN) | 3 moments, validated, `foundry_live`, 8.2 s |

The first browser walkthrough against the public URL stopped at the live-overlay check: over that link a newer live
overlay request was always in flight, and the client drew only the answer to the latest request, so nothing was drawn.
The fix and a slow-link check (400 ms per request) are in the next commit; see `docs/evaluation.md`.

After the fixes (image `sha-eaae7c0`, 2026-10-25 about 11:20 US Eastern; the environment had just been converted from
the Express mode to the workload-profiles mode, see `infra/README.md`):

| Call | Result |
|---|---|
| `POST /mcp` `tools/list` over HTTPS | 200, 5 tools |
| 1x replay of `clip_001` over `wss://` | 23.8 s of play in 23.8 s, then the clip-end snapshot |
| Ask after the replay, same question | a moment, 3 validated sentences, 5.8 s, 3 tool calls, no repair |
| Recap after the replay (fan, EN) | 2 moments, validated, `foundry_live`, 9.1 s |

### Browser walkthrough against the public URL (image `sha-6c98218`, 2026-10-25 about 14:20 US Eastern)

`evaluation/walkthrough/walkthrough.py --base-url <public URL>` from an HPC compute node, Foundry on: 58 checks
passed, 0 failed, no browser console errors. With Foundry on, the four checks of particular template sentences are kept
as information instead of pass or fail, because the agents may choose other validated sentences: the agents chose other
sentences in 3 of them and the expected sentence appeared in 1. The run used 81 of the 300 daily Foundry runs (counter
after the run; the counter restarts with each deployment). `/api/health` reported `build.sha` 6c98218, `/api/clips`
12 clips and `/mcp` 5 tools.

The same check on image `sha-9afb2c2` (the fan-caption fix), 2026-10-25 about 16:20 US Eastern: 58 passed, 0 failed,
4 template-sentence checks kept as information (the agents chose other sentences in 3, the expected one appeared in 1).

### The guided breakdown on the public URL (2026-10-25 and 10-26, US Eastern)

- Image `sha-3612feb` (first breakdown build), 10-25 about 22:15: 66 passed, 1 failed, 4 kept as information. The
  failure was a browser console 404: Play at the end of a clip rewound while a request for the moment it had shown
  was still on its way. Looking into it found a second timing problem over a slow link (a seek to the end while
  playing could leave Pause on an ended clip). Both were fixed in `4e11e09`; see `docs/evaluation.md`.
- Image `sha-4e11e09`, 10-25 23:28, first run after the deployment (empty narration cache), the video take scripted
  at 1920x1080 (`Break down the defence`, the four steps, On air, approve, then the desk and one Ask question):
  the first step appeared 5.2 s after the click (the analysis runs to the end of the clip on the 0.5 vCPU replica);
  the On air step showed "Microsoft Foundry agents (gpt-5-mini)" 23.1 s after the click; the approved caption went to
  the broadcast view; Ask answered "Why did Red set up like that at 10:22?" in 6.4 s in the browser (6.0 s agent time,
  1 run, 2 tool calls, 7,143 input and 136 output tokens), with the fixed intent note first. No page errors.
- Image `sha-b3e6013` (the question card stays up while the clip is analysed), 10-26 00:26: 68 passed, 0 failed, no
  console errors, 4 template-sentence checks kept as information (the expected sentence appeared in 2, the agents chose
  other validated sentences in 2). `/api/health` after the run: `build.sha` b3e6013, 89 of the 300 daily runs used,
  last call succeeded.

- Image `sha-631c890` (fixes from the four persona usability runs), 10-26 13:23, first run after the deployment:
  the first step 7.1 s after the click; On air showed the Foundry text 31.3 s after the click; Ask 6.5 s. The public
  walkthrough at 13:30 stopped at the On air approval: the socket had opened after the breakdown's seek and did not
  send the insights built by it, so nothing could be approved for about 30 s. Fixed in `d47c0fe`.
- Image `sha-d47c0fe` (the socket sends the insights so far when it opens), 10-26 13:53, first run after the
  deployment: the first step 5.6 s after the click; On air had the validated-template text at once and showed the
  Foundry text 26.1 s after the click; Ask answered in 7.8 s in the browser. No page errors. Public walkthrough at
  13:59: 70 passed, 0 failed, no console errors, 4 template-sentence checks kept as information (expected sentence in
  2, other validated sentences in 2). `/api/health` after the run: 90 of the 300 daily runs used, last call succeeded.
- Image `sha-3344b47` (moment chips in the breakdown, the next pass when no outcome is recorded, templates-0.3.2,
  gliding playback), deployed 10-26 18:44. Public walkthrough at 18:46: 71 passed, 0 failed, no console errors, 4
  template-sentence checks kept as information (expected sentence in 2, other validated sentences in 2). Playback on
  the public URL (Clip 1, Just watch at 1x, 5 s): players moved on 300 of 301 animation frames, at most 0.13 m per
  animation frame. A rehearsal of the video path after that (not a first run: the walkthrough had already built the
  moment's narration): the first step 3.6 s after the click, On air with the Foundry text at once, Ask 7.3 s, no page
  errors. `/api/health` after the runs: 87 of the 300 daily runs used since the deployment, last call succeeded.
- Image `sha-54c0581` (generator 0.6 team behaviour, analytics 0.2, unmarked players), deployed 10-26 23:23. Public
  walkthrough at 23:25: 71 passed, 0 failed, no console errors, 4 template-sentence checks kept as information. A
  rehearsal of the video path after it (not a first run): the first step 3.2 s after the click, On air with the Foundry
  text at once, Ask 6.8 s, no page errors. `/api/health` after the runs: 66 of the 300 daily runs used since the
  deployment, last call succeeded.

## Application Insights tracing, from the development machine (2026-10-25, about 16:07 US Eastern)

The app (in process, `TestClient`) with `APPLICATIONINSIGHTS_CONNECTION_STRING` set to the project's existing
Application Insights resource: one replay of `clip_001` sought to its end (11 narration pipelines) and one Ask question.
A query of the resource a few minutes later listed, among others: `invoke_agent pitchiq-analyst`,
`invoke_agent pitchiq-producer` and `invoke_agent pitchiq-ask` (Agent Framework agent runs), `chat gpt-5-mini` (model
calls), `execute_tool get_evidence`, `get_history` and `answer_options` (the Ask agent's tool calls), the Foundry HTTP
calls, the PitchIQ spans `pitchiq.narration` (11) and `pitchiq.ask` (1), and the request `POST /api/replays/{rid}/ask`.
No prompt or model text is recorded (sensitive data off). The live app does not send traces yet: that needs the
connection string as a Container App secret (see `infra/README.md`).

## Application Insights tracing from the live app (image `sha-f45f1a6`, 2026-10-25 about 18:15 US Eastern)

The connection string of the existing Application Insights resource was set as a Container App secret and passed to
the app as `APPLICATIONINSIGHTS_CONNECTION_STRING`; `/api/health` then reported `telemetry.enabled: true`. One scripted
browser run of the video path (Clip 1 at 0.5×, evidence, Ask, approve, export, broadcast, recap) followed. A query of
the resource a few minutes later, filtered to the container's role instance, listed among others:
`invoke_agent pitchiq-analyst` (9), `invoke_agent pitchiq-producer` (14), `invoke_agent pitchiq-ask` (1),
`chat gpt-5-mini` (22), `execute_tool answer_options` (4), `pitchiq.narration` (14), `pitchiq.ask` (1), the Foundry
HTTP calls, and API requests such as `POST /api/replays/{rid}/recap/agent` and `GET /api/replays/{rid}/export/...`.
It also showed one span per WebSocket message (238 in this run) and the ASGI layer's internal send and receive spans;
the next build leaves both out (the replay stream and per-frame routes are not traced).

On image `sha-d3dc493` (2026-10-25 about 19:42 US Eastern), a 6-second live stream of 64 WebSocket messages left no
WebSocket or internal send/receive spans; the API requests (`POST /api/replays`, `POST /api/replays/{rid}/control`,
`DELETE /api/replays/{rid}`), the agent runs, the model calls and `pitchiq.narration` were traced.
