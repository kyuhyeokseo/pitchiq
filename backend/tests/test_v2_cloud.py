"""Cloud insight pipeline with a fake gateway (no network, no SDK): limits, repair, fallback, provenance, late
results. These tests prove the app-side workflow only; they are not evidence that a real Foundry call works."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agents.cloud import CloudInsights, Limits, run_pipeline
from app.agents.fallback import analyst_plan, producer_plan
from app.agents.gateway import ModelReply
from app.agents.llm_plans import AnalystChoice, AudienceChoice, ProducerChoice, sentence_candidates
from app.replay.catalog import load_clip
from app.replay.session import ReplaySession
from app.agents.workflow import InsightService

from .v2_helpers import PUBLIC, load_public, run_analyzer


@pytest.fixture(scope="module")
def complete_packet():
    meta, frames, events = load_public(PUBLIC / "clip_003")
    an = run_analyzer(meta, frames, events)
    revs = next(r for r in an.packets.values() if r[-1].episode.analysis_status == "complete_tradeoff")
    return revs[-1]


def option_ids(packet, audience, sentences):
    ids = {(c.sentence.template_key, c.sentence.claim_id, c.sentence.change_id): c.option_id
           for c in sentence_candidates(packet, audience)}
    return [ids[(s.template_key, s.claim_id, s.change_id)] for s in sentences]


def choices_from(packet):
    """What a well-behaved model would choose: the deterministic plan's selections, as sentence option ids."""
    a = analyst_plan(packet)
    p = producer_plan(packet, a)
    ac = AnalystChoice(focus_claim_ids=a.focus_claim_ids, ordered_change_ids=a.ordered_change_ids,
                       omitted_claim_ids=a.omitted_claim_ids, caveat_codes=a.caveat_codes)
    picks = {}
    for aud in ("fan", "analyst"):
        v = p.variant(aud, "en")
        picks[aud] = AudienceChoice(option_ids=option_ids(packet, aud, v.sentences), focus_layers=v.focus_layers)
    return ac, ProducerChoice(**picks)


class FakeGateway:
    model = "fake-deployment"

    def __init__(self, analyst_replies, producer_replies, delay_s: float = 0.0):
        self.analyst_replies, self.producer_replies = list(analyst_replies), list(producer_replies)
        self.delay_s = delay_s
        self.prompts: list[str] = []

    async def _reply(self, queue, prompt):
        self.prompts.append(prompt)
        await asyncio.sleep(self.delay_s)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return ModelReply(r, {"input_token_count": 100, "output_token_count": 20})

    async def analyst(self, prompt):
        return await self._reply(self.analyst_replies, prompt)

    async def producer(self, prompt):
        return await self._reply(self.producer_replies, prompt)


def test_valid_choices_become_validated_plans(complete_packet):
    ac, pc = choices_from(complete_packet)
    a, p, trace = asyncio.run(run_pipeline(complete_packet, FakeGateway([ac], [pc])))
    assert a is not None and p is not None and trace.result == "foundry_live"
    assert trace.calls == 2 and trace.repairs == 0
    assert trace.usage == {"input_token_count": 200, "output_token_count": 40}
    assert [s["stage"] for s in trace.stages] == ["analyst", "analyst_validation", "producer", "producer_validation"]
    # metrics were bound by code to the packet's own ids
    ids = {m.metric_id for m in complete_packet.metrics}
    assert all(set(s.metric_ids) <= ids for v in p.variants for s in v.sentences)


def test_one_repair_then_fallback(complete_packet):
    ac, pc = choices_from(complete_packet)
    bad = AnalystChoice(focus_claim_ids=["cl_invented"], ordered_change_ids=[], omitted_claim_ids=[], caveat_codes=[])
    a, p, trace = asyncio.run(run_pipeline(complete_packet, FakeGateway([bad, ac], [pc])))
    assert trace.result == "foundry_live" and trace.repairs == 1 and trace.calls == 3
    assert any(s.get("error") == "unknown_claim" for s in trace.stages)
    a, p, trace = asyncio.run(run_pipeline(complete_packet, FakeGateway([bad, bad], [pc])))
    assert (a, p) == (None, None) and trace.result == "fallback" and trace.reason == "analyst_invalid"
    assert trace.calls == 2


