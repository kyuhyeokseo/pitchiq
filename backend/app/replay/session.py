"""Replay sessions: clock, epochs, seek, per-session review state (spec 14.3).

The analyzer only ever receives frames up to the cursor and events whose
``available_at_s`` has passed. Seek starts a new epoch and rebuilds the
analysis from the clip start without any cloud call. Review decisions and
broadcast visibility live in the session; immutable packets and plans may be
shared through a global cache keyed by evidence hash.
"""

from __future__ import annotations

import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..agents.cloud import CloudInsights
from ..agents.workflow import InsightService
from ..analytics.engine import FrameResult, TacticalAnalyzer
from ..contracts import AnalystPlan, EvidencePacket, InsightRevision, ProducerPlan
from .catalog import Clip

MAX_SESSIONS = 20  # spec 19: one process, sessions in memory; beyond this the least recently used one is evicted
IDLE_TTL_S = 30 * 60  # no client action for this long: evicted even with an open socket (the client starts anew)
UNWATCHED_TTL_S = 10 * 60  # no socket and no client action for this long: evicted


class StaleEpoch(Exception):
    pass


class StaleRevision(Exception):
    pass


@dataclass
class Review:
    status: str  # approved | rejected
    revision: int
    evidence_hash: str
    decided_epoch: int
    visibility: str = "hidden"  # hidden | scheduled | showing | expired
    decided_at: str = ""  # UTC, ISO 8601: the audit record carried by the export

    def record(self, insight_id: str) -> dict:
        return {"insight_id": insight_id, "status": self.status, "insight_revision": self.revision,
                "evidence_hash": self.evidence_hash, "epoch": self.decided_epoch, "decided_at": self.decided_at}


