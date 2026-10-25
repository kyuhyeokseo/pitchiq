"""What the Analyst and Producer models choose, and how a choice becomes a checkable plan (spec 12.1).

The models only choose: which claims to focus on and in what order, which changes to tell, what to omit, which
caveats apply, and which sentence options each audience gets (both languages tell the same sentences, as in the
deterministic plan). Sentence options are registry templates that code has already bound to this packet's claims,
changes and metrics and validated, named by short ids (``s1``, ``s2``...); a short id keeps the model's output
small, which is most of a reasoning model's latency. The models never write numbers, ids or sentences. Code binds
every chosen template to the packet's own metric and event ids, and the same validators as the deterministic plans
decide what may be shown. No SDK types here (the gateway owns those).
"""

from __future__ import annotations

import json

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from dataclasses import dataclass

from ..contracts import AnalystPlan, EvidencePacket, ProducerPlan, ProducerVariant, SentencePlan
from ..contracts.registry import LAYERS
from ..narrative.registry import TEMPLATES
from .validate import PlanError, caveat_conditions, check_sentence

PROMPT_VERSION = "prompts-0.2.0"
AUDIENCES = ("fan", "analyst")
LANGUAGES = ("en", "ko")


# ------------------------------------------------------------------ model output schemas
class AnalystChoice(BaseModel):
    focus_claim_ids: list[str] = Field(min_length=1, description="claims to tell, most important first")
    ordered_change_ids: list[str] = Field(description="observed changes to tell, in this order")
    omitted_claim_ids: list[str] = Field(description="claims deliberately left out")
    caveat_codes: list[str] = Field(description="caveats that apply, from the allowed list only")


class SentenceChoice(BaseModel):
    template_key: str
    claim_id: str | None = None
    change_id: str | None = None


OptionId = Annotated[str, StringConstraints(pattern=r"^s[0-9]{1,2}$")]


class AudienceChoice(BaseModel):
    option_ids: list[OptionId] = Field(min_length=1, max_length=6,
                                       description="at most 6 option ids from this audience's list, one id per item, "
                                                   "in telling order")
    focus_layers: list[str] = Field(min_length=1)


class ProducerChoice(BaseModel):
    fan: AudienceChoice
    analyst: AudienceChoice


# ------------------------------------------------------------------ prompts
ANALYST_INSTRUCTIONS = """You are the Analyst for PitchIQ, a defensive-analysis tool on synthetic football tracking.
You receive one evidence packet: claims about what a defensive structure restricts and what it exposes, measured
metrics, observed changes and the observed outcome. Decide the story: which claims to focus on (restricting side
first, then the exposure it pairs with), which observed changes to tell and in what order, which claims to omit,
and which caveats apply. Use only ids that appear in the packet and caveat codes from the allowed list. If the
status is complete_tradeoff, keep at least one restricted and one exposed claim in focus. Do not infer intent, do
not compute new numbers, do not describe outcomes that were not observed. Answer with the JSON schema only."""

PRODUCER_INSTRUCTIONS = """You are the Producer for PitchIQ. Using the evidence packet and the Analyst's plan, choose
what each audience is told: for fan and for analyst, 1-6 sentence option ids from that audience's own list (at most
6: leave the rest out; each array item is exactly one id as listed), in telling order, and the focus layers to draw
(from the allowed layers). Both languages tell the same sentences. Fans get the trade-off in 3-5 sentences; analysts
get the strongest evidence and the heuristic caveat. Keep both sides of a complete
trade-off for each audience (a restricted and an exposed option), follow the Analyst's order of changes, and tell
the observed outcome when there is one. You never write sentences or numbers yourself: the options are already
filled with the packet's metrics. Answer with the JSON schema only."""


def packet_brief(packet: EvidencePacket) -> dict:
    """The packet as the models see it: ids, codes, dimensions, measured values (for relevance only)."""
    metrics = {m.metric_id: m for m in packet.metrics}
    ep = packet.episode
    return {
        "episode_id": ep.episode_id, "evidence_hash": packet.evidence_hash, "analysis_status": ep.analysis_status,
        "lifecycle": ep.lifecycle,
        "outcome": ep.outcome.status,
        "claims": [{"claim_id": c.claim_id, "code": c.code, "dimension": c.dimension,
                    "support_score": c.support_score,
                    "metrics": {metrics[m].key: metrics[m].value for m in c.metric_ids if m in metrics}}
                   for c in packet.claims],
        "changes": [{"change_id": ch.change_id, "code": ch.code, "relation": ch.relation,
                     "observed_at_s": ch.observed_at_s} for ch in packet.changes],
        "not_claimed": sorted({f for f in packet.forbidden_inferences if ":" in f}),
    }


@dataclass(frozen=True)
class Candidate:
    option_id: str
    kind: str  # restricted | exposed | context (claim sentences) | change | outcome | caveat | partial
    sentence: SentencePlan


