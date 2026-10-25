"""Training segments and windows from the resampled IDSSE halves (generator-private, offline).

A segment is a run of 5 Hz frames in which the ball is in play, the same team has it and the same 22 players are
tracked without gaps. Segments end at stoppages, possession changes and substitutions, which are counted, not
bridged. Coordinates are turned so that the team in possession attacks +x (PitchIQ's canonical frame).

Agents per frame: 0-10 the team in possession (0 = its goalkeeper), 11-21 the defending team (11 = goalkeeper),
22 the ball. No names, shirt numbers, match ids or outcomes are model inputs; the split is by complete match.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Split by complete match before any window is cut (5 train / 1 validation / 1 test).
# v1 (2026-10-22): v0 tested on J03WN1, where the away team plays with 10 men from about minute 7, so only 682 frames
# passed the 11-v-11 rule (202 test windows, one 10 s autonomous start). Generated clips are always 11 v 11, so the
# model does not need to handle 10-man spells: J03WN1 moves to train (its 682 frames) and the test match becomes
# J03WOY, the smallest fully 11-v-11 match (13,599 kept frames, 45 segments long enough for 15 s rollouts).
SPLITS = {
    "train": ["DFL-MAT-J03WMX", "DFL-MAT-J03WN1", "DFL-MAT-J03WPY", "DFL-MAT-J03WQQ", "DFL-MAT-J03WR9"],
    "val": ["DFL-MAT-J03WOH"],
    "test": ["DFL-MAT-J03WOY"],
}
HIST = 15  # 3.0 s of history at 5 Hz
ROLL = 10  # 2.0 s rolled out on the model's own outputs during multi-step training
DT = 0.2
L, W = 105.0, 68.0


@dataclass
class Segment:
    match_id: str
    half: str
    start_frame: int
    xy: np.ndarray  # (T, 23, 2) attack frame
    ball_z: np.ndarray  # (T,)


def _goalkeeper_cols(persons: list[str], players: dict, side: str) -> list[int]:
    return [j for j, p in enumerate(persons) if players[p]["side"] == side and players[p]["gk"]]


def segments_for_match(npz: Path, players_json: Path, min_len: int = HIST + 1) -> tuple[list[Segment], dict]:
    d = np.load(npz)
    players = json.loads(players_json.read_text())
    mid = npz.stem
    segs: list[Segment] = []
    stats = {"frames": 0, "in_play": 0, "kept": 0, "cut_status": 0, "cut_possession": 0, "cut_roster": 0}
    for half in ("firstHalf", "secondHalf"):
        if f"{half}_frames" not in d:
            continue
        frames, pos, ball = d[f"{half}_frames"], d[f"{half}_pos"], d[f"{half}_ball"]
        poss, status, persons = d[f"{half}_possession"], d[f"{half}_status"], list(d[f"{half}_persons"])
        side = np.array([players[p]["side"] for p in persons])
        valid = ~np.isnan(pos[..., 0])
        # attack direction of the home team in this half, from its goalkeeper's mean position
        hg = _goalkeeper_cols(persons, players, "home")
        home_dir = 1 if np.nanmean(pos[:, hg[0], 0]) < L / 2 else -1
        stats["frames"] += len(frames)
        stats["in_play"] += int((status == 1).sum())

        def key(k: int):
            """What must stay constant inside a segment: possession and the exact set of tracked players."""
            if status[k] != 1 or poss[k] not in (1, 2):
                return None
            on = valid[k]
            if (on & (side == "home")).sum() != 11 or (on & (side == "away")).sum() != 11:
                return None
            return int(poss[k]), on.tobytes()

        k = 0
        while k < len(frames):
            kk = key(k)
            if kk is None:
                k += 1
                continue
            e = k + 1
            while e < len(frames) and key(e) == kk and frames[e] - frames[e - 1] == 5:
                e += 1
            if e < len(frames):
                if status[e] != 1:
                    stats["cut_status"] += 1
                elif key(e) is not None and key(e)[0] != kk[0]:
                    stats["cut_possession"] += 1
                else:
                    stats["cut_roster"] += 1
            if e - k >= min_len:
                att_side = "home" if kk[0] == 1 else "away"
                cols = np.flatnonzero(valid[k])
                att = [j for j in cols if side[j] == att_side]
                dfn = [j for j in cols if side[j] != att_side]
                gk = {j for j in cols if players[persons[j]]["gk"]}
                if len(gk & set(att)) != 1 or len(gk & set(dfn)) != 1:
                    k = e
                    continue  # needs exactly one tracked goalkeeper per team
                att.sort(key=lambda j: (j not in gk, j))
                dfn.sort(key=lambda j: (j not in gk, j))
                xy = np.concatenate([pos[k:e][:, att + dfn], ball[k:e, None, :2]], axis=1).astype(np.float32)
                a_dir = home_dir if att_side == "home" else -home_dir
                if a_dir == -1:  # rotate so the team in possession attacks +x
                    xy[..., 0] = L - xy[..., 0]
                    xy[..., 1] = W - xy[..., 1]
                if not np.isnan(xy).any():
                    segs.append(Segment(mid, half, int(frames[k]), xy, ball[k:e, 2].astype(np.float32)))
                    stats["kept"] += e - k
            k = e
    return segs, stats


def load_split(processed: Path, split: str) -> tuple[list[Segment], dict]:
    segs, stats = [], {}
    for mid in SPLITS[split]:
        s, st = segments_for_match(processed / f"{mid}.npz", processed / f"{mid}.players.json")
        segs += s
        stats[mid] = st
    return segs, stats


def windows(segs: list[Segment], length: int, stride: int) -> np.ndarray:
    """Every ``length``-step window of every segment as (N, 23, length, 2)."""
    out = [seg.xy[s:s + length] for seg in segs for s in range(0, len(seg.xy) - length + 1, stride)]
    return np.stack(out).transpose(0, 2, 1, 3).astype(np.float32)


def windows_ahead(segs: list[Segment], length: int, ahead: int, stride: int) -> tuple[np.ndarray, np.ndarray]:
    """The same windows as ``windows`` with up to ``ahead`` later frames of the same segment appended (NaN past its
    end), as (N, 23, length + ahead, 2), and how many of those later frames exist (N,). Goal-conditioned training
    draws each player's goal from them."""
    out, n_ahead = [], []
    for seg in segs:
        for s in range(0, len(seg.xy) - length + 1, stride):
            w = np.full((length + ahead, 23, 2), np.nan, dtype=np.float32)
            part = seg.xy[s:s + length + ahead]
            w[:len(part)] = part
            out.append(w)
            n_ahead.append(len(part) - length)
    return np.stack(out).transpose(0, 2, 1, 3), np.array(n_ahead, dtype=np.int64)


def phase_of(ball_x: float) -> str:
    """Reproducible phase from the ball's position in the attack frame (also computable during generation)."""
    return "buildup" if ball_x < 35.0 else ("middle" if ball_x < 70.0 else "final")
