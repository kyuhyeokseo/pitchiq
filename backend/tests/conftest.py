from __future__ import annotations

import os
from pathlib import Path

# Tests never call the cloud: whatever a developer's .env says, the API under test runs offline.
os.environ["LLM_MODE"] = "offline"
os.environ["PITCHIQ_DOTENV"] = "0"

import pytest

from app.simulator.generate import build_clip, generate_dataset, generate_demo, write_scenario
from app.simulator.recipes import load_recipes

MATRIX_SEED = 777


@pytest.fixture(scope="session")
def cfg() -> dict:
    return load_recipes()


@pytest.fixture(scope="session")
def matrix(tmp_path_factory, cfg) -> tuple[Path, Path]:
    """Every recipe x flank x attacking team x period (both attack directions)."""
    root = tmp_path_factory.mktemp("matrix")
    clips = []
    i = 0
    slot = {1: 0, 2: 0}
    for recipe in sorted(cfg["recipes"]):
        for flank in ("right", "left"):
            for team in ("blue", "red"):
                for period in (1, 2):
                    start = (10.0 if period == 1 else 2710.0) + 26.0 * slot[period]
                    slot[period] += 1
                    spec = {"clip_id": f"m{i:03d}", "recipe": recipe, "attacking_team": team, "flank": flank,
                            "start_match_s": start, "scenario_id": "matrix"}
                    clips.append(build_clip(cfg, spec, "synthetic_matrix", MATRIX_SEED, i, blue_dir_p1=1))
                    i += 1
    out, truth = root / "public" / "matrix", root / "private_truth" / "matrix.jsonl"
    write_scenario(out, truth, scenario_id="matrix", match_id="synthetic_matrix", title="test matrix",
                   generator_version=cfg["generator_version"], clips=clips)
    return out, truth


@pytest.fixture(scope="session")
def dataset(tmp_path_factory, cfg) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("dataset")
    out = generate_dataset(cfg, 42, 40, root / "public" / "ds_s42", root / "private_truth")
    return out, root / "private_truth" / "ds_s42.jsonl"


@pytest.fixture(scope="session")
def demo(tmp_path_factory, cfg) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("demo")
    (out,) = generate_demo(cfg, root / "public", root / "private_truth")
    return out, root / "private_truth" / f"{out.name}.jsonl"
