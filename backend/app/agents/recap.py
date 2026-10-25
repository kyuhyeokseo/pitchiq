"""Recap: which moments tell the story of the replay so far, for one viewer.

A viewer (fan or analyst, English or Korean, optionally a club they support and a player they follow) asks for a
recap of the replay up to now. The Recap agent chooses 1-3 of the moments found so far and, for each, 1-3 of its
sentence options; it never writes a sentence or a number. Code computes everything else: the tags shown with each
moment (whether the followed player is in its evidence, whether the viewer's club was defending or attacking), the
telling order (replay order), and the check that decides whether the choice may be shown (known moments and options,
both sides of a complete trade-off, the followed player's moment when there is one). When the choice fails that
check, or the agent cannot answer, the deterministic recap below is shown instead.

The agent run itself is a Microsoft Agent Framework workflow (``recap_workflow``); this module holds what it
chooses, how code checks it, and the deterministic fallback. No SDK types here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import EvidencePacket
from ..narrative.registry import ASK_DECLINES
from .ask import Option, sentence_options
from .llm_plans import LANGUAGES
from .tools import View, _rel
from .validate import PlanError

MAX_ITEMS = 3
MAX_ITEM_SENTENCES = 3


# ------------------------------------------------------------------ who the recap is for
class RecapProfile(BaseModel):
    """The viewer a recap is for. ``my_club`` is a team_id ('blue', 'red'); ``follow_player`` a player_id
    ('blue_09'). Check them against the clip with ``check_profile`` before running."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    audience: Literal["fan", "analyst"] = "fan"
    language: Literal["en", "ko"] = "en"
    my_club: str | None = None
    follow_player: str | None = None


def check_profile(profile: RecapProfile, view: View) -> None:
    """Raise ValueError when the club or player is not in this clip (an endpoint answers 422 with the message)."""
    meta = view.clip.meta
    teams = [t.team_id for t in meta.teams]
    if profile.my_club is not None and profile.my_club not in teams:
        raise ValueError(f"my_club {profile.my_club!r} is not a team in this clip; use one of {teams}")
    if profile.follow_player is not None and profile.follow_player not in {r.player_id for r in meta.roster}:
        raise ValueError(f"follow_player {profile.follow_player!r} is not in this clip's roster")


# ------------------------------------------------------------------ code-computed tags
def involved_players(packet: EvidencePacket) -> set[str]:
    """Players in a revision's evidence: any claim's players or any observed change's actors."""
    return {p for c in packet.claims for p in c.player_ids} | {p for ch in packet.changes for p in ch.actor_player_ids}


def involves(packet: EvidencePacket, player_id: str | None) -> bool:
    return player_id is not None and player_id in involved_players(packet)


def my_club_role(packet: EvidencePacket, profile: RecapProfile, view: View) -> str | None:
    """'defending' or 'attacking' for the viewer's club in this moment, None without a (known) club."""
    club = profile.my_club
    if club is None or club not in {t.team_id for t in view.clip.meta.teams}:
        return None
    return "defending" if club == packet.episode.defending_team_id else "attacking"


def _latest(view: View) -> list[EvidencePacket]:
    return [revs[-1] for revs in view.revisions.values()]


def followed_moments(view: View, profile: RecapProfile) -> list[str]:
    """Episode ids whose latest revision (as of the view) involves the followed player."""
    return sorted(p.episode.episode_id for p in _latest(view) if involves(p, profile.follow_player))


def _chronological(packet: EvidencePacket) -> tuple:
    return (packet.episode.detected_at_s, packet.episode.episode_id)


# ------------------------------------------------------------------ what the model chooses
class RecapItemChoice(BaseModel):
    episode_id: str = Field(description="a moment's episode_id from the moments list")
    option_ids: list[str] = Field(description="1-3 option ids that answer_options returned for this moment, in the "
                                              "order to tell them")


class RecapChoice(BaseModel):
    items: list[RecapItemChoice] = Field(description="1-3 different moments that tell the story so far for this "
                                                     "viewer; code tells them in replay order")


