"""Ask PitchIQ with a fake model (no network, no SDK): the agent looks with causal tools, answers only with
validated sentence options, gets one repair, and otherwise falls back to the deterministic narration. These tests
prove the app-side agent loop only; they are not evidence that a real Foundry call works."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agents import tools
from app.agents.ask import AskChoice, AskLimits, fallback_answer, main_moment, run_ask, sentence_options
from app.agents.cloud import CloudInsights
from app.agents.gateway import ModelReply
from app.api import app as api
from app.narrative.registry import ASK_DECLINES


@pytest.fixture(scope="module")
def view():
    return tools.view_at("clip_001")


@pytest.fixture(scope="module")
def main(view):
    p = main_moment(view)
    assert p.episode.analysis_status == "complete_tradeoff"
    return p


def pick(view, packet, audience, kinds):
    """Option ids for the first option of each kind, as a sensible model would choose them."""
    opts = sentence_options(packet, view.clip.meta, audience)
    return [next(o.option_id for o in opts if o.kind == k) for k in kinds]


def moment(packet, ids):
    return AskChoice(answer="moment", episode_id=packet.episode.episode_id, option_ids=ids, note_code=None)


def decline(code):
    return AskChoice(answer="decline", episode_id=None, option_ids=[], note_code=code)


class FakeAsk:
    """Plays a scripted agent: per run, the tool calls to make (name, episode_id), then the reply."""
    model = "fake-deployment"

    def __init__(self, runs, delay_s: float = 0.0):
        self.runs = list(runs)
        self.delay_s = delay_s
        self.prompts: list[str] = []
        self.results: list[dict] = []

    async def ask(self, prompt, tool_fns):
        self.prompts.append(prompt)
        calls, reply = self.runs.pop(0)
        by_name = {f.__name__: f for f in tool_fns}
        for name, eid in calls:
            self.results.append(by_name[name](eid))
        await asyncio.sleep(self.delay_s)
        if isinstance(reply, Exception):
            raise reply
        return ModelReply(reply, {"input_token_count": 1000, "output_token_count": 300})


def test_the_agent_looks_with_tools_then_answers_with_validated_sentences(view, main):
    eid = main.episode.episode_id
    ids = pick(view, main, "fan", ["restricted", "exposed", "outcome"])
    gw = FakeAsk([([("answer_options", eid), ("get_evidence", eid)], moment(main, ids))])
    answer, trace = asyncio.run(run_ask(view, "Why did the space behind open up?", "fan", "en", gw))
    assert trace.result == "foundry_live" and trace.runs == 1 and trace.repairs == 0 and trace.tool_calls == 2
    assert [s["step"] for s in trace.steps] == ["agent", "tool", "tool", "validation"]
    assert [s.get("name") for s in trace.steps[1:3]] == ["answer_options", "get_evidence"]
    assert all(s["ok"] for s in trace.steps) and trace.steps[1]["args"] == {"episode_id": eid}
    assert trace.usage == {"input_token_count": 1000, "output_token_count": 300}
    # the words shown are exactly the chosen options, rendered by code from the packet, in both languages
    opts = {o.option_id: o for o in sentence_options(main, view.clip.meta, "fan")}
    for lang in ("en", "ko"):
        assert answer["sentences"][lang] == [opts[i].text[lang] for i in ids]
    assert answer["kind"] == "moment" and answer["episode_id"] == eid
    assert answer["evidence_hash"] == main.evidence_hash
    # the options the model saw were in the viewer's language and carried no numbers it could change
    seen = gw.results[0]["options"]
    assert {o["option_id"] for o in seen} == set(opts) and seen[0]["text"] == opts[seen[0]["option_id"]].text["en"]


def test_tools_are_bound_to_the_question_time(main):
    early = tools.view_at("clip_001", max(0.0, main.episode.detected_at_s - 0.5 - tools.view_at("clip_001").clip.start_s))
    assert main.episode.episode_id not in early.revisions
    gw = FakeAsk([([("get_evidence", main.episode.episode_id)], decline("not_yet_known"))])
    answer, trace = asyncio.run(run_ask(early, "Will they score from this?", "fan", "ko", gw))
    tool_step = next(s for s in trace.steps if s["step"] == "tool")
    assert not tool_step["ok"] and "no moment" in tool_step["summary"]
    assert trace.result == "foundry_live" and answer["kind"] == "decline"
    assert answer["sentences"]["ko"] == [ASK_DECLINES["not_yet_known"]["ko"]]
    # a later reading never reaches the prompt either
    assert main.episode.episode_id not in gw.prompts[0]


def test_one_sided_or_invented_answers_get_one_repair_then_the_fallback(view, main):
    one_sided = moment(main, pick(view, main, "fan", ["exposed"]))
    invented = moment(main, ["s99"])
    gw = FakeAsk([([("answer_options", main.episode.episode_id)], one_sided), ([], invented)])
    answer, trace = asyncio.run(run_ask(view, "What was exposed?", "fan", "en", gw))
    codes = [s.get("error") for s in trace.steps if s["step"] == "validation"]
    assert codes == ["one_sided", "unknown_option"] and trace.repairs == 1 and trace.runs == 2
    assert trace.result == "fallback" and trace.reason == "invalid:unknown_option"
    assert "one_sided" in gw.prompts[1]  # the repair is told why
    assert answer == fallback_answer(view, "fan")


def test_a_repair_can_succeed(view, main):
    good = moment(main, pick(view, main, "analyst", ["restricted", "exposed", "caveat"]))
    gw = FakeAsk([([], moment(main, []).model_copy(update={"episode_id": "ep_invented"})), ([], good)])
    answer, trace = asyncio.run(run_ask(view, "Show me the evidence", "analyst", "en", gw))
    assert trace.result == "foundry_live" and trace.repairs == 1 and answer["episode_id"] == main.episode.episode_id
    assert [s["step"] for s in trace.steps] == ["agent", "validation", "agent_repair", "validation"]


def test_an_intent_question_gets_the_fixed_note_then_what_the_shape_did(view, main):
    ids = pick(view, main, "fan", ["restricted", "exposed"])
    noted = moment(main, ids).model_copy(update={"note_code": "intent_not_inferred"})
    answer, trace = asyncio.run(run_ask(view, "Why did the coach want this?", "fan", "en",
                                        FakeAsk([([("answer_options", main.episode.episode_id)], noted)])))
    assert trace.result == "foundry_live" and answer["kind"] == "moment" and answer["note_code"] == "intent_not_inferred"
    assert answer["sentences"]["en"][0] == ASK_DECLINES["intent_not_inferred"]["en"] and len(answer["sentences"]["en"]) == 3
    # only that note may preface an answer; anything else must be a decline
    wrong = moment(main, ids).model_copy(update={"note_code": "outside_scope"})
    _, trace = asyncio.run(run_ask(view, "q", "fan", "en", FakeAsk([([], wrong), ([], wrong)])))
    assert trace.result == "fallback" and trace.reason == "invalid:note_not_allowed"


def test_nothing_found_yet_is_the_only_time_no_moment_yet_is_accepted(view):
    gw = FakeAsk([([], decline("no_moment_yet")), ([], decline("no_moment_yet"))])
    _, trace = asyncio.run(run_ask(view, "Anything?", "fan", "en", gw))
    assert [s.get("error") for s in trace.steps if s["step"] == "validation"] == ["moments_exist", "moments_exist"]
    start = tools.view_at("clip_001", 0.0)
    assert not start.revisions
    assert fallback_answer(start, "fan")["note_code"] == "no_moment_yet"


def test_timeouts_service_errors_and_the_tool_budget(view, main):
    eid = main.episode.episode_id
    slow = FakeAsk([([], decline("outside_scope"))], delay_s=0.5)
    _, trace = asyncio.run(run_ask(view, "q", "fan", "en", slow, AskLimits(deadline_s=0.1)))
    assert trace.result == "fallback" and trace.reason == "timeout"
    broken = FakeAsk([([], PermissionError("403"))])
    _, trace = asyncio.run(run_ask(view, "q", "fan", "en", broken))
    assert trace.reason == "service_error:PermissionError" and trace.runs == 1
    greedy = FakeAsk([([("get_history", eid)] * 10, decline("outside_scope"))])
    _, trace = asyncio.run(run_ask(view, "q", "fan", "en", greedy, AskLimits(max_tool_calls=3)))
    assert trace.tool_calls == 3 and sum(not s["ok"] for s in trace.steps if s["step"] == "tool") == 7
    assert "budget" in greedy.results[-1]["error"]


# ------------------------------------------------------------------ API
def _create(c, clip="clip_001"):
    r = c.post("/api/replays", json={"clip_id": clip}).json()
    return r["replay_id"], {"x-session-token": r["session_token"]}


def test_api_offline_answers_with_the_fallback_and_says_why():
    api.STORE.sessions.clear()
    c = TestClient(api.app)
    rid, H = _create(c)
    end = api.STORE.get(rid).clip.end_s
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": end, "expected_epoch": 1}, headers=H)
    assert c.post(f"/api/replays/{rid}/ask", json={"question": "hi"}).status_code == 403
    assert c.post(f"/api/replays/{rid}/ask", json={"question": ""}, headers=H).status_code == 422
    r = c.post(f"/api/replays/{rid}/ask", json={"question": "Why was the space open?", "language": "ko"}, headers=H)
    body = r.json()
    assert r.status_code == 200 and body["source_mode"] == "deterministic_fallback"
    assert body["trace"]["result"] == "fallback" and body["trace"]["reason"] == api.CLOUD.disabled_reason
    assert body["answer"]["kind"] == "moment" and body["answer"]["sentences"]["ko"]
    assert c.get(f"/api/replays/{rid}/asks", headers=H).json()[-1]["ask_id"] == body["ask_id"]


def test_api_answers_from_the_agent_and_counts_against_the_daily_cap(monkeypatch, view, main):
    from app.agents.cloud import Limits

    eid = main.episode.episode_id
    ids = pick(view, main, "fan", ["restricted", "exposed"])
    gw = FakeAsk([([("answer_options", eid)], moment(main, ids))])
    cloud = CloudInsights(lambda: gw, model="fake-deployment", limits=Limits(daily_pipelines=1))
    monkeypatch.setattr(api, "CLOUD", cloud)
    api.STORE.sessions.clear()
    c = TestClient(api.app)
    rid, H = _create(c)
    s = api.STORE.get(rid)
    s.cloud = None  # this test is about the question, not the background narration
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": s.clip.end_s, "expected_epoch": 1},
           headers=H)
    body = c.post(f"/api/replays/{rid}/ask", json={"question": "What did the press shut?"}, headers=H).json()
    assert body["source_mode"] == "foundry_live" and body["answer"]["episode_id"] == eid
    assert [st.get("name") for st in body["trace"]["steps"] if st["step"] == "tool"] == ["answer_options"]
    assert cloud.status["calls_total"] == 1 and cloud.status["last_call_succeeded"] is True
    again = c.post(f"/api/replays/{rid}/ask", json={"question": "And then?"}, headers=H).json()
    assert again["source_mode"] == "deterministic_fallback" and again["trace"]["reason"] == "daily_limit"
