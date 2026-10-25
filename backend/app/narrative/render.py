"""Render validated sentence plans and overlay labels from packet values only."""

from __future__ import annotations

import re

from ..contracts import EvidenceMetric, EvidencePacket, MatchMeta, SentencePlan
from .registry import FORMAT, LABELS, TEMPLATES

PLACEHOLDER = re.compile(r"\{(v|a|p|s|team):([a-z_0-9]+)(?::(\d+))?\}|\{outcome_target\}")


class RenderError(ValueError):
    pass


def display_names(meta: MatchMeta) -> dict[str, str]:
    short = {t.team_id: t.display_name.split()[0][:3].upper() for t in meta.teams}
    return {r.player_id: f"{short[r.team_id]} #{r.jersey}" for r in meta.roster}


def team_names(meta: MatchMeta) -> dict[str, str]:
    return {t.team_id: t.display_name for t in meta.teams}


def fmt(m: EvidenceMetric) -> str:
    if m.value is None:
        raise RenderError(f"metric {m.metric_id} has no value")
    return FORMAT[m.unit].format(m.value)


def _fill(text: str, metrics: list[EvidenceMetric], packet: EvidencePacket, meta: MatchMeta) -> str:
    by_key = {m.key: m for m in metrics}
    names = display_names(meta)
    teams = team_names(meta)
    defending = packet.episode.defending_team_id
    attacking = next(t for t in teams if t != defending)

    def sub(mo: re.Match) -> str:
        if mo.group(0) == "{outcome_target}":
            tid = packet.episode.outcome.realized_target_id
            if tid is None:
                raise RenderError("outcome has no realized target")
            return names[tid]
        kind, key, idx = mo.group(1), mo.group(2), mo.group(3)
        if kind == "team":
            return teams[defending if key == "defending" else attacking]
        if key not in by_key:
            raise RenderError(f"plan does not provide metric {key}")
        m = by_key[key]
        if kind == "v":
            return fmt(m)
        if kind == "s":  # plural ending for the metric's count ("1 lane", "2 lanes")
            return "" if m.value == 1 else "s"
        if kind == "a":  # magnitude, for wording that already carries the direction ("dropped 5.8 m", "1.4 s late")
            return (fmt(m.model_copy(update={"value": abs(m.value)})) if m.value is not None else fmt(m)).lstrip("+")
        i = int(idx or 0)
        if i >= len(m.player_ids):
            raise RenderError(f"metric {key} has no player #{i}")
        return names[m.player_ids[i]]
    return PLACEHOLDER.sub(sub, text)


def render_sentence(plan: SentencePlan, packet: EvidencePacket, language: str, meta: MatchMeta) -> str:
    t = TEMPLATES[plan.template_key]
    metrics = [packet.metric(mid) for mid in plan.metric_ids]
    return _fill(t.text[language], metrics, packet, meta)


def render_label(template_key: str, metrics: list[EvidenceMetric], packet: EvidencePacket | None, language: str,
                 meta: MatchMeta) -> str:
    spec = LABELS[template_key]
    by_key = {m.key: m for m in metrics}
    missing = [k for k in spec["keys"] if k not in by_key]
    if missing:
        raise RenderError(f"label {template_key} misses metrics {missing}")
    text = spec[language]
    def val(mo: re.Match) -> str:
        m = by_key[mo.group(2)]
        if mo.group(1) == "s":
            return "" if m.value == 1 else "s"
        if mo.group(1) == "a" and m.value is not None:
            return fmt(m.model_copy(update={"value": abs(m.value)})).lstrip("+")
        return fmt(m)
    return re.sub(r"\{(v|a|s):([a-z_0-9]+)\}", val, text)
