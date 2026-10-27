"""Private scenario plans for the defensive families (generator-private; never served).

Each builder samples a ``Plan`` from seeded RNG streams and returns it with a
private truth record. Truth describes what the generator *intended* (family,
variant, behaviour parameters, expected relations). The analytics never see
it; only the offline evaluation runner joins it with observed outputs.

Family A ("high man-oriented press + high back line"):
  main     - every outfield attacker near the ball is man-marked, the second
             centre-back follows the left winger, so nobody covers a run
             in behind the high line.
  cover    - identical attack; the second centre-back stays as a spare,
             goal-side of the striker (spec 6.2 mandatory negative).
"""

from __future__ import annotations

import numpy as np

from .engine import Plan, Seg

FAMILY_A_VARIANTS = ("main", "cover")


def _story_keys(truth: dict) -> tuple[str, ...]:
    """The players a story is built on (truth roles): the engine keeps their slots from wandering."""
    keys = [k for v in truth["roles"].values() for k in (v if isinstance(v, list) else [v])]
    return tuple(sorted(set(keys)))


def _j(rng: np.random.Generator, xy: tuple[float, float], s: float = 1.0) -> tuple[float, float]:
    return (float(xy[0] + rng.uniform(-s, s)), float(xy[1] + rng.uniform(-s, s)))


def family_a(attack_rng: np.random.Generator, defense_rng: np.random.Generator, variant: str) -> tuple[Plan, dict]:
    if variant not in FAMILY_A_VARIANTS:
        raise ValueError(variant)
    ar, dr = attack_rng, defense_rng
    t_run = float(ar.uniform(11.0, 11.6))
    # diagonal run into the channel between centre-back and full back: a marker with a normal cushion is still
    # beaten by a run that goes away from him
    run_to = _j(ar, (78.0, 21.0), 1.5)
    st_start = _j(ar, (45.5, 33.0), 0.8)

    init_a = {
        "A:GK": (5.5, 34.0), "A:RCB": _j(ar, (15.0, 22.0)), "A:LCB": _j(ar, (15.0, 46.0)),
        "A:RB": _j(ar, (25.0, 6.0)), "A:LB": _j(ar, (36.0, 61.0)), "A:DM": _j(ar, (25.0, 34.0)),
        "A:RCM": _j(ar, (35.0, 23.0)), "A:LCM": _j(ar, (36.0, 43.0)), "A:RW": _j(ar, (50.0, 8.0)),
        "A:LW": _j(ar, (51.0, 46.0)), "A:ST": st_start,
    }
    gs_cb = float(dr.uniform(4.0, 5.0))  # the centre-back's goal-side cushion on the striker
    mark_lag = float(dr.uniform(0.3, 0.5))
    spare_at = _j(dr, (58.5, 31.0), 1.0)  # shaded toward the channel behind the marking centre-back
    init_d = {
        "D:GK": (92.0, 34.0), "D:ST": _j(dr, (26.0, 30.0)), "D:LW": _j(dr, (27.0, 9.0)), "D:RW": _j(dr, (25.0, 49.0)),
        "D:DM": _j(dr, (28.0, 35.0)), "D:LCM": _j(dr, (38.0, 24.0)), "D:RCM": _j(dr, (39.0, 43.0)),
        "D:RB": _j(dr, (39.0, 59.0)), "D:LB": _j(dr, (53.0, 9.0)), "D:LCB": (st_start[0] + gs_cb, st_start[1] - 0.5),
        "D:RCB": _j(dr, (54.0, 46.0)) if variant == "main" else spare_at,
    }

    long = ("long_ball_release", 0.0)
    segs: dict[str, list[Seg]] = {
        # attack
        "A:GK": [Seg(0.0, "gk", {"at": (5.5, 34.0), "track": 0.1})],
        "A:RCB": [Seg(0.0, "hold", {"at": init_a["A:RCB"], "shift": (0.1, 0.05)}),
                  Seg(8.6, "carry", {"to": _j(ar, (19.5, 15.0)), "speed": 1.8})],
        "A:LCB": [Seg(0.0, "check", {"at": init_a["A:LCB"], "amp": 1.5})],
        "A:RB": [Seg(0.0, "check", {"at": init_a["A:RB"], "amp": 2.2})],
        "A:LB": [Seg(0.0, "check", {"at": init_a["A:LB"], "amp": 1.5})],
        "A:DM": [Seg(0.0, "check", {"at": init_a["A:DM"], "amp": 2.6})],
        "A:RCM": [Seg(0.0, "check", {"at": init_a["A:RCM"], "amp": 2.6})],
        "A:LCM": [Seg(0.0, "check", {"at": init_a["A:LCM"], "amp": 2.4})],
        "A:RW": [Seg(0.0, "check", {"at": init_a["A:RW"], "amp": 1.5})],
        "A:LW": [Seg(0.0, "check", {"at": init_a["A:LW"], "amp": 1.5})],
        "A:ST": [Seg(0.0, "check", {"at": st_start, "amp": 1.2}),
                 Seg(t_run, "run", {"to": run_to, "speed": 8.2}),
                 Seg(("long_ball_arrive", 0.0), "carry", {"to": (93.0, 35.0), "speed": 6.0})],
        # defence: man-oriented press
        "D:GK": [Seg(0.0, "gk", {"at": (92.0, 34.0)}), Seg(long, "sweep", {"u_range": (88.5, 103.0)}),
                 Seg(("shot_release", 0.0), "gk", {"at": (101.5, 34.0), "speed": 6.0})],
        # the pressers mark goal-side of their centre-backs until the ball is played to them (the pass is on), then
        # press it, the striker curving in from outside to cut the pass to the full back and show him inside, onto
        # the markers
        "D:ST": [Seg(0.0, "press_mark", {"who": "A:RCB", "cut": "A:RB", "gs": 1.0, "stop": 1.0, "speed": 7.2})],
        "D:RW": [Seg(0.0, "press_mark", {"who": "A:LCB", "cut": "A:LB", "gs": 1.0, "stop": 1.6, "speed": 7.0})],
        # man-oriented near the ball, but the midfield and the wide midfielder drop off a man who is far from it
        "D:LW": [Seg(0.0, "mark", {"who": "A:RB", "gs": 1.5, "lag": mark_lag, "loose": True, "speed": 7.2})],
        "D:DM": [Seg(0.0, "mark", {"who": "A:DM", "gs": 1.7, "lag": mark_lag, "loose": True, "speed": 7.0})],
        "D:LCM": [Seg(0.0, "mark", {"who": "A:RCM", "gs": 1.7, "lag": mark_lag, "loose": True, "speed": 7.0})],
        "D:RCM": [Seg(0.0, "mark", {"who": "A:LCM", "gs": 1.7, "lag": mark_lag, "loose": True, "speed": 7.0})],
        # the full backs hold the high line: the far one tucks in off his winger at the line's height
        "D:RB": [Seg(0.0, "mark", {"who": "A:LB", "gs": 2.0, "lag": mark_lag, "loose": True, "loose_gs": 0.0,
                                   "speed": 7.0}),
                 Seg(long, "recover", {"du": 18.0, "speed": 6.5})],
        "D:LB": [Seg(0.0, "mark", {"who": "A:RW", "gs": 2.5, "lag": mark_lag, "speed": 7.0}),
                 Seg(long, "recover", {"du": 16.0, "speed": 7.0})],
        "D:LCB": [Seg(0.0, "mark", {"who": "A:ST", "gs": gs_cb, "lag": mark_lag, "antic": 0.3, "speed": 7.6}),
                  Seg(long, "chase", {"who": "A:ST", "speed": 8.2})],
    }
    if variant == "main":
        segs["D:RCB"] = [Seg(0.0, "mark", {"who": "A:LW", "gs": 2.0, "lag": mark_lag, "speed": 7.0}),
                         Seg(long, "recover", {"du": 16.0, "who": "A:ST", "speed": 7.4})]
    else:
        # the spare reads the striker's run early (short lag) and stays goal-side of it
        segs["D:RCB"] = [Seg(0.0, "cover", {"at": spare_at, "track": "A:ST", "depth": 4.5, "k": 0.85, "lag": 0.2,
                                            "speed": 7.2}),
                         Seg(long, "attack_ball", {"speed": 8.4})]

    ball_steps = [
        {"kind": "pass", "from": "A:GK", "to": "A:LCB", "at": float(ar.uniform(1.8, 2.3)), "speed": 15.0},
        {"kind": "pass", "from": "A:LCB", "to": "A:GK", "after_receive_s": float(ar.uniform(1.6, 2.0)),
         "speed": 15.0},
        {"kind": "pass", "name": "rcb_ball", "from": "A:GK", "to": "A:RCB", "after_receive_s": float(ar.uniform(1.4, 1.8)),
         "speed": 15.0},
        {"kind": "pass", "name": "long_ball", "from": "A:RCB", "to": "A:ST", "style": "lofted", "speed": 22.0,
         "lead": 0.95, "run_speed": 7.6, "at": t_run + 0.8,
         "trigger": {"kind": "onside_line", "runner": "A:ST", "margin_m": 2.0, "min_speed": 5.0,
                     "latest": t_run + 2.6}},
        {"kind": "shot", "name": "shot", "from": "A:ST", "after_receive_s": float(ar.uniform(2.4, 2.9)),
         "speed": 24.0},
    ]
    plan = Plan(duration_s=24.0, init={**init_a, **init_d}, segs=segs, ball_steps=ball_steps, first_owner="A:GK",
                sigma={"A:ST": 0.2, "D:LCB": 0.2, "D:RCB": 0.25, "D:ST": 0.15}, lag_s=mark_lag)
    truth = {
        "family": "A",
        # the designed story, for offline evaluation only: from this ball arrival to the last ball action; the
        # analytics family keys whose episodes may tell it
        "story": {"start": "rcb_ball_arrive", "families": ["press_vs_depth"], "used": variant == "main",
                  "optional": ["isolated_1v1"]},
        "variant": variant,
        "generator_intent": "high_man_press_high_line" + ("" if variant == "main" else "_with_spare_cover"),
        "expected": {
            "restricted": ["short_options_screened"],
            "exposed": ["behind_space_exposed", "isolated_1v1"] if variant == "main" else [],
            "suppressed": [] if variant == "main" else ["isolated_1v1", "behind_space_exposed"],
            "attacking_response": "runner_in_behind_and_long_ball",
            "defensive_adjustment": "line_retreat",
        },
        "params": {"t_run": round(t_run, 3), "run_to": run_to, "gs_cb": round(gs_cb, 3),
                   "mark_lag_s": round(mark_lag, 3), "spare_at": spare_at if variant == "cover" else None},
        "roles": {"runner": "A:ST", "runner_marker": "D:LCB", "second_cb": "D:RCB", "carrier": "A:RCB",
                  "presser": "D:ST"},
    }
    plan.steady = _story_keys(truth)
    return plan, truth


