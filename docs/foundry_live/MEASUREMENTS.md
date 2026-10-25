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
