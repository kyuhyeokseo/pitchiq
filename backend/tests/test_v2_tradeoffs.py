"""Trade-off episodes on the family-A clips: claims, suppression, revisions, causality, invariance."""

from __future__ import annotations

import json
import re
import time

import numpy as np
import pytest

from app.contracts import MatchEvent
from app.simulator.defense.generate import FIXTURES, generate_clip
from app.contracts import MatchMeta, ObservationFrame

from .v2_helpers import PUBLIC, TRUTH, load_public, run_analyzer


@pytest.fixture(scope="module")
def main():
    meta, frames, events = load_public(PUBLIC / "clip_001")
    return meta, frames, events, run_analyzer(meta, frames, events)


@pytest.fixture(scope="module")
def cover():
    meta, frames, events = load_public(PUBLIC / "clip_004")
    return meta, frames, events, run_analyzer(meta, frames, events)


def _complete(an):
    return [p for revs in an.packets.values() for p in revs if p.episode.analysis_status == "complete_tradeoff"]


def test_high_line_press_produces_a_supported_restricted_exposed_pair(main):
    pk = _complete(main[3])
    assert pk, "expected a complete trade-off on the main clip"
    codes = {c.code: c for c in pk[0].claims}
    assert codes["short_options_screened"].dimension == "restricted"
    assert {"behind_space_exposed", "isolated_1v1"} <= set(codes)
    m = {x.key: x.value for x in pk[0].metrics}
    assert m["back_line_depth_m"] >= 40.0
    assert m["short_options_screened"] >= 2 and m["short_option_candidates"] >= 3
    assert m["cover_margin_s"] >= 0.5 and m["gk_cover_margin_s"] >= 0.5


def test_outcome_is_a_new_revision_and_history_is_kept(main):
    revs = next(revs for revs in main[3].packets.values() if any(
        p.episode.analysis_status == "complete_tradeoff" for p in revs))
    statuses = [p.episode.outcome.status for p in revs]
    assert "exploited_in_synthetic_play" in statuses
    first = statuses.index("exploited_in_synthetic_play")
    assert all(s == "not_yet_observed" for s in statuses[:first])
    later = revs[first]
    assert later.episode.outcome.realized_target_id is not None and len(later.events) >= 1
    # earlier revisions are frozen: their claims stay and their hash still verifies
    assert revs[first - 1].episode.exposed_claim_ids == later.episode.exposed_claim_ids
    assert [p.episode.revision for p in revs] == list(range(1, len(revs) + 1))
    assert any(ch.code == "line_retreat" and ch.relation == "observed_next" for ch in later.changes)


def test_extra_cover_suppresses_isolation_but_keeps_the_restriction(cover):
    # the designed story's defending team: once the long ball is won the other team defends (another story)
    team = json.loads((TRUTH / "clip_004.json").read_text())["defending_team_id"]
    packets = [p for revs in cover[3].packets.values() for p in revs if p.episode.defending_team_id == team]
    assert not [p for p in packets if p.episode.analysis_status == "complete_tradeoff"]
    assert not any(c.code in ("isolated_1v1", "behind_space_exposed") for p in packets for c in p.claims)
    assert any(c.code == "short_options_screened" for p in packets for c in p.claims)
    forbidden = {f for p in packets for f in p.forbidden_inferences}
    assert any(f.startswith("isolated_1v1:") and "cover_in_time" in f for f in forbidden)
    # same high line: the back line depth is still measured high in the negative case
    depths = [g.own_goal_depth_m for p in packets for s in p.states for g in s.line_groups if g.group == "back"]
    assert depths and max(depths) >= 40.0


def test_a_revision_after_a_turnover_keeps_its_own_defending_team(cover):
    """An outcome revision can come after possession changed. The other team's current state must not be pulled into
    the episode (the packet validator rejects mixed defending teams; this used to stop the analyzer)."""
    an = run_analyzer(*cover[:3])  # its own analyzer: the revision below adds a packet
    ep = next(e for e in an.episodes.values() if e.defending == "red")
    other = next(s for s in an.states.values() if s.defending_team_id not in (None, ep.defending))
    an._revise(ep, len(an.buf.t) - 1, other)
    packet = an.packets[ep.episode_id][-1]
    assert {s.defending_team_id for s in packet.states} <= {None, ep.defending}


