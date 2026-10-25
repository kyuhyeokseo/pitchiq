"""The serving image boundary (docs/design.md): public observations and serving code only. Docker is not available on
the development host, so the build files themselves are checked."""

from __future__ import annotations

from .v2_helpers import ROOT


def test_dockerfile_copies_only_serving_code_frontend_and_public_clips():
    lines = (ROOT / "infra" / "Dockerfile").read_text().splitlines()
    sources = [tok for line in lines if line.startswith("COPY ") and "--from=" not in line
               for tok in line.split()[1:-1]]
    allowed = ("pyproject.toml", "infra/constraints.txt", "backend/app", "data/public", "frontend/")
    assert sources and all(s.startswith(allowed) for s in sources), sources
    assert not any(w in " ".join(sources) for w in ("private_truth", "evaluation", "simulator", ".env"))


def test_dockerignore_keeps_generator_code_and_truth_out_of_the_build_context():
    rules = [r.strip() for r in (ROOT / ".dockerignore").read_text().splitlines() if r.strip() and not r.startswith("#")]
    assert rules[0] == "**"  # everything is excluded unless re-included
    reincluded = [r[1:] for r in rules if r.startswith("!")]
    assert not any(w in r for r in reincluded for w in ("private_truth", "evaluation", ".env", "tests"))
    assert "backend/app/simulator/" in rules and rules.index("backend/app/simulator/") > rules.index("!backend/app/**")