RECAP_INSTRUCTIONS = """You are the Recap agent of PitchIQ, a defensive-analysis tool on synthetic football tracking.
A viewer asks for a recap of the replay so far. You get the viewer's profile (fan or analyst, language, optionally the
club they support and a player they follow) and the defensive moments found up to now, each with what the defence
restricted, what it exposed, what changed next, the observed outcome, its story_priority, and two tags computed by
code: involves_followed (the followed player is in the moment's evidence) and my_club_role (whether the viewer's club
was defending or attacking in it). Choose the 1-3 moments that best tell the story of the replay so far for this
viewer: when any moment involves the followed player, include at least one of those and treat it as the most
important; prefer complete trade-offs and higher story_priority; use my_club_role to favour what matters to the
viewer's club. Before choosing ids for a moment, call answer_options for it, and choose 1-3 of the option ids it
returned in the order to tell them; get_evidence shows a moment's claims and measured values when you need them. When
an item about a complete trade-off uses a restricted or an exposed sentence, keep one of each side. Code tells the
chosen moments in replay order. Do not explain why anyone did anything. You never write sentences or numbers: the
chosen options are the only words shown. Answer with the JSON schema only."""


def recap_prompt(view: View, profile: RecapProfile) -> str:
    """What the agent sees: the profile and the moments known at the view's cutoff, with the code-computed tags."""
    moments = []
    for m in view.moments()["moments"]:
        p = view.packet(m["episode_id"])
        moments.append({**m, "involves_followed": involves(p, profile.follow_player),
                        "my_club_role": my_club_role(p, profile, view)})
    club = next((t for t in view.clip.meta.teams if t.team_id == profile.my_club), None)
    player = next((r for r in view.clip.meta.roster if r.player_id == profile.follow_player), None)
    body = {"audience": profile.audience, "language": profile.language,
            "my_club": {"team_id": club.team_id, "name": club.display_name} if club else None,
            "follow_player": ({"player_id": player.player_id, "team_id": player.team_id, "jersey": player.jersey}
                              if player else None),
            "followed_player_involved": any(m["involves_followed"] for m in moments),
            "replay_time_s": _rel(view.clip, view.t), "moments": moments}
    return json.dumps(body, ensure_ascii=False)


# ------------------------------------------------------------------ validation (code decides what may be shown)
Chosen = list[tuple[EvidencePacket, list[Option]]]


def validate_recap(choice: RecapChoice, view: View, profile: RecapProfile) -> Chosen:
    """The chosen moments with their options in replay order, or a PlanError saying what to fix."""
    items = choice.items
    if not 1 <= len(items) <= MAX_ITEMS:
        raise PlanError("item_count", f"choose 1-{MAX_ITEMS} moments, not {len(items)}")
    chosen: Chosen = []
    seen: set[str] = set()
    for item in items:
        eid = item.episode_id
        if eid not in view.revisions:
            raise PlanError("unknown_moment", f"{eid}: use an episode_id from the moments list")
        if eid in seen:
            raise PlanError("duplicate_moment", f"{eid} is chosen twice")
        seen.add(eid)
        ids = item.option_ids
        if not 1 <= len(ids) <= MAX_ITEM_SENTENCES:
            raise PlanError("sentence_count", f"{eid}: choose 1-{MAX_ITEM_SENTENCES} options, not {len(ids)}")
        if len(set(ids)) != len(ids):
            raise PlanError("duplicate_option", f"{eid}: {ids}")
        packet = view.packet(eid)
        options = {o.option_id: o for o in sentence_options(packet, view.clip.meta, profile.audience)}
        unknown = [i for i in ids if i not in options]
        if unknown:
            raise PlanError("unknown_option", f"{eid}: {unknown}: call answer_options for {eid}")
        picked = [options[i] for i in ids]
        sides = {o.kind for o in picked} & {"restricted", "exposed"}
        if packet.episode.analysis_status == "complete_tradeoff" and sides and sides != {"restricted", "exposed"}:
            raise PlanError("one_sided", f"{eid}: a complete trade-off keeps a restricted and an exposed sentence")
        chosen.append((packet, picked))
    followed = followed_moments(view, profile)
    if followed and not any(involves(p, profile.follow_player) for p, _ in chosen):
        raise PlanError("follow_player_missing", f"{profile.follow_player} is in {followed}: include one of them")
    return sorted(chosen, key=lambda c: _chronological(c[0]))


