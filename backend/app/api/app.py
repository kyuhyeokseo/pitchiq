"""FastAPI app: REST + WebSocket replay API (spec 14) and the built frontend.

Run:  uvicorn app.api.app:app --app-dir backend --port 8000
Missing Foundry settings never block anything here: insights fall back to the
deterministic planner and say so in ``source_mode``.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..agents import mcp_server
from ..agents.ask import MAX_QUESTION_CHARS, AskTrace, fallback_answer
from ..agents.cloud import CloudInsights, Limits
from ..agents.fallback import changes_from_rules
from ..agents.recap import RecapProfile, RecapTrace, check_profile, fallback_recap
from ..agents.gateway import FoundryGateway, foundry_settings, load_env_file, sdk_available
from ..agents.tools import View
from ..agents.workflow import InsightService, render_variants
from ..analytics.engine import ANALYTICS_VERSION
from ..contracts import InsightRevision
from ..contracts.registry import METRIC_REGISTRY_VERSION
from ..narrative.registry import REGISTRY_VERSION
from ..overlay.builder import build_overlay, key_state, validate_overlay
from ..replay.catalog import list_clips, load_clip
from ..replay.breakdown import breakdown as breakdown_of
from ..replay.session import ReplaySession, SessionStore, StaleEpoch, StaleRevision
from .. import telemetry

APP_VERSION = "0.2.0"
ROOT = Path(__file__).resolve().parents[3]


def _cloud_from_env() -> CloudInsights:
    s = foundry_settings()
    if s["llm_mode"] != "foundry":
        return CloudInsights(None, disabled_reason="LLM_MODE is not foundry")
    if not (s["endpoint"] and s["model"]):
        return CloudInsights(None, disabled_reason="FOUNDRY_PROJECT_ENDPOINT or FOUNDRY_MODEL is not set")
    if not sdk_available():
        return CloudInsights(None, disabled_reason="agent-framework-foundry is not installed (pip install .[foundry])")
    return CloudInsights(FoundryGateway.from_env, model=s["model"],
                         limits=Limits(daily_pipelines=max(0, int(os.environ.get("FOUNDRY_DAILY_LIMIT", "0") or 0))))


load_env_file(os.environ.get("PITCHIQ_ENV_FILE", ROOT / ".env"))
telemetry.configure()  # Application Insights, only with APPLICATIONINSIGHTS_CONNECTION_STRING
CLOUD = _cloud_from_env()
STORE = SessionStore(InsightService(), CLOUD)
# the read-only match tools for MCP clients at /mcp, when the optional mcp extra is installed
MCP = mcp_server.HttpEndpoint() if mcp_server.available() else None


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with MCP.run() if MCP is not None else nullcontext():
        yield


app = FastAPI(title="PitchIQ Defensive Intelligence", version=APP_VERSION, lifespan=_lifespan)
telemetry.instrument_app(app)


# ------------------------------------------------------------------ helpers
def _session(rid: str, token: str | None) -> ReplaySession:
    try:
        s = STORE.get(rid)
    except KeyError:
        raise HTTPException(404, "unknown replay") from None
    if token != s.token:
        raise HTTPException(403, "missing or wrong session token")
    s.touched = time.monotonic()  # a client action keeps the session; socket ticks do not
    return s


def _episodes(s: ReplaySession) -> list[dict]:
    out = []
    for eid, revs in s.analyzer.packets.items():
        ep = revs[-1].episode
        codes = {c.claim_id: c.code for c in revs[-1].claims}
        out.append({"episode_id": eid, "revision": ep.revision, "lifecycle": ep.lifecycle,
                    "analysis_status": ep.analysis_status, "detected_at_s": ep.detected_at_s,
                    "analyzed_until_s": ep.analyzed_until_s, "story_priority": ep.story_priority,
                    "restricted": [codes[c] for c in ep.restricted_claim_ids],
                    "exposed": [codes[c] for c in ep.exposed_claim_ids],
                    # which of these claims are heuristic, from their own epistemic tags (marked in the UI)
                    "heuristic": sorted({c.code for c in revs[-1].claims if c.epistemic == "heuristic_inference"}),
                    "changes": [c.code for c in revs[-1].changes], "outcome": ep.outcome.status,
                    "not_claimed": sorted({f.split(":")[0] for f in revs[-1].forbidden_inferences if ":" in f}),
                    "evidence_hash": revs[-1].evidence_hash, "insight_id": f"ins_{eid}",
                    # for personalisation: who defends, and every player the evidence names (claims and changes)
                    "defending_team_id": ep.defending_team_id,
                    "player_ids": sorted({pid for c in revs[-1].claims for pid in c.player_ids}
                                         | {pid for ch in revs[-1].changes for pid in ch.actor_player_ids})})
    return sorted(out, key=lambda e: e["detected_at_s"])


def _snapshot(s: ReplaySession) -> dict:
    r = s.results[-1]
    return {"schema_version": "2.0", "replay_id": s.replay_id, "clip_id": s.clip.clip_id,
            "analysis_epoch": s.epoch, "seq": s.next_seq(), "clock_s": s.clock_s, "playing": s.playing,
            "speed": s.speed, "clip_start_s": s.clip.start_s, "clip_end_s": s.clip.end_s,
            "frame": s.clip.frames[r.k].model_dump(mode="json"), "state": r.state.model_dump(mode="json"),
            "live_claims": sorted(r.supported), "episodes": _episodes(s),
            "insights": [{"insight_id": iid, "revision": revs[-1].revision,
                          "review_status": s.review_status(revs[-1])} for iid, revs in s.revisions.items()]}


def _packet(s: ReplaySession, episode_id: str, revision: int | None = None):
    revs = s.analyzer.packets.get(episode_id)
    if not revs:
        raise HTTPException(404, "unknown episode at this cutoff")
    if revision is None:
        return revs[-1]
    for p in revs:
        if p.episode.revision == revision:
            return p
    raise HTTPException(404, "unknown episode revision")


def _players(frame) -> dict[str, tuple[float, float]]:
    return {p.player_id: (p.position.x_m, p.position.y_m) for p in frame.players}


def _positions_at(s: ReplaySession):
    """Past positions for change paths; never beyond the analysed cursor."""
    def at(frame_index: int) -> dict[str, tuple[float, float]]:
        return _players(s.clip.frames[min(frame_index, s.cursor_k)])
    return at


def _insight_view(s: ReplaySession, insight_id: str, revision=None) -> dict:
    revs = s.revisions.get(insight_id)
    if not revs:
        raise HTTPException(404, "unknown insight")
    ins = revs[-1] if revision is None else next((r for r in revs if r.revision == revision), None)
    if ins is None:
        raise HTTPException(404, "unknown insight revision")
    pk = next(p for p in s.analyzer.packets[ins.episode_id] if p.evidence_hash == ins.evidence_hash)
    status = s.review_status(ins)
    body = ins.model_copy(update={"review_status": status}).model_dump(mode="json")
    approval, approved = s.approvals.get(insight_id), s.approved_revision(insight_id)
    trace = s.traces.get(ins.generation_trace_id) if ins.generation_trace_id else None
    # for a cloud revision: what the agents changed against the rule-based plan for the same evidence, computed in code
    changes = None if ins.source_mode == "deterministic_fallback" else changes_from_rules(pk, ins.analyst_plan,
                                                                                            ins.producer_plan)
    return {"insight": body, "texts": render_variants(ins, pk, s.clip.meta), "provenance": trace,
            "agent_changes": changes,
            "analysis_status": pk.episode.analysis_status, "outcome": pk.episode.outcome.status,
            "broadcast_visibility": approval.visibility if approval and status == "approved" else "hidden",
            "is_latest": ins is revs[-1],
            # the revision a producer approved, which may be older than this one: broadcast keeps airing it until the
            # newer revision is approved in its own right
            "approved_revision": approved.revision if approved else None}


# ------------------------------------------------------------------ REST
@app.get("/api/health")
def health() -> dict:
    s = foundry_settings()
    configured = bool(s["endpoint"] and s["model"])  # settings present; says nothing about a working call
    return {"status": "ok", "version": APP_VERSION, "build": {"sha": os.environ.get("PITCHIQ_GIT_SHA", "dev")[:7]},
            "analytics_version": ANALYTICS_VERSION,
            "template_registry_version": REGISTRY_VERSION, "metric_registry_version": METRIC_REGISTRY_VERSION,
            "llm_mode": s["llm_mode"], "foundry_configured": configured, "cloud_enabled": CLOUD.enabled,
            "telemetry": {"enabled": telemetry.STATE["enabled"], "error": telemetry.STATE["error"]},
            "cloud_disabled_reason": CLOUD.disabled_reason, "last_cloud_result": dict(CLOUD.status),
            "sessions": len(STORE.sessions), "sockets": sum(x.sockets for x in list(STORE.sessions.values()))}


@app.get("/api/clips")
def clips() -> list[dict]:
    out = []
    for cid in list_clips():
        c = load_clip(cid)
        out.append({"clip_id": cid, "match_id": c.meta.match_id, "start_s": c.start_s, "end_s": c.end_s,
                    "duration_s": round(c.end_s - c.start_s, 1), "tracking_hz": c.meta.tracking_hz,
                    "frames": len(c.frames), "synthetic": True,
                    "teams": [{"team_id": t.team_id, "display_name": t.display_name, "attack_direction": t.attack_direction}
                              for t in c.meta.teams]})
    return out


class CreateReplay(BaseModel):
    clip_id: str
    start_s: float | None = None
    speed: float | None = Field(default=None, ge=0.25, le=4.0)


@app.post("/api/replays")
def create_replay(req: CreateReplay) -> dict:
    try:
        clip = load_clip(req.clip_id)
    except KeyError:
        raise HTTPException(404, "unknown clip") from None
    try:
        s = STORE.create(clip, req.start_s, req.speed)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except OverflowError as e:
        raise HTTPException(429, str(e)) from None
    return {"replay_id": s.replay_id, "session_token": s.token, "analysis_epoch": s.epoch,
            "snapshot": _snapshot(s)}


@app.delete("/api/replays/{rid}", status_code=204)
def delete_replay(rid: str, x_session_token: str | None = Header(default=None)) -> Response:
    """Free a replay the client is leaving (clip switch, closed tab). Idempotent: an unknown replay is already gone."""
    if rid in STORE.sessions:
        _session(rid, x_session_token)
        STORE.delete(rid)
    return Response(status_code=204)


class Control(BaseModel):
    action: Literal["play", "pause", "seek", "set_speed"]
    value: float | None = None
    expected_epoch: int


@app.post("/api/replays/{rid}/control")
def control(rid: str, req: Control, x_session_token: str | None = Header(default=None)) -> dict:
    s = _session(rid, x_session_token)
    try:
        s.control(req.action, req.value, req.expected_epoch)
    except StaleEpoch as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return _snapshot(s)


@app.get("/api/replays/{rid}/snapshot")
def snapshot(rid: str, x_session_token: str | None = Header(default=None)) -> dict:
    s = _session(rid, x_session_token)
    s.sync()
    return _snapshot(s)


@app.get("/api/replays/{rid}/frames/{frame_index}")
def frame(rid: str, frame_index: int, x_session_token: str | None = Header(default=None)) -> dict:
    s = _session(rid, x_session_token)
    if frame_index < 0 or frame_index > s.cursor_k:
        raise HTTPException(404, "frame not observed yet at this cutoff")
    return s.clip.frames[frame_index].model_dump(mode="json")


@app.get("/api/replays/{rid}/episodes")
def episodes(rid: str, x_session_token: str | None = Header(default=None)) -> list[dict]:
    s = _session(rid, x_session_token)
    return [p.episode.model_dump(mode="json") for revs in s.analyzer.packets.values() for p in revs[-1:]]


@app.get("/api/replays/{rid}/evidence/{episode_id}")
def evidence(rid: str, episode_id: str, revision: int | None = None,
             x_session_token: str | None = Header(default=None)) -> dict:
    s = _session(rid, x_session_token)
    return _packet(s, episode_id, revision).model_dump(mode="json")


@app.get("/api/replays/{rid}/overlays")
def overlays(rid: str, episode_id: str, audience: Literal["fan", "analyst"] = "analyst",
             language: Literal["en", "ko"] = "en",
             surface: Literal["inspector_preview", "broadcast"] = "inspector_preview",
             focus: Literal["both", "restricted", "exposed"] = "both",
             display_mode: Literal["live", "retrospective_replay"] = "retrospective_replay",
             revision: int | None = None,
             x_session_token: str | None = Header(default=None)) -> dict:
    s = _session(rid, x_session_token)
    pk = _packet(s, episode_id, revision)
    approved = None
    if surface == "broadcast":
        approved = s.approved_revision(f"ins_{episode_id}")
        if approved is None:
            raise HTTPException(409, "broadcast shows approved revisions only")
        pk = next(p for p in s.analyzer.packets[episode_id] if p.evidence_hash == approved.evidence_hash)
        display_mode = "retrospective_replay"
    now = s.results[-1] if s.results else None
    current = (now is not None and pk.episode.lifecycle != "closed"
               and now.state.defending_team_id == pk.episode.defending_team_id)
    if display_mode == "live" and current:
        state, frame_ = now.state, s.clip.frames[now.k]
        metrics_now = [m for fc in now.supported.values() for m in fc.metrics]
        live_codes = set(now.supported)
    else:
        # replay, or a live view of an episode that is over (or the ball changed hands): its own moment, which a live
        # client sees as expired instead of drawing the old claims over the new frame
        state = key_state(pk)
        frame_ = s.clip.frames[state.frame_ref.frame_index]
        metrics_now = live_codes = None
    players = _players(frame_)
    res = build_overlay(state=state, packet=pk, frame_players=players, meta=s.clip.meta, replay_id=s.replay_id,
                        epoch=s.epoch, audience=audience, language=language, surface=surface,
                        display_mode=display_mode, focus=focus, metrics_now=metrics_now, live_codes=live_codes,
                        positions_at=_positions_at(s))
    if display_mode == "retrospective_replay":
        validate_overlay(res.snapshot, players, pk)
    # broadcast airs the approved revision's own narration: a newer revision with the same evidence (a cloud plan that
    # arrived after the approval) waits for its own approval. Elsewhere, the narration of this exact evidence, so an
    # earlier moment is never told with what happened later.
    ins = approved or next((r for r in reversed(s.revisions.get(f"ins_{episode_id}", []))
                            if r.evidence_hash == pk.evidence_hash), None)
    texts = render_variants(ins, pk, s.clip.meta) if ins else None
    body = {"overlay": res.snapshot.model_dump(mode="json", by_alias=True), "label_text": res.label_text,
            "hidden_labels": res.hidden_labels, "frame_index": state.frame_ref.frame_index,
            "analyzed_through_s": pk.episode.analyzed_until_s, "episode_revision": pk.episode.revision,
            "insight_revision": ins.revision if ins else None, "texts": texts}
    if approved is not None:
        body["on_air"] = _on_air(approved, pk, texts, audience, language)
    return body


def _on_air(ins: InsightRevision, pk, texts: dict[str, list[str]], audience: str, language: str) -> list[str]:
    """The broadcast caption. Fans get what was taken away, what it cost and what happened: the first restricted
    sentence, the first exposed one and the observed outcome, chosen by what they say (an agent may reorder the plan)
    and told in the plan's order; the next sentences fill in when one is missing. Analysts get up to six sentences."""
    lines = texts.get(f"{audience}_{language}", [])
    if audience != "fan":
        return lines[:6]
    plan = next((v for v in ins.producer_plan.variants if v.audience == audience and v.language == language), None)
    dim = {c.claim_id: c.dimension for c in pk.claims}

    def kind(x) -> str:
        if x.template_key.startswith("outcome."):
            return "outcome" if x.template_key != "outcome.pending" else "pending"
        return dim.get(x.claim_id, "other") if x.claim_id else "other"
    kinds = [kind(x) for x in (plan.sentences if plan else [])][:len(lines)]
    picked = [next((i for i, k in enumerate(kinds) if k == want), None) for want in ("restricted", "exposed", "outcome")]
    chosen = {i for i in picked if i is not None}
    for i in range(len(lines)):  # fill up to three in plan order
        if len(chosen) >= 3:
            break
        chosen.add(i)
    return [lines[i] for i in sorted(chosen)]


