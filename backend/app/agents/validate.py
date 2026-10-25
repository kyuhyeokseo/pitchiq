"""Code validators for Analyst and Producer plans (spec 11.1, 12.1).

The validator, not the model's confidence, decides what may be shown. A plan
may only reference ids that exist in the packet, templates from the registry
that fit the audience/language and what they are attached to, and outcome or
caveat sentences that match the packet's current state.
"""

from __future__ import annotations

from ..contracts import AnalystPlan, EvidencePacket, ProducerPlan, SentencePlan
from ..contracts.registry import LAYERS
from ..narrative.registry import TEMPLATES

CAVEAT_CODES = ("heuristic", "cover_in_time", "outcome_pending", "partial_analysis")


class PlanError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def caveat_conditions(packet: EvidencePacket) -> set[str]:
    out = {"heuristic"}
    if any("cover_in_time" in f for f in packet.forbidden_inferences):
        out.add("cover_in_time")
    if packet.episode.outcome.status == "not_yet_observed":
        out.add("outcome_pending")
    if packet.episode.analysis_status == "partial":
        out.add("partial_analysis")
    return out


def validate_analyst_plan(plan: AnalystPlan, packet: EvidencePacket) -> None:
    ep = packet.episode
    if plan.episode_id != ep.episode_id or plan.evidence_hash != packet.evidence_hash:
        raise PlanError("stale_evidence", "plan refers to another episode or evidence hash")
    claims = {c.claim_id: c for c in packet.claims}
    for cid in plan.focus_claim_ids + plan.restricted_claim_ids + plan.exposed_claim_ids + plan.omitted_claim_ids:
        if cid not in claims:
            raise PlanError("unknown_claim", cid)
    if set(plan.restricted_claim_ids) - set(ep.restricted_claim_ids):
        raise PlanError("wrong_dimension", "a claim listed as restricted is not restricted in the packet")
    if set(plan.exposed_claim_ids) - set(ep.exposed_claim_ids):
        raise PlanError("wrong_dimension", "a claim listed as exposed is not exposed in the packet")
    if set(plan.focus_claim_ids) & set(plan.omitted_claim_ids):
        raise PlanError("focus_and_omitted", "a claim cannot be both focused and omitted")
    if ep.analysis_status == "complete_tradeoff":
        focus = set(plan.focus_claim_ids)
        if not (focus & set(ep.restricted_claim_ids) and focus & set(ep.exposed_claim_ids)):
            raise PlanError("one_sided", "a complete trade-off must keep a restricted and an exposed claim in focus")
    if set(plan.ordered_change_ids) - {c.change_id for c in packet.changes}:
        raise PlanError("unknown_change", str(plan.ordered_change_ids))
    allowed = caveat_conditions(packet)
    for code in plan.caveat_codes:
        if code not in CAVEAT_CODES:
            raise PlanError("unknown_caveat", code)
        if code not in allowed:
            raise PlanError("caveat_not_applicable", code)


def check_sentence(s: SentencePlan, audience: str, language: str, packet: EvidencePacket,
                   focus: set[str] | None = None) -> str | None:
    """One sentence against the packet: a registry template for this audience and language, attached to a claim,
    change, outcome or caveat that is really there, reading only its own metrics. With ``focus``, a claim sentence
    must name a focused claim. Returns the claim's dimension for a claim sentence, else None."""
    ep = packet.episode
    t = TEMPLATES.get(s.template_key)
    if t is None:
        raise PlanError("unknown_template", s.template_key)
    if audience not in t.audience or language not in t.text:
        raise PlanError("template_audience", f"{s.template_key} for {audience}/{language}")
    claims = {c.claim_id: c for c in packet.claims}
    changes = {c.change_id: c for c in packet.changes}
    metrics = {m.metric_id: m for m in packet.metrics}
    dim = None
    kind, _, target = t.attach.partition(":")
    if kind == "claim":
        c = claims.get(s.claim_id or "")
        if c is None or c.code != target:
            raise PlanError("template_claim", f"{s.template_key} needs a {target} claim")
        if focus is not None and c.claim_id not in focus:
            raise PlanError("unfocused_claim", c.claim_id)
        dim = c.dimension
    elif kind == "change":
        ch = changes.get(s.change_id or "")
        if ch is None or ch.code != target:
            raise PlanError("template_change", f"{s.template_key} needs a {target} change")
    elif kind == "outcome":
        if ep.outcome.status != target:
            raise PlanError("outcome_mismatch", f"{s.template_key} but outcome is {ep.outcome.status}")
    elif kind == "caveat":
        if target not in caveat_conditions(packet):
            raise PlanError("caveat_not_applicable", s.template_key)
    elif kind == "partial":
        if ep.analysis_status != "partial":
            raise PlanError("not_partial", s.template_key)
    for mid in s.metric_ids:
        if mid not in metrics:
            raise PlanError("unknown_metric", mid)
    if kind == "claim" and not set(s.metric_ids) <= set(claims[s.claim_id].metric_ids):
        raise PlanError("foreign_metric", f"{s.template_key} uses metrics outside its claim")
    if kind == "change" and not set(s.metric_ids) <= set(changes[s.change_id].metric_ids):
        raise PlanError("foreign_metric", f"{s.template_key} uses metrics outside its change")
    keys = {metrics[mid].key for mid in s.metric_ids}
    if set(t.metric_keys) - keys:
        raise PlanError("missing_metric", f"{s.template_key} needs {sorted(set(t.metric_keys) - keys)}")
    if t.requires is not None and not t.requires({metrics[mid].key: metrics[mid].value for mid in s.metric_ids}):
        raise PlanError("template_condition", f"{s.template_key} does not fit these values")
    if set(s.event_ids) - {e.event_id for e in packet.events}:
        raise PlanError("unknown_event", str(s.event_ids))
    return dim


def validate_producer_plan(plan: ProducerPlan, packet: EvidencePacket, analyst: AnalystPlan) -> None:
    ep = packet.episode
    if plan.episode_id != ep.episode_id or plan.evidence_hash != packet.evidence_hash:
        raise PlanError("stale_evidence", "producer plan refers to another episode or evidence hash")
    focus = set(analyst.focus_claim_ids)
    for v in plan.variants:
        dims = {check_sentence(s, v.audience, v.language, packet, focus) for s in v.sentences}
        if ep.analysis_status == "complete_tradeoff" and not {"restricted", "exposed"} <= dims:
            raise PlanError("one_sided", f"{v.audience}/{v.language} drops one side of the trade-off")
        for layer in v.focus_layers:
            if layer not in LAYERS:
                raise PlanError("unknown_layer", layer)
