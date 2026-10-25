"""Holdout evaluation pieces. The runner lives in evaluation/ and may read private truth; analytics never does."""

from __future__ import annotations

import importlib.util
import json

from .v2_helpers import ROOT, TRUTH


def _runner():
    spec = importlib.util.spec_from_file_location("holdout_v2", ROOT / "evaluation" / "holdout_v2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_iou():
    m = _runner()
    assert m.iou((0.0, 10.0), (5.0, 15.0)) == 5.0 / 15.0
    assert m.iou((0.0, 1.0), (2.0, 3.0)) == 0.0


def test_every_committed_clip_has_a_played_story_in_its_private_truth():
    for p in sorted(TRUTH.glob("clip_*.json")):
        s = json.loads(p.read_text())["story"]
        assert s["executed"] and s["families"], p.name
        assert s["window_clip_s"][1] > s["window_clip_s"][0], p.name


def test_holdout_perturbation_is_deterministic_and_leaves_plain_clips_alone():
    from app.simulator.defense.generate import FIXTURES, generate_clip
    cfg = {**FIXTURES[0], "clip_id": "clip_h"}
    ranges = {"lag_scale": [0.8, 1.2], "cushion_scale": [0.9, 1.1], "speed_scale": [0.95, 1.05],
              "timing_scale": [0.9, 1.1], "start_jitter_m": 1.0}
    a = generate_clip(5000, {**cfg, "perturb": ranges})
    b = generate_clip(5000, {**cfg, "perturb": ranges})
    assert a[1] == b[1] and a[3]["perturbation"] == b[3]["perturbation"]
    plain = generate_clip(5000, cfg)
    assert plain[1] != a[1] and "perturbation" not in plain[3]
