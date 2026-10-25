"""Build a candidate BroadcastInsight (deterministic template text + typed overlay).

Text uses ONLY values from ``numeric_facts`` (formatted to one decimal) and
public roster display names. Fan and analyst variants carry different detail;
Korean is written separately, not machine-translated. Wording is associative
("as ... the gap grew"), never causal. This is the offline fallback that the
Foundry agents (Phase 4) must match or beat on evidence fidelity.
"""

from __future__ import annotations

from ..analytics.patterns import Detection
from ..analytics.significance import Measurement
from ..ingestion.loader import ClipData
from ..schemas import BroadcastInsight, MatchEvent, SignificanceResult

DISPLAY_TTL_S = 6.0
EVIDENCE_LOOKBACK_S = 2.0

SIDE_EN = {"right": "right", "left": "left"}
SIDE_KO = {"right": "오른쪽", "left": "왼쪽"}
PATTERN_EN = {"overlap": "overlap", "underlap": "underlap"}
PATTERN_KO = {"overlap": "오버랩", "underlap": "언더랩"}
WAY_EN = {"overlap": "outside", "underlap": "inside"}
WAY_KO = {"overlap": "바깥쪽", "underlap": "안쪽"}


def importance_for(score: float) -> str:
    return "high" if score >= 0.75 else "medium" if score >= 0.55 else "low"


def numeric_facts(det: Detection, m: Measurement, sig: SignificanceResult) -> dict[str, float | int]:
    return {
        "fullback_progress_m": round(det.features["progress_m"], 1),
        "lateral_offset_m": round(abs(det.features["lateral_offset_m"]), 1),
        "defender_gap_before_m": round(m.gap_before_m, 1),
        "defender_gap_after_m": round(m.gap_after_m, 1),
        "defender_gap_delta_m": round(m.gap_after_m - m.gap_before_m, 1),
        "new_option_lanes": len(m.new_lane_receivers),
        "detector_strength": round(det.prediction.confidence, 2),
        "significance_score": round(sig.score, 2),
    }


def _fmt(v: float) -> str:
    return f"{v:.1f}"


def template_texts(pattern: str, side: str, names: dict[str, str], f: dict) -> dict[str, str]:
    fb, w, dfb, dcb = names["fullback"], names["winger"], names["dfb"], names["dcb"]
    widened = f["defender_gap_delta_m"] > 0
    lanes = f["new_option_lanes"]
    title = f"{names['team']} {SIDE_EN[side]}-flank {PATTERN_EN[pattern]}"
    if widened:
        fan_en = f"{fb} races {WAY_EN[pattern]} past {w} - and the gap beside {dfb} stretches to {_fmt(f['defender_gap_after_m'])} m!"
        fan_ko = f"{fb}, {w}의 {WAY_KO[pattern]}으로 질주! {dfb} 옆 공간이 {_fmt(f['defender_gap_after_m'])}m까지 벌어집니다!"
    else:
        fan_en = f"{fb} makes the {PATTERN_EN[pattern]} run {WAY_EN[pattern]} {w} - a new passing option appears!"
        fan_ko = f"{fb}의 {PATTERN_KO[pattern]} 침투! {w}의 {WAY_KO[pattern]}으로 들어가며 새 패스 길이 열립니다!"
    analyst_en = (
        f"{PATTERN_EN[pattern].capitalize()}: {fb} advanced {_fmt(f['fullback_progress_m'])} m and passed {w} on the "
        f"{WAY_EN[pattern]} (lateral offset {_fmt(f['lateral_offset_m'])} m). Over the observed window the {dfb}-{dcb} "
        f"spacing went from {_fmt(f['defender_gap_before_m'])} m to {_fmt(f['defender_gap_after_m'])} m "
        f"({f['defender_gap_delta_m']:+.1f} m); new forward option lanes: {lanes}. Observed association only, "
        f"not a causal estimate."
    )
    analyst_ko = (
        f"{PATTERN_KO[pattern]}: {fb} 선수가 {_fmt(f['fullback_progress_m'])}m 전진하며 {w}의 {WAY_KO[pattern]}으로 "
        f"지나갔습니다(측면 간격 {_fmt(f['lateral_offset_m'])}m). 관측 구간에서 {dfb}-{dcb} 간격은 "
        f"{_fmt(f['defender_gap_before_m'])}m에서 {_fmt(f['defender_gap_after_m'])}m로 변했고"
        f"({f['defender_gap_delta_m']:+.1f}m), 새로 열린 전진 패스 길은 {lanes}개입니다. 관측된 연관성이며 인과 추정이 아닙니다."
    )
    return {"title": title, "fan_en": fan_en, "analyst_en": analyst_en, "fan_ko": fan_ko, "analyst_ko": analyst_ko}


