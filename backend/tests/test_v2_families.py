"""Families B to G, their counter-examples, and checks that run over every committed v2 clip."""

from __future__ import annotations

import json

import pytest

from app.agents.fallback import analyst_plan, producer_plan
from app.agents.validate import PlanError, validate_analyst_plan, validate_producer_plan
from app.contracts import MatchMeta, SentencePlan
from app.overlay.builder import (EXPOSED_UNITS, FAN_UNIT, RESTRICTED_UNITS, build_overlay, display_units, key_state,
                                 validate_overlay)

from .v2_helpers import PUBLIC, TRUTH, load_public, run_analyzer

CLIPS = ("clip_001", "clip_002", "clip_003", "clip_004", "clip_005", "clip_006", "clip_007", "clip_008", "clip_009",
         "clip_010", "clip_011", "clip_012")


@pytest.fixture(scope="module")
def analyzed():
    out = {}
    for cid in CLIPS:
        meta, frames, events = load_public(PUBLIC / cid)
        out[cid] = (meta, frames, events, run_analyzer(meta, frames, events))
    return out


def truth(cid: str) -> dict:
    return json.loads((TRUTH / f"{cid}.json").read_text())


def family_revs(an, family: str):
    return [revs for eid, revs in an.packets.items() if f"_{family}_" in eid]


def codes(p) -> set[str]:
    return {c.code for c in p.claims}


@pytest.mark.parametrize("cid", CLIPS)
def test_expected_claims_per_clip(analyzed, cid):
    """Offline check against the generator's private intent (evaluation-style; never fed back to analytics)."""
    an = analyzed[cid][3]
    exp = truth(cid)["expected"]
    families = {"A": ("press_vs_depth",), "B": ("line_separation",), "C": ("central_screen",),
                "D": ("press_vs_depth", "central_screen"), "E": ("press_vs_depth",),
                "G": ("press_vs_depth",)}[truth(cid)["family"]]
    # the designed story's defending team only: after a turnover the other team defends, which is another story
    team = truth(cid)["defending_team_id"]
    packets = [p for fam in families for revs in family_revs(an, fam) for p in revs
               if p.episode.defending_team_id == team]
    seen = set().union(*(codes(p) for p in packets)) if packets else set()
    assert set(exp["restricted"]) <= seen, (cid, seen)
    assert set(exp["exposed"]) <= seen, (cid, seen)
    assert not set(exp.get("suppressed", [])) & seen, (cid, seen)
    complete = any(p.episode.analysis_status == "complete_tradeoff" for p in packets)
    assert complete == bool(exp["exposed"]), cid


def test_d_lane_cut_press_holds_or_is_escaped(analyzed):
    """D (four-player press with lane cuts): the trap on the left centre-back holds in main, is escaped via the
    goalkeeper and the free central centre-back in the other variant."""
    main = analyzed["clip_007"][3]
    trap = [p for revs in main.packets.values() for p in revs
            if codes(p) & {"short_options_screened", "central_routes_screened"} and p.episode.analysis_status == "partial"
            and any(x.key == "press_cut_lanes" for x in p.metrics)]
    assert trap, "the trap on the left centre-back should be a restricting-only story"
    m = {x.key: x.value for x in trap[-1].metrics}
    assert m["press_cut_lanes"] >= 2  # the presser himself stands in the lines back to the middle
    assert m["buildup_count_defense"] < m["buildup_count_attack"]  # pressing with fewer players...
    assert m["rest_count_defense"] > m["rest_count_attack"]  # ...while keeping a spare behind
    assert not any(p.episode.outcome.status == "exploited_in_synthetic_play"
                   for revs in main.packets.values() for p in revs)
    esc = analyzed["clip_008"][3]
    used = [p for revs in esc.packets.values() for p in revs
            if p.episode.outcome.status == "exploited_in_synthetic_play"]
    assert used
    last = used[-1]
    free = next(c for c in last.claims if c.code == "accessible_free_receiver")
    assert last.episode.outcome.realized_target_id in free.player_ids
    assert any(ch.code == "defender_step" for p in used for ch in p.changes)
    # told in the order it was observed: the free man received, then the striker stepped up to him
    a = analyst_plan(last)
    plan = producer_plan(last, a)
    keys = [s.template_key for s in plan.variants[0].sentences]
    assert keys.index("outcome.exploited") < keys.index("change.defender_step")
    # "press with fewer and still keep more behind" is refused when the numbers do not say so
    restricted = next(c for c in last.claims if c.code == "short_options_screened")
    m = {last.metric(i).key: last.metric(i).value for i in restricted.metric_ids}
    assert not (m["buildup_count_defense"] < m["buildup_count_attack"] and m["rest_count_defense"] > m["rest_count_attack"])
    forced = plan.model_copy(deep=True)
    forced.variants[0].sentences.insert(1, SentencePlan(
        template_key="fan.press_numbers.short", claim_id=restricted.claim_id, change_id=None, event_ids=[],
        metric_ids=[i for i in restricted.metric_ids if last.metric(i).key.endswith(("_attack", "_defense"))
                    and last.metric(i).key.startswith(("buildup", "rest"))]))
    with pytest.raises(PlanError, match="template_condition"):
        validate_producer_plan(forced, last, a)


