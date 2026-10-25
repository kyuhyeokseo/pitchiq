"""Generator-private ground truth must never reach public data or live code."""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from app.privacy import FORBIDDEN_PUBLIC_KEYS, find_private_keys, find_private_tokens
from app.simulator.validate import private_vocabulary

from .helpers import events, manifest, truth_rows

APP_DIR = Path(__file__).resolve().parents[1] / "app"
# Modules allowed to know about private truth: the generator itself, offline ML
# training/evaluation (future) and the guard that lists forbidden keys.
PRIVATE_OK = ("simulator", "ml")
PRIVATE_MARKERS = re.compile(
    r"private_truth|pattern_truth|generator_intent|defender_response|recipe_name|"
    r"from\s+\.\.?simulator|import\s+\.\.?simulator|app\.simulator"
)


def _public_texts(d: Path) -> list[str]:
    texts = [(d / "manifest.json").read_text(), (d / "events.jsonl").read_text()]
    texts += [gzip.decompress(p.read_bytes()).decode() for p in sorted((d / "tracking").glob("*.jsonl.gz"))]
    return texts


def _public_payloads(texts: list[str]):
    yield json.loads(texts[0])  # manifest
    for text in texts[1:]:  # events + tracking (JSON lines)
        for line in text.splitlines():
            yield json.loads(line)


@pytest.mark.parametrize("which", ["matrix", "dataset", "demo"])
def test_public_files_contain_no_private_keys_or_vocabulary(which, request, cfg):
    d, truth_path = request.getfixturevalue(which)
    texts = _public_texts(d)
    vocab = private_vocabulary(cfg)
    for text in texts:
        assert not find_private_tokens(text, vocab)
    for payload in _public_payloads(texts):
        assert not find_private_keys(payload)
    # The private side really does hold the labels (and lives elsewhere).
    rows = truth_rows(truth_path)
    assert all({"pattern_truth", "generator_intent", "defender_response"} <= set(r) for r in rows.values())
    assert d.resolve() not in truth_path.resolve().parents


def test_manifest_exposes_only_observable_metadata(demo):
    m = manifest(demo[0])
    assert set(m) == {"schema_version", "generator_version", "scenario_id", "match_id", "title", "synthetic",
                      "data_notice", "teams", "roster", "clips", "events_file", "events_sha256", "tracking_available"}
    assert set(m["clips"][0]) == {"clip_id", "period", "start_match_s", "end_match_s", "n_frames", "sample_rate_hz",
                                  "attack_direction", "tracking_file", "tracking_sha256"}
    assert m["synthetic"] is True and "Synthetic" in m["data_notice"]


def test_event_context_keys_are_whitelisted(dataset):
    allowed = {"length_m", "pass_event_id", "start_match_s", "distance_m", "unresolved_at_clip_end"}
    for e in events(dataset[0]):
        assert set(e["context"]) <= allowed, e


def test_clip_ids_are_neutral(dataset, cfg):
    vocab = private_vocabulary(cfg)
    for c in manifest(dataset[0])["clips"]:
        assert re.fullmatch(r"k\d{4}", c["clip_id"])
        assert not find_private_tokens(c["clip_id"] + c["tracking_file"], vocab)


def test_forbidden_key_list_covers_truth_record_fields(dataset):
    row = next(iter(truth_rows(dataset[1]).values()))
    for key in ("pattern_truth", "generator_intent", "phase_truth", "defender_response", "recipe_name"):
        assert key in row and key in FORBIDDEN_PUBLIC_KEYS


def test_live_code_does_not_touch_generator_private_modules():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR)
        if rel.parts[0] in PRIVATE_OK or rel.name == "privacy.py":
            continue
        if PRIVATE_MARKERS.search(path.read_text()):
            offenders.append(str(rel))
    assert not offenders, f"live modules reference generator-private data: {offenders}"
