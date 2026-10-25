"""v2 generator: determinism, physical caps, truth separation (spec 5.3, 6.3, 17.1)."""

from __future__ import annotations

import json
from collections import Counter

import numpy as np
import pytest

from app.simulator.defense.generate import FIXTURES, generate_clip, generate_fixtures

from .v2_helpers import PUBLIC, TRUTH, jsonl


def test_committed_v2_fixtures_match_the_generator(tmp_path):
    pub, tru = tmp_path / "public", tmp_path / "truth"
    generate_fixtures(pub, tru)
    for cfg in FIXTURES:
        cid = cfg["clip_id"]
        for name in ("manifest.json", "tracking.jsonl", "events.jsonl"):
            assert (pub / cid / name).read_bytes() == (PUBLIC / cid / name).read_bytes(), (cid, name)
        assert (tru / f"{cid}.json").read_bytes() == (TRUTH / f"{cid}.json").read_bytes()


def _checksum(seed, cfg):
    meta, obs, evs, _ = generate_clip(seed, cfg)
    return json.dumps(obs, sort_keys=True).__hash__(), obs


def test_same_seed_same_clip_and_different_seed_differs():
    cfg = dict(FIXTURES[0])
    a1, _ = _checksum(11, cfg)
    a2, _ = _checksum(11, cfg)
    b, _ = _checksum(12, cfg)
    assert a1 == a2 and a1 != b


@pytest.mark.parametrize("variant", ["main", "cover"])
def test_frames_respect_development_caps(variant):
    cfg = {**FIXTURES[0], "variant": variant}
    for seed in (3, 4):
        _, obs, evs, _ = generate_clip(seed, cfg)
        P = np.array([[[p["position"]["x_m"], p["position"]["y_m"]] for p in f["players"]] for f in obs])
        V = np.array([[[p["velocity"]["vx_mps"], p["velocity"]["vy_mps"]] for p in f["players"]] for f in obs])
        B = np.array([[f["ball"]["position"]["x_m"], f["ball"]["position"]["y_m"]] for f in obs])
        assert np.linalg.norm(V, axis=2).max() <= 9.0
        fd = np.diff(P, axis=0) / 0.2
        assert np.linalg.norm(fd, axis=2).max() <= 9.0 + 0.2
        acc = np.linalg.norm(np.diff(fd, axis=0) / 0.2, axis=2)
        assert np.percentile(acc, 99.5) <= 5.0 + 0.6 and acc.max() <= 9.0  # separation pushes may add a little
        assert np.linalg.norm(np.diff(B, axis=0), axis=1).max() / 0.2 <= 35.0
        assert all(f["match_time_s"] < g["match_time_s"] for f, g in zip(obs, obs[1:]))
        t0, t1 = obs[0]["match_time_s"], obs[-1]["match_time_s"]
        assert all(t0 <= e["event_time_s"] <= t1 for e in evs)
        kinds = Counter(e["kind"] for e in evs)
        assert kinds["pass_release"] >= kinds["pass_receive"]


def test_public_files_carry_no_private_truth():
    for cfg in FIXTURES:
        cid = cfg["clip_id"]
        truth = json.loads((TRUTH / f"{cid}.json").read_text())
        public_text = "".join((PUBLIC / cid / n).read_text() for n in ("manifest.json", "tracking.jsonl",
                                                                       "events.jsonl"))
        for token in (truth["generator_intent"], "family", "variant", "generator", "expected", "spare", "seed",
                      "high_man_press", cfg["variant"] + "\""):
            assert token not in public_text, (cid, token)
        assert "nominal_role" not in public_text and "formation" not in public_text
        assert not str(TRUTH.resolve()).startswith(str(PUBLIC.resolve()))


def test_long_ball_outcomes_differ_by_variant_across_seeds():
    """Generator behaviour check (not an analytics result): spare cover usually wins the ball.

    Measured on seeds 100-139 when written: main 39/40 received, cover 33/40 intercepted.
    The thresholds are regression guards below those values.
    """
    got = Counter()
    for seed in range(100, 140):
        for variant in ("main", "cover"):
            _, obs, evs, truth = generate_clip(seed, {**FIXTURES[0], "variant": variant})
            t = truth["markers_match_s"].get("long_ball_arrive_t")
            ev = next(e for e in evs if abs(e["event_time_s"] - t) < 0.05 and e["kind"] in ("pass_receive",
                                                                                             "interception"))
            got[(variant, ev["kind"])] += 1
    assert got[("main", "pass_receive")] >= 36
    assert got[("cover", "interception")] >= 26


def test_truth_is_stored_outside_the_public_tree():
    assert all((TRUTH / f"{c['clip_id']}.json").is_file() for c in FIXTURES)
    assert not any(p.name.endswith(".json") and "truth" in p.name for p in PUBLIC.rglob("*"))
    for row in jsonl(PUBLIC / FIXTURES[0]["clip_id"] / "events.jsonl"):
        assert set(row) == {"event_id", "match_id", "clip_id", "event_time_s", "available_at_s", "kind", "actor_id",
                            "target_id", "related_event_id", "position"}
