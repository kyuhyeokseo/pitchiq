// Runtime validation (zod) for every backend payload the UI renders.
// The compile-time checks at the bottom make tsc fail if a schema drifts from src/contracts.ts.
import { z } from "zod";
import type * as C from "./contracts";

const ID = z.string().min(1);
const num = z.number().finite();
export const XYZ = z.object({ x_m: num, y_m: num }).strict();
const VelocityZ = z.object({ vx_mps: num, vy_mps: num }).strict();
const FrameRefZ = z.object({ clip_id: ID, frame_index: z.number().int().nonnegative() }).strict();
const WindowZ = z.object({ start_s: num, end_s: num }).strict();
const Status = z.enum(["ok", "uncertain", "unavailable"]);

export const ObservationFrameZ = z.object({
  schema_version: z.literal("2.0"),
  match_id: ID,
  frame_ref: FrameRefZ,
  period: z.union([z.literal(1), z.literal(2)]),
  match_time_s: num,
  players: z.array(z.object({
    player_id: ID, team_id: ID, is_goalkeeper: z.boolean(), position: XYZ, velocity: VelocityZ,
  }).strict()).length(22),
  ball: z.object({ position: XYZ, z_m: num, owner_id: ID.nullable() }).strict(),
  possession_team_id: ID.nullable(),
  ball_state: z.enum(["controlled", "in_flight", "loose", "dead"]),
}).strict();

const LineGroupZ = z.object({
  group: z.enum(["back", "mid", "front"]), player_ids: z.array(ID), own_goal_depth_m: num, spread_m: num,
  support_score: num,
}).strict();
const MarkingZ = z.object({
  relation_id: ID, defender_id: ID, attacker_id: ID, since_s: num, mean_distance_m: num,
  follow_support_score: num, status: Status,
}).strict();
const LaneZ = z.object({
  relation_id: ID, carrier_id: ID, receiver_id: ID, start: XYZ, end: XYZ,
  eligibility: z.enum(["onside_geometry", "offside_geometry", "unknown"]),
  availability: z.enum(["open", "screened", "uncertain"]), min_arrival_margin_s: num.nullable(),
  screening_player_ids: z.array(ID),
}).strict();
const PressureZ = z.object({
  relation_id: ID, presser_id: ID, carrier_id: ID, distance_m: num, closing_speed_mps: num,
  approach_vector: VelocityZ, funnel: z.enum(["left", "right", "none", "uncertain"]), likely_outlet_ids: z.array(ID),
  support_score: num,
}).strict();
const CoverZ = z.object({
  relation_id: ID, attacker_id: ID, primary_defender_id: ID.nullable(), cover_player_id: ID.nullable(), target: XYZ,
  attacker_arrival_s: num.nullable(), cover_arrival_s: num.nullable(), cover_margin_s: num.nullable(), status: Status,
}).strict();
const RegionZ = z.object({
  region_id: ID, polygon: z.array(XYZ).min(3), attacking_player_ids: z.array(ID), defending_player_ids: z.array(ID),
  accessible_receiver_ids: z.array(ID),
}).strict();

export const TacticalStateZ = z.object({
  state_id: ID, analyzed_until_s: num, frame_ref: FrameRefZ, window: WindowZ, attacking_team_id: ID.nullable(),
  defending_team_id: ID.nullable(), possession_phase: z.enum(["buildup", "progression", "transition", "unknown"]),
  active_press: z.boolean(),
  shape_label: z.enum(["approx_442", "approx_433", "approx_325", "ambiguous", "transition", "unclassified"]),
  line_groups: z.array(LineGroupZ), markings: z.array(MarkingZ), pressures: z.array(PressureZ), lanes: z.array(LaneZ),
  covers: z.array(CoverZ), regions: z.array(RegionZ), quality_flags: z.array(z.string()),
}).strict();

const PlayerAnchorZ = z.object({ kind: z.literal("player"), player_id: ID }).strict();
const WorldAnchorZ = z.object({ kind: z.literal("world"), point: XYZ }).strict();
const AnchorZ = z.discriminatedUnion("kind", [PlayerAnchorZ, WorldAnchorZ]);
const GeometryZ = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ring"), center: AnchorZ, radius_m: num }).strict(),
  z.object({ type: z.literal("connection"), from: AnchorZ, to: AnchorZ }).strict(),
  z.object({ type: z.literal("arrow"), from: AnchorZ, to: AnchorZ, meaning: z.enum(["observed", "inferred"]) }).strict(),
  z.object({ type: z.literal("polygon"), points: z.array(XYZ).min(3) }).strict(),
  z.object({ type: z.literal("polyline"), points: z.array(XYZ).min(2) }).strict(),
  z.object({ type: z.literal("label"), at: AnchorZ, template_key: z.string(), metric_ids: z.array(ID) }).strict(),
]);
export const LayerZ = z.enum(["structure", "marking", "pressure", "screened_lanes", "outlets", "exposure", "cover",
  "numbers", "changes", "caption"]);