@dataclass
class ReplaySession:
    replay_id: str
    token: str
    clip: Clip
    insights: InsightService
    speed: float = 1.0
    playing: bool = False
    epoch: int = 1
    seq: int = 0
    cursor_k: int = -1
    clock_s: float = 0.0
    last_wall: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)  # last client action (REST call), not socket ticks
    sockets: int = 0  # open WebSocket connections
    closed: bool = False  # evicted or deleted: open sockets close with 4410
    analyzer: TacticalAnalyzer | None = None
    results: list[FrameResult] = field(default_factory=list)
    revisions: dict[str, list[InsightRevision]] = field(default_factory=dict)  # insight_id -> revisions
    reviews: dict[str, Review] = field(default_factory=dict)  # insight_id -> latest decision
    approvals: dict[str, Review] = field(default_factory=dict)  # insight_id -> the approval broadcast airs
    history: list[dict] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)
    cloud: CloudInsights | None = None
    traces: dict[str, dict] = field(default_factory=dict)  # generation_trace_id -> trace of a cloud revision
    asks: list[dict] = field(default_factory=list)  # Ask PitchIQ questions and answers, newest last
    asking: bool = False  # one question at a time per replay

    def __post_init__(self) -> None:
        self.clock_s = self.clip.start_s
        self._new_analyzer()
        self._advance_to(0)

    # ------------------------------------------------------------ clock
    def _new_analyzer(self) -> None:
        self.analyzer = TacticalAnalyzer(self.clip.meta, self.clip.clip_id)
        self.results = []
        self.cursor_k = -1
        self.revisions = {}
        self._delivered: set[str] = set()
        self._insight_packets: dict[str, int] = {}  # episode_id -> packets already turned into insights
        # one cloud job per episode at a time; a newer revision waits and only the latest is sent when it finishes
        self._cloud_busy: dict[str, tuple[int, str]] = {}  # episode_id -> (epoch, evidence_hash) in flight
        self._cloud_next: dict[str, EvidencePacket] = {}  # episode_id -> newest revision waiting

    def _advance_to(self, k_target: int) -> list[FrameResult]:
        new = []
        k_target = min(k_target, len(self.clip.frames) - 1)
        while self.cursor_k < k_target:
            k = self.cursor_k + 1
            f = self.clip.frames[k]
            arrived = [e for e in self.clip.events
                       if e.available_at_s <= f.match_time_s + 1e-9 and e.event_id not in self._delivered]
            self._delivered |= {e.event_id for e in arrived}
            r = self.analyzer.step(f, arrived)
            self.results.append(r)
            self.cursor_k = k
            for eid in dict.fromkeys(r.revised):  # an episode can be revised twice in one step (e.g. closed)
                packets = self.analyzer.packets.get(eid, [])
                for p in packets[self._insight_packets.get(eid, 0):]:  # every new revision, in order, once
                    self._on_revision(p)
                self._insight_packets[eid] = len(packets)
            new.append(r)
        self.clock_s = max(self.clock_s, self.clip.frames[self.cursor_k].match_time_s)
        return new

    def _on_revision(self, packet: EvidencePacket) -> None:
        ins = self.insights.deterministic_revision(packet, len(self.revisions.get(f"ins_{packet.episode.episode_id}",
                                                                                   [])) + 1)
        self.revisions.setdefault(ins.insight_id, []).append(ins)
        if self.cloud is not None and self.cloud.enabled:
            eid = packet.episode.episode_id
            if eid in self._cloud_busy:  # its plan would arrive for evidence that has moved on: send the latest later
                self._cloud_next[eid] = packet
                return
            self._submit_cloud(packet)

    def _submit_cloud(self, packet: EvidencePacket) -> str:
        epoch, eid = self.epoch, packet.episode.episode_id
        self._cloud_busy[eid] = (epoch, packet.evidence_hash)
        status = self.cloud.submit(self.replay_id, packet,
                                   lambda a, p, trace, source: self._attach_cloud(packet, epoch, a, p, trace, source))
        if status in ("queue_full", "daily_limit", "disabled"):  # spec 12.4: the fallback shows at once instead
            del self._cloud_busy[eid]
            if status == "queue_full":  # waits for a free slot; the most important waiting episode goes first
                self._cloud_next[eid] = packet
            self.history.append({"event": "cloud_skipped", "reason": status, "insight_id": f"ins_{eid}",
                                 "evidence_hash": packet.evidence_hash, "epoch": epoch})
        return status

    def _cloud_done(self, packet: EvidencePacket, epoch: int) -> None:
        """Free the episode's cloud slot and send its newest waiting revision, if that is still the latest."""
        eid = packet.episode.episode_id
        if self._cloud_busy.get(eid) != (epoch, packet.evidence_hash):
            return  # a job from an earlier epoch: the slot belongs to the new analysis
        del self._cloud_busy[eid]
        waiting = []
        for wid, nxt in list(self._cloud_next.items()):
            revs = self.revisions.get(f"ins_{wid}")
            if wid in self._cloud_busy:
                continue
            del self._cloud_next[wid]
            if revs and revs[-1].evidence_hash == nxt.evidence_hash:  # only the latest reading is worth a call
                waiting.append(nxt)
        waiting.sort(key=lambda p: (p.episode.analysis_status == "complete_tradeoff", p.episode.story_priority),
                     reverse=True)
        for i, nxt in enumerate(waiting):
            if self._submit_cloud(nxt) == "queue_full":
                for rest in waiting[i + 1:]:
                    self._cloud_next[rest.episode.episode_id] = rest
                break

    def _attach_cloud(self, packet: EvidencePacket, epoch: int, analyst: AnalystPlan | None,
                      producer: ProducerPlan | None, trace: dict, source: str) -> None:
        """A cloud plan arrives later than the replay: it becomes a new suggested revision only if the epoch and the
        evidence are still current; otherwise it goes to the history and never to the broadcast pitch."""
        with self.lock:
            self._cloud_done(packet, epoch)
            iid = f"ins_{packet.episode.episode_id}"
            entry = {"event": "cloud_result", "insight_id": iid, "evidence_hash": packet.evidence_hash,
                     "trace_id": trace.get("trace_id"), "result": trace.get("result"), "reason": trace.get("reason"),
                     "epoch": epoch}
            if analyst is None or producer is None:
                self.history.append({**entry, "attached": False})
                return
            revs = self.revisions.get(iid)
            if epoch != self.epoch or not revs or revs[-1].evidence_hash != packet.evidence_hash:
                self.history.append({**entry, "attached": False,
                                     "late": "epoch" if epoch != self.epoch else "evidence_moved_on"})
                return
            ep = packet.episode
            revs.append(InsightRevision(insight_id=iid, revision=len(revs) + 1, episode_id=ep.episode_id,
                                        episode_revision=ep.revision, evidence_hash=packet.evidence_hash,
                                        analyst_plan=analyst, producer_plan=producer, source_mode=source,
                                        review_status="suggested", generation_trace_id=trace.get("trace_id")))
            self.traces[trace["trace_id"]] = trace
            self.history.append({**entry, "attached": True, "revision": len(revs)})

    def sync(self, now: float | None = None) -> list[FrameResult]:
        """Advance the match clock by wall time while playing; analysis follows the clock."""
        with self.lock:
            now = time.monotonic() if now is None else now
            if self.playing:
                self.clock_s = min(self.clock_s + (now - self.last_wall) * self.speed, self.clip.end_s)
                if self.clock_s >= self.clip.end_s - 1e-9:
                    self.playing = False
            self.last_wall = now
            # the last frame at or before the clock: rounding to the nearest frame would move the clock up to it on
            # every call, and the WebSocket loop calls this every 0.1 s
            k = math.floor((self.clock_s - self.clip.start_s) * self.clip.meta.tracking_hz + 1e-6)
            return self._advance_to(min(max(k, 0), len(self.clip.frames) - 1))

    # ------------------------------------------------------------ controls
    def control(self, action: str, value: float | None, expected_epoch: int) -> None:
        with self.lock:
            if expected_epoch != self.epoch:
                raise StaleEpoch(f"expected epoch {self.epoch}")
            self.sync()
            if action == "play":
                self.playing = True
                self.last_wall = time.monotonic()
            elif action == "pause":
                self.playing = False
            elif action == "set_speed":
                if value is None or not (0.25 <= value <= 4.0):
                    raise ValueError("speed must be between 0.25 and 4")
                self.speed = float(value)
            elif action == "seek":
                if value is None or not (self.clip.start_s <= value <= self.clip.end_s):
                    raise ValueError("seek target is outside the clip")
                self.seek(float(value))
            else:
                raise ValueError(f"unknown action {action}")

    def seek(self, t: float) -> None:
        """New epoch: drop pending work and visible broadcast, rebuild deterministic analysis up to t."""
        with self.lock:
            self.epoch += 1
            self.seq = 0
            for rv in [*self.reviews.values(), *self.approvals.values()]:
                rv.visibility = "hidden"  # an approval stays valid for its exact revision and evidence, but off air
            self.history.append({"event": "seek", "to_s": t, "epoch": self.epoch})
            self._new_analyzer()
            self._advance_to(self.clip.frame_at(t))
            self.clock_s = t
            self.last_wall = time.monotonic()

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    # ------------------------------------------------------------ review
    def latest_revision(self, insight_id: str) -> InsightRevision:
        revs = self.revisions.get(insight_id)
        if not revs:
            raise KeyError(insight_id)
        return revs[-1]

    def decide(self, insight_id: str, revision: int, evidence_hash: str, decision: str) -> Review:
        with self.lock:
            latest = self.latest_revision(insight_id)
            if latest.revision != revision or latest.evidence_hash != evidence_hash:
                raise StaleRevision(f"latest revision is {latest.revision}")
            status = {"approve": "approved", "reject": "rejected"}[decision]
            rv = Review(status, revision, evidence_hash, self.epoch,
                        "scheduled" if status == "approved" else "hidden",
                        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
            self.reviews[insight_id] = rv
            on_air = self.approvals.get(insight_id)
            if status == "approved":
                self.approvals[insight_id] = rv
            elif on_air is not None and on_air.revision == revision and on_air.evidence_hash == evidence_hash:
                del self.approvals[insight_id]  # rejecting the revision on air takes it off; rejecting a newer one does not
            self.history.append({"event": "decision", "insight_id": insight_id, "revision": revision,
                                 "decision": status, "epoch": self.epoch, "at_clock_s": self.clock_s})
            return rv

    def review_status(self, ins: InsightRevision) -> str:
        for rv in (self.reviews.get(ins.insight_id), self.approvals.get(ins.insight_id)):
            if rv is not None and rv.revision == ins.revision and rv.evidence_hash == ins.evidence_hash:
                return rv.status
        return "suggested"

    def approved_revision(self, insight_id: str) -> InsightRevision | None:
        """The approved revision while this analysis has it: the same number on the same evidence, as in
        review_status. A seek rebuilds the revisions, so a number alone could name a revision nobody approved."""
        rv = self.approvals.get(insight_id)
        if rv is None:
            return None
        return next((r for r in self.revisions.get(insight_id, [])
                     if r.revision == rv.revision and r.evidence_hash == rv.evidence_hash), None)


class SessionStore:
    """Replay sessions of one process. A public link must never lock people out: when full, the least recently used
    session is evicted instead of refusing a new one, and sessions nobody uses any more are dropped."""

    def __init__(self, insights: InsightService, cloud: CloudInsights | None = None):
        self.insights = insights
        self.cloud = cloud
        self.sessions: dict[str, ReplaySession] = {}
        self.lock = threading.Lock()
        self._n = 0

    def create(self, clip: Clip, start_s: float | None, speed: float | None) -> ReplaySession:
        if start_s is not None and not (clip.start_s <= start_s <= clip.end_s):
            raise ValueError("start_s is outside the clip")
        with self.lock:
            self.gc()
            while len(self.sessions) >= MAX_SESSIONS:
                self._drop(min(self.sessions.values(), key=lambda x: x.touched).replay_id)
            self._n += 1
            rid = f"replay_{self._n:03d}_{secrets.token_hex(3)}"
        s = ReplaySession(rid, secrets.token_urlsafe(16), clip, self.insights, speed=speed or 1.0, cloud=self.cloud)
        if start_s is not None:
            s.seek(start_s)
            s.epoch = 1
        with self.lock:
            self.sessions[rid] = s
        return s

    def get(self, rid: str) -> ReplaySession:
        s = self.sessions.get(rid)
        if s is None:
            raise KeyError(rid)
        return s

    def delete(self, rid: str) -> None:
        with self.lock:
            self._drop(rid)

    def _drop(self, rid: str) -> None:
        s = self.sessions.pop(rid, None)
        if s is not None:
            s.closed = True

    def gc(self, now: float | None = None) -> None:
        """Call with the lock held."""
        now = time.monotonic() if now is None else now
        for rid, s in list(self.sessions.items()):
            idle = now - s.touched
            if idle > IDLE_TTL_S or (s.sockets == 0 and idle > UNWATCHED_TTL_S):
                self._drop(rid)
