"""Dev-only visual check of a generated clip (offline tool, not part of the app).

    python -m app.simulator.plot data/fixtures/scenario_a c01 --out /tmp/c01.png [--highlight blue_02,blue_07]

Draws the pitch, all 22 trajectories (faint), highlighted trajectories, the
ball path and snapshots at four instants. Reads only public files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .validate import load_events, load_tracking  # noqa: E402


def draw_pitch(ax) -> None:
    ax.set_facecolor("#2e7d32")
    kw = dict(color="white", lw=1, alpha=0.8)
    ax.plot([0, 105, 105, 0, 0], [0, 0, 68, 68, 0], **kw)
    ax.plot([52.5, 52.5], [0, 68], **kw)
    ax.add_patch(plt.Circle((52.5, 34), 9.15, fill=False, **kw))
    for x0, s in ((0, 1), (105, -1)):
        ax.plot([x0, x0 + s * 16.5, x0 + s * 16.5, x0], [13.85, 13.85, 54.15, 54.15], **kw)
        ax.plot([x0, x0 + s * 5.5, x0 + s * 5.5, x0], [24.85, 24.85, 43.15, 43.15], **kw)
    ax.set_xlim(-2, 107)
    ax.set_ylim(-2, 70)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def plot_clip(scenario_dir: Path, clip_id: str, out: Path, highlight: list[str]) -> None:
    man = json.loads((scenario_dir / "manifest.json").read_text())
    clip = next(c for c in man["clips"] if c["clip_id"] == clip_id)
    frames = load_tracking(scenario_dir / clip["tracking_file"])
    events = [e for e in load_events(scenario_dir / man["events_file"]) if e["clip_id"] == clip_id]
    colors = {t["team_id"]: t["color"] for t in man["teams"]}
    pids = [p["player_id"] for p in frames[0]["players"]]
    traj = {pid: np.array([[p["position"]["x_m"], p["position"]["y_m"]] for f in frames for p in f["players"]
                           if p["player_id"] == pid]) for pid in pids}
    ball = np.array([[f["ball_position"]["x_m"], f["ball_position"]["y_m"]] for f in frames])
    team_of = {p["player_id"]: p["team_id"] for p in frames[0]["players"]}
    role_of = {p["player_id"]: p["nominal_role"] for p in frames[0]["players"]}

    snaps = np.linspace(0, len(frames) - 1, 4).astype(int)
    fig, axes = plt.subplots(1, 5, figsize=(30, 5.6))
    ax = axes[0]
    draw_pitch(ax)
    for pid, tr in traj.items():
        hl = pid in highlight
        ax.plot(tr[:, 0], tr[:, 1], color=colors[team_of[pid]], lw=2.4 if hl else 0.8, alpha=1.0 if hl else 0.45)
        ax.scatter(*tr[-1], color=colors[team_of[pid]], s=40 if hl else 12, edgecolor="white", zorder=3)
        if hl:
            ax.annotate(role_of[pid], tr[-1], color="white", fontsize=8, xytext=(3, 3), textcoords="offset points")
    ax.plot(ball[:, 0], ball[:, 1], color="white", lw=1, ls="--")
    ax.set_title(f"{man['scenario_id']}/{clip_id} trajectories ({len(frames)} frames, dir={clip['attack_direction']})")
    for ax, k in zip(axes[1:], snaps):
        draw_pitch(ax)
        f = frames[k]
        for p in f["players"]:
            x, y = p["position"]["x_m"], p["position"]["y_m"]
            ax.scatter(x, y, color=colors[p["team_id"]], s=70 if p["player_id"] in highlight else 35,
                       edgecolor="white", zorder=3)
            ax.annotate(p["nominal_role"], (x, y), color="white", fontsize=6, xytext=(2, 2), textcoords="offset points")
        ax.scatter(f["ball_position"]["x_m"], f["ball_position"]["y_m"], color="yellow", s=25, zorder=4)
        evs = [e["event_type"] for e in events if abs(e["match_time_s"] - f["match_time_s"]) < 0.11]
        ax.set_title(f"t={f['clip_time_s']:.1f}s owner={f['ball_owner_id']} {evs}")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=70)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario_dir", type=Path)
    ap.add_argument("clip_id")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--highlight", type=str, default="")
    args = ap.parse_args(argv)
    plot_clip(args.scenario_dir, args.clip_id, args.out, [h for h in args.highlight.split(",") if h])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
