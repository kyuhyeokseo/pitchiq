"""Streaming clip analysis: detection -> delayed significance -> candidate insight.

``ClipAnalyzer.advance_to(k)`` processes frames in order and never reads
beyond frame k; significance for a detection is evaluated once the replay
reaches its evaluation frame (a fixed short delay after detection). The same
object serves offline evaluation (advance to the last frame) and live replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..broadcast.candidate import build_candidate
from ..ingestion.loader import ClipData, Scenario
from ..schemas import BroadcastInsight, MatchEvent, SignificanceResult, TacticalPrediction
from .patterns import Detection, OverlapDetector
from .significance import InsightSelector, Measurement, eval_frame, measure, score


@dataclass
class Candidate:
    """A detection after significance evaluation. ``insight`` is None when suppressed."""

    detection: Detection
    measurement: Measurement
    significance: SignificanceResult
    insight: BroadcastInsight | None
    evaluated_at_match_s: float

    @property
    def prediction(self) -> TacticalPrediction:
        return self.detection.prediction


@dataclass
class ClipAnalyzer:
    clip: ClipData
    events: list[MatchEvent]
    selector: InsightSelector
    display_names: dict[str, str]
    team_names: dict[str, str]
    cursor: int = -1
    detector: OverlapDetector = field(init=False)
    pending: list[tuple[int, Detection]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.detector = OverlapDetector(self.clip)

    def advance_to(self, k: int) -> list[Detection | Candidate]:
        """Process frames cursor+1..k. Returns detections and evaluated candidates in time order."""
        k = min(k, len(self.clip) - 1)
        out: list[Detection | Candidate] = []
        while self.cursor < k:
            self.cursor += 1
            for det in self.detector.step(self.cursor):
                out.append(det)
                self.pending.append((eval_frame(det, len(self.clip)), det))
            ready = [(ke, d) for ke, d in self.pending if ke <= self.cursor]
            self.pending = [(ke, d) for ke, d in self.pending if ke > self.cursor]
            for ke, det in ready:
                out.append(self._evaluate(det, ke))
        return out

    def _evaluate(self, det: Detection, k_eval: int) -> Candidate:
        t_eval = float(self.clip.t[k_eval])
        visible = [e for e in self.events if e.match_time_s <= t_eval]
        m = measure(det, self.clip, k_eval, visible)
        p = det.prediction
        novelty = self.selector.novelty(t_eval, det.pair.team_id, det.pair.side, p.pattern)
        sig = self.selector.admit(t_eval, score(det, m, novelty))
        insight = None
        if sig.should_suggest:
            self.selector.record(t_eval, det.pair.team_id, det.pair.side, p.pattern)
            insight = build_candidate(det, m, sig, self.clip.until(k_eval), visible, self.display_names,
                                      self.team_names)
        return Candidate(det, m, sig, insight, round(t_eval, 3))


def analyzer_for(scenario: Scenario, clip_id: str, selector: InsightSelector) -> ClipAnalyzer:
    names = {r.player_id: r.display_name for r in scenario.manifest.roster}
    teams = {t.team_id: t.short_name for t in scenario.manifest.teams}
    return ClipAnalyzer(scenario.clips[clip_id], scenario.clip_events(clip_id), selector, names, teams)


def analyze_scenario(scenario: Scenario, selector: InsightSelector | None = None) -> list[Candidate]:
    """Offline helper: run every clip in match-time order through one shared selector."""
    selector = selector or InsightSelector()
    out: list[Candidate] = []
    for clip_info in sorted(scenario.manifest.clips, key=lambda c: c.start_match_s):
        an = analyzer_for(scenario, clip_info.clip_id, selector)
        out.extend(x for x in an.advance_to(len(scenario.clips[clip_info.clip_id]) - 1) if isinstance(x, Candidate))
    return out