# ------------------------------------------------------------------ the recap shown (code renders it)
def _empty_sentences() -> dict[str, list[str]]:
    return {lang: [] for lang in LANGUAGES}


def render_recap(view: View, profile: RecapProfile, chosen: Chosen) -> dict:
    """The recap from validated (or deterministic) choices: option texts only, with code-computed tags."""
    if not chosen:
        return empty_recap(view)
    items = [{"episode_id": p.episode.episode_id, "episode_revision": p.episode.revision,
              "detected_at_s": _rel(view.clip, p.episode.detected_at_s), "evidence_hash": p.evidence_hash,
              "involves_followed": involves(p, profile.follow_player), "my_club_role": my_club_role(p, profile, view),
              "sentences": {lang: [o.text[lang] for o in opts] for lang in LANGUAGES}}
             for p, opts in sorted(chosen, key=lambda c: _chronological(c[0]))]
    return {"kind": "recap", "analyzed_through_s": _rel(view.clip, view.t), "items": items,
            "note_sentences": _empty_sentences()}


def empty_recap(view: View) -> dict:
    return {"kind": "empty", "analyzed_through_s": _rel(view.clip, view.t), "items": [],
            "note_sentences": {lang: [ASK_DECLINES["no_moment_yet"][lang]] for lang in LANGUAGES}}


# ------------------------------------------------------------------ the deterministic recap
_PREFERRED_KINDS = {"complete_tradeoff": ("restricted", "exposed", "outcome"),
                    "partial": ("restricted", "partial", "outcome")}


def _fallback_options(packet: EvidencePacket, options: list[Option]) -> list[Option]:
    """First restricted, first exposed and the outcome for a complete trade-off; first restricted, the partial note
    and the outcome otherwise; the first options available when none of those exist. At most three."""
    kinds = _PREFERRED_KINDS.get(packet.episode.analysis_status, _PREFERRED_KINDS["partial"])
    firsts = (next((o for o in options if o.kind == k), None) for k in kinds)
    picked = [o for o in firsts if o is not None]
    return (picked or options)[:MAX_ITEM_SENTENCES]


def fallback_choice(view: View, profile: RecapProfile) -> Chosen:
    """Up to three moments ranked by (involves the followed player, complete trade-off, story priority), each with
    its options picked by ``_fallback_options``; in replay order."""
    ranked = sorted(_latest(view), reverse=True,
                    key=lambda p: (involves(p, profile.follow_player), p.episode.analysis_status == "complete_tradeoff",
                                   p.episode.story_priority, p.episode.detected_at_s, p.episode.episode_id))
    chosen: Chosen = []
    for p in ranked:
        opts = _fallback_options(p, sentence_options(p, view.clip.meta, profile.audience))
        if opts:
            chosen.append((p, opts))
        if len(chosen) == MAX_ITEMS:
            break
    return sorted(chosen, key=lambda c: _chronological(c[0]))


def fallback_recap(view: View, profile: RecapProfile) -> dict:
    """The deterministic recap (or 'no moments yet'): what Recap shows when the agent cannot."""
    return render_recap(view, profile, fallback_choice(view, profile))


def as_choice(chosen: Chosen) -> RecapChoice:
    """The choice that would select these moments and options (used to check the fallback like a model's)."""
    return RecapChoice(items=[RecapItemChoice(episode_id=p.episode.episode_id, option_ids=[o.option_id for o in opts])
                              for p, opts in chosen])


# ------------------------------------------------------------------ run limits and trace
@dataclass(frozen=True)
class RecapLimits:
    deadline_s: float = 30.0  # the whole recap, tool calls included
    max_tool_calls: int = 8  # per recap


@dataclass
class RecapTrace:
    trace_id: str
    model: str
    steps: list[dict] = field(default_factory=list)  # gather, recap_agent, tool..., check, render | fallback
    runs: int = 0  # agent runs (0 or 1: no repair loop)
    tool_calls: int = 0
    usage: dict = field(default_factory=dict)
    result: str = "pending"  # foundry_live | fallback
    reason: str | None = None
    total_ms: float = 0.0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("trace_id", "model", "steps", "runs", "tool_calls", "usage", "result",
                                              "reason", "total_ms")}
