"""Read-only match tools for agents and the MCP server: causal answers, numbers only from the analysis, validated
template explanations, and clear errors for unknown clips or moments."""

from __future__ import annotations

import asyncio

import pytest

from app.agents import tools


def _best(clip_id: str, at_s: float | None = None) -> dict:
    m = tools.defensive_moments(clip_id, at_s)["moments"]
    return max(m, key=lambda x: (x["analysis_status"] == "complete_tradeoff", x["story_priority"]))


def test_clips_list_with_clip_relative_lengths():
    clips = tools.list_clips()
    assert len(clips) == 12 and all(c["clip_id"].startswith("clip_") and c["duration_s"] > 0 for c in clips)


def test_answers_are_causal_and_never_see_later_revisions():
    best = _best("clip_001")
    at = best["detected_at_s"]
    early = tools.defensive_moments("clip_001", at)
    assert all(m["analyzed_until_s"] <= at + 1e-6 for m in early["moments"])
    hist = tools.moment_history("clip_001", best["episode_id"])["revisions"]
    assert [r["revision"] for r in hist] == list(range(1, len(hist) + 1))
    first = tools.moment_evidence("clip_001", best["episode_id"], hist[0]["analyzed_until_s"])
    assert first["revision"] == 1 and first["outcome"] == hist[0]["outcome"]
    with pytest.raises(tools.ToolError):
        tools.moment_evidence("clip_001", best["episode_id"], max(0.0, at - 0.5))


def test_evidence_numbers_come_from_the_packet_and_explanations_from_templates():
    best = _best("clip_001")
    ev = tools.moment_evidence("clip_001", best["episode_id"])
    assert ev["claims"] and all(c["metrics"] for c in ev["claims"])
    assert {c["dimension"] for c in ev["claims"]} >= {"restricted", "exposed"}
    for audience in ("fan", "analyst"):
        for language in ("en", "ko"):
            ex = tools.explain_moment("clip_001", best["episode_id"], audience, language)
            assert ex["sentences"] and ex["evidence_hash"] == ev["evidence_hash"]
    with pytest.raises(tools.ToolError):
        tools.explain_moment("clip_001", best["episode_id"], "coach", "en")


def test_unknown_clip_and_moment_say_what_to_call():
    with pytest.raises(tools.ToolError, match="list_clips"):
        tools.defensive_moments("clip_999")
    with pytest.raises(tools.ToolError, match="defensive_moments"):
        tools.moment_evidence("clip_001", "ep_nope")


def test_the_mcp_server_lists_read_only_tools_and_answers_a_call():
    pytest.importorskip("mcp")
    from app.agents.mcp_server import build_server

    srv = build_server()

    async def go():
        listed = await srv.list_tools()
        res = await srv.call_tool("defensive_moments", {"clip_id": "clip_003", "at_s": 10.0})
        return listed, res

    listed, res = asyncio.run(go())
    assert {t.name for t in listed} == {f.__name__ for f in tools.TOOLS}
    assert all(t.annotations.read_only_hint for t in listed)
    assert not getattr(res, "is_error", False) and "clip_003" in str(res)
