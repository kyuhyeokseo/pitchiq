"""Agent plan and insight revision contracts (spec 11, 12)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import ID, SourceMode, StrictModel
from .overlay import Layer

Audience = Literal["fan", "analyst"]
Language = Literal["en", "ko"]
VARIANTS: tuple[tuple[str, str], ...] = (("fan", "en"), ("fan", "ko"), ("analyst", "en"), ("analyst", "ko"))


class AnalystPlan(StrictModel):
    episode_id: ID
    evidence_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    focus_claim_ids: list[ID] = Field(min_length=1)
    restricted_claim_ids: list[ID]
    exposed_claim_ids: list[ID]
    ordered_change_ids: list[ID]
    omitted_claim_ids: list[ID]
    caveat_codes: list[str]


class SentencePlan(StrictModel):
    template_key: str = Field(min_length=1)
    claim_id: ID | None
    change_id: ID | None
    metric_ids: list[ID]
    event_ids: list[ID]


class ProducerVariant(StrictModel):
    audience: Audience
    language: Language
    sentences: list[SentencePlan] = Field(min_length=1, max_length=6)
    focus_layers: list[Layer] = Field(min_length=1)


class ProducerPlan(StrictModel):
    episode_id: ID
    evidence_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    variants: list[ProducerVariant]

    @model_validator(mode="after")
    def _all_variants_once(self) -> ProducerPlan:
        got = sorted((v.audience, v.language) for v in self.variants)
        if got != sorted(VARIANTS):
            raise ValueError("ProducerPlan needs (fan,en), (fan,ko), (analyst,en), (analyst,ko) exactly once each")
        return self

    def variant(self, audience: str, language: str) -> ProducerVariant:
        return next(v for v in self.variants if v.audience == audience and v.language == language)


class InsightRevision(StrictModel):
    insight_id: ID
    revision: int = Field(ge=1)
    episode_id: ID
    episode_revision: int = Field(ge=1)
    evidence_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    analyst_plan: AnalystPlan
    producer_plan: ProducerPlan
    source_mode: SourceMode
    review_status: Literal["suggested", "approved", "rejected"]
    generation_trace_id: ID | None

    @model_validator(mode="after")
    def _same_evidence(self) -> InsightRevision:
        for plan in (self.analyst_plan, self.producer_plan):
            if plan.episode_id != self.episode_id or plan.evidence_hash != self.evidence_hash:
                raise ValueError("plans must refer to the revision's episode and evidence hash")
        return self
