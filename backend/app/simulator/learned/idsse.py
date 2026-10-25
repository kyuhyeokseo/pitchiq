"""IDSSE open tracking data: parse, resample and audit (generator-private, offline only).

Source: Bassek, Rein, Weber & Memmert (2025), "An integrated dataset of spatiotemporal and event data in elite
soccer", Scientific Data 12:195, data at https://doi.org/10.6084/m9.figshare.28196177, licensed CC BY 4.0 with the
DFL's authorisation. Changes made here: referee tracks and names dropped, 25 Hz resampled to 5 Hz, coordinates moved
from the centre-origin frame to PitchIQ's 105 x 68 bottom-left frame. The real data never enters data/public and the
serving app never imports this module.

    PYTHONPATH=backend python -m app.simulator.learned.idsse --raw <raw dir> --out <processed dir> [--jobs 7]
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import numpy as np

STEP = 5  # 25 Hz -> 5 Hz (PitchIQ's tracking rate)
HALVES = ("firstHalf", "secondHalf")


def match_info(path: Path) -> dict:
    """Teams, pitch size and per-player team / starter / goalkeeper flags. Names are deliberately not read."""
    root = ET.parse(path).getroot()
    g = root.find(".//General").attrib
    env = root.find(".//Environment").attrib
    players = {}
    for team in root.iter("Team"):
        tid, role = team.attrib["TeamId"], team.attrib["Role"]
        for p in team.iter("Player"):
            a = p.attrib
            players[a["PersonId"]] = {"team": tid, "side": "home" if role == "home" else "away",
                                      "starting": a.get("Starting") == "true",
                                      "gk": a.get("PlayingPosition") == "TW", "position": a.get("PlayingPosition"),
                                      "shirt": int(a["ShirtNumber"])}
    return {"match_id": g["MatchId"], "competition": g["CompetitionName"], "season": g["Season"],
            "home": g["HomeTeamId"], "away": g["GuestTeamId"], "lineup": {t.attrib["Role"]: t.attrib.get("LineUp")
                                                                          for t in root.iter("Team")},
            "pitch": (float(env["PitchX"]), float(env["PitchY"])), "players": players}


def parse_positions(path: Path, info: dict) -> dict:
    """Every object's 25 Hz frames per half, kept only at 5 Hz, plus 25 Hz counts for the audit."""
    raw: dict[tuple[str, str], dict[int, tuple]] = {}
    counts: Counter = Counter()
    cur = None
    for ev, el in ET.iterparse(path, events=("start", "end")):
        if ev == "start" and el.tag == "FrameSet":
            a = el.attrib
            cur = (a["GameSection"], a["PersonId"] if a["TeamId"].lower() != "ball" else "BALL", a["TeamId"])
            raw.setdefault(cur[:2], {})
        elif ev == "end" and el.tag == "Frame":
            if cur[2] != "referee":
                a = el.attrib
                n = int(a["N"])
                counts[cur[:2]] += 1
                if n % STEP == 0:
                    if cur[1] == "BALL":
                        raw[cur[:2]][n] = (float(a["X"]), float(a["Y"]), float(a.get("Z", 0.0)),
                                           int(a.get("BallPossession", 0)), int(a.get("BallStatus", 0)))
                    else:
                        raw[cur[:2]][n] = (float(a["X"]), float(a["Y"]))
            el.clear()
        elif ev == "end" and el.tag == "FrameSet":
            el.clear()
    L, W = info["pitch"]
    out = {}
    for half in HALVES:
        ball = raw.get((half, "BALL"), {})
        if not ball:
            continue
        frames = np.array(sorted(ball))
        persons = sorted((pid for (h, pid) in raw if h == half and pid != "BALL" and pid in info["players"]),
                         key=lambda p: (info["players"][p]["side"] != "home", p))
        idx = {n: k for k, n in enumerate(frames)}
        pos = np.full((len(frames), len(persons), 2), np.nan, dtype=np.float32)
        for j, pid in enumerate(persons):
            for n, (x, y) in raw[(half, pid)].items():
                k = idx.get(n)
                if k is not None:
                    pos[k, j] = (x + L / 2, y + W / 2)
        b = np.array([ball[n] for n in frames], dtype=np.float32)
        out[half] = {"frames": frames, "persons": persons, "pos": pos,
                     "ball": np.stack([b[:, 0] + L / 2, b[:, 1] + W / 2, b[:, 2]], axis=1),
                     "possession": b[:, 3].astype(np.int8), "status": b[:, 4].astype(np.int8),
                     "frames25": {pid: counts[(half, pid)] for pid in persons + ["BALL"]}}
    return out


def parse_events(path: Path) -> Counter:
    """Event type counts (the first child of each Event): used for the audit, not as movement data."""
    c: Counter = Counter()
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag == "Event":
            kids = list(el)
            c[kids[0].tag if kids else "(none)"] += 1
            el.clear()
    return c