def test_isolation_is_never_inferred_from_a_nearest_pair_alone(cover):
    """In the negative clip the runner has a sustained marker, yet no isolation is claimed."""
    an = cover[3]
    paired = [s for s in an.states.values() for m in s.markings if m.status == "ok" and m.attacker_id == "blue_09"]
    assert paired
    assert not any(c.code == "isolated_1v1" for revs in an.packets.values() for p in revs for c in p.claims)


def test_prefix_analysis_gives_identical_packets(main):
    meta, frames, events, full = main
    k = 70  # mid-flight of the long ball
    cut = frames[k].match_time_s
    part = run_analyzer(meta, frames, events, upto=k)
    full_hashes = {(eid, p.episode.revision): p.evidence_hash for eid, revs in full.packets.items() for p in revs
                   if p.episode.analyzed_until_s <= cut}
    part_hashes = {(eid, p.episode.revision): p.evidence_hash for eid, revs in part.packets.items() for p in revs}
    assert full_hashes and full_hashes == part_hashes


def test_future_events_do_not_change_the_past(main):
    meta, frames, events, full = main
    cut = frames[70].match_time_s
    tampered = [e if e.available_at_s <= cut else MatchEvent.model_validate(
        {**e.model_dump(mode="json"), "target_id": None if e.kind == "pass_release" else "blue_02"}) for e in events]
    an = run_analyzer(meta, frames, tampered)
    before = {(eid, p.episode.revision): p.evidence_hash for eid, revs in full.packets.items() for p in revs
              if p.episode.analyzed_until_s <= cut}
    after = {(eid, p.episode.revision): p.evidence_hash for eid, revs in an.packets.items() for p in revs
             if p.episode.analyzed_until_s <= cut}
    assert before == after


def _summary(an):
    out = []
    for revs in an.packets.values():
        for p in revs:
            out.append((p.episode.revision, p.episode.analysis_status, tuple(sorted(c.code for c in p.claims)),
                        p.episode.outcome.status, round(p.episode.analyzed_until_s - an.buf.t[0], 1)))
    return sorted(out, key=lambda x: (x[4], x[0]))


def _metrics(an):
    return sorted((m.key, round(m.value, 1)) for revs in an.packets.values() for p in revs for m in p.metrics
                  if m.value is not None and m.unit in ("m", "s", "count"))


@pytest.mark.parametrize("direction,mirror", [(-1, False), (1, True), (-1, True)])
def test_world_rotation_and_mirror_do_not_change_the_analysis(direction, mirror):
    cfg = dict(FIXTURES[0])
    ref = _analyze(generate_clip(cfg["seed"], cfg))
    other = _analyze(generate_clip(cfg["seed"], {**cfg, "attack_direction": direction, "mirror": mirror}))
    assert _summary(ref) == _summary(other)
    a, b = _metrics(ref), _metrics(other)
    assert len(a) == len(b)
    assert all(ka == kb and abs(va - vb) <= 0.11 for (ka, va), (kb, vb) in zip(a, b))


def _analyze(generated):
    meta, obs, evs, _ = generated
    m = MatchMeta.model_validate({**meta, "clips": [{**meta["clips"][0], "checksum": "sha256:" + "0" * 64}]})
    frames = [ObservationFrame.model_validate(o) for o in obs]
    events = [MatchEvent.model_validate(e) for e in evs]
    return run_analyzer(m, frames, events)


def test_analytics_never_read_roles_or_formation_names():
    from pathlib import Path
    app = Path(__file__).resolve().parents[1] / "app"
    for mod in ("analytics/engine.py", "analytics/lines.py", "analytics/marking.py", "analytics/lanes.py",
                "analytics/cover.py", "analytics/regions.py", "analytics/pressure.py", "analytics/phase.py",
                "analytics/tradeoffs.py", "contracts/observation.py"):
        text = (app / mod).read_text()
        # "formation_superiority" is the name of a forbidden inference, not a use of formation labels
        assert "nominal_role" not in text and not re.search(r"formation(?!_superiority)", text), mod


def test_analysis_step_time_is_well_inside_the_5hz_budget():
    meta, frames, events = load_public(PUBLIC / "clip_001")
    from app.analytics.engine import TacticalAnalyzer
    an = TacticalAnalyzer(meta, "clip_001")
    seen, dts = set(), []
    for f in frames:
        arrived = [e for e in events if e.available_at_s <= f.match_time_s and e.event_id not in seen]
        seen |= {e.event_id for e in arrived}
        t0 = time.perf_counter()
        an.step(f, arrived)
        dts.append(time.perf_counter() - t0)
    assert np.percentile(dts, 95) < 0.2
