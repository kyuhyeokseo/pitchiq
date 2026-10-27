"""Recap workflow with a fake model (no network): the Agent Framework graph gather -> recap_agent -> check ->
render | fallback, causal read-only tools, code-checked choices and code-computed tags, the deterministic fallback and
the shared daily cap. These tests prove the app-side workflow only; they are not evidence that a real Foundry call
works."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("agent_framework")

from app.agents import smoke, tools  # noqa: E402
from app.agents.ask import AskLimits, sentence_options  # noqa: E402
from app.agents.cloud import CloudInsights, Limits  # noqa: E402
from app.agents.gateway import ModelReply  # noqa: E402
from app.agents.recap import (  # noqa: E402
    RecapChoice,
    RecapItemChoice,
    RecapLimits,
    RecapProfile,
    _fallback_options,
    as_choice,
    check_profile,
    fallback_choice,
    fallback_recap,
    followed_moments,
    involved_players,
    recap_prompt,
    render_recap,
    validate_recap,
)
from app.agents.recap_workflow import (  # noqa: E402
    EXECUTOR_IDS,
    MERMAID_PATH,
    build_recap_workflow,
    run_recap,
)
from app.agents.validate import PlanError  # noqa: E402
from app.narrative.registry import ASK_DECLINES  # noqa: E402

FAN = RecapProfile()


@pytest.fixture(scope="module")
def view():
    return tools.view_at("clip_001")


def latest(view):
    return sorted((r[-1] for r in view.revisions.values()), key=lambda p: p.episode.detected_at_s)


@pytest.fixture(scope="module")
def complete(view):
    return next(p for p in latest(view) if p.episode.analysis_status == "complete_tradeoff")


@pytest.fixture(scope="module")
def partial(view, complete):
    return next(p for p in latest(view) if p.episode.analysis_status == "partial"
                and p.episode.detected_at_s > complete.episode.detected_at_s)


def ids(view, packet, kinds, audience="fan"):
    opts = sentence_options(packet, view.clip.meta, audience)
    return [next(o.option_id for o in opts if o.kind == k) for k in kinds]


def item(packet, option_ids):
    return RecapItemChoice(episode_id=packet.episode.episode_id, option_ids=option_ids)


def recap_of(*items):
    return RecapChoice(items=list(items))


class FakeRecap:
    """Plays one scripted agent run: the tool calls to make (name, episode_id), then the reply."""
    model = "fake-deployment"

    def __init__(self, calls, reply, delay_s: float = 0.0):
        self.calls, self.reply, self.delay_s = list(calls), reply, delay_s
        self.prompts: list[str] = []
        self.results: list[dict] = []
        self.tool_names: list[str] = []

    async def recap(self, prompt, tool_fns):
        self.prompts.append(prompt)
        by_name = {f.__name__: f for f in tool_fns}
        self.tool_names = sorted(by_name)
        for name, eid in self.calls:
            self.results.append(by_name[name](eid))
        await asyncio.sleep(self.delay_s)
        if isinstance(self.reply, Exception):
            raise self.reply
        return ModelReply(self.reply, {"input_token_count": 1200, "output_token_count": 80})

    async def close(self):
        pass


def steps(trace):
    return [s["step"] for s in trace.steps]


# ------------------------------------------------------------------ the workflow run
def test_a_valid_recap_shows_only_option_texts_in_replay_order(view, complete, partial):
    later = item(partial, ids(view, partial, ["restricted", "outcome"]))
    earlier = item(complete, ids(view, complete, ["exposed", "restricted", "outcome"]))
    eids = [complete.episode.episode_id, partial.episode.episode_id]
    gw = FakeRecap([("answer_options", eids[1]), ("answer_options", eids[0]), ("get_evidence", eids[0])],
                   recap_of(later, earlier))  # the model lists the later moment first
    out, trace = asyncio.run(run_recap(view, FAN, gw))
    assert trace.result == "foundry_live" and trace.reason is None and trace.runs == 1 and trace.tool_calls == 3
    assert steps(trace) == ["gather", "recap_agent", "tool", "tool", "tool", "check", "render"]
    assert all(s["ok"] for s in trace.steps) and all("ms" in s for s in trace.steps)
    assert trace.usage == {"input_token_count": 1200, "output_token_count": 80}
    assert out["kind"] == "recap" and [i["episode_id"] for i in out["items"]] == eids  # replay order, by code
    assert out["note_sentences"] == {"en": [], "ko": []}
    for it, (packet, chosen) in zip(out["items"], [(complete, earlier), (partial, later)]):
        opts = {o.option_id: o for o in sentence_options(packet, view.clip.meta, "fan")}
        for lang in ("en", "ko"):  # exactly the chosen options, in the chosen order, rendered by code
            assert it["sentences"][lang] == [opts[i].text[lang] for i in chosen.option_ids]
        assert it["evidence_hash"] == packet.evidence_hash and it["episode_revision"] == packet.episode.revision
        assert it["detected_at_s"] == round(packet.episode.detected_at_s - view.clip.start_s, 2)
    assert out["analyzed_through_s"] == round(view.t - view.clip.start_s, 2)
    # the options the agent saw were in the viewer's language
    seen = gw.results[1]["options"]
    opts = {o.option_id: o for o in sentence_options(complete, view.clip.meta, "fan")}
    assert {o["option_id"] for o in seen} == set(opts) and seen[0]["text"] == opts[seen[0]["option_id"]].text["en"]


def test_the_agent_gets_two_read_only_tools_without_a_time_argument(view, complete):
    gw = FakeRecap([], recap_of(item(complete, ids(view, complete, ["restricted", "exposed"]))))
    asyncio.run(run_recap(view, FAN, gw))
    assert gw.tool_names == ["answer_options", "get_evidence"]
    greedy = FakeRecap([("get_evidence", complete.episode.episode_id)] * 10, gw.reply)
    _, trace = asyncio.run(run_recap(view, FAN, greedy, RecapLimits(max_tool_calls=3)))
    tool_steps = [s for s in trace.steps if s["step"] == "tool"]
    assert trace.tool_calls == 3 and sum(not s["ok"] for s in tool_steps) == 7
    assert "budget" in greedy.results[-1]["error"] and trace.result == "foundry_live"


@pytest.mark.parametrize("case", ["unknown_option", "one_sided", "follow_player_missing", "duplicate_moment",
                                  "item_count", "sentence_count", "duplicate_option", "unknown_moment"])
def test_an_invalid_choice_shows_the_fallback_with_the_code_and_no_repair(view, complete, partial, case):
    profile = FAN
    good = item(complete, ids(view, complete, ["restricted", "exposed"]))
    others = [p for p in latest(view) if p.episode.episode_id != complete.episode.episode_id]
    choice = {
        "unknown_option": lambda: recap_of(item(complete, ["s99"])),
        "one_sided": lambda: recap_of(item(complete, ids(view, complete, ["exposed", "outcome"]))),
        "duplicate_moment": lambda: recap_of(good, good),
        "item_count": lambda: recap_of(*[item(p, ids(view, p, ["restricted"])) for p in latest(view)[:4]]),
        "sentence_count": lambda: recap_of(item(complete, ids(view, complete, ["restricted", "exposed", "change",
                                                                               "outcome"]))),
        "duplicate_option": lambda: recap_of(item(complete, ids(view, complete, ["restricted", "restricted"]))),
        "unknown_moment": lambda: recap_of(RecapItemChoice(episode_id="ep_invented", option_ids=["s1"])),
    }.get(case, lambda: recap_of(good))()
    if case == "follow_player_missing":
        player = next(pid for p in others for pid in sorted(involved_players(p))
                      if pid not in involved_players(complete))
        profile = RecapProfile(follow_player=player)
    gw = FakeRecap([("answer_options", complete.episode.episode_id)], choice)
    out, trace = asyncio.run(run_recap(view, profile, gw))
    check = next(s for s in trace.steps if s["step"] == "check")
    assert not check["ok"] and check["error"] == case
    assert trace.result == "fallback" and trace.reason == f"invalid:{case}" and steps(trace)[-1] == "fallback"
    assert len(gw.prompts) == 1 and trace.runs == 1  # no repair loop
    assert out == fallback_recap(view, profile)


def test_timeouts_service_errors_and_bad_output_fall_back(view, complete):
    good = recap_of(item(complete, ids(view, complete, ["restricted", "exposed"])))
    cases = [(FakeRecap([], good, delay_s=0.5), RecapLimits(deadline_s=0.1), "timeout"),
             (FakeRecap([], PermissionError("403")), RecapLimits(), "service_error:PermissionError"),
             (FakeRecap([], ValueError("not json")), RecapLimits(), "invalid_output")]
    for gw, limits, reason in cases:
        out, trace = asyncio.run(run_recap(view, FAN, gw, limits))
        agent = next(s for s in trace.steps if s["step"] == "recap_agent")
        assert not agent["ok"] and agent["error"] == reason
        assert trace.result == "fallback" and trace.reason == reason
        assert steps(trace) == ["gather", "recap_agent", "check", "fallback"]
        assert next(s for s in trace.steps if s["step"] == "check")["error"] == "no_choice"
        assert out == fallback_recap(view, FAN)
    _, trace = asyncio.run(run_recap(view, FAN, FakeRecap([], {"items": []})))  # not a RecapChoice
    assert trace.reason == "invalid:schema"


def test_a_later_moment_is_out_of_reach(view, complete):
    eid = complete.episode.episode_id
    early = tools.view_at("clip_001", complete.episode.detected_at_s - view.clip.start_s - 0.4)
    assert eid not in early.revisions and early.revisions
    gw = FakeRecap([("answer_options", eid), ("get_evidence", eid)],
                   recap_of(item(complete, ids(view, complete, ["restricted", "exposed"]))))
    out, trace = asyncio.run(run_recap(early, FAN, gw))
    assert all("no moment" in r["error"] for r in gw.results)  # the tools cannot see it
    assert [s["ok"] for s in trace.steps if s["step"] == "tool"] == [False, False]
    assert trace.reason == "invalid:unknown_moment" and eid not in gw.prompts[0]
    assert out["analyzed_through_s"] == round(early.t - early.clip.start_s, 2)
    for it in out["items"]:  # the fallback tells each moment as known then, never a later revision
        assert it["episode_id"] in early.revisions
        assert it["evidence_hash"] == early.packet(it["episode_id"]).evidence_hash
        assert it["detected_at_s"] <= out["analyzed_through_s"]


def test_no_moment_yet_is_a_fixed_note_without_an_agent_run():
    start = tools.view_at("clip_001", 0.0)
    assert not start.revisions
    out, trace = asyncio.run(run_recap(start, FAN, FakeRecap([], AssertionError("must not run"))))
    assert out["kind"] == "empty" and out["items"] == [] and trace.runs == 0 and trace.steps == []
    assert out["note_sentences"] == {lang: [ASK_DECLINES["no_moment_yet"][lang]] for lang in ("en", "ko")}
    assert trace.result == "fallback" and trace.reason == "no_moments" and out == fallback_recap(start, FAN)


# ------------------------------------------------------------------ code-computed tags and the fallback
def test_my_club_role_and_involves_followed_are_computed_by_code(view):
    packets = latest(view)
    player = sorted(involved_players(packets[-1]))[0]
    for club in ("blue", "red"):
        profile = RecapProfile(my_club=club, follow_player=player)
        chosen = [(p, sentence_options(p, view.clip.meta, "fan")[:1]) for p in packets[:2] + packets[-1:]]
        out = render_recap(view, profile, chosen)
        for it in out["items"]:
            p = view.packet(it["episode_id"])
            assert it["my_club_role"] == ("defending" if p.episode.defending_team_id == club else "attacking")
            assert it["involves_followed"] == (player in involved_players(p))
        prompt = json.loads(recap_prompt(view, profile))
        assert prompt["my_club"]["team_id"] == club and prompt["follow_player"]["player_id"] == player
        assert prompt["followed_player_involved"] is True
        tagged = {m["episode_id"]: m["my_club_role"] for m in prompt["moments"]}
        # a clip ends once the defence wins the ball, so in clip_001 Red defends every moment
        assert set(tagged.values()) == ({"defending"} if club == "red" else {"attacking"})
    plain = render_recap(view, FAN, [(packets[0], sentence_options(packets[0], view.clip.meta, "fan")[:1])])
    assert plain["items"][0]["my_club_role"] is None and plain["items"][0]["involves_followed"] is False
    with pytest.raises(ValueError, match="my_club"):
        check_profile(RecapProfile(my_club="green"), view)
    with pytest.raises(ValueError, match="follow_player"):
        check_profile(RecapProfile(follow_player="green_09"), view)
    check_profile(RecapProfile(my_club="blue", follow_player=player), view)


def test_the_fallback_prefers_the_followed_players_moments(view):
    base = {p.episode.episode_id for p, _ in fallback_choice(view, FAN)}
    roster = [r.player_id for r in view.clip.meta.roster]
    player, followed = next((pid, set(followed_moments(view, RecapProfile(follow_player=pid)))) for pid in roster
                            if 0 < len(followed_moments(view, RecapProfile(follow_player=pid))) <= 2
                            and not set(followed_moments(view, RecapProfile(follow_player=pid))) & base)
    profile = RecapProfile(follow_player=player)
    out = fallback_recap(view, profile)
    shown = [it["episode_id"] for it in out["items"]]
    # every followed moment is shown, unless it would open with a sentence one already shown opens with
    leads = {it["sentences"]["en"][0] for it in out["items"]}
    for e in followed - set(shown):
        p = view.packet(e)
        assert _fallback_options(p, sentence_options(p, view.clip.meta, "fan"))[0].text["en"] in leads
    assert followed & set(shown) and len(shown) == 3
    assert all(it["involves_followed"] == (it["episode_id"] in followed) for it in out["items"])
    times = [it["detected_at_s"] for it in out["items"]]
    assert times == sorted(times)
    # without a followed player the complete trade-off leads
    assert any(view.packet(e).episode.analysis_status == "complete_tradeoff" for e in base)


@pytest.mark.parametrize("clip_id", ["clip_001", "clip_003", "clip_009"])
def test_the_fallback_is_itself_a_valid_recap(clip_id):
    full = tools.view_at(clip_id)
    players = [r.player_id for r in full.clip.meta.roster][::4]
    for at in (3.0, 8.0, None):
        v = tools.view_at(clip_id, at)
        for profile in [RecapProfile(audience=a, follow_player=fp) for a in ("fan", "analyst") for fp in
                        [None, *players]]:
            chosen = fallback_choice(v, profile)
            assert bool(chosen) == bool(v.revisions)
            if chosen:
                ok = validate_recap(as_choice(chosen), v, profile)
                assert [(p.episode.episode_id, [o.option_id for o in o_]) for p, o_ in ok] == \
                       [(p.episode.episode_id, [o.option_id for o in o_]) for p, o_ in chosen]


def test_validate_recap_codes_directly(view, complete):
    with pytest.raises(PlanError) as e:
        validate_recap(RecapChoice(items=[]), view, FAN)
    assert e.value.code == "item_count"


def test_two_moments_that_open_with_the_same_sentence_are_refused(view, complete):
    a = ids(view, complete, ["restricted", "exposed"])
    lead_of = lambda p: next(o.text["en"] for o in sentence_options(p, view.clip.meta, "fan")  # noqa: E731
                             if o.option_id == ids(view, p, ["restricted"])[0])
    # a partial moment of the same clip whose press reads exactly like the complete one's
    partial = next(p for p in latest(view) if p.episode.analysis_status == "partial"
                   and ids(view, p, ["restricted"]) and lead_of(p) == lead_of(complete))
    b = ids(view, partial, ["restricted"])
    lead = lambda p, i: next(o.text["en"] for o in sentence_options(p, view.clip.meta, "fan") if o.option_id == i)
    assert lead(complete, a[0]) == lead(partial, b[0])  # the same press, read the same way
    with pytest.raises(PlanError) as e:
        validate_recap(recap_of(item(complete, a), item(partial, b)), view, FAN)
    assert e.value.code == "repeated_lead"
    # opened with another of its options, the later moment reads differently and passes
    validate_recap(recap_of(item(complete, a), item(partial, ids(view, partial, ["partial", "restricted"]))), view, FAN)
    # the deterministic recap never opens two moments with the same sentence
    for clip_id in ("clip_001", "clip_004", "clip_008"):
        v = tools.view_at(clip_id)
        for profile in (FAN, RecapProfile(audience="analyst"), RecapProfile(my_club="blue", follow_player="blue_09")):
            leads = [opts[0].text[profile.language] for _, opts in fallback_choice(v, profile)]
            assert len(leads) == len(set(leads)), (clip_id, profile)


# ------------------------------------------------------------------ the graph
def test_the_workflow_has_the_expected_executors_and_edges():
    from agent_framework import WorkflowViz

    wf = build_recap_workflow()
    assert set(wf.executors) == set(EXECUTOR_IDS) and wf.get_start_executor().id == "gather"
    groups = [(type(g).__name__, g.source_executor_ids, g.target_executor_ids) for g in wf.edge_groups
              if not any(s.startswith("internal:") for s in g.source_executor_ids)]
    assert groups == [("SingleEdgeGroup", ["gather"], ["recap_agent"]),
                      ("SingleEdgeGroup", ["recap_agent"], ["check"]),
                      ("SwitchCaseEdgeGroup", ["check"], ["render", "fallback"])]
    # the committed diagram is this graph (regenerate with: python -m app.agents.recap_workflow)
    assert MERMAID_PATH.read_text() == WorkflowViz(wf).to_mermaid() + "\n"


# ------------------------------------------------------------------ cloud loop and smoke script
def test_cloud_recap_shares_the_daily_cap_with_ask(view, complete):
    good = recap_of(item(complete, ids(view, complete, ["restricted", "exposed"])))
    gw = FakeRecap([("answer_options", complete.episode.episode_id)], good)
    cloud = CloudInsights(lambda: gw, model="fake-deployment", limits=Limits(daily_pipelines=1))
    out, trace = cloud.recap(view, FAN).result(timeout=20)
    assert trace["result"] == "foundry_live" and out["items"][0]["episode_id"] == complete.episode.episode_id
    assert cloud.status["pipelines_today"] == 1 and cloud.status["calls_total"] == 1
    assert cloud.status["last_call_succeeded"] is True
    assert cloud.recap(view, FAN) == "daily_limit"
    assert cloud.ask(view, "And then?", "fan", "en", AskLimits()) == "daily_limit"
    empty, trace = cloud.recap(tools.view_at("clip_001", 0.0), FAN).result(timeout=20)  # no run, no slot
    assert empty["kind"] == "empty" and trace["runs"] == 0 and cloud.status["pipelines_today"] == 1
    assert CloudInsights(None, disabled_reason="offline").recap(view, FAN) == "disabled"


def test_cloud_recap_falls_back_when_the_gateway_cannot_be_built(view):
    def broken():
        raise RuntimeError("no settings")
    cloud = CloudInsights(broken, model="fake-deployment")
    out, trace = cloud.recap(view, FAN).result(timeout=20)
    assert trace["result"] == "fallback" and trace["reason"] == "gateway_error:RuntimeError"
    assert out == fallback_recap(view, FAN)


def test_smoke_recap_prints_the_trace_and_sentences(monkeypatch, capsys, view, complete):
    good = recap_of(item(complete, ids(view, complete, ["restricted", "exposed"])))

    class Gateway:
        @classmethod
        def from_env(cls):
            return FakeRecap([("answer_options", complete.episode.episode_id)], good)

    monkeypatch.setattr(smoke, "FoundryGateway", Gateway)
    assert asyncio.run(smoke._recap("clip_001", RecapProfile(language="ko", my_club="blue"))) == 0
    printed = capsys.readouterr().out
    assert '"result": "foundry_live"' in printed and "my_club=attacking" in printed
    opts = {o.option_id: o for o in sentence_options(complete, view.clip.meta, "fan")}
    assert all(opts[i].text["ko"] in printed for i in good.items[0].option_ids)
    # an unknown club stops before any gateway is made
    monkeypatch.setattr(smoke, "FoundryGateway", None)
    assert asyncio.run(smoke._recap("clip_001", RecapProfile(my_club="green"))) == 2
    # the command line takes the recap flags, and without settings nothing is called
    monkeypatch.setenv("PITCHIQ_DOTENV", "0")
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("FOUNDRY_MODEL", raising=False)
    assert smoke.main(["--clip", "clip_001", "--recap", "--my-club", "blue", "--follow", "blue_09"]) == 2


# ------------------------------------------------------------------ API
def _replay_at_end(c, clip="clip_001"):
    from app.api import app as api
    r = c.post("/api/replays", json={"clip_id": clip}).json()
    rid, H = r["replay_id"], {"x-session-token": r["session_token"]}
    end = api.STORE.get(rid).clip.end_s
    c.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": end, "expected_epoch": 1}, headers=H)
    return rid, H


def test_api_recap_offline_is_the_deterministic_recap_and_checks_the_profile():
    from fastapi.testclient import TestClient
    from app.api import app as api

    api.STORE.sessions.clear()
    c = TestClient(api.app)
    rid, H = _replay_at_end(c)
    body = {"audience": "fan", "language": "ko", "my_club": "red", "follow_player": "blue_09"}
    assert c.post(f"/api/replays/{rid}/recap/agent", json=body).status_code == 403
    r = c.post(f"/api/replays/{rid}/recap/agent", json=body, headers=H)
    j = r.json()
    assert r.status_code == 200 and j["source_mode"] == "deterministic_fallback"
    assert j["trace"]["reason"] == api.CLOUD.disabled_reason and j["recap"]["kind"] == "recap"
    assert all(it["sentences"]["ko"] and it["my_club_role"] in ("defending", "attacking") for it in j["recap"]["items"])
    for bad in ({"my_club": "green"}, {"follow_player": "blue_99"}, {"audience": "coach"}, {"extra": 1}):
        assert c.post(f"/api/replays/{rid}/recap/agent", json=bad, headers=H).status_code == 422


def test_api_recap_from_the_workflow_agent(monkeypatch, view, complete):
    from fastapi.testclient import TestClient
    from app.api import app as api

    good = recap_of(item(complete, ids(view, complete, ["restricted", "exposed"])))
    gw = FakeRecap([("answer_options", complete.episode.episode_id)], good)
    monkeypatch.setattr(api, "CLOUD", CloudInsights(lambda: gw, model="fake-deployment"))
    api.STORE.sessions.clear()
    c = TestClient(api.app)
    rid, H = _replay_at_end(c)
    j = c.post(f"/api/replays/{rid}/recap/agent", json={"audience": "fan", "language": "en"}, headers=H).json()
    assert j["source_mode"] == "foundry_live"
    assert [it["episode_id"] for it in j["recap"]["items"]] == [complete.episode.episode_id]
    assert [st["step"] for st in j["trace"]["steps"]][:2] == ["gather", "recap_agent"]
