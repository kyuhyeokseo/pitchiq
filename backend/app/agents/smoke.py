"""One real Microsoft Foundry run of the Analyst -> Producer workflow on a committed clip.

    PYTHONPATH=backend python -m app.agents.smoke [--clip clip_003]
    PYTHONPATH=backend python -m app.agents.smoke --clip clip_001 --ask "Why was the space behind open?"
    PYTHONPATH=backend python -m app.agents.smoke --clip clip_001 --recap [--my-club blue --follow blue_09]

Needs .env (or the environment) with FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL, the ``foundry`` extra installed,
and an Azure login that may call the project (``az login`` for AUTH_MODE=azure_cli). Prints the trace - stages,
timings, deployment name, attempts, validation codes, reported token counts - and the rendered fan/en text. With
``--ask``, one Ask PitchIQ question about the whole clip instead: the trace with every tool call, and the answer. With
``--recap``, one run of the Recap workflow on the whole clip for that viewer: the trace (every workflow step and tool
call) and the recap's sentences. Exits 1 when the run fell back. Never prints the endpoint, prompts or credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from ..narrative.render import render_sentence
from ..replay.catalog import load_clip
from .ask import run_ask
from .cloud import Limits, run_pipeline
from .gateway import FoundryGateway, foundry_settings, load_env_file, sdk_available
from .recap import RecapProfile, check_profile
from .recap_workflow import run_recap

ROOT = Path(__file__).resolve().parents[3]


def _complete_packet(clip_id: str):
    from ..analytics.engine import TacticalAnalyzer
    clip = load_clip(clip_id)
    an = TacticalAnalyzer(clip.meta, clip.clip_id)
    seen: set[str] = set()
    for f in clip.frames:
        arrived = [e for e in clip.events if e.available_at_s <= f.match_time_s + 1e-9 and e.event_id not in seen]
        seen |= {e.event_id for e in arrived}
        an.step(f, arrived)
    revs = [r[-1] for r in an.packets.values()]
    best = max(revs, key=lambda p: (p.episode.analysis_status == "complete_tradeoff", p.episode.story_priority))
    return clip, best


async def _main(clip_id: str) -> int:
    clip, packet = _complete_packet(clip_id)
    gw = FoundryGateway.from_env()
    try:
        analyst, producer, trace = await run_pipeline(packet, gw, Limits())
    finally:
        await gw.close()
    print(json.dumps({"episode": packet.episode.episode_id, "status": packet.episode.analysis_status,
                      "trace": trace.as_dict()}, indent=1))
    if producer is None:
        print("fell back to the deterministic plan:", trace.reason, file=sys.stderr)
        return 1
    v = producer.variant("fan", "en")
    for s in v.sentences:
        print(" -", render_sentence(s, packet, "en", clip.meta))
    return 0


async def _ask(clip_id: str, question: str, audience: str, language: str) -> int:
    from .tools import view_at
    view = view_at(clip_id)
    gw = FoundryGateway.from_env()
    try:
        answer, trace = await run_ask(view, question, audience, language, gw)
    finally:
        await gw.close()
    print(json.dumps({"question": question, "trace": trace.as_dict()}, indent=1, ensure_ascii=False))
    for line in answer["sentences"][language]:
        print(" -", line)
    if trace.result != "foundry_live":
        print("fell back to the deterministic answer:", trace.reason, file=sys.stderr)
        return 1
    return 0


async def _recap(clip_id: str, profile: RecapProfile) -> int:
    from .tools import view_at
    view = view_at(clip_id)
    try:
        check_profile(profile, view)
    except ValueError as e:
        print(f"{e}; nothing was called.", file=sys.stderr)
        return 2
    gw = FoundryGateway.from_env()
    try:
        recap, trace = await run_recap(view, profile, gw)
    finally:
        await gw.close()
    print(json.dumps({"profile": profile.model_dump(), "trace": trace.as_dict()}, indent=1, ensure_ascii=False))
    for item in recap["items"]:
        tags = f"followed={item['involves_followed']} my_club={item['my_club_role']}"
        print(f"[{item['detected_at_s']:.1f} s] {item['episode_id']} ({tags})")
        for line in item["sentences"][profile.language]:
            print(" -", line)
    for line in recap["note_sentences"][profile.language]:
        print(" -", line)
    if trace.result != "foundry_live":
        print("fell back to the deterministic recap:", trace.reason, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default="clip_003")
    ap.add_argument("--ask", default="", help="ask this question instead of running the Analyst/Producer pipeline")
    ap.add_argument("--audience", default="fan", choices=("fan", "analyst"))
    ap.add_argument("--language", default="en", choices=("en", "ko"))
    ap.add_argument("--recap", action="store_true", help="run the Recap workflow on the whole clip instead")
    ap.add_argument("--my-club", default=None, help="recap: the viewer's club, a team_id such as blue")
    ap.add_argument("--follow", default=None, help="recap: a followed player_id such as blue_09")
    args = ap.parse_args(argv)
    load_env_file(os.environ.get("PITCHIQ_ENV_FILE", ROOT / ".env"))
    s = foundry_settings()
    if not (s["endpoint"] and s["model"]):
        print("FOUNDRY_PROJECT_ENDPOINT / FOUNDRY_MODEL are not set (.env); nothing was called.", file=sys.stderr)
        return 2
    if not sdk_available():
        print("agent-framework-foundry is not installed: pip install -e '.[foundry]'; nothing was called.",
              file=sys.stderr)
        return 2
    if args.recap:
        profile = RecapProfile(audience=args.audience, language=args.language, my_club=args.my_club,
                               follow_player=args.follow)
        return asyncio.run(_recap(args.clip, profile))
    if args.ask:
        return asyncio.run(_ask(args.clip, args.ask, args.audience, args.language))
    return asyncio.run(_main(args.clip))


if __name__ == "__main__":
    raise SystemExit(main())
