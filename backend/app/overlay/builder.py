"""Deterministic overlay builder (spec 10): coordinates come from code, never from a model.

``build_overlay`` draws one episode on one frame. Geometry is taken from the
TacticalState of that frame; labels only use metrics measured on that same
frame (live) or the packet metrics of the frozen frame (retrospective
replay), so an old count is never pinned onto current positions. Presets cap
the number of elements and labels; ``focus`` restricts the drawing to the
restricted side, the exposed side, or both.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..analytics.config import Config, load_config
from ..analytics.observe import to_attack_frame, to_world
from ..contracts import (
    XY,
    Arrow,
    Connection,
    EvidenceMetric,
    EvidencePacket,
    Label,
    MatchMeta,
    OverlayElement,
    OverlaySnapshot,
    PlayerAnchor,
    Polygon,
    Polyline,
    Ring,
    TacticalState,
    WorldAnchor,
    player_anchor_ids,
)
from ..narrative.render import render_label

LABEL_PRIORITY = {"label.short_screened": 0, "label.central_screened": 0, "label.depth_reduced": 0,
                  "label.cover_margin": 1, "label.cover_in_time": 1, "label.mid_back_gap": 1, "label.free_receiver": 1,
                  "label.wide_outlet": 2, "label.back_depth": 2, "label.press_cut": 2, "label.passive_screen": 2,
                  # an observed change is what a later revision is about: it outranks the context counts
                  "label.line_retreat": 3, "label.change.attacker_drop": 3, "label.change.defender_step": 3,
                  "label.change.lane_change": 3, "label.change.block_shift": 3, "label.change.line_step_up": 3,
                  "label.count": 4, "label.press_numbers": 4,
                  "label.presser": 5}


@dataclass
class OverlayResult:
    snapshot: OverlaySnapshot
    label_text: dict[str, str]
    hidden_labels: list[str]


def P(pid: str) -> PlayerAnchor:
    return PlayerAnchor(kind="player", player_id=pid)


def W(x: float, y: float) -> WorldAnchor:
    return WorldAnchor(kind="world", point=XY(x_m=round(float(np.clip(x, 0, 105)), 2),
                                              y_m=round(float(np.clip(y, 0, 68)), 2)))


def _xy(x: float, y: float) -> XY:
    return XY(x_m=round(float(np.clip(x, 0, 105)), 2), y_m=round(float(np.clip(y, 0, 68)), 2))


# Fan preset (spec 10.2, as amended 2026-10-24): the cap counts display units, not shapes. Shapes that say one thing
# together are one unit: the screened lanes of a press, the presser's arrow and the carrier's ring, the exposed space
# and its runner, the open outlet and its target, the late cover and its link. A complete trade-off keeps one unit of
# each side, so a fan never sees only what was taken away or only what was left open.
FAN_UNIT = {"lane_screened": "lanes", "press_cut_lane": "lanes", "press_arrow": "press", "carrier_ring": "press",
            "exposure_hatched": "space", "runner_ring": "space", "lane_open": "outlet", "outlet_target": "outlet",
            "free_receiver_ring": "outlet", "cover_late": "cover", "cover_link": "cover", "cover_in_time": "cover"}
FAN_ORDER = {"space": 0, "lanes": 1, "outlet": 2, "press": 3, "cover": 4}
RESTRICTED_UNITS = {"lanes", "press"}
EXPOSED_UNITS = {"space", "outlet", "cover"}
_LAYER_ORDER = {"exposure": 5, "pressure": 6, "screened_lanes": 7, "cover": 8, "structure": 9, "changes": 10,
                "marking": 11}


def display_units(els) -> list[list]:
    """Elements grouped into display units, in drawing order: each label is its own unit."""
    units: dict[str, list] = {}
    for e in els:
        key = e.element_id if e.geometry.type == "label" else FAN_UNIT.get(e.style_token, e.element_id)
        units.setdefault(key, []).append(e)
    return list(units.values())


def _unit_key(group) -> str:
    e = group[0]
    return "label" if e.geometry.type == "label" else FAN_UNIT.get(e.style_token, e.element_id)


def _fan_rank(group) -> float:
    key = _unit_key(group)
    if key in FAN_ORDER:
        return FAN_ORDER[key]
    # a screening defender's ring repeats what its lane already shows
    return _LAYER_ORDER.get(group[0].layer, 12) + (0.5 if group[0].style_token == "screen_marker" else 0.0)


def fan_selection(els, max_units: int, both_sides: bool) -> list:
    """At most ``max_units`` display units: labels first, then the most telling units, with one restricted and one
    exposed unit kept for a complete trade-off whenever the overlay has them."""
    groups = display_units(els)
    labels = [g for g in groups if _unit_key(g) == "label"]
    rest = sorted((g for g in groups if _unit_key(g) != "label"), key=_fan_rank)
    budget = max(0, max_units - len(labels))
    chosen = rest[:budget]
    for side, other in ((RESTRICTED_UNITS, EXPOSED_UNITS), (EXPOSED_UNITS, RESTRICTED_UNITS)):
        if not both_sides or budget == 0 or any(_unit_key(g) in side for g in chosen):
            continue
        best = next((g for g in rest if _unit_key(g) in side), None)
        if best is None:
            continue
        if len(chosen) < budget:
            chosen.append(best)
            continue
        # give up the least telling unit, unless it is the other side's only one
        for k in range(len(chosen) - 1, -1, -1):
            if not (_unit_key(chosen[k]) in other and sum(_unit_key(g) in other for g in chosen) == 1):
                chosen[k] = best
                break
    keep = {e.element_id for g in labels + chosen for e in g}
    return [e for e in els if e.element_id in keep]


def build_overlay(*, state: TacticalState, packet: EvidencePacket, frame_players: dict[str, tuple[float, float]],
                  meta: MatchMeta, replay_id: str, epoch: int, audience: str, language: str, surface: str,
                  display_mode: str, focus: str = "both", metrics_now: list[EvidenceMetric] | None = None,
                  live_codes: set[str] | None = None, positions_at=None, cfg: Config | None = None) -> OverlayResult:
    cfg = cfg or load_config()
    ep = packet.episode
    claims = {c.code: c for c in packet.claims}
    if live_codes is not None:  # live: only what holds on this frame, never an earlier claim on today's positions
        claims = {k: c for k, c in claims.items() if k in live_codes}
    fidx = state.frame_ref.frame_index
    # metrics usable for labels: measured on this frame only
    if metrics_now is not None:
        same = {m.key: m for m in metrics_now}
    else:
        same = {m.key: m for m in packet.metrics if any(fr.frame_index == fidx for fr in m.frame_refs)}
    for m in packet.metrics:  # changes are reported with the window they span
        if m.key == "behind_depth_change_m" and any(fr.frame_index <= fidx for fr in m.frame_refs[-1:]):
            same.setdefault(m.key, m)
    els: list[OverlayElement] = []
    labels: list[tuple[int, OverlayElement]] = []

    def add(layer: str, geom, style: str, claim_codes=(), metric_ids=(), relation_ids=()):
        cids = [claims[c].claim_id for c in claim_codes if c in claims]
        e = OverlayElement(element_id=f"el{len(els):02d}_{layer}", layer=layer, claim_ids=cids,
                           metric_ids=list(metric_ids), relation_ids=list(relation_ids), geometry=geom,
                           style_token=style)
        els.append(e)
        return e

    def add_label(layer: str, at, key: str, metric_keys, claim_codes=()):
        if not all(k in same for k in metric_keys):
            return
        ms = [same[k] for k in metric_keys]
        e = add(layer, Label(type="label", at=at, template_key=key, metric_ids=[m.metric_id for m in ms]),
                "count_label" if layer in ("screened_lanes", "numbers") else "line_label", claim_codes,
                [m.metric_id for m in ms])
        labels.append((LABEL_PRIORITY.get(key, 9), e))

    restricted = focus in ("both", "restricted")
    exposed = focus in ("both", "exposed")
    attacking = state.attacking_team_id
    direction = meta.attack_direction(attacking) if attacking else 1

    presser = state.pressures[0].presser_id if state.pressures else None

    def lane_style(ln) -> str:
        """A lane the primary presser stands in himself: pressing while cutting it (spec 8.1 shadow)."""
        return "press_cut_lane" if presser is not None and presser in ln.screening_player_ids else "lane_screened"

    def press_labels(code: str, carrier: str | None) -> None:
        if presser is not None:
            add_label("screened_lanes", P(presser), "label.press_cut", ("press_cut_lanes",), [code])
        if carrier in frame_players and audience == "analyst":
            # numbers around and ahead of the ball, written a few metres behind the carrier
            uv = to_attack_frame(np.array(frame_players[carrier]), direction) - np.array([6.0, 0.0])
            x, y = to_world(uv[None, :], direction)[0]
            add_label("numbers", W(x, y), "label.press_numbers", ("buildup_count_defense", "buildup_count_attack",
                                                                   "rest_count_defense", "rest_count_attack"), [code])

    # ------------------------------------------------ restricted side
    if restricted and "short_options_screened" in claims:
        c = claims["short_options_screened"]
        for pr in state.pressures[:1]:
            add("pressure", Arrow(type="arrow", from_=P(pr.presser_id), to=P(pr.carrier_id), meaning="observed"),
                "press_arrow", ["short_options_screened"], relation_ids=[pr.relation_id])
            add("pressure", Ring(type="ring", center=P(pr.carrier_id), radius_m=2.2), "carrier_ring",
                ["short_options_screened"], relation_ids=[pr.relation_id])
        scr = [ln for ln in state.lanes if ln.availability == "screened" and ln.eligibility == "onside_geometry"]
        for ln in scr:
            add("screened_lanes", Connection(type="connection", from_=P(ln.carrier_id), to=P(ln.receiver_id)),
                lane_style(ln), ["short_options_screened"], relation_ids=[ln.relation_id])
        if audience == "analyst":
            for ln in scr:
                for s in ln.screening_player_ids[:1]:
                    add("screened_lanes", Ring(type="ring", center=P(s), radius_m=1.4), "screen_marker",
                        ["short_options_screened"], relation_ids=[ln.relation_id])
            for mk in state.markings:
                if mk.status == "ok":
                    add("marking", Connection(type="connection", from_=P(mk.defender_id), to=P(mk.attacker_id)),
                        "mark_supported", relation_ids=[mk.relation_id])
        carrier = state.pressures[0].carrier_id if state.pressures else (scr[0].carrier_id if scr else None)
        if carrier:
            add_label("screened_lanes", P(carrier), "label.short_screened",
                      ("short_options_screened", "short_option_candidates"), ["short_options_screened"])
        room = same.get("carrier_nearest_defender_m")
        if carrier and not state.pressures and room is not None and room.value is not None and room.value >= 5.0:
            # passive screen: nobody on the carrier, the restriction comes from where the others stand
            add("pressure", Ring(type="ring", center=P(carrier), radius_m=round(min(room.value, 12.0), 2)),
                "carrier_space_ring", ["short_options_screened"], [room.metric_id])
            add_label("pressure", P(carrier), "label.passive_screen", ("carrier_nearest_defender_m",),
                      ["short_options_screened"])
        press_labels("short_options_screened", carrier)
    # ------------------------------------------------ exposed side
    if exposed and ("behind_space_exposed" in claims or "isolated_1v1" in claims):
        back = next((g for g in state.line_groups if g.group == "back"), None)
        runner = None
        runner_metric = packet.metric(next(m for m in claims.get("behind_space_exposed", claims.get(
            "isolated_1v1")).metric_ids if packet.metric(m).key in ("runner_forward_speed_mps",)))  # noqa: E501
        runner = runner_metric.player_ids[0]
        if back is not None and audience == "analyst":
            pts = sorted((frame_players[p] for p in back.player_ids), key=lambda q: q[1])
            add("structure", Polyline(type="polyline", points=[_xy(*q) for q in pts]), "line_back",
                ["behind_space_exposed"])
            add_label("structure", P(back.player_ids[len(back.player_ids) // 2]), "label.back_depth",
                      ("back_line_depth_m",), ["behind_space_exposed"])
        if back is not None and runner in frame_players:
            line_u = 105.0 - back.own_goal_depth_m
            rv = to_attack_frame(np.array(frame_players[runner]), direction)[1]
            u0, u1 = line_u, min(line_u + 20.0, 104.0)
            poly_uv = np.array([(u0, rv - 8.0), (u1, rv - 8.0), (u1, rv + 8.0), (u0, rv + 8.0)])
            poly_uv[:, 1] = np.clip(poly_uv[:, 1], 0.0, 68.0)
            poly = to_world(poly_uv, direction)
            add("exposure", Polygon(type="polygon", points=[_xy(x, y) for x, y in poly]), "exposure_hatched",
                ["behind_space_exposed", "isolated_1v1"],
                [m for m in claims.get("behind_space_exposed", claims.get("isolated_1v1")).metric_ids])
        if runner in frame_players:
            add("exposure", Ring(type="ring", center=P(runner), radius_m=2.0), "runner_ring",
                ["behind_space_exposed", "isolated_1v1"])
        cov = next((cv for cv in state.covers if cv.attacker_id == runner and cv.relation_id.split("_")[-2] == "out"),
                   None)
        if cov is not None and cov.status == "ok" and cov.cover_player_id:
            late = cov.cover_margin_s is not None and cov.cover_margin_s > cfg.cover.late_cover_margin_s
            add("cover", Connection(type="connection", from_=P(cov.cover_player_id),
                                    to=W(cov.target.x_m, cov.target.y_m)),
                "cover_late" if late else "cover_in_time", ["behind_space_exposed"], relation_ids=[cov.relation_id])
            add_label("cover", W(cov.target.x_m, cov.target.y_m), "label.cover_margin" if late else
                      "label.cover_in_time", ("cover_margin_s",), ["behind_space_exposed"])
            if runner in frame_players:
                add("cover", Arrow(type="arrow", from_=P(runner), to=W(cov.target.x_m, cov.target.y_m),
                                   meaning="inferred"), "cover_link", ["behind_space_exposed"],
                    relation_ids=[cov.relation_id])
        if audience == "analyst" and "isolated_1v1" in claims:
            mk = next((m for m in state.markings if m.attacker_id == runner), None)
            if mk is not None:
                add("marking", Connection(type="connection", from_=P(mk.defender_id), to=P(mk.attacker_id)),
                    "mark_supported", ["isolated_1v1"], relation_ids=[mk.relation_id])
    lanes_by_recv = {ln.receiver_id: ln for ln in state.lanes}

    def lanes_for(code: str, availability: str):
        """Lanes named by the claim (exact ids on its own frame, same receivers on a live frame)."""
        c = claims[code]
        exact = [ln for ln in state.lanes if ln.relation_id in c.relation_ids]
        if exact:
            return exact
        return [ln for ln in state.lanes if ln.receiver_id in c.player_ids and ln.availability == availability]

    # ------------------------------------------------ family C: central screen, wide outlet, free receiver, numbers
    if restricted and "central_routes_screened" in claims:
        for pr in state.pressures[:1]:
            add("pressure", Arrow(type="arrow", from_=P(pr.presser_id), to=P(pr.carrier_id), meaning="observed"),
                "press_arrow", ["central_routes_screened"], relation_ids=[pr.relation_id])
            add("pressure", Ring(type="ring", center=P(pr.carrier_id), radius_m=2.2), "carrier_ring",
                ["central_routes_screened"], relation_ids=[pr.relation_id])
        scr = [ln for ln in lanes_for("central_routes_screened", "screened") if ln.availability == "screened"]
        for ln in scr:
            add("screened_lanes", Connection(type="connection", from_=P(ln.carrier_id), to=P(ln.receiver_id)),
                lane_style(ln), ["central_routes_screened"], relation_ids=[ln.relation_id])
        if audience == "analyst":
            for ln in scr:
                for sp in ln.screening_player_ids[:1]:
                    add("screened_lanes", Ring(type="ring", center=P(sp), radius_m=1.4), "screen_marker",
                        ["central_routes_screened"], relation_ids=[ln.relation_id])
        if scr:
            add_label("screened_lanes", P(scr[0].carrier_id), "label.central_screened",
                      ("central_screened", "central_candidates"), ["central_routes_screened"])
        press_labels("central_routes_screened", scr[0].carrier_id if scr else None)
    if exposed:
        for code, style, label in (("wide_outlet_available", "outlet_target", "label.wide_outlet"),
                                   ("accessible_free_receiver", "free_receiver_ring", "label.free_receiver")):
            if code not in claims:
                continue
            opened = [ln for ln in lanes_for(code, "open") if ln.availability == "open"]
            for ln in opened[:2]:
                add("outlets", Arrow(type="arrow", from_=P(ln.carrier_id), to=P(ln.receiver_id), meaning="inferred"),
                    "lane_open", [code], relation_ids=[ln.relation_id])
                add("outlets", Ring(type="ring", center=P(ln.receiver_id), radius_m=2.0), style, [code],
                    relation_ids=[ln.relation_id])
            if opened:
                add_label("outlets", P(opened[0].receiver_id), label,
                          ("lane_arrival_margin_s",) if code == "wide_outlet_available" else ("receiver_margin_s",),
                          [code])
    if "local_central_overload" in claims and state.regions and audience == "analyst":
        rg = state.regions[0]
        add("numbers", Polygon(type="polygon", points=list(rg.polygon)), "region_outline", ["local_central_overload"],
            relation_ids=[rg.region_id])
        for pid_ in rg.attacking_player_ids + rg.defending_player_ids:
            add("numbers", Ring(type="ring", center=P(pid_), radius_m=1.7), "player_count_ring",
                ["local_central_overload"], relation_ids=[rg.region_id])
        cx = sum(p.x_m for p in rg.polygon) / len(rg.polygon)
        top = max(p.y_m for p in rg.polygon)
        add_label("numbers", W(cx, min(top + 2.5, 66.0)), "label.count", ("central_count_attack",
                                                                          "central_count_defense"),
                  ["local_central_overload"])
    # ------------------------------------------------ family B: lines, gap, receiver between the lines
    b_codes = [c for c in ("behind_depth_reduced", "press_block_disconnected", "between_lines_receiver") if c in claims]
    if b_codes:
        groups = {g.group: g for g in state.line_groups}
        style = {"back": "line_back", "mid": "line_mid", "front": "line_front"}
        for name, g in groups.items():
            if audience == "fan" and name != "back":
                continue
            pts = sorted((frame_players[p] for p in g.player_ids if p in frame_players), key=lambda q: q[1])
            if len(pts) >= 2:
                add("structure", Polyline(type="polyline", points=[_xy(*q) for q in pts]), style[name], b_codes)
        if restricted and "behind_depth_reduced" in claims and "back" in groups:
            add_label("structure", P(groups["back"].player_ids[len(groups["back"].player_ids) // 2]),
                      "label.depth_reduced", ("behind_depth_change_m",), ["behind_depth_reduced"])
        if exposed and "back" in groups and "mid" in groups and (
                "press_block_disconnected" in claims or "between_lines_receiver" in claims):
            u_back, u_mid = 105.0 - groups["back"].own_goal_depth_m, 105.0 - groups["mid"].own_goal_depth_m
            recv = None
            if "between_lines_receiver" in claims:
                m = next((packet.metric(x) for x in claims["between_lines_receiver"].metric_ids
                          if packet.metric(x).key == "receiver_margin_s"), None)
                recv = m.player_ids[0] if m is not None else None
            if recv in frame_players:
                rv = to_attack_frame(np.array(frame_players[recv]), direction)[1]
                v0, v1 = max(rv - 9.0, 0.0), min(rv + 9.0, 68.0)
            else:
                v0, v1 = 6.0, 62.0
            poly = to_world(np.array([(u_mid, v0), (u_back, v0), (u_back, v1), (u_mid, v1)]), direction)
            add("exposure", Polygon(type="polygon", points=[_xy(x, y) for x, y in poly]), "exposure_hatched",
                [c for c in ("press_block_disconnected", "between_lines_receiver") if c in claims])
            mid_anchor = to_world(np.array([(u_mid + u_back) / 2.0, v1 + 2.0 if v1 < 64 else v0 - 2.0]), direction)
            add_label("exposure", W(*mid_anchor), "label.mid_back_gap", ("mid_back_gap_m",),
                      ["press_block_disconnected"])
            if recv in frame_players:
                add("exposure", Ring(type="ring", center=P(recv), radius_m=2.0), "free_receiver_ring",
                    ["between_lines_receiver"])
                ln = lanes_by_recv.get(recv)
                if ln is not None and ln.availability == "open":
                    add("outlets", Arrow(type="arrow", from_=P(ln.carrier_id), to=P(recv), meaning="inferred"),
                        "lane_open", ["between_lines_receiver"], relation_ids=[ln.relation_id])
        if exposed and "press_block_disconnected" in claims:
            for pr in state.pressures[:1]:
                add("pressure", Arrow(type="arrow", from_=P(pr.presser_id), to=P(pr.carrier_id), meaning="observed"),
                    "press_arrow", ["press_block_disconnected"], relation_ids=[pr.relation_id])
    # ------------------------------------------------ changes: where the actors moved (frames before -> after)
    if packet.changes and audience == "analyst":
        for ch in packet.changes:
            if ch.code == "line_retreat":
                anchor = ch.actor_player_ids[len(ch.actor_player_ids) // 2]
                if anchor in frame_players:
                    add_label("changes", P(anchor), "label.line_retreat", ("behind_depth_change_m",))
                continue
            if positions_at is None:
                continue
            m = packet.metric(ch.metric_ids[0]) if ch.metric_ids else None
            f0, f1 = (m.frame_refs[0].frame_index, m.frame_refs[-1].frame_index) if m else (None, None)
            if f1 is None or f1 > fidx:
                continue  # never draw a movement that has not happened on this frame yet
            before, after = positions_at(f0), positions_at(f1)
            for pid_ in ch.actor_player_ids[:4]:
                if pid_ in before and pid_ in after:
                    add("changes", Polyline(type="polyline", points=[_xy(*before[pid_]), _xy(*after[pid_])]),
                        "ghost_path", [])
            if ch.actor_player_ids and ch.actor_player_ids[0] in frame_players and m is not None:
                same.setdefault(m.key, m)
                key = "label.change.line_step_up" if m.key == "back_line_step_m" else f"label.change.{ch.code}"
                add_label("changes", P(ch.actor_player_ids[0]), key, (m.key,))
    # ------------------------------------------------ presets: element and label caps
    max_el = cfg.overlay.fan_max_elements if audience == "fan" else cfg.overlay.analyst_max_elements
    max_lb = cfg.overlay.fan_max_labels if audience == "fan" else cfg.overlay.analyst_max_labels
    keep_labels = {e.element_id for _, e in sorted(labels, key=lambda x: x[0])[:max_lb]}
    hidden = [e.element_id for _, e in labels if e.element_id not in keep_labels]
    els = [e for e in els if e.geometry.type != "label" or e.element_id in keep_labels]
    els = [e for e in els if all(p in frame_players for p in player_anchor_ids(e.geometry))]
    if audience == "fan":
        els = fan_selection(els, max_el, both_sides=ep.analysis_status == "complete_tradeoff")
    elif len(els) > max_el:  # keep the most informative layers first
        order = {"exposure": 0, "pressure": 1, "screened_lanes": 2, "cover": 3, "structure": 4, "changes": 5,
                 "marking": 6}
        # a screening defender's ring repeats what its lane already shows, so it gives way to observed movement
        rank = {"screen_marker": order["changes"] + 0.5}
        els = sorted(els, key=lambda e: (e.geometry.type != "label",
                                         rank.get(e.style_token, order.get(e.layer, 9))))[:max_el]
    label_text = {}
    for e in els:
        if e.geometry.type == "label":
            ms = [m for m in same.values() if m.metric_id in e.geometry.metric_ids]
            label_text[e.element_id] = render_label(e.geometry.template_key, ms, packet, language, meta)
    ttl = cfg.overlay.live_ttl_s if display_mode == "live" else 3.0
    snap = OverlaySnapshot(schema_version="2.0",
                           overlay_id=f"ov_{ep.episode_id}_r{ep.revision}_f{fidx:04d}_{audience}_{language}_{focus}",
                           replay_id=replay_id, analysis_epoch=epoch, episode_id=ep.episode_id,
                           episode_revision=ep.revision, evidence_hash=packet.evidence_hash,
                           frame_ref=state.frame_ref, match_time_s=state.analyzed_until_s,
                           valid_until_s=round(state.analyzed_until_s + ttl, 3), display_mode=display_mode,
                           surface=surface, audience=audience, language=language, elements=els)
    return OverlayResult(snap, label_text, hidden)


def key_state(packet: EvidencePacket) -> TacticalState:
    """The frozen state on which the packet's latest claim instance was measured (used for replay overlays)."""
    t = max(c.window.end_s for c in packet.claims)
    return min(packet.states, key=lambda s: (abs(s.analyzed_until_s - t), -s.analyzed_until_s))


def validate_overlay(snapshot: OverlaySnapshot, frame_players: dict[str, tuple[float, float]],
                     packet: EvidencePacket) -> None:
    """Spec 11.1: anchors exist on the frame, ids exist in the packet, counts stay inside presets."""
    claim_ids = {c.claim_id for c in packet.claims}
    rel_ids = {r for s in packet.states for r in s.relation_ids()} | {g.region_id for s in packet.states
                                                                      for g in s.regions}
    if snapshot.evidence_hash != packet.evidence_hash or snapshot.episode_revision != packet.episode.revision:
        raise ValueError("overlay refers to another evidence revision")
    for e in snapshot.elements:
        for p in player_anchor_ids(e.geometry):
            if p not in frame_players:
                raise ValueError(f"{e.element_id}: player {p} is not on the frame")
        if set(e.claim_ids) - claim_ids:
            raise ValueError(f"{e.element_id}: unknown claim")
        if e.layer != "changes" and set(e.relation_ids) - rel_ids and snapshot.display_mode == "retrospective_replay":
            raise ValueError(f"{e.element_id}: relation not in the packet")
