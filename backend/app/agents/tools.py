"""Read-only match tools for agents: the PitchIQ MCP server and any Foundry agent that calls it.

What the analysis knew about a clip at a moment, never later (spec 3: frame t sees only [t - W, t], events once
available). Each clip is analysed once, causally, exactly as a replay would; a question "at t" sees the episode
revisions whose cutoff is at or before t. Numbers, positions and claims come from the analyzer. Explanations are the
validated deterministic templates (spec 12): no tool lets a model write a number or a sentence.

Times in and out are seconds from the clip's start, as on the replay clock (``match_start_s`` gives the offset).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from ..analytics.engine import TacticalAnalyzer
from ..contracts import EvidencePacket
from ..replay.catalog import Clip
from ..replay.catalog import list_clips as _clip_ids
from ..replay.catalog import load_clip
from .workflow import InsightService, render_variants

_LOCK = threading.Lock()
_INSIGHTS = InsightService()


class ToolError(ValueError):
    """A request the tools cannot answer (unknown clip or moment); the message says what is valid."""


@lru_cache(maxsize=32)
def _analysed(clip_id: str) -> tuple[Clip, dict[str, list[EvidencePacket]]]:
    if clip_id not in _clip_ids():
        raise ToolError(f"unknown clip {clip_id!r}; call list_clips for the available ids")
    clip = load_clip(clip_id)
    an = TacticalAnalyzer(clip.meta, clip.clip_id)
    seen: set[str] = set()
    for f in clip.frames:
        arrived = [e for e in clip.events if e.available_at_s <= f.match_time_s + 1e-9 and e.event_id not in seen]
        seen |= {e.event_id for e in arrived}
        an.step(f, arrived)
    return clip, {eid: list(revs) for eid, revs in an.packets.items()}


def _rel(clip: Clip, t: float) -> float:
    return round(t - clip.start_s, 2)


def _team(clip: Clip, team_id: str) -> str:
    return next((tm.display_name for tm in clip.meta.teams if tm.team_id == team_id), team_id)


def _summary(clip: Clip, p: EvidencePacket) -> dict:
    ep, codes = p.episode, {c.claim_id: c.code for c in p.claims}
    return {"episode_id": ep.episode_id, "revision": ep.revision, "detected_at_s": _rel(clip, ep.detected_at_s),
            "analyzed_until_s": _rel(clip, ep.analyzed_until_s), "lifecycle": ep.lifecycle,
            "analysis_status": ep.analysis_status, "defending_team": _team(clip, ep.defending_team_id),
            "restricted": [codes[c] for c in ep.restricted_claim_ids],
            "exposed": [codes[c] for c in ep.exposed_claim_ids],
            "changes": [c.code for c in p.changes], "outcome": ep.outcome.status,
            "story_priority": ep.story_priority}


@dataclass(frozen=True)
class View:
    """What the analysis knew about one clip at match time ``t``: every episode revision whose cutoff is at or
    before t. Built from a whole-clip analysis (``view_at``) or from a running replay's analyzer; either way the
    cutoff is fixed by code, so nothing that reads a view can see later play."""
    clip: Clip
    t: float
    revisions: dict[str, list[EvidencePacket]]

    @classmethod
    def of(cls, clip: Clip, t: float, packets: dict[str, list[EvidencePacket]]) -> "View":
        known = {}
        for eid, revs in packets.items():
            seen = [p for p in revs if p.episode.analyzed_until_s <= t + 1e-9]
            if seen:
                known[eid] = seen
        return cls(clip, t, known)

    def packet(self, episode_id: str) -> EvidencePacket:
        if episode_id not in self.revisions:
            raise ToolError(f"no moment {episode_id!r} in {self.clip.clip_id} by {_rel(self.clip, self.t):.1f} s into "
                            "the clip; use an episode_id from defensive_moments")
        return self.revisions[episode_id][-1]

    def moments(self) -> dict:
        moments = sorted((_summary(self.clip, r[-1]) for r in self.revisions.values()),
                         key=lambda m: m["detected_at_s"])
        return {"analyzed_through_s": _rel(self.clip, self.t), "moments": moments}

    def evidence(self, episode_id: str) -> dict:
        clip, p = self.clip, self.packet(episode_id)
        metrics = {m.metric_id: m for m in p.metrics}
        claims = [{"claim_id": c.claim_id, "code": c.code, "dimension": c.dimension, "epistemic": c.epistemic,
                   "support_score": c.support_score, "player_ids": c.player_ids,
                   "metrics": [{"key": metrics[m].key, "value": metrics[m].value, "unit": metrics[m].unit,
                                "status": metrics[m].status} for m in c.metric_ids if m in metrics]}
                  for c in p.claims]
        changes = [{"code": c.code, "relation": c.relation, "observed_at_s": _rel(clip, c.observed_at_s),
                    "team": _team(clip, c.actor_team_id), "player_ids": c.actor_player_ids} for c in p.changes]
        return {**_summary(clip, p), "analyzed_through_s": _rel(clip, self.t), "evidence_hash": p.evidence_hash,
                "claims": claims, "changes": changes,
                "not_claimed": sorted({f.split(":")[0] for f in p.forbidden_inferences if ":" in f})}

    def explain(self, episode_id: str, audience: str, language: str) -> dict:
        if audience not in ("fan", "analyst") or language not in ("en", "ko"):
            raise ToolError("audience is 'fan' or 'analyst'; language is 'en' or 'ko'")
        p = self.packet(episode_id)
        ins = _INSIGHTS.deterministic_revision(p, 1)
        return {"episode_id": episode_id, "revision": p.episode.revision,
                "analyzed_through_s": _rel(self.clip, self.t), "audience": audience, "language": language,
                "sentences": render_variants(ins, p, self.clip.meta)[f"{audience}_{language}"],
                "source": "deterministic templates, validated against the evidence", "evidence_hash": p.evidence_hash}

    def history(self, episode_id: str) -> dict:
        self.packet(episode_id)
        return {"episode_id": episode_id, "analyzed_through_s": _rel(self.clip, self.t),
                "revisions": [_summary(self.clip, p) for p in self.revisions[episode_id]]}


def view_at(clip_id: str, at_s: float | None = None) -> View:
    """A committed clip as known ``at_s`` seconds into it (default: its end)."""
    with _LOCK:  # one analysis per clip, shared by every caller
        clip, packets = _analysed(clip_id)
    t = clip.end_s if at_s is None else min(max(clip.start_s + float(at_s), clip.start_s), clip.end_s)
    return View.of(clip, t, packets)


# ------------------------------------------------------------------ tools
def list_clips() -> list[dict]:
    """The synthetic clips available: id, teams, length in seconds and where the clip sits in its match."""
    out = []
    for cid in _clip_ids():
        clip = load_clip(cid)
        out.append({"clip_id": cid, "duration_s": _rel(clip, clip.end_s), "match_start_s": clip.start_s,
                    "teams": [tm.display_name for tm in clip.meta.teams]})
    return out


def defensive_moments(clip_id: str, at_s: float | None = None) -> dict:
    """The defensive trade-off moments the analysis had found in a clip by ``at_s`` seconds into it (default: the
    clip's end): what each defence restricted, what it exposed, what changed next and what the synthetic play did."""
    return {"clip_id": clip_id, **view_at(clip_id, at_s).moments()}


def moment_evidence(clip_id: str, episode_id: str, at_s: float | None = None) -> dict:
    """The evidence behind one moment as known by ``at_s``: each claim with its measured metrics and players, the
    observed changes, the outcome, and the inferences the analysis does not make."""
    return view_at(clip_id, at_s).evidence(episode_id)


def explain_moment(clip_id: str, episode_id: str, audience: Literal["fan", "analyst"] = "fan",
                   language: Literal["en", "ko"] = "en", at_s: float | None = None) -> dict:
    """A validated explanation of one moment as known by ``at_s``, for a fan or an analyst, in English or Korean.
    The sentences are the registry templates bound to the evidence's own numbers (no generated text)."""
    if audience not in ("fan", "analyst") or language not in ("en", "ko"):
        raise ToolError("audience is 'fan' or 'analyst'; language is 'en' or 'ko'")
    return view_at(clip_id, at_s).explain(episode_id, audience, language)


def moment_history(clip_id: str, episode_id: str, at_s: float | None = None) -> dict:
    """How one moment's reading evolved up to ``at_s``: every revision with its status, claims, changes and outcome.
    A later outcome is a new revision; earlier ones are never rewritten."""
    return {"clip_id": clip_id, **view_at(clip_id, at_s).history(episode_id)}


TOOLS = (list_clips, defensive_moments, moment_evidence, explain_moment, moment_history)
