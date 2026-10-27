"""Deterministic Analyst/Producer plans: the immediate fallback when Foundry is off, slow or invalid."""

from __future__ import annotations

from ..contracts import AnalystPlan, EvidencePacket, ProducerPlan, ProducerVariant, SentencePlan
from ..narrative.registry import TEMPLATES
from .validate import caveat_conditions

OUTCOME_TEMPLATE = {
    "exploited_in_synthetic_play": "outcome.exploited",
    "not_exploited_in_window": "outcome.not_exploited",
    "not_yet_observed": "outcome.pending",
    "inconclusive": "outcome.inconclusive",
}
CLAIM_ORDER = ("short_options_screened", "central_routes_screened", "behind_depth_reduced", "behind_space_exposed",
               "isolated_1v1", "press_block_disconnected", "between_lines_receiver", "wide_outlet_available",
               "accessible_free_receiver", "local_central_overload", "weak_side_accessible")


def analyst_plan(packet: EvidencePacket) -> AnalystPlan:
    ep = packet.episode
    by_id = {c.claim_id: c for c in packet.claims}
    focus = sorted(ep.restricted_claim_ids + ep.exposed_claim_ids + ep.context_claim_ids,
                   key=lambda c: CLAIM_ORDER.index(by_id[c].code))
    caveats = ["heuristic"] + [c for c in ("cover_in_time", "outcome_pending", "partial_analysis")
                               if c in caveat_conditions(packet)]
    return AnalystPlan(episode_id=ep.episode_id, evidence_hash=packet.evidence_hash,
                       focus_claim_ids=focus or [c.claim_id for c in packet.claims][:1],
                       restricted_claim_ids=list(ep.restricted_claim_ids), exposed_claim_ids=list(ep.exposed_claim_ids),
                       ordered_change_ids=[c.change_id for c in packet.changes],
                       omitted_claim_ids=sorted(set(by_id) - set(focus)), caveat_codes=caveats)


def _claim_sentence(packet: EvidencePacket, claim_id: str, template_key: str) -> SentencePlan | None:
    t = TEMPLATES[template_key]
    claim = packet.claim(claim_id)
    mids = [mid for mid in claim.metric_ids if packet.metric(mid).key in t.metric_keys and packet.metric(
        mid).value is not None]
    if {packet.metric(m).key for m in mids} != set(t.metric_keys):
        return None
    if t.requires is not None and not t.requires({packet.metric(m).key: packet.metric(m).value for m in mids}):
        return None
    return SentencePlan(template_key=template_key, claim_id=claim_id, change_id=None, metric_ids=mids, event_ids=[])


EXTRA = {
    "analyst": {"short_options_screened": ["analyst.passive_screen.short", "analyst.press_cut.short",
                                           "analyst.press_numbers.short",
                                           "analyst.short_options_press", "analyst.zone_marking"],
                "central_routes_screened": ["analyst.press_cut.central", "analyst.press_numbers.central",
                                            "analyst.central_press"]},
    "fan": {"short_options_screened": ["fan.passive_screen.short", "fan.press_cut.short", "fan.press_numbers.short"],
            "central_routes_screened": ["fan.press_cut.central", "fan.press_numbers.central"]},
}
# by change code, or by the change's metric where one code covers two movements (block_shift: midfield drop in B,
# back line stepping up in G)
CHANGE_TEMPLATE = {"line_retreat": "change.line_retreat", "back_line_step_m": "change.line_step_up"}


