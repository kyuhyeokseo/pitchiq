"""Ask PitchIQ: a question-answering agent over the replay so far.

A fan or analyst asks a question while a replay runs. The agent gets the moments found so far and decides what to
look at - a moment's evidence, how its reading changed, the sentences it may answer with - through read-only tools
bound to the analysis as of the question (code fixes the cutoff; no tool takes a time, so later play is out of
reach). It answers by choosing one moment and up to five of that moment's sentence options (prefaced by a fixed
note when the question asked about intent), or by declining with a fixed reason. It never writes a sentence or a
number: the options are registry templates already bound to the packet's own metrics and validated, code checks the
choice (known ids, both sides of a complete trade-off), allows one repair, and otherwise answers with the
deterministic narration of the main moment.

The trace records each tool call (name, arguments, time, a short result summary), each agent run with the token
counts the service reported, and the validation codes; never prompts or credentials.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..contracts import EvidencePacket, MatchMeta, SentencePlan
from ..narrative.registry import ASK_DECLINES
from ..narrative.render import RenderError, render_sentence
from .llm_plans import LANGUAGES, sentence_candidates
from .tools import ToolError, View, _rel
from .validate import PlanError
from .workflow import InsightService, render_variants

MAX_SENTENCES = 5
MAX_QUESTION_CHARS = 280
_INSIGHTS = InsightService()


# ------------------------------------------------------------------ sentence options (code, not the model)
@dataclass(frozen=True)
class Option:
    option_id: str
    kind: str  # restricted | exposed | context | change | outcome | caveat | partial
    sentence: SentencePlan
    text: dict[str, str]  # language -> rendered sentence


def sentence_options(packet: EvidencePacket, meta: MatchMeta, audience: str) -> list[Option]:
    """The Producer's sentence candidates for this audience (same ids), rendered in both languages."""
    out: list[Option] = []
    for c in sentence_candidates(packet, audience):
        try:
            text = {lang: render_sentence(c.sentence, packet, lang, meta) for lang in LANGUAGES}
        except (RenderError, KeyError):
            continue
        out.append(Option(c.option_id, c.kind, c.sentence, text))
    return out


# ------------------------------------------------------------------ what the model chooses
DECLINE_CODES = tuple(ASK_DECLINES)


class AskChoice(BaseModel):
    answer: Literal["moment", "decline"]
    episode_id: str | None = Field(description="answer=moment: the moment the answer is about")
    option_ids: list[str] = Field(description="answer=moment: 1-5 option ids from answer_options for that moment, "
                                              "in the order to tell them")
    note_code: str | None = Field(description="answer=decline: why, one of " + ", ".join(DECLINE_CODES) + ". "
                                                "answer=moment: intent_not_inferred when the question asks about "
                                                "intent (shown before the answer), else null")


ASK_INSTRUCTIONS = """You are Ask PitchIQ, the question-answering agent of a defensive-analysis tool on synthetic
football tracking. A viewer asks about the replay so far; you get the defensive moments found up to now (what each
defence restricted, what it exposed, what changed next, the observed outcome). Decide which moment the question is
about. Use the tools to look before you answer: get_evidence for a moment's claims and measured values,
get_history for how its reading changed, and answer_options for the sentences you may answer with (always call it
for the moment you answer about, and use only the ids it returns). Answer with one moment and 1-5 of its option ids
in the order to tell them. When a complete trade-off answer uses a restricted or exposed sentence, keep one of each
side. A question about why a coach, team or player chose, wanted, planned or meant something (in any language, e.g.
"why did the coach...", "what were they trying to do", "왜 그렇게 했어") asks about intent: always set note_code to
intent_not_inferred for it, and still answer with the moment it is about. The note says PitchIQ does not read minds;
the options say what the shape did. Otherwise note_code is null for an answer.
Decline (answer=decline with that note_code) when the question asks about play that has not happened yet
(not_yet_known), is not about this clip's defending (outside_scope), or when no moment exists yet (no_moment_yet).
You never write sentences or numbers: the note and the chosen options are the only words shown. Answer with the JSON
schema only."""


def ask_prompt(view: View, question: str, audience: str, language: str, feedback: str | None = None) -> str:
    body = {"question": question[:MAX_QUESTION_CHARS], "audience": audience, "language": language,
            "replay_time_s": _rel(view.clip, view.t), "moments": view.moments()["moments"],
            "note_codes": list(DECLINE_CODES)}
    if feedback:
        body["previous_answer_rejected_because"] = feedback
    return json.dumps(body, ensure_ascii=False)