def test_e_passive_screen_step_out_frees_the_man_behind(analyzed):
    """E (passive screen): nobody presses the centre-back; once a midfielder steps out to his carry, the man he
    screened comes free and receives. In the other variant the screen holds and nobody comes free."""
    an = analyzed["clip_009"][3]
    revs = next(r for r in family_revs(an, "press_vs_depth") if r[-1].episode.analysis_status == "complete_tradeoff")
    first = revs[0]
    room = next(m for m in first.metrics if m.key == "carrier_nearest_defender_m")
    assert room.value >= 5.0 and not first.states[-1].pressures  # a screen without a presser
    i_step = next(i for i, p in enumerate(revs) if any(c.code == "defender_step" for c in p.changes))
    i_free = next(i for i, p in enumerate(revs) if "accessible_free_receiver" in codes(p))
    assert i_step <= i_free  # the step comes first, the free man after it, in one story
    step = next(c for c in revs[-1].changes if c.code == "defender_step")
    assert step.actor_team_id == revs[-1].episode.defending_team_id
    free = next(c for c in revs[-1].claims if c.code == "accessible_free_receiver")
    assert revs[-1].episode.outcome.status == "exploited_in_synthetic_play"
    assert revs[-1].episode.outcome.realized_target_id in free.player_ids
    keys = [s.template_key for s in producer_plan(first, analyst_plan(first)).variants[2].sentences]
    assert "analyst.passive_screen.short" in keys
    # the step names the carrier he stepped to, not the free man
    m_step = revs[-1].metric(step.metric_ids[0])
    assert m_step.player_ids[1] in room.player_ids[:1]
    hold = analyzed["clip_010"][3]
    packets = [p for revs in hold.packets.values() for p in revs]
    assert packets and all(p.episode.analysis_status == "partial" for p in packets)
    assert not any(c.code == "defender_step" for p in packets for c in p.changes)


def test_g_back_pass_trigger_steps_the_line_up(analyzed):
    """G (back-pass trigger): the press on the centre-back comes with the back line stepping up together; a runner
    who stayed onside gets in behind, one left beyond the line is not claimed as space behind it."""
    an = analyzed["clip_011"][3]
    revs = next(r for r in family_revs(an, "press_vs_depth") if r[-1].episode.analysis_status == "complete_tradeoff")
    last = revs[-1]
    step = next(c for c in last.changes if c.code == "block_shift")
    m_step = last.metric(step.metric_ids[0])
    assert m_step.key == "back_line_step_m" and m_step.value >= 6.0 and len(step.actor_player_ids) >= 3
    i_step = next(i for i, p in enumerate(revs) if any(c.code == "block_shift" for c in p.changes))
    i_behind = next(i for i, p in enumerate(revs) if "behind_space_exposed" in codes(p))
    assert i_step <= i_behind  # the higher line comes first, the space behind it after
    depth = next(m for m in last.metrics if m.key == "back_line_depth_m")
    assert depth.value >= 40.0
    behind = next(c for c in last.claims if c.code == "behind_space_exposed")
    assert last.episode.outcome.status == "exploited_in_synthetic_play"
    assert last.episode.outcome.realized_target_id in behind.player_ids
    keys = [s.template_key for s in producer_plan(last, analyst_plan(last)).variants[2].sentences]
    assert "change.line_step_up" in keys and "change.block_shift" not in keys
    # the replay draws every stepping defender's path and the step-up label within the analyst caps
    meta, frames = analyzed["clip_011"][:2]
    pos_at = lambda f: {p.player_id: (p.position.x_m, p.position.y_m) for p in frames[f].players}  # noqa: E731
    ks = key_state(last)
    ov = build_overlay(state=ks, packet=last, frame_players=pos_at(ks.frame_ref.frame_index), meta=meta,
                       replay_id="r", epoch=1, audience="analyst", language="en", surface="inspector_preview",
                       display_mode="retrospective_replay", positions_at=pos_at)
    ghosts = [e for e in ov.snapshot.elements if e.style_token == "ghost_path"]
    assert len(ghosts) == min(len(step.actor_player_ids), 4)
    assert any(e.geometry.type == "label" and e.geometry.template_key == "label.change.line_step_up"
               for e in ov.snapshot.elements)
    off = analyzed["clip_012"][3]
    story = next(r for r in family_revs(off, "press_vs_depth") if any(c.code == "block_shift" for c in r[-1].changes))
    assert all(p.episode.analysis_status == "partial" for p in story)
    assert any(f.startswith("behind_space_exposed:offside_geometry:") for f in story[-1].forbidden_inferences)