def audit_half(h: dict, info: dict) -> dict:
    pos, persons = h["pos"], h["persons"]
    valid = ~np.isnan(pos[..., 0])
    teams = [info["players"][p]["side"] for p in persons]
    in_play = h["status"] == 1
    per_team = {s: valid[:, [j for j, t in enumerate(teams) if t == s]].sum(axis=1) for s in ("home", "away")}
    gaps = 0
    for j in range(len(persons)):
        ks = np.flatnonzero(valid[:, j])
        if len(ks):
            gaps += int((~valid[ks[0]:ks[-1] + 1, j]).sum())
    gk_cols = [j for j, p in enumerate(persons) if info["players"][p]["gk"]]
    home_gk = [j for j in gk_cols if teams[j] == "home"]
    home_gk_x = float(np.nanmean(pos[:, home_gk[0], 0])) if home_gk else float("nan")
    return {
        "frames_5hz": int(len(h["frames"])), "frames_25hz_ball": int(h["frames25"]["BALL"]),
        "duration_min": round(len(h["frames"]) * STEP / 25 / 60, 1),
        "in_play_share": round(float(in_play.mean()), 3),
        "possession_known_in_play": round(float((h["possession"][in_play] > 0).mean()), 3),
        "persons_tracked": {s: sum(t == s for t in teams) for s in ("home", "away")},
        "max_on_pitch": {s: int(v.max()) for s, v in per_team.items()},
        "frames_with_not_11": {s: int((v != 11).sum()) for s, v in per_team.items()},
        "mid_track_gap_frames": gaps,
        "goalkeepers_tracked": len(gk_cols),
        "home_attacks": "+x" if home_gk_x < 52.5 else "-x",
        "ball_missing_frames": int(np.isnan(h["ball"][:, 0]).sum()),
    }


def process(args: tuple[Path, Path, Path, Path]) -> dict:
    info_p, pos_p, ev_p, out_dir = args
    info = match_info(info_p)
    halves = parse_positions(pos_p, info)
    events = parse_events(ev_p)
    mid = info["match_id"]
    arrays = {}
    for half, h in halves.items():
        for k in ("frames", "pos", "ball", "possession", "status"):
            arrays[f"{half}_{k}"] = h[k]
        arrays[f"{half}_persons"] = np.array(h["persons"])
    np.savez_compressed(out_dir / f"{mid}.npz", **arrays)
    meta = {pid: {k: v for k, v in p.items()} for pid, p in info["players"].items()}
    (out_dir / f"{mid}.players.json").write_text(json.dumps(meta, indent=1))
    return {"match_id": mid, "competition": info["competition"], "season": info["season"],
            "pitch_m": info["pitch"], "lineups": info["lineup"],
            "players_listed": len(info["players"]), "events": dict(events.most_common()),
            "halves": {half: audit_half(h, info) for half, h in halves.items()}}


FIGSHARE_ARTICLE = "https://api.figshare.com/v2/articles/28196177"


def fetch(raw: Path) -> None:
    """Download the 21 IDSSE files from figshare into ``raw`` and check every file against figshare's MD5."""
    import hashlib
    import urllib.request

    raw.mkdir(parents=True, exist_ok=True)
    meta = json.loads(urllib.request.urlopen(FIGSHARE_ARTICLE, timeout=60).read())
    (raw / "figshare_article_28196177.json").write_text(json.dumps(meta, indent=1))
    lines = []
    for f in meta["files"]:
        dst = raw / f["name"]
        if not (dst.exists() and dst.stat().st_size == f["size"]):
            urllib.request.urlretrieve(f["download_url"], dst)
        h = hashlib.md5()
        with dst.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        ok = h.hexdigest() == f["computed_md5"]
        lines.append(f'{f["name"]}  {f["size"]}  md5 {h.hexdigest()}  {"OK" if ok else "MISMATCH"}')
        if not ok:
            raise SystemExit(f"MD5 mismatch for {f['name']}")
    (raw / "MD5_VERIFIED.txt").write_text("\n".join(lines) + "\n")
    print(f"fetched and verified {len(lines)} files into {raw}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download from figshare first (MD5-checked)")
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=7)
    args = ap.parse_args(argv)
    if args.fetch:
        fetch(args.raw)
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for info_p in sorted(args.raw.glob("DFL_02_01_matchinformation_*.xml")):
        mid = info_p.stem.split("_")[-1]
        jobs.append((info_p, next(args.raw.glob(f"DFL_04_03_positions_raw_observed_*_{mid}.xml")),
                     next(args.raw.glob(f"DFL_03_02_events_raw_*_{mid}.xml")), args.out))
    with Pool(min(args.jobs, len(jobs))) as pool:
        audits = pool.map(process, jobs)
    (args.out / "audit.json").write_text(json.dumps(audits, indent=1))
    for a in audits:
        for half, h in a["halves"].items():
            print(a["match_id"], a["competition"][:14], half, h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