def test_one_sided_producer_plan_is_refused(complete_packet):
    ac, pc = choices_from(complete_packet)
    one_sided = pc.model_copy(deep=True)
    for aud in ("fan", "analyst"):  # keep only restricting sentences and the tail
        kinds = {c.option_id: c.kind for c in sentence_candidates(complete_packet, aud)}
        pick = getattr(one_sided, aud)
        pick.option_ids = [i for i in pick.option_ids if kinds[i] != "exposed"]
    a, p, trace = asyncio.run(run_pipeline(complete_packet, FakeGateway([ac], [one_sided, one_sided])))
    assert p is None and trace.reason == "producer_invalid"
    assert any(s.get("error") == "one_sided" for s in trace.stages)


def test_timeout_and_service_errors_fall_back_without_retry(complete_packet):
    ac, pc = choices_from(complete_packet)
    limits = Limits(stage_timeout_s=0.05, pipeline_deadline_s=1.0)
    a, p, trace = asyncio.run(run_pipeline(complete_packet, FakeGateway([ac], [pc], delay_s=0.2), limits))
    assert p is None and trace.reason == "timeout" and trace.calls == 1
    a, p, trace = asyncio.run(run_pipeline(complete_packet, FakeGateway([RuntimeError("429")], [pc])))
    assert p is None and trace.reason == "service_error:RuntimeError" and trace.calls == 1


def test_call_budget_is_three(complete_packet):
    ac, pc = choices_from(complete_packet)
    bad = AnalystChoice(focus_claim_ids=["cl_invented"], ordered_change_ids=[], omitted_claim_ids=[], caveat_codes=[])
    broken = pc.model_copy(deep=True)
    broken.fan.option_ids[0] = "s999"
    a, p, trace = asyncio.run(run_pipeline(complete_packet, FakeGateway([bad, ac], [broken, pc])))
    assert p is None and trace.calls == 3 and trace.reason == "producer_invalid"


