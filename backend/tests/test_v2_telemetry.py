"""Application Insights tracing is opt-in: without APPLICATIONINSIGHTS_CONNECTION_STRING nothing is configured and the
PitchIQ spans are no-ops; with it, Azure Monitor is configured once, without sensitive data and without the per-frame
polling routes. These tests stub Azure Monitor; they are not evidence that traces reach Application Insights."""

from __future__ import annotations

import os
import sys
import types

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient

from app import telemetry
from app.api.app import app


@pytest.fixture()
def fresh_state(monkeypatch):
    monkeypatch.setattr(telemetry, "STATE", {"enabled": False, "error": None})
    return telemetry.STATE


def test_off_without_a_connection_string(fresh_state, monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert telemetry.configure() == {"enabled": False, "error": None}
    with telemetry.span("pitchiq.test", episode_id="ep_x") as s:  # a no-op span still works
        telemetry.record(s, result="foundry_live")
    assert TestClient(app).get("/api/health").json()["telemetry"]["enabled"] is False


def test_on_with_a_connection_string_and_never_sensitive(fresh_state, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))  # configure() sets OTEL_* defaults: keep them in this test
    calls = {}
    azure = types.ModuleType("azure.monitor.opentelemetry")
    azure.configure_azure_monitor = lambda **kw: calls.setdefault("azure", kw)
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", azure)
    import agent_framework.observability as obs
    monkeypatch.setattr(obs, "enable_instrumentation", lambda **kw: calls.setdefault("af", kw))
    os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = "fake-connection-string-for-tests"
    os.environ.pop("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", None)
    state = telemetry.configure()
    assert state == {"enabled": True, "error": None}
    assert calls["af"] == {"enable_sensitive_data": False}
    assert calls["azure"]["enable_live_metrics"] is False
    assert calls["azure"]["instrumentation_options"]["fastapi"] == {"enabled": False}  # instrument_app does it


def test_api_requests_are_traced_except_the_per_frame_polling(fresh_state):
    from fastapi import FastAPI
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exported = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exported))
    demo = FastAPI()
    for path in ("/api/health", "/api/replays/{rid}/overlays", "/api/replays/{rid}/frames/{k}",
                 "/api/replays/{rid}/snapshot", "/api/replays/{rid}/ask"):
        demo.add_api_route(path, lambda: {"ok": True}, methods=["GET"])

    @demo.websocket("/ws/replays/{rid}")
    async def stream(websocket: WebSocket, rid: str):
        await websocket.accept()
        for _ in range(5):
            await websocket.send_json({"type": "tracking_frame"})
        await websocket.close()
    fresh_state["enabled"] = True
    telemetry.instrument_app(demo, tracer_provider=provider)
    c = TestClient(demo)
    for url in ("/api/health", "/api/replays/r1/overlays", "/api/replays/r1/frames/3", "/api/replays/r1/snapshot",
                "/api/replays/r1/ask"):
        c.get(url)
    with c.websocket_connect("/ws/replays/r1") as ws:  # the per-frame stream stays out too
        for _ in range(5):
            ws.receive_json()
        assert ws.receive()["type"] == "websocket.close"
    spans = exported.get_finished_spans()
    assert [s.name for s in spans] == ["GET /api/replays/{rid}/ask"]  # no internal send/receive spans either


def test_a_failing_exporter_never_stops_the_app(fresh_state, monkeypatch):
    azure = types.ModuleType("azure.monitor.opentelemetry")

    def boom(**_kw):
        raise ValueError("bad connection string")
    azure.configure_azure_monitor = boom
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", azure)
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "nonsense")
    assert telemetry.configure() == {"enabled": False, "error": "ValueError"}
