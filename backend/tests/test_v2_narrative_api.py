"""Plans, validators, rendering and the replay API (spec 11.1, 12, 13.2, 14, 17.1)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.agents.fallback import analyst_plan, changes_from_rules, producer_plan
from app.agents.validate import PlanError, validate_analyst_plan, validate_producer_plan
from app.agents.workflow import render_variants
from app.api.app import STORE, app
from app.contracts import SentencePlan
from app.narrative.registry import FORMAT, TEMPLATES
from app.narrative.render import render_sentence
from app.overlay.builder import key_state
from app.replay.catalog import load_clip

from .v2_helpers import PUBLIC, load_public, run_analyzer


@pytest.fixture(scope="module")
def packets():
    out = []
    for cid in ("clip_001", "clip_004"):
        meta, frames, events = load_public(PUBLIC / cid)
        an = run_analyzer(meta, frames, events)
        out += [(meta, p) for revs in an.packets.values() for p in revs]
    return out


def test_fallback_plans_validate_for_every_packet(packets):
    for meta, p in packets:
        a = analyst_plan(p)
        validate_analyst_plan(a, p)
        pp = producer_plan(p, a)
        validate_producer_plan(pp, p, a)


def test_rendered_numbers_come_from_packet_metrics(packets):
    for meta, p in packets:
        a = analyst_plan(p)
        pp = producer_plan(p, a)
        allowed = {FORMAT[m.unit].format(m.value) for m in p.metrics if m.value is not None}
        allowed |= {s.lstrip("+-") for s in allowed}  # a magnitude ({a:...}) of a packet metric is still that metric
        for v in pp.variants:
            for s in v.sentences:
                from app.narrative.render import render_sentence
                text = render_sentence(s, p, v.language, meta)
                num_re = r"(?<![#\w])[+-]?\d+(?:\.\d+)?%?"
                literal = set(re.findall(num_re, re.sub(r"\{[^}]*\}", " ", TEMPLATES[s.template_key].text[v.language])))
                for num in re.findall(num_re, text):
                    assert num in allowed or num.lstrip("+") in allowed or num in literal, (num, text)


def test_texts_avoid_intent_and_probability_language(packets):
    banned = re.compile(r"\b(intend|intention|deliberate|forced|will score|probability|likely to)\b", re.I)
    for meta, p in packets:
        a = analyst_plan(p)
        pp = producer_plan(p, a)
        from app.contracts import InsightRevision
        ins = InsightRevision(insight_id="i", revision=1, episode_id=p.episode.episode_id,
                              episode_revision=p.episode.revision, evidence_hash=p.evidence_hash, analyst_plan=a,
                              producer_plan=pp, source_mode="deterministic_fallback", review_status="suggested",
                              generation_trace_id=None)
        for lines in render_variants(ins, p, meta).values():
            for line in lines:
                assert not banned.search(line), line


def _complete(packets):
    return next(p for _, p in packets if p.episode.analysis_status == "complete_tradeoff")


@pytest.mark.parametrize("break_it,code", [
    (lambda a, pp, p: a.model_copy(update={"focus_claim_ids": ["nope"]}), "unknown_claim"),
    (lambda a, pp, p: a.model_copy(update={"focus_claim_ids": p.episode.restricted_claim_ids}), "one_sided"),
    (lambda a, pp, p: a.model_copy(update={"evidence_hash": "sha256:" + "f" * 64}), "stale_evidence"),
    (lambda a, pp, p: a.model_copy(update={"caveat_codes": ["cover_in_time"]}), "caveat_not_applicable"),
])
def test_analyst_validator_rejects_bad_plans(packets, break_it, code):
    p = _complete(packets)
    a = analyst_plan(p)
    with pytest.raises(PlanError) as e:
        validate_analyst_plan(break_it(a, None, p), p)
    assert e.value.code == code


def _swap_sentence(pp, i, j, sentence):
    variants = [v.model_copy() for v in pp.variants]
    sents = list(variants[i].sentences)
    sents[j] = sentence
    variants[i] = variants[i].model_copy(update={"sentences": sents})
    return pp.model_copy(update={"variants": variants})


def test_producer_validator_rejects_bad_sentences(packets):
    p = _complete(packets)
    a = analyst_plan(p)
    pp = producer_plan(p, a)
    validate_producer_plan(pp, p, a)
    restricted = p.claim(p.episode.restricted_claim_ids[0])
    exposed = p.claim(p.episode.exposed_claim_ids[0])
    cases = {
        "template_audience": SentencePlan(template_key="analyst.behind_space_exposed", claim_id=exposed.claim_id,
                                          change_id=None, metric_ids=exposed.metric_ids, event_ids=[]),
        "outcome_mismatch": SentencePlan(template_key="outcome.pending" if p.episode.outcome.status != "not_yet_observed"
                                         else "outcome.exploited", claim_id=None, change_id=None, metric_ids=[],
                                         event_ids=[]),
        "foreign_metric": SentencePlan(template_key="fan.short_options_screened", claim_id=restricted.claim_id,
                                       change_id=None, metric_ids=restricted.metric_ids + exposed.metric_ids[:1],
                                       event_ids=[]),
        "template_claim": SentencePlan(template_key="fan.short_options_screened", claim_id=exposed.claim_id,
                                       change_id=None, metric_ids=exposed.metric_ids, event_ids=[]),
    }
    fan_en = next(i for i, v in enumerate(pp.variants) if (v.audience, v.language) == ("fan", "en"))
    for code, sentence in cases.items():
        with pytest.raises(PlanError) as e:
            validate_producer_plan(_swap_sentence(pp, fan_en, 0, sentence), p, a)
        assert e.value.code == code, (code, e.value)
    # dropping the exposed side from a complete trade-off is refused
    one_sided = [s for s in pp.variants[fan_en].sentences if s.claim_id != exposed.claim_id and s.claim_id not in
                 p.episode.exposed_claim_ids]
    variants = list(pp.variants)
    variants[fan_en] = variants[fan_en].model_copy(update={"sentences": one_sided})
    with pytest.raises(PlanError) as e:
        validate_producer_plan(pp.model_copy(update={"variants": variants}), p, a)
    assert e.value.code == "one_sided"


# ------------------------------------------------------------------ API
@pytest.fixture()
def client():
    STORE.sessions.clear()
    return TestClient(app)


def _create(c, clip="clip_001"):
    r = c.post("/api/replays", json={"clip_id": clip}).json()
    return r["replay_id"], {"x-session-token": r["session_token"]}, r


def test_api_status_codes(client):
    c = client
    assert c.post("/api/replays", json={"clip_id": "nope"}).status_code == 404
    rid, H, r = _create(c)
    assert r["analysis_epoch"] == 1
    assert c.get(f"/api/replays/{rid}/snapshot").status_code == 403
    assert c.get("/api/replays/unknown/snapshot", headers=H).status_code == 404
    clip = load_clip("clip_001")
    assert c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s + 5,
                                                         "expected_epoch": 1}, headers=H).status_code == 422
    assert c.post(f"/api/replays/{rid}/control", json={"action": "set_speed", "value": 9, "expected_epoch": 1},
                  headers=H).status_code == 422
    ok = c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 16.4,
                                                      "expected_epoch": 1}, headers=H)
    assert ok.status_code == 200 and ok.json()["analysis_epoch"] == 2
    assert c.post(f"/api/replays/{rid}/control", json={"action": "play", "expected_epoch": 1},
                  headers=H).status_code == 409
    assert c.get(f"/api/replays/{rid}/frames/999", headers=H).status_code == 404


def _complete_insight(c, rid, H):
    ins = c.get(f"/api/replays/{rid}/insights", headers=H).json()
    return next(i for i in ins if i["analysis_status"] == "complete_tradeoff")


def test_approval_is_per_revision_per_session_and_drives_broadcast(client):
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 13.4, "expected_epoch": 1},
           headers=H)
    view = _complete_insight(c, rid, H)
    iid, ep = view["insight"]["insight_id"], view["insight"]["episode_id"]
    assert c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "surface": "broadcast"},
                 headers=H).status_code == 409
    assert c.get(f"/api/replays/{rid}/export/{iid}", headers=H).status_code == 409
    stale = c.post(f"/api/replays/{rid}/insights/{iid}/decision", headers=H, json={
        "revision": view["insight"]["revision"], "evidence_hash": "sha256:" + "0" * 64, "decision": "approve"})
    assert stale.status_code == 409 and "latest" in stale.json()
    d = c.post(f"/api/replays/{rid}/insights/{iid}/decision", headers=H, json={
        "revision": view["insight"]["revision"], "evidence_hash": view["insight"]["evidence_hash"],
        "decision": "approve"})
    assert d.status_code == 200 and d.json()["insight"]["review_status"] == "approved"
    bc = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "surface": "broadcast"}, headers=H)
    assert bc.status_code == 200 and bc.json()["overlay"]["display_mode"] == "retrospective_replay"
    exp = c.get(f"/api/replays/{rid}/export/{iid}", headers=H)
    assert exp.status_code == 200 and len(exp.json()["overlays"]) == 4
    # a second session never sees the first session's approval
    rid2, H2, _ = _create(c)
    c.post(f"/api/replays/{rid2}/control", json={"action": "seek", "value": clip.start_s + 13.4, "expected_epoch": 1},
           headers=H2)
    other = next(i for i in c.get(f"/api/replays/{rid2}/insights", headers=H2).json() if i["insight"]["insight_id"] == iid)
    assert other["insight"]["review_status"] == "suggested"
    # analysis moves on: a newer revision is suggested again and the old approval does not cover it
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 17.0, "expected_epoch": 2},
           headers=H)
    later = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json() if i["insight"]["insight_id"] == iid)
    assert later["insight"]["revision"] > view["insight"]["revision"]
    assert later["insight"]["review_status"] == "suggested"


def test_broadcast_keeps_airing_the_approved_revision_when_a_newer_plan_arrives(client):
    # a cloud plan for the same evidence lands after the approval: it is a new revision that needs its own approval,
    # so broadcast, its caption and the export stay on the approved revision's own narration
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 13.4, "expected_epoch": 1},
           headers=H)
    view = _complete_insight(c, rid, H)
    iid, ep, rev = view["insight"]["insight_id"], view["insight"]["episode_id"], view["insight"]["revision"]
    assert c.post(f"/api/replays/{rid}/insights/{iid}/decision", headers=H, json={
        "revision": rev, "evidence_hash": view["insight"]["evidence_hash"], "decision": "approve"}).status_code == 200
    s = STORE.sessions[rid]
    ins = s.revisions[iid][-1]
    pk = next(p for p in s.analyzer.packets[ep] if p.evidence_hash == ins.evidence_hash)
    reordered = ins.producer_plan.model_copy(update={"variants": [
        v.model_copy(update={"sentences": list(reversed(v.sentences))}) for v in ins.producer_plan.variants]})
    s._attach_cloud(pk, s.epoch, ins.analyst_plan, reordered, {"trace_id": "trace_after_approval"}, "foundry_live")
    assert s.revisions[iid][-1].revision == rev + 1

    latest = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json() if i["insight"]["insight_id"] == iid)
    assert latest["insight"]["revision"] == rev + 1 and latest["insight"]["review_status"] == "suggested"
    assert latest["approved_revision"] == rev and latest["texts"]["fan_en"] != view["texts"]["fan_en"]
    bc = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "surface": "broadcast", "audience": "fan",
                                                        "language": "en"}, headers=H).json()
    assert bc["insight_revision"] == rev and bc["texts"]["fan_en"] == view["texts"]["fan_en"]
    assert bc["on_air"] == view["texts"]["fan_en"][:3]  # the approved plan's caption, in its own order
    exp = c.get(f"/api/replays/{rid}/export/{iid}", headers=H).json()
    assert exp["decision"]["insight_revision"] == rev and exp["decision"]["status"] == "approved"
    assert exp["decision"]["evidence_hash"] == view["insight"]["evidence_hash"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", exp["decision"]["decided_at"])
    assert exp["insight"]["insight"]["revision"] == rev


def _approve_latest(c, rid, H, iid):
    v = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json() if i["insight"]["insight_id"] == iid)
    assert c.post(f"/api/replays/{rid}/insights/{iid}/decision", headers=H, json={
        "revision": v["insight"]["revision"], "evidence_hash": v["insight"]["evidence_hash"],
        "decision": "approve"}).status_code == 200
    return v


def _attach_reordered(rid, iid, ep, trace_id):
    s = STORE.sessions[rid]
    ins = s.revisions[iid][-1]
    pk = next(p for p in s.analyzer.packets[ep] if p.evidence_hash == ins.evidence_hash)
    reordered = ins.producer_plan.model_copy(update={"variants": [
        v.model_copy(update={"sentences": list(reversed(v.sentences))}) for v in ins.producer_plan.variants]})
    s._attach_cloud(pk, s.epoch, ins.analyst_plan, reordered, {"trace_id": trace_id}, "foundry_live")


def test_broadcast_caption_takes_the_approved_plans_observed_outcome(client):
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s, "expected_epoch": 1}, headers=H)
    view = _complete_insight(c, rid, H)
    iid, ep = view["insight"]["insight_id"], view["insight"]["episode_id"]
    _approve_latest(c, rid, H, iid)
    keys = [x.template_key for x in STORE.sessions[rid].revisions[iid][-1].producer_plan.variant("fan", "en").sentences]
    at = next(i for i, k in enumerate(keys) if k.startswith("outcome.") and k != "outcome.pending")
    assert at > 2  # the fan caption is what was taken away, what it cost, and what happened
    _attach_reordered(rid, iid, ep, "trace_outcome")
    bc = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "surface": "broadcast", "audience": "fan",
                                                        "language": "en"}, headers=H).json()
    fan = view["texts"]["fan_en"]
    assert bc["on_air"] == [fan[0], fan[1], fan[at]]


def test_the_fan_caption_keeps_both_sides_when_an_agent_reorders_the_plan(client):
    # seen on the live app: the Producer moved the exposed sentence behind a second restricted one, and a caption
    # chosen by position ("first two, then the outcome") dropped what the press left open
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s, "expected_epoch": 1}, headers=H)
    view = _complete_insight(c, rid, H)
    iid, ep = view["insight"]["insight_id"], view["insight"]["episode_id"]
    s = STORE.sessions[rid]
    ins = s.revisions[iid][-1]
    pk = next(p for p in s.analyzer.packets[ep] if p.evidence_hash == ins.evidence_hash)
    dim = {cl.claim_id: cl.dimension for cl in pk.claims}
    side = lambda x: dim.get(x.claim_id) if x.claim_id else None  # noqa: E731
    fan = ins.producer_plan.variant("fan", "en").sentences
    exp = next(i for i, x in enumerate(fan) if side(x) == "exposed")
    order = [x for i, x in enumerate(fan) if i != exp]
    order.insert(2, fan[exp])
    assert [side(x) for x in order[:3]] == ["restricted", "restricted", "exposed"]
    plan = ins.producer_plan.model_copy(update={"variants": [
        v.model_copy(update={"sentences": order}) if v.audience == "fan" else v for v in ins.producer_plan.variants]})
    s._attach_cloud(pk, s.epoch, ins.analyst_plan, plan, {"trace_id": "trace_reordered"}, "foundry_live")
    _approve_latest(c, rid, H, iid)
    texts = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json()
                 if i["insight"]["insight_id"] == iid)["texts"]["fan_en"]
    bc = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "surface": "broadcast", "audience": "fan",
                                                        "language": "en"}, headers=H).json()
    outcome = next(i for i, x in enumerate(order) if x.template_key.startswith("outcome.")
                   and x.template_key != "outcome.pending")
    assert bc["on_air"] == [texts[0], texts[2], texts[outcome]]  # taken away, what it cost, what happened


def test_an_approval_holds_only_for_its_own_revision_and_evidence_across_seeks(client):
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 17.0, "expected_epoch": 1},
           headers=H)
    view = _complete_insight(c, rid, H)
    iid, ep, rev = view["insight"]["insight_id"], view["insight"]["episode_id"], view["insight"]["revision"]
    _approve_latest(c, rid, H, iid)
    # back to before that revision existed: the approval names nothing in this analysis, so nothing airs
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 13.4, "expected_epoch": 2},
           headers=H)
    now = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json() if i["insight"]["insight_id"] == iid)
    assert now["insight"]["revision"] < rev and now["approved_revision"] is None
    assert c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "surface": "broadcast"},
                 headers=H).status_code == 409
    assert c.get(f"/api/replays/{rid}/export/{iid}", headers=H).status_code == 409
    # forward again to the same evidence: the exact revision is back, and so is its approval
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 17.0, "expected_epoch": 3},
           headers=H)
    again = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json() if i["insight"]["insight_id"] == iid)
    assert again["approved_revision"] == rev and again["insight"]["evidence_hash"] == view["insight"]["evidence_hash"]
    assert c.get(f"/api/replays/{rid}/export/{iid}", headers=H).json()["decision"]["insight_revision"] == rev


def test_rejecting_a_newer_revision_keeps_the_approved_one_on_air(client):
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 13.4, "expected_epoch": 1},
           headers=H)
    view = _complete_insight(c, rid, H)
    iid, ep, rev = view["insight"]["insight_id"], view["insight"]["episode_id"], view["insight"]["revision"]
    _approve_latest(c, rid, H, iid)
    _attach_reordered(rid, iid, ep, "trace_rejected")
    newer = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json() if i["insight"]["insight_id"] == iid)
    assert c.post(f"/api/replays/{rid}/insights/{iid}/decision", headers=H, json={
        "revision": newer["insight"]["revision"], "evidence_hash": newer["insight"]["evidence_hash"],
        "decision": "reject"}).json()["insight"]["review_status"] == "rejected"
    after = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json() if i["insight"]["insight_id"] == iid)
    assert after["approved_revision"] == rev
    bc = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "surface": "broadcast"}, headers=H).json()
    assert bc["insight_revision"] == rev
    assert c.get(f"/api/replays/{rid}/export/{iid}", headers=H).json()["decision"]["insight_revision"] == rev
    # the approved revision itself still reads as approved, the rejected one as rejected
    s = STORE.sessions[rid]
    assert [s.review_status(r) for r in s.revisions[iid][-2:]] == ["approved", "rejected"]


def test_agent_changes_are_computed_in_code_against_the_rule_plan(packets, client):
    meta, p = next((m, p) for m, p in packets if p.episode.analysis_status == "complete_tradeoff")
    rules_a = analyst_plan(p)
    rules_p = producer_plan(p, rules_a)
    same = changes_from_rules(p, rules_a, rules_p)
    assert same["same_as_rules"] and same["sentences"]["fan"]["moved"] == 0
    flipped = rules_p.model_copy(update={"variants": [
        v.model_copy(update={"sentences": list(reversed(v.sentences))}) for v in rules_p.variants]})
    moved = changes_from_rules(p, rules_a, flipped)
    n = len(rules_p.variant("fan", "en").sentences)
    assert not moved["same_as_rules"] and moved["sentences"]["fan"] == {"kept": n, "dropped": 0, "added": 0,
                                                                         "moved": n - 1}
    shorter = rules_p.model_copy(update={"variants": [
        v.model_copy(update={"sentences": v.sentences[:-1]}) for v in rules_p.variants]})
    assert changes_from_rules(p, rules_a, shorter)["sentences"]["analyst"]["dropped"] == 1
    lead_last = rules_a.model_copy(update={"focus_claim_ids": rules_a.focus_claim_ids[1:] + rules_a.focus_claim_ids[:1]})
    if len(rules_a.focus_claim_ids) > 1:
        assert changes_from_rules(p, lead_last, rules_p)["lead"]["agents"] != changes_from_rules(p, rules_a, rules_p)["lead"]["agents"]
    # on the API: a cloud revision carries the comparison, the deterministic one does not
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 13.4, "expected_epoch": 1},
           headers=H)
    view = _complete_insight(c, rid, H)
    assert view["agent_changes"] is None
    s = STORE.sessions[rid]
    iid, ep = view["insight"]["insight_id"], view["insight"]["episode_id"]
    ins = s.revisions[iid][-1]
    pk = next(q for q in s.analyzer.packets[ep] if q.evidence_hash == ins.evidence_hash)
    reordered = ins.producer_plan.model_copy(update={"variants": [
        v.model_copy(update={"sentences": list(reversed(v.sentences))}) for v in ins.producer_plan.variants]})
    s._attach_cloud(pk, s.epoch, ins.analyst_plan, reordered, {"trace_id": "trace_changes"}, "foundry_live")
    cloud = next(i for i in c.get(f"/api/replays/{rid}/insights", headers=H).json() if i["insight"]["insight_id"] == iid)
    assert cloud["agent_changes"]["sentences"]["fan"]["moved"] > 0 and not cloud["agent_changes"]["same_as_rules"]


def test_replay_of_an_earlier_revision_comes_with_that_revisions_narration(client):
    """E: rev 1 is told as it was then (nobody presses), not with the step and the reception that came later."""
    c = client
    clip = load_clip("clip_009")
    rid, H, _ = _create(c, "clip_009")
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s, "expected_epoch": 1}, headers=H)
    view = _complete_insight(c, rid, H)
    ep = view["insight"]["episode_id"]
    q = {"episode_id": ep, "revision": 1}
    first = c.get(f"/api/replays/{rid}/overlays", params=q, headers=H).json()
    latest = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep}, headers=H).json()
    assert first["episode_revision"] == 1 and first["insight_revision"] is not None
    assert any("Nobody presses" in t for t in first["texts"]["analyst_en"])
    assert not any("stepped up" in t or "was received by" in t for t in first["texts"]["analyst_en"])
    assert latest["texts"] == view["texts"]


def test_live_overlay_draws_only_claims_that_hold_on_the_displayed_frame(client):
    """A claim the episode made earlier is not drawn live on a later player configuration that no longer supports it."""
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    s = STORE.sessions[rid]
    seen = 0
    for t in (10.4, 11.6, 12.8, 13.6, 14.6, 15.2):
        epoch = c.get(f"/api/replays/{rid}/snapshot", headers=H).json()["analysis_epoch"]
        c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + t, "expected_epoch": epoch},
               headers=H)
        for ep in c.get(f"/api/replays/{rid}/episodes", headers=H).json():
            o = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep["episode_id"], "display_mode": "live"},
                      headers=H).json()
            if o["overlay"]["valid_until_s"] < s.clock_s:
                continue  # expired: the client draws nothing
            codes = {cl.claim_id: cl.code for cl in s.analyzer.latest_packet(ep["episode_id"]).claims}
            drawn = {codes[cid] for e in o["overlay"]["elements"] for cid in e["claim_ids"]}
            assert drawn <= set(s.results[-1].supported), (t, drawn, set(s.results[-1].supported))
            seen += bool(drawn)
    assert seen  # the property was exercised on frames that do draw claims


def test_live_overlay_of_a_closed_episode_is_expired_not_redrawn_on_the_new_frame(client):
    """After the long ball is received and red wins the ball back, A's claims must not be drawn over the new frame."""
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s, "expected_epoch": 1}, headers=H)
    ep = _complete_insight(c, rid, H)["insight"]["episode_id"]
    live = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "display_mode": "live"}, headers=H).json()
    snap = c.get(f"/api/replays/{rid}/snapshot", headers=H).json()
    replay = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep}, headers=H).json()
    assert live["overlay"]["valid_until_s"] < snap["clock_s"]  # the client shows "nothing current"
    assert live["frame_index"] == replay["frame_index"] != snap["frame"]["frame_ref"]["frame_index"]