# ---------------------------------------------------------------------------- family B
FAMILY_B_VARIANTS = ("main", "compact")


def family_b(attack_rng: np.random.Generator, defense_rng: np.random.Generator, variant: str) -> tuple[Plan, dict]:
    """High front press with a deeper back line (main) versus a whole block that drops together (compact).

    The back line starts around 42 m from its goal and retreats to about 28 m.
    In "main" the front three keep pressing high and the midfield line stays
    with them, so a gap opens; the attacking "10" then moves into it and is
    found between the lines, after which the defending midfield drops. In
    "compact" all three lines drop together and the press stops.
    """
    if variant not in FAMILY_B_VARIANTS:
        raise ValueError(variant)
    ar, dr = attack_rng, defense_rng
    t_drop = float(dr.uniform(1.0, 1.6))
    back_u = float(dr.uniform(76.0, 78.0))
    t_move = float(ar.uniform(6.2, 6.8))  # the 10 moves into the gap
    gap_spot = _j(ar, (53.0, 43.5), 0.8)
    compact = variant == "compact"
    mid_u = 53.0 if compact else 44.5  # compact: the midfield about 10 m in front of the back four, not level with it
    init_a = {
        "A:GK": (5.5, 34.0), "A:RCB": _j(ar, (16.0, 23.0)), "A:LCB": _j(ar, (16.0, 45.0)),
        "A:RB": _j(ar, (27.0, 6.0)), "A:LB": _j(ar, (27.0, 62.0)), "A:DM": _j(ar, (27.0, 34.0)),
        "A:RCM": _j(ar, (40.0, 22.0)), "A:LCM": _j(ar, (42.5, 45.0)), "A:RW": _j(ar, (58.0, 8.0)),
        "A:LW": _j(ar, (58.0, 60.0)), "A:ST": _j(ar, (66.0, 33.0)),
    }
    init_d = {
        "D:GK": (100.0, 34.0), "D:ST": _j(dr, (25.0, 31.0)), "D:LW": _j(dr, (27.0, 14.0)),
        "D:RW": _j(dr, (27.0, 52.0)), "D:LCM": _j(dr, (mid_u - 1.0, 25.0)), "D:DM": _j(dr, (mid_u + 1.5, 34.0)),
        "D:RCM": _j(dr, (mid_u - 1.0, 44.0)), "D:LB": _j(dr, (63.0, 12.0)), "D:LCB": _j(dr, (64.0, 28.0)),
        "D:RCB": _j(dr, (64.0, 40.0)), "D:RB": _j(dr, (63.0, 56.0)),
    }
    if not compact:  # the midfield line stays tight to the press, leaving the half-space behind it empty
        init_d["D:DM"] = _j(dr, (mid_u + 1.5, 29.5), 0.8)
        init_d["D:RCM"] = _j(dr, (mid_u - 4.5, 55.0), 0.8)
    if compact:
        for k, (u, v) in list(init_d.items()):
            if k in ("D:ST", "D:LW", "D:RW"):
                init_d[k] = (u + 22.0, v)
    segs: dict[str, list[Seg]] = {
        "A:GK": [Seg(0.0, "gk", {"at": (5.5, 34.0), "track": 0.1})],
        "A:RCB": [Seg(0.0, "check", {"at": init_a["A:RCB"], "amp": 1.2})],
        "A:LCB": [Seg(0.0, "check", {"at": init_a["A:LCB"], "amp": 1.2})],
        "A:RB": [Seg(0.0, "check", {"at": init_a["A:RB"], "amp": 1.5})],
        "A:LB": [Seg(0.0, "check", {"at": init_a["A:LB"], "amp": 1.5})],
        "A:DM": [Seg(0.0, "check", {"at": init_a["A:DM"], "amp": 1.5}),
                 Seg(("dm_ball_arrive", 0.0), "carry", {"to": _j(ar, (36.0, 33.0), 0.6), "speed": 2.0})],
        "A:RCM": [Seg(0.0, "check", {"at": init_a["A:RCM"], "amp": 2.0})],
        "A:LCM": [Seg(0.0, "check", {"at": init_a["A:LCM"], "amp": 1.5}),
                  Seg(t_move, "run", {"to": gap_spot, "speed": 5.5}),
                  Seg(("ten_ball_arrive", 0.0), "carry", {"to": (66.0, 38.0), "speed": 3.0})],
        "A:RW": [Seg(0.0, "check", {"at": init_a["A:RW"], "amp": 1.5})],
        "A:LW": [Seg(0.0, "check", {"at": init_a["A:LW"], "amp": 1.5})],
        "A:ST": [Seg(0.0, "check", {"at": init_a["A:ST"], "amp": 1.5})],
        "D:GK": [Seg(0.0, "gk", {"at": (100.0, 34.0)})],
        "D:LB": [Seg(0.0, "hold", {"at": init_d["D:LB"], "shift": (0.0, 0.15)}),
                 Seg(t_drop, "run", {"to": (back_u - 1.0, 12.0), "speed": 4.5})],
        "D:LCB": [Seg(0.0, "hold", {"at": init_d["D:LCB"], "shift": (0.0, 0.15)}),
                  Seg(t_drop, "run", {"to": (back_u, 28.0), "speed": 4.5})],
        "D:RCB": [Seg(0.0, "hold", {"at": init_d["D:RCB"], "shift": (0.0, 0.15)}),
                  Seg(t_drop, "run", {"to": (back_u, 40.0), "speed": 4.5})],
        "D:RB": [Seg(0.0, "hold", {"at": init_d["D:RB"], "shift": (0.0, 0.15)}),
                 Seg(t_drop, "run", {"to": (back_u - 1.0, 56.0), "speed": 4.5})],
    }
    if compact:
        for k, v in (("D:ST", 31.0), ("D:LW", 16.0), ("D:RW", 50.0)):
            segs[k] = [Seg(0.0, "hold", {"at": (init_d[k][0], v), "shift": (0.2, 0.25)}),
                       Seg(t_drop, "run", {"to": (48.0, v), "speed": 3.5})]
        for k, v in (("D:LCM", 25.0), ("D:DM", 34.0), ("D:RCM", 44.0)):
            segs[k] = [Seg(0.0, "hold", {"at": init_d[k], "shift": (0.2, 0.3)}),
                       Seg(t_drop, "run", {"to": (63.0 if k != "D:DM" else 65.0, v), "speed": 3.5}),
                       Seg(t_move + 0.6, "screen", {"who": "A:LCM", "frac": 0.75, "speed": 6.0}) if k == "D:DM"
                       else Seg(t_drop + 0.2, "run", {"to": (63.0, v), "speed": 3.5})]
    else:
        # the striker presses the centre-back from outside, forcing the ball inside to the pivot; the wide forward
        # takes the full back
        segs["D:ST"] = [Seg(0.0, "press_mark", {"who": "A:RCB", "cut": "A:RB", "gs": 1.0, "stop": 1.2,
                                                "speed": 7.0}),
                        Seg(("ten_ball_arrive", 0.4), "run", {"to": (40.0, 34.0), "speed": 4.0})]
        segs["D:LW"] = [Seg(0.0, "mark", {"who": "A:RB", "gs": 1.5, "lag": 0.4, "speed": 6.5})]
        segs["D:RW"] = [Seg(0.0, "mark", {"who": "A:LCB", "gs": -2.0, "lag": 0.4, "speed": 6.5})]
        segs["D:LCM"] = [Seg(0.0, "mark", {"who": "A:RCM", "gs": 1.8, "lag": 0.4, "speed": 6.5}),
                         Seg(("ten_ball_arrive", 0.6), "run", {"to": (60.0, 26.0), "speed": 6.0})]
        segs["D:RCM"] = [Seg(0.0, "hold", {"at": init_d["D:RCM"], "shift": (0.1, 0.0)}),
                         Seg(("ten_ball_arrive", 0.6), "run", {"to": (60.0, 44.0), "speed": 6.0})]
        # the holding midfielder is drawn up onto the pivot with the press (behind him, the striker in front): that
        # is what leaves the space behind the midfield, and he chases the 10 only once the ball is in there
        segs["D:DM"] = [Seg(0.0, "press_mark", {"who": "A:DM", "cut": "A:RCM", "gs": 1.2, "stop": 1.6, "lag": 0.4,
                                                "speed": 6.5}),
                        Seg(("ten_ball_arrive", 0.3), "chase", {"who": "A:LCM", "gs": 1.5, "speed": 7.0})]
    ball_steps = [
        {"kind": "pass", "from": "A:GK", "to": "A:RCB", "at": float(ar.uniform(1.8, 2.2)), "speed": 15.0},
        {"kind": "pass", "name": "dm_ball", "from": "A:RCB", "to": "A:DM", "after_receive_s": float(ar.uniform(1.4, 1.8)),
         "speed": 14.0},
        {"kind": "pass", "name": "ten_ball", "from": "A:DM", "to": "A:LCM", "speed": 15.0, "lead": 0.4,
         "at": t_move + float(ar.uniform(2.4, 2.8))},
        {"kind": "pass", "from": "A:LCM", "to": "A:ST", "after_receive_s": float(ar.uniform(2.6, 3.2)), "speed": 15.0},
    ]
    plan = Plan(duration_s=26.0, init={**init_a, **init_d}, segs=segs, ball_steps=ball_steps, first_owner="A:GK",
                sigma={"A:LCM": 0.2, "D:DM": 0.2}, lag_s=0.4)
    truth = {
        "family": "B", "variant": variant,
        "story": {"start": "dm_ball_arrive", "families": ["line_separation"], "used": variant == "main",
                  "optional": ["press_block_disconnected"]},
        "generator_intent": "high_press_deep_line" if not compact else "compact_low_block",
        "expected": {
            "restricted": ["behind_depth_reduced"],
            "exposed": [] if compact else ["press_block_disconnected", "between_lines_receiver"],
            "suppressed": ["between_lines_receiver", "press_block_disconnected"] if compact else [],
            "attacking_response": "midfielder_into_gap", "defensive_adjustment": "midfield_drop",
        },
        "params": {"t_drop": round(t_drop, 3), "back_u": round(back_u, 2), "t_move": round(t_move, 3),
                   "gap_spot": gap_spot, "mid_u": mid_u},
        "roles": {"between_lines": "A:LCM", "carrier": "A:DM", "mid_drop": "D:DM"},
    }
    plan.steady = _story_keys(truth)
    return plan, truth