# ------------------------------------------------------------------ validation and answers
def validate_ask(choice: AskChoice, view: View, audience: str) -> tuple[EvidencePacket | None, list[Option]]:
    """The chosen moment and options, or a PlanError saying what to fix."""
    if choice.answer == "decline":
        if choice.note_code not in ASK_DECLINES:
            raise PlanError("unknown_decline", str(choice.note_code))
        if choice.note_code == "no_moment_yet" and view.revisions:
            raise PlanError("moments_exist", "moments exist: answer about one of them or use another code")
        return None, []
    if choice.note_code not in (None, "intent_not_inferred"):
        raise PlanError("note_not_allowed", f"{choice.note_code}: an answer about a moment takes only "
                                            "intent_not_inferred as its note; otherwise decline")
    if choice.episode_id not in view.revisions:
        raise PlanError("unknown_moment", f"{choice.episode_id}: use an episode_id from the moments list")
    packet = view.packet(choice.episode_id)
    ids = choice.option_ids
    if not 1 <= len(ids) <= MAX_SENTENCES:
        raise PlanError("sentence_count", f"choose 1-{MAX_SENTENCES} options, not {len(ids)}")
    if len(set(ids)) != len(ids):
        raise PlanError("duplicate_option", str(ids))
    options = {o.option_id: o for o in sentence_options(packet, view.clip.meta, audience)}
    unknown = [i for i in ids if i not in options]
    if unknown:
        raise PlanError("unknown_option", f"{unknown}: call answer_options for {choice.episode_id}")
    chosen = [options[i] for i in ids]
    sides = {o.kind for o in chosen} & {"restricted", "exposed"}
    if packet.episode.analysis_status == "complete_tradeoff" and sides and sides != {"restricted", "exposed"}:
        raise PlanError("one_sided", "a complete trade-off keeps a restricted and an exposed sentence")
    return packet, chosen


def _answer(view: View, packet: EvidencePacket | None, sentences: dict[str, list[str]],
            note_code: str | None = None) -> dict:
    if note_code is not None:
        sentences = {lang: [ASK_DECLINES[note_code][lang]] + sentences.get(lang, []) for lang in LANGUAGES}
    out = {"kind": "decline" if packet is None else "moment", "note_code": note_code,
           "analyzed_through_s": _rel(view.clip, view.t), "episode_id": None, "episode_revision": None,
           "evidence_hash": None, "detected_at_s": None, "sentences": sentences}
    if packet is not None:
        ep = packet.episode
        out.update(episode_id=ep.episode_id, episode_revision=ep.revision, evidence_hash=packet.evidence_hash,
                   detected_at_s=_rel(view.clip, ep.detected_at_s))
    return out


def decline_answer(view: View, code: str) -> dict:
    return _answer(view, None, {}, code)


def main_moment(view: View) -> EvidencePacket | None:
    """The moment the replay leads with: the strongest complete trade-off, else the one with the most evidence."""
    latest = [r[-1] for r in view.revisions.values()]
    if not latest:
        return None
    return max(latest, key=lambda p: (p.episode.analysis_status == "complete_tradeoff", p.episode.story_priority,
                                      p.episode.detected_at_s))


def fallback_answer(view: View, audience: str) -> dict:
    """The deterministic narration of the main moment (or 'nothing yet'): what Ask shows when the agent cannot."""
    p = main_moment(view)
    if p is None:
        return decline_answer(view, "no_moment_yet")
    texts = render_variants(_INSIGHTS.deterministic_revision(p, 1), p, view.clip.meta)
    return _answer(view, p, {lang: texts[f"{audience}_{lang}"] for lang in LANGUAGES})


# ------------------------------------------------------------------ the agent run
@dataclass(frozen=True)
class AskLimits:
    deadline_s: float = 45.0  # the whole answer, tools and one repair included
    max_runs: int = 2  # the agent run plus one repair
    max_tool_calls: int = 8  # per question, across runs


@dataclass
class AskTrace:
    trace_id: str
    model: str
    steps: list[dict] = field(default_factory=list)
    runs: int = 0
    repairs: int = 0
    tool_calls: int = 0
    usage: dict = field(default_factory=dict)
    result: str = "pending"  # foundry_live | fallback
    reason: str | None = None
    total_ms: float = 0.0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("trace_id", "model", "steps", "runs", "repairs", "tool_calls", "usage",
                                              "result", "reason", "total_ms")}


def _count(n: int, what: str) -> str:
    return f"{n} {what}{'' if n == 1 else 's'}"


def _summarise(name: str, result: dict) -> str:
    if "error" in result:
        return result["error"]
    if name == "get_evidence":
        return (f"{_count(len(result['claims']), 'claim')}, {_count(len(result['changes']), 'change')}, "
                f"outcome {result['outcome'].replace('_', ' ')}")
    if name == "get_history":
        return _count(len(result["revisions"]), "revision")
    return _count(len(result["options"]), "option")


