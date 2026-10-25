"""Train and evaluate the shared GRU + GNN movement model on IDSSE (generator-private, offline, compute node).

    PYTHONPATH=backend python -m app.simulator.learned.train --processed <dir> --out <run dir>

Stage 1 learns one-step velocity changes from observed history. Stage 2 is multi-step rollout training: the model
runs ROLL steps (2 s) on its own player outputs, with the observed ball as the context node, and the loss covers
every step. Early stopping on the validation rollout error; AdamW weight decay and a left-right flip as the only
regularisation. Feature scales come from the training matches only.

With --stochastic the model also predicts a Laplace scale: stage 1 minimises the Laplace negative log-likelihood of the
velocity change, stage 2 keeps a small share of it next to the rollout loss (on the location) so the scale stays
calibrated, and the autonomous rollouts are sampled (the location-only rollout is reported next to them).

With --goal the model is goal-conditioned: each player gets a goal, his real position 1-4 s after the last history
step (stage 2: one that is still 1.2-3.2 s away at the rollout's last step) and the seconds left to it, except for a
--goal-drop share of players, who get none so the model also moves players without one. Validation and test report
the errors with goals (fixed draws) and without. The autonomous rollouts refresh each player's goal every 2 s from what
really happened, as the scenario controller refreshes its targets in generation.

Held-out test match: one-step error, 1 s / 2 s rollout errors by phase, and autonomous 15 s rollouts summarised next
to the same statistics of what really happened. Disclosed conditioning: the ball is a context node whose path is
supplied (observed path in evaluation, the scenario controller's ball in generation).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import torch

from .dataset import HIST, ROLL, SPLITS, load_split, phase_of, windows, windows_ahead
from .model import DT, MotionGNN, rollout, step

LONG_STEPS = 75  # 15 s autonomous rollouts on the test match
GOAL_STEPS_1 = (5, 20)  # stage 1: the goal is 1-4 s after the last history step
GOAL_STEPS_2 = (ROLL + 5, ROLL + 15)  # stage 2: still 1.2-3.2 s away at the rollout's last step


def flip_y(x: torch.Tensor) -> torch.Tensor:
    y = x.clone()
    y[..., 1] = 68.0 - y[..., 1]
    return y


def true_dv(x: torch.Tensor) -> torch.Tensor:
    v0 = (x[:, :22, HIST - 1] - x[:, :22, HIST - 2]) / DT
    v1 = (x[:, :22, HIST] - x[:, :22, HIST - 1]) / DT
    return v1 - v0


def draw_goals(xf: torch.Tensor, avail: torch.Tensor, lo: int, hi: int, drop: float, steps: int = 1,
               generator: torch.Generator | None = None) -> torch.Tensor:
    """Goals from what really happened. xf (B, 23, T, 2) windows with their later frames; avail (B,) how many frames
    exist after the last history step. Per player: his position h steps after it, h uniform in [lo, min(hi, avail)],
    or no goal with probability ``drop`` (and for everyone when fewer than ``lo`` frames exist). Returns
    (B, 22, steps, 4) rows [x, y, seconds left, on], the seconds counting down over the steps."""
    B, dev = xf.shape[0], xf.device
    top = avail.clamp(max=hi)
    h = lo + (torch.rand(B, 22, generator=generator, device=dev) * (top - lo + 1).clamp_min(1)[:, None]).long()
    h = torch.minimum(h, top.clamp_min(lo)[:, None])
    on = (top >= lo)[:, None] & (torch.rand(B, 22, generator=generator, device=dev) >= drop)
    idx = (HIST - 1 + h).clamp(max=xf.shape[2] - 1)
    g = torch.gather(xf[:, :22], 2, idx[:, :, None, None].expand(-1, -1, 1, 2))[:, :, 0]
    tau = (h[:, :, None] - torch.arange(steps, device=dev)) * DT
    goal = torch.cat([g[:, :, None].expand(-1, -1, steps, -1), tau[..., None],
                      torch.ones_like(tau)[..., None]], dim=-1)
    return torch.where(on[:, :, None, None], goal, torch.zeros_like(goal))


def laplace_nll(model: MotionGNN, x: torch.Tensor, goal: torch.Tensor | None = None) -> torch.Tensor:
    """Laplace negative log-likelihood of the next velocity change, in units of the training dv scale."""
    loc, scale = model.dist(x[:, :, :HIST], goal)
    b = scale / model.dv_std
    return (torch.log(b) + (true_dv(x) - loc).abs() / model.dv_std / b).mean()


def rollout_loss(model: MotionGNN, x: torch.Tensor, steps: int, goals: torch.Tensor | None = None) -> torch.Tensor:
    pred = rollout(model, x[:, :, :HIST], x[:, 22], steps, goals=goals)
    e = torch.linalg.norm(pred - x[:, :22, HIST:HIST + steps], dim=-1)
    return e.mean() + 0.5 * e[..., -1].mean()


def evaluate(model: MotionGNN, one: torch.Tensor, roll: torch.Tensor, batch: int = 512,
             goals_one: torch.Tensor | None = None, goals_roll: torch.Tensor | None = None) -> dict:
    """One-step errors on (N, 23, HIST + 1, 2) and 1 s / 2 s rollout errors on (N, 23, HIST + ROLL, 2), with the
    given goals (N, 22, 1, 4) and (N, 22, ROLL, 4) for a goal-conditioned model; it is also scored without goals."""
    model.eval()
    out: dict = {}
    if goals_roll is not None:
        out["goal_share"] = round(float(goals_roll[:, :, 0, 3].mean()), 3)
        free = evaluate(model, one, roll, batch)
        out["no_goal"] = {"one_step": free["one_step"], "rollout_all": free["rollout_all"]}

    def gi(goals, i, k=None):
        return None if goals is None else (goals[i:i + batch] if k is None else goals[i:i + batch, :, k])

    with torch.no_grad():
        pe, ve = [], []
        for i in range(0, len(one), batch):
            x = one[i:i + batch]
            dv = model(x[:, :, :HIST], gi(goals_one, i, 0))
            pe.append(torch.linalg.norm(step(x[:, :, :HIST], dv) - x[:, :22, HIST], dim=-1).mean(1).cpu())
            ve.append(torch.linalg.norm(dv - true_dv(x), dim=-1).mean(1).cpu())
        out["one_step"] = {"pos_err_m": round(float(torch.cat(pe).mean()), 4),
                           "dv_err_mps": round(float(torch.cat(ve).mean()), 4), "n": int(len(one))}
        rows = []
        for i in range(0, len(roll), batch):
            x = roll[i:i + batch]
            e = torch.linalg.norm(rollout(model, x[:, :, :HIST], x[:, 22], ROLL, goals=gi(goals_roll, i))
                                  - x[:, :22, HIST:], dim=-1)
            e = e.cpu().numpy()
            bx = x[:, 22, HIST - 1, 0].cpu().numpy()
            for b in range(len(x)):
                rows.append((phase_of(float(bx[b])), e[b, :, :5].mean(), e[b, :, 4].mean(), e[b].mean(), e[b, :, -1].mean(),
                             e[b, :11].mean(), e[b, 11:].mean()))
    for ph in ("all", "buildup", "middle", "final"):
        r = np.array([x[1:] for x in rows if ph == "all" or x[0] == ph])
        if len(r):
            out[f"rollout_{ph}"] = {
                "n": len(r), "ade_1s": round(float(r[:, 0].mean()), 3), "fde_1s": round(float(r[:, 1].mean()), 3),
                "ade_2s": round(float(r[:, 2].mean()), 3), "fde_2s": round(float(r[:, 3].mean()), 3),
                "ade_2s_team_in_possession": round(float(r[:, 4].mean()), 3),
                "ade_2s_defending_team": round(float(r[:, 5].mean()), 3)}
    if model.stochastic:  # one sampled rollout per window (fixed seed): what generation does
        g = torch.Generator(device=roll.device).manual_seed(0)
        e = []
        with torch.no_grad():
            for i in range(0, len(roll), batch):
                x = roll[i:i + batch]
                p = rollout(model, x[:, :, :HIST], x[:, 22], ROLL, sample=True, generator=g, goals=gi(goals_roll, i))
                e.append(torch.linalg.norm(p - x[:, :22, HIST:], dim=-1).cpu())
        e = torch.cat(e)
        out["rollout_all_sampled"] = {"n": int(len(e)), "ade_2s": round(float(e.mean()), 3),
                                      "fde_2s": round(float(e[:, :, -1].mean()), 3)}
    return out


def shape_stats(xy: np.ndarray) -> dict:
    """xy (N, 22, T, 2) attack frame: speed/acceleration, width/depth, back line to midfield, persistent clustering."""
    v = np.diff(xy, axis=2) / DT
    sp = np.linalg.norm(v, axis=-1)
    acc = np.linalg.norm(np.diff(v, axis=2), axis=-1) / DT
    att, dfn = xy[:, 1:11], xy[:, 12:22]  # outfield players
    srt = np.sort(dfn[..., 0], axis=1)  # defending team (attacks -x): four deepest = back line, next three = midfield
    line_gap = srt[:, -4:].mean(axis=1) - srt[:, -7:-4].mean(axis=1)
    held, pairs = 0, 0
    for team in (att, dfn):
        d = np.linalg.norm(team[:, :, None] - team[:, None], axis=-1)  # (N, 10, 10, T)
        iu = np.triu_indices(10, 1)
        close = d[:, iu[0], iu[1]] < 2.0
        run = np.zeros(close.shape[:2])
        best = np.zeros(close.shape[:2])
        for t in range(close.shape[2]):
            run = np.where(close[..., t], run + 1, 0)
            best = np.maximum(best, run)
        held += int((best >= 10).sum())
        pairs += best.size
    return {"speed_mean": round(float(sp.mean()), 2), "speed_p50": round(float(np.percentile(sp, 50)), 2),
            "speed_p95": round(float(np.percentile(sp, 95)), 2), "speed_max": round(float(sp.max()), 2),
            "acc_p50": round(float(np.percentile(acc, 50)), 2), "acc_p95": round(float(np.percentile(acc, 95)), 2),
            "share_below_0_5mps": round(float((sp < 0.5).mean()), 3),
            "width_in_possession": round(float(np.ptp(att[..., 1], axis=1).mean()), 1),
            "width_defending": round(float(np.ptp(dfn[..., 1], axis=1).mean()), 1),
            "depth_in_possession": round(float(np.ptp(att[..., 0], axis=1).mean()), 1),
            "depth_defending": round(float(np.ptp(dfn[..., 0], axis=1).mean()), 1),
            "defending_backline_to_midfield_m": round(float(line_gap.mean()), 1),
            "teammate_pairs_within_2m_for_2s": f"{held}/{pairs}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs1", type=int, default=8)
    ap.add_argument("--epochs2", type=int, default=12)
    ap.add_argument("--patience", type=int, default=3)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-minutes", type=float, default=0.0, help="stop training after this long (0 = no limit)")
    ap.add_argument("--roll-stride", type=int, default=2)
    ap.add_argument("--stochastic", action="store_true", help="predict a Laplace scale and sample in rollouts")
    ap.add_argument("--nll-weight", type=float, default=0.1, help="stage 2: share of the one-step NLL (stochastic)")
    ap.add_argument("--goal", action="store_true", help="goal-conditioned: each player is given where to be and when")
    ap.add_argument("--goal-drop", type=float, default=0.15, help="share of players trained without a goal")
    args = ap.parse_args(argv)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.set_num_threads(int(os.environ.get("SLURM_CPUS_PER_TASK", len(os.sched_getaffinity(0)))))
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    segs = {s: load_split(args.processed, s) for s in SPLITS}

    # windows with the later frames goals are drawn from (NaN past a segment's end); x[:, :, :length] is the window
    def win(split: str, length: int, stride: int, ahead: int) -> tuple[torch.Tensor, torch.Tensor]:
        if not args.goal:
            return torch.from_numpy(windows(segs[split][0], length, stride)).to(dev), None
        w, n = windows_ahead(segs[split][0], length, ahead, stride)
        return torch.from_numpy(w).to(dev), torch.from_numpy(n + length - HIST).to(dev)

    one_w = {s: win(s, HIST + 1, 1 if s == "train" else 2, GOAL_STEPS_1[1] - 1) for s in SPLITS}
    roll_w = {s: win(s, HIST + ROLL, args.roll_stride if s == "train" else 5, GOAL_STEPS_2[1] - ROLL) for s in SPLITS}
    one = {s: one_w[s][0][:, :, :HIST + 1] for s in SPLITS}
    roll = {s: roll_w[s][0][:, :, :HIST + ROLL] for s in SPLITS}
    fixed = {}  # validation and test goals: one fixed draw, nobody dropped
    if args.goal:
        g = torch.Generator(device=dev).manual_seed(args.seed + 1)
        for s in ("val", "test"):
            fixed[s] = {"goals_one": draw_goals(*one_w[s], *GOAL_STEPS_1, 0.0, 1, g),
                        "goals_roll": draw_goals(*roll_w[s], *GOAL_STEPS_2, 0.0, ROLL, g)}
    log = {"model": "MotionGNN (shared 1-layer GRU 64 + 2 GNN layers 64 + shared decoder"
                    + (", Laplace location and scale" if args.stochastic else "")
                    + (", goal-conditioned" if args.goal else "") + ")",
           "splits": SPLITS, "segment_stats": {s: segs[s][1] for s in SPLITS},
           "windows": {s: {"one_step": int(len(one[s])), "rollout_2s": int(len(roll[s]))} for s in SPLITS},
           "settings": {"hist_steps": HIST, "dt_s": DT, "roll_steps": ROLL,
                        **({"goal_steps_stage1": GOAL_STEPS_1, "goal_steps_stage2": GOAL_STEPS_2} if args.goal else {}),
                        **{k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}},
           "conditioning": "ball = context node with a supplied path (observed in evaluation, controller in generation)",
           "device": str(dev), "gpu": torch.cuda.get_device_name(0) if dev.type == "cuda" else None}
    print(json.dumps(log["windows"]), flush=True)

    model = MotionGNN(stochastic=args.stochastic, goal=args.goal).to(dev)
    model.set_scales(one["train"][torch.randperm(len(one["train"]), device=dev)[:20000]])  # training matches only
    log["parameters"] = sum(p.numel() for p in model.parameters())
    print("parameters", log["parameters"], flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    history, best, best_state, bad = [], float("inf"), None, 0
    stage1_end = args.epochs1
    ep = 0
    while ep < stage1_end + args.epochs2:
        stage = 1 if ep < stage1_end else 2
        if ep == stage1_end:  # stage 2 starts from the best stage-1 weights with its own early stopping
            model.load_state_dict(best_state)
            best, bad = float("inf"), 0
            for g in opt.param_groups:
                g["lr"] = args.lr * 0.3
        model.train()
        src, avail = one_w["train"] if stage == 1 else roll_w["train"]
        length = HIST + 1 if stage == 1 else HIST + ROLL
        perm = torch.randperm(len(src), device=dev)
        tot, n = 0.0, 0
        for i in range(0, len(src), args.batch):
            idx = perm[i:i + args.batch]
            xf = src[idx]
            if torch.rand(()) < 0.5:
                xf = flip_y(xf)
            x = xf[:, :, :length]
            goals = None
            if args.goal:
                goals = draw_goals(xf, avail[idx], *(GOAL_STEPS_1 if stage == 1 else GOAL_STEPS_2), args.goal_drop,
                                   1 if stage == 1 else ROLL)
            g0 = None if goals is None else goals[:, :, 0]
            if stage == 1 and args.stochastic:
                loss = laplace_nll(model, x, g0)
            elif stage == 1:
                err = (model(x[:, :, :HIST], g0) - true_dv(x)) / model.dv_std
                loss = torch.nn.functional.smooth_l1_loss(err, torch.zeros_like(err))
            else:
                loss = rollout_loss(model, x, ROLL, goals)
                if args.stochastic:
                    loss = loss + args.nll_weight * laplace_nll(model, x[:, :, :HIST + 1], g0)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += loss.item() * len(x)
            n += len(x)
        val = evaluate(model, one["val"], roll["val"], **fixed.get("val", {}))
        score = val["rollout_all"]["ade_2s"]
        history.append({"epoch": ep, "stage": stage, "train_loss": round(tot / n, 4), "val_one_step": val["one_step"],
                        "val_rollout_2s": val["rollout_all"], "elapsed_s": round(time.time() - t0, 1)})
        print(json.dumps(history[-1]), flush=True)
        if score < best - 1e-4:
            best, best_state, bad = score, {k: v.detach().clone() for k, v in model.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= args.patience:
                if stage == 2:
                    break
                stage1_end = ep + 1  # stop stage 1 early and move on
        ep += 1
        if args.max_minutes and time.time() - t0 > args.max_minutes * 60:
            history.append({"note": f"time limit reached after epoch {ep - 1}"})
            break
    model.load_state_dict(best_state)
    torch.save({"state_dict": model.state_dict(), "config": {"d": 64, "mlp": 128, "hist_steps": HIST, "dt_s": DT,
                                                              "stochastic": args.stochastic, "goal": args.goal}},
               args.out / "motion_gnn.pt")
    log["history"] = history
    log["val"] = evaluate(model, one["val"], roll["val"], **fixed.get("val", {}))
    log["test"] = evaluate(model, one["test"], roll["test"], **fixed.get("test", {}))

    # the longest autonomous horizon the test match's uninterrupted possessions allow (15 s, else 10 s, else 5 s)
    for n_steps in (LONG_STEPS, 50, 25):
        if any(len(seg.xy) >= HIST + n_steps for seg in segs["test"][0]):
            break
    long = torch.from_numpy(windows(segs["test"][0], HIST + n_steps, 25)).to(dev)
    long_goals = None
    if args.goal:  # every 2 s each player's goal becomes where he really was 3 s later (none in the last stretch)
        k = torch.arange(n_steps, device=dev)
        frame = HIST - 1 + (k // 10) * 10 + 15
        ok = frame <= HIST + n_steps - 1
        g = long[:, :22, frame.clamp(max=HIST + n_steps - 1)]  # (B, 22, steps, 2)
        tau = ((frame - (HIST - 1 + k)) * DT).expand(len(long), 22, -1)
        long_goals = torch.cat([g, tau[..., None], torch.ones_like(tau)[..., None]], dim=-1)
        long_goals = torch.where(ok[None, None, :, None], long_goals, torch.zeros_like(long_goals))

    def gl(i):
        return None if long_goals is None else long_goals[i:i + 256]

    g = torch.Generator(device=dev).manual_seed(args.seed)
    with torch.no_grad():
        gen = torch.cat([rollout(model, long[i:i + 256, :, :HIST], long[i:i + 256, 22], n_steps,
                                 sample=args.stochastic, generator=g, goals=gl(i))
                         for i in range(0, len(long), 256)]).cpu().numpy()
        gen_loc = gen if not args.stochastic else torch.cat([
            rollout(model, long[i:i + 256, :, :HIST], long[i:i + 256, 22], n_steps, goals=gl(i))
            for i in range(0, len(long), 256)]).cpu().numpy()
        gen_free = None if not args.goal else torch.cat([
            rollout(model, long[i:i + 256, :, :HIST], long[i:i + 256, 22], n_steps, sample=args.stochastic, generator=g)
            for i in range(0, len(long), 256)]).cpu().numpy()
    real = long[:, :22, HIST:].cpu().numpy()
    fde = np.linalg.norm(gen - real, axis=-1)
    log["autonomous_rollout_test"] = {
        "n_starts": int(len(long)), "seconds": n_steps * DT,
        "real": shape_stats(real), "model": shape_stats(gen),
        "displacement_from_real_m": {"at_2s": round(float(fde[:, :, 9].mean()), 2),
                                     "at_5s": round(float(fde[:, :, 24].mean()), 2),
                                     "at_end": round(float(fde[:, :, -1].mean()), 2)},
        "non_finite": bool(~np.isfinite(gen).all())}
    if args.stochastic:  # "model" is the sampled rollout (what generation does); the location-only one for reference
        log["autonomous_rollout_test"]["model_location_only"] = shape_stats(gen_loc)
    if args.goal:  # "model" follows goals refreshed every 2 s from what really happened; here nobody has a goal
        log["autonomous_rollout_test"]["goals"] = "every 2 s: the real position 3 s later; none in the last 1 s"
        log["autonomous_rollout_test"]["model_no_goal"] = shape_stats(gen_free)
        fde_free = np.linalg.norm(gen_free - real, axis=-1)
        log["autonomous_rollout_test"]["displacement_from_real_no_goal_m"] = {
            "at_2s": round(float(fde_free[:, :, 9].mean()), 2), "at_5s": round(float(fde_free[:, :, 24].mean()), 2),
            "at_end": round(float(fde_free[:, :, -1].mean()), 2)}
    np.savez_compressed(args.out / "rollouts_test.npz", start=long[:, :, :HIST].cpu().numpy(), real=real, model=gen,
                        model_location_only=gen_loc, ball=long[:, 22].cpu().numpy(),
                        **({"model_no_goal": gen_free} if args.goal else {}))
    log["elapsed_s"] = round(time.time() - t0, 1)
    (args.out / "metrics.json").write_text(json.dumps(log, indent=1))
    print(json.dumps({k: log[k] for k in ("parameters", "test", "autonomous_rollout_test")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
