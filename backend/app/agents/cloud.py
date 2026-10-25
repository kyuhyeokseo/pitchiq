"""Cloud insight pipeline: Analyst -> validation -> Producer -> validation, bounded and traced (spec 12.2-12.4).

Replay never waits for it: every revision already has the deterministic plan. When Foundry is configured, a
background loop asks the two agents for a plan of the same evidence; a plan that validates becomes a new
``suggested`` insight revision with ``source_mode`` foundry_live (or foundry_cached on a cache hit). Anything else
- timeout, error, invalid output after the one allowed repair, a full queue - leaves the fallback in place and is
recorded in the trace. Traces hold stage times, the deployment name, attempts, validation error codes and the
token counts the service reported; never secrets or prompts.

A daily cap (``Limits.daily_pipelines``, FOUNDRY_DAILY_LIMIT; 0 = none) bounds the live pipelines started per UTC
day, so a public deployment cannot spend the subscription's credit. Cached plans are not counted and still attach;
beyond the cap the deterministic plan stays, as with a full queue. Ask PitchIQ questions (``ask``) run on the same
loop and count against the same daily cap, but have their own concurrency slots: a viewer waiting for an answer
should not queue behind background narration. Recaps (``recap``) share the Ask slots and the daily cap.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..analytics.engine import ANALYTICS_VERSION
from ..contracts import AnalystPlan, EvidencePacket, ProducerPlan
from ..narrative.registry import REGISTRY_VERSION
from .ask import AskLimits, AskTrace, fallback_answer, run_ask
from .recap import RecapLimits, RecapProfile, RecapTrace, fallback_recap
from .recap_workflow import run_recap
from .llm_plans import (
    PROMPT_VERSION,
    analyst_prompt,
    analyst_repair_prompt,
    bind_analyst,
    bind_producer,
    producer_prompt,
)
from .validate import PlanError, validate_analyst_plan, validate_producer_plan


@dataclass(frozen=True)
class Limits:
    pipeline_deadline_s: float = 20.0
    stage_timeout_s: float = 8.0
    max_model_calls: int = 3  # two agents plus one repair
    max_repairs: int = 1
    global_concurrency: int = 2
    per_replay_pending: int = 4
    ask_concurrency: int = 2  # questions in flight per process, apart from the narration pipelines
    daily_pipelines: int = 0  # live pipelines started per UTC day; 0 = no cap


@dataclass
class Trace:
    trace_id: str
    episode_id: str
    evidence_hash: str
    model: str
    stages: list[dict] = field(default_factory=list)
    calls: int = 0
    repairs: int = 0
    usage: dict = field(default_factory=dict)
    result: str = "pending"  # foundry_live | foundry_cached | fallback
    reason: str | None = None  # why it fell back
    total_ms: float = 0.0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("trace_id", "episode_id", "evidence_hash", "model", "stages", "calls",
                                              "repairs", "usage", "result", "reason", "total_ms")}


class _StageFailed(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def run_pipeline(packet: EvidencePacket, gateway, limits: Limits = Limits(),
                       clock: Callable[[], float] = time.monotonic) -> tuple[AnalystPlan | None, ProducerPlan | None,
                                                                             Trace]:
    """Run the two agents with code validation between them. Returns plans only if both validated."""
    trace = Trace(f"tr_{uuid.uuid4().hex[:12]}", packet.episode.episode_id, packet.evidence_hash,
                  getattr(gateway, "model", "unknown"))
    t_start = clock()
    deadline = t_start + limits.pipeline_deadline_s

    async def call(stage: str, fn):
        if trace.calls >= limits.max_model_calls:
            raise _StageFailed("call_budget")
        remaining = deadline - clock()
        if remaining <= 0:
            raise _StageFailed("deadline")
        trace.calls += 1
        t0 = clock()
        rec = {"stage": stage, "ok": False}
        try:
            reply = await asyncio.wait_for(fn(), timeout=min(limits.stage_timeout_s, remaining))
        except asyncio.TimeoutError:
            rec["error"] = "timeout"
            raise _StageFailed("timeout") from None
        except (ValidationError, ValueError):
            rec["error"] = "invalid_output"  # the model's JSON did not parse into the schema: repairable
            return None
        except Exception as e:  # service errors (auth, 429, network): not repaired, fall back
            rec["error"] = f"service_error:{type(e).__name__}"
            raise _StageFailed(rec["error"]) from None
        finally:
            rec["ms"] = round((clock() - t0) * 1000.0, 1)
            trace.stages.append(rec)
        rec["ok"] = True
        for k, v in reply.usage.items():
            trace.usage[k] = trace.usage.get(k, 0) + v
        return reply.value

    def note_invalid(stage: str, err: Exception) -> str:
        code = err.code if isinstance(err, PlanError) else "schema"
        trace.stages.append({"stage": f"{stage}_validation", "ok": False, "error": code})
        return str(err)

    analyst = producer = None
    try:
        prompt = analyst_prompt(packet)
        for attempt in range(limits.max_repairs + 1):
            value = await call("analyst" if attempt == 0 else "analyst_repair", lambda p=prompt: gateway.analyst(p))
            try:
                if value is None:
                    raise ValueError("model output did not match the AnalystChoice schema")
                plan = bind_analyst(value, packet)
                validate_analyst_plan(plan, packet)
                analyst = plan
                trace.stages.append({"stage": "analyst_validation", "ok": True})
                break
            except (PlanError, ValidationError, ValueError, TypeError, AttributeError) as e:
                feedback = note_invalid("analyst", e)
                if trace.repairs >= limits.max_repairs:
                    raise _StageFailed("analyst_invalid") from None
                trace.repairs += 1
                prompt = analyst_repair_prompt(packet, feedback)
        feedback = None
        while producer is None:
            stage = "producer" if feedback is None else "producer_repair"
            value = await call(stage, lambda f=feedback: gateway.producer(producer_prompt(packet, analyst, f)))
            try:
                if value is None:
                    raise ValueError("model output did not match the ProducerChoice schema")
                plan = bind_producer(value, packet)
                validate_producer_plan(plan, packet, analyst)
                producer = plan
                trace.stages.append({"stage": "producer_validation", "ok": True})
            except (PlanError, ValidationError, ValueError, TypeError, AttributeError) as e:
                feedback = note_invalid("producer", e)
                if trace.repairs >= limits.max_repairs:
                    raise _StageFailed("producer_invalid") from None
                trace.repairs += 1
        trace.result = "foundry_live"
    except _StageFailed as e:
        analyst = producer = None
        trace.result, trace.reason = "fallback", e.code
    trace.total_ms = round((clock() - t_start) * 1000.0, 1)
    return analyst, producer, trace


def cache_key(packet: EvidencePacket, model: str) -> tuple:
    return (packet.evidence_hash, PROMPT_VERSION, REGISTRY_VERSION, ANALYTICS_VERSION, model)


class CloudInsights:
    """Background loop for cloud plans. One instance per process; sessions submit, results come back by callback."""

    def __init__(self, gateway_factory: Callable[[], object] | None, model: str = "", limits: Limits = Limits(),
                 disabled_reason: str | None = None):
        self.limits = limits
        self.model = model
        self.enabled = gateway_factory is not None
        self.disabled_reason = disabled_reason
        self._factory = gateway_factory
        self._gateway = None
        self._cache: dict[tuple, tuple[AnalystPlan, ProducerPlan, str]] = {}
        self._inflight: dict[tuple, list[Callable]] = {}
        self._pending: dict[str, int] = {}
        self._lock = threading.Lock()
        self.traces: dict[str, dict] = {}
        self.status = {"calls_total": 0, "last_call_succeeded": None, "last_error": None, "last_result_utc": None,
                       "pipelines_today": 0, "daily_limit": limits.daily_pipelines or None}
        self._day = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sem: asyncio.Semaphore | None = None
        self._ask_sem: asyncio.Semaphore | None = None
        if self.enabled:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, name="pitchiq-cloud", daemon=True).start()

    def submit(self, owner: str, packet: EvidencePacket,
               on_done: Callable[[AnalystPlan | None, ProducerPlan | None, dict, str], None]) -> str:
        """Queue a cloud plan for this packet. Returns 'queued', 'cached', 'joined', 'queue_full', 'daily_limit' or
        'disabled'."""
        if not self.enabled:
            return "disabled"
        k = cache_key(packet, self.model)
        with self._lock:
            hit = self._cache.get(k)
            if hit is None:
                if k in self._inflight:
                    self._inflight[k].append(on_done)
                    return "joined"
                if self._pending.get(owner, 0) >= self.limits.per_replay_pending:
                    return "queue_full"
                if not self._take_daily_slot():
                    return "daily_limit"
                self._pending[owner] = self._pending.get(owner, 0) + 1
                self._inflight[k] = [on_done]
            trace = self.traces.get(hit[2], {}) if hit else None
        if hit is not None:
            # delivered from the cloud thread like a live result: the caller may hold its session lock right now
            self._loop.call_soon_threadsafe(on_done, hit[0], hit[1], trace, "foundry_cached")
            return "cached"
        asyncio.run_coroutine_threadsafe(self._run(owner, k, packet), self._loop)
        return "queued"

    def _take_daily_slot(self) -> bool:
        """Count one live run against today's cap (call with the lock held); False when the cap is reached."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if day != self._day:
            self._day, self.status["pipelines_today"] = day, 0
        if self.limits.daily_pipelines and self.status["pipelines_today"] >= self.limits.daily_pipelines:
            return False
        self.status["pipelines_today"] += 1
        return True

    def _note(self, calls: int, succeeded: bool, reason: str | None) -> None:
        with self._lock:
            self.status["calls_total"] += calls
            if calls:
                self.status["last_call_succeeded"] = succeeded
                self.status["last_error"] = reason
                self.status["last_result_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def ask(self, view, question: str, audience: str, language: str, limits: AskLimits = AskLimits()):
        """Answer one Ask PitchIQ question on the cloud loop. Returns a future of (answer, trace dict), or
        'disabled' / 'daily_limit' when nothing was started (the caller then answers with the fallback)."""
        if not self.enabled:
            return "disabled"
        with self._lock:
            if not self._take_daily_slot():
                return "daily_limit"
        return asyncio.run_coroutine_threadsafe(self._ask(view, question, audience, language, limits), self._loop)

    async def _ask(self, view, question: str, audience: str, language: str, limits: AskLimits) -> tuple[dict, dict]:
        if self._ask_sem is None:
            self._ask_sem = asyncio.Semaphore(self.limits.ask_concurrency)
        async with self._ask_sem:
            try:
                if self._gateway is None:
                    self._gateway = self._factory()
                answer, trace = await run_ask(view, question, audience, language, self._gateway, limits)
            except Exception as e:  # gateway construction failed (missing package, bad settings)
                answer = fallback_answer(view, audience)
                trace = AskTrace(f"ask_{uuid.uuid4().hex[:12]}", "unknown", result="fallback",
                                 reason=f"gateway_error:{type(e).__name__}")
        self._note(trace.runs, trace.result == "foundry_live", trace.reason)
        return answer, trace.as_dict()

    def recap(self, view, profile: RecapProfile, limits: RecapLimits = RecapLimits()):
        """Run one recap workflow on the cloud loop. Returns a future of (recap, trace dict), or 'disabled' /
        'daily_limit' when nothing was started (the caller then shows ``fallback_recap``). A view with no moment
        yet starts no agent run, so it takes no daily slot."""
        if not self.enabled:
            return "disabled"
        if view.revisions:
            with self._lock:
                if not self._take_daily_slot():
                    return "daily_limit"
        return asyncio.run_coroutine_threadsafe(self._recap(view, profile, limits), self._loop)

    async def _recap(self, view, profile: RecapProfile, limits: RecapLimits) -> tuple[dict, dict]:
        if self._ask_sem is None:
            self._ask_sem = asyncio.Semaphore(self.limits.ask_concurrency)
        async with self._ask_sem:
            try:
                if view.revisions and self._gateway is None:  # nothing to choose from: no gateway needed
                    self._gateway = self._factory()
            except Exception as e:  # gateway construction failed (missing package, bad settings)
                result = fallback_recap(view, profile)
                trace = RecapTrace(f"recap_{uuid.uuid4().hex[:12]}", "unknown", result="fallback",
                                   reason=f"gateway_error:{type(e).__name__}")
            else:
                try:
                    result, trace = await run_recap(view, profile, self._gateway, limits)
                except Exception as e:  # never a 500 for the viewer: the deterministic recap instead
                    result = fallback_recap(view, profile)
                    trace = RecapTrace(f"recap_{uuid.uuid4().hex[:12]}", getattr(self._gateway, "model", "unknown"),
                                       result="fallback", reason=f"recap_error:{type(e).__name__}")
        self._note(trace.runs, trace.result == "foundry_live", trace.reason)
        return result, trace.as_dict()

    async def _run(self, owner: str, k: tuple, packet: EvidencePacket) -> None:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.limits.global_concurrency)
        try:
            async with self._sem:
                try:
                    if self._gateway is None:
                        self._gateway = self._factory()
                    a, p, trace = await run_pipeline(packet, self._gateway, self.limits)
                except Exception as e:  # gateway construction failed (missing package, bad settings)
                    a = p = None
                    trace = Trace(f"tr_{uuid.uuid4().hex[:12]}", packet.episode.episode_id, packet.evidence_hash,
                                  "unknown", result="fallback", reason=f"gateway_error:{type(e).__name__}")
        finally:
            with self._lock:
                self._pending[owner] = max(0, self._pending.get(owner, 1) - 1)
                waiters = self._inflight.pop(k, [])
        t = trace.as_dict()
        self._note(trace.calls, trace.result == "foundry_live", trace.reason)
        with self._lock:
            self.traces[trace.trace_id] = t
            if a is not None and p is not None:
                self._cache[k] = (a, p, trace.trace_id)
        for cb in waiters:
            cb(a, p, t, "foundry_live")
