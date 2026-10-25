"""Frame-by-frame tactical analysis (spec 16.1): observation -> state -> claims -> episodes -> packets.

``TacticalAnalyzer.step`` consumes one observed frame plus the events that
became available by that frame, and never looks ahead. Everything it emits is
a v2 contract model. Replays rebuild an analyzer from the clip start, so the
same prefix always gives the same ids, claims and packets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..contracts import (
    XY,
    CoverRelation,
    EvidencePacket,
    FrameRef,
    LaneRelation,
    LineGroup,
    LocalRegion,
    MarkingRelation,
    MatchEvent,
    MatchMeta,
    ObservationFrame,
    Outcome,
    PressureRelation,
    TacticalChange,
    TacticalState,
    TradeoffEpisode,
    Velocity,
    Window,
    compute_evidence_hash,
)
from .config import Config, load_config
from .cover import CoverResult, estimate_cover, runners
from .lanes import Lane, offside_line_u, screen_pass_options
from .lines import LinesResult, estimate_lines
from .marking import MarkEdge, MarkingTracker, zone_stats
from .observe import ObservationBuffer, to_attack_frame, to_world
from .phase import PhaseInfo, estimate_phase
from .pressure import Press, infer_pressure
from .regions import Region, central_region
from ..contracts.evidence import _state_players
from ..contracts.registry import CLAIM_DIMENSION
from .tradeoffs import (
    FAMILY_OF,
    FORBIDDEN_ALWAYS,
    Episode,
    FrameClaim,
    metric,
    outcome_from_events,
    story_priority,
    to_claim,
)

ANALYTICS_VERSION = "analytics-0.1.1"
JOINABLE = frozenset({"accessible_free_receiver", "wide_outlet_available", "local_central_overload"})
JOIN_TARGETS = frozenset({"press_vs_depth", "central_screen"})
SUSTAIN_KEY = {"restricted": "sustain_s", "exposed": "exposure_sustain_s", "context": "sustain_s"}


@dataclass
class FrameRelations:
    phase: PhaseInfo
    lines: LinesResult | None
    edges: list[MarkEdge]
    lanes: list[Lane]
    presses: list[Press]
    covers: list[CoverResult]
    region: Region | None
    zone: object | None


@dataclass
class FrameResult:
    k: int
    t: float
    state: TacticalState
    relations: FrameRelations
    supported: dict[str, FrameClaim]
    revised: list[str]  # episode ids that got a new revision on this frame


class TacticalAnalyzer:
    def __init__(self, meta: MatchMeta, clip_id: str, cfg: Config | None = None):
        self.cfg = cfg or load_config()
        self.meta = meta
        self.clip_id = clip_id
        self.clip_end_s = next(c.end_s for c in meta.clips if c.clip_id == clip_id)
        self.hz = meta.tracking_hz
        self.buf = ObservationBuffer(meta, clip_id)
        self.marking = MarkingTracker(self.cfg)
        self.lane_hist: list[list[Lane]] = []
        self.last_attacking: str | None = None
        self.states: dict[str, TacticalState] = {}
        self.relations: dict[int, FrameRelations] = {}
        self.streak: dict[str, int] = {}
        self.episodes: dict[str, Episode] = {}
        self.open_key: dict[tuple[str, str], str] = {}
        self.packets: dict[str, list[EvidencePacket]] = {}
        self.claim_state: dict[str, str] = {}  # claim_id -> state_id it was taken from
        self.closed_log: list[tuple[float, str, str]] = []
        self._seq = 0
        self._side_revised: list[str] = []  # revisions made outside _episodes (closing on possession change)
        self.back_hist: list[tuple[int, tuple[int, ...], float]] = []  # (frame, back-line members, depth)
        self.watch: dict[str, float] = {}  # closed episodes whose outcome window is still open -> window end

    # ------------------------------------------------------------------ ids
    def pid(self, i: int) -> str:
        return self.buf.pids[i]

    def fref(self, k: int) -> FrameRef:
        return FrameRef(clip_id=self.clip_id, frame_index=self.buf.frames[k].frame_ref.frame_index)

    def state_id(self, k: int) -> str:
        return f"st_{self.clip_id}_f{self.buf.frames[k].frame_ref.frame_index:04d}"

    # ------------------------------------------------------------------ main
    def step(self, frame: ObservationFrame, arrived: list[MatchEvent] = ()) -> FrameResult:
        k = self.buf.append(frame, list(arrived))
        t = frame.match_time_s
        ph = estimate_phase(self.buf, k, self.cfg)
        if ph.attacking != self.last_attacking:
            if self.last_attacking is not None:
                self._close_all(k, "possession_change")
            self.marking.reset()
            self.lane_hist.clear()
            self.streak.clear()
            self.back_hist.clear()
            self.last_attacking = ph.attacking
        rel = self._relations(k, ph)
        self.relations[k] = rel
        back = rel.lines.get("back") if rel.lines is not None else None
        if back is not None:
            self.back_hist.append((k, back.idx, back.depth_m))
        state = self._state(k, rel)
        self.states[state.state_id] = state
        fclaims = self._frame_claims(k, rel, state) if self._claims_allowed(ph) else {}
        for code in set(self.streak) | set(fclaims):
            self.streak[code] = self.streak.get(code, 0) + 1 if code in fclaims else 0
        supported = {}
        for code, fc in fclaims.items():
            dim = "restricted" if code in ("short_options_screened", "central_routes_screened",
                                           "behind_depth_reduced") else "exposed"
            need = max(1, int(round(getattr(self.cfg.claims, SUSTAIN_KEY[dim]) * self.hz)))
            if self.streak[code] >= need:
                supported[code] = fc
        revised = self._side_revised + self._episodes(k, t, ph, rel, state, fclaims, supported)
        self._side_revised = []
        revised += self._watch_outcomes(k, t, state)
        return FrameResult(k, t, state, rel, supported, revised)

    def _claims_allowed(self, ph: PhaseInfo) -> bool:
        return ph.possession_phase in ("buildup", "progression") and ph.ball_state == "controlled"

    # ------------------------------------------------------------------ relations
    def _relations(self, k: int, ph: PhaseInfo) -> FrameRelations:
        if ph.attacking is None:
            return FrameRelations(ph, None, [], [], [], [], None, None)
        A, D, cfg, buf = ph.attacking, ph.defending, self.cfg, self.buf
        lines = estimate_lines(buf, k, D, A, cfg)
        edges = self.marking.update(buf, k, D, A)
        zone = zone_stats(buf, k, A, edges, cfg)
        lanes = screen_pass_options(buf, k, A, D, cfg)
        self.lane_hist.append(lanes)
        presses = infer_pressure(buf, k, A, D, self.lane_hist, cfg)
        covers = [estimate_cover(buf, k, A, D, a, el, edges, cfg) for a, el in runners(buf, k, A, D, cfg)]
        region = central_region(buf, k, A, D, lanes, edges, cfg)
        return FrameRelations(ph, lines, edges, lanes, presses, covers, region, zone)

    def _state(self, k: int, r: FrameRelations) -> TacticalState:
        buf, cfg, t = self.buf, self.cfg, self.buf.t[k]
        ph = r.phase
        w0 = buf.t[buf.window(k, cfg.window.state_s).start]
        flags = list(ph.flags)
        line_groups, shape = [], "unclassified"
        if r.lines is not None:
            line_groups = [LineGroup(group=g.group, player_ids=[self.pid(i) for i in g.idx],
                                     own_goal_depth_m=round(g.depth_m, 2), spread_m=round(g.spread_m, 2),
                                     support_score=round(g.support, 3)) for g in r.lines.groups]
            shape = r.lines.shape_label
            if not r.lines.full:
                flags.append("partial_line_groups")
        if ph.possession_phase == "transition":
            shape = "transition"
            if line_groups and "partial_line_groups" not in flags and len(line_groups) < 3:
                flags.append("partial_line_groups")
        if ph.ball_state == "in_flight":
            flags.append("ball_in_flight")
        markings = [MarkingRelation(relation_id=f"mk_{self.pid(e.defender)}_{self.pid(e.attacker)}_f{e.since_k:04d}",
                                    defender_id=self.pid(e.defender), attacker_id=self.pid(e.attacker),
                                    since_s=buf.t[e.since_k], mean_distance_m=round(e.mean_distance_m, 2),
                                    follow_support_score=round(e.follow_support, 3), status=e.status)
                    for e in r.edges]
        if r.zone is not None and r.zone.coverage is not None:
            if (len(r.zone.supported) >= cfg.marking.man_oriented_min_edges
                    and r.zone.coverage >= cfg.marking.man_oriented_min_coverage):
                flags.append("man_oriented_zone")
        pressures = [PressureRelation(relation_id=f"pr_{self.pid(p.presser)}_{self.pid(p.carrier)}_f{p.since_k:04d}",
                                      presser_id=self.pid(p.presser), carrier_id=self.pid(p.carrier),
                                      distance_m=round(p.distance_m, 2), closing_speed_mps=round(p.closing_mps, 2),
                                      approach_vector=Velocity(vx_mps=round(p.approach[0], 2),
                                                               vy_mps=round(p.approach[1], 2)),
                                      funnel=p.funnel, likely_outlet_ids=[self.pid(i) for i in p.likely_outlets],
                                      support_score=round(p.support, 3))
                     for p in r.presses[:1]]
        lanes = []
        for lane in r.lanes:
            s = buf.ball[k]
            e = buf.pos[k][lane.receiver]
            lanes.append(LaneRelation(relation_id=self._lane_id(k, lane), carrier_id=self.pid(lane.carrier),
                                      receiver_id=self.pid(lane.receiver), start=_xy(s), end=_xy(e),
                                      eligibility=lane.eligibility, availability=lane.availability,
                                      min_arrival_margin_s=round(lane.min_margin_s, 3),
                                      screening_player_ids=[self.pid(i) for i in lane.screeners]))
        covers = []
        for c in r.covers:
            tgt = _xy(to_world(np.array(c.target_uv), buf.direction(ph.attacking)))
            att = round(c.attacker_arrival_s, 3)
            if c.outfield_cover is not None:
                cov = round(c.outfield_arrival_s, 3)
                covers.append(CoverRelation(relation_id=self._cover_id(k, c, "out"), attacker_id=self.pid(c.attacker),
                                            primary_defender_id=None if c.marker is None else self.pid(c.marker),
                                            cover_player_id=self.pid(c.outfield_cover), target=tgt,
                                            attacker_arrival_s=att, cover_arrival_s=cov,
                                            cover_margin_s=round(cov - att, 6), status="ok"))
            else:
                covers.append(CoverRelation(relation_id=self._cover_id(k, c, "out"), attacker_id=self.pid(c.attacker),
                                            primary_defender_id=None if c.marker is None else self.pid(c.marker),
                                            cover_player_id=None, target=tgt, attacker_arrival_s=None,
                                            cover_arrival_s=None, cover_margin_s=None, status="unavailable"))
            gk = round(c.gk_arrival_s, 3)
            covers.append(CoverRelation(relation_id=self._cover_id(k, c, "gk"), attacker_id=self.pid(c.attacker),
                                        primary_defender_id=None if c.marker is None else self.pid(c.marker),
                                        cover_player_id=self.pid(c.gk), target=tgt, attacker_arrival_s=att,
                                        cover_arrival_s=gk, cover_margin_s=round(gk - att, 6), status="ok"))
        regions = []
        if r.region is not None:
            regions.append(LocalRegion(region_id=f"rg_central_f{self.fref(k).frame_index:04d}",
                                       polygon=[XY(x_m=round(x, 2), y_m=round(y, 2)) for x, y in r.region.polygon_world],
                                       attacking_player_ids=[self.pid(i) for i in r.region.attackers],
                                       defending_player_ids=[self.pid(i) for i in r.region.defenders],
                                       accessible_receiver_ids=[self.pid(i) for i in r.region.accessible]))
        return TacticalState(state_id=self.state_id(k), analyzed_until_s=t, frame_ref=self.fref(k),
                             window=Window(start_s=w0, end_s=t), attacking_team_id=ph.attacking,
                             defending_team_id=ph.defending, possession_phase=ph.possession_phase,
                             active_press=bool(r.presses), shape_label=shape, line_groups=line_groups,
                             markings=markings, pressures=pressures, lanes=lanes, covers=covers, regions=regions,
                             quality_flags=sorted(set(flags)))

    def _lane_id(self, k: int, lane: Lane) -> str:
        return f"ln_{self.pid(lane.carrier)}_{self.pid(lane.receiver)}_f{self.fref(k).frame_index:04d}"

    def _cover_id(self, k: int, c: CoverResult, kind: str) -> str:
        return f"cv_{self.pid(c.attacker)}_{kind}_f{self.fref(k).frame_index:04d}"

    # ------------------------------------------------------------------ claims
    def _frame_claims(self, k: int, r: FrameRelations, state: TacticalState) -> dict[str, FrameClaim]:
        cfg, buf = self.cfg, self.buf
        t = buf.t[k]
        fr = [self.fref(k)]
        f = self.fref(k).frame_index
        out: dict[str, FrameClaim] = {}
        win = (buf.t[buf.window(k, cfg.window.state_s).start], t)
        # --- restricted: short options screened
        elig = [lane for lane in r.lanes if lane.eligibility == "onside_geometry"]
        scr = [lane for lane in elig if lane.availability == "screened"]
        if len(elig) >= cfg.claims.short_options_min_candidates and len(scr) >= cfg.claims.short_options_min_screened:
            carrier = self.pid(elig[0].carrier)
            ms = [metric(f"m_short_candidates_f{f:04d}", "short_option_candidates", len(elig), "count", (t, t),
                         [carrier] + [self.pid(lane.receiver) for lane in elig], fr),
                  metric(f"m_short_screened_f{f:04d}", "short_options_screened", len(scr), "count", (t, t),
                         sorted({self.pid(i) for lane in scr for i in (lane.receiver, *lane.screeners)}), fr)]
            if r.zone is not None and r.zone.coverage is not None:
                ms.append(metric(f"m_zone_cov_f{f:04d}", "zone_marking_coverage", r.zone.coverage, "ratio", win,
                                 [self.pid(i) for i in r.zone.zone_opponents], fr, method="marking"))
            rels = [self._lane_id(k, lane) for lane in scr]
            if r.presses:
                p = r.presses[0]
                ms.append(metric(f"m_presser_dist_f{f:04d}", "presser_distance_m", p.distance_m, "m",
                                 (buf.t[max(0, k - self.hz)], t), [self.pid(p.presser), carrier], fr,
                                 method="pressure"))
                rels.append(state.pressures[0].relation_id)
            players = sorted({carrier, *(self.pid(lane.receiver) for lane in scr),
                              *(self.pid(i) for lane in scr for i in lane.screeners)})
            # screened by the lane model (fixed speeds and reaction), like central_routes_screened: a heuristic
            out["short_options_screened"] = FrameClaim("short_options_screened", "heuristic_inference", ms, rels,
                                                       players, 0.4 + 0.6 * len(scr) / len(elig))
        # --- exposed: behind space (needs a high identified back line) / isolation (needs a supported pair)
        back = r.lines.get("back") if r.lines is not None else None
        high = back is not None and back.depth_m >= cfg.lines.high_back_depth_m
        cands = [c for c in r.covers if c.eligibility == "onside_geometry"]
        if high and not cands:
            for c in r.covers:
                if c.eligibility == "offside_geometry":  # a run from beyond the line is not space behind it
                    self._note_suppressed("behind_space_exposed", "offside_geometry", self.pid(c.attacker))
        if cands:
            c = max(cands, key=lambda x: x.forward_speed_mps)
            runner = self.pid(c.attacker)
            back_ids = [self.pid(i) for i in back.idx] if back is not None else []
            m_speed = metric(f"m_runner_speed_f{f:04d}", "runner_forward_speed_mps", c.forward_speed_mps, "mps",
                             (t, t), [runner], fr, method="cover")
            m_out = c.outfield_margin_s
            cover_ids = [self._cover_id(k, c, "out"), self._cover_id(k, c, "gk")]
            m_cov = metric(f"m_cover_margin_f{f:04d}", "cover_margin_s", m_out, "s", (t, t),
                           [runner] + ([self.pid(c.outfield_cover)] if c.outfield_cover is not None else []), fr,
                           method="cover")
            m_gk = metric(f"m_gk_margin_f{f:04d}", "gk_cover_margin_s", c.gk_margin_s, "s", (t, t),
                          [runner, self.pid(c.gk)], fr, method="cover")
            late = m_out is None or m_out > cfg.cover.late_cover_margin_s
            if high and late:
                m_depth = metric(f"m_back_depth_f{f:04d}", "back_line_depth_m", back.depth_m, "m", win, back_ids,
                                 fr, method="lines")
                players = sorted({runner, *back_ids,
                                  *([self.pid(c.outfield_cover)] if c.outfield_cover is not None else [])})
                sup = 0.5 + 0.3 * min((1.0 if m_out is None else m_out) / 1.0, 1.0) + 0.2 * min(
                    c.forward_speed_mps / 6.0, 1.0)
                out["behind_space_exposed"] = FrameClaim("behind_space_exposed", "heuristic_inference",
                                                         [m_depth, m_cov, m_speed], cover_ids[:1], players, sup)
            elif high and not late:
                self._note_suppressed("behind_space_exposed", "secondary_cover_in_time", runner)
            edge = c.marker_edge
            if edge is not None and edge.status == "ok":
                if c.min_margin_s >= cfg.cover.isolated_cover_margin_s and late:
                    m_follow = metric(f"m_follow_f{f:04d}", "marking_follow_support", edge.follow_support, "score",
                                      win, [self.pid(edge.defender), runner], fr, method="marking")
                    players = sorted({runner, self.pid(edge.defender), self.pid(c.gk),
                                      *([self.pid(c.outfield_cover)] if c.outfield_cover is not None else [])})
                    mk = next(m.relation_id for m in state.markings if m.attacker_id == runner)
                    sup = 0.45 + 0.35 * min(c.min_margin_s / 1.0, 1.0) + 0.2 * edge.follow_support
                    out["isolated_1v1"] = FrameClaim("isolated_1v1", "heuristic_inference",
                                                     [m_cov, m_gk, m_follow, m_speed], [mk, *cover_ids], players, sup)
                else:
                    secondary_close = m_out is not None and m_out < cfg.cover.isolated_cover_margin_s
                    reason = "secondary_cover_in_time" if secondary_close else "gk_cover_in_time"
                    self._note_suppressed("isolated_1v1", reason, runner)
        self._claims_b(k, r, state, out, fr, f, win)
        self._claims_c(k, r, state, out, fr, f, win, elig)
        for code in ("short_options_screened", "central_routes_screened"):
            if code in out:
                out[code].metrics.extend(self._press_context(k, r, fr, f))
        # One restricting story per press: man-oriented relations around the ball make it family A (short options
        # screened by markers); without them the screening is lane-oriented, family C (spec 6.1). A story already
        # running on this carrier keeps its family: a defender stepping onto him continues it, not a new one.
        if "short_options_screened" in out and "central_routes_screened" in out:
            carrier = self.buf.owner[k]
            ongoing = {fam for (team, fam), eid in self.open_key.items()
                       if team == r.phase.defending and fam in ("press_vs_depth", "central_screen")
                       and self.episodes[eid].lifecycle != "candidate" and self.episodes[eid].anchor_carrier == carrier}
            if len(ongoing) == 1:
                del out["central_routes_screened" if ongoing == {"press_vs_depth"} else "short_options_screened"]
            else:
                del out["central_routes_screened" if "man_oriented_zone" in state.quality_flags
                        else "short_options_screened"]
        return out

    def _press_context(self, k, r, fr, f) -> list:
        """Lanes the primary presser himself cuts (or, with no presser, how much room the carrier has), and the
        numbers around and ahead of the ball."""
        cfg, buf, t = self.cfg, self.buf, self.buf.t[k]
        A, D = r.phase.attacking, r.phase.defending
        out = []
        if not r.presses and buf.owner[k] >= 0:
            c_ = buf.owner[k]
            dist, d = min((float(np.linalg.norm(buf.pos[k][d] - buf.pos[k][c_])), d) for d in buf.members(D))
            out.append(metric(f"m_carrier_space_f{f:04d}", "carrier_nearest_defender_m", dist, "m", (t, t),
                              [self.pid(c_), self.pid(d)], fr, method="pressure"))
        if r.presses:
            p = r.presses[0]
            cut = [lane for lane in r.lanes if lane.availability == "screened" and p.presser in lane.screeners
                   and lane.eligibility == "onside_geometry"]
            if cut:
                out.append(metric(f"m_press_cut_f{f:04d}", "press_cut_lanes", len(cut), "count", (t, t),
                                  [self.pid(p.presser)] + [self.pid(lane.receiver) for lane in cut], fr,
                                  method="pressure"))
        uv = buf.uv(k, A)
        ball_u = float(buf.ball_uv(k, A)[0])
        def zone(team, lo, hi):
            return [self.pid(i) for i in buf.members(team) if lo <= uv[i, 0] - ball_u <= hi]
        c = cfg.claims
        ba, bd = zone(A, -105.0, c.buildup_zone_ahead_m), zone(D, -105.0, c.buildup_zone_ahead_m)
        ra, rd = zone(A, c.rest_zone_ahead_m, 105.0), zone(D, c.rest_zone_ahead_m, 105.0)
        for key, ids in (("buildup_count_attack", ba), ("buildup_count_defense", bd),
                         ("rest_count_attack", ra), ("rest_count_defense", rd)):
            out.append(metric(f"m_{key}_f{f:04d}", key, len(ids), "count", (t, t), ids, fr, method="regions"))
        return out

    def _claims_b(self, k, r, state, out, fr, f, win) -> None:
        """Family B: a back line that dropped, a press disconnected from it, receivers between the lines."""
        cfg, buf, t = self.cfg, self.buf, self.buf.t[k]
        A = r.phase.attacking
        lines = r.lines
        back = lines.get("back") if lines is not None else None
        mid = lines.get("mid") if lines is not None else None
        front = lines.get("front") if lines is not None else None
        if back is None:
            return
        back_ids = [self.pid(i) for i in back.idx]
        win_k = int(round(cfg.claims.depth_change_window_s * self.hz))
        earlier = [(kk, idx, d) for kk, idx, d in self.back_hist
                   if k - win_k <= kk < k - 2 and len(set(idx) & set(back.idx)) >= 3]
        if earlier:
            kk, _, d0 = max(earlier, key=lambda x: x[2])
            drop = back.depth_m - d0
            if drop <= -cfg.claims.depth_reduced_min_m:
                m_depth = metric(f"m_back_depth_f{f:04d}", "back_line_depth_m", back.depth_m, "m", win, back_ids, fr,
                                 method="lines")
                m_drop = metric(f"m_depth_drop_f{f:04d}", "behind_depth_change_m", drop, "m", (buf.t[kk], t),
                                back_ids, [self.fref(kk), self.fref(k)], method="lines")
                out["behind_depth_reduced"] = FrameClaim(
                    "behind_depth_reduced", "computed_observation", [m_depth, m_drop], [], back_ids,
                    0.6 + 0.4 * min(-drop / 16.0, 1.0))
        if mid is None:
            return
        uv = buf.uv(k, A)
        carrier = buf.owner[k]
        u_back, u_mid = 105.0 - back.depth_m, 105.0 - mid.depth_m
        m = cfg.claims.between_lines_margin_m
        between = [int(a) for a in buf.members(A) if a != carrier and u_mid + m < uv[a, 0] < u_back - m]
        gap = mid.depth_m - back.depth_m
        span = front.depth_m - back.depth_m if front is not None else None
        mid_ids = [self.pid(i) for i in mid.idx]
        m_gap = metric(f"m_mid_back_gap_f{f:04d}", "mid_back_gap_m", gap, "m", win, mid_ids + back_ids, fr,
                       method="lines")
        stretched = gap >= cfg.claims.gap_disconnected_m or (span is not None and span >= cfg.claims.span_disconnected_m)
        if r.presses and stretched and between:
            p = r.presses[0]
            ms = [m_gap, metric(f"m_presser_dist_f{f:04d}", "presser_distance_m", p.distance_m, "m",
                                (buf.t[max(0, k - self.hz)], t), [self.pid(p.presser), self.pid(p.carrier)], fr,
                                method="pressure")]
            if span is not None:
                ms.insert(1, metric(f"m_front_back_span_f{f:04d}", "front_back_span_m", span, "m", win,
                                    [self.pid(i) for i in front.idx] + back_ids, fr, method="lines"))
            known = _state_players(state)  # a claim may only name players that appear in this state's relations
            players = sorted({self.pid(p.presser), *mid_ids, *back_ids,
                              *(self.pid(a) for a in between if self.pid(a) in known)})
            out["press_block_disconnected"] = FrameClaim(
                "press_block_disconnected", "heuristic_inference", ms, [state.pressures[0].relation_id], players,
                0.55 + 0.45 * min(max(gap - cfg.claims.gap_disconnected_m, 0.0) / 15.0 + 0.3, 1.0))
        lanes_by = {lane.receiver: lane for lane in r.lanes}
        ok = [a for a in between if a in lanes_by and lanes_by[a].availability == "open"
              and lanes_by[a].eligibility == "onside_geometry"
              and lanes_by[a].reception_margin_s >= cfg.regions.receiver_margin_s]
        if ok:
            a = max(ok, key=lambda x: lanes_by[x].reception_margin_s)
            lane = lanes_by[a]
            near = float(np.min(np.linalg.norm(buf.pos[k][buf.members(r.phase.defending)] - buf.pos[k][a], axis=1)))
            ms = [m_gap,
                  metric(f"m_recv_margin_{self.pid(a)}_f{f:04d}", "receiver_margin_s", lane.reception_margin_s, "s",
                         (t, t), [self.pid(a)], fr),
                  metric(f"m_nearest_def_{self.pid(a)}_f{f:04d}", "nearest_defender_m", near, "m", (t, t),
                         [self.pid(a)], fr, method="regions")]
            out["between_lines_receiver"] = FrameClaim(
                "between_lines_receiver", "heuristic_inference", ms, [self._lane_id(k, lane)],
                sorted({self.pid(carrier), self.pid(a), *mid_ids, *back_ids}),
                0.5 + 0.25 * min(gap / 30.0, 1.0) + 0.25 * min(lane.reception_margin_s, 1.0), (self.pid(a),))

    def _claims_c(self, k, r, state, out, fr, f, win, elig) -> None:
        """Family C: central routes screened while a wide outlet stays open; local numbers; free receivers."""
        cfg, buf, t = self.cfg, self.buf, self.buf.t[k]
        carrier = buf.owner[k]
        cen = [lane for lane in elig if lane.side == "central"]
        cen_scr = [lane for lane in cen if lane.availability == "screened"]
        if (r.presses and len(cen) >= cfg.claims.central_min_candidates
                and len(cen_scr) >= cfg.claims.central_min_screened):
            p = r.presses[0]
            ms = [metric(f"m_central_cand_f{f:04d}", "central_candidates", len(cen), "count", (t, t),
                         [self.pid(carrier)] + [self.pid(lane.receiver) for lane in cen], fr),
                  metric(f"m_central_scr_f{f:04d}", "central_screened", len(cen_scr), "count", (t, t),
                         sorted({self.pid(i) for lane in cen_scr for i in (lane.receiver, *lane.screeners)}), fr),
                  metric(f"m_presser_dist_f{f:04d}", "presser_distance_m", p.distance_m, "m",
                         (buf.t[max(0, k - self.hz)], t), [self.pid(p.presser), self.pid(p.carrier)], fr,
                         method="pressure")]
            players = sorted({self.pid(carrier), *(self.pid(lane.receiver) for lane in cen_scr),
                              *(self.pid(i) for lane in cen_scr for i in lane.screeners)})
            out["central_routes_screened"] = FrameClaim(
                "central_routes_screened", "heuristic_inference", ms,
                [self._lane_id(k, lane) for lane in cen_scr] + [state.pressures[0].relation_id], players,
                0.45 + 0.55 * len(cen_scr) / len(cen))
            wide = [lane for lane in elig if lane.side != "central" and lane.availability == "open"]
            if wide:
                best = max(wide, key=lambda lane: lane.min_margin_s)
                ms = [metric(f"m_wide_open_f{f:04d}", "wide_open_outlets", len(wide), "count", (t, t),
                             [self.pid(lane.receiver) for lane in wide], fr),
                      metric(f"m_wide_margin_f{f:04d}", "lane_arrival_margin_s", best.min_margin_s, "s", (t, t),
                             [self.pid(best.receiver)], fr)]
                out["wide_outlet_available"] = FrameClaim(
                    "wide_outlet_available", "heuristic_inference", ms, [self._lane_id(k, lane) for lane in wide],
                    sorted({self.pid(carrier), *(self.pid(lane.receiver) for lane in wide)}),
                    0.5 + 0.5 * min(best.min_margin_s / 1.0, 1.0), tuple(self.pid(lane.receiver) for lane in wide))
        reg = r.region
        if reg is None or not state.regions:
            return
        rid = state.regions[0].region_id
        nA, nD = len(reg.attackers), len(reg.defenders)
        m_ca = metric(f"m_central_att_f{f:04d}", "central_count_attack", nA, "count", (t, t),
                      [self.pid(i) for i in reg.attackers], fr, method="regions")
        m_cd = metric(f"m_central_def_f{f:04d}", "central_count_defense", nD, "count", (t, t),
                      [self.pid(i) for i in reg.defenders], fr, method="regions")
        if nA and nA - nD >= cfg.claims.overload_min_delta:
            out["local_central_overload"] = FrameClaim(
                "local_central_overload", "computed_observation", [m_ca, m_cd], [rid],
                sorted({*(self.pid(i) for i in reg.attackers), *(self.pid(i) for i in reg.defenders)}),
                min(1.0, 0.6 + 0.1 * (nA - nD)))
        lane_by = {lane.receiver: lane for lane in r.lanes}
        if reg.free:
            a = max(reg.free, key=lambda x: reg.receiver_checks[x]["receiver_margin_s"])
            ch = reg.receiver_checks[a]
            ms = [metric(f"m_recv_margin_{self.pid(a)}_f{f:04d}", "receiver_margin_s", ch["receiver_margin_s"], "s",
                         (t, t), [self.pid(a)], fr),
                  metric(f"m_nearest_def_{self.pid(a)}_f{f:04d}", "nearest_defender_m", ch["nearest_defender_m"], "m",
                         (buf.t[buf.window(k, cfg.window.pressure_s).start], t), [self.pid(a)], fr,
                         method="regions"),
                  m_ca, m_cd]
            out["accessible_free_receiver"] = FrameClaim(
                "accessible_free_receiver", "heuristic_inference", ms, [self._lane_id(k, lane_by[a]), rid],
                sorted({self.pid(carrier), self.pid(a)}), 0.5 + 0.5 * min(ch["receiver_margin_s"] / 1.0, 1.0),
                (self.pid(a),))
        elif nA - nD >= cfg.claims.overload_min_delta:
            self._note_suppressed("accessible_free_receiver", "count_without_accessible_lane", "central",
                                  family="central_screen")

    def _note_suppressed(self, code: str, reason: str, runner: str, family: str = "press_vs_depth") -> None:
        """Record why a claim was NOT made; the narrative may never assert it for this episode."""
        for ep in self.episodes.values():
            if ep.lifecycle in ("candidate", "active", "cooling") and ep.family == family and code not in ep.claims:
                ep.suppressed.add(f"{code}:{reason}:{runner}")

    # ------------------------------------------------------------------ episodes
    def _episodes(self, k: int, t: float, ph: PhaseInfo, r: FrameRelations, state: TacticalState,
                  fclaims: dict[str, FrameClaim], supported: dict[str, FrameClaim]) -> list[str]:
        cfg, hz = self.cfg, self.hz
        revised: list[str] = []
        if ph.defending is None:
            return revised
        in_flight = ph.ball_state == "in_flight"
        # an episode opens only on a restricting claim; exposed and context claims join an open episode
        fams_true = {FAMILY_OF[c] for c in fclaims if CLAIM_DIMENSION[c] == "restricted"}
        fams_open = {fam for (team, fam) in self.open_key if team == ph.defending}
        fams_sup: dict[str, dict[str, FrameClaim]] = {}
        # only stories about screened passing options can take a joining free man / outlet / count; a deeper
        # line does not "create" the space in front of it, that space is always there
        open_eps = sorted((self.episodes[eid] for (team, fam), eid in self.open_key.items()
                           if team == ph.defending and fam in JOIN_TARGETS),
                          key=lambda x: x.last_support_k, reverse=True)
        for code, fc in supported.items():
            fam = FAMILY_OF[code]
            if code in JOINABLE and fam not in {x.family for x in open_eps} and open_eps:
                # a free man, an open outlet or a local count is a consequence of whichever restriction is
                # currently in play, so it joins the open story of the same defending team
                fam = open_eps[0].family
            fams_sup.setdefault(fam, {})[code] = fc
        carrier = self.buf.owner[k]
        ball_uv = self.buf.ball_uv(k, ph.attacking)
        fams_sup = {fam: v for fam, v in fams_sup.items()
                    if fam in fams_open or any(CLAIM_DIMENSION[c] == "restricted" for c in v)}
        for fam in fams_true | set(fams_sup):
            key = (ph.defending, fam)
            if key in self.open_key:  # same family, new place and carrier: the old story is over
                old = self.episodes[self.open_key[key]]
                moved = float(np.linalg.norm(np.array(old.anchor_ball_uv) - ball_uv))
                # so is it once the ball reaches the man the story said was free: what is screened around him now
                # is a new situation, and the old story keeps watching for its outcome after it closes
                used = carrier >= 0 and self.pid(carrier) in old.targets
                if carrier >= 0 and carrier != old.anchor_carrier and (moved > cfg.episodes.new_region_m or used):
                    self._close_one(key, k, "moved_on")
            if key not in self.open_key:
                self._seq += 1
                eid = f"ep_{self.clip_id}_{ph.defending}_{fam}_f{self.fref(k).frame_index:04d}"
                self.episodes[eid] = Episode(eid, fam, ph.defending, ph.attacking, k, last_support_k=k,
                                             anchor_carrier=carrier,
                                             anchor_ball_uv=(float(ball_uv[0]), float(ball_uv[1])))
                self.open_key[key] = eid
        for key, eid in list(self.open_key.items()):
            ep = self.episodes[eid]
            if key[0] != ph.defending:
                continue
            fam = key[1]
            sup = fams_sup.get(fam, {})
            before_codes = ep.codes()
            changed_claims = False
            for code, fc in sup.items():
                cid = f"cl_{eid}_{code}"
                streak_s = self.streak[code] / hz
                claim = to_claim(fc, cid, (max(self.buf.t[0], t - streak_s), t))
                ep.claims[code] = claim
                if CLAIM_DIMENSION[code] == "exposed":
                    ep.exposed_first.setdefault(code, k)
                ep.targets |= set(fc.targets)
                for m in fc.metrics:
                    ep.metrics[m.metric_id] = m
                self.claim_state[cid] = state.state_id
                changed_claims = True
            if sup or fam in fams_true or in_flight:
                ep.last_support_k = k if (sup or in_flight) else ep.last_support_k
            gap_s = (k - ep.last_support_k) / hz
            if ep.lifecycle == "candidate":
                if any(CLAIM_DIMENSION[c] == "restricted" for c in sup):
                    ep.lifecycle = "active"
                    ep.detected_k = k
                    ep.state_ids = [state.state_id]
                    self._baseline(ep, k, r)
                    revised.append(self._revise(ep, k, state))
                    continue
                if fam not in fams_true and (k - ep.start_k) / hz > 0.6:
                    del self.episodes[eid]
                    del self.open_key[key]
                continue
            # active / cooling
            if ep.lifecycle == "active" and gap_s >= cfg.episodes.cooling_s:
                ep.lifecycle = "cooling"
            elif ep.lifecycle == "cooling" and sup:
                ep.lifecycle = "active"
            change = self._detect_change(ep, k, state)
            outcome = self._outcome(ep, k, t)
            new_outcome = outcome["status"] != ep.outcome["status"]
            if new_outcome:
                ep.outcome = outcome
            if gap_s >= cfg.episodes.close_s:
                ep.lifecycle = "closed"
                ep.closed_reason = "conditions_gone"
                outcome = self._outcome(ep, k, t)
                new_outcome = new_outcome or outcome["status"] != ep.outcome["status"]
                ep.outcome = outcome
            if ep.codes() != before_codes or change or new_outcome or ep.lifecycle == "closed":
                revised.append(self._revise(ep, k, state))
            elif changed_claims:
                pass  # same claim set: values refresh in the live state, packet revision unchanged
            if ep.lifecycle == "closed":
                self._start_watch(ep)
                self.closed_log.append((t, ep.defending, ep.family))
                del self.open_key[key]
        return revised

    def _watch_outcomes(self, k: int, t: float, state: TacticalState) -> list[str]:
        """Outcome events can arrive after an episode closed (feed latency, possession change), and so can the
        defence's answer to the exposure it described: the free man is often closed down, or the midfield drops onto
        the man in the gap, only once he has the ball."""
        out = []
        for eid, end in list(self.watch.items()):
            ep = self.episodes[eid]
            if t > end + 1.0:
                del self.watch[eid]
                continue
            revised = False
            if ep.outcome["status"] in ("not_yet_observed", "inconclusive"):
                oc = self._outcome(ep, k, t, final=True)
                if oc["status"] == "exploited_in_synthetic_play":
                    ep.outcome, revised = oc, True
            # the defence's answer only counts while it still defends: after a turnover the state is the other team's
            defending = state.defending_team_id in (None, ep.defending)
            if defending and ep.family == "press_vs_depth" and self._change_step_to_free(ep, k, state):
                revised = True
            # the midfield's answer to a man who got into the gap can come after he has received there
            if (defending and ep.family == "line_separation" and "lane_change" in ep.change_marks
                    and self._change_gap_shift(ep, k, state)):
                revised = True
            if revised:
                out.append(self._revise(ep, k, state))
        return out

    def _exposure_window(self, ep: Episode) -> tuple[float, float]:
        """An exposure can only be used once it was there: each exposed claim gets its outcome window from when it
        was first claimed (or from activation, if it came first). Returns (first opening, last opening)."""
        ks = [max(ep.detected_k, kk) for kk in ep.exposed_first.values()] or [ep.detected_k]
        return self.buf.t[min(ks)], self.buf.t[max(ks)]

    def _start_watch(self, ep: Episode) -> None:
        if ep.detected_k is not None and ep.by_dimension("exposed"):
            self.watch[ep.episode_id] = self._exposure_window(ep)[1] + self.cfg.episodes.outcome_window_s

    def _close_one(self, key: tuple[str, str], k: int, reason: str) -> None:
        eid = self.open_key.pop(key)
        ep = self.episodes[eid]
        if ep.lifecycle == "candidate":
            del self.episodes[eid]
            return
        prev_k = max(k - 1, 0)
        state = self.states[self.state_id(prev_k)]
        ep.lifecycle, ep.closed_reason = "closed", reason
        ep.outcome = self._outcome(ep, prev_k, self.buf.t[prev_k])
        self._side_revised.append(self._revise(ep, prev_k, state))
        self._start_watch(ep)
        self.closed_log.append((self.buf.t[prev_k], ep.defending, ep.family))

    def _close_all(self, k: int, reason: str) -> None:
        """Possession changed at frame k: close open episodes using the last analysed state."""
        prev_k = k - 1
        state = self.states.get(self.state_id(prev_k)) if prev_k >= 0 else None
        for key, eid in list(self.open_key.items()):
            ep = self.episodes[eid]
            if ep.lifecycle == "candidate" or state is None:
                del self.episodes[eid]
                del self.open_key[key]
                continue
            outcome = self._outcome(ep, prev_k, self.buf.t[prev_k], include_k=k)
            ep.outcome = outcome
            ep.lifecycle = "closed"
            ep.closed_reason = reason
            self._side_revised.append(self._revise(ep, prev_k, state, events_until=self.buf.t[prev_k]))
            self._start_watch(ep)
            self.closed_log.append((self.buf.t[prev_k], ep.defending, ep.family))
            del self.open_key[key]

    def _baseline(self, ep: Episode, k: int, r: FrameRelations) -> None:
        ep.passive = not r.presses and not self._closing_on_carrier(k, ep.defending)
        back = r.lines.get("back") if r.lines is not None else None
        if back is not None:
            uv = self.buf.uv(k, ep.attacking)
            ep.baseline = {"k": k, "back_idx": back.idx, "depth": float(np.median(105.0 - uv[list(back.idx), 0])),
                           "state_id": self.state_id(k)}

    def _detect_change(self, ep: Episode, k: int, state: TacticalState) -> bool:
        if ep.family == "press_vs_depth":
            return (self._change_line_step_up(ep, k, state) or self._change_line_retreat(ep, k, state)
                    or self._change_step_to_free(ep, k, state) or self._change_step_out(ep, k, state))
        if ep.family == "central_screen":
            return self._change_drop_step(ep, k, state)
        if ep.family == "line_separation":
            return self._change_gap_shift(ep, k, state)
        return False

    def _record_change(self, ep: Episode, k: int, k0: int, code: str, team: str, actors: list[int], key: str,
                       value: float, state: TacticalState, toward: int | None = None) -> None:
        f = self.fref(k).frame_index
        ids = [self.pid(i) for i in actors]
        # the player a step was made toward is named in the metric (not as an actor of the other team's change)
        m = metric(f"m_{code}_f{f:04d}", key, value, "m", (self.buf.t[k0], self.buf.t[k]),
                   ids + ([self.pid(toward)] if toward is not None else []), [self.fref(k0), self.fref(k)],
                   method="changes")
        ep.change_metrics[m.metric_id] = m
        relation = "observed_next" if not ep.changes else "consistent_with_response"
        ep.changes.append(TacticalChange(change_id=f"ch_{ep.episode_id}_{code}", before_state_id=self.state_id(k0),
                                         after_state_id=state.state_id, actor_team_id=team, actor_player_ids=ids,
                                         code=code, relation=relation, metric_ids=[m.metric_id],
                                         observed_at_s=self.buf.t[k]))
        ep.change_marks[code] = {"k": k, "actors": actors}

    def _change_line_retreat(self, ep: Episode, k: int, state: TacticalState) -> bool:
        if not ep.baseline or "line_retreat" in ep.change_marks:
            return False
        uv = self.buf.uv(k, ep.attacking)
        depth = float(np.median(105.0 - uv[list(ep.baseline["back_idx"]), 0]))
        delta = depth - ep.baseline["depth"]
        if delta > -5.0:
            return False
        self._record_change(ep, k, ep.baseline["k"], "line_retreat", ep.defending, list(ep.baseline["back_idx"]),
                            "behind_depth_change_m", delta, state)
        return True

    def _closing_on_carrier(self, k: int, defending: str) -> bool:
        """A defender within 15 m running at the carrier (>= 2 m/s toward him): a press on its way, not a passive
        screen, even if the pressure gate has not been met yet."""
        c = self.buf.owner[k]
        if c < 0:
            return False
        for d in self.buf.members(defending):
            rel = self.buf.pos[k][c] - self.buf.pos[k][d]
            dist = float(np.linalg.norm(rel))
            if 1e-6 < dist <= 15.0 and float(self.buf.vel[k][d] @ rel) / dist >= 2.0:
                return True
        return False

    def _change_line_step_up(self, ep: Episode, k: int, state: TacticalState) -> bool:
        """The back line steps up together: >= line_step_m higher than its lowest point in the depth-change
        window, with at least three of the same players (a block shift forward, not one defender stepping)."""
        cfg = self.cfg
        if "block_shift" in ep.change_marks:
            return False
        r = self.relations.get(k)
        back = r.lines.get("back") if r is not None and r.lines is not None else None
        if back is None:
            return False
        win_k = int(round(cfg.claims.depth_change_window_s * self.hz))
        earlier = [(kk, idx, d) for kk, idx, d in self.back_hist
                   if k - win_k <= kk < k - 2 and len(set(idx) & set(back.idx)) >= 3 and self.state_id(kk) in self.states]
        if not earlier:
            return False
        k0, _, d0 = min(earlier, key=lambda x: x[2])
        rise = back.depth_m - d0
        if rise < cfg.changes.line_step_m:
            return False
        self._record_change(ep, k, k0, "block_shift", ep.defending, list(back.idx), "back_line_step_m", rise, state)
        return True

    def _window_start(self, k: int) -> int | None:
        k0 = k - int(round(self.cfg.changes.window_s * self.hz))
        return k0 if k0 >= 0 and self.state_id(k0) in self.states else None

    def _change_drop_step(self, ep: Episode, k: int, state: TacticalState) -> bool:
        """Family C: an attacker drops >= 5 m toward the ball, then a defender steps up to him."""
        c = self.cfg.changes
        k0 = self._window_start(k)
        if k0 is None or self.buf.owner[k] < 0:
            return False
        uv, uv0 = self.buf.uv(k, ep.attacking), self.buf.uv(k0, ep.attacking)
        if "attacker_drop" not in ep.change_marks:
            carrier = self.buf.owner[k]
            for a in self.buf.members(ep.attacking):
                du = float(uv[a, 0] - uv0[a, 0])
                near_ball = float(np.linalg.norm(self.buf.pos[k][a] - self.buf.pos[k][carrier])) <= 25.0
                if a != carrier and du <= -c.attacker_move_m and near_ball and abs(uv[a, 1] - 34.0) <= 16.0:
                    self._record_change(ep, k, k0, "attacker_drop", ep.attacking, [int(a)], "attacker_drop_m",
                                        abs(du), state)
                    return True
            return False
        if "defender_step" not in ep.change_marks:
            dropper = ep.change_marks["attacker_drop"]["actors"][0]
            for d in self.buf.members(ep.defending):
                du = float(uv[d, 0] - uv0[d, 0])  # toward the attacker's goal is negative u
                close = float(np.linalg.norm(self.buf.pos[k][d] - self.buf.pos[k][dropper])) <= c.defender_near_m
                if du <= -c.defender_step_m and close:
                    self._record_change(ep, k, k0, "defender_step", ep.defending, [int(d)], "defender_step_m",
                                        abs(du), state, toward=int(dropper))
                    return True
        return False

    def _change_step_out(self, ep: Episode, k: int, state: TacticalState) -> bool:
        """A passive screen (nobody on the carrier when the story began): a defender steps up >= 4 m onto him."""
        c = self.cfg.changes
        if not ep.passive or "defender_step" in ep.change_marks:
            return False
        k0 = self._window_start(k)
        carrier = self.buf.owner[k]
        if k0 is None or carrier < 0 or self.buf.team[carrier] != ep.attacking:
            return False
        uv, uv0 = self.buf.uv(k, ep.attacking), self.buf.uv(k0, ep.attacking)
        for d in self.buf.members(ep.defending):
            du = float(uv[d, 0] - uv0[d, 0])  # toward the attacker's goal is negative u
            close = float(np.linalg.norm(self.buf.pos[k][d] - self.buf.pos[k][carrier])) <= c.defender_near_m
            if du <= -c.defender_step_m and close:
                self._record_change(ep, k, k0, "defender_step", ep.defending, [int(d)], "defender_step_m", abs(du),
                                    state, toward=int(carrier))
                return True
        return False

    def _change_step_to_free(self, ep: Episode, k: int, state: TacticalState) -> bool:
        """After a free receiver was claimed, a defender moves >= 4 m and closes to within 6 m of him."""
        c = self.cfg.changes
        if "defender_step" in ep.change_marks or "accessible_free_receiver" not in ep.claims or not ep.targets:
            return False
        k0 = self._window_start(k)
        if k0 is None:
            return False
        targets = [self.buf.idx(p) for p in ep.targets if p in self.buf._index]
        for d in self.buf.members(ep.defending):
            moved = float(np.linalg.norm(self.buf.pos[k][d] - self.buf.pos[k0][d]))
            if moved < c.defender_step_m:
                continue
            for a in targets:
                if float(np.linalg.norm(self.buf.pos[k][d] - self.buf.pos[k][a])) <= c.defender_near_m:
                    self._record_change(ep, k, k0, "defender_step", ep.defending, [int(d)], "defender_step_m", moved,
                                        state, toward=int(a))
                    return True
        return False

    def _change_gap_shift(self, ep: Episode, k: int, state: TacticalState) -> bool:
        """Family B: an attacker moves >= 5 m forward into the gap, then the defending midfield drops."""
        c = self.cfg.changes
        k0 = self._window_start(k)
        r = self.relations.get(k)
        if k0 is None or r is None or r.lines is None:
            return False
        back, mid = r.lines.get("back"), r.lines.get("mid")
        uv, uv0 = self.buf.uv(k, ep.attacking), self.buf.uv(k0, ep.attacking)
        if "lane_change" not in ep.change_marks:
            if back is None or mid is None:
                return False
            u_back, u_mid = 105.0 - back.depth_m, 105.0 - mid.depth_m
            for a in self.buf.members(ep.attacking):
                du = float(uv[a, 0] - uv0[a, 0])
                if du >= c.attacker_move_m and u_mid < uv[a, 0] < u_back:
                    self._record_change(ep, k, k0, "lane_change", ep.attacking, [int(a)], "attacker_move_m", du,
                                        state)
                    ep.change_marks["lane_change"]["mid_idx"] = mid.idx
                    return True
            return False
        if "block_shift" not in ep.change_marks:
            mid_idx = list(ep.change_marks["lane_change"]["mid_idx"])
            d_now = float(np.median(105.0 - uv[mid_idx, 0]))
            d_then = float(np.median(105.0 - uv0[mid_idx, 0]))
            if d_now - d_then <= -c.mid_shift_m:
                self._record_change(ep, k, k0, "block_shift", ep.defending, mid_idx, "mid_line_shift_m",
                                    abs(d_now - d_then), state)
                return True
        return False

    def _outcome(self, ep: Episode, k: int, t: float, include_k: int | None = None, final: bool = False) -> dict:
        if ep.detected_k is None:
            return ep.outcome
        if not ep.by_dimension("exposed"):
            # nothing exposed was claimed, so there is no exploitation to observe
            closing = include_k is not None or ep.lifecycle == "closed"
            return {"status": "inconclusive" if closing else "not_yet_observed", "event_ids": [],
                    "realized_target_id": None}
        events = self.buf.events_until(self.buf.t[include_k] if include_k is not None else t)
        line_u = 105.0 - ep.baseline.get("depth", 0.0) if ep.baseline else 105.0

        def used(e: MatchEvent) -> bool:
            if e.target_id in ep.targets:  # the ball reached a receiver named by an exposed claim
                return True
            if ep.family != "press_vs_depth" or "behind_space_exposed" not in ep.claims or e.position is None:
                return False
            u = to_attack_frame(np.array([e.position.x_m, e.position.y_m]), self.buf.direction(ep.attacking))[0]
            return bool(u >= line_u - 1.0) and self.buf.team[self.buf.idx(e.target_id)] == ep.attacking
        first_s, last_s = self._exposure_window(ep)
        return outcome_from_events(ep, events, first_s, t, self.clip_end_s, used, self.cfg, last_opened_s=last_s)

    def _revise(self, ep: Episode, k: int, state: TacticalState, events_until: float | None = None) -> str:
        ep.revision += 1
        t = self.buf.t[k]
        # an outcome revision can come after possession changed: the current state then describes the other team
        # defending and is not part of this episode (a packet may not mix defending teams)
        if state.state_id not in ep.state_ids and state.defending_team_id in (None, ep.defending):
            ep.state_ids.append(state.state_id)
        ball = self.buf.ball[k]
        pts = [self.buf.pos[k][self.buf.idx(p)] for c in ep.claims.values() for p in c.player_ids]
        # ball relevance: how close the nearest player named in the claims is to the ball
        rel = float(np.clip(1.0 - min(np.linalg.norm(q - ball) for q in pts) / 30.0, 0.0, 1.0)) if pts else 0.0
        novelty = 0.3 if any(t0 >= self.buf.t[ep.detected_k] - self.cfg.episodes.novelty_window_s and d == ep.defending
                             and fam == ep.family for t0, d, fam in self.closed_log) else 1.0
        ep.story_priority = story_priority(ep, rel, novelty, self.cfg)
        packet = self._freeze(ep, k, state, events_until if events_until is not None else t)
        self.packets.setdefault(ep.episode_id, []).append(packet)
        return ep.episode_id

    def _freeze(self, ep: Episode, k: int, state: TacticalState, events_until: float) -> EvidencePacket:
        t = self.buf.t[k]
        state_ids = list(dict.fromkeys(ep.state_ids + [self.claim_state[c.claim_id] for c in ep.claims.values()]
                                       + [s for ch in ep.changes for s in (ch.before_state_id, ch.after_state_id)]))
        states = [self.states[s] for s in state_ids]
        claims = sorted(ep.claims.values(), key=lambda c: c.claim_id)
        metric_ids = {m for c in claims for m in c.metric_ids} | {m for ch in ep.changes for m in ch.metric_ids}
        metrics = sorted([m for mid, m in {**ep.metrics, **ep.change_metrics}.items() if mid in metric_ids],
                         key=lambda m: m.metric_id)
        events = [e for e in self.buf.events_until(events_until) if e.event_id in set(ep.outcome["event_ids"])]
        episode = TradeoffEpisode(
            episode_id=ep.episode_id, revision=ep.revision, match_id=self.meta.match_id, clip_id=self.clip_id,
            lifecycle=ep.lifecycle, analysis_status=ep.analysis_status(), defending_team_id=ep.defending,
            analyzed_until_s=t, detected_at_s=self.buf.t[ep.detected_k],
            window=Window(start_s=min(c.window.start_s for c in claims) if claims else t, end_s=t),
            state_ids=list(dict.fromkeys(ep.state_ids)), restricted_claim_ids=ep.by_dimension("restricted"),
            exposed_claim_ids=ep.by_dimension("exposed"), context_claim_ids=ep.by_dimension("context"),
            change_ids=[c.change_id for c in ep.changes], story_priority=ep.story_priority,
            outcome=Outcome(status=ep.outcome["status"], observed_until_s=t, event_ids=ep.outcome["event_ids"],
                            realized_target_id=ep.outcome["realized_target_id"]))
        forbidden = sorted(set(FORBIDDEN_ALWAYS) | ep.suppressed)
        h = compute_evidence_hash(episode, states, metrics, claims, ep.changes, events, forbidden)
        return EvidencePacket(schema_version="2.0", evidence_hash=h, episode=episode, states=states, metrics=metrics,
                              claims=claims, changes=list(ep.changes), events=events, forbidden_inferences=forbidden)

    # ------------------------------------------------------------------ queries
    def latest_packet(self, episode_id: str) -> EvidencePacket | None:  # noqa: D102
        revs = self.packets.get(episode_id)
        return revs[-1] if revs else None


def _xy(p) -> XY:
    return XY(x_m=round(float(p[0]), 2), y_m=round(float(p[1]), 2))


def offside_line(buf: ObservationBuffer, k: int, attacking: str) -> float:
    return offside_line_u(buf.uv(k, attacking), buf, buf.other(attacking), float(buf.ball_uv(k, attacking)[0]))