export const OverlaySnapshotZ = z.object({
  schema_version: z.literal("2.0"), overlay_id: ID, replay_id: ID, analysis_epoch: z.number().int(),
  episode_id: ID, episode_revision: z.number().int(), evidence_hash: z.string(), frame_ref: FrameRefZ,
  match_time_s: num, valid_until_s: num, display_mode: z.enum(["live", "retrospective_replay"]),
  surface: z.enum(["inspector_preview", "broadcast"]), audience: z.enum(["fan", "analyst"]),
  language: z.enum(["en", "ko"]),
  elements: z.array(z.object({
    element_id: ID, layer: LayerZ, claim_ids: z.array(ID), metric_ids: z.array(ID), relation_ids: z.array(ID),
    geometry: GeometryZ, style_token: z.string(),
  }).strict()),
}).strict();

const MetricZ = z.object({
  metric_id: ID, key: z.string(), value: num.nullable(),
  unit: z.enum(["m", "s", "mps", "count", "ratio", "score"]), status: Status, window: WindowZ,
  player_ids: z.array(ID), frame_refs: z.array(FrameRefZ), event_ids: z.array(ID), method_version: z.string(),
}).strict();
const ClaimCodeZ = z.enum(["short_options_screened", "central_routes_screened", "behind_space_exposed",
  "behind_depth_reduced", "press_block_disconnected", "between_lines_receiver", "isolated_1v1",
  "wide_outlet_available", "weak_side_accessible", "local_central_overload", "accessible_free_receiver",
  "second_ball_support_gap"]);
const ClaimZ = z.object({
  claim_id: ID, code: ClaimCodeZ, epistemic: z.enum(["computed_observation", "heuristic_inference", "realized_event"]),
  dimension: z.enum(["restricted", "exposed", "context"]), metric_ids: z.array(ID), relation_ids: z.array(ID),
  player_ids: z.array(ID), support_score: num, window: WindowZ,
}).strict();
const ChangeZ = z.object({
  change_id: ID, before_state_id: ID, after_state_id: ID, actor_team_id: ID, actor_player_ids: z.array(ID),
  code: z.enum(["attacker_drop", "defender_step", "line_retreat", "block_shift", "mark_switch", "lane_change",
    "count_change"]),
  relation: z.enum(["observed_next", "consistent_with_response"]), metric_ids: z.array(ID), observed_at_s: num,
}).strict();
export const EpisodeZ = z.object({
  episode_id: ID, revision: z.number().int(), match_id: ID, clip_id: ID,
  lifecycle: z.enum(["candidate", "active", "cooling", "closed"]),
  analysis_status: z.enum(["complete_tradeoff", "partial", "insufficient_evidence"]), defending_team_id: ID,
  analyzed_until_s: num, detected_at_s: num, window: WindowZ, state_ids: z.array(ID),
  restricted_claim_ids: z.array(ID), exposed_claim_ids: z.array(ID), context_claim_ids: z.array(ID),
  change_ids: z.array(ID), story_priority: num,
  outcome: z.object({
    status: z.enum(["not_yet_observed", "exploited_in_synthetic_play", "not_exploited_in_window", "inconclusive"]),
    observed_until_s: num, event_ids: z.array(ID), realized_target_id: ID.nullable(),
  }).strict(),
}).strict();
const EventZ = z.object({
  event_id: ID, match_id: ID, clip_id: ID, event_time_s: num, available_at_s: num,
  kind: z.enum(["pass_release", "pass_receive", "interception", "carry", "duel", "turnover", "shot", "goal",
    "restart"]),
  actor_id: ID.nullable(), target_id: ID.nullable(), related_event_id: ID.nullable(), position: XYZ.nullable(),
}).strict();
export const EvidencePacketZ = z.object({
  schema_version: z.literal("2.0"), evidence_hash: z.string(), episode: EpisodeZ, states: z.array(TacticalStateZ),
  metrics: z.array(MetricZ), claims: z.array(ClaimZ), changes: z.array(ChangeZ), events: z.array(EventZ),
  forbidden_inferences: z.array(z.string()),
}).strict();

const SentencePlanZ = z.object({
  template_key: z.string(), claim_id: ID.nullable(), change_id: ID.nullable(), metric_ids: z.array(ID),
  event_ids: z.array(ID),
}).strict();
export const InsightRevisionZ = z.object({
  insight_id: ID, revision: z.number().int(), episode_id: ID, episode_revision: z.number().int(),
  evidence_hash: z.string(),
  analyst_plan: z.object({
    episode_id: ID, evidence_hash: z.string(), focus_claim_ids: z.array(ID), restricted_claim_ids: z.array(ID),
    exposed_claim_ids: z.array(ID), ordered_change_ids: z.array(ID), omitted_claim_ids: z.array(ID),
    caveat_codes: z.array(z.string()),
  }).strict(),
  producer_plan: z.object({
    episode_id: ID, evidence_hash: z.string(),
    variants: z.array(z.object({
      audience: z.enum(["fan", "analyst"]), language: z.enum(["en", "ko"]), sentences: z.array(SentencePlanZ),
      focus_layers: z.array(LayerZ),
    }).strict()),
  }).strict(),
  source_mode: z.enum(["foundry_live", "foundry_cached", "deterministic_fallback"]),
  review_status: z.enum(["suggested", "approved", "rejected"]), generation_trace_id: ID.nullable(),
}).strict();