def sentence_candidates(packet: EvidencePacket, audience: str) -> list[Candidate]:
    """Every registry sentence this audience may say about this packet, bound to the packet's own claims, changes
    and metrics and valid in both languages. Ids follow registry order, so they are the same every time."""
    out: list[Candidate] = []
    for key, t in TEMPLATES.items():
        if audience not in t.audience:
            continue
        kind, _, target = t.attach.partition(":")
        if kind == "claim":
            cands = [SentenceChoice(template_key=key, claim_id=c.claim_id) for c in packet.claims if c.code == target]
        elif kind == "change":
            cands = [SentenceChoice(template_key=key, change_id=c.change_id) for c in packet.changes
                     if c.code == target]
        else:
            cands = [SentenceChoice(template_key=key)]
        for choice in cands:
            sp = bind_sentence(choice, packet)
            try:
                dims = {check_sentence(sp, audience, lang, packet) for lang in LANGUAGES}
            except PlanError:
                continue
            out.append(Candidate(f"s{len(out) + 1}", dims.pop() or kind, sp))
    return out


def _option_brief(c: Candidate) -> dict:
    s = c.sentence
    return {"option_id": c.option_id, "kind": c.kind, "about": s.claim_id or s.change_id,
            "template_en": TEMPLATES[s.template_key].text["en"]}


def analyst_prompt(packet: EvidencePacket) -> str:
    return json.dumps({"packet": packet_brief(packet),
                       "allowed_caveat_codes": sorted(caveat_conditions(packet))}, ensure_ascii=False)


def producer_prompt(packet: EvidencePacket, analyst: AnalystPlan, feedback: str | None = None) -> str:
    focus = set(analyst.focus_claim_ids)
    options = {aud: [_option_brief(c) for c in sentence_candidates(packet, aud)
                     if c.sentence.claim_id is None or c.sentence.claim_id in focus] for aud in AUDIENCES}
    body = {"packet": packet_brief(packet), "analyst_plan": analyst.model_dump(mode="json"),
            "sentence_options": options, "allowed_layers": list(LAYERS)}
    if feedback:
        body["previous_attempt_rejected_because"] = feedback
    return json.dumps(body, ensure_ascii=False)


def analyst_repair_prompt(packet: EvidencePacket, feedback: str) -> str:
    return json.dumps({"packet": packet_brief(packet), "allowed_caveat_codes": sorted(caveat_conditions(packet)),
                       "previous_attempt_rejected_because": feedback}, ensure_ascii=False)


# ------------------------------------------------------------------ binding (code, not the model)
def bind_analyst(choice: AnalystChoice, packet: EvidencePacket) -> AnalystPlan:
    ep = packet.episode
    return AnalystPlan(episode_id=ep.episode_id, evidence_hash=packet.evidence_hash,
                       focus_claim_ids=list(choice.focus_claim_ids),
                       restricted_claim_ids=list(ep.restricted_claim_ids), exposed_claim_ids=list(ep.exposed_claim_ids),
                       ordered_change_ids=list(choice.ordered_change_ids),
                       omitted_claim_ids=list(choice.omitted_claim_ids), caveat_codes=list(choice.caveat_codes))


def bind_sentence(s: SentenceChoice, packet: EvidencePacket) -> SentencePlan:
    """A template choice with the packet's metric and event ids it reads, picked by code."""
    t = TEMPLATES.get(s.template_key)
    metric_ids: list[str] = []
    event_ids: list[str] = []
    if t is not None:
        kind = t.attach.partition(":")[0]
        if kind == "claim" and s.claim_id:
            claim = next((c for c in packet.claims if c.claim_id == s.claim_id), None)
            if claim is not None:
                seen: set[str] = set()
                for mid in claim.metric_ids:  # first metric per key the template reads
                    key = packet.metric(mid).key
                    if key in t.metric_keys and key not in seen and packet.metric(mid).value is not None:
                        metric_ids.append(mid)
                        seen.add(key)
        elif kind == "change" and s.change_id:
            ch = next((c for c in packet.changes if c.change_id == s.change_id), None)
            if ch is not None:
                metric_ids = list(ch.metric_ids)
        elif kind == "outcome":
            event_ids = list(packet.episode.outcome.event_ids)
    return SentencePlan(template_key=s.template_key, claim_id=s.claim_id, change_id=s.change_id,
                        metric_ids=metric_ids, event_ids=event_ids)


def bind_producer(choice: ProducerChoice, packet: EvidencePacket) -> ProducerPlan:
    variants = []
    for audience in AUDIENCES:
        pick = getattr(choice, audience)
        cands = {c.option_id: c for c in sentence_candidates(packet, audience)}
        unknown = [i for i in pick.option_ids if i not in cands]
        if unknown:
            raise PlanError("unknown_option", f"{audience}: {unknown} are not in its sentence_options")
        if len(set(pick.option_ids)) != len(pick.option_ids):
            raise PlanError("duplicate_option", f"{audience}: {pick.option_ids}")
        sentences = [cands[i].sentence for i in pick.option_ids]
        variants += [ProducerVariant(audience=audience, language=lang, sentences=sentences,
                                     focus_layers=list(dict.fromkeys(pick.focus_layers))) for lang in LANGUAGES]
    return ProducerPlan(episode_id=packet.episode.episode_id, evidence_hash=packet.evidence_hash, variants=variants)