@app.get("/api/replays/{rid}/insights")
def insights(rid: str, x_session_token: str | None = Header(default=None)) -> list[dict]:
    s = _session(rid, x_session_token)
    return [_insight_view(s, iid) for iid in s.revisions]


class Decision(BaseModel):
    revision: int
    evidence_hash: str
    decision: Literal["approve", "reject"]


@app.post("/api/replays/{rid}/insights/{insight_id}/decision")
def decide(rid: str, insight_id: str, req: Decision, x_session_token: str | None = Header(default=None)) -> dict:
    s = _session(rid, x_session_token)
    try:
        rv = s.decide(insight_id, req.revision, req.evidence_hash, req.decision)
    except KeyError:
        raise HTTPException(404, "unknown insight") from None
    except StaleRevision as e:
        latest = _insight_view(s, insight_id)
        return JSONResponse(status_code=409, content={"detail": str(e), "latest": latest})
    view = _insight_view(s, insight_id)
    view["decision"] = rv.record(insight_id)
    return view


@app.get("/api/replays/{rid}/breakdown")
def breakdown(rid: str, episode_id: str, language: Literal["en", "ko"] = "en",
              x_session_token: str | None = Header(default=None)) -> dict:
    """The guided breakdown of one moment from what the replay has seen so far: the press, what it takes away, what it
    leaves open and what happened, each with its revision, drawing layers, players and code-rendered sentences."""
    s = _session(rid, x_session_token)
    with s.lock:
        revs = list(s.analyzer.packets.get(episode_id, []))
    if not revs:
        raise HTTPException(404, "unknown episode")
    return breakdown_of(revs, s.clip, language)