# ---------------------------------------------------------------------------- family C
FAMILY_C_VARIANTS = ("main", "support")


def family_c(attack_rng: np.random.Generator, defense_rng: np.random.Generator, variant: str) -> tuple[Plan, dict]:
    """Lane-oriented press: central routes screened, wide outlet left (main) or closed by supporting wingers.

    main    - the striker and central midfielders stand in the passing lines
              from the ball-carrying centre-back to the central midfielders;
              the right back stays free wide. The left central midfielder drops
              out of the screen and is free for a moment, then the defending
              central midfielder steps up to him; the ball goes wide.
    support - same attack, but the wide midfielders tuck in to support the
              screen: the centre looks like a local 3v2 for the attack, yet
              no lane opens and the wide outlet is marked (spec 6.2 negative).
    """
    if variant not in FAMILY_C_VARIANTS:
        raise ValueError(variant)
    ar, dr = attack_rng, defense_rng
    t_drop = float(ar.uniform(4.4, 4.8))
    drop_to = _j(ar, (22.5, 43.0), 0.6)
    step_lag = float(dr.uniform(2.6, 3.0))
    support = variant == "support"
    init_a = {
        "A:GK": (5.5, 34.0), "A:RCB": _j(ar, (17.0, 21.0)), "A:LCB": _j(ar, (17.0, 47.0)),
        "A:RB": _j(ar, (24.0, 5.0)), "A:LB": _j(ar, (26.0, 63.0)), "A:DM": _j(ar, (30.0, 33.0)),
        "A:RCM": _j(ar, (37.0, 25.0)), "A:LCM": _j(ar, (39.0, 41.5)), "A:RW": _j(ar, (48.0, 8.0)),
        "A:LW": _j(ar, (48.0, 60.0)), "A:ST": _j(ar, (51.0, 34.0)),
    }
    init_d = {
        "D:GK": (94.0, 34.0), "D:ST": _j(dr, (26.0, 27.0)), "D:LCM": _j(dr, (33.0, 27.5)),
        "D:RCM": _j(dr, (35.0, 38.0)), "D:DM": _j(dr, (44.0, 34.0)),
        "D:LW": _j(dr, (36.0, 12.0)) if not support else _j(dr, (31.0, 18.0)),
        "D:RW": _j(dr, (36.0, 56.0)) if not support else _j(dr, (34.0, 47.0)),
        # the back line follows the press up (about 20-25 m behind the midfield screen, not 30)
        "D:LB": _j(dr, (52.0, 10.0)), "D:LCB": _j(dr, (55.0, 28.0)), "D:RCB": _j(dr, (55.0, 40.0)),
        "D:RB": _j(dr, (52.0, 58.0)),
    }
    segs: dict[str, list[Seg]] = {
        "A:GK": [Seg(0.0, "gk", {"at": (5.5, 34.0), "track": 0.1})],
        # the story's players shift their feet where they wait ("wander"); the carrier keeps the ball moving, slowly
        # (the presser does not let him go anywhere), instead of stopping on it
        "A:RCB": [Seg(0.0, "hold", {"at": init_a["A:RCB"], "shift": (0.1, 0.05), "wander": 0.8}),
                  Seg(("rcb_ball_arrive", 0.0), "carry", {"to": _j(ar, (21.0, 20.0)), "speed": 0.8})],
        # the far centre-back stays a few metres deeper than the ball-side one (cover) and holds his width (sliding
        # into the middle would make him a central option the analyzer counts as a free man)
        "A:LCB": [Seg(0.0, "check", {"at": init_a["A:LCB"], "amp": 1.2, "shift": (0.0, 0.05)})],
        "A:RB": [Seg(0.0, "check", {"at": init_a["A:RB"], "amp": 1.5, "wander": 1.2}),
                 Seg(("wide_ball_arrive", 0.0), "carry", {"to": (40.0, 6.0), "speed": 4.0})],
        "A:LB": [Seg(0.0, "check", {"at": init_a["A:LB"], "amp": 1.5})],
        # the single pivot holds his depth in front of the centre-backs (rest defence) instead of following the
        # build-up forward (the midfielder screening him would come with him into the lane to the man who drops);
        # he slides across with the ball
        "A:DM": [Seg(0.0, "check", {"at": init_a["A:DM"], "amp": 1.6, "shift": (0.0, 0.15)})],
        "A:RCM": [Seg(0.0, "check", {"at": init_a["A:RCM"], "amp": 1.6})],
        # waits between the lines at his height, then drops quickly (from a standing start) and, once there, keeps
        # adjusting to receive
        "A:LCM": [Seg(0.0, "check", {"at": init_a["A:LCM"], "amp": 1.5, "shift": (0.0, 0.1), "wander": 0.8}),
                  Seg(t_drop, "run", {"to": drop_to, "speed": 6.8}),
                  Seg(t_drop + 3.2, "hold", {"at": drop_to, "shift": (0.1, 0.1), "wander": 1.0, "speed": 3.0})],
        "A:RW": [Seg(0.0, "check", {"at": init_a["A:RW"], "amp": 1.5})],
        "A:LW": [Seg(0.0, "check", {"at": init_a["A:LW"], "amp": 1.5})],
        "A:ST": [Seg(0.0, "check", {"at": init_a["A:ST"], "amp": 1.5})],
        "D:GK": [Seg(0.0, "gk", {"at": (94.0, 34.0)})],
        # lane-oriented: curve onto the carrier from inside, stand in the central passing lines
        "D:ST": [Seg(0.0, "press_mark", {"who": "A:RCB", "cut": "A:DM", "gs": 1.0, "stop": 1.5, "speed": 6.5})],
        # the screens are set on the centre-back the goalkeeper is about to find, before he has the ball
        # once the ball goes wide the screen is over: the midfield slides across toward it from its spots
        # the three screens stand apart (each nearer its receiver than the carrier); once the ball goes wide the
        # screen is over and each midfielder picks up his own man (no two on one)
        "D:LCM": [Seg(0.0, "screen", {"who": "A:RCM", "from": "A:RCB", "frac": 0.75, "speed": 6.0}),
                  Seg(t_drop, "hold", {"at": (31.5, 23.0), "shift": (0.0, 0.0), "speed": 3.0}),
                  Seg(("wide_ball_release", 0.2), "mark", {"who": "A:RCM", "gs": 1.8, "lag": 0.4, "speed": 6.0})],
        "D:RCM": [Seg(0.0, "screen", {"who": "A:LCM", "from": "A:RCB", "frac": 0.6, "speed": 6.0}),
                  Seg(t_drop + step_lag, "mark", {"who": "A:LCM", "gs": 1.2, "lag": 0.3, "speed": 7.0})],
        "D:DM": [Seg(0.0, "screen", {"who": "A:DM", "from": "A:RCB", "frac": 0.6, "speed": 5.5}),
                 Seg(t_drop, "hold", {"at": (26.0, 29.5), "shift": (0.0, 0.0), "speed": 3.0}),
                 Seg(("wide_ball_release", 0.2), "mark", {"who": "A:DM", "gs": 1.8, "lag": 0.4, "speed": 6.0})],
        "D:LB": [Seg(0.0, "hold", {"at": init_d["D:LB"], "shift": (0.2, 0.15)})],
        "D:LCB": [Seg(0.0, "hold", {"at": init_d["D:LCB"], "shift": (0.2, 0.1)})],
        "D:RCB": [Seg(0.0, "hold", {"at": init_d["D:RCB"], "shift": (0.2, 0.1)})],
        "D:RB": [Seg(0.0, "hold", {"at": init_d["D:RB"], "shift": (0.2, 0.15)})],
    }
    if support:
        # the striker drops into the centre and stays there: the region counts an extra attacker, but he is out
        # of passing reach
        segs["A:ST"] = [Seg(0.0, "check", {"at": init_a["A:ST"], "amp": 1.5}),
                        Seg(2.0, "run", {"to": _j(ar, (43.5, 35.0), 0.6), "speed": 4.0, "shift": (0.0, 0.0)})]
        segs["D:LW"] = [Seg(0.0, "mark", {"who": "A:RB", "gs": 1.4, "lag": 0.3, "speed": 7.0})]
        # the far winger steps up into the lane to the far centre-back: left open, that square ball would be the
        # free man the support is meant to take away (the midfield screens the dropping midfielder behind him)
        segs["D:RW"] = [Seg(0.0, "screen", {"who": "A:LCB", "from": "A:RCB", "frac": 0.55, "speed": 6.5})]
        segs["D:RCM"] = [Seg(0.0, "screen", {"who": "A:LCM", "from": "A:RCB", "frac": 0.6, "speed": 6.0}),
                         Seg(t_drop + 0.6, "mark", {"who": "A:LCM", "gs": 1.2, "lag": 0.3, "speed": 7.0})]
    else:
        segs["D:LW"] = [Seg(0.0, "hold", {"at": init_d["D:LW"], "shift": (0.15, 0.1)})]
        segs["D:RW"] = [Seg(0.0, "hold", {"at": init_d["D:RW"], "shift": (0.15, 0.1)})]
        # holds the screen on the LCM's original lane, so the drop takes the LCM out of it for a moment
        segs["D:RCM"] = [Seg(0.0, "screen", {"who": "A:LCM", "from": "A:RCB", "frac": 0.6, "speed": 6.0}),
                         Seg(t_drop - 0.2, "hold", {"at": (39.0, 36.5), "shift": (0.0, 0.0), "speed": 3.0}),
                         Seg(t_drop + step_lag, "mark", {"who": "A:LCM", "gs": 1.2, "lag": 0.3, "speed": 7.4})]
    ball_steps = [
        {"kind": "pass", "name": "rcb_ball", "from": "A:GK", "to": "A:RCB", "at": float(ar.uniform(1.8, 2.2)),
         "speed": 15.0},
        # main: the wide outlet is free and the ball goes there; support: the full back is marked, so the centre-back
        # goes back to his keeper instead of playing into him
        {"kind": "pass", "name": "wide_ball", "from": "A:RCB", "to": "A:RB", "speed": 14.0,
         "at": t_drop + step_lag + float(ar.uniform(1.4, 1.8))} if not support else
        {"kind": "pass", "name": "back_ball", "from": "A:RCB", "to": "A:GK", "speed": 14.0,
         "at": t_drop + step_lag + float(ar.uniform(1.4, 1.8))},
    ]
    plan = Plan(duration_s=26.0, init={**init_a, **init_d}, segs=segs, ball_steps=ball_steps, first_owner="A:GK",
                sigma={"A:LCM": 0.2, "D:RCM": 0.2, "D:ST": 0.15}, lag_s=0.4)
    truth = {
        "family": "C", "variant": variant,
        # the same restriction is called central routes screened (lane-oriented) or short options screened when
        # markers are near the ball; the evaluation (from holdout-v2-0.2) accepts either label
        "story": {"start": "rcb_ball_arrive", "families": ["central_screen", "press_vs_depth"],
                  "used": variant == "main",
                  "restricted_any": ["central_routes_screened", "short_options_screened"],
                  "optional": ["accessible_free_receiver"] if variant == "main" else []},
        "generator_intent": "lane_oriented_press_wide_outlet" if not support else "lane_press_with_winger_support",
        "expected": {
            "restricted": ["central_routes_screened"],
            "exposed": ["wide_outlet_available", "accessible_free_receiver"] if not support else [],
            "context": ["local_central_overload"],
            "suppressed": ["accessible_free_receiver"] if support else [],
            "attacking_response": "central_midfielder_drop", "defensive_adjustment": "defender_step",
        },
        "params": {"t_drop": round(t_drop, 3), "drop_to": drop_to, "step_lag": round(step_lag, 3)},
        "roles": {"dropper": "A:LCM", "stepper": "D:RCM", "carrier": "A:RCB", "wide_outlet": "A:RB"},
    }
    plan.steady = _story_keys(truth)
    return plan, truth


