// Shared v2 contracts, copied verbatim from PitchIQ_Defensive_Intelligence_Implementation_Spec_v2.md section 11.
// Do not edit by hand: the backend Pydantic models mirror these types, and src/schemas.ts validates payloads at runtime.
// A test (backend/tests/test_contract_parity.py) checks that this file still matches the spec.

export type ID = string;
export type XY = { x_m: number; y_m: number };
export type Velocity = { vx_mps: number; vy_mps: number };
export type FrameRef = { clip_id: ID; frame_index: number };
export type Window = { start_s: number; end_s: number };
export type EvidenceStatus = "ok" | "uncertain" | "unavailable";
export type SourceMode = "foundry_live" | "foundry_cached" | "deterministic_fallback";
export type Epistemic = "computed_observation" | "heuristic_inference" | "realized_event";

export interface MatchMeta {
  schema_version: "2.0";
  match_id: ID;
  period: 1 | 2;
  pitch: { length_m: 105; width_m: 68 };
  tracking_hz: 5;
  teams: Array<{ team_id: ID; display_name: string; attack_direction: 1 | -1 }>;
  roster: Array<{ player_id: ID; team_id: ID; jersey: number; is_goalkeeper: boolean }>;
  clips: Array<{ clip_id: ID; start_s: number; end_s: number; checksum: string }>;
}

export interface ObservationFrame {
  schema_version: "2.0";
  match_id: ID;
  frame_ref: FrameRef;
  period: 1 | 2;
  match_time_s: number;
  players: Array<{
    player_id: ID; team_id: ID; is_goalkeeper: boolean;
    position: XY; velocity: Velocity;
  }>;
  ball: { position: XY; z_m: number; owner_id: ID | null };
  possession_team_id: ID | null;
  ball_state: "controlled" | "in_flight" | "loose" | "dead";
}

export interface MatchEvent {
  event_id: ID;
  match_id: ID;
  clip_id: ID;
  event_time_s: number;
  available_at_s: number;
  kind: "pass_release" | "pass_receive" | "interception" | "carry"
      | "duel" | "turnover" | "shot" | "goal" | "restart";
  actor_id: ID | null;
  target_id: ID | null; // P0 release: null; receive: observed receiver only
  related_event_id: ID | null;
  position: XY | null;
}

export interface LineGroup {
  group: "back" | "mid" | "front";
  player_ids: ID[];
  own_goal_depth_m: number;
  spread_m: number;
  support_score: number;
}
export interface MarkingRelation {
  relation_id: ID;
  defender_id: ID;
  attacker_id: ID;
  since_s: number;
  mean_distance_m: number;
  follow_support_score: number;
  status: EvidenceStatus;
}
export interface LaneRelation {
  relation_id: ID;
  carrier_id: ID;
  receiver_id: ID;
  start: XY;
  end: XY;
  eligibility: "onside_geometry" | "offside_geometry" | "unknown";
  availability: "open" | "screened" | "uncertain";
  min_arrival_margin_s: number | null;
  screening_player_ids: ID[];
}
export interface PressureRelation {
  relation_id: ID;
  presser_id: ID;
  carrier_id: ID;
  distance_m: number;
  closing_speed_mps: number;
  approach_vector: Velocity;
  funnel: "left" | "right" | "none" | "uncertain";
  likely_outlet_ids: ID[];
  support_score: number;
}
export interface CoverRelation {
  relation_id: ID;
  attacker_id: ID;
  primary_defender_id: ID | null;
  cover_player_id: ID | null;
  target: XY;
  attacker_arrival_s: number | null;
  cover_arrival_s: number | null;
  cover_margin_s: number | null; // cover arrival - attacker arrival
  status: EvidenceStatus;
}
export interface LocalRegion {
  region_id: ID;
  polygon: XY[];
  attacking_player_ids: ID[];
  defending_player_ids: ID[];
  accessible_receiver_ids: ID[];
}
export interface TacticalState {
  state_id: ID;
  analyzed_until_s: number;
  frame_ref: FrameRef;
  window: Window;
  attacking_team_id: ID | null;
  defending_team_id: ID | null;
  possession_phase: "buildup" | "progression" | "transition" | "unknown";
  active_press: boolean;
  shape_label: "approx_442" | "approx_433" | "approx_325"
             | "ambiguous" | "transition" | "unclassified";
  line_groups: LineGroup[];
  markings: MarkingRelation[];
  pressures: PressureRelation[];
  lanes: LaneRelation[];
  covers: CoverRelation[];
  regions: LocalRegion[];
  quality_flags: string[];
}

export type ClaimCode = "short_options_screened" | "central_routes_screened"
  | "behind_space_exposed" | "behind_depth_reduced" | "press_block_disconnected"
  | "between_lines_receiver" | "isolated_1v1" | "wide_outlet_available"
  | "weak_side_accessible" | "local_central_overload"
  | "accessible_free_receiver" | "second_ball_support_gap";