def _vec(xy) -> dict[str, float]:
    return {"x_m": round(float(xy[0]), 2), "y_m": round(float(xy[1]), 2)}


def build_overlay(det: Detection, m: Measurement, clip: ClipData, display_at: float) -> dict:
    fb, w = det.pair.fullback, det.pair.winger
    path = [_vec(clip.pos[k, fb]) for k in range(det.k_start, det.k_cross + 1, 2)]
    path.append(_vec(clip.pos[det.k_detect, fb]))
    dfb, dcb = clip.idx(m.defending_fullback), clip.idx(m.defending_centreback)
    elements: list[dict] = [
        {"type": "player_highlight", "player_id": clip.player_ids[fb], "style": "primary"},
        {"type": "player_highlight", "player_id": clip.player_ids[w], "style": "secondary"},
        {"type": "player_highlight", "player_id": m.defending_fullback, "style": "defender"},
        {"type": "player_highlight", "player_id": m.defending_centreback, "style": "defender"},
        {"type": "movement_arrow", "player_id": clip.player_ids[fb], "path": path,
         "start_s": round(float(clip.t[det.k_start]), 3), "end_s": round(float(clip.t[det.k_detect]), 3),
         "style": "highlight"},
        {"type": "space_zone", "kind": "defender_gap",
         "polygon": [_vec(clip.pos[m.k_gap_after, dfb]), _vec(clip.pos[m.k_gap_after, dcb])],
         "player_ids": [m.defending_fullback, m.defending_centreback]},
    ]
    if m.k_new_lane is not None and m.new_lane_receivers:
        r = clip.idx(m.new_lane_receivers[0])
        elements.append({"type": "space_zone", "kind": "passing_lane",
                         "polygon": [_vec(clip.ball[m.k_new_lane]), _vec(clip.pos[m.k_new_lane, r])],
                         "player_ids": [m.new_lane_receivers[0]]})
    elements.append({"type": "caption_card", "anchor": _vec(clip.pos[det.k_cross, fb]), "text_key": "title"})
    return {"sync_match_s": display_at, "ttl_s": DISPLAY_TTL_S, "source_prediction_id": det.prediction.prediction_id,
            "elements": elements}


def evidence_event_ids(events: list[MatchEvent], det: Detection, m: Measurement, clip: ClipData) -> list[str]:
    t_lo = float(clip.t[det.k_start]) - EVIDENCE_LOOKBACK_S
    t_hi = float(clip.t[m.k_eval])
    involved = {*det.prediction.actor_player_ids, m.defending_fullback, m.defending_centreback}
    return [
        e.event_id for e in events
        if e.clip_id == clip.clip_id and t_lo <= e.match_time_s <= t_hi
        and (e.actor_id in involved or e.target_id in involved or e.event_type in ("pass", "receive"))
    ]


def build_candidate(det: Detection, m: Measurement, sig: SignificanceResult, clip: ClipData,
                    events: list[MatchEvent], display_names: dict[str, str], team_names: dict[str, str]) -> BroadcastInsight:
    pattern = det.prediction.pattern
    if pattern not in ("overlap", "underlap"):
        raise ValueError("only overlap/underlap detections become broadcast candidates")
    facts = numeric_facts(det, m, sig)
    names = {
        "fullback": display_names[clip.player_ids[det.pair.fullback]],
        "winger": display_names[clip.player_ids[det.pair.winger]],
        "dfb": display_names[m.defending_fullback],
        "dcb": display_names[m.defending_centreback],
        "team": team_names[det.pair.team_id],
    }
    texts = template_texts(pattern, det.pair.side, names, facts)
    detected_at = round(float(clip.t[det.k_detect]), 3)
    display_at = round(float(clip.t[m.k_eval]), 3)
    return BroadcastInsight.model_validate({
        "insight_id": f"ins-{det.prediction.prediction_id}",
        "match_id": clip.match_id,
        "clip_id": clip.clip_id,
        "prediction_id": det.prediction.prediction_id,
        "detected_at_match_s": detected_at,
        "display_at_match_s": display_at,
        "expires_at_match_s": round(display_at + DISPLAY_TTL_S, 3),
        "evidence_frame_indices": sorted({*det.prediction.evidence_frame_indices, int(clip.frame_index[m.k_gap_after])}),
        "evidence_event_ids": evidence_event_ids(events, det, m, clip),
        "actor_player_ids": det.prediction.actor_player_ids,
        "pattern": pattern,
        "importance": importance_for(sig.score),
        **texts,
        "numeric_facts": facts,
        "overlay": build_overlay(det, m, clip, display_at),
        "production_status": "suggested",
        "source_mode": "deterministic_fallback",
    })