# ---------------------------------------------------------------------------- family D
FAMILY_D_VARIANTS = ("main", "escape")


def family_d(attack_rng: np.random.Generator, defense_rng: np.random.Generator, variant: str) -> tuple[Plan, dict]:
    """Four-player press with lane cuts against a 3+2 build-up (designed with the user, 2026-10-21).

    Attack: goalkeeper + back three (the right back stays as the right of three, the left back pushes high and
    wide) + a double pivot build up; five players stand high. Defence (4-4-2): the first striker presses the
    left centre-back while cutting his pass back to the central centre-back, the second striker shuts the right
    back's side, the two central midfielders mark the pivots, the wide midfielders and the back four take the
    five high players (6 v 5 behind). The goalkeeper is not pressed.

    main   - the trap holds: the left centre-back has no short option left and kicks long; the back line, a
             player up, wins it.
    escape - the left centre-back finds the unpressed goalkeeper; the central centre-back is free for a moment,
             the first striker shifts across too late and the ball goes through him.
    """
    if variant not in FAMILY_D_VARIANTS:
        raise ValueError(variant)
    ar, dr = attack_rng, defense_rng
    escape = variant == "escape"
    init_a = {
        "A:GK": (5.5, 34.0), "A:RB": _j(ar, (17.0, 15.0)), "A:RCB": _j(ar, (13.0, 34.0)),
        "A:LCB": _j(ar, (17.0, 53.0)), "A:DM": _j(ar, (27.0, 41.0)), "A:RCM": _j(ar, (27.0, 27.0)),
        "A:LB": _j(ar, (50.0, 63.0)), "A:LCM": _j(ar, (48.0, 45.0)), "A:ST": _j(ar, (58.0, 34.0)),
        "A:LW": _j(ar, (49.0, 23.0)), "A:RW": _j(ar, (52.0, 6.0)),
    }
    init_d = {
        "D:GK": (93.0, 34.0), "D:ST": _j(dr, (19.0, 47.0), 0.6), "D:RW": _j(dr, (24.0, 25.0)),
        "D:LCM": _j(dr, (30.0, 41.5)), "D:RCM": _j(dr, (30.0, 27.0)),
        "D:DM": _j(dr, (53.0, 60.5)), "D:LW": _j(dr, (55.0, 8.5)),
        # the back line starts where its marking puts it (no settling run in the first seconds)
        "D:RB": _j(dr, (51.5, 44.5)), "D:RCB": _j(dr, (60.5, 34.0)), "D:LCB": _j(dr, (52.5, 23.5)),
        "D:LB": _j(dr, (60.0, 15.0)),
    }
    lag = float(dr.uniform(0.3, 0.45))
    segs: dict[str, list[Seg]] = {
        "A:GK": [Seg(0.0, "gk", {"at": (5.5, 34.0), "track": 0.1})],
        "A:RB": [Seg(0.0, "check", {"at": init_a["A:RB"], "amp": 1.2})],
        # the central centre-back of the three holds the centre (the safety) rather than drifting toward the ball
        "A:RCB": [Seg(0.0, "check", {"at": init_a["A:RCB"], "amp": 1.0, "shift": (0.1, 0.05)})],
        "A:LCB": [Seg(0.0, "hold", {"at": init_a["A:LCB"], "shift": (0.0, 0.0)}),
                  Seg(("lcb_ball_arrive", 0.0), "carry", {"to": _j(ar, (19.0, 54.0), 0.5), "speed": 1.0})],
        # the double pivot is the build-up's second line: it holds its height and slides across with the ball
        "A:DM": [Seg(0.0, "check", {"at": init_a["A:DM"], "amp": 1.5, "shift": (0.0, 0.15)})],
        "A:RCM": [Seg(0.0, "check", {"at": init_a["A:RCM"], "amp": 1.5, "shift": (0.0, 0.15)})],
        "A:LB": [Seg(0.0, "check", {"at": init_a["A:LB"], "amp": 1.5})],
        "A:LCM": [Seg(0.0, "check", {"at": init_a["A:LCM"], "amp": 1.5})],
        "A:ST": [Seg(0.0, "check", {"at": init_a["A:ST"], "amp": 1.5})],
        "A:LW": [Seg(0.0, "check", {"at": init_a["A:LW"], "amp": 1.5})],
        "A:RW": [Seg(0.0, "check", {"at": init_a["A:RW"], "amp": 1.5})],
        "D:GK": [Seg(0.0, "gk", {"at": (93.0, 34.0)}), Seg(("kick_release", 0.0), "sweep", {"u_range": (90.0, 103.0)})],
        # first striker: curved press on the left centre-back, standing in his pass back to the middle
        "D:ST": [Seg(0.0, "screen", {"who": "A:RCB", "from": "A:LCB", "frac": 0.7, "speed": 5.0}),
                 Seg(("lcb_ball_release", 0.2 if not escape else 1.0), "press_mark",
                     {"who": "A:LCB", "cut": "A:RCB", "gs": -2.5, "stop": 1.2 if not escape else 2.8, "speed": 7.0})],
        # second striker: shuts the right back's side (stands in the line from the middle to the right back)
        "D:RW": [Seg(0.0, "screen", {"who": "A:RB", "from": "A:RCB", "frac": 0.6, "speed": 6.0})],
        "D:LCM": [Seg(0.0, "mark", {"who": "A:DM", "gs": 1.6, "lag": lag, "speed": 7.0})],
        "D:RCM": [Seg(0.0, "mark", {"who": "A:RCM", "gs": 1.6, "lag": lag, "speed": 7.0})],
        # wide midfielders and the back four take the five high attackers (6 v 5)
        "D:DM": [Seg(0.0, "mark", {"who": "A:LB", "gs": 2.0, "lag": lag, "speed": 6.5})],
        "D:LW": [Seg(0.0, "mark", {"who": "A:RW", "gs": 2.0, "lag": lag, "speed": 6.5})],
        "D:RB": [Seg(0.0, "mark", {"who": "A:LCM", "gs": 3.0, "lag": lag, "speed": 6.5}),
                 Seg(("kick_release", 0.0), "attack_ball", {"speed": 7.5})],
        "D:RCB": [Seg(0.0, "mark", {"who": "A:ST", "gs": 2.5, "lag": lag, "speed": 6.5}),
                  Seg(("kick_release", 0.0), "attack_ball", {"speed": 8.0})],
        "D:LCB": [Seg(0.0, "mark", {"who": "A:LW", "gs": 3.0, "lag": lag, "speed": 6.5})],
        "D:LB": [Seg(0.0, "hold", {"at": init_d["D:LB"], "shift": (0.2, 0.1)})],  # the spare
    }
    ball_steps: list[dict] = [
        {"kind": "pass", "name": "lcb_ball", "from": "A:GK", "to": "A:LCB", "at": float(ar.uniform(1.8, 2.2)),
         "speed": 15.0},
    ]
    if not escape:
        # under the cut press the kick goes long toward the striker but lands in the back line's zone
        ball_steps.append({"kind": "pass", "name": "kick", "from": "A:LCB", "to": "A:ST", "style": "lofted",
                           "speed": 22.0, "lead": 0.0, "aim": (9.0, 0.0), "after_receive_s": float(ar.uniform(2.6, 3.0))})
    else:
        t_back = float(ar.uniform(1.0, 1.3))
        ball_steps += [
            {"kind": "pass", "name": "gk_back", "from": "A:LCB", "to": "A:GK", "after_receive_s": t_back,
             "speed": 15.0},
            # the keeper plays the free centre-back quickly, before the striker can shift across
            {"kind": "pass", "name": "rcb_ball", "from": "A:GK", "to": "A:RCB",
             "after_receive_s": float(ar.uniform(0.8, 1.1)), "speed": 14.0},
            {"kind": "pass", "name": "out_ball", "from": "A:RCB", "to": "A:RCM",
             "after_receive_s": float(ar.uniform(1.8, 2.2)), "speed": 15.0},
        ]
        # the first striker leaves the left centre-back and shifts across once the goalkeeper has it - too late
        segs["D:ST"].append(Seg(("gk_back_arrive", float(dr.uniform(0.6, 0.9))), "press_mark",
                                {"who": "A:RCB", "cut": "A:GK", "gs": -2.0, "stop": 1.6, "speed": 6.8}))
        segs["A:RCB"].append(Seg(("rcb_ball_arrive", 0.0), "carry", {"to": (24.0, 33.0), "speed": 3.0}))
    plan = Plan(duration_s=22.0, init={**init_a, **init_d}, segs=segs, ball_steps=ball_steps, first_owner="A:GK",
                sigma={"D:ST": 0.15, "A:LCB": 0.15}, lag_s=lag)
    truth = {
        "family": "D", "variant": variant,
        # the story starts with the left centre-back on the ball (the goalkeeper phase before it is not part of it);
        # the trap is the presser standing in the lines (central routes screened) next to markers on the short
        # options, so either label names it: the analyzer calls it by whichever it sees first on that carrier, and
        # the evaluation (from holdout-v2-0.6) accepts either, as for C
        "story": {"start": "lcb_ball_arrive", "families": ["press_vs_depth", "central_screen"], "used": escape,
                  **({"restricted": ["short_options_screened"]} if escape else
                     {"restricted_any": ["central_routes_screened", "short_options_screened"]}),
                  "exposed": ["accessible_free_receiver"] if escape else []},
        "generator_intent": "four_player_lane_cut_press" + ("_escaped" if escape else ""),
        "expected": {
            # the goalkeeper phase is screened by men around the ball (A-style), the trap on the left centre-back by
            # the presser standing in his lines (lane-oriented, C-style); in "escape" the trap is never set
            "restricted": ["short_options_screened"] + ([] if escape else ["central_routes_screened"]),
            # pressing 5+GK with 4 always leaves someone free: while the goalkeeper has it, the central
            # centre-back is the free man in both variants; only "escape" actually uses him
            "exposed": ["accessible_free_receiver"],
            "free_man_used": escape,
            "suppressed": [],
            "attacking_response": "back_pass_to_goalkeeper" if escape else "long_kick",
            "defensive_adjustment": "first_striker_shift" if escape else "back_line_wins_ball",
        },
        "params": {"lag_s": round(lag, 3)},
        "roles": {"presser": "D:ST", "second_striker": "D:RW", "target": "A:LCB", "free_man": "A:RCB"},
    }
    plan.steady = _story_keys(truth)
    return plan, truth


