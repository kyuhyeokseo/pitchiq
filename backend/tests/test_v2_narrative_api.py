"""Plans, validators, rendering and the replay API (spec 11.1, 12, 13.2, 14, 17.1)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.agents.fallback import analyst_plan, producer_plan
from app.agents.validate import PlanError, validate_analyst_plan, validate_producer_plan
from app.agents.workflow import render_variants
from app.api.app import STORE, app
from app.contracts import SentencePlan
from app.narrative.registry import FORMAT, TEMPLATES
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
