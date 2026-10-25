"""Insight workflow: Analyst -> validation -> Producer -> validation -> render (spec 12).

Offline (no Foundry settings) every revision uses the deterministic planner.
The Foundry gateway plugs in through the same validators; whatever it
returns is checked here and replaced by the deterministic plan if invalid.
"""

from __future__ import annotations

from ..contracts import EvidencePacket, InsightRevision, MatchMeta
from ..narrative.render import render_sentence
from .fallback import analyst_plan, producer_plan
from .validate import validate_analyst_plan, validate_producer_plan


class InsightService:
    def __init__(self) -> None:
        self._cache: dict[str, tuple] = {}  # evidence_hash -> (analyst, producer); immutable, safe to share

    def deterministic_revision(self, packet: EvidencePacket, revision: int) -> InsightRevision:
        h = packet.evidence_hash
        if h not in self._cache:
            a = analyst_plan(packet)
            validate_analyst_plan(a, packet)
            p = producer_plan(packet, a)
            validate_producer_plan(p, packet, a)
            self._cache[h] = (a, p)
        a, p = self._cache[h]
        ep = packet.episode
        return InsightRevision(insight_id=f"ins_{ep.episode_id}", revision=revision, episode_id=ep.episode_id,
                               episode_revision=ep.revision, evidence_hash=h, analyst_plan=a, producer_plan=p,
                               source_mode="deterministic_fallback", review_status="suggested",
                               generation_trace_id=None)


def render_variants(ins: InsightRevision, packet: EvidencePacket, meta: MatchMeta) -> dict[str, list[str]]:
    out = {}
    for v in ins.producer_plan.variants:
        out[f"{v.audience}_{v.language}"] = [render_sentence(s, packet, v.language, meta) for s in v.sentences]
    return out
