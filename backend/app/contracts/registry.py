"""Versioned allowlists referenced by the contracts (spec 11.1).

Metric keys carry their unit so a metric cannot change meaning silently.
Style tokens are the only styling a payload may request; the frontend maps
them to drawing rules, so no raw CSS/SVG/HTML ever travels in a payload.
"""

from __future__ import annotations

from typing import Literal

METRIC_REGISTRY_VERSION = "metrics/2.0.0"

MetricUnit = Literal["m", "s", "mps", "count", "ratio", "score"]

METRIC_UNITS: dict[str, str] = {
    # lines (7.2)
    "back_line_depth_m": "m",
    "mid_back_gap_m": "m",
    "front_back_span_m": "m",
    "back_line_spread_m": "m",
    "behind_depth_change_m": "m",
    # marking (7.4)
    "supported_marking_edges": "count",
    "zone_opponents": "count",
    "zone_marking_coverage": "ratio",
    "marking_follow_support": "score",
    # pressure (7.3)
    "presser_distance_m": "m",
    "presser_closing_speed_mps": "mps",
    # lanes (8.1)
    "short_option_candidates": "count",
    "short_options_screened": "count",
    "short_options_open": "count",
    "central_candidates": "count",
    "central_screened": "count",
    "wide_open_outlets": "count",
    "lane_arrival_margin_s": "s",
    "press_cut_lanes": "count",
    "carrier_nearest_defender_m": "m",
    "carrier_closing_mps": "mps",
    "back_line_step_m": "m",
    "buildup_count_attack": "count",
    "buildup_count_defense": "count",
    "rest_count_attack": "count",
    "rest_count_defense": "count",
    # cover (8.3)
    "runner_forward_speed_mps": "mps",
    "attacker_arrival_s": "s",
    "cover_arrival_s": "s",
    "cover_margin_s": "s",
    "gk_cover_margin_s": "s",
    # regions (8.4)
    "central_count_attack": "count",
    "central_count_defense": "count",
    "receiver_margin_s": "s",
    "nearest_defender_m": "m",
    # changes (9.3)
    "attacker_drop_m": "m",
    "attacker_move_m": "m",
    "defender_step_m": "m",
    "mid_line_shift_m": "m",
}

CLAIM_CODES = (
    "short_options_screened", "central_routes_screened", "behind_space_exposed", "behind_depth_reduced",
    "press_block_disconnected", "between_lines_receiver", "isolated_1v1", "wide_outlet_available",
    "weak_side_accessible", "local_central_overload", "accessible_free_receiver", "second_ball_support_gap",
)

CLAIM_DIMENSION: dict[str, str] = {
    "short_options_screened": "restricted",
    "central_routes_screened": "restricted",
    "behind_depth_reduced": "restricted",
    "behind_space_exposed": "exposed",
    "press_block_disconnected": "exposed",
    "between_lines_receiver": "exposed",
    "isolated_1v1": "exposed",
    "wide_outlet_available": "exposed",
    "weak_side_accessible": "exposed",
    "accessible_free_receiver": "exposed",
    "second_ball_support_gap": "exposed",
    "local_central_overload": "context",
}

LAYERS = ("structure", "marking", "pressure", "screened_lanes", "outlets", "exposure", "cover", "numbers",
          "changes", "caption")

STYLE_TOKENS = frozenset({
    "line_back", "line_mid", "line_front", "line_label",
    "mark_supported", "mark_weak",
    "press_arrow", "carrier_ring",
    "lane_screened", "screen_marker", "lane_open", "outlet_target",
    "exposure_hatched", "runner_ring",
    "cover_link", "cover_late", "cover_in_time", "cover_label",
    "region_outline", "count_label", "player_count_ring", "free_receiver_ring", "carrier_space_ring",
    "ghost_path", "change_label", "press_cut_lane",
    "caption_label",
})
