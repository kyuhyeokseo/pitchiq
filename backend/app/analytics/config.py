"""Load the versioned analytics configuration (ANALYTICS_CONFIG_PATH overrides the packaged file)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).with_name("analytics_config.yaml")


class Config:
    """Read-only nested access: ``cfg.lanes.ball_speed_mps``."""

    def __init__(self, data: dict[str, Any]):
        self._data = MappingProxyType(data)

    def __getattr__(self, name: str) -> Any:
        try:
            v = self._data[name]
        except KeyError as e:
            raise AttributeError(name) from e
        return Config(v) if isinstance(v, dict) else v

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> Config:
    p = Path(path or os.environ.get("ANALYTICS_CONFIG_PATH") or DEFAULT_PATH)
    return Config(yaml.safe_load(p.read_text(encoding="utf-8")))