@app.get("/api/replays/{rid}/recap")
def recap(rid: str, language: Literal["en", "ko"] = "en", x_session_token: str | None = Header(default=None)) -> dict:
    s = _session(rid, x_session_token)
    items = []
    for iid, revs in s.revisions.items():
        view = _insight_view(s, iid)
        if view["analysis_status"] == "insufficient_evidence":
            continue
        items.append({"insight_id": iid, "analysis_status": view["analysis_status"], "outcome": view["outcome"],
                      "review_status": view["insight"]["review_status"],
                      "sentences": view["texts"][f"fan_{language}"]})
    return {"analyzed_through_s": s.clock_s, "items": items}


@app.get("/api/replays/{rid}/export/{insight_id}")
def export(rid: str, insight_id: str, x_session_token: str | None = Header(default=None)) -> dict:
    s = _session(rid, x_session_token)
    ins = s.approved_revision(insight_id)
    if ins is None:
        raise HTTPException(409, "only approved insight revisions can be exported")
    rv = s.approvals[insight_id]
    view = _insight_view(s, insight_id, ins.revision)
    pk = next(p for p in s.analyzer.packets[ins.episode_id] if p.evidence_hash == ins.evidence_hash)
    state = key_state(pk)
    players = _players(s.clip.frames[state.frame_ref.frame_index])
    ovs = []
    for audience in ("fan", "analyst"):
        for language in ("en", "ko"):
            res = build_overlay(state=state, packet=pk, frame_players=players, meta=s.clip.meta,
                                replay_id=s.replay_id, epoch=s.epoch, audience=audience, language=language,
                                surface="broadcast", display_mode="retrospective_replay",
                                positions_at=_positions_at(s))
            ovs.append({"overlay": res.snapshot.model_dump(mode="json", by_alias=True), "label_text": res.label_text})
    # the decision record travels with the moment: what was approved, when and on which evidence, from the file alone
    decision = {**rv.record(insight_id), "episode_revision": pk.episode.revision, "source_mode": ins.source_mode,
                "generation_trace_id": ins.generation_trace_id}
    return {"schema_version": "2.0", "decision": decision, "insight": view, "evidence": pk.model_dump(mode="json"),
            "overlays": ovs}


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    audience: Literal["fan", "analyst"] = "fan"
    language: Literal["en", "ko"] = "en"