def test_live_overlay_is_rebuilt_on_the_current_frame(client):
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 12.8, "expected_epoch": 1},
           headers=H)
    ep = _complete_insight(c, rid, H)["insight"]["episode_id"]
    live = c.get(f"/api/replays/{rid}/overlays", params={"episode_id": ep, "display_mode": "live"}, headers=H).json()
    snap = c.get(f"/api/replays/{rid}/snapshot", headers=H).json()
    assert live["overlay"]["display_mode"] == "live"
    assert live["frame_index"] == snap["frame"]["frame_ref"]["frame_index"]
    assert live["overlay"]["valid_until_s"] - live["overlay"]["match_time_s"] == pytest.approx(0.6)


def test_health_and_catalog_leak_nothing(client, monkeypatch):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://secret.example/should-not-leak")
    h = client.get("/api/health").json()
    assert "secret.example" not in str(h)
    text = str(client.get("/api/clips").json())
    for token in ("main", "cover", "family", "variant", "seed", "high_man"):
        assert token not in text


def test_websocket_sends_snapshot_then_resyncs_after_seek(client):
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    with c.websocket_connect(f"/ws/replays/{rid}?token={H['x-session-token']}") as ws:
        first = ws.receive_json()
        assert first["type"] == "state_snapshot" and first["analysis_epoch"] == 1
        c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 5.0,
                                                     "expected_epoch": 1}, headers=H)
        for _ in range(20):
            m = ws.receive_json()
            if m["type"] == "state_snapshot":
                assert m["analysis_epoch"] == 2
                break
        else:
            pytest.fail("no resync snapshot after seek")
    with pytest.raises(Exception):
        with c.websocket_connect(f"/ws/replays/{rid}?token=wrong") as ws:
            ws.receive_json()


