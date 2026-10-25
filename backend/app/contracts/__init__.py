"""Shared v2 contracts (spec section 11). Every serving payload is one of these models."""

from .common import (
    FRAME_DT_S,
    ID,
    MAX_BALL_SPEED_MPS,
    MAX_PLAYER_ACCEL_MPS2,
    MAX_PLAYER_SPEED_MPS,
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    SCHEMA_VERSION,
    TRACKING_HZ,
    Epistemic,
    EvidenceStatus,
    FrameRef,
    SourceMode,
    StrictModel,
    Velocity,
    Window,
    XY,
)
from .evidence import (
    ClaimCode,
    EvidenceMetric,
    EvidencePacket,
    Outcome,
    TacticalChange,
    TacticalClaim,
    TradeoffEpisode,
    canonical_json,
    compute_evidence_hash,
)
from .narrative import VARIANTS, AnalystPlan, InsightRevision, ProducerPlan, ProducerVariant, SentencePlan
from .observation import BallObs, ClipMeta, MatchEvent, MatchMeta, ObservationFrame, Pitch, PlayerObs, RosterEntry, \
    TeamMeta
from .overlay import (
    Arrow,
    Connection,
    Label,
    Layer,
    OverlayElement,
    OverlaySnapshot,
    PlayerAnchor,
    Polygon,
    Polyline,
    Ring,
    WorldAnchor,
    player_anchor_ids,
    world_points,
)
from .state import (
    CoverRelation,
    LaneRelation,
    LineGroup,
    LocalRegion,
    MarkingRelation,
    PressureRelation,
    TacticalState,
)

__all__ = [n for n in dir() if not n.startswith("_")]