def _sentences(packet: EvidencePacket, plan: AnalystPlan, audience: str) -> list[SentencePlan]:
    by_id = {c.claim_id: c for c in packet.claims}
    main: list[SentencePlan] = []
    extra: list[SentencePlan] = []
    sides_done: set[str] = set()
    for cid in plan.focus_claim_ids:
        claim = by_id[cid]
        if audience == "fan" and (claim.dimension in sides_done or claim.dimension == "context"):
            continue  # fans get one restricting and one exposing sentence: the trade-off, not the inventory
        s = _claim_sentence(packet, cid, f"{audience}.{claim.code}") if f"{audience}.{claim.code}" in TEMPLATES else None
        if s is not None:
            main.append(s)
            sides_done.add(claim.dimension)
        for key in EXTRA[audience].get(claim.code, []):
            e = _claim_sentence(packet, cid, key)
            if e is not None:
                extra.append(e)
    tail: list[SentencePlan] = []
    if packet.episode.analysis_status == "partial":
        tail.append(SentencePlan(template_key="partial.restricted_only", claim_id=None, change_id=None, metric_ids=[],
                                 event_ids=[]))
    if "cover_in_time" in plan.caveat_codes:
        tail.append(SentencePlan(template_key="caveat.cover_in_time", claim_id=None, change_id=None, metric_ids=[],
                                 event_ids=[]))
    changes: list[SentencePlan] = []
    for ch in packet.changes[: (2 if audience == "fan" else 4)]:
        mkey = packet.metric(ch.metric_ids[0]).key if ch.metric_ids else None
        key = CHANGE_TEMPLATE.get(mkey) or CHANGE_TEMPLATE.get(ch.code, f"change.{ch.code}")
        if audience == "analyst" and f"analyst.{key}" in TEMPLATES:  # the analyst wording keeps the distance
            key = f"analyst.{key}"
        if key in TEMPLATES and audience in TEMPLATES[key].audience:
            changes.append(SentencePlan(template_key=key, claim_id=None, change_id=ch.change_id,
                                        metric_ids=list(ch.metric_ids), event_ids=[]))
    oc = packet.episode.outcome
    outcome = []
    if packet.episode.exposed_claim_ids:
        outcome.append(SentencePlan(template_key=OUTCOME_TEMPLATE[oc.status], claim_id=None, change_id=None,
                                    metric_ids=[], event_ids=list(oc.event_ids)))
    # what happened next is told in the order it was observed (the order of the revisions): a defence can step up
    # only after the pass arrived
    at = {c.change_id: c.observed_at_s for c in packet.changes}
    ev = {e.event_id: e.available_at_s for e in packet.events}
    def when(s: SentencePlan) -> float:
        if s.change_id is not None:
            return at[s.change_id]
        times = [ev[e] for e in s.event_ids if e in ev]
        return max(times) if times else float("inf")  # the outcome is complete when the reception is known
    caveat = [SentencePlan(template_key="caveat.heuristic", claim_id=None, change_id=None, metric_ids=[],
                           event_ids=[])] if audience == "analyst" else []
    # priority when trimming to 6: the trade-off itself, then what happened next, then supporting detail
    ordered = main + tail + sorted(changes + outcome, key=when)
    room = 6 - len(ordered) - len(caveat)
    keep_extra = extra[:max(room, 0)]
    if room < 0:
        ordered = main + tail + sorted(changes[: max(0, 6 - len(main) - len(tail) - len(outcome))] + outcome, key=when)
        caveat = []
    pos = len(main)
    return (ordered[:pos] + keep_extra + ordered[pos:] + caveat)[:6]


FOCUS_LAYERS = {
    "fan": ["pressure", "screened_lanes", "exposure", "caption"],
    "analyst": ["structure", "marking", "pressure", "screened_lanes", "outlets", "exposure", "cover", "numbers",
                "changes", "caption"],
}


def producer_plan(packet: EvidencePacket, plan: AnalystPlan) -> ProducerPlan:
    variants = []
    for audience in ("fan", "analyst"):
        sents = _sentences(packet, plan, audience)
        for language in ("en", "ko"):
            variants.append(ProducerVariant(audience=audience, language=language, sentences=sents,
                                            focus_layers=FOCUS_LAYERS[audience]))
    return ProducerPlan(episode_id=plan.episode_id, evidence_hash=plan.evidence_hash, variants=variants)


def _common_order(a: list, b: list) -> int:
    """Length of the longest common subsequence: how many shared sentences keep their relative order."""
    best = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, 1):
            prev, best[j] = best[j], (prev + 1 if x == y else max(best[j], best[j - 1]))
    return best[-1]


def changes_from_rules(packet: EvidencePacket, analyst: AnalystPlan, producer: ProducerPlan) -> dict:
    """What a cloud plan changed against the deterministic plans for the same evidence, computed in code: the claim
    the Analyst led with, the claims and caveats it added or left out, and per audience the sentences kept, dropped,
    added and moved. Both languages share the Producer's sentences, so English stands for both."""
    code = {c.claim_id: c.code for c in packet.claims}
    rules_a = analyst_plan(packet)
    rules_p = producer_plan(packet, rules_a)
    focus, rules_focus = analyst.focus_claim_ids, rules_a.focus_claim_ids
    out = {"lead": {"agents": code.get(focus[0]), "rules": code.get(rules_focus[0])},
           "left_out": sorted({code[c] for c in rules_focus if c not in focus and c in code}),
           "added": sorted({code[c] for c in focus if c not in rules_focus and c in code}),
           "caveats_added": [c for c in analyst.caveat_codes if c not in rules_a.caveat_codes],
           "caveats_dropped": [c for c in rules_a.caveat_codes if c not in analyst.caveat_codes],
           "sentences": {}}
    key = lambda s: (s.template_key, s.claim_id, s.change_id)  # noqa: E731
    for audience in ("fan", "analyst"):
        mine = [key(s) for s in producer.variant(audience, "en").sentences]
        rules = [key(s) for s in rules_p.variant(audience, "en").sentences]
        kept_mine = [k for k in mine if k in rules]
        kept_rules = [k for k in rules if k in mine]
        out["sentences"][audience] = {"kept": len(kept_mine), "dropped": sum(k not in mine for k in rules),
                                      "added": sum(k not in rules for k in mine),
                                      "moved": len(kept_mine) - _common_order(kept_mine, kept_rules)}
    out["same_as_rules"] = (not out["left_out"] and not out["added"] and out["lead"]["agents"] == out["lead"]["rules"]
                            and not out["caveats_added"] and not out["caveats_dropped"]
                            and all(v["dropped"] == v["added"] == v["moved"] == 0 for v in out["sentences"].values()))
    return out