def test_a_socket_opened_after_a_seek_gets_the_insights_so_far(client):
    # a viewer who starts the breakdown before the socket is open: the seek has already built the insights, and the
    # socket must send them with its first snapshot (the client has no other news of them until something changes)
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s, "expected_epoch": 1}, headers=H)
    want = {v["insight"]["insight_id"] for v in c.get(f"/api/replays/{rid}/insights", headers=H).json()}
    assert want
    try:
        with c.websocket_connect(f"/ws/replays/{rid}?token={H['x-session-token']}") as ws:
            assert ws.receive_json()["type"] == "state_snapshot"
            got = {ws.receive_json()["payload"]["insight"]["insight_id"] for _ in want}
    finally:
        STORE.delete(rid)  # ends the server loop whatever happened above
    assert got == want


def test_the_breakdown_tells_the_moment_in_four_steps_from_the_evidence(client):
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    ep = "ep_clip_001_red_press_vs_depth_f0048"
    # before the outcome is known, the last step says so and draws no ball path (the replay's own causality)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.start_s + 13.4, "expected_epoch": 1},
           headers=H)
    early = c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": ep}, headers=H).json()
    assert [s["key"] for s in early["steps"]] == ["press", "takes_away", "leaves_open", "what_happened"]
    assert early["steps"][3]["ball_path"] == [] and "not been observed" in " ".join(early["steps"][3]["sentences"])
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s, "expected_epoch": 2}, headers=H)
    b = c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": ep}, headers=H).json()
    press, takes, opens, happened = b["steps"]
    assert press["roles"] == {"red_09": "presser", "blue_05": "carrier"} and "closes down" in press["sentences"][0]
    # who follows whom: every sustained marking pair of the revision's own state, defender first
    state = key_state(next(p for p in STORE.sessions[rid].analyzer.packets[ep] if p.episode.revision == press["revision"]))
    assert press["marks"] == [[m.defender_id, m.attacker_id] for m in state.markings if m.status == "ok"]
    assert ["red_04", "blue_09"] in press["marks"] and all(d.startswith("red") for d, _ in press["marks"])
    assert "short passes" in takes["sentences"][0] and "screened_lanes" in takes["layers"]
    assert "exposure" in opens["layers"] and "BLU #9" in opens["sentences"][0] and opens["roles"]["blue_09"] == "runner"
    assert happened["revision"] > opens["revision"] and "received by BLU #9" in happened["sentences"][-1]
    assert happened["roles"] == {"blue_05": "passer", "blue_09": "receiver"}
    # the ball's path runs from the passer to the receiver, from the frames themselves
    s = STORE.sessions[rid]
    at = lambda t: next(f for f in s.clip.frames if abs(f.match_time_s - t) < 0.11)  # noqa: E731
    pos = lambda f, pid: next((p.position.x_m, p.position.y_m) for p in f.players if p.player_id == pid)  # noqa: E731
    first, last = happened["ball_path"][0], happened["ball_path"][-1]
    assert abs(first[0] - pos(at(625.0), "blue_05")[0]) < 3 and abs(last[0] - pos(at(627.6), "blue_09")[0]) < 3
    # every sentence is one the narration renders for that revision (code-filled numbers, no new prose)
    texts = {t for v in c.get(f"/api/replays/{rid}/insights", headers=H).json() for t in v["texts"]["fan_en"]}
    pk = {p.episode.revision: p for p in s.analyzer.packets[ep]}
    for step in b["steps"]:
        rendered = {render_sentence(x, pk[step["revision"]], "en", s.clip.meta)
                    for x in producer_plan(pk[step["revision"]], analyst_plan(pk[step["revision"]])).variant("fan", "en").sentences}
        assert set(step["sentences"]) <= rendered
    assert texts  # the insights exist for the same evidence
    assert c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": "ep_none"}, headers=H).status_code == 404