def _view(s: ReplaySession) -> View:
    """The replay's analysis as of now, frozen: the agent answers from this even if the replay plays on."""
    with s.lock:
        s.sync()
        t = s.clip.frames[s.cursor_k].match_time_s
        return View.of(s.clip, t, {eid: list(revs) for eid, revs in s.analyzer.packets.items()})


@app.post("/api/replays/{rid}/ask")
async def ask(rid: str, req: Ask, x_session_token: str | None = Header(default=None)) -> dict:
    """Ask PitchIQ: a question about the replay so far, answered by the Ask agent (Foundry) from validated sentences,
    or by the deterministic narration of the main moment when the agent is off, over its cap or fails."""
    s = _session(rid, x_session_token)
    with s.lock:
        if s.asking:
            raise HTTPException(429, "one question at a time")
        s.asking = True
    try:
        view = await run_in_threadpool(_view, s)
        job = CLOUD.ask(view, req.question.strip(), req.audience, req.language)
        if isinstance(job, str):
            reason = CLOUD.disabled_reason if job == "disabled" else job
            trace = AskTrace(f"ask_{os.urandom(6).hex()}", "none", result="fallback", reason=reason).as_dict()
            answer = await run_in_threadpool(fallback_answer, view, req.audience)
        else:
            answer, trace = await asyncio.wrap_future(job)
    finally:
        s.asking = False
    rec = {"ask_id": trace["trace_id"], "question": req.question.strip(), "audience": req.audience,
           "language": req.language, "epoch": s.epoch, "answer": answer,
           "source_mode": "foundry_live" if trace["result"] == "foundry_live" else "deterministic_fallback",
           "trace": trace}
    with s.lock:
        s.asks.append(rec)
        del s.asks[:-20]
    return rec