def _wait(cond, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_session_attaches_cloud_revision_and_keeps_late_results_off_the_pitch():
    clip = load_clip("clip_003")
    cloud = CloudInsights(FakeGatewayFromPackets, model="fake-deployment")
    s = ReplaySession("replay_t", "tok", clip, InsightService(), cloud=cloud)
    for k in range(len(clip.frames)):  # like a replay: frames arrive over time, the queue drains between them
        with s.lock:
            s._advance_to(k)
        assert _wait(lambda: cloud._pending.get("replay_t", 0) == 0)
    ins = [r for revs in s.revisions.values() for r in revs if r.source_mode == "foundry_live"]
    assert ins, s.history[-5:]
    assert all(r.review_status == "suggested" and r.generation_trace_id in s.traces for r in ins)
    assert s.traces[ins[0].generation_trace_id]["model"] == "fake-deployment"
    # a cloud revision always follows the deterministic one for the same evidence
    for r in ins:
        prev = s.revisions[r.insight_id][r.revision - 2]
        assert prev.source_mode == "deterministic_fallback" and prev.evidence_hash == r.evidence_hash
    # a result for an old epoch goes to the history only
    packet = s.analyzer.latest_packet(ins[0].episode_id)
    n = len(s.revisions[ins[0].insight_id])
    s._attach_cloud(packet, s.epoch - 1, ins[0].analyst_plan, ins[0].producer_plan, {"trace_id": "tr_old"},
                    "foundry_live")
    assert len(s.revisions[ins[0].insight_id]) == n and s.history[-1]["late"] == "epoch"


def test_burst_of_revisions_sends_the_latest_of_each_episode_and_never_attaches_stale_plans():
    clip = load_clip("clip_003")
    cloud = CloudInsights(FakeGatewayFromPackets, model="fake-deployment")
    s = ReplaySession("replay_b", "tok", clip, InsightService(), cloud=cloud)
    with s.lock:
        s._advance_to(len(clip.frames) - 1)  # every revision of the clip at once
    assert _wait(lambda: not s._cloud_busy and cloud._pending.get("replay_b", 0) == 0)
    sent = [h for h in s.history if h["event"] == "cloud_result"]
    for iid, revs in s.revisions.items():
        # one job in flight per episode: at most the first revision and the newest one waiting behind it
        assert sum(h["insight_id"] == iid for h in sent) <= 2
        assert revs[-1].source_mode == "foundry_live", (iid, [r.source_mode for r in revs])
        for r in revs:
            if r.source_mode != "deterministic_fallback":
                assert r.evidence_hash == s.analyzer.packets[r.episode_id][-1].evidence_hash


def test_more_episodes_than_the_queue_allows_keep_the_fallback():
    clip = load_clip("clip_003")
    cloud = CloudInsights(FakeGatewayFromPackets, model="fake-deployment", limits=Limits(per_replay_pending=1))
    s = ReplaySession("replay_q", "tok", clip, InsightService(), cloud=cloud)
    with s.lock:
        s._advance_to(len(clip.frames) - 1)
    assert _wait(lambda: not s._cloud_busy and cloud._pending.get("replay_q", 0) == 0)
    assert ("cloud_skipped", "queue_full") in [(h["event"], h.get("reason")) for h in s.history]
    # a skipped episode waits for the free slot instead of losing its cloud plan
    assert all(revs[-1].source_mode == "foundry_live" for revs in s.revisions.values())


class FakeGatewayFromPackets:
    """Answers each prompt with the choices a well-behaved model would make for exactly that packet."""
    model = "fake-deployment"

    async def analyst(self, prompt):
        return ModelReply(choices_from(_packet_by_hash(prompt))[0], {})

    async def producer(self, prompt):
        return ModelReply(choices_from(_packet_by_hash(prompt))[1], {})


_BY_HASH = {}


def _packet_by_hash(prompt):
    import json
    if not _BY_HASH:
        meta, frames, events = load_public(PUBLIC / "clip_003")
        an = run_analyzer(meta, frames, events)
        _BY_HASH.update({p.evidence_hash: p for revs in an.packets.values() for p in revs})
    return _BY_HASH[json.loads(prompt)["packet"]["evidence_hash"]]


def test_gateway_builds_four_app_owned_agents_offline():
    """Only checks that the adapter matches the installed SDK; nothing is called (the endpoint does not exist)."""
    pytest.importorskip("agent_framework.foundry")
    from app.agents.gateway import FoundryGateway

    async def build():
        gw = FoundryGateway("https://example.invalid/api/projects/none", "model-not-real", "azure_cli")
        names = (gw._analyst.name, gw._producer.name, gw._ask.name, gw._recap.name)
        await gw.close()
        return names

    assert asyncio.run(build()) == ("pitchiq-analyst", "pitchiq-producer", "pitchiq-ask", "pitchiq-recap")


def test_the_daily_cap_keeps_the_fallback_and_cached_plans_still_attach():
    clip = load_clip("clip_003")
    cloud = CloudInsights(FakeGatewayFromPackets, model="fake-deployment", limits=Limits(daily_pipelines=1))
    s = ReplaySession("replay_d", "tok", clip, InsightService(), cloud=cloud)
    for k in range(len(clip.frames)):
        with s.lock:
            s._advance_to(k)
        assert _wait(lambda: cloud._pending.get("replay_d", 0) == 0)
    assert cloud.status["pipelines_today"] == 1 and cloud.status["daily_limit"] == 1
    assert any(h["event"] == "cloud_skipped" and h["reason"] == "daily_limit" for h in s.history)
    live = [r for revs in s.revisions.values() for r in revs if r.source_mode == "foundry_live"]
    assert len(live) == 1
    # the same evidence again (a second replay): served from the cache, not counted. The cached plan arrives from the
    # cloud thread (never inside the caller's session lock); let it land before the next frame, as wall time would
    s2 = ReplaySession("replay_d2", "tok", clip, InsightService(), cloud=cloud)
    for k in range(len(clip.frames)):
        with s2.lock:
            s2._advance_to(k)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), cloud._loop).result(timeout=10)
    cached = [r for revs in s2.revisions.values() for r in revs if r.source_mode == "foundry_cached"]
    assert len(cached) == 1 and cloud.status["pipelines_today"] == 1