# ---------------------------------------------------------------------------- family E
FAMILY_E_VARIANTS = ("step", "hold")


def family_e(attack_rng: np.random.Generator, defense_rng: np.random.Generator, variant: str) -> tuple[Plan, dict]:
    """Passive screen (designed with the user, 2026-10-21): nobody presses the centre-backs; the defence stands in
    the lines their passes would leave by.

    Attack: goalkeeper, two wide centre-backs, a single pivot, full backs at midfield height, two central midfielders
    between the lines, three forwards. Defence (4-4-2 mid-block): the two strikers stand in the lines from the
    centre-backs to the pivot, the wide midfielders in the lines to the full backs, the central midfielders in the
    lines to the attacking central midfielders. The right centre-back gets the ball unpressed and carries it forward.

    step - the defending central midfielder in front of him steps out to stop the carry; the attacking midfielder
           he was screening moves into the space he left and receives (the screen traded for the carry).
    hold - the screen holds and the carry is conceded: the centre-back, still with nobody on him, switches to the
           other centre-back; no one in the middle comes free.
    """
    if variant not in FAMILY_E_VARIANTS:
        raise ValueError(variant)
    ar, dr = attack_rng, defense_rng
    step = variant == "step"
    t_wait = float(ar.uniform(1.4, 1.8))  # the centre-back takes a look before carrying
    carry_to = _j(ar, (29.0, 23.5), 0.5)
    t_out = t_wait + float(dr.uniform(1.9, 2.2))  # when the central midfielder decides to step out
    init_a = {
        "A:GK": (5.5, 34.0), "A:RCB": _j(ar, (16.0, 19.5)), "A:LCB": _j(ar, (16.0, 48.5)),
        "A:RB": _j(ar, (27.0, 5.0)), "A:LB": _j(ar, (27.0, 63.0)), "A:DM": _j(ar, (26.0, 34.0)),
        "A:RCM": _j(ar, (40.0, 27.0)), "A:LCM": _j(ar, (40.0, 42.0)), "A:RW": _j(ar, (57.0, 8.0)),
        "A:LW": _j(ar, (57.0, 60.0)), "A:ST": _j(ar, (60.0, 34.0)),
    }
    # a mid-block: the back line about 42 m from its goal (with the keeper at home, a higher line leaves the whole
    # space behind it open)
    init_d = {
        "D:GK": (94.0, 34.0), "D:ST": _j(dr, (26.0, 27.0)), "D:RW": _j(dr, (26.0, 41.0)),
        "D:LCM": _j(dr, (36.0, 25.5)), "D:RCM": _j(dr, (36.0, 42.5)),
        "D:DM": _j(dr, (23.0, 11.0)), "D:LW": _j(dr, (23.0, 57.0)),
        "D:LB": _j(dr, (61.0, 10.0)), "D:LCB": _j(dr, (63.0, 28.0)), "D:RCB": _j(dr, (63.0, 40.0)),
        "D:RB": _j(dr, (61.0, 58.0)),
    }
    lag = float(dr.uniform(0.3, 0.45))
    segs: dict[str, list[Seg]] = {
        "A:GK": [Seg(0.0, "gk", {"at": (5.5, 34.0), "track": 0.1})],
        "A:RCB": [Seg(0.0, "hold", {"at": init_a["A:RCB"], "shift": (0.0, 0.0)}),
                  Seg(("rcb_ball_arrive", t_wait), "carry", {"to": carry_to, "speed": 3.6})],
        "A:LCB": [Seg(0.0, "check", {"at": init_a["A:LCB"], "amp": 1.0})],
        "A:RB": [Seg(0.0, "check", {"at": init_a["A:RB"], "amp": 1.5})],
        "A:LB": [Seg(0.0, "check", {"at": init_a["A:LB"], "amp": 1.5})],
        # the single pivot holds his depth behind the carry (rest defence) instead of following it into the
        # midfield space; the strikers screening him would come with him
        "A:DM": [Seg(0.0, "check", {"at": init_a["A:DM"], "amp": 1.5, "shift": (0.0, 0.1)})],
        # waits between the lines at his height until he moves into the space the stepping midfielder leaves
        "A:RCM": [Seg(0.0, "check", {"at": init_a["A:RCM"], "amp": 1.2, "shift": (0.0, 0.1)})],
        "A:LCM": [Seg(0.0, "check", {"at": init_a["A:LCM"], "amp": 1.2})],
        # the front three hold their height and width against the mid-block (pinning the back four), waiting
        "A:RW": [Seg(0.0, "check", {"at": init_a["A:RW"], "amp": 1.5, "shift": (0.05, 0.1)})],
        "A:LW": [Seg(0.0, "check", {"at": init_a["A:LW"], "amp": 1.5, "shift": (0.05, 0.1)})],
        "A:ST": [Seg(0.0, "check", {"at": init_a["A:ST"], "amp": 1.5, "shift": (0.05, 0.1)})],
        "D:GK": [Seg(0.0, "gk", {"at": (94.0, 34.0)})],
        # strikers: in the lines from each centre-back to the pivot, never onto the centre-backs
        "D:ST": [Seg(0.0, "screen", {"who": "A:DM", "from": "A:RCB", "frac": 0.5, "speed": 5.0})],
        "D:RW": [Seg(0.0, "screen", {"who": "A:DM", "from": "A:LCB", "frac": 0.5, "speed": 5.0})],
        # central midfielders: in the lines to the attacking central midfielders, close to the receivers
        "D:LCM": [Seg(0.0, "screen", {"who": "A:RCM", "from": "A:RCB", "frac": 0.82, "speed": 6.0})],
        "D:RCM": [Seg(0.0, "screen", {"who": "A:LCM", "from": "A:LCB", "frac": 0.82, "speed": 6.0})],
        # wide midfielders: in the lines to the full backs
        "D:DM": [Seg(0.0, "screen", {"who": "A:RB", "from": "A:RCB", "frac": 0.6, "speed": 6.0})],
        "D:LW": [Seg(0.0, "screen", {"who": "A:LB", "from": "A:LCB", "frac": 0.6, "speed": 6.0})],
        "D:LB": [Seg(0.0, "mark", {"who": "A:RW", "gs": 3.0, "lag": lag, "speed": 6.5})],
        "D:LCB": [Seg(0.0, "mark", {"who": "A:ST", "gs": 2.5, "lag": lag, "speed": 6.5})],
        "D:RCB": [Seg(0.0, "hold", {"at": init_d["D:RCB"], "shift": (0.2, 0.1)})],
        "D:RB": [Seg(0.0, "mark", {"who": "A:LW", "gs": 3.0, "lag": lag, "speed": 6.5})],
    }
    ball_steps: list[dict] = [
        {"kind": "pass", "name": "rcb_ball", "from": "A:GK", "to": "A:RCB", "at": float(ar.uniform(0.9, 1.2)),
         "speed": 15.0},
    ]
    if step:
        # steps out to stop the carry, straight in front of the ball - not along the line to the man behind him
        segs["D:LCM"].append(Seg(("rcb_ball_arrive", t_out), "press",
                                 {"cut": (70.0, carry_to[1]), "stop": 1.8, "speed": 7.2}))
        # the attacking midfielder moves into the space the stepping midfielder left
        segs["A:RCM"].append(Seg(("rcb_ball_arrive", t_out + 0.3), "run", {"to": _j(ar, (37.5, 33.0), 0.5),
                                                                          "speed": 5.0}))
        ball_steps.append({"kind": "pass", "name": "free_ball", "from": "A:RCB", "to": "A:RCM", "speed": 15.0,
                           "after_receive_s": t_out + float(ar.uniform(2.5, 2.8))})
    else:
        # the switch goes over the screen to the far full back (a ground ball to the other centre-back would run
        # past both strikers)
        ball_steps.append({"kind": "pass", "name": "switch", "from": "A:RCB", "to": "A:LB", "style": "lofted",
                           "speed": 20.0, "after_receive_s": t_out + float(ar.uniform(2.0, 2.4))})
    plan = Plan(duration_s=20.0, init={**init_a, **init_d}, segs=segs, ball_steps=ball_steps, first_owner="A:GK",
                sigma={"A:RCB": 0.15, "D:LCM": 0.15}, lag_s=lag)
    truth = {
        "family": "E", "variant": variant,
        "story": {"start": "rcb_ball_arrive", "families": ["press_vs_depth"], "used": step, "optional": []},
        "generator_intent": "passive_lane_screen" + ("_step_out" if step else "_holds"),
        "expected": {
            "restricted": ["short_options_screened"],
            "exposed": ["accessible_free_receiver"] if step else [],
            "suppressed": [] if step else ["accessible_free_receiver"],
            "attacking_response": "centre_back_carry",
            "defensive_adjustment": "central_midfielder_steps_out" if step else "screen_holds",
        },
        "params": {"t_wait": round(t_wait, 3), "t_out": round(t_out, 3), "carry_to": carry_to, "lag_s": round(lag, 3)},
        "roles": {"carrier": "A:RCB", "stepper": "D:LCM", "free_man": "A:RCM"},
    }
    plan.steady = _story_keys(truth)
    return plan, truth


