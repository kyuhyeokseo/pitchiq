"""Seeded reproducibility: identical bytes for identical seeds; committed fixtures
are exactly what the current code generates."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.simulator.generate import main as generate_main
from app.simulator.recipes import REPO_ROOT
from app.simulator.validate import validate_scenario_dir


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _gen(root: Path, seed: int, clips: int = 6) -> Path:
    rc = generate_main(["--seed", str(seed), "--clips", str(clips), "--output", str(root / "public" / "ds"),
                        "--truth-dir", str(root / "private_truth")])
    assert rc == 0
    return root


def test_same_seed_gives_byte_identical_public_and_private_output(tmp_path):
    a = _tree(_gen(tmp_path / "a", 11))
    b = _tree(_gen(tmp_path / "b", 11))
    assert a == b
    assert any(k.endswith(".jsonl.gz") for k in a) and "private_truth/ds.jsonl" in a


def test_different_seed_differs_but_stays_valid(tmp_path, cfg):
    a = _gen(tmp_path / "a", 11)
    b = _gen(tmp_path / "b", 12)
    assert _tree(a) != _tree(b)
    for root in (a, b):
        rep = validate_scenario_dir(root / "public" / "ds", truth_path=root / "private_truth" / "ds.jsonl",
                                    private_vocab_cfg=cfg)
        assert rep.ok, rep.errors[:5]


def test_committed_demo_fixtures_match_current_generator(tmp_path):
    rc = generate_main(["--demo", "--output", str(tmp_path / "fixtures"), "--truth-dir", str(tmp_path / "truth")])
    assert rc == 0
    committed = REPO_ROOT / "data" / "fixtures" / "scenario_a"
    assert _tree(tmp_path / "fixtures" / "scenario_a") == _tree(committed), (
        "data/fixtures is stale: run `make fixtures` and commit the result"
    )
    assert (tmp_path / "truth" / "scenario_a.jsonl").read_bytes() == (
        REPO_ROOT / "data" / "private_truth" / "scenario_a.jsonl"
    ).read_bytes()