def _breakdown_at_end(c, clip_id: str) -> tuple[dict, list[dict]]:
    """The breakdown of the strongest moment of a clip analysed to its end (as the UI picks it), and the episodes."""
    clip = load_clip(clip_id)
    r = c.post("/api/replays", json={"clip_id": clip_id}).json()
    H = {"x-session-token": r["session_token"]}
    snap = c.post(f"/api/replays/{r['replay_id']}/control",
                  json={"action": "seek", "value": clip.end_s, "expected_epoch": 1}, headers=H).json()
    eps = snap["episodes"]
    pick = sorted([e for e in eps if e["analysis_status"] == "complete_tradeoff"] or eps,
                  key=lambda e: -e["story_priority"])[0]
    b = c.get(f"/api/replays/{r['replay_id']}/breakdown", params={"episode_id": pick["episode_id"]}, headers=H).json()
    return b, eps


def test_the_breakdown_tells_one_story_and_says_why_nothing_is_open(client):
    # once the attack used the space, "what it leaves open" is the claim about the player who received the ball, so
    # steps 3 and 4 are about the same player
    b, _ = _breakdown_at_end(client, "clip_003")
    _, _, opens, happened = b["steps"]
    receiver = next(p for p, r in happened["roles"].items() if r == "receiver")
    assert receiver in opens["player_ids"]
    name = f"{receiver.split('_')[0][:3].upper()} #{int(receiver.split('_')[1])}"
    assert name in " ".join(opens["sentences"]) and name in happened["sentences"][-1]
    # nothing open: the reason the analysis held a claim back is given, and what the play showed meanwhile
    b, _ = _breakdown_at_end(client, "clip_010")
    _, _, opens, happened = b["steps"]
    assert opens["layers"] == [] and {"reason": "secondary_cover_in_time"}.items() <= opens["held_back"][0].items()
    b, _ = _breakdown_at_end(client, "clip_004")
    assert "changes" in b["steps"][3]["layers"] and "stepped up" in b["steps"][3]["sentences"][0]