# ---------------------------------------------------------------------------- family G
FAMILY_G_VARIANTS = ("main", "offside")


def family_g(attack_rng: np.random.Generator, defense_rng: np.random.Generator, variant: str) -> tuple[Plan, dict]:
    """Back-pass trigger (designed with the user, 2026-10-21): a mid-block waits; when the ball goes back to a
    centre-back the whole team steps up - the strikers press him, the midfield picks up its men and the back line
    moves up about ten metres together.

    main    - the striker drops with the line and stays onside, then turns and runs in behind; the centre-back
              plays over the top before the back line can turn.
    offside - the striker stays where he was, so the step-up leaves him in an offside position; his later run starts
              from there and the centre-back plays back to the goalkeeper instead.
    """
    if variant not in FAMILY_G_VARIANTS:
        raise ValueError(variant)
    ar, dr = attack_rng, defense_rng
    main = variant == "main"
    t_bp = float(ar.uniform(1.4, 1.8))  # the back pass
    react = float(dr.uniform(0.2, 0.35))
    step_u = float(dr.uniform(57.0, 58.5))  # where the back line steps up to
    t_go = float(ar.uniform(3.0, 3.4))  # after the centre-back receives, the striker turns and runs
    run_to = _j(ar, (86.0, 51.0), 1.0)  # diagonal, into the channel between centre-back and full back
    init_a = {
        "A:GK": (5.5, 34.0), "A:RCB": _j(ar, (19.0, 22.0)), "A:LCB": _j(ar, (19.0, 46.0)),
        "A:RB": _j(ar, (33.0, 6.0)), "A:LB": _j(ar, (33.0, 62.0)), "A:DM": _j(ar, (30.0, 34.0)),
        "A:RCM": _j(ar, (38.0, 27.0)), "A:LCM": _j(ar, (41.0, 42.0)), "A:RW": _j(ar, (58.0, 9.0)),
        "A:LW": _j(ar, (58.0, 59.0)), "A:ST": _j(ar, (63.0, 47.5), 0.6),
    }
    line_u = float(dr.uniform(66.5, 67.5))  # the mid-block's back line before the trigger
    init_d = {
        "D:GK": (96.0, 34.0), "D:ST": _j(dr, (44.0, 30.0)), "D:RW": _j(dr, (44.0, 41.0)),
        "D:LCM": _j(dr, (50.0, 27.0)), "D:RCM": _j(dr, (50.0, 41.0)),
        "D:DM": _j(dr, (49.0, 11.0)), "D:LW": _j(dr, (49.0, 57.0)),
        "D:LB": (line_u - 0.5, 12.0), "D:LCB": (line_u, 28.5), "D:RCB": (line_u, 40.0), "D:RB": (line_u - 0.5, 56.0),
    }
    go = ("bp_release", react)
    lag = float(dr.uniform(0.3, 0.45))
    segs: dict[str, list[Seg]] = {
        "A:GK": [Seg(0.0, "gk", {"at": (5.5, 34.0), "track": 0.1})],
        "A:RCB": [Seg(0.0, "check", {"at": init_a["A:RCB"], "amp": 1.0}),
                  Seg(("bp_arrive", 0.0), "carry", {"to": _j(ar, (22.0, 20.0), 0.5), "speed": 1.5})],
        "A:LCB": [Seg(0.0, "check", {"at": init_a["A:LCB"], "amp": 1.0})],
        # the full backs stay high and wide to stretch the block; they do not come back with the back pass
        "A:RB": [Seg(0.0, "check", {"at": init_a["A:RB"], "amp": 1.5, "shift": (0.0, 0.1)})],
        "A:LB": [Seg(0.0, "check", {"at": init_a["A:LB"], "amp": 1.5, "shift": (0.0, 0.1)})],
        # the single pivot holds in front of the centre-backs (rest defence) instead of following the back pass
        "A:DM": [Seg(0.0, "check", {"at": init_a["A:DM"], "amp": 1.5, "shift": (0.1, 0.1)})],
        "A:RCM": [Seg(0.0, "hold", {"at": init_a["A:RCM"], "shift": (0.0, 0.0)}),
                  Seg(("bp_release", 0.4), "check", {"at": init_a["A:RCM"], "amp": 1.5})],
        "A:LCM": [Seg(0.0, "check", {"at": init_a["A:LCM"], "amp": 1.5})],
        "A:RW": [Seg(0.0, "check", {"at": init_a["A:RW"], "amp": 1.2})],
        "A:LW": [Seg(0.0, "check", {"at": init_a["A:LW"], "amp": 1.2})],
        "A:ST": [Seg(0.0, "check", {"at": init_a["A:ST"], "amp": 0.8})],
        "D:GK": [Seg(0.0, "gk", {"at": (96.0, 34.0)}), Seg(("long_ball_release", 0.0), "sweep", {"u_range": (86.0, 103.0)})],
        # the mid-block before the trigger: strikers and midfield screen, the back line holds its height
        "D:ST": [Seg(0.0, "screen", {"who": "A:DM", "frac": 0.45, "speed": 5.0}),
                 Seg(go, "press_mark", {"who": "A:RCB", "cut": "A:LCB", "gs": -2.5, "stop": 1.3, "speed": 7.6})],
        "D:RW": [Seg(0.0, "screen", {"who": "A:LCM", "frac": 0.4, "speed": 5.0}),
                 Seg(go, "screen", {"who": "A:DM", "from": "A:RCB", "frac": 0.5, "speed": 7.2})],
        "D:LCM": [Seg(0.0, "screen", {"who": "A:RCM", "frac": 0.0, "speed": 3.0}),
                  Seg(go, "mark", {"who": "A:RCM", "gs": 1.5, "lag": lag, "speed": 7.2})],
        "D:RCM": [Seg(0.0, "screen", {"who": "A:LCM", "frac": 0.7, "speed": 5.0}),
                  Seg(go, "mark", {"who": "A:LCM", "gs": 1.5, "lag": lag, "speed": 7.2})],
        "D:DM": [Seg(0.0, "hold", {"at": init_d["D:DM"], "shift": (0.1, 0.1)}),
                 Seg(go, "mark", {"who": "A:RB", "gs": 1.6, "lag": lag, "speed": 7.0})],
        "D:LW": [Seg(0.0, "hold", {"at": init_d["D:LW"], "shift": (0.1, 0.1)}),
                 Seg(go, "mark", {"who": "A:LB", "gs": 1.6, "lag": lag, "speed": 7.0})],
    }
    # the back line steps up together, then turns and recovers once the ball goes over it
    for key, y in (("D:LB", 12.0), ("D:LCB", 28.5), ("D:RCB", 40.0), ("D:RB", 56.0)):
        segs[key] = [Seg(0.0, "hold", {"at": init_d[key], "shift": (0.1, 0.05)}),
                     # keeps the stepped-up height exactly (the offside line); in "offside", where the ball then goes
                     # back to the goalkeeper and the line stays up, it shuffles across with the ball meanwhile
                     Seg(go, "hold", {"at": (step_u - (0.5 if key in ("D:LB", "D:RB") else 0.0), y),
                                      "shift": (0.0, 0.0 if main else 0.15), "speed": 6.0})]
    # the centre-back on the side of the run turns and chases him; the other holds his side of the pitch and sprints
    # back toward his box to cover (he does not cross over to follow the striker)
    segs["D:RCB"].append(Seg(("long_ball_release", 0.0), "chase", {"who": "A:ST", "speed": 8.0}))
    segs["D:LCB"].append(Seg(("long_ball_release", 0.0), "recover", {"du": 26.0, "speed": 7.6}))
    ball_steps: list[dict] = [
        {"kind": "pass", "name": "bp", "from": "A:RCM", "to": "A:RCB", "at": t_bp, "speed": 15.0},
    ]
    if main:
        # drops with the line to stay onside, then turns and runs in behind
        segs["A:ST"] += [Seg(("bp_release", 0.5), "run", {"to": (step_u - 5.5, 47.5), "speed": 5.0}),
                         Seg(("bp_arrive", t_go), "run", {"to": run_to, "speed": 8.2}),
                         Seg(("long_ball_arrive", 0.0), "carry", {"to": (95.0, 40.0), "speed": 6.0})]
        ball_steps.append({"kind": "pass", "name": "long_ball", "from": "A:RCB", "to": "A:ST", "style": "lofted",
                           "speed": 22.0, "lead": 0.95, "run_speed": 7.6, "after_receive_s": t_go + 0.3,
                           "trigger": {"kind": "onside_line", "runner": "A:ST", "margin_m": 2.0, "min_speed": 5.0}})
        # through on goal, he shoots once he is in the box (instead of dribbling up to the keeper)
        ball_steps.append({"kind": "shot", "name": "shot", "from": "A:ST", "after_receive_s": float(ar.uniform(1.8, 2.2)),
                           "speed": 24.0})
    else:
        # stays where he was; the step-up leaves him offside, and he runs from there, then walks back onside once
        # the ball has gone back
        segs["A:ST"] += [Seg(("bp_arrive", t_go), "run", {"to": run_to, "speed": 7.5}),
                         Seg(("back_to_gk_release", 0.8), "run", {"to": (step_u - 4.0, 46.0), "speed": 3.5})]
        ball_steps.append({"kind": "pass", "name": "back_to_gk", "from": "A:RCB", "to": "A:GK", "speed": 15.0,
                           "after_receive_s": t_go + float(ar.uniform(0.8, 1.1))})
    plan = Plan(duration_s=20.0, init={**init_a, **init_d}, segs=segs, ball_steps=ball_steps, first_owner="A:RCM",
                sigma={"A:ST": 0.15, "D:ST": 0.15}, lag_s=lag)
    truth = {
        "family": "G", "variant": variant,
        "story": {"start": "bp_arrive", "families": ["press_vs_depth"], "used": main,
                  "optional": ["accessible_free_receiver"]},
        "generator_intent": "back_pass_trigger_step_up" + ("" if main else "_striker_left_offside"),
        "expected": {
            "restricted": ["short_options_screened"],
            "exposed": ["behind_space_exposed"] if main else [],
            "suppressed": [] if main else ["behind_space_exposed"],
            "attacking_response": "back_pass" + ("_then_run_in_behind" if main else ""),
            "defensive_adjustment": "whole_line_steps_up",
        },
        "params": {"t_bp": round(t_bp, 3), "react": round(react, 3), "line_u": round(line_u, 3),
                   "step_u": round(step_u, 3), "t_go": round(t_go, 3), "run_to": run_to, "lag_s": round(lag, 3)},
        "roles": {"carrier": "A:RCB", "presser": "D:ST", "runner": "A:ST", "back_line": ["D:LB", "D:LCB", "D:RCB",
                                                                                          "D:RB"]},
    }
    plan.steady = _story_keys(truth)
    return plan, truth
