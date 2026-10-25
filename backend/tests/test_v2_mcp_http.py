"""The MCP endpoint inside the web app (/mcp): the five read-only match tools over streamable HTTP (stateless, JSON
responses) behind Host/Origin checks, running in the app's lifespan. In-process ASGI only: no server is started."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from app.agents import mcp_server, tools  # noqa: E402
from app.api import app as api  # noqa: E402

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LOCAL = "http://localhost:8000"


def rpc(c: TestClient, method: str, params: dict | None = None, path: str = "/mcp", headers: dict | None = None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return c.post(path, json=body, headers={**HEADERS, **(headers or {})})


def test_the_endpoint_lists_exactly_the_five_read_only_tools():
    with TestClient(api.app, base_url=LOCAL) as c:
        init = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "test", "version": "0"}})
        assert init.status_code == 200 and init.headers["content-type"].startswith("application/json")
        assert init.json()["result"]["serverInfo"]["name"] == "pitchiq"
        assert "mcp-session-id" not in init.headers  # stateless: nothing to keep between requests
        listed = rpc(c, "tools/list").json()["result"]["tools"]
    assert sorted(t["name"] for t in listed) == sorted(f.__name__ for f in tools.TOOLS) and len(listed) == 5
    for t in listed:
        assert t["annotations"]["readOnlyHint"] is True and t["annotations"]["destructiveHint"] is False


def test_a_call_over_http_returns_exactly_what_the_tool_returns():
    want = json.loads(json.dumps(tools.defensive_moments("clip_001", 10.0)))
    with TestClient(api.app, base_url=LOCAL) as c:
        res = rpc(c, "tools/call", {"name": "defensive_moments",
                                    "arguments": {"clip_id": "clip_001", "at_s": 10.0}}).json()["result"]
    assert res.get("isError") is not True
    assert json.loads(res["content"][0]["text"]) == want
    assert want["analyzed_through_s"] == 10.0 and want["moments"]  # a real answer, cut at the asked moment


def test_the_sdk_client_works_against_the_mounted_endpoint():
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async def go():
        async with api.app.router.lifespan_context(api.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=api.app), base_url=LOCAL) as http:
                async with Client(streamable_http_client(f"{LOCAL}/mcp", http_client=http)) as client:
                    listed = await client.list_tools()
                    res = await client.call_tool("moment_history", {"clip_id": "clip_001", "episode_id": eid,
                                                                    "at_s": 10.0})
                    bad = await client.call_tool("defensive_moments", {"clip_id": "clip_999"})
                    return listed, res, bad

    eid = tools.defensive_moments("clip_001", 10.0)["moments"][0]["episode_id"]
    listed, res, bad = asyncio.run(go())
    assert {t.name for t in listed.tools} == {f.__name__ for f in tools.TOOLS}
    assert not res.is_error
    assert json.loads(res.content[0].text) == json.loads(json.dumps(tools.moment_history("clip_001", eid, 10.0)))
    # a tool's own error reaches the client with its hint, not as a generic failure
    assert bad.is_error and "list_clips" in bad.content[0].text


def test_mcp_and_mcp_slash_are_the_same_endpoint_and_only_post_is_served():
    with TestClient(api.app, base_url=LOCAL) as c:
        a, b = rpc(c, "tools/list"), rpc(c, "tools/list", path="/mcp/")
        assert a.status_code == b.status_code == 200 and a.json() == b.json()
        for method in ("GET", "DELETE"):  # no server-to-client stream and no sessions to end
            r = c.request(method, "/mcp", headers={"Accept": "text/event-stream"})
            assert r.status_code == 405 and r.headers["allow"] == "POST"
    paths = [getattr(r, "path", None) for r in api.app.routes]
    mounts = [i for i, r in enumerate(api.app.routes) if isinstance(r, Mount) and r.path == ""]
    if api._DIST.is_dir():  # the frontend's "/" mount exists once the frontend is built (not before, as in CI)
        assert mounts
    assert all(paths.index(p) < m for p in ("/mcp", "/mcp/") for m in mounts)  # ahead of the frontend's "/"


def test_only_loopback_and_configured_public_hosts_get_through(monkeypatch):
    monkeypatch.delenv(mcp_server.PUBLIC_HOSTS_ENV, raising=False)
    with TestClient(api.app, base_url="https://pitchiq.example.org") as c:
        assert rpc(c, "tools/list").status_code == 421  # not configured: rejected
        assert rpc(c, "tools/list", headers={"Host": "127.0.0.1:8000"}).status_code == 200

    monkeypatch.setenv(mcp_server.PUBLIC_HOSTS_ENV, "https://PitchIQ.example.org/, other.example.org")
    with TestClient(api.app, base_url="https://pitchiq.example.org") as c:  # hosts are read when the app starts
        assert rpc(c, "tools/list").status_code == 200
        assert rpc(c, "tools/list", headers={"Host": "pitchiq.example.org:443"}).status_code == 200
        assert rpc(c, "tools/list", headers={"Origin": "https://pitchiq.example.org"}).status_code == 200
        assert rpc(c, "tools/list", headers={"Host": "evil.example"}).status_code == 421
        assert rpc(c, "tools/list", headers={"Host": "pitchiq.example.org.evil.example"}).status_code == 421
        assert rpc(c, "tools/list", headers={"Origin": "https://evil.example"}).status_code == 403
        assert rpc(c, "tools/list", headers={"Origin": "http://pitchiq.example.org"}).status_code == 403
        assert c.get("/api/health").status_code == 200  # the checks guard /mcp only


def test_public_hosts_parsing():
    assert mcp_server.public_hosts("") == []
    assert mcp_server.public_hosts(" https://A.example.org/, b.example.org ,,a.example.org") == [
        "a.example.org", "b.example.org"]
    assert mcp_server.public_hosts("pitchiq.example.org:443, https://c.example.org:8443/mcp") == [
        "pitchiq.example.org", "c.example.org"]  # a port or a path is dropped, so the plain Host still matches


def test_nested_lifespans_hand_the_endpoint_back_to_the_outer_one():
    with TestClient(api.app, base_url=LOCAL) as outer:
        with TestClient(api.app, base_url=LOCAL) as inner:
            assert rpc(inner, "tools/list").status_code == 200
        assert rpc(outer, "tools/list").status_code == 200


def test_the_rest_of_the_api_runs_inside_the_lifespan():
    with TestClient(api.app, base_url=LOCAL) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        clips = c.get("/api/clips").json()
        assert clips and clips[0]["clip_id"] == "clip_001"
        r = c.post("/api/replays", json={"clip_id": "clip_001"})
        assert r.status_code == 200
        rid, token = r.json()["replay_id"], r.json()["session_token"]
        assert c.delete(f"/api/replays/{rid}", headers={"x-session-token": token}).status_code == 204
    with TestClient(api.app, base_url=LOCAL) as c:  # a second start gets a fresh MCP session manager
        assert rpc(c, "tools/list").status_code == 200


def test_without_the_lifespan_the_endpoint_says_it_is_not_running():
    assert rpc(TestClient(api.app, base_url=LOCAL), "tools/list").status_code == 503