@app.get("/api/replays/{rid}/asks")
def asks(rid: str, x_session_token: str | None = Header(default=None)) -> list[dict]:
    return list(_session(rid, x_session_token).asks)


@app.post("/api/replays/{rid}/recap/agent")
async def recap_agent(rid: str, profile: RecapProfile, x_session_token: str | None = Header(default=None)) -> dict:
    """Recap of the replay so far for one viewer (audience, language, my club, followed player): the Recap workflow
    on Foundry (an Agent Framework workflow: gather -> recap agent -> code check -> render or fallback), or the
    deterministic recap when the agent is off, over its cap or fails. The plain GET /recap stays deterministic."""
    s = _session(rid, x_session_token)
    with s.lock:
        if s.asking:
            raise HTTPException(429, "one question or recap at a time")
        s.asking = True
    try:
        view = await run_in_threadpool(_view, s)
        try:
            check_profile(profile, view)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        job = CLOUD.recap(view, profile)
        if isinstance(job, str):
            reason = CLOUD.disabled_reason if job == "disabled" else job
            trace = RecapTrace(f"recap_{os.urandom(6).hex()}", "none", result="fallback", reason=reason).as_dict()
            recap = await run_in_threadpool(fallback_recap, view, profile)
        else:
            recap, trace = await asyncio.wrap_future(job)
    finally:
        s.asking = False
    return {"recap": recap, "profile": profile.model_dump(), "epoch": s.epoch,
            "source_mode": "foundry_live" if trace["result"] == "foundry_live" else "deterministic_fallback",
            "trace": trace}