def make_tools(view: View, audience: str, language: str, trace: AskTrace, limits: AskLimits,
               clock: Callable[[], float], t_start: float) -> list[Callable]:
    """The agent's tools, bound to this view: read-only, no time argument, every call traced and budgeted."""
    def call(name: str, args: dict, fn: Callable[[], dict]) -> dict:
        t0 = clock()
        # ids come from the model: capped, since traces are returned to the client
        step = {"step": "tool", "name": name, "args": {k: str(v)[:64] for k, v in args.items()},
                "at_ms": round((t0 - t_start) * 1000.0, 1)}
        if trace.tool_calls >= limits.max_tool_calls:
            result = {"error": "tool budget used up: answer now with what you have"}
        else:
            trace.tool_calls += 1
            try:
                result = fn()
            except ToolError as e:
                result = {"error": str(e)}
        step.update(ok="error" not in result, ms=round((clock() - t0) * 1000.0, 1),
                    summary=_summarise(name, result)[:160])
        trace.steps.append(step)
        return result

    def get_evidence(episode_id: str) -> dict:
        """One moment's evidence as known now: each claim (restricted, exposed or context) with its measured values
        and players, the observed changes, the outcome, and what the analysis does not claim."""
        return call("get_evidence", {"episode_id": episode_id}, lambda: view.evidence(episode_id))

    def get_history(episode_id: str) -> dict:
        """How one moment's reading changed so far: each revision's status, claims, changes and outcome."""
        return call("get_history", {"episode_id": episode_id}, lambda: view.history(episode_id))

    def answer_options(episode_id: str) -> dict:
        """The sentences you may answer with about one moment, with their ids: validated templates already filled
        with the moment's measured values, in the viewer's language."""
        def options() -> dict:
            p = view.packet(episode_id)
            return {"episode_id": episode_id, "analysis_status": p.episode.analysis_status,
                    "options": [{"option_id": o.option_id, "kind": o.kind, "text": o.text[language]}
                                for o in sentence_options(p, view.clip.meta, audience)]}
        return call("answer_options", {"episode_id": episode_id}, options)

    return [get_evidence, get_history, answer_options]


async def run_ask(view: View, question: str, audience: str, language: str, gateway, limits: AskLimits = AskLimits(),
                  clock: Callable[[], float] = time.monotonic) -> tuple[dict, AskTrace]:
    """Run the Ask agent on one question. Returns the answer (validated, or the fallback) and its trace."""
    trace = AskTrace(f"ask_{uuid.uuid4().hex[:12]}", getattr(gateway, "model", "unknown"))
    t_start = clock()
    deadline = t_start + limits.deadline_s
    tools = make_tools(view, audience, language, trace, limits, clock, t_start)
    feedback = None
    answer = None
    for attempt in range(limits.max_runs):
        remaining = deadline - clock()
        if remaining <= 0:
            trace.reason = "deadline"
            break
        if attempt:
            trace.repairs += 1
        trace.runs += 1
        t0 = clock()
        step = {"step": "agent" if attempt == 0 else "agent_repair", "at_ms": round((t0 - t_start) * 1000.0, 1)}
        trace.steps.append(step)  # first, so the tool calls of this run follow it
        try:
            reply = await asyncio.wait_for(gateway.ask(ask_prompt(view, question, audience, language, feedback),
                                                       tools), timeout=remaining)
        except asyncio.TimeoutError:
            step.update(ok=False, error="timeout")
            trace.reason = "timeout"
            break
        except (ValidationError, ValueError):
            step.update(ok=False, error="invalid_output")
            feedback = "the output did not match the AskChoice schema"
            trace.reason = "invalid_output"
            continue
        except Exception as e:  # service errors (auth, 429, network): not repaired
            step.update(ok=False, error=f"service_error:{type(e).__name__}")
            trace.reason = step["error"]
            break
        finally:
            step["ms"] = round((clock() - t0) * 1000.0, 1)
        step["ok"] = True
        for k, v in reply.usage.items():
            trace.usage[k] = trace.usage.get(k, 0) + v
        try:
            if not isinstance(reply.value, AskChoice):
                raise PlanError("schema", "the output did not match the AskChoice schema")
            packet, chosen = validate_ask(reply.value, view, audience)
        except PlanError as e:
            trace.steps.append({"step": "validation", "ok": False, "error": e.code})
            feedback = str(e)
            trace.reason = f"invalid:{e.code}"
            continue
        trace.steps.append({"step": "validation", "ok": True})
        answer = (decline_answer(view, reply.value.note_code) if packet is None
                  else _answer(view, packet, {lang: [o.text[lang] for o in chosen] for lang in LANGUAGES},
                               reply.value.note_code))
        trace.result, trace.reason = "foundry_live", None
        break
    if answer is None:
        trace.result = "fallback"
        answer = fallback_answer(view, audience)
    trace.total_ms = round((clock() - t_start) * 1000.0, 1)
    return answer, trace
