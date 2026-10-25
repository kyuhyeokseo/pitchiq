"""Validate a public scenario directory against the PitchIQ data contract.

    python -m app.simulator.validate data/fixtures/scenario_a
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..ingestion.loader import read_jsonl, read_jsonl_gz
from ..privacy import find_private_keys, find_private_tokens
from ..schemas import (
    FRAME_DT_S,
    MAX_BALL_SPEED_MPS,
    MAX_PLAYER_SPEED_MPS,
    MatchEvent,
    ScenarioManifest,
    TrackingFrame,
)
from .recipes import load_recipes
from .roster import FORMATION_LINES

MIN_FRAMES, MAX_FRAMES = 60, 125
SPEED_TOL = 0.1  # positions are rounded to 1 cm
OWNED_BALL_MAX_DIST_M = 3.0


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        s = ", ".join(f"{k}={v}" for k, v in self.stats.items() if not isinstance(v, dict))
        return f"{'OK' if self.ok else 'FAILED'} ({len(self.errors)} errors) {s}"


load_tracking = read_jsonl_gz
load_events = read_jsonl


def private_vocabulary(cfg: dict) -> set[str]:
    vocab = set(cfg["recipes"])
    for r in cfg["recipes"].values():
        vocab.add(r["generator_intent"])
        vocab.update(r["defender_response"])
    vocab.update(cfg["defense"]["profiles"])
    # Public fixtures are pure observations: no pattern words at all.
    vocab.update({"overlap", "underlap"})
    return vocab


def _dist(a: dict, b: dict) -> float:
    return math.hypot(a["x_m"] - b["x_m"], a["y_m"] - b["y_m"])


def validate_scenario_dir(scenario_dir: Path | str, truth_path: Path | None = None,
                          private_vocab_cfg: dict | None = None) -> ValidationReport:
    d = Path(scenario_dir)
    rep = ValidationReport()
    err = rep.errors.append
    manifest_text = (d / "manifest.json").read_text(encoding="utf-8")
    manifest = ScenarioManifest.model_validate_json(manifest_text)
    roster = {r.player_id: r for r in manifest.roster}
    vocab = private_vocabulary(private_vocab_cfg or load_recipes())

    for team in manifest.teams:
        lines = FORMATION_LINES.get(team.nominal_formation)
        if lines is None:
            err(f"team {team.team_id}: unknown formation {team.nominal_formation}")
            continue
        flat = [r for line in lines for r in line]
        if len(flat) != 10 or len(set(flat)) != 10 or "GK" in flat:
            err(f"formation {team.nominal_formation}: lines must hold 10 unique outfield roles")

    texts = [manifest_text]
    frames_by_time: dict[float, dict] = {}
    clip_bounds: dict[str, tuple[float, float]] = {}
    n_frames_total = 0
    max_player_speed = 0.0
    max_ball_speed = 0.0
    for clip in manifest.clips:
        path = d / clip.tracking_file
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != clip.tracking_sha256:
            err(f"{clip.clip_id}: tracking checksum mismatch")
        raw = gzip.decompress(blob).decode("utf-8")
        texts.append(raw)
        frames = [json.loads(line) for line in raw.splitlines() if line.strip()]
        clip_bounds[clip.clip_id] = (clip.start_match_s, clip.end_match_s)
        n_frames_total += len(frames)
        if len(frames) != clip.n_frames:
            err(f"{clip.clip_id}: n_frames {len(frames)} != manifest {clip.n_frames}")
        if not MIN_FRAMES <= len(frames) <= MAX_FRAMES:
            err(f"{clip.clip_id}: {len(frames)} frames outside [{MIN_FRAMES}, {MAX_FRAMES}]")
        prev = None
        for i, fr in enumerate(frames):
            tag = f"{clip.clip_id}#{i}"
            try:
                TrackingFrame.model_validate(fr)
            except ValidationError as e:
                err(f"{tag}: schema {e.errors()[0]['msg']}")
                continue
            if fr["frame_index"] != i:
                err(f"{tag}: frame_index {fr['frame_index']}")
            if abs(fr["clip_time_s"] - i * FRAME_DT_S) > 1e-3:
                err(f"{tag}: clip_time_s {fr['clip_time_s']}")
            if abs(fr["match_time_s"] - (clip.start_match_s + i * FRAME_DT_S)) > 1e-3:
                err(f"{tag}: match_time_s not on 5 Hz grid")
            if fr["match_id"] != manifest.match_id or fr["clip_id"] != clip.clip_id:
                err(f"{tag}: match/clip id mismatch")
            if fr["attack_direction"] != clip.attack_direction or fr["period"] != clip.period:
                err(f"{tag}: period/attack_direction differ from manifest")
            for pl in fr["players"]:
                r = roster.get(pl["player_id"])
                if r is None or r.team_id != pl["team_id"] or r.nominal_role != pl["nominal_role"]:
                    err(f"{tag}: player {pl['player_id']} inconsistent with roster")
            if fr["ball_owner_id"] is not None:
                owner = next(p for p in fr["players"] if p["player_id"] == fr["ball_owner_id"])
                if _dist(owner["position"], fr["ball_position"]) > OWNED_BALL_MAX_DIST_M:
                    err(f"{tag}: ball {_dist(owner['position'], fr['ball_position']):.1f} m from its owner")
            if prev is not None:
                if fr["match_time_s"] <= prev["match_time_s"]:
                    err(f"{tag}: non-monotonic time")
                pp = {p["player_id"]: p["position"] for p in prev["players"]}
                for pl in fr["players"]:
                    v = _dist(pl["position"], pp[pl["player_id"]]) / FRAME_DT_S
                    max_player_speed = max(max_player_speed, v)
                    if v > MAX_PLAYER_SPEED_MPS + SPEED_TOL:
                        err(f"{tag}: {pl['player_id']} speed {v:.2f} m/s")
                vb = _dist(fr["ball_position"], prev["ball_position"]) / FRAME_DT_S
                max_ball_speed = max(max_ball_speed, vb)
                if vb > MAX_BALL_SPEED_MPS + SPEED_TOL:
                    err(f"{tag}: ball speed {vb:.2f} m/s")
            frames_by_time[round(fr["match_time_s"], 3)] = fr
            prev = fr
        hits = find_private_keys(frames[0]) if frames else []
        if hits:
            err(f"{clip.clip_id}: private keys {hits}")

    events_text = (d / manifest.events_file).read_text(encoding="utf-8")
    if hashlib.sha256(events_text.encode("utf-8")).hexdigest() != manifest.events_sha256:
        err("events checksum mismatch")
    texts.append(events_text)
    events = [json.loads(line) for line in events_text.splitlines() if line.strip()]
    ids = Counter(e["event_id"] for e in events)
    for eid, n in ids.items():
        if n > 1:
            err(f"duplicate event id {eid}")
    by_id = {e["event_id"]: e for e in events}
    last_t = -1.0
    for e in events:
        tag = e.get("event_id", "?")
        try:
            MatchEvent.model_validate(e)
        except ValidationError as ex:
            err(f"{tag}: schema {ex.errors()[0]['msg']}")
            continue
        if e["match_time_s"] < last_t:
            err(f"{tag}: events not sorted by time")
        last_t = e["match_time_s"]
        if e["match_id"] != manifest.match_id:
            err(f"{tag}: match id mismatch")
        bounds = clip_bounds.get(e["clip_id"])
        if bounds is None:
            err(f"{tag}: unknown clip {e['clip_id']}")
            continue
        if not bounds[0] - 1e-6 <= e["match_time_s"] <= bounds[1] + 1e-6:
            err(f"{tag}: event outside its clip interval (after final frame or before first)")
        unknown = [k for k in ("actor_id", "target_id") if e[k] is not None and e[k] not in roster]
        for key in unknown:
            err(f"{tag}: unknown {key} {e[key]}")
        if unknown:
            continue
        if e["actor_id"] is not None and e["team_id"] != roster[e["actor_id"]].team_id:
            err(f"{tag}: team_id does not match actor")
        fr = frames_by_time.get(round(e["match_time_s"], 3))
        if fr is None:
            err(f"{tag}: no tracking frame at event time")
            continue
        if e["event_type"] == "pass":
            if fr["ball_owner_id"] != e["actor_id"] or _dist(fr["ball_position"], e["start"]) > 0.05:
                err(f"{tag}: pass start disagrees with tracking")
        elif e["event_type"] == "receive":
            if fr["ball_owner_id"] != e["actor_id"]:
                err(f"{tag}: receiver does not own the ball at receive time")
            src = by_id.get(e["context"].get("pass_event_id"))
            if src is None or src["target_id"] != e["actor_id"] or src["match_time_s"] >= e["match_time_s"]:
                err(f"{tag}: receive not linked to an earlier pass to this player")
        elif e["event_type"] in ("carry",):
            if fr["ball_owner_id"] != e["actor_id"]:
                err(f"{tag}: carry actor does not own the ball")
        elif e["event_type"] == "pressure":
            if fr["ball_owner_id"] != e["target_id"] or roster[e["target_id"]].team_id == e["team_id"]:
                err(f"{tag}: pressure must target the opposing ball owner")
        if e["team_id"] is not None and fr["possession_team_id"] not in (None, e["team_id"]) and e["event_type"] in ("pass", "receive", "carry"):
            err(f"{tag}: possession mismatch")

    hits = find_private_keys(json.loads(manifest_text)) + find_private_keys(events)
    if hits:
        err(f"private keys in public payloads: {hits[:5]}")
    leaked = sorted({tok for txt in texts for tok in find_private_tokens(txt, vocab)})
    if leaked:
        err(f"private vocabulary found in public files: {leaked}")

    if truth_path is not None:
        tp = Path(truth_path).resolve()
        if d.resolve() in tp.parents:
            err("private truth must not live inside the public scenario directory")
        rows = [json.loads(line) for line in tp.read_text(encoding="utf-8").splitlines() if line.strip()]
        if sorted(r["clip_id"] for r in rows) != sorted(c.clip_id for c in manifest.clips):
            err("private truth clip ids do not match manifest clips")

    rep.stats = {
        "clips": len(manifest.clips),
        "frames": n_frames_total,
        "events": len(events),
        "max_player_speed_mps": round(max_player_speed, 2),
        "max_ball_speed_mps": round(max_ball_speed, 2),
        "event_types": dict(Counter(e["event_type"] for e in events)),
    }
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario_dir", type=Path)
    ap.add_argument("--truth", type=Path, default=None)
    args = ap.parse_args(argv)
    rep = validate_scenario_dir(args.scenario_dir, args.truth)
    print(rep.summary())
    print(f"event_types={rep.stats.get('event_types')}")
    for e in rep.errors[:50]:
        print("ERROR", e)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
