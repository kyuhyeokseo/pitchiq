import { z } from "zod";
import {
  AskResponseZ,
  RecapResponseZ,
  EvidencePacketZ,
  InsightViewZ,
  ObservationFrameZ,
  OverlayResponseZ,
  TacticalStateZ,
  type AskResponse,
  type RecapResponse,
  type InsightView,
  type OverlayResponse,
} from "./schemas";
import type { EvidencePacket, ObservationFrame, TacticalState } from "./contracts";

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

export type EpisodeSummary = {
  episode_id: string; revision: number; lifecycle: string; analysis_status: string; detected_at_s: number;
  analyzed_until_s: number; story_priority: number; restricted: string[]; exposed: string[]; changes: string[];
  heuristic?: string[];  // codes of the claims tagged heuristic_inference in the latest revision
  outcome: string; evidence_hash: string; insight_id: string; not_claimed: string[];
  defending_team_id?: string; player_ids?: string[];  // who defends, and every player the evidence names
};
export type Snapshot = {
  replay_id: string; clip_id: string; analysis_epoch: number; seq: number; clock_s: number; playing: boolean;
  speed: number; clip_start_s: number; clip_end_s: number; frame: ObservationFrame; state: TacticalState;
  live_claims: string[]; episodes: EpisodeSummary[];
  insights: { insight_id: string; revision: number; review_status: string }[];
};
export type Session = { replay_id: string; session_token: string };

const SnapshotZ = z.object({
  replay_id: z.string(), clip_id: z.string(), analysis_epoch: z.number(), seq: z.number(), clock_s: z.number(),
  playing: z.boolean(), speed: z.number(), clip_start_s: z.number(), clip_end_s: z.number(),
  frame: ObservationFrameZ, state: TacticalStateZ, live_claims: z.array(z.string()), episodes: z.array(z.any()),
  insights: z.array(z.any()),
}).passthrough();

async function req<T>(path: string, init: RequestInit, schema?: z.ZodType<T>, session?: Session): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (session) headers["x-session-token"] = session.session_token;
  const r = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as object) } });
  const body = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, body?.detail ?? r.statusText, body);
  if (!schema) return body as T;
  const parsed = schema.safeParse(body);
  if (!parsed.success) throw new ApiError(0, `payload failed contract validation: ${parsed.error.issues[0]?.path.join(".")} ${parsed.error.issues[0]?.message}`);
  return parsed.data;
}

export const api = {
  health: () => req<any>("/api/health", {}),
  clips: () => req<any[]>("/api/clips", {}),
  create: (clip_id: string) => req<any>("/api/replays", { method: "POST", body: JSON.stringify({ clip_id }) }),
  control: (s: Session, action: string, expected_epoch: number, value?: number) =>
    req<Snapshot>(`/api/replays/${s.replay_id}/control`,
      { method: "POST", body: JSON.stringify({ action, value, expected_epoch }) }, SnapshotZ as any, s),
  frame: (s: Session, k: number) => req<ObservationFrame>(`/api/replays/${s.replay_id}/frames/${k}`, {}, ObservationFrameZ, s),
  evidence: (s: Session, episode: string, revision?: number) =>
    req<EvidencePacket>(`/api/replays/${s.replay_id}/evidence/${episode}${revision ? `?revision=${revision}` : ""}`, {},
      EvidencePacketZ as any, s),
  overlay: (s: Session, q: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]));
    return req<OverlayResponse>(`/api/replays/${s.replay_id}/overlays?${qs}`, {}, OverlayResponseZ as any, s);
  },
  insights: (s: Session) => req<InsightView[]>(`/api/replays/${s.replay_id}/insights`, {}, z.array(InsightViewZ) as any, s),
  decide: (s: Session, insight_id: string, revision: number, evidence_hash: string, decision: "approve" | "reject") =>
    req<InsightView>(`/api/replays/${s.replay_id}/insights/${insight_id}/decision`,
      { method: "POST", body: JSON.stringify({ revision, evidence_hash, decision }) }, undefined, s),
  ask: (s: Session, question: string, audience: "fan" | "analyst", language: "en" | "ko") =>
    req<AskResponse>(`/api/replays/${s.replay_id}/ask`,
      { method: "POST", body: JSON.stringify({ question, audience, language }) }, AskResponseZ as any, s),
  recap: (s: Session, profile: { audience: "fan" | "analyst"; language: "en" | "ko"; my_club: string | null;
                                 follow_player: string | null }) =>
    req<RecapResponse>(`/api/replays/${s.replay_id}/recap/agent`, { method: "POST", body: JSON.stringify(profile) },
      RecapResponseZ as any, s),
  remove: (s: Session) => req<null>(`/api/replays/${s.replay_id}`, { method: "DELETE" }, undefined, s),
  // the page is going away: a keepalive request outlives it, and nothing waits for the answer
  removeOnExit: (s: Session) => {
    try {
      void fetch(`/api/replays/${s.replay_id}`, { method: "DELETE", keepalive: true, headers: { "x-session-token": s.session_token } });
    } catch { /* best effort */ }
  },
  exportJson: (s: Session, insight_id: string) => req<any>(`/api/replays/${s.replay_id}/export/${insight_id}`, {}, undefined, s),
  breakdown: (s: Session, episode_id: string, language: "en" | "ko") =>
    req<Breakdown>(`/api/replays/${s.replay_id}/breakdown?${new URLSearchParams({ episode_id, language })}`, {}, undefined, s),
};

// the guided breakdown of one moment (computed by the server from the evidence; sentences rendered by code)
export type BreakdownStep = {
  key: "press" | "takes_away" | "leaves_open" | "what_happened"; revision: number; match_time_s: number;
  layers: string[]; player_ids: string[]; roles: Record<string, string>; sentences: string[]; ball_path?: number[][];
  marks?: string[][];  // [defender, attacker] pairs with a sustained marking relation (the press step)
};
export type Breakdown = { episode_id: string; analysis_status: string; outcome: string; language: string; steps: BreakdownStep[] };

export type WsMessage = { type: string; analysis_epoch: number; seq: number; match_time_s: number; payload: any };

export function openSocket(s: Session, onMessage: (m: WsMessage) => void, onClose: (ev: CloseEvent) => void): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/replays/${s.replay_id}?token=${encodeURIComponent(s.session_token)}`);
  ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
  ws.onclose = onClose;
  return ws;
}