def test_b_line_separation_sequence(analyzed):
    an = analyzed["clip_002"][3]
    revs = next(r for r in family_revs(an, "line_separation") if r[-1].episode.analysis_status == "complete_tradeoff")
    last = revs[-1]
    assert [c.code for c in last.changes] == ["lane_change", "block_shift"]
    assert last.changes[0].actor_team_id != last.changes[1].actor_team_id
    m = {x.key: x.value for x in last.metrics}
    assert m["mid_back_gap_m"] >= 20.0 and m["behind_depth_change_m"] <= -8.0
    recv = next(c for c in last.claims if c.code == "between_lines_receiver")
    assert last.episode.outcome.status == "exploited_in_synthetic_play"
    assert last.episode.outcome.realized_target_id in recv.player_ids


def test_b_compact_block_creates_no_between_lines_exposure(analyzed):
    an = analyzed["clip_005"][3]
    packets = [p for revs in family_revs(an, "line_separation") for p in revs]
    assert packets and all(p.episode.analysis_status == "partial" for p in packets)
    assert not any(c.code in ("press_block_disconnected", "between_lines_receiver") for p in packets for c in p.claims)


def _supported_timeline(meta, frames, events):
    """Re-run the analyzer and record which claims are live (supported) on each frame."""
    from app.analytics.engine import TacticalAnalyzer
    an = TacticalAnalyzer(meta, frames[0].frame_ref.clip_id)
    seen, live = set(), []
    for f in frames:
        arrived = [e for e in events if e.available_at_s <= f.match_time_s and e.event_id not in seen]
        seen |= {e.event_id for e in arrived}
        r = an.step(f, arrived)
        live.append((r.t, {code: set(fc.player_ids) for code, fc in r.supported.items()}))
    return an, live


def test_c_drop_then_step_updates_the_free_receiver(analyzed):
    meta, frames, events, _ = analyzed["clip_003"]
    an, live = _supported_timeline(meta, frames, events)
    revs = next(r for r in family_revs(an, "central_screen") if r[-1].episode.analysis_status == "complete_tradeoff")
    last = revs[-1]
    changes = {c.code: c for c in last.changes}
    assert list(changes) == ["attacker_drop", "defender_step"]
    t_drop, t_step = changes["attacker_drop"].observed_at_s, changes["defender_step"].observed_at_s
    dropper = changes["attacker_drop"].actor_player_ids[0]
    # the man who dropped is the free receiver the drop opens (another player, e.g. the far centre-back for a moment
    # as the ball arrives, is a separate observation)
    free_times = [t for t, s in live if dropper in s.get("accessible_free_receiver", set())]
    assert free_times, "the drop should open an accessible free receiver"
    assert min(free_times) >= t_drop - 1e-6  # appears only after the drop was observed
    assert all(t < t_step + 0.6 for t in free_times)  # and is gone shortly after the step
    # the step was made by a defender close to the player who dropped
    assert changes["defender_step"].actor_team_id == last.episode.defending_team_id
    assert {"central_routes_screened", "wide_outlet_available", "accessible_free_receiver"} <= codes(last)


