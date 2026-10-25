"""Read-only catalog of PUBLIC clips (data/public/<clip_id>/). No private truth is ever loaded here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..contracts import MatchEvent, MatchMeta, ObservationFrame

PUBLIC_DIR = Path(os.environ.get("PUBLIC_DATA_DIR", "data/public"))


@dataclass(frozen=True)
class Clip:
    clip_id: str
    meta: MatchMeta
    frames: tuple[ObservationFrame, ...]
    events: tuple[MatchEvent, ...]

    @property
    def start_s(self) -> float:
        return self.frames[0].match_time_s

    @property
    def end_s(self) -> float:
        return self.frames[-1].match_time_s

    def frame_at(self, t: float) -> int:
        k = int(round((t - self.start_s) * self.meta.tracking_hz))
        return max(0, min(k, len(self.frames) - 1))


def _verify(root: Path, meta: MatchMeta) -> None:
    from hashlib import sha256
    tracking = (root / "tracking.jsonl").read_bytes()
    events = (root / "events.jsonl").read_bytes()
    h = sha256()
    h.update(tracking)
    h.update(b"\n--events--\n")
    h.update(events)
    if meta.clips[0].checksum != "sha256:" + h.hexdigest():
        raise ValueError(f"checksum mismatch for {root.name}")


@lru_cache(maxsize=32)
def load_clip(clip_id: str, root: str | None = None) -> Clip:
    base = Path(root) if root else PUBLIC_DIR
    d = base / clip_id
    if not d.is_dir() or "/" in clip_id or ".." in clip_id:
        raise KeyError(clip_id)
    meta = MatchMeta.model_validate_json((d / "manifest.json").read_text(encoding="utf-8"))
    _verify(d, meta)
    frames = tuple(ObservationFrame.model_validate_json(line) for line in (d / "tracking.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip())
    events = tuple(MatchEvent.model_validate_json(line) for line in (d / "events.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip())
    return Clip(clip_id, meta, frames, events)


def list_clips(root: str | None = None) -> list[str]:
    base = Path(root) if root else PUBLIC_DIR
    return sorted(p.name for p in base.iterdir() if (p / "manifest.json").is_file())
