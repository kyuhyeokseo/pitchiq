"""Overlay contracts: anchors, geometry primitives, snapshots (spec 10, 11)."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .common import ID, FrameRef, StrictModel, XY, _finite
from .registry import STYLE_TOKENS

Layer = Literal["structure", "marking", "pressure", "screened_lanes", "outlets", "exposure", "cover", "numbers",
                "changes", "caption"]


class PlayerAnchor(StrictModel):
    kind: Literal["player"]
    player_id: ID


class WorldAnchor(StrictModel):
    kind: Literal["world"]
    point: XY


Anchor = Annotated[Union[PlayerAnchor, WorldAnchor], Field(discriminator="kind")]


class Ring(StrictModel):
    type: Literal["ring"]
    center: Anchor
    radius_m: float = Field(gt=0.0, le=30.0)


class Connection(StrictModel):
    type: Literal["connection"]
    from_: Anchor = Field(alias="from")
    to: Anchor

    model_config = {**StrictModel.model_config, "populate_by_name": True, "serialize_by_alias": True}


class Arrow(StrictModel):
    type: Literal["arrow"]
    from_: Anchor = Field(alias="from")
    to: Anchor
    meaning: Literal["observed", "inferred"]

    model_config = {**StrictModel.model_config, "populate_by_name": True, "serialize_by_alias": True}


class Polygon(StrictModel):
    type: Literal["polygon"]
    points: list[XY] = Field(min_length=3)


class Polyline(StrictModel):
    type: Literal["polyline"]
    points: list[XY] = Field(min_length=2)


class Label(StrictModel):
    type: Literal["label"]
    at: Anchor
    template_key: str = Field(min_length=1)
    metric_ids: list[ID]


Geometry = Annotated[Union[Ring, Connection, Arrow, Polygon, Polyline, Label], Field(discriminator="type")]


class OverlayElement(StrictModel):
    element_id: ID
    layer: Layer
    claim_ids: list[ID]
    metric_ids: list[ID]
    relation_ids: list[ID]
    geometry: Geometry
    style_token: str

    @model_validator(mode="after")
    def _style(self) -> OverlayElement:
        if self.style_token not in STYLE_TOKENS:
            raise ValueError(f"style_token {self.style_token!r} is not allowlisted")
        for p in world_points(self.geometry):
            if not p.in_pitch():
                raise ValueError(f"element {self.element_id} has a world point outside the pitch")
        return self


class OverlaySnapshot(StrictModel):
    schema_version: Literal["2.0"]
    overlay_id: ID
    replay_id: ID
    analysis_epoch: int = Field(ge=0)
    episode_id: ID
    episode_revision: int = Field(ge=1)
    evidence_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    frame_ref: FrameRef
    match_time_s: float
    valid_until_s: float
    display_mode: Literal["live", "retrospective_replay"]
    surface: Literal["inspector_preview", "broadcast"]
    audience: Literal["fan", "analyst"]
    language: Literal["en", "ko"]
    elements: list[OverlayElement]

    @model_validator(mode="after")
    def _times(self) -> OverlaySnapshot:
        _finite(self.match_time_s)
        _finite(self.valid_until_s)
        if self.valid_until_s < self.match_time_s:
            raise ValueError("valid_until_s must not precede match_time_s")
        ids = [e.element_id for e in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("element_id values must be unique")
        return self


def anchors(geometry) -> list:
    g = geometry
    if g.type == "ring":
        return [g.center]
    if g.type in ("connection", "arrow"):
        return [g.from_, g.to]
    if g.type == "label":
        return [g.at]
    return []


def world_points(geometry) -> list[XY]:
    pts = [a.point for a in anchors(geometry) if a.kind == "world"]
    if geometry.type in ("polygon", "polyline"):
        pts.extend(geometry.points)
    return pts


def player_anchor_ids(geometry) -> list[str]:
    return [a.player_id for a in anchors(geometry) if a.kind == "player"]