export const InsightViewZ = z.object({
  insight: InsightRevisionZ, texts: z.record(z.array(z.string())),
  analysis_status: z.string(), outcome: z.string(), broadcast_visibility: z.string(), is_latest: z.boolean(),
  decision: z.any().optional(),
  // trace of a cloud revision (null for the deterministic plan); recorded by the backend after a real call
  provenance: z.object({
    trace_id: z.string(), model: z.string(), calls: z.number().int(), repairs: z.number().int(),
    result: z.string(), reason: z.string().nullable(), total_ms: z.number(),
    stages: z.array(z.any()), usage: z.record(z.number()),
  }).nullable().optional(),
});
export type InsightView = z.infer<typeof InsightViewZ>;

export const OverlayResponseZ = z.object({
  overlay: OverlaySnapshotZ, label_text: z.record(z.string()), hidden_labels: z.array(z.string()),
  frame_index: z.number().int(), analyzed_through_s: num, episode_revision: z.number().int(),
  insight_revision: z.number().int().nullable(), texts: z.record(z.array(z.string())).nullable(),
});
export type OverlayResponse = z.infer<typeof OverlayResponseZ>;

// Ask PitchIQ: one question, the validated answer (or the fallback) and the agent's real trace
const AskStepZ = z.object({
  step: z.enum(["agent", "agent_repair", "tool", "validation"]), ok: z.boolean().optional(),
  error: z.string().optional(), name: z.string().optional(), args: z.record(z.string()).optional(),
  summary: z.string().optional(), ms: num.optional(), at_ms: num.optional(),
});
export const AskResponseZ = z.object({
  ask_id: ID, question: z.string(), audience: z.enum(["fan", "analyst"]), language: z.enum(["en", "ko"]),
  epoch: z.number().int(), source_mode: z.enum(["foundry_live", "deterministic_fallback"]),
  answer: z.object({
    kind: z.enum(["moment", "decline"]), note_code: z.string().nullable(), analyzed_through_s: num,
    episode_id: z.string().nullable(), episode_revision: z.number().int().nullable(),
    evidence_hash: z.string().nullable(), detected_at_s: num.nullable(),
    sentences: z.object({ en: z.array(z.string()), ko: z.array(z.string()) }),
  }),
  trace: z.object({
    trace_id: ID, model: z.string(), steps: z.array(AskStepZ), runs: z.number().int(), repairs: z.number().int(),
    tool_calls: z.number().int(), usage: z.record(z.number()), result: z.string(), reason: z.string().nullable(),
    total_ms: num,
  }),
});
export type AskResponse = z.infer<typeof AskResponseZ>;
export type AskStep = z.infer<typeof AskStepZ>;

// Recap so far: the Recap workflow's validated items (or the deterministic recap) and its real trace
const LangTexts = z.object({ en: z.array(z.string()), ko: z.array(z.string()) });
const RecapStepZ = z.object({
  step: z.string(), ok: z.boolean().optional(), error: z.string().optional(), name: z.string().optional(),
  args: z.record(z.string()).optional(), summary: z.string().optional(), ms: num.optional(), at_ms: num.optional(),
  items: z.number().optional(),
});
export const RecapResponseZ = z.object({
  recap: z.object({
    kind: z.enum(["recap", "empty"]), analyzed_through_s: num, note_sentences: LangTexts,
    items: z.array(z.object({
      episode_id: ID, episode_revision: z.number().int(), detected_at_s: num, evidence_hash: z.string(),
      involves_followed: z.boolean(), my_club_role: z.enum(["defending", "attacking"]).nullable(),
      sentences: LangTexts,
    })),
  }),
  profile: z.any(), epoch: z.number().int(), source_mode: z.enum(["foundry_live", "deterministic_fallback"]),
  trace: z.object({
    trace_id: ID, model: z.string(), steps: z.array(RecapStepZ), runs: z.number().int(), tool_calls: z.number().int(),
    usage: z.record(z.number()), result: z.string(), reason: z.string().nullable(), total_ms: num,
  }),
});
export type RecapResponse = z.infer<typeof RecapResponseZ>;

// ---- compile-time parity with the spec contracts (tsc fails on drift) ----
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
const assert = <T extends true>(_: T) => _;
assert<Same<z.infer<typeof ObservationFrameZ>, C.ObservationFrame>>(true);
assert<Same<z.infer<typeof TacticalStateZ>, C.TacticalState>>(true);
assert<Same<z.infer<typeof OverlaySnapshotZ>, C.OverlaySnapshot>>(true);
assert<Same<z.infer<typeof EpisodeZ>, C.TradeoffEpisode>>(true);
assert<Same<z.infer<typeof EvidencePacketZ>, C.EvidencePacket>>(true);
assert<Same<z.infer<typeof InsightRevisionZ>, C.InsightRevision>>(true);