def test_without_an_outcome_the_breakdown_shows_the_next_pass_the_replay_has_seen(client):
    # clip 7: Red's set-up while Blue's goalkeeper has the ball records no outcome; the last step shows where his next
    # pass went (an observation) instead of "not observed yet", once the replay has seen it arrive
    c = client
    clip = load_clip("clip_007")
    rid, H, _ = _create(c, "clip_007")
    ep = "ep_clip_007_red_press_vs_depth_f0000"
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": 418.0, "expected_epoch": 1}, headers=H)
    early = c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": ep}, headers=H).json()["steps"][3]
    assert early["next_pass"] is None and early["ball_path"] == [] and early["roles"] == {}  # the reception is at 419.0
    assert "not been observed" in " ".join(early["sentences"])
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s, "expected_epoch": 2}, headers=H)
    b = c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": ep}, headers=H).json()
    happened = b["steps"][3]
    assert b["outcome"] == "not_yet_observed" and not any("not been observed" in t for t in happened["sentences"])
    assert happened["next_pass"] == {"kind": "pass", "from_id": "blue_01", "to_id": "blue_04", "release_s": 417.0,
                                     "end_s": 418.6}
    assert happened["roles"] == {"blue_01": "passer", "blue_04": "receiver"}
    assert happened["ball_path"][0] == [6.3, 34.09] and happened["ball_path"][-1] == [17.49, 52.32]
    # that pass went along a lane step 2 drew as blocked, and the step says so
    s = STORE.sessions[rid]
    pk = next(p for p in s.analyzer.packets[ep] if p.episode.revision == b["steps"][1]["revision"])
    assert ("blue_01", "blue_04") in {(ln.carrier_id, ln.receiver_id) for ln in key_state(pk).lanes
                                      if ln.availability == "screened"}
    assert happened["through_blocked"] is True
    # the next moment, where Red block the middle: BLU #4's pass is intercepted by RED #5
    b = c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": "ep_clip_007_red_central_screen_f0023"},
              headers=H).json()
    happened = b["steps"][3]
    assert happened["next_pass"]["kind"] == "interception"
    assert happened["roles"] == {"blue_04": "passer", "red_05": "interceptor"}
    assert happened["ball_path"][-1] == [70.89, 35.85]  # where the interception was
    # the last moment (Blue press after the interception): nobody presses, so the player on the ball is tagged; nobody
    # plays the ball on before the clip ends, and the step says so instead of "not observed yet"
    b = c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": "ep_clip_007_blue_press_vs_depth_f0055"},
              headers=H).json()
    assert b["steps"][0]["roles"] == {"red_05": "carrier"} and "Nobody closes RED #5 down" in b["steps"][0]["sentences"][0]
    happened = b["steps"][3]
    assert happened["next_pass"] is None and happened["clip_ends"] is True
    assert not any("not been observed" in t for t in happened["sentences"])


