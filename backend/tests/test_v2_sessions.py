"""Sessions on a public link: nobody is locked out when the store is full, leaving a clip frees its replay, unused
replays are dropped, and an evicted replay's socket closes with 4410 so the client starts a new one."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.app import STORE, app
from app.replay import session as sess


@pytest.fixture()
def client():
    STORE.sessions.clear()
    return TestClient(app)


def _create(c, clip="clip_001"):
    r = c.post("/api/replays", json={"clip_id": clip})
    assert r.status_code == 200
    body = r.json()
    return body["replay_id"], {"x-session-token": body["session_token"]}


def test_a_full_store_evicts_the_least_recently_used_replay_instead_of_refusing(client):
    made = [_create(client) for _ in range(sess.MAX_SESSIONS + 5)]
    assert len(STORE.sessions) == sess.MAX_SESSIONS
    oldest, newest = made[0], made[-1]
    assert oldest[0] not in STORE.sessions and newest[0] in STORE.sessions
    # using a replay keeps it: touch the second-oldest, then fill again; the untouched ones go first
    survivor = made[5]
    assert client.get(f"/api/replays/{survivor[0]}/snapshot", headers=survivor[1]).status_code == 200
    for _ in range(sess.MAX_SESSIONS - 1):
        _create(client)
    assert survivor[0] in STORE.sessions
    assert client.get(f"/api/replays/{oldest[0]}/snapshot", headers=oldest[1]).status_code == 404


def test_delete_frees_a_replay_and_checks_the_token(client):
    rid, H = _create(client)
    assert client.delete(f"/api/replays/{rid}").status_code == 403
    assert client.delete(f"/api/replays/{rid}", headers=H).status_code == 204
    assert rid not in STORE.sessions
    assert client.delete(f"/api/replays/{rid}", headers=H).status_code == 204  # idempotent
    assert client.get(f"/api/replays/{rid}/snapshot", headers=H).status_code == 404


def test_unwatched_replays_are_dropped_sooner_than_watched_ones(client):
    watched, _ = _create(client)
    unwatched, _ = _create(client)
    now = time.monotonic()
    for rid in (watched, unwatched):
        STORE.sessions[rid].touched = now - sess.UNWATCHED_TTL_S - 1
    STORE.sessions[watched].sockets = 1
    with STORE.lock:
        STORE.gc(now)
    assert watched in STORE.sessions and unwatched not in STORE.sessions
    STORE.sessions[watched].touched = now - sess.IDLE_TTL_S - 1
    with STORE.lock:
        STORE.gc(now)
    assert watched not in STORE.sessions


def test_an_evicted_replays_socket_closes_with_4410(client):
    rid, H = _create(client)
    token = H["x-session-token"]
    with client.websocket_connect(f"/ws/replays/{rid}?token={token}") as ws:
        assert ws.receive_json()["type"] == "state_snapshot"
        assert STORE.sessions[rid].sockets == 1
        STORE.delete(rid)
        with pytest.raises(WebSocketDisconnect) as e:
            while True:
                ws.receive_json()
        assert e.value.code == 4410
    assert client.get("/api/health").json()["sockets"] == 0


def test_the_socket_tells_the_client_when_the_clip_ends(client):
    rid, H = _create(client)
    token = H["x-session-token"]
    end = STORE.sessions[rid].clip.end_s
    assert client.post(f"/api/replays/{rid}/control", json={"action": "seek", "value": end - 0.6, "expected_epoch": 1},
                       headers=H).status_code == 200
    client.post(f"/api/replays/{rid}/control", json={"action": "set_speed", "value": 2, "expected_epoch": 2}, headers=H)
    stopped = False
    try:
        with client.websocket_connect(f"/ws/replays/{rid}?token={token}") as ws:
            assert ws.receive_json()["payload"]["playing"] is False
            client.post(f"/api/replays/{rid}/control", json={"action": "play", "expected_epoch": 2}, headers=H)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 10:  # frames, then a snapshot once the clip has ended
                m = ws.receive_json()
                if m["type"] == "state_snapshot" and m["payload"]["playing"] is False:
                    stopped = True
                    break
    finally:
        STORE.delete(rid)  # ends the server loop whatever happened above
    assert stopped, "no snapshot after the clip ended"
