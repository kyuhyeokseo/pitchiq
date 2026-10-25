"""v2 contract tests (spec 11 / 11.1): real payloads round-trip, invalid ones are rejected."""

from __future__ import annotations

import copy
import re

import pytest
from pydantic import ValidationError

from app.contracts import (
    EvidenceMetric,
    EvidencePacket,
    MatchEvent,
    ObservationFrame,
    OverlayElement,
    ProducerPlan,
    TradeoffEpisode,
)

from .v2_helpers import PUBLIC, ROOT, base_positions, frame_dict, load_public, run_analyzer


@pytest.fixture(scope="module")
def clip1():
    meta, frames, events = load_public(PUBLIC / "clip_001")
    return meta, frames, events, run_analyzer(meta, frames, events)


def test_public_payloads_round_trip(clip1):
    meta, frames, events, an = clip1
    for f in frames:
        assert ObservationFrame.model_validate(f.model_dump(mode="json")) == f
    for e in events:
        assert MatchEvent.model_validate(e.model_dump(mode="json")) == e
    packets = [p for revs in an.packets.values() for p in revs]
    assert packets
    for p in packets:
        again = EvidencePacket.model_validate(p.model_dump(mode="json"))  # re-checks integrity and the hash
        assert again.evidence_hash == p.evidence_hash


def _frame(**over):
    d = frame_dict(0, base_positions())
    for k, v in over.items():
        d[k] = v
    return d


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d["players"].pop(), "22 unique"),
    (lambda d: d["players"].__setitem__(1, {**d["players"][1], "player_id": d["players"][0]["player_id"]}), "unique"),
    (lambda d: d["players"].__setitem__(1, {**d["players"][1], "is_goalkeeper": True}), "goalkeeper"),
    (lambda d: d["players"][3]["position"].__setitem__("x_m", 106.0), "outside the pitch"),
    (lambda d: d.__setitem__("possession_team_id", "red"), "possession_team_id"),
    (lambda d: d["ball"].__setitem__("owner_id", None), "needs an owner"),
])
def test_frame_invariants_are_enforced(mutate, msg):
    d = _frame()
    mutate(d)
    with pytest.raises(ValidationError, match=msg):
        ObservationFrame.model_validate(d)


def test_events_cannot_name_future_receivers_or_arrive_early():
    base = {"event_id": "e1", "match_id": "m", "clip_id": "c", "event_time_s": 10.0, "available_at_s": 10.4,
            "kind": "pass_release", "actor_id": "blue_05", "target_id": None, "related_event_id": None, "position": None}
    MatchEvent.model_validate(base)
    with pytest.raises(ValidationError, match="future"):
        MatchEvent.model_validate({**base, "target_id": "blue_09"})
    with pytest.raises(ValidationError, match="precede"):
        MatchEvent.model_validate({**base, "available_at_s": 9.0})


def _metric(**over):
    d = {"metric_id": "m1", "key": "back_line_depth_m", "value": 45.0, "unit": "m", "status": "ok",
         "window": {"start_s": 0, "end_s": 1}, "player_ids": [], "frame_refs": [], "event_ids": [],
         "method_version": "t"}
    d.update(over)
    return d


@pytest.mark.parametrize("over,msg", [
    ({"status": "unavailable"}, "value=null"),
    ({"value": None}, "finite value"),
    ({"key": "zone_marking_coverage", "unit": "ratio", "value": 1.2}, r"\[0, 1\]"),
    ({"key": "short_options_screened", "unit": "count", "value": 2.5}, "non-negative integer"),
    ({"key": "made_up_metric"}, "not in the registry"),
    ({"unit": "s"}, "must use unit"),
])
def test_metric_rules(over, msg):
    EvidenceMetric.model_validate(_metric())
    with pytest.raises(ValidationError, match=msg):
        EvidenceMetric.model_validate(_metric(**over))


def test_complete_tradeoff_needs_both_sides(clip1):
    ep = next(p for revs in clip1[3].packets.values() for p in revs
              if p.episode.analysis_status == "complete_tradeoff").episode
    d = ep.model_dump(mode="json")
    d["exposed_claim_ids"] = []
    with pytest.raises(ValidationError, match="restricted and one exposed"):
        TradeoffEpisode.model_validate(d)


def test_packet_integrity_is_checked(clip1):
    p = next(p for revs in clip1[3].packets.values() for p in revs if p.episode.analysis_status == "complete_tradeoff")
    d = p.model_dump(mode="json")
    bad = copy.deepcopy(d)
    bad["evidence_hash"] = "sha256:" + "1" * 64
    with pytest.raises(ValidationError, match="evidence_hash"):
        EvidencePacket.model_validate(bad)
    bad = copy.deepcopy(d)
    bad["claims"][0]["metric_ids"] = ["m_not_there"]
    with pytest.raises(ValidationError, match="unknown metric"):
        EvidencePacket.model_validate(bad)
    bad = copy.deepcopy(d)
    bad["metrics"][0]["window"]["end_s"] = d["episode"]["analyzed_until_s"] + 5
    with pytest.raises(ValidationError, match="cutoff"):
        EvidencePacket.model_validate(bad)


def test_overlay_elements_are_constrained():
    good = {"element_id": "e", "layer": "exposure", "claim_ids": [], "metric_ids": [], "relation_ids": [],
            "geometry": {"type": "polygon", "points": [{"x_m": 1, "y_m": 1}, {"x_m": 5, "y_m": 1}, {"x_m": 5, "y_m": 5}]},
            "style_token": "exposure_hatched"}
    OverlayElement.model_validate(good)
    with pytest.raises(ValidationError, match="allowlisted"):
        OverlayElement.model_validate({**good, "style_token": "fill:red"})
    with pytest.raises(ValidationError):
        OverlayElement.model_validate({**good, "geometry": {"type": "polygon", "points": good["geometry"]["points"][:2]}})
    with pytest.raises(ValidationError, match="outside the pitch"):
        OverlayElement.model_validate({**good, "geometry": {"type": "polyline", "points": [
            {"x_m": 1, "y_m": 1}, {"x_m": 120, "y_m": 1}]}})


def test_producer_plan_needs_all_four_variants():
    v = {"sentences": [{"template_key": "outcome.pending", "claim_id": None, "change_id": None, "metric_ids": [],
                        "event_ids": []}], "focus_layers": ["caption"]}
    variants = [{"audience": a, "language": l, **v} for a, l in (("fan", "en"), ("fan", "ko"), ("analyst", "en"))]
    with pytest.raises(ValidationError, match="exactly once"):
        ProducerPlan.model_validate({"episode_id": "e", "evidence_hash": "sha256:" + "0" * 64, "variants": variants})


def test_typescript_contracts_are_the_spec_block():
    spec = (ROOT / "PitchIQ_Defensive_Intelligence_Implementation_Spec_v2.md").read_text()
    block = re.search(r"```typescript\n(.*?)```", spec, re.S).group(1)
    ts = (ROOT / "frontend" / "src" / "contracts.ts").read_text()
    body = "\n".join(line.removeprefix("export ") for line in ts.splitlines() if not line.startswith("//"))
    assert block.strip() in body.strip()