def test_the_breakdown_press_step_and_a_shot_without_an_outcome(client):
    # clip 1 at 10:28: the narration has no sentence about the press, so the step gives the player on the ball and the
    # nearest defender, measured on its frame (and highlights him); the next thing on the ball is BLU #9's shot, which
    # RED #1 saves
    c = client
    clip = load_clip("clip_001")
    rid, H, _ = _create(c)
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": clip.end_s, "expected_epoch": 1}, headers=H)
    b = c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": "ep_clip_001_red_press_vs_depth_f0078"},
              headers=H).json()
    press, happened = b["steps"][0], b["steps"][3]
    assert press["sentences"] == [] and press["press_facts"]["carrier_id"] == "blue_09"
    s = STORE.sessions[rid]
    pk = next(p for p in s.analyzer.packets["ep_clip_001_red_press_vs_depth_f0078"] if p.episode.revision == press["revision"])
    frame = s.clip.frames[key_state(pk).frame_ref.frame_index]
    at = {p.player_id: p.position for p in frame.players}
    d = press["press_facts"]["defender_id"]
    assert d in press["player_ids"]
    assert d.startswith("red_") and abs(press["press_facts"]["distance_m"] - (
        (at[d].x_m - at["blue_09"].x_m) ** 2 + (at[d].y_m - at["blue_09"].y_m) ** 2) ** 0.5) < 0.06
    # the keeper stops it: a save
    assert happened["next_pass"]["kind"] == "save" and happened["roles"] == {"blue_09": "shooter", "red_01": "saver"}
    # the default moment of the clip keeps its press sentence, so it carries no measured stand-in
    b = c.get(f"/api/replays/{rid}/breakdown", params={"episode_id": "ep_clip_001_red_press_vs_depth_f0048"},
              headers=H).json()
    assert b["steps"][0]["press_facts"] is None and b["steps"][3].get("next_pass") is None
