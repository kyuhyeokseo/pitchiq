"""Application Insights tracing through Azure Monitor OpenTelemetry (the optional ``telemetry`` extra), on only when
APPLICATIONINSIGHTS_CONNECTION_STRING is set.

What goes to Application Insights: the Agent Framework's own spans for agent runs, model calls and tool calls (with
token counts, never prompt or model text: sensitive data stays off), the Azure SDK calls to Foundry, a PitchIQ span
around each narration pipeline, Ask question and recap (result, runs, repairs, tool calls, validation code), and API
requests except the per-frame polling routes. Without the variable nothing is configured and ``span`` is a no-op.
"""

from __future__ import annotations

import os
from contextlib import contextmanager, nullcontext
from collections.abc import Iterator

try:  # opentelemetry-api comes with the Agent Framework (foundry extra); the offline app may not have it
    from opentelemetry import trace as _trace
except ImportError:  # pragma: no cover - depends on the installed extras
    _trace = None

STATE: dict = {"enabled": False, "error": None}
# per-frame polling would drown the requests worth looking at
EXCLUDED_URLS = "api/replays/[^/]+/overlays,api/replays/[^/]+/frames/,api/replays/[^/]+/snapshot,api/health,assets/"


def configure() -> dict:
    """Route OpenTelemetry to Application Insights if a connection string is set. Call once at start-up, before the
    FastAPI app is created (the FastAPI instrumentation patches the class). Telemetry never stops the app: any error
    is recorded in STATE and the app runs without it."""
    cs = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not cs or STATE["enabled"]:
        return STATE
    os.environ.setdefault("OTEL_SERVICE_NAME", "pitchiq")
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from agent_framework.observability import enable_instrumentation
    except ImportError as e:
        STATE["error"] = f"not installed: {e.name} (pip install .[foundry,telemetry])"
        return STATE
    try:
        # FastAPI is instrumented by instrument_app, on the app itself and with the polling routes left out
        configure_azure_monitor(connection_string=cs, enable_live_metrics=False,
                                instrumentation_options={"fastapi": {"enabled": False}, "django": {"enabled": False},
                                                         "flask": {"enabled": False}, "psycopg2": {"enabled": False}})
        enable_instrumentation(enable_sensitive_data=False)
    except Exception as e:  # noqa: BLE001 - a bad connection string must not take the replay down
        STATE["error"] = type(e).__name__
        return STATE
    STATE["enabled"], STATE["error"] = True, None
    return STATE


def instrument_app(app, tracer_provider=None) -> None:
    """Trace the app's API requests, except the per-frame polling routes (only when telemetry is on)."""
    if not STATE["enabled"]:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS, tracer_provider=tracer_provider)
    except Exception as e:  # noqa: BLE001 - requests then go untraced; agent and pipeline spans still flow
        STATE["error"] = f"fastapi:{type(e).__name__}"


@contextmanager
def span(name: str, **attributes) -> Iterator[object]:
    """A PitchIQ span; a no-op without a configured provider. Attributes are ids, codes and counts only."""
    if _trace is None:
        with nullcontext() as s:
            yield s
        return
    attrs = {f"pitchiq.{k}": v for k, v in attributes.items() if v is not None}
    with _trace.get_tracer("pitchiq").start_as_current_span(name, attributes=attrs) as s:
        yield s


def record(s: object, **attributes) -> None:
    """Add result attributes to a span from ``span`` (ignored when tracing is off)."""
    setter = getattr(s, "set_attribute", None)
    if setter is None:
        return
    for k, v in attributes.items():
        if v is not None:
            setter(f"pitchiq.{k}", v)
