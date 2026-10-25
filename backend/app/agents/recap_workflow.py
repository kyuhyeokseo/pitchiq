"""The Recap agent run as a Microsoft Agent Framework workflow.

    gather (code) -> recap_agent (Foundry agent with read-only tools) -> check (code) -> render (code)
                                                                                  \\-> fallback (code)

``gather`` turns the frozen view into the agent's context; ``recap_agent`` asks the app-owned ``pitchiq-recap``
agent (through the gateway) to choose moments and option ids, with two tools bound to the view: ``answer_options``
and ``get_evidence`` (no tool takes a time, so later play is out of reach); ``check`` runs ``validate_recap``; a
switch sends a valid choice to ``render`` and anything else - invalid choice, timeout, service error - to
``fallback`` (the deterministic recap). There is no repair loop. Every step, every tool call and the token counts
the service reported go into a ``RecapTrace``; never prompts or credentials.

The Agent Framework is the optional ``foundry`` extra, so it is imported only when a workflow is built. A new
workflow is built per recap: workflow instances keep state between runs and do not allow concurrent runs.
``export_mermaid`` writes the graph to docs/agents/recap_workflow.mmd:

    PYTHONPATH=backend python -m app.agents.recap_workflow
"""

# No ``from __future__ import annotations``: the workflow runtime reads the executors' type annotations.
import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .ask import make_tools
from .recap import (
    Chosen,
    RecapChoice,
    RecapLimits,
    RecapProfile,
    RecapTrace,
    fallback_recap,
    recap_prompt,
    render_recap,
    validate_recap,
)
from .tools import View
from .validate import PlanError

WORKFLOW_NAME = "pitchiq-recap"
EXECUTOR_IDS = ("gather", "recap_agent", "check", "render", "fallback")
MERMAID_PATH = Path(__file__).resolve().parents[3] / "docs" / "agents" / "recap_workflow.mmd"
RECAP_TOOLS = ("answer_options", "get_evidence")
_GRACE_S = 2.0  # the code steps after the agent's own deadline


# ------------------------------------------------------------------ messages between executors
# Small on purpose: the runtime deep-copies every message it delivers, and a view holds the whole clip. The view and
# the profile are bound to the executors instead (``_Run``), like the tools.
@dataclass(frozen=True)
class RecapRequest:
    trace_id: str


@dataclass(frozen=True)
class RecapContext:
    prompt: str


@dataclass(frozen=True)
class AgentOutcome:
    choice: Any  # the parsed RecapChoice, or None when the run failed
    error: str | None


@dataclass(frozen=True)
class CheckResult:
    chosen: Chosen | None  # the validated moments and options in replay order; None: show the fallback


@dataclass
class _Run:
    """What the executors of one recap share: the frozen view, the viewer, the gateway, the limits, the clock and
    the trace."""
    view: View
    profile: RecapProfile
    gateway: Any
    limits: RecapLimits
    clock: Callable[[], float]
    trace: RecapTrace
    t_start: float

    def at_ms(self, t: float) -> float:
        return round((t - self.t_start) * 1000.0, 1)


def recap_tools(run: _Run) -> list[Callable]:
    """The agent's tools for this view: read-only, no time argument, every call traced and budgeted (Ask's tools,
    without the revision history)."""
    tools = make_tools(run.view, run.profile.audience, run.profile.language, run.trace, run.limits, run.clock,
                       run.t_start)
    return [f for f in tools if f.__name__ in RECAP_TOOLS]


