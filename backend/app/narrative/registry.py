"""Bilingual sentence and label templates (spec 12.1: template renderer).

Every template declares what it may be attached to (a claim code, a change
code, an outcome status or a caveat condition) and which metric keys it
reads. Agents may only pick template keys from this registry; the renderer
inserts real metric values and player names, so no number is ever taken from
free text. Placeholders:
  {v:<metric_key>}            value of the plan's metric with that key
  {p:<metric_key>:<index>}    display name of that metric's player_ids[index]
  {team:defending} / {team:attacking}
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

REGISTRY_VERSION = "templates-0.3.1"


@dataclass(frozen=True)
class Template:
    key: str
    audience: tuple[str, ...]
    attach: str  # claim:<code> | change:<code> | outcome:<status> | caveat:<code> | partial
    metric_keys: tuple[str, ...]
    text: dict[str, str] = field(default_factory=dict)  # language -> template
    # extra condition on the metric values, for wording that implies more than the numbers (e.g. "still keep")
    requires: Callable[[dict[str, float]], bool] | None = None


T = Template
# "fewer around the ball and still more behind" is only said when both counts say so
PRESS_FEWER = (lambda v: v["buildup_count_defense"] < v["buildup_count_attack"]
               and v["rest_count_defense"] > v["rest_count_attack"])
# "nobody presses him" needs real room, not a defender who happens to be 2 m off this frame
UNPRESSED = (lambda v: v["carrier_nearest_defender_m"] >= 5.0)

TEMPLATES: dict[str, Template] = {t.key: t for t in [
    # ---------------------------------------------------------------- restricted
    T("fan.short_options_screened", ("fan",), "claim:short_options_screened",
      ("short_options_screened", "short_option_candidates"), {
          "en": "{team:defending} are shutting the short passes: {v:short_options_screened} of "
                "{v:short_option_candidates} nearby options are covered.",
          "ko": "{team:defending} 수비가 짧은 패스를 막고 있습니다. 가까운 패스 옵션 "
                "{v:short_option_candidates}개 중 {v:short_options_screened}개가 막혔습니다."}),
    T("analyst.short_options_screened", ("analyst",), "claim:short_options_screened",
      ("short_options_screened", "short_option_candidates"), {
          "en": "Restricted: {v:short_options_screened}/{v:short_option_candidates} eligible ground-pass options "
                "from {p:short_option_candidates:0} are screened (a defender reaches the lane or the receiver "
                "within 0.1 s of the ball).",
          "ko": "제한: {p:short_option_candidates:0} 선수의 지상 패스 후보 {v:short_option_candidates}개 중 "
                "{v:short_options_screened}개가 차단된 것으로 추정됩니다(수비가 공보다 0.1초 이내로 먼저 도달)."}),
    T("analyst.short_options_press", ("analyst",), "claim:short_options_screened", ("presser_distance_m",), {
          "en": "{p:presser_distance_m:0} is pressing the carrier from {v:presser_distance_m} m.",
          "ko": "{p:presser_distance_m:0} 선수가 {v:presser_distance_m}m 거리에서 볼 보유자를 압박합니다."}),
    T("analyst.zone_marking", ("analyst",), "claim:short_options_screened", ("zone_marking_coverage",), {
          "en": "Near the ball, {v:zone_marking_coverage} of attackers have a sustained marker "
                "(man-oriented relations, heuristic).",
          "ko": "공 주변 공격수의 {v:zone_marking_coverage}가 지속적인 마크를 받고 있습니다"
                "(대인 관계 추정, 휴리스틱)."}),
    # ---------------------------------------------------------------- exposed
    T("fan.behind_space_exposed", ("fan",), "claim:behind_space_exposed",
      ("back_line_depth_m", "runner_forward_speed_mps"), {
          "en": "The cost: the back line sits {v:back_line_depth_m} m from its goal, and "
                "{p:runner_forward_speed_mps:0} is running into the space behind.",
          "ko": "대가는 뒷공간입니다. 수비 라인이 자기 골문에서 {v:back_line_depth_m}m 올라와 있고, "
                "{p:runner_forward_speed_mps:0} 선수가 그 뒤로 뛰어듭니다."}),
    T("analyst.behind_space_exposed", ("analyst",), "claim:behind_space_exposed",
      ("back_line_depth_m", "cover_margin_s"), {
          "en": "Exposed: back line {v:back_line_depth_m} m from its own goal; the nearest secondary cover reaches "
                "{p:cover_margin_s:0}'s projected point {v:cover_margin_s} s after him.",
          "ko": "노출: 수비 라인이 자기 골문에서 {v:back_line_depth_m}m 떨어져 있고, 가장 가까운 2차 커버는 "
                "{p:cover_margin_s:0} 선수의 예상 지점에 {v:cover_margin_s}초 늦게 도달합니다."}),
    T("fan.isolated_1v1", ("fan",), "claim:isolated_1v1", ("marking_follow_support",), {
          "en": "{p:marking_follow_support:1} is one against one with {p:marking_follow_support:0}, with no help "
                "arriving first.",
          "ko": "{p:marking_follow_support:1} 선수와 {p:marking_follow_support:0} 선수의 1대1이고, "
                "먼저 도착할 도움 수비가 없습니다."}),
    T("analyst.isolated_1v1", ("analyst",), "claim:isolated_1v1", ("cover_margin_s", "gk_cover_margin_s"), {
          "en": "Isolation (heuristic): secondary cover {v:cover_margin_s} s late and goalkeeper "
                "{v:gk_cover_margin_s} s late to {p:cover_margin_s:0}'s projected point.",
          "ko": "고립(휴리스틱): {p:cover_margin_s:0} 선수의 예상 지점에 2차 커버는 {v:cover_margin_s}초, "
                "골키퍼는 {v:gk_cover_margin_s}초 늦습니다."}),
    # ---------------------------------------------------------------- changes
    T("analyst.change.line_retreat", ("analyst", "fan"), "change:line_retreat", ("behind_depth_change_m",), {
          "en": "Observed next: the back line dropped {a:behind_depth_change_m} m toward its own goal.",
          "ko": "이어서 관측된 변화: 수비 라인이 자기 골문 쪽으로 {a:behind_depth_change_m}m 내려섰습니다."}),
    # ---------------------------------------------------------------- outcomes (observed after the analysis)
    T("outcome.exploited", ("fan", "analyst"), "outcome:exploited_in_synthetic_play", (), {
          "en": "What happened next (synthetic play): a pass into that space was received by {outcome_target}.",
          "ko": "이후 관측된 결과(합성 플레이): 그 공간으로 간 패스를 {outcome_target} 선수가 받았습니다."}),
    T("outcome.not_exploited", ("fan", "analyst"), "outcome:not_exploited_in_window", (), {
          "en": "What happened next: the space was not used within the observation window.",
          "ko": "이후 관측된 결과: 관측 구간 안에서 그 공간은 활용되지 않았습니다."}),
    T("outcome.pending", ("fan", "analyst"), "outcome:not_yet_observed", (), {
          "en": "The outcome has not been observed yet.",
          "ko": "결과는 아직 관측되지 않았습니다."}),
    T("outcome.inconclusive", ("fan", "analyst"), "outcome:inconclusive", (), {
          "en": "The moment ended with nothing left open to use, so there is no outcome to judge.",
          "ko": "내준 공간 없이 장면이 끝나 판단할 결과가 없습니다."}),
    # ---------------------------------------------------------------- caveats
    T("caveat.heuristic", ("analyst",), "caveat:heuristic", (), {
          "en": "Scores are heuristic evidence strength under fixed speed assumptions, not probabilities.",
          "ko": "점수는 고정 속도 가정에 따른 휴리스틱 근거 강도이며 확률이 아닙니다."}),
    T("caveat.cover_in_time", ("fan", "analyst"), "caveat:cover_in_time", (), {
          "en": "Cover arrives in time here, so no isolation is claimed.",
          "ko": "여기서는 커버가 제때 도착하므로 고립으로 보지 않습니다."}),
    T("partial.restricted_only", ("fan", "analyst"), "partial", (), {
          "en": "Only the restricting side is supported so far; nothing the defence leaves open is supported yet.",
          "ko": "지금까지는 제한하는 쪽만 근거가 있고, 수비가 내준 공간은 아직 근거로 뒷받침되지 않습니다."}),
    T("analyst.press_cut.short", ("analyst",), "claim:short_options_screened", ("press_cut_lanes",), {
          "en": "{p:press_cut_lanes:0} presses the carrier while standing in the passing line, cutting "
                "{v:press_cut_lanes} option{s:press_cut_lanes} (approximate cover shadow; body orientation is not observed).",
          "ko": "{p:press_cut_lanes:0} 선수가 볼 보유자를 압박하면서 패스 길 위에 서서 옵션 {v:press_cut_lanes}개를 "
                "자릅니다(몸 방향은 관측되지 않아 대략적인 커버 섀도로 추정)."}),
    T("fan.press_cut.short", ("fan",), "claim:short_options_screened", ("press_cut_lanes",), {
          "en": "{p:press_cut_lanes:0} closes down the player on the ball and cuts the pass at the same time.",
          "ko": "{p:press_cut_lanes:0} 선수가 공을 가진 선수를 압박하면서 동시에 패스 길을 자릅니다."}),
    T("analyst.press_numbers.short", ("analyst",), "claim:short_options_screened",
      ("buildup_count_attack", "buildup_count_defense", "rest_count_attack", "rest_count_defense"), {
          "en": "Numbers: {v:buildup_count_defense} defenders against {v:buildup_count_attack} attackers around the "
                "ball; further up the defence keeps {v:rest_count_defense} v {v:rest_count_attack}.",
          "ko": "숫자: 공 주변에서는 수비 {v:buildup_count_defense}명이 공격 {v:buildup_count_attack}명을 상대하고, "
                "그 앞에서는 수비가 {v:rest_count_defense} 대 {v:rest_count_attack} 구도를 유지합니다."}),
    T("fan.press_numbers.short", ("fan",), "claim:short_options_screened",
      ("buildup_count_attack", "buildup_count_defense", "rest_count_attack", "rest_count_defense"), {
          "en": "Around the ball they face {v:buildup_count_attack} with {v:buildup_count_defense} and still keep "
                "{v:rest_count_defense} v {v:rest_count_attack} behind.",
          "ko": "공 주변에서는 {v:buildup_count_defense}명으로 {v:buildup_count_attack}명을 상대하면서도 뒤에서는 "
                "{v:rest_count_defense} 대 {v:rest_count_attack} 구도를 지킵니다."}, requires=PRESS_FEWER),
    T("analyst.passive_screen.short", ("analyst",), "claim:short_options_screened", ("carrier_nearest_defender_m",), {
          "en": "Nobody presses {p:carrier_nearest_defender_m:0} (nearest defender {v:carrier_nearest_defender_m} m): "
                "the screen stands in his passing lines instead (passive screen, heuristic).",
          "ko": "{p:carrier_nearest_defender_m:0} 선수를 압박하는 수비는 없습니다(가장 가까운 수비 "
                "{v:carrier_nearest_defender_m}m). 대신 그의 패스 길 위에 서서 막습니다(수동적 차단, 휴리스틱)."},
      requires=UNPRESSED),
    T("fan.passive_screen.short", ("fan",), "claim:short_options_screened", ("carrier_nearest_defender_m",), {
          "en": "Nobody closes {p:carrier_nearest_defender_m:0} down (nearest defender {v:carrier_nearest_defender_m} m); "
                "they just stand in his passing lines.",
          "ko": "아무도 {p:carrier_nearest_defender_m:0} 선수에게 달려들지 않고(가장 가까운 수비 "
                "{v:carrier_nearest_defender_m}m), 그의 패스 길 위에 서서 막기만 합니다."},
      requires=UNPRESSED),
    T("analyst.press_cut.central", ("analyst",), "claim:central_routes_screened", ("press_cut_lanes",), {
          "en": "{p:press_cut_lanes:0} presses the carrier while standing in the passing line, cutting "
                "{v:press_cut_lanes} option{s:press_cut_lanes} (approximate cover shadow; body orientation is not observed).",
          "ko": "{p:press_cut_lanes:0} 선수가 볼 보유자를 압박하면서 패스 길 위에 서서 옵션 {v:press_cut_lanes}개를 "
                "자릅니다(몸 방향은 관측되지 않아 대략적인 커버 섀도로 추정)."}),
    T("fan.press_cut.central", ("fan",), "claim:central_routes_screened", ("press_cut_lanes",), {
          "en": "{p:press_cut_lanes:0} closes down the player on the ball and cuts the pass at the same time.",
          "ko": "{p:press_cut_lanes:0} 선수가 공을 가진 선수를 압박하면서 동시에 패스 길을 자릅니다."}),
    T("analyst.press_numbers.central", ("analyst",), "claim:central_routes_screened",
      ("buildup_count_attack", "buildup_count_defense", "rest_count_attack", "rest_count_defense"), {
          "en": "Numbers: {v:buildup_count_defense} defenders against {v:buildup_count_attack} attackers around the "
                "ball; further up the defence keeps {v:rest_count_defense} v {v:rest_count_attack}.",
          "ko": "숫자: 공 주변에서는 수비 {v:buildup_count_defense}명이 공격 {v:buildup_count_attack}명을 상대하고, "
                "그 앞에서는 수비가 {v:rest_count_defense} 대 {v:rest_count_attack} 구도를 유지합니다."}),
    T("fan.press_numbers.central", ("fan",), "claim:central_routes_screened",
      ("buildup_count_attack", "buildup_count_defense", "rest_count_attack", "rest_count_defense"), {
          "en": "Around the ball they face {v:buildup_count_attack} with {v:buildup_count_defense} and still keep "
                "{v:rest_count_defense} v {v:rest_count_attack} behind.",
          "ko": "공 주변에서는 {v:buildup_count_defense}명으로 {v:buildup_count_attack}명을 상대하면서도 뒤에서는 "
                "{v:rest_count_defense} 대 {v:rest_count_attack} 구도를 지킵니다."}, requires=PRESS_FEWER),
    # ---------------------------------------------------------------- family B
    T("fan.behind_depth_reduced", ("fan",), "claim:behind_depth_reduced", ("back_line_depth_m",), {
          "en": "{team:defending} have taken the space behind away: their back line now sits {v:back_line_depth_m} m "
                "from goal.",
          "ko": "{team:defending} 수비가 뒷공간을 줄였습니다. 수비 라인이 이제 골문에서 {v:back_line_depth_m}m에 "
                "있습니다."}),
    T("analyst.behind_depth_reduced", ("analyst",), "claim:behind_depth_reduced",
      ("back_line_depth_m", "behind_depth_change_m"), {
          "en": "Restricted: the same back line dropped {a:behind_depth_change_m} m toward its goal within the window "
                "and now sits {v:back_line_depth_m} m from it, leaving less space behind.",
          "ko": "제한: 같은 수비 라인이 관측 구간 안에서 골문 쪽으로 {a:behind_depth_change_m}m 내려서 지금은 골문에서 "
                "{v:back_line_depth_m}m에 있습니다. 라인 뒤 공간이 줄었습니다."}),
    T("fan.press_block_disconnected", ("fan",), "claim:press_block_disconnected", ("mid_back_gap_m",), {
          "en": "The cost: the press up front has left a {v:mid_back_gap_m} m gap in front of that line.",
          "ko": "대가는 간격입니다. 앞선의 압박이 수비 라인 앞에 {v:mid_back_gap_m}m 간격을 남겼습니다."}),
    T("analyst.press_block_disconnected", ("analyst",), "claim:press_block_disconnected",
      ("mid_back_gap_m", "presser_distance_m"), {
          "en": "Exposed: the press is active ({p:presser_distance_m:0} at {v:presser_distance_m} m) while the midfield "
                "line sits {v:mid_back_gap_m} m in front of the back line (heuristic gate 20 m).",
          "ko": "노출: 압박이 진행 중이고({p:presser_distance_m:0} 선수, {v:presser_distance_m}m) 미드필드 라인이 "
                "수비 라인 앞 {v:mid_back_gap_m}m에 있습니다(휴리스틱 기준 20m)."}),
    T("fan.between_lines_receiver", ("fan",), "claim:between_lines_receiver", ("receiver_margin_s",), {
          "en": "{p:receiver_margin_s:0} is waiting between the lines, and the pass to him is on.",
          "ko": "{p:receiver_margin_s:0} 선수가 라인 사이에서 기다리고 있고, 그에게 가는 패스 길이 열려 있습니다."}),
    T("analyst.between_lines_receiver", ("analyst",), "claim:between_lines_receiver",
      ("receiver_margin_s", "nearest_defender_m"), {
          "en": "Exposed: {p:receiver_margin_s:0} stands between the identified lines with an open ground-pass lane; "
                "reception margin {v:receiver_margin_s} s, nearest defender {v:nearest_defender_m} m.",
          "ko": "노출: {p:receiver_margin_s:0} 선수가 식별된 두 라인 사이에 있고 지상 패스 길이 열려 있습니다. "
                "수신 여유 {v:receiver_margin_s}초, 가장 가까운 수비 {v:nearest_defender_m}m."}),
    # ---------------------------------------------------------------- family C
    T("fan.central_routes_screened", ("fan",), "claim:central_routes_screened",
      ("central_screened", "central_candidates"), {
          "en": "{team:defending} are blocking the passes through the middle: {v:central_screened} of "
                "{v:central_candidates} central options are covered.",
          "ko": "{team:defending} 수비가 가운데 패스를 막고 있습니다. 중앙 옵션 {v:central_candidates}개 중 "
                "{v:central_screened}개가 막혔습니다."}),
    T("analyst.central_routes_screened", ("analyst",), "claim:central_routes_screened",
      ("central_screened", "central_candidates"), {
          "en": "Restricted: {v:central_screened}/{v:central_candidates} eligible central ground-pass options from "
                "{p:central_candidates:0} are screened (lane-oriented screening, heuristic).",
          "ko": "제한: {p:central_candidates:0} 선수의 중앙 지상 패스 후보 {v:central_candidates}개 중 "
                "{v:central_screened}개가 차단된 것으로 추정됩니다(레인 지향 차단, 휴리스틱)."}),
    T("analyst.central_press", ("analyst",), "claim:central_routes_screened", ("presser_distance_m",), {
          "en": "{p:presser_distance_m:0} is pressing the carrier from {v:presser_distance_m} m.",
          "ko": "{p:presser_distance_m:0} 선수가 {v:presser_distance_m}m 거리에서 볼 보유자를 압박합니다."}),
    T("fan.wide_outlet_available", ("fan",), "claim:wide_outlet_available", ("lane_arrival_margin_s",), {
          "en": "The cost: the wide pass to {p:lane_arrival_margin_s:0} stays open.",
          "ko": "대가는 측면입니다. {p:lane_arrival_margin_s:0} 선수에게 가는 측면 패스가 열려 있습니다."}),
    T("analyst.wide_outlet_available", ("analyst",), "claim:wide_outlet_available",
      ("wide_open_outlets", "lane_arrival_margin_s"), {
          "en": "Exposed: wide options still open in the same window: {v:wide_open_outlets}; the best lane, to "
                "{p:lane_arrival_margin_s:0}, has an arrival margin of {v:lane_arrival_margin_s} s.",
          "ko": "노출: 같은 시점에 측면 옵션 {v:wide_open_outlets}개가 열려 있습니다. 가장 좋은 길은 "
                "{p:lane_arrival_margin_s:0} 선수에게 가며 도달 여유는 {v:lane_arrival_margin_s}초입니다."}),
    T("fan.accessible_free_receiver", ("fan",), "claim:accessible_free_receiver", ("receiver_margin_s",), {
          "en": "{p:receiver_margin_s:0} is free and can be reached.",
          "ko": "{p:receiver_margin_s:0} 선수가 비어 있어 패스를 받을 수 있습니다."}),
    T("analyst.accessible_free_receiver", ("analyst",), "claim:accessible_free_receiver",
      ("receiver_margin_s", "nearest_defender_m"), {
          "en": "Exposed: {p:receiver_margin_s:0} is an accessible free receiver: open eligible lane, weak marking, "
                "reception margin {v:receiver_margin_s} s, nearest defender {v:nearest_defender_m} m over the last "
                "second.",
          "ko": "노출: {p:receiver_margin_s:0} 선수는 접근 가능한 자유 수신자입니다. 열린 적격 패스 길, 약한 마킹, "
                "수신 여유 {v:receiver_margin_s}초, 최근 1초 가장 가까운 수비 {v:nearest_defender_m}m."}),
    T("analyst.local_central_overload", ("analyst",), "claim:local_central_overload",
      ("central_count_attack", "central_count_defense"), {
          "en": "Context: in the fixed central region the count is {v:central_count_attack} v "
                "{v:central_count_defense}. A count on its own is not access to a free player.",
          "ko": "맥락: 고정된 중앙 구역의 숫자는 {v:central_count_attack} 대 {v:central_count_defense}입니다. "
                "숫자만으로는 자유로운 선수에게 접근할 수 있다는 뜻이 아닙니다."}),
    # ---------------------------------------------------------------- observed changes
    T("change.attacker_drop", ("fan", "analyst"), "change:attacker_drop", ("attacker_drop_m",), {
          "en": "Attacking response observed: {p:attacker_drop_m:0} dropped {v:attacker_drop_m} m toward the ball.",
          "ko": "관측된 공격 대응: {p:attacker_drop_m:0} 선수가 공 쪽으로 {v:attacker_drop_m}m 내려왔습니다."}),
    T("change.defender_step", ("fan", "analyst"), "change:defender_step", ("defender_step_m",), {
          "en": "Defensive adjustment observed next: {p:defender_step_m:0} stepped up {v:defender_step_m} m to "
                "{p:defender_step_m:1}.",
          "ko": "이어서 관측된 수비 조정: {p:defender_step_m:0} 선수가 {p:defender_step_m:1} 선수 쪽으로 "
                "{v:defender_step_m}m 전진해 붙었습니다."}),
    T("change.line_step_up", ("fan", "analyst"), "change:block_shift", ("back_line_step_m",), {
          "en": "Defensive adjustment observed: the back line stepped up {v:back_line_step_m} m together.",
          "ko": "관측된 수비 조정: 수비 라인이 함께 {v:back_line_step_m}m 올라섰습니다."}),
    T("change.lane_change", ("fan", "analyst"), "change:lane_change", ("attacker_move_m",), {
          "en": "Attacking response observed: {p:attacker_move_m:0} moved {v:attacker_move_m} m forward into the gap.",
          "ko": "관측된 공격 대응: {p:attacker_move_m:0} 선수가 간격 안으로 {v:attacker_move_m}m 전진했습니다."}),
    T("change.block_shift", ("fan", "analyst"), "change:block_shift", ("mid_line_shift_m",), {
          "en": "Defensive adjustment observed next: the midfield line dropped {v:mid_line_shift_m} m toward its goal.",
          "ko": "이어서 관측된 수비 조정: 미드필드 라인이 골문 쪽으로 {v:mid_line_shift_m}m 내려섰습니다."}),
]}

LABELS: dict[str, dict] = {
    "label.short_screened": {"keys": ("short_options_screened", "short_option_candidates"),
                             "en": "{v:short_options_screened}/{v:short_option_candidates} short options screened",
                             "ko": "짧은 패스 {v:short_option_candidates}개 중 {v:short_options_screened}개 차단"},
    "label.back_depth": {"keys": ("back_line_depth_m",), "en": "line {v:back_line_depth_m} m from goal",
                         "ko": "수비 라인 높이 {v:back_line_depth_m}m"},
    "label.cover_margin": {"keys": ("cover_margin_s",), "en": "cover {a:cover_margin_s} s late",
                           "ko": "커버 {a:cover_margin_s}초 늦음"},
    "label.cover_in_time": {"keys": ("cover_margin_s",), "en": "cover in time ({v:cover_margin_s} s)",
                            "ko": "커버 제때 도착({v:cover_margin_s}초)"},
    "label.line_retreat": {"keys": ("behind_depth_change_m",), "en": "line dropped {a:behind_depth_change_m} m",
                           "ko": "라인 {a:behind_depth_change_m}m 후퇴"},
    "label.presser": {"keys": ("presser_distance_m",), "en": "press {v:presser_distance_m} m",
                      "ko": "압박 {v:presser_distance_m}m"},
    "label.passive_screen": {"keys": ("carrier_nearest_defender_m",), "en": "unpressed, nearest {v:carrier_nearest_defender_m} m",
                             "ko": "압박 없음, 최근접 {v:carrier_nearest_defender_m}m"},
    "label.press_cut": {"keys": ("press_cut_lanes",), "en": "press cuts {v:press_cut_lanes} lane{s:press_cut_lanes}",
                        "ko": "압박으로 패스 길 {v:press_cut_lanes}개 차단"},
    "label.press_numbers": {"keys": ("buildup_count_defense", "buildup_count_attack", "rest_count_defense",
                                     "rest_count_attack"),
                            "en": "ball zone {v:buildup_count_defense} v {v:buildup_count_attack} · behind "
                                  "{v:rest_count_defense} v {v:rest_count_attack}",
                            "ko": "공 주변 {v:buildup_count_defense} 대 {v:buildup_count_attack} · 뒷선 "
                                  "{v:rest_count_defense} 대 {v:rest_count_attack}"},
    "label.mid_back_gap": {"keys": ("mid_back_gap_m",), "en": "{v:mid_back_gap_m} m between lines",
                           "ko": "라인 간격 {v:mid_back_gap_m}m"},
    "label.central_screened": {"keys": ("central_screened", "central_candidates"),
                               "en": "{v:central_screened}/{v:central_candidates} central routes screened",
                               "ko": "중앙 {v:central_candidates}개 중 {v:central_screened}개 차단"},
    "label.count": {"keys": ("central_count_attack", "central_count_defense"),
                    "en": "{v:central_count_attack} v {v:central_count_defense}",
                    "ko": "{v:central_count_attack} 대 {v:central_count_defense}"},
    "label.free_receiver": {"keys": ("receiver_margin_s",), "en": "free, margin {v:receiver_margin_s} s",
                            "ko": "자유, 여유 {v:receiver_margin_s}초"},
    "label.wide_outlet": {"keys": ("lane_arrival_margin_s",), "en": "open, margin {v:lane_arrival_margin_s} s",
                          "ko": "열림, 여유 {v:lane_arrival_margin_s}초"},
    "label.depth_reduced": {"keys": ("behind_depth_change_m",), "en": "line dropped {a:behind_depth_change_m} m",
                            "ko": "라인 {a:behind_depth_change_m}m 후퇴"},
    "label.change.attacker_drop": {"keys": ("attacker_drop_m",), "en": "dropped {v:attacker_drop_m} m",
                                   "ko": "{v:attacker_drop_m}m 내려옴"},
    "label.change.defender_step": {"keys": ("defender_step_m",), "en": "stepped {v:defender_step_m} m",
                                   "ko": "{v:defender_step_m}m 전진"},
    "label.change.line_step_up": {"keys": ("back_line_step_m",), "en": "line up {v:back_line_step_m} m",
                                  "ko": "라인 {v:back_line_step_m}m 전진"},
    "label.change.lane_change": {"keys": ("attacker_move_m",), "en": "moved {v:attacker_move_m} m",
                                 "ko": "{v:attacker_move_m}m 이동"},
    "label.change.block_shift": {"keys": ("mid_line_shift_m",), "en": "midfield -{v:mid_line_shift_m} m",
                                 "ko": "중원 {v:mid_line_shift_m}m 하강"},
}

# Fixed replies when Ask PitchIQ declines a question: the agent picks the code, never the words.
ASK_DECLINES: dict[str, dict[str, str]] = {
    "no_moment_yet": {
        "en": "Nothing to explain yet: no defensive trade-off has been detected up to this point in the replay.",
        "ko": "아직 설명할 장면이 없습니다. 리플레이의 이 시점까지 감지된 수비 트레이드오프가 없습니다."},
    "not_yet_known": {
        "en": "That has not happened yet at this point in the replay. PitchIQ only knows the play it has seen so far.",
        "ko": "리플레이의 이 시점에서는 아직 일어나지 않은 일입니다. PitchIQ는 지금까지 본 플레이만 압니다."},
    "intent_not_inferred": {
        "en": "PitchIQ does not read minds: it shows what the defensive shape restricted and what it exposed, not what "
              "anyone meant to do.",
        "ko": "PitchIQ는 의도를 추측하지 않습니다. 수비 형태가 무엇을 막았고 무엇을 내줬는지만 보여 줍니다."},
    "outside_scope": {
        "en": "PitchIQ answers questions about the defensive structure in this clip only.",
        "ko": "PitchIQ는 이 클립의 수비 구조에 관한 질문에만 답합니다."},
}

FORMAT = {"m": "{:.1f}", "s": "{:+.1f}", "mps": "{:.1f}", "count": "{:.0f}", "ratio": "{:.0%}", "score": "{:.2f}"}


def allowed_templates(audience: str, language: str) -> list[str]:
    return sorted(k for k, t in TEMPLATES.items() if audience in t.audience and language in t.text)