# ------------------------------------------------------------------ WebSocket
@app.websocket("/ws/replays/{rid}")
async def ws(websocket: WebSocket, rid: str, token: str = Query(default="")) -> None:
    try:
        s = STORE.get(rid)
    except KeyError:
        await websocket.close(code=4404)
        return
    if token != s.token:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    s.sockets += 1
    s.touched = time.monotonic()

    def env(kind: str, payload: dict, t: float) -> dict:
        return {"schema_version": "2.0", "replay_id": s.replay_id, "analysis_epoch": s.epoch, "seq": s.next_seq(),
                "type": kind, "match_time_s": t, "payload": payload}
    try:
        await websocket.send_json(env("state_snapshot", _snapshot(s), s.clock_s))
        epoch, last_k = s.epoch, s.cursor_k
        known = {iid: revs[-1].revision for iid, revs in s.revisions.items()}
        last_sent = time.monotonic()
        was_playing = s.playing
        while True:
            await asyncio.sleep(0.1)
            if s.closed:  # evicted or deleted: the client starts a new replay
                await websocket.close(code=4410)
                return
            if time.monotonic() - last_sent > 15.0:  # keeps proxies from closing a paused replay's socket
                await websocket.send_json(env("heartbeat", {}, s.clock_s))
                last_sent = time.monotonic()
            s.sync()
            if s.playing != was_playing:  # the clip ended (or another tab paused): the client's controls follow
                was_playing = s.playing
                await websocket.send_json(env("state_snapshot", _snapshot(s), s.clock_s))
                last_sent = time.monotonic()
            if s.epoch != epoch:  # seek elsewhere: resynchronise with a snapshot instead of replaying a log
                epoch, last_k = s.epoch, s.cursor_k
                known = {iid: revs[-1].revision for iid, revs in s.revisions.items()}
                await websocket.send_json(env("state_snapshot", _snapshot(s), s.clock_s))
                for iid in known:  # the rebuilt revisions, so no card keeps narration from before the seek
                    await websocket.send_json(env("insight_update", _insight_view(s, iid), s.clock_s))
                continue
            if s.cursor_k > last_k:
                k = s.cursor_k  # slow clients get the latest frame only; decisions/insights are never dropped
                r = s.results[k]
                await websocket.send_json(env("tracking_frame", s.clip.frames[k].model_dump(mode="json"), r.t))
                await websocket.send_json(env("tactical_snapshot", {"state": r.state.model_dump(mode="json"),
                                                                    "live_claims": sorted(r.supported)}, r.t))
                last_k, last_sent = k, time.monotonic()
            for iid, revs in list(s.revisions.items()):  # cloud revisions are appended from another thread
                if known.get(iid) != revs[-1].revision:
                    known[iid] = revs[-1].revision
                    await websocket.send_json(env("episode_update", {"episodes": _episodes(s)}, s.clock_s))
                    await websocket.send_json(env("insight_update", _insight_view(s, iid), s.clock_s))
    except WebSocketDisconnect:
        return
    finally:
        s.sockets -= 1


# ------------------------------------------------------------------ MCP
# Streamable HTTP for MCP clients. /mcp is the URL to use; /mcp/ is the same endpoint, so neither falls through to
# the frontend. Registered before the frontend's catch-all "/" mount.
if MCP is not None:
    for _path in ("/mcp", "/mcp/"):
        app.add_route(_path, MCP, name="mcp", include_in_schema=False)


# ------------------------------------------------------------------ frontend
_DIST = Path(os.environ.get("FRONTEND_DIST", Path(__file__).resolve().parents[3] / "frontend" / "dist"))
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