def build_recap_workflow(run: _Run | None = None):
    """The recap graph. ``run`` binds the executors to one recap; without it the graph is only for inspection."""
    from typing import Never

    from agent_framework import Case, Default, WorkflowBuilder, WorkflowContext, executor

    def step(name: str, t0: float, **fields) -> dict:
        rec = {"step": name, "at_ms": run.at_ms(t0), **fields}
        run.trace.steps.append(rec)
        return rec

    @executor(id="gather")
    async def gather(req: RecapRequest, ctx: WorkflowContext[RecapContext]) -> None:
        t0 = run.clock()
        prompt = recap_prompt(run.view, run.profile)
        step("gather", t0, ok=True, moments=len(run.view.revisions), ms=round((run.clock() - t0) * 1000.0, 1))
        await ctx.send_message(RecapContext(prompt))

    @executor(id="recap_agent")
    async def recap_agent(c: RecapContext, ctx: WorkflowContext[AgentOutcome]) -> None:
        trace, t0 = run.trace, run.clock()
        rec = step("recap_agent", t0)  # first, so the tool calls of this run follow it
        remaining = run.t_start + run.limits.deadline_s - t0
        choice, error = None, None
        if remaining <= 0:
            error = "deadline"
        else:
            trace.runs += 1
            try:
                reply = await asyncio.wait_for(run.gateway.recap(c.prompt, recap_tools(run)), timeout=remaining)
            except asyncio.TimeoutError:
                error = "timeout"
            except (ValidationError, ValueError):
                error = "invalid_output"
            except Exception as e:  # service errors (auth, 429, network)
                error = f"service_error:{type(e).__name__}"
            else:
                choice = reply.value
                for k, v in reply.usage.items():
                    trace.usage[k] = trace.usage.get(k, 0) + v
        rec.update(ok=error is None, ms=round((run.clock() - t0) * 1000.0, 1))
        if error:
            rec["error"] = error
            trace.reason = error
        await ctx.send_message(AgentOutcome(choice, error))

    @executor(id="check")
    async def check(o: AgentOutcome, ctx: WorkflowContext[CheckResult]) -> None:
        t0 = run.clock()
        chosen = None
        if o.error is not None:
            rec = step("check", t0, ok=False, error="no_choice")
        else:
            try:
                if not isinstance(o.choice, RecapChoice):
                    raise PlanError("schema", "the output did not match the RecapChoice schema")
                chosen = validate_recap(o.choice, run.view, run.profile)
                rec = step("check", t0, ok=True)
            except PlanError as e:
                rec = step("check", t0, ok=False, error=e.code)
                run.trace.reason = f"invalid:{e.code}"
        rec["ms"] = round((run.clock() - t0) * 1000.0, 1)
        await ctx.send_message(CheckResult(chosen))

    @executor(id="render")
    async def render(r: CheckResult, ctx: WorkflowContext[Never, dict]) -> None:
        t0 = run.clock()
        out = render_recap(run.view, run.profile, r.chosen)
        step("render", t0, ok=True, items=len(out["items"]), ms=round((run.clock() - t0) * 1000.0, 1))
        run.trace.result, run.trace.reason = "foundry_live", None
        await ctx.yield_output(out)

    @executor(id="fallback")
    async def fallback(r: CheckResult, ctx: WorkflowContext[Never, dict]) -> None:
        t0 = run.clock()
        out = fallback_recap(run.view, run.profile)
        step("fallback", t0, ok=True, items=len(out["items"]), ms=round((run.clock() - t0) * 1000.0, 1))
        run.trace.result = "fallback"
        await ctx.yield_output(out)

    return (WorkflowBuilder(start_executor=gather, name=WORKFLOW_NAME,
                            description="Recap: choose moments and sentence options, checked by code")
            .add_edge(gather, recap_agent)
            .add_edge(recap_agent, check)
            .add_switch_case_edge_group(check, [Case(condition=lambda r: r.chosen is not None, target=render),
                                                Default(target=fallback)])
            .build())


async def run_recap(view: View, profile: RecapProfile, gateway, limits: RecapLimits = RecapLimits(),
                    clock: Callable[[], float] = time.monotonic) -> tuple[dict, RecapTrace]:
    """Run the recap workflow for one viewer on one view. Returns the recap (validated, or the fallback) and its
    trace. With no moment yet there is nothing to choose from: no agent run, the fixed 'no moments yet' recap."""
    trace = RecapTrace(f"recap_{uuid.uuid4().hex[:12]}", getattr(gateway, "model", "unknown"))
    t_start = clock()
    result = None
    if not view.revisions:
        trace.result, trace.reason = "fallback", "no_moments"
    else:
        run = _Run(view, profile, gateway, limits, clock, trace, t_start)
        try:
            workflow = build_recap_workflow(run)
            done = await asyncio.wait_for(workflow.run(RecapRequest(trace.trace_id)),
                                          timeout=limits.deadline_s + _GRACE_S)
            outputs = done.get_outputs()
            result = outputs[0] if outputs else None
            if result is None:
                trace.reason = trace.reason or "no_output"
        except asyncio.TimeoutError:
            trace.reason = "deadline"
        except Exception as e:  # the workflow runtime itself failed (missing package, a code step raised)
            trace.reason = f"workflow_error:{type(e).__name__}"
    if result is None:
        ended = any(st.get("step") in ("render", "fallback") for st in trace.steps)
        if view.revisions and not ended:  # failed outside the graph: record the fallback step it never reached
            t0 = clock()
            result = fallback_recap(view, profile)
            trace.steps.append({"step": "fallback", "at_ms": round((t0 - t_start) * 1000.0, 1), "ok": True,
                                "items": len(result.get("items", [])), "ms": round((clock() - t0) * 1000.0, 1)})
        else:
            result = fallback_recap(view, profile)
        trace.result = "fallback"
    trace.total_ms = round((clock() - t_start) * 1000.0, 1)
    return result, trace


def export_mermaid(path: Path = MERMAID_PATH) -> str:
    """Write the recap graph as Mermaid (Agent Framework's WorkflowViz) and return the text."""
    from agent_framework import WorkflowViz

    text = WorkflowViz(build_recap_workflow()).to_mermaid() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


if __name__ == "__main__":
    print(export_mermaid(), end="")