def test_c_outcome_arriving_after_close_is_a_new_revision(analyzed):
    an = analyzed["clip_003"][3]
    revs = next(r for r in family_revs(an, "central_screen") if r[-1].episode.analysis_status == "complete_tradeoff")
    closed = [p for p in revs if p.episode.lifecycle == "closed"]
    assert len(closed) >= 2
    assert closed[0].episode.outcome.status == "not_yet_observed"
    assert closed[-1].episode.outcome.status == "exploited_in_synthetic_play"
    wide = next(c for c in closed[-1].claims if c.code == "wide_outlet_available")
    assert closed[-1].episode.outcome.realized_target_id in wide.player_ids


def test_c_counter_example_shows_numbers_without_a_free_man(analyzed):
    an = analyzed["clip_006"][3]
    packets = [p for revs in family_revs(an, "central_screen") for p in revs]
    assert any(c.code == "local_central_overload" for p in packets for c in p.claims)
    assert not any(c.code == "accessible_free_receiver" for p in packets for c in p.claims)
    assert any(f.startswith("accessible_free_receiver:count_without_accessible_lane") for p in packets
               for f in p.forbidden_inferences)
    overload = next(c for p in packets for c in p.claims if c.code == "local_central_overload")
    assert overload.dimension == "context"


def test_nominal_labels_do_not_change_the_analysis(analyzed):
    """Swapping jersey numbers and team names (the only nominal labels in public data) changes nothing."""
    meta, frames, events, ref = analyzed["clip_003"]
    d = meta.model_dump(mode="json")
    jerseys = [r["jersey"] for r in d["roster"]]
    for r, j in zip(d["roster"], reversed(jerseys)):
        r["jersey"] = j
    for t in d["teams"]:
        t["display_name"] = "Team " + t["team_id"].upper()
    other = run_analyzer(MatchMeta.model_validate(d), frames, events)
    def summary(an):
        return sorted((eid, p.episode.revision, p.episode.analysis_status, tuple(sorted(codes(p))),
                       tuple((m.key, m.value) for m in p.metrics)) for eid, revs in an.packets.items() for p in revs)
    assert summary(ref) == summary(other)


@pytest.mark.parametrize("cid", CLIPS)
def test_every_revision_has_valid_plans_and_overlays(analyzed, cid):
    meta, frames, events, an = analyzed[cid]
    pos_at = lambda f: {p.player_id: (p.position.x_m, p.position.y_m) for p in frames[f].players}  # noqa: E731
    for revs in an.packets.values():
        for p in revs:
            a = analyst_plan(p)
            validate_analyst_plan(a, p)
            validate_producer_plan(producer_plan(p, a), p, a)
            ks = key_state(p)
            players = pos_at(ks.frame_ref.frame_index)
            for audience in ("fan", "analyst"):
                ov = build_overlay(state=ks, packet=p, frame_players=players, meta=meta, replay_id="r", epoch=1,
                                   audience=audience, language="en", surface="inspector_preview",
                                   display_mode="retrospective_replay", positions_at=pos_at)
                validate_overlay(ov.snapshot, players, p)
                if audience == "fan":  # spec 10.2 as amended: display units, both sides of a complete trade-off
                    units = {FAN_UNIT.get(e.style_token, e.element_id) for e in ov.snapshot.elements}
                    assert len(display_units(ov.snapshot.elements)) <= 6
                    if p.episode.analysis_status == "complete_tradeoff":
                        full = build_overlay(state=ks, packet=p, frame_players=players, meta=meta, replay_id="r",
                                             epoch=1, audience="analyst", language="en", surface="inspector_preview",
                                             display_mode="retrospective_replay", positions_at=pos_at)
                        has = {FAN_UNIT.get(e.style_token) for e in full.snapshot.elements}
                        for side in (RESTRICTED_UNITS, EXPOSED_UNITS):
                            if has & side:
                                assert units & side, (p.episode.episode_id, side, sorted(units))
                else:
                    assert len(ov.snapshot.elements) <= 20
                n_labels = sum(e.geometry.type == "label" for e in ov.snapshot.elements)
                assert n_labels <= (2 if audience == "fan" else 5)
