"""Clean diagnostic views for football-plausibility review (offline; no tactical overlay of any kind).

Shows only players (shirt number, optional role tag), the ball (with height), each team's attack direction,
possession and the displayed frame's timestamp.

    PYTHONPATH=backend python evaluation/diagnostics/render.py clip clip_001 --out docs/diagnostics/current
    PYTHONPATH=backend python evaluation/diagnostics/render.py rollouts <run>/rollouts_test.npz --out <dir> [--n 4]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

from common import Clip, load_committed  # noqa: E402

COLORS = {"blue": "#2f6fde", "red": "#d6453d"}


@dataclass
class Panel:
    title: str
    xy: np.ndarray  # (T, 22, 2)
    ball: np.ndarray  # (T, 3)
    teams: list[str]  # per player
    labels: list[str]  # shirt numbers
    roles: list[str] | None
    directions: dict[str, int]  # team -> +1 / -1 along x
    possession: list[str | None]  # per frame
    t: np.ndarray  # seconds shown in the corner


def draw_pitch(ax) -> None:
    ax.set_facecolor("#2e6b34")
    kw = dict(color="white", lw=0.9, alpha=0.85)
    ax.plot([0, 105, 105, 0, 0], [0, 0, 68, 68, 0], **kw)
    ax.plot([52.5, 52.5], [0, 68], **kw)
    ax.add_patch(plt.Circle((52.5, 34), 9.15, fill=False, **kw))
    for x0, s in ((0, 1), (105, -1)):
        ax.plot([x0, x0 + s * 16.5, x0 + s * 16.5, x0], [13.84, 13.84, 54.16, 54.16], **kw)
        ax.plot([x0, x0 + s * 5.5, x0 + s * 5.5, x0], [24.84, 24.84, 43.16, 43.16], **kw)
    ax.set_xlim(-3, 108)
    ax.set_ylim(-7, 72)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def panel_from_clip(c: Clip, roles: bool = True) -> Panel:
    teams = [c.team(p) for p in c.pids]
    return Panel(c.clip_id, c.xy, c.ball, teams, [str(int(p.split("_")[1])) for p in c.pids],
                 [c.role(p) for p in c.pids] if roles else None,
                 {tm: c.direction(tm) for tm in set(teams)},
                 [r["possession_team_id"] or None for r in c.rows], c.t)


class Scene:
    """The artists of one panel, updated frame by frame."""

    def __init__(self, ax, p: Panel):
        self.p = p
        draw_pitch(ax)
        ax.set_title(p.title, fontsize=9)
        for k, (tm, d) in enumerate(sorted(p.directions.items())):
            x0, x1 = (8, 26) if d == 1 else (26, 8)
            y = -3.5 if k == 0 else 70.5
            ax.annotate("", xy=(x1 + (70 if d == 1 else 70), y), xytext=(x0 + 70, y),
                        arrowprops=dict(arrowstyle="->", color=COLORS[tm], lw=2))
            ax.text(62, y, f"{tm} attacks", color=COLORS[tm], fontsize=7, va="center", ha="right")
        self.dots = ax.scatter(p.xy[0, :, 0], p.xy[0, :, 1], s=120, c=[COLORS[t] for t in p.teams],
                               edgecolors="white", linewidths=0.8, zorder=3)
        self.nums = [ax.text(0, 0, lab, color="white", fontsize=6, ha="center", va="center", zorder=4,
                             fontweight="bold") for lab in p.labels]
        self.roles = [ax.text(0, 0, r, color="#f2f2f2", fontsize=5, ha="center", va="top", zorder=4)
                      for r in p.roles] if p.roles else []
        self.ball = ax.scatter([0], [0], s=30, c="white", edgecolors="black", linewidths=0.6, zorder=5)
        self.shadow = ax.scatter([0], [0], s=18, c="black", alpha=0.35, zorder=2)
        self.info = ax.text(1, -6.2, "", color="black", fontsize=7, ha="left", va="center",
                            bbox=dict(facecolor="white", alpha=0.8, lw=0))

    def update(self, k: int) -> None:
        p = self.p
        self.dots.set_offsets(p.xy[k])
        for i, t in enumerate(self.nums):
            t.set_position(p.xy[k, i])
        for i, t in enumerate(self.roles):
            t.set_position((p.xy[k, i, 0], p.xy[k, i, 1] - 1.6))
        bx, by, bz = p.ball[k]
        self.ball.set_offsets([[bx, by + 0.3 * bz]])
        self.ball.set_sizes([30 + 4 * bz])
        self.shadow.set_offsets([[bx, by]])
        poss = p.possession[k]
        self.info.set_text(f"t = {p.t[k]:5.1f} s   frame {k}   possession: {poss or 'none'}")


def frames_png(p: Panel, picks: list[tuple[str, int]], out: Path) -> None:
    fig, axes = plt.subplots(1, len(picks), figsize=(6.2 * len(picks), 4.4))
    for ax, (name, k) in zip(np.atleast_1d(axes), picks):
        s = Scene(ax, Panel(f"{p.title} - {name}", p.xy, p.ball, p.teams, p.labels, p.roles, p.directions,
                            p.possession, p.t))
        s.update(k)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)


def video_gif(panels: list[Panel], out: Path, fps: int = 5, every: int = 1) -> None:
    fig, axes = plt.subplots(1, len(panels), figsize=(6.2 * len(panels), 4.4))
    scenes = [Scene(ax, p) for ax, p in zip(np.atleast_1d(axes), panels)]
    n = min(len(p.xy) for p in panels)
    fig.tight_layout()

    def upd(k):
        for s in scenes:
            s.update(k)
        return []

    anim = FuncAnimation(fig, upd, frames=range(0, n, every), blit=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    anim.save(out, writer=PillowWriter(fps=fps), dpi=70)
    plt.close(fig)


def story_picks(c: Clip) -> list[tuple[str, int]]:
    """Before / during / after from the private story window and the last ball release inside it."""
    w = (c.truth.get("story") or {}).get("window_clip_s") or [c.t[0], c.t[-1]]
    rel = [v - c.start_s for k, v in c.truth.get("markers_match_s", {}).items()
           if k.endswith("_release_t") and not k.startswith("shot") and isinstance(v, float)]
    during = max([t for t in rel if w[0] <= t <= w[1]], default=(w[0] + w[1]) / 2)
    k = lambda t: int(np.clip(round(t / 0.2), 0, len(c.t) - 1))  # noqa: E731
    return [("before", k(w[0])), ("during", k(during)), ("after", k(min(w[1] + 1.0, c.t[-1])))]


def rollout_panels(npz: Path, i: int) -> list[Panel]:
    d = np.load(npz)
    start, real, model, ball = d["start"][i], d["real"][i], d["model"][i], d["ball"][i]
    T = real.shape[1]
    ball_xy = ball[-T:] if len(ball) >= T else ball
    b3 = np.concatenate([ball_xy, np.zeros((len(ball_xy), 1))], axis=1)
    teams = ["blue"] * 11 + ["red"] * 11
    labels = ["GK"] + [str(j) for j in range(2, 12)] + ["GK"] + [str(j) for j in range(2, 12)]
    t = np.arange(T) * 0.2
    poss = ["blue (in possession)"] * T
    dirs = {"blue": 1, "red": -1}
    real_xy = real.transpose(1, 0, 2)
    gen_xy = model.transpose(1, 0, 2)
    return [Panel(f"observed (held-out match) #{i}", real_xy, b3, teams, labels, None, dirs, poss, t),
            Panel(f"model rollout, own outputs #{i}", gen_xy, b3, teams, labels, None, dirs, poss, t)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("clip")
    a.add_argument("clips", nargs="+")
    a.add_argument("--out", type=Path, required=True)
    b = sub.add_parser("rollouts")
    b.add_argument("npz", type=Path)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--n", type=int, default=4)
    args = ap.parse_args(argv)
    if args.cmd == "clip":
        for cid in args.clips:
            c = load_committed(cid)
            frames_png(panel_from_clip(c), story_picks(c), args.out / f"{cid}_before_during_after.png")
            video_gif([panel_from_clip(c, roles=False)], args.out / f"{cid}.gif")
            print("wrote", cid)
    else:
        d = np.load(args.npz)
        n = d["real"].shape[0]
        for i in np.linspace(0, n - 1, min(args.n, n)).astype(int):
            video_gif(rollout_panels(args.npz, int(i)), args.out / f"rollout_{i:03d}_observed_vs_model.gif")
            print("wrote rollout", i)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