export interface EvidenceMetric {
  metric_id: ID;
  key: string; // allowlisted key from the versioned metric registry
  value: number | null;
  unit: "m" | "s" | "mps" | "count" | "ratio" | "score";
  status: EvidenceStatus;
  window: Window;
  player_ids: ID[];
  frame_refs: FrameRef[];
  event_ids: ID[];
  method_version: string;
}
export interface TacticalClaim {
  claim_id: ID;
  code: ClaimCode;
  epistemic: Epistemic;
  dimension: "restricted" | "exposed" | "context";
  metric_ids: ID[];
  relation_ids: ID[];
  player_ids: ID[];
  support_score: number;
  window: Window;
}
export interface TacticalChange {
  change_id: ID;
  before_state_id: ID;
  after_state_id: ID;
  actor_team_id: ID;
  actor_player_ids: ID[];
  code: "attacker_drop" | "defender_step" | "line_retreat" | "block_shift"
      | "mark_switch" | "lane_change" | "count_change";
  relation: "observed_next" | "consistent_with_response";
  metric_ids: ID[];
  observed_at_s: number;
}
export interface TradeoffEpisode {
  episode_id: ID;
  revision: number;
  match_id: ID;
  clip_id: ID;
  lifecycle: "candidate" | "active" | "cooling" | "closed";
  analysis_status: "complete_tradeoff" | "partial" | "insufficient_evidence";
  defending_team_id: ID;
  analyzed_until_s: number;
  detected_at_s: number;
  window: Window;
  state_ids: ID[];
  restricted_claim_ids: ID[];
  exposed_claim_ids: ID[];
  context_claim_ids: ID[];
  change_ids: ID[];
  story_priority: number;
  outcome: {
    status: "not_yet_observed" | "exploited_in_synthetic_play"
          | "not_exploited_in_window" | "inconclusive";
    observed_until_s: number;
    event_ids: ID[];
    realized_target_id: ID | null;
  };
}
export interface EvidencePacket {
  schema_version: "2.0";
  evidence_hash: string;
  episode: TradeoffEpisode;
  states: TacticalState[];
  metrics: EvidenceMetric[];
  claims: TacticalClaim[];
  changes: TacticalChange[];
  events: MatchEvent[];
  forbidden_inferences: string[];
}

export type Layer = "structure" | "marking" | "pressure" | "screened_lanes"
  | "outlets" | "exposure" | "cover" | "numbers" | "changes" | "caption";
export type Anchor = { kind: "player"; player_id: ID } | { kind: "world"; point: XY };
export type Geometry =
  | { type: "ring"; center: Anchor; radius_m: number }
  | { type: "connection"; from: Anchor; to: Anchor }
  | { type: "arrow"; from: Anchor; to: Anchor; meaning: "observed" | "inferred" }
  | { type: "polygon"; points: XY[] }
  | { type: "polyline"; points: XY[] }
  | { type: "label"; at: Anchor; template_key: string; metric_ids: ID[] };
export interface OverlayElement {
  element_id: ID;
  layer: Layer;
  claim_ids: ID[];
  metric_ids: ID[];
  relation_ids: ID[];
  geometry: Geometry;
  style_token: string; // allowlisted registry; no raw CSS/SVG/HTML
}
export interface OverlaySnapshot {
  schema_version: "2.0";
  overlay_id: ID;
  replay_id: ID;
  analysis_epoch: number;
  episode_id: ID;
  episode_revision: number;
  evidence_hash: string;
  frame_ref: FrameRef;
  match_time_s: number;
  valid_until_s: number;
  display_mode: "live" | "retrospective_replay";
  surface: "inspector_preview" | "broadcast";
  audience: "fan" | "analyst";
  language: "en" | "ko";
  elements: OverlayElement[];
}

export interface AnalystPlan {
  episode_id: ID;
  evidence_hash: string;
  focus_claim_ids: ID[];
  restricted_claim_ids: ID[];
  exposed_claim_ids: ID[];
  ordered_change_ids: ID[];
  omitted_claim_ids: ID[];
  caveat_codes: string[];
}
export interface SentencePlan {
  template_key: string;
  claim_id: ID | null;
  change_id: ID | null;
  metric_ids: ID[];
  event_ids: ID[];
}
export interface ProducerPlan {
  episode_id: ID;
  evidence_hash: string;
  variants: Array<{
    audience: "fan" | "analyst";
    language: "en" | "ko";
    sentences: SentencePlan[];
    focus_layers: Layer[];
  }>;
}
export interface InsightRevision {
  insight_id: ID;
  revision: number;
  episode_id: ID;
  episode_revision: number;
  evidence_hash: string;
  analyst_plan: AnalystPlan;
  producer_plan: ProducerPlan;
  source_mode: SourceMode;
  review_status: "suggested" | "approved" | "rejected";
  generation_trace_id: ID | null;
}
