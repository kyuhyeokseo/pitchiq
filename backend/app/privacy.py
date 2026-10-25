"""Guards that keep generator-private ground truth out of public payloads.

Anything the simulator knows but a live system could not observe (scenario
recipe, intent, true pattern label, defender policy, seeds) is private. Public
tracking, events, manifests, API responses and agent evidence must never carry
these keys. The guard is applied in schema validators and in tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

FORBIDDEN_PUBLIC_KEYS: frozenset[str] = frozenset(
    {
        "pattern_truth",
        "generator_intent",
        "phase_truth",
        "recipe",
        "recipe_name",
        "scenario_plan",
        "defender_response",
        "seed",
        "random_seed",
        "label",
        "labels",
        "truth",
        "hidden_intent",
        "ground_truth",
    }
)


class PrivateFieldLeak(ValueError):
    pass


def find_private_keys(obj: Any, path: str = "$") -> list[str]:
    """Return JSON paths of any forbidden keys found anywhere in ``obj``."""
    hits: list[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in FORBIDDEN_PUBLIC_KEYS:
                hits.append(f"{path}.{k}")
            hits.extend(find_private_keys(v, f"{path}.{k}"))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits.extend(find_private_keys(v, f"{path}[{i}]"))
    return hits


def find_private_tokens(text: str, tokens: Iterable[str]) -> list[str]:
    """Return private vocabulary tokens (e.g. recipe names) present in serialized text."""
    return sorted({t for t in tokens if t and t in text})


def assert_public(obj: Any) -> None:
    hits = find_private_keys(obj)
    if hits:
        raise PrivateFieldLeak(f"private generator fields in public payload: {hits}")
