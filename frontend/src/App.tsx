import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { api, ApiError, openSocket, type Breakdown, type EpisodeSummary, type Session, type WsMessage } from "./api";
import type { EvidencePacket, ObservationFrame, OverlayElement, TacticalState } from "./contracts";
import { GlidingPitch } from "./Pitch";
import {
  InsightViewZ, ObservationFrameZ, TacticalStateZ, type AskResponse, type AskStep, type InsightView, type OverlayResponse,
  type RecapResponse,
} from "./schemas";

type Focus = "both" | "restricted" | "exposed";
type View = "live" | "replay";
type Surface = "inspector_preview" | "broadcast";

const LAYER_NAMES: Record<string, string> = {
  structure: "Lines", marking: "Marking", pressure: "Pressure", screened_lanes: "Screened lanes", outlets: "Outlets",
  exposure: "Exposure", cover: "Cover", numbers: "Numbers", changes: "Changes", caption: "Caption",
};
const STYLE_LEGEND: Record<string, string> = {
  press_arrow: "Observed approach of the presser", carrier_ring: "Ball carrier under pressure",
  lane_screened: "Short option screened (heuristic)", screen_marker: "Screening defender",
  press_cut_lane: "Lane the presser stands in (pressing while cutting it)",
  carrier_space_ring: "Room around an unpressed carrier (nearest defender)",
  mark_supported: "Sustained marking relation", exposure_hatched: "Space behind the line relevant to the runner",
  runner_ring: "Onside runner", cover_late: "Cover arrives late", cover_in_time: "Cover arrives in time",
  cover_link: "Runner's 1 s projection (inferred)", line_back: "Back line", count_label: "Measured count",
  line_label: "Measured value", line_mid: "Midfield line", line_front: "Front line",
  lane_open: "Open ground-pass lane (heuristic)", outlet_target: "Open wide outlet",
  free_receiver_ring: "Accessible receiver", region_outline: "Fixed central region",
  player_count_ring: "Player counted in the region", ghost_path: "Observed movement (before → after)",
};
const CLAIM_TEXT: Record<string, string> = {
  short_options_screened: "Short options screened", central_routes_screened: "Central routes screened",
  behind_space_exposed: "Space behind the line", isolated_1v1: "Isolated 1v1",
  behind_depth_reduced: "Depth behind reduced", press_block_disconnected: "Press disconnected from block",
  between_lines_receiver: "Receiver between lines", wide_outlet_available: "Wide outlet available",
  accessible_free_receiver: "Accessible free receiver", local_central_overload: "Local central overload",
  weak_side_accessible: "Weak side accessible", second_ball_support_gap: "Second-ball support gap",
};
const OUTCOME_TEXT: Record<string, string> = {
  not_yet_observed: "Outcome not yet observed", exploited_in_synthetic_play: "Exploited in synthetic play",
  not_exploited_in_window: "Not exploited in window", inconclusive: "Inconclusive",
};

const fmtClock = (t: number, t0: number) => `${(t - t0).toFixed(1)}s`;
const SEEK_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"]);
const FAN_QUIET = new Set(["numbers"]);  // fans get the lines and areas, not the analyst's count tags
const matchClock = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const matchClockTenths = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
const SOURCE_TEXT: Record<string, string> = {
  foundry_live: "Microsoft Foundry agents", foundry_cached: "Microsoft Foundry agents (cached)",
  deterministic_fallback: "validated templates",
};
const STAGE_TEXT: Record<string, string> = {
  analyst: "Analyst agent chose the story", analyst_repair: "Analyst agent repaired its choice",
  analyst_validation: "Code check of the Analyst plan", producer: "Producer agent chose the sentences",
  producer_repair: "Producer agent repaired its choice", producer_validation: "Code check of the Producer plan",
};
// the guided breakdown: four steps from the evidence (server), then the agents' narration and the approval (on air)
type Lang = "en" | "ko";
const BD_STEPS: { key: string; title: Record<Lang, string>; q: Record<Lang, string> }[] = [
  { key: "press", title: { en: "The press", ko: "압박과 마킹" },
    q: { en: "Who goes to the ball, and who follows whom?", ko: "누가 공으로 달려들고, 누가 누구를 따라붙나?" } },
  { key: "takes_away", title: { en: "What it takes away", ko: "막은 것" },
    q: { en: "Which passes does the defence shut?", ko: "수비가 어떤 패스 길을 막았나?" } },
  { key: "leaves_open", title: { en: "What it leaves open", ko: "내준 것" },
    q: { en: "What is the price of it?", ko: "그 대가로 어디가 비었나?" } },
  { key: "what_happened", title: { en: "What happened", ko: "실제로 일어난 일" },
    q: { en: "Did the attack use it?", ko: "공격이 그 틈을 썼나?" } },
  { key: "on_air", title: { en: "On air", ko: "방송까지" },
    q: { en: "The agents write it up from these facts; a person approves it.", ko: "에이전트가 이 사실로 문장을 고르고, 사람이 승인합니다." } },
];
// what each mark on the pitch means, in the words of a fan (only the marks the step draws are listed)
const BD_KEY: Record<string, Record<Lang, string>> = {
  press_arrow: { en: "Yellow arrow: the presser's run at the ball", ko: "노란 화살표: 공으로 달려드는 압박" },
  carrier_ring: { en: "Yellow ring: the player on the ball", ko: "노란 원: 공을 가진 선수" },
  carrier_space_ring: { en: "Dashed ring: the room the ball carrier has (nobody presses)", ko: "점선 원: 아무도 압박하지 않는 공 주변 공간" },
  mark_supported: { en: "Thin white line: who follows whom", ko: "가는 흰 선: 누가 누구를 따라붙는지" },
  lane_screened: { en: "Pink dots ending in a bar: a pass the defence has blocked", ko: "막대로 끝나는 분홍 점선: 수비가 막은 패스 길" },
  press_cut_lane: { en: "Faint pink line with an X: the pass the presser cuts on the way in", ko: "X 표시된 옅은 분홍 선: 압박 선수가 달려가며 끊는 패스 길" },
  screen_marker: { en: "Pink ring: the defender standing in that pass", ko: "분홍 원: 그 패스 길에 서 있는 수비수" },
  line_back: { en: "White dashes: the back line", ko: "흰 점선: 최종 수비 라인" },
  line_mid: { en: "Faint dashes: the midfield line", ko: "옅은 점선: 미드필드 라인" },
  line_front: { en: "Faintest dashes: the front line", ko: "가장 옅은 점선: 최전방 라인" },
  exposure_hatched: { en: "Cyan stripes: the space the defence leaves open", ko: "하늘색 빗금: 수비가 비워 둔 공간" },
  runner_ring: { en: "Cyan ring: the runner going into it", ko: "하늘색 원: 그 공간으로 뛰는 선수" },
  cover_late: { en: "Cyan dashes: the cover, arriving late", ko: "하늘색 점선: 늦게 도착하는 커버" },
  cover_in_time: { en: "Green line: the cover, arriving in time", ko: "초록 선: 제때 도착하는 커버" },
  cover_link: { en: "Cyan arrow: where the runner is heading", ko: "하늘색 화살표: 침투 선수가 향하는 곳" },
  lane_open: { en: "Green arrow: a pass that is still on", ko: "초록 화살표: 아직 열려 있는 패스" },
  outlet_target: { en: "Green ring: the open player out wide", ko: "초록 원: 측면에 비어 있는 선수" },
  free_receiver_ring: { en: "Cyan dashed ring: a free player who can be reached", ko: "하늘색 점선 원: 패스를 받을 수 있는 빈 선수" },
  ghost_path: { en: "White dots: how the players moved", ko: "흰 점: 선수들이 움직인 길" },
};
const ROLE_TEXT: Record<string, Record<Lang, string>> = {
  presser: { en: "Presser", ko: "압박" }, carrier: { en: "On the ball", ko: "공 소유" },
  runner: { en: "Runner", ko: "침투" }, cover: { en: "Cover", ko: "커버" },
  passer: { en: "Pass", ko: "패스" }, receiver: { en: "Receives here", ko: "여기서 받음" },
  interceptor: { en: "Intercepts", ko: "가로챔" }, shooter: { en: "Shot", ko: "슈팅" },
  saver: { en: "Saves", ko: "선방" }, blocker: { en: "Blocks", ko: "막아 냄" },
};
const BD_UI: Record<Lang, Record<string, string>> = {
  en: { intro: "What does this defence take away, and what does it leave open?", go: "Break down the defence",
        sub: "One moment in four steps, then on air. Every line and number on the pitch is measured from the tracking data.",
        live: "Just watch", code: "Code measures every line and number", person: "A person approves before air",
        agents: "Microsoft Foundry agents choose the words (gpt-5-mini)", templates: "Offline: validated templates choose the words",
        onairNote: "Revision {rev}, numbers as of {t}. The steps showed each fact when the analysis first had it, so a number can differ slightly.",
        back: "Back", next: "Next", exit: "Exit", stop: "Exit the breakdown", busy: "Analysing the clip…", ball: "White arrow: where the ball actually went",
        approve: "Approve and put on air", explore: "Explore the desk", onair: "On air", none: "Nothing more to show here.",
        waiting: "Preparing the text for approval…",
        marks: "White band: a defender and the attacker he follows (sustained marking)",
        moments: "Moments in this clip", defending: "{team} defending",
        next_pass: "Next on the ball, at {t}: {p} passes to {q}.",
        next_interception: "Next on the ball, at {t}: {q} intercepts {p}'s pass.",
        next_save: "Next on the ball, at {t}: {p} shoots, and {q} saves it.",
        next_block: "Next on the ball, at {t}: {p} shoots, and {q} blocks it.",
        through_blocked: "That was one of the passes counted as blocked in step 2: the block is estimated from where players stand, and this one did not hold.",
        clip_ends: "The clip ends before anyone plays the ball on.",
        press_facts: "On the ball: {c}. Nearest defender: {d}, {m} m away.",
        unmarked: "Also left unmarked: {p}.", unmarked_partial: "No short pass to them counts as open, but left unmarked: {p}.",
        other_run: "{r} is running in behind too: {c} goes with him.", other_run_late: "{r} is running in behind too, and nobody gets there in time." },
  ko: { intro: "이 수비는 무엇을 막고, 그 대가로 무엇을 내줄까?", go: "수비 전술 해부",
        sub: "한 장면을 4단계로 보고, 마지막에 방송까지 이어집니다. 경기장 위의 선과 숫자는 모두 추적 데이터로 계산한 것입니다.",
        live: "그냥 경기 보기", code: "코드가 모든 선과 숫자를 계산", person: "사람이 승인해야 방송",
        agents: "Microsoft Foundry 에이전트가 문장을 고름 (gpt-5-mini)", templates: "오프라인: 검증된 템플릿이 문장을 고름",
        onairNote: "revision {rev}, {t} 기준 숫자입니다. 앞 단계들은 각 사실을 처음 잡았을 때의 값이라 숫자가 조금 다를 수 있습니다.",
        back: "이전", next: "다음", exit: "끝내기", stop: "해부 끝내기", busy: "클립을 분석하는 중…", ball: "흰 화살표: 공이 실제로 간 길",
        approve: "승인하고 방송에 내보내기", explore: "직접 살펴보기", onair: "방송 중", none: "이 단계에서 더 보여 줄 것이 없어요.",
        waiting: "승인할 문장을 준비하는 중…",
        marks: "흰 띠: 수비수와 그가 따라붙는 공격수 (지속된 마킹)",
        moments: "이 클립의 장면", defending: "{team} 수비",
        next_pass: "{t} 다음 공 흐름: {p} → {q} 패스.",
        next_interception: "{t} 다음 공 흐름: {p}의 패스를 {q}가 가로챔.",
        next_save: "{t} 다음 공 흐름: {p}의 슈팅을 {q}가 선방.",
        next_block: "{t} 다음 공 흐름: {p}의 슈팅을 {q}가 막아 냄.",
        through_blocked: "이 패스는 2단계에서 막힌 것으로 본 패스입니다. 막힘은 선수 위치로 추정한 값이라, 이번에는 통과했습니다.",
        clip_ends: "공이 다음으로 연결되기 전에 클립이 끝납니다.",
        press_facts: "공 소유: {c}. 가장 가까운 수비: {d}, {m} m.",
        unmarked: "그 밖에 마크 없이 남은 선수: {p}.", unmarked_partial: "열린 짧은 패스로 볼 수는 없지만, 마크 없이 남은 선수: {p}.",
        other_run: "{r} 선수도 뒷공간으로 뜁니다. {c} 선수가 따라갑니다.", other_run_late: "{r} 선수도 뒷공간으로 뛰고, 제때 따라가는 수비가 없습니다." },
};
const STATUS_TEXT: Record<string, string> = { complete_tradeoff: "complete trade-off", partial: "partial",
  insufficient_evidence: "insufficient evidence" };
// why a claim about what is left open was held back (the analysis records a reason code); {p} is the player
const HELD_TEXT: Record<string, Record<Lang, string>> = {
  offside_geometry: { en: "Not counted as space behind: {p}'s run starts beyond the back line (offside).",
                      ko: "{p}의 침투는 수비 라인보다 앞(오프사이드)에서 시작해 뒷공간으로 세지 않습니다." },
  secondary_cover_in_time: { en: "Not counted as space behind: cover reaches {p} in time.",
                             ko: "커버가 {p}에게 제때 도착해 뒷공간으로 세지 않습니다." },
  gk_cover_in_time: { en: "Not counted as a 1v1: the goalkeeper reaches {p} in time.",
                      ko: "골키퍼가 {p}에게 제때 도착해 1대1 고립으로 세지 않습니다." },
  count_without_accessible_lane: { en: "More attackers in the middle, but no free player can be reached.",
                                   ko: "중앙에 공격수가 더 많지만, 패스를 받을 수 있는 빈 선수는 없습니다." },
};

const SUGGESTIONS: Record<"en" | "ko", string[]> = {
  en: ["What did the defence shut down?", "What did that leave open?", "What happened next?", "Why did the coach set it up like that?"],
  ko: ["수비가 무엇을 막았어?", "그 대가로 무엇을 내줬어?", "그다음에 어떻게 됐어?", "감독은 왜 그렇게 세웠어?"],
};

export function App() {
  const [health, setHealth] = useState<any>(null);
  const [clips, setClips] = useState<any[]>([]);
  const [clipId, setClipId] = useState<string>("");
  const [session, setSession] = useState<Session | null>(null);
  const [epoch, setEpoch] = useState(1);
  const [clock, setClock] = useState(0);
  const [range, setRange] = useState<[number, number]>([0, 1]);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [frame, setFrame] = useState<ObservationFrame | null>(null);
  const [state, setState] = useState<TacticalState | null>(null);
  const [liveClaims, setLiveClaims] = useState<string[]>([]);
  const [episodes, setEpisodes] = useState<EpisodeSummary[]>([]);
  const [insights, setInsights] = useState<Record<string, InsightView>>({});
  const [selEp, setSelEp] = useState<string | null>(null);
  const [selRev, setSelRev] = useState<number | null>(null);
  const [audience, setAudience] = useState<"fan" | "analyst">("fan");
  const [details, setDetails] = useState(false);
  const [followed, setFollowed] = useState<string | null>(null);  // player the viewer follows (click a player)
  const [onlyFollowed, setOnlyFollowed] = useState(false);  // timeline shows only the followed player's moments
  const [myClub, setMyClubState] = useState<string>(() => { try { return localStorage.getItem("pitchiq.myClub") ?? ""; } catch { return ""; } });
  const setMyClub = (v: string) => { setMyClubState(v); try { localStorage.setItem("pitchiq.myClub", v); } catch { /* private mode */ } };  // technical ids, hashes and epochs, for analysts and engineers
  const [language, setLanguage] = useState<"en" | "ko">("en");
  const [focus, setFocus] = useState<Focus>("both");
  const [view, setView] = useState<View>("live");
  const [surface, setSurface] = useState<Surface>("inspector_preview");
  const [overlay, setOverlay] = useState<OverlayResponse | null>(null);
  const [replayFrame, setReplayFrame] = useState<ObservationFrame | null>(null);
  const [packet, setPacket] = useState<EvidencePacket | null>(null);
  const [selEl, setSelEl] = useState<OverlayElement | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showFree, setShowFree] = useState(true);  // ring the attacking team's unmarked players
  const [userPicked, setUserPicked] = useState(false);
  const [scrub, setScrub] = useState<number | null>(null);  // where the viewer holds the seek bar, until the seek is answered
  const lastSnap = useRef<[number, number]>([0, -1]);  // (epoch, seq) of the newest snapshot applied
  const seekQueue = useRef<{ busy: boolean; next: number | null }>({ busy: false, next: null });
  const playButton = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {  // Space plays or pauses, as in a video player, unless a control or a text field has the focus
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat) return;
      if ((e.target as HTMLElement).closest?.("input, textarea, select, button, summary, [contenteditable]")) return;
      e.preventDefault();
      playButton.current?.click();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [bd, setBd] = useState<Breakdown | null>(null);  // the guided breakdown on screen, if any
  const [bdStep, setBdStep] = useState(0);
  const [bdBusy, setBdBusy] = useState(false);
  const [intro, setIntro] = useState(true);  // a clip opens on the question the app answers
  const [autoReplay, setAutoReplay] = useState(false);  // paused or ended on an empty live pitch: showing the moment
  const liveChosen = useRef(false);  // the user picked Live while paused: respect it until the next Play
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const currentReplay = useRef<string | null>(null);
  const sessionRef = useRef<Session | null>(null);
  const [sessionNonce, setSessionNonce] = useState(0);  // bumped to start a new replay when the server dropped ours

  // the narration status line follows real calls: refreshed after each cloud revision or answer, and every 30 s
  const refreshHealth = useCallback(() => { api.health().then(setHealth).catch(() => {}); }, []);
  useEffect(() => {
    const id = window.setInterval(refreshHealth, 30000);
    return () => window.clearInterval(id);
  }, [refreshHealth]);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(String(e)));
    api.clips().then((c) => { setClips(c); if (c.length) setClipId(c[0].clip_id); }).catch((e) => setError(String(e)));
  }, []);

  const applySnapshot = useCallback((s: any) => {
    if (s.replay_id !== currentReplay.current) return;  // a response for a session we already left
    // a control reply and the socket's snapshots can arrive in either order: keep the newest (epoch, seq), so a late
    // older one never turns Pause back into Play or moves the clock back
    const [e, q] = lastSnap.current;
    if (s.analysis_epoch < e || (s.analysis_epoch === e && s.seq < q)) return;
    lastSnap.current = [s.analysis_epoch, s.seq];
    setEpoch(s.analysis_epoch); setClock(s.clock_s); setPlaying(s.playing); setSpeed(s.speed);
    setRange([s.clip_start_s, s.clip_end_s]); setFrame(s.frame); setState(s.state); setLiveClaims(s.live_claims);
    setEpisodes(s.episodes);
  }, []);

  const refreshInsights = useCallback(async (s: Session) => {
    const list = await api.insights(s);
    setInsights(Object.fromEntries(list.map((v) => [v.insight.insight_id, v])));
  }, []);

  // ---------------------------------------------------------------- session + socket
  useEffect(() => {
    if (!clipId) return;
    let closed = false;
    let retry = 0;
    let timer: number | undefined;
    setSelEp(null); setSelRev(null); setOverlay(null); setPacket(null); setInsights({}); setSelEl(null);
    setBd(null); setBdStep(0); setIntro(true);
    setUserPicked(false); setView("live"); setAutoReplay(false); setFollowed(null); setOnlyFollowed(false); setSurface("inspector_preview"); setEpisodes([]); setSession(null);
    currentReplay.current = null;  // drop anything the previous session's socket still delivers
    lastSnap.current = [0, -1]; setScrub(null);
    const connect = (s: Session) => {
      wsRef.current?.close();
      wsRef.current = openSocket(s, (m: WsMessage) => { retry = 0; onMessage(m); }, (ev) => {
        if (closed || s.replay_id !== currentReplay.current) return;  // we closed it, or it belongs to an old replay
        if (ev.code === 4404 || ev.code === 4403 || ev.code === 4410) {  // the server dropped this replay
          setNotice("This replay had expired, so a new one was started.");
          setSessionNonce((n) => n + 1);
          return;
        }
        if (retry >= 5) { setError("Lost the connection to the server. Reload the page to continue."); return; }
        timer = window.setTimeout(() => { if (!closed) connect(s); }, 1000 * 2 ** retry++);
      });
    };
    api.create(clipId).then((r) => {
      const s = { replay_id: r.replay_id, session_token: r.session_token };
      if (closed) { api.remove(s).catch(() => {}); return; }  // the clip changed while this one was being made
      currentReplay.current = r.replay_id;
      sessionRef.current = s;
      setSession(s);
      applySnapshot(r.snapshot);
      refreshInsights(s).catch((e) => { if (!closed) setError(String(e)); });
      connect(s);
    }).catch((e) => setError(String(e)));
    return () => {
      closed = true;
      window.clearTimeout(timer);
      wsRef.current?.close();
      const s = sessionRef.current;
      sessionRef.current = null;
      // leaving a clip frees its replay on the server, after its last in-flight requests have had time to finish
      if (s) window.setTimeout(() => { api.remove(s).catch(() => {}); }, 3000);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipId, sessionNonce]);

  useEffect(() => {  // closing the tab frees the replay too
    const bye = () => { if (sessionRef.current) api.removeOnExit(sessionRef.current); };
    window.addEventListener("pagehide", bye);
    return () => window.removeEventListener("pagehide", bye);
  }, []);

  const onMessage = (m: WsMessage & { replay_id?: string }) => {
    if (m.replay_id && m.replay_id !== currentReplay.current) return;  // a late message from a previous session
    if (m.type === "state_snapshot") { applySnapshot(m.payload); return; }
    if ((m.type === "tracking_frame" || m.type === "tactical_snapshot") && m.analysis_epoch < lastSnap.current[0]) return;
    if (m.type === "tracking_frame") {
      const f = ObservationFrameZ.safeParse(m.payload);
      if (!f.success) { setError("tracking frame failed contract validation"); return; }
      setFrame(f.data); setClock(m.match_time_s);
    } else if (m.type === "tactical_snapshot") {
      const st = TacticalStateZ.safeParse(m.payload.state);
      if (!st.success) { setError("tactical state failed contract validation"); return; }
      setState(st.data); setLiveClaims(m.payload.live_claims);
    } else if (m.type === "episode_update") {
      setEpisodes(m.payload.episodes);
    } else if (m.type === "insight_update") {
      const v = InsightViewZ.safeParse(m.payload);
      if (v.success) setInsights((prev) => ({ ...prev, [v.data.insight.insight_id]: v.data }));
      if (v.success && v.data.insight.source_mode !== "deterministic_fallback") refreshHealth();
    }
  };

  // Until the producer picks one, follow the best story: highest priority complete trade-off, else the latest episode.
  // While the replay plays, only moments still in progress compete, so the live pitch shows what is happening now
  // instead of holding on to a moment that has closed (its live overlay has expired).
  useEffect(() => {
    if (userPicked || !episodes.length) return;
    // only the live Inspector pitch follows what is happening now; Broadcast and Replay moment keep their moment while
    // the replay plays, so an approved moment stays on air
    const liveDesk = view === "live" && surface === "inspector_preview";
    if (playing && !liveDesk && episodes.some((e) => e.episode_id === selEp)) return;
    const open = episodes.filter((e) => e.lifecycle !== "closed");
    const now = playing || liveChosen.current;  // playing, or paused where the viewer sought to
    const pool = now && liveDesk && open.length ? open : episodes;
    const complete = pool.filter((e) => e.analysis_status === "complete_tradeoff");
    // keep the followed moment while it is still open, unless a complete trade-off has opened elsewhere
    const cur = open.find((e) => e.episode_id === selEp);
    if (now && liveDesk && cur && (cur.analysis_status === "complete_tradeoff" || !complete.length)) return;
    // without a complete trade-off, show the partial episode with the most observed evidence
    // (changes and explicitly withheld claims), then the strongest one
    const weight = (e: EpisodeSummary) => e.changes.length + (e.not_claimed?.length ?? 0);
    const pick = complete.length
      ? complete.slice().sort((a, b) => b.story_priority - a.story_priority)[0]
      : pool.slice().sort((a, b) => weight(b) - weight(a) || b.story_priority - a.story_priority)[0];
    if (pick.episode_id !== selEp) { setSelEp(pick.episode_id); setSelRev(null); }  // a revision belongs to its episode
  }, [episodes, selEp, userPicked, playing, view, surface]);

  const ep = episodes.find((e) => e.episode_id === selEp) ?? null;
  const insight = ep ? insights[ep.insight_id] : undefined;
  const approved = insight?.insight.review_status === "approved";  // this (latest) revision is approved
  // broadcast and export follow the approved revision, which may be older than the latest one
  const approvedRev = insight?.approved_revision ?? null;

  // ---------------------------------------------------------------- overlay + evidence fetch
  const liveKey = view === "live" && surface === "inspector_preview" ? frame?.frame_ref.frame_index : -1;
  const liveSentAt = useRef(0);
  const overlaySeq = useRef(0);
  const overlayDrawn = useRef(0);
  const overlayKey = useRef("");
  useEffect(() => {
    overlayKey.current = "";
    if (!session || !ep) { setOverlay(null); return; }
    if (surface === "broadcast" && approvedRev === null) { setOverlay(null); return; }
    // what an answer is for, apart from the live frame: an answer for anything else is never drawn
    const key = [session.replay_id, ep.episode_id, ep.revision, audience, language, focus, view, surface, selRev].join("|");
    overlayKey.current = key;
    const mode = surface === "broadcast" || view === "replay" ? "retrospective_replay" : "live";
    // live: at most one request per 150 ms but never starved (at 2x a new frame arrives every 100 ms)
    const wait = mode === "live" ? Math.max(0, 150 - (performance.now() - liveSentAt.current)) : 0;
    const t = setTimeout(() => {
      if (mode === "live") liveSentAt.current = performance.now();
      const seq = ++overlaySeq.current;
      api.overlay(session, {
        // the inspector pitch is the producer's desk: always the full evidence overlay; the audience setting changes
        // the narration and the broadcast overlay, which is what fans see
        episode_id: ep.episode_id, audience: surface === "broadcast" ? audience : "analyst", language, surface, focus,
        display_mode: mode,
        revision: mode === "retrospective_replay" && surface !== "broadcast" ? selRev ?? undefined : undefined,
      }).then(async (o) => {
        // over a slow link several live requests are in flight, so draw every answer newer than the one on screen
        // (waiting for the latest would draw nothing); never draw an older answer over a newer one
        if (key !== overlayKey.current || seq <= overlayDrawn.current) return;
        overlayDrawn.current = seq;
        setOverlay(o);
        if (o.overlay.display_mode !== "retrospective_replay") return;
        const f = await api.frame(session, o.frame_index);
        // a frame that arrives after a newer overlay was drawn belongs to the older one: it would put the newer
        // overlay on the wrong players (a breakdown step showing another step's frame)
        if (key === overlayKey.current && seq === overlayDrawn.current) setReplayFrame(f);
      // 404: a moment this cutoff no longer has; 409: no approved revision of it at this cutoff (broadcast)
      }).catch((e) => { if (!(e instanceof ApiError && (e.status === 404 || e.status === 409))) setError(String(e)); });
    }, wait);
    return () => clearTimeout(t);
  }, [session, ep?.episode_id, ep?.revision, audience, language, focus, view, surface, selRev, liveKey, approvedRev]);

  useEffect(() => {
    if (!session || !ep) { setPacket(null); return; }
    // a 404 is a moment the replay no longer has after a seek back (as for the overlay)
    api.evidence(session, ep.episode_id, selRev ?? undefined).then(setPacket)
      .catch((e) => { if (!(e instanceof ApiError && e.status === 404)) setError(String(e)); });
  }, [session, ep?.episode_id, ep?.revision, selRev]);

  // ---------------------------------------------------------------- controls
  const control = async (action: string, value?: number) => {
    if (!session || session.replay_id !== currentReplay.current) return;
    // the epoch of the newest snapshot, not of the last render: a control right after a seek is not refused as stale
    try { applySnapshot(await api.control(session, action, lastSnap.current[0] || epoch, value)); setNotice(null); }
    catch (e) {
      if (e instanceof ApiError && e.status === 409) setNotice("The replay moved on meanwhile; the desk has caught up.");
      else setError(String(e));
    }
  };
  // a seek from the bar: the pitch then shows the frame at that time (not the last moment's replay) until the next Play
  const seekTo = async (t: number) => {
    setIntro(false); exitBreakdown();
    liveChosen.current = true; setView("live"); setSelRev(null); setAutoReplay(false);
    if (t < clock - 0.05) { setUserPicked(false); setEpisodes([]); setSelEp(null); setSelEl(null); }  // later moments go
    // one seek at a time, each with the epoch the previous one returned; quick successive seeks keep only the newest
    const q = seekQueue.current;
    q.next = t;
    if (q.busy) return;
    q.busy = true;
    try {
      while (q.next !== null) { const target = q.next; q.next = null; await control("seek", target); }
    } finally { q.busy = false; setScrub(null); }
  };
  const replayFromStart = async () => {
    if (!session || session.replay_id !== currentReplay.current) return;
    // back to the live desk, with no moments listed or selected, before the seek leaves: the rewound replay has not
    // found them yet, so nothing may ask for them, and a replay answer still in flight is dropped instead of drawn
    setUserPicked(false); setEpisodes([]); setSelEp(null); setSelEl(null); setView("live"); setSurface("inspector_preview");
    setSelRev(null); setAutoReplay(false);
    liveChosen.current = false;
    try {
      const snap = await api.control(session, "seek", lastSnap.current[0] || epoch, range[0]);
      applySnapshot(snap);
      applySnapshot(await api.control(session, "play", snap.analysis_epoch));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setNotice("The replay moved on meanwhile; the desk has caught up.");
      else setError(String(e));
    }
  };
  const decide = async (decision: "approve" | "reject"): Promise<boolean> => {
    if (!session || !insight) return false;
    try {
      const v = await api.decide(session, insight.insight.insight_id, insight.insight.revision,
        insight.insight.evidence_hash, decision);
      setInsights((p) => ({ ...p, [v.insight.insight_id]: v }));
      const rev = ep?.revision;  // the evidence revision the card shows (the timeline's number)
      setNotice(decision === "approve" ? `Approved revision ${rev}. Broadcast replays it.`
        : v.approved_revision != null  // a newer revision was rejected: the approved one stays on air
          ? `Rejected revision ${rev}. Broadcast keeps the approved one.`
          : `Rejected revision ${rev}. Hidden from broadcast.`);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setNotice("The evidence changed: a newer revision needs review.");
        await refreshInsights(session);
      } else setError(String(e));
      return false;
    }
  };
  // The breakdown tells one finished moment: the analysis first runs to the end of the clip (each step still shows
  // the revision that first had it), then the moment the viewer picked, or else the best complete trade-off.
  const startBreakdown = async () => {
    if (!session || session.replay_id !== currentReplay.current || bdBusy) return;
    setBdBusy(true);  // the question card stays up, saying it is analysing, until the first step is ready
    try {
      let eps = episodes;
      if (playing || clock < range[1] - 0.1) {
        const snap = await api.control(session, "seek", lastSnap.current[0] || epoch, range[1]);
        applySnapshot(snap); eps = snap.episodes;
        // the seek built the moments' insights; fetch them rather than wait for the socket (it may open after this)
        refreshInsights(session).catch(() => {});
      }
      const complete = eps.filter((e) => e.analysis_status === "complete_tradeoff");
      // a complete trade-off whose outcome the replay saw tells the whole story, so it opens first (a look-alike pair
      // then opens on the moment that tells it apart, not on their shared opening)
      const seen = (e: EpisodeSummary) => (e.outcome === "exploited_in_synthetic_play" ? 1 : 0);
      const pick = (userPicked && eps.find((e) => e.episode_id === selEp))
        || (complete.length ? complete : eps).slice().sort((a, b) => seen(b) - seen(a)
          || b.story_priority - a.story_priority)[0];
      if (!pick) { setNotice("No moment in this clip to break down."); return; }
      const b = await api.breakdown(session, pick.episode_id, language);
      setUserPicked(true); setSelEp(pick.episode_id); setSelEl(null); setFocus("both"); setAutoReplay(false);
      setBd(b); setBdStep(0);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setNotice("The replay moved on meanwhile; the desk has caught up.");
      else setError(String(e));
    } finally { setBdBusy(false); setIntro(false); }
  };
  // another moment of the same clip, picked in the panel: its own breakdown from the first step
  const pickMoment = async (id: string) => {
    if (!session || bdBusy) return;
    setBdBusy(true);
    try {
      const b = await api.breakdown(session, id, language);
      setUserPicked(true); setSelEp(id); setSelEl(null); setFocus("both"); setBd(b); setBdStep(0);
    } catch (e) {
      setError(String(e));
    } finally { setBdBusy(false); }
  };
  const exitBreakdown = () => { setBd(null); setBdStep(0); };
  // each step shows the moment as the analysis had it then; the last one is the latest revision, ready for approval
  useEffect(() => {
    if (!bd) return;
    setView("replay"); setSelEl(null);
    if (bdStep < bd.steps.length) { setSurface("inspector_preview"); setSelRev(bd.steps[bdStep].revision); }
    else setSelRev(null);
  }, [bd, bdStep]);
  useEffect(() => {  // the breakdown speaks the chosen language
    if (!bd || !session || bd.language === language) return;
    api.breakdown(session, bd.episode_id, language).then(setBd).catch((e) => setError(String(e)));
  }, [language, bd, session]);
  const exportJson = async () => {
    if (!session || !insight) return;
    const data = await api.exportJson(session, insight.insight.insight_id);
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = `${insight.insight.insight_id}_r${data.decision?.insight_revision ?? approvedRev}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  // ---------------------------------------------------------------- derived
  const teams = clips.find((c) => c.clip_id === clipId)?.teams ?? [];
  const teamColor = (id: string) => (id === "blue" ? "var(--blue)" : id === "red" ? "var(--red)" : "#999");
  const jersey = (pid: string) => String(parseInt(pid.split("_").pop() ?? "0", 10));
  // a replay draws on its own frame; until that frame arrives, the current one without the overlay (never a blank pitch)
  const replayMode = surface === "broadcast" || view === "replay";
  const shownFrame = replayMode ? replayFrame ?? frame : frame;
  // a live overlay belongs to the moment it was analysed: past its valid_until it would draw an old press or lane
  // on players who have moved on, so it is not drawn at all
  const liveExpired = overlay?.overlay.display_mode === "live" && view === "live" && surface === "inspector_preview"
    && clock > overlay.overlay.valid_until_s + 1e-6;
  const overlaySnap = liveExpired || (replayMode && !replayFrame) ? null : overlay?.overlay ?? null;
  // a finished replay never leaves an empty pitch: at the end of the clip it shows the followed moment as analysed
  // (Play starts the clip again). Pause keeps the pitch on the paused frame, as any video player does. While playing,
  // an expired live overlay is still not drawn on later frames.
  useEffect(() => {
    const ended = clock >= range[1] - 0.1;
    if (!ended) { if (playing) liveChosen.current = false; return; }
    if (view !== "live" || surface !== "inspector_preview" || !liveExpired || liveChosen.current) return;
    setView("replay"); setSelRev(null); setAutoReplay(true);
  }, [playing, view, surface, liveExpired, clock, range]);
  const layersPresent = useMemo(() => Array.from(new Set(overlaySnap?.elements.map((e) => e.layer) ?? [])), [overlaySnap]);
  const bdCur = bd && bdStep < bd.steps.length ? bd.steps[bdStep] : null;
  const L: Lang = language;
  // a breakdown step draws only its own layers, and dims everyone but the players it is about
  const shownHidden = useMemo(() => bdCur ? new Set(layersPresent.filter((l) => !bdCur.layers.includes(l))) : hidden,
    [bdCur, layersPresent, hidden]);
  const bdKey = useMemo(() => {
    if (!bd) return [];
    const pos = Object.fromEntries((shownFrame?.players ?? []).map((p) => [p.player_id, p.position]));
    const shown = (e: OverlayElement) => {  // a presser on top of the carrier hides the arrow: don't list it
      const g = e.geometry as any;
      if (e.style_token !== "press_arrow" || g.from?.kind !== "player" || g.to?.kind !== "player") return true;
      const a = pos[g.from.player_id], b = pos[g.to.player_id];
      return !a || !b || Math.hypot(a.x_m - b.x_m, a.y_m - b.y_m) >= 3;
    };
    return Array.from(new Set(overlaySnap?.elements.filter((e) => !shownHidden.has(e.layer) && e.geometry.type !== "label"
      && shown(e)).map((e) => e.style_token) ?? [])).filter((t) => BD_KEY[t]);
  }, [bd, overlaySnap, shownHidden, shownFrame]);
  const bdTags = useMemo(() => bdCur ? Object.fromEntries(Object.entries(bdCur.roles)
    .map(([pid, r]) => [pid, { text: ROLE_TEXT[r]?.[L] ?? r, role: r }])) : undefined, [bdCur, L]);
  const showIntro = intro && !bd && (bdBusy || (!playing && clock <= range[0] + 0.05 && surface === "inspector_preview"
    && view === "live"));
  const stylesPresent = useMemo(() => Array.from(new Set(overlaySnap?.elements.filter((e) => !hidden.has(e.layer)).map((e) => e.style_token) ?? [])), [overlaySnap, hidden]);
  const claimById = useMemo(() => Object.fromEntries((packet?.claims ?? []).map((c) => [c.claim_id, c])), [packet]);
  const metricById = useMemo(() => Object.fromEntries((packet?.metrics ?? []).map((m) => [m.metric_id, m])), [packet]);
  // the "heuristic" mark comes from the epistemic tags of the claims the card lists (the latest revision's)
  const heuristicCodes = useMemo(() => new Set<string>(ep?.heuristic ?? []), [ep]);
  const claimLabel = (c: string) => <>{CLAIM_TEXT[c]}{heuristicCodes.has(c) && <> <small>heuristic</small></>}</>;
  const highlight = useMemo(() => {
    const s = new Set<string>();
    if (bdCur) { for (const p of [...bdCur.player_ids, ...(bdCur.marks ?? []).flat()]) s.add(p); return s; }
    if (selEl) for (const cid of selEl.claim_ids) for (const p of claimById[cid]?.player_ids ?? []) s.add(p);
    return s;
  }, [selEl, claimById, bdCur]);
  // an earlier revision on the pitch is told with its own narration, never with what happened later
  const viewingPast = view === "replay" && surface === "inspector_preview" && !!ep && selRev !== null
    && selRev !== ep.revision;
  // the card describes the revision on screen: an earlier one with its own claims, changes and outcome
  const cardEp = useMemo((): EpisodeSummary | null => {
    if (!ep || !viewingPast || !packet || packet.episode.revision !== selRev) return ep;
    const code = Object.fromEntries(packet.claims.map((c) => [c.claim_id, c.code]));
    return { ...ep, restricted: packet.episode.restricted_claim_ids.map((c) => code[c]),
             exposed: packet.episode.exposed_claim_ids.map((c) => code[c]),
             changes: packet.changes.map((c) => c.code), outcome: packet.episode.outcome.status };
  }, [ep, viewingPast, packet, selRev]);
  const texts = (viewingPast ? (overlay?.episode_revision === selRev ? overlay.texts : null) : insight?.texts)
    ?.[`${audience}_${language}`] ?? [];
  // the cutoff of the narration actually shown (none while the matching narration is still loading)
  const asOf = viewingPast
    ? (overlay?.episode_revision === selRev ? overlay.analyzed_through_s : null)
    : (ep && insight && insight.insight.evidence_hash === ep.evidence_hash ? ep.analyzed_until_s : null);
  const [t0, t1] = range;
  const shownClock = scrub ?? clock;
  const lastCloud = health?.last_cloud_result;
  const statusLine = health ? (health.cloud_enabled
    ? `Narration: Foundry enabled · last call ${lastCloud?.last_call_succeeded == null ? "none yet"
        : lastCloud.last_call_succeeded ? "succeeded" : `failed (${lastCloud.last_error})`}`
    : `Narration: deterministic fallback (${health.cloud_disabled_reason ?? "Foundry not configured"})`) : "…";

  useEffect(() => {  // a notice is a toast: it leaves on its own and never moves the desk
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(t);
  }, [notice]);
  useEffect(() => {  // the evidence for a click shows next to it
    if (selEl) document.querySelector(".insp")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selEl]);

  const bdPanel = bd && <BreakdownPanel bd={bd} step={bdStep} lang={L} keys={bdKey} texts={texts} insight={insight}
    approvedRev={approvedRev} surface={surface} latestRev={ep?.revision ?? null} asOf={asOf} onStep={setBdStep}
    moments={[...episodes].sort((a, b) => a.detected_at_s - b.detected_at_s)} busy={bdBusy} onPick={pickMoment}
    teamName={(id) => teams.find((tm: any) => tm.team_id === id)?.display_name ?? id} teamColor={teamColor}
    onExit={() => { exitBreakdown(); setSurface("inspector_preview"); }}
    onApprove={async () => { if (await decide("approve")) setSurface("broadcast"); }} />;

  if (!frame) return <div className="boot">{error ?? "Loading PitchIQ…"}</div>;
  return (
    <div className="app">
      <header className="top">
        <div className="brand"><b>PitchIQ</b> <span>Defensive Intelligence</span> <em className="synthetic">SYNTHETIC DATA</em></div>
        <select value={clipId} onChange={(e) => setClipId(e.target.value)} aria-label="Clip">
          {clips.map((c) => <option key={c.clip_id} value={c.clip_id}>Clip {parseInt(c.clip_id.split("_").pop() ?? "0", 10)} · {matchClock(c.duration_s)}</option>)}
        </select>
        {/* while a clip switch is starting its replay there is no session, so the controls wait for it instead of
            dropping the click (over a slow link that takes a noticeable moment) */}
        <button ref={playButton} disabled={!session} onClick={() => {
          setIntro(false);
          if (bd) { exitBreakdown(); setView("live"); setSelRev(null); setSurface("inspector_preview"); }
          if (!playing && autoReplay) { setView("live"); setSelRev(null); setAutoReplay(false); }
          if (!playing && clock >= range[1] - 0.1) replayFromStart();  // at the end, Play starts the clip again
          else control(playing ? "pause" : "play");
        }}>{playing ? "Pause" : "Play"}</button>
        {/* the bar stays where the viewer put it until the seek is answered: frames still streaming in would
            otherwise pull it back to the playing position */}
        <input type="range" min={t0} max={t1} step={0.2} value={shownClock} aria-label="Seek" disabled={!session}
          style={{ "--p": `${((shownClock - t0) / Math.max(t1 - t0, 1e-6)) * 100}%` } as CSSProperties}
          onChange={(e) => setScrub(parseFloat(e.target.value))}
          onPointerUp={(e) => seekTo(parseFloat((e.target as HTMLInputElement).value))}
          onPointerCancel={() => setScrub(null)}
          onKeyUp={(e) => { if (SEEK_KEYS.has(e.key)) seekTo(parseFloat((e.target as HTMLInputElement).value)); }} />
        <span className="clock" title="match clock · time into the clip"><b>{matchClock(shownClock)}</b><span>{fmtClock(shownClock, t0)} / {fmtClock(t1, t0)}{details ? ` · epoch ${epoch}` : ""}</span></span>
        <select value={myClub} onChange={(e) => setMyClub(e.target.value)} aria-label="My club">
          <option value="">Pick your club</option>
          {teams.map((tm: any) => <option key={tm.team_id} value={tm.team_id}>My club: {tm.display_name}</option>)}
        </select>
        <select value={speed} onChange={(e) => control("set_speed", parseFloat(e.target.value))} aria-label="Speed"
          disabled={!session}>
          {[0.5, 1, 2].map((v) => <option key={v} value={v}>{v}×</option>)}
        </select>
        {!details && health?.cloud_enabled && lastCloud?.last_call_succeeded &&
          <span className="src" title={statusLine}>Narration by Microsoft Foundry agents</span>}
        {details && <span className="status">{statusLine}</span>}
      </header>
      {error && <div className="banner err" onClick={() => setError(null)}>{error}</div>}
      {notice && <div className="banner" onClick={() => setNotice(null)}>{notice}</div>}

      <main className="desk">
        <section className="stage">
          <div className="toolbar">
            {!showIntro && <button className={`bd-go${bd ? " on" : ""}`} disabled={!session || bdBusy}
              onClick={() => (bd ? exitBreakdown() : startBreakdown())}>
              {bdBusy ? BD_UI[L].busy : bd ? `✕ ${BD_UI[L].stop}` : `▶ ${BD_UI[L].go}`}</button>}
            <div className="seg">
              <button className={surface === "inspector_preview" ? "on" : ""} onClick={() => setSurface("inspector_preview")}>Inspector</button>
              <button className={surface === "broadcast" ? "on" : ""} onClick={() => setSurface("broadcast")}>Broadcast</button>
            </div>
            {surface === "inspector_preview" && <div className="seg">
              <button className={view === "live" ? "on" : ""} onClick={() => { liveChosen.current = !playing || clock >= range[1] - 0.1; setView("live"); setSelRev(null); setAutoReplay(false); }}>Live</button>
              <button className={view === "replay" ? "on" : ""} onClick={() => { setView("replay"); setAutoReplay(false); }}>Replay moment</button>
            </div>}
            <div className="seg focus">
              <button className={`r${focus === "restricted" ? " on" : ""}`} onClick={() => setFocus(focus === "restricted" ? "both" : "restricted")}>What it takes away</button>
              <button className={`x${focus === "exposed" ? " on" : ""}`} onClick={() => setFocus(focus === "exposed" ? "both" : "exposed")}>What it leaves open</button>
            </div>
            {followed && <div className="seg">
              <button className={onlyFollowed ? "on" : ""} onClick={() => setOnlyFollowed(!onlyFollowed)} aria-pressed={onlyFollowed}>
                Only #{jersey(followed)}'s moments</button>
              <button className="follow-chip" onClick={() => { setFollowed(null); setOnlyFollowed(false); }} title="Stop following">
                Following {followed.split("_")[0].slice(0, 3).toUpperCase()} #{jersey(followed)} · stop</button>
            </div>}
            <div className="prefs">
              <div className="seg">
                {(["fan", "analyst"] as const).map((a) => <button key={a} className={audience === a ? "on" : ""} onClick={() => setAudience(a)}>{a === "fan" ? "Fan" : "Analyst"}</button>)}
              </div>
              <div className="seg">
                {(["en", "ko"] as const).map((l) => <button key={l} className={language === l ? "on" : ""} onClick={() => setLanguage(l)}>{l.toUpperCase()}</button>)}
              </div>
              <div className="seg">
                <button className={details ? "on" : ""} onClick={() => setDetails(!details)} aria-pressed={details}>Details</button>
              </div>
            </div>
          </div>
          <p className="pitch-legend">
            {teams.map((tm: any) => <span key={tm.team_id} className={`kit${ep?.defending_team_id === tm.team_id ? " def" : ""}`}
              style={{ "--kit": teamColor(tm.team_id) } as CSSProperties}>{tm.display_name}{tm.attack_direction ? (tm.attack_direction === 1 ? " →" : " ←") : ""}{ep?.defending_team_id === tm.team_id ? " · defending" : ""}</span>)}
            <span className="lg r" /> what the defence takes away <span className="lg x" /> what it leaves open
            {showFree && <><span className="lg free" /> unmarked (no defender within 10 m)</>}
</p>
          <div className={`pitch-wrap${bdCur ? ` bd-on bd-${bdCur.key}` : ""}`} data-focus={focus}>
            {bdCur ? <div className="badge bd-badge">{bdStep + 1} / {BD_STEPS.length} · {BD_STEPS[bdStep].title[L]}
              {` · ${matchClockTenths(bdCur.match_time_s)} · rev ${bdCur.revision}`}</div>
            : <div className={`badge ${surface === "broadcast" ? "bc" : "pv"}`}>
              {surface === "broadcast" ? "BROADCAST" : "PREVIEW"}
              {overlay && (liveExpired
                ? <> · <span className="state live">LIVE</span> · nothing current{details
                    ? ` for this episode (last analysed ${fmtClock(overlay.analyzed_through_s, t0)}; use Replay moment)`
                    : ` · last moment ${matchClock(overlay.analyzed_through_s)}`}</>
                : <> · <span className={`state${overlay.overlay.display_mode === "live" ? " live" : ""}`}>{overlay.overlay.display_mode === "live" ? "LIVE" : "REPLAY"}</span>
                    {` · ${matchClock(overlay.overlay.match_time_s)}`}{details ? ` · analysed through ${fmtClock(overlay.analyzed_through_s, t0)}` : ""}</>)}
            </div>}
            {showIntro && <div className="intro">
              <div className="intro-card">
                <h2>{BD_UI[L].intro}</h2>
                <p>{BD_UI[L].sub}</p>
                <ul className="who">
                  <li><b>1</b>{BD_UI[L].code}</li>
                  <li><b>2</b>{health?.cloud_enabled ? BD_UI[L].agents : BD_UI[L].templates}</li>
                  <li><b>3</b>{BD_UI[L].person}</li>
                </ul>
                <div className="row">
                  <button className="primary" onClick={startBreakdown} disabled={!session || bdBusy}>
                    {bdBusy ? BD_UI[L].busy : `▶ ${BD_UI[L].go}`}</button>
                  <button onClick={() => { setIntro(false); control("play"); }} disabled={!session || bdBusy}>{BD_UI[L].live}</button>
                </div>
              </div>
            </div>}
            {surface === "broadcast" && approvedRev === null
              ? <div className="empty">Nothing approved for air at this point of the replay. Approve the moment in Inspector.</div>
              : shownFrame && <GlidingPitch glideMs={1000 / (5 * speed)} glide={playing && !replayMode && !bd}
                  frame={shownFrame} overlay={overlaySnap} labels={overlay?.label_text} hiddenLayers={shownHidden}
                  ballPath={bdCur?.key === "what_happened" ? bdCur.ball_path : undefined} tags={bdTags} marks={bdCur?.marks}
                  freeIds={bdCur?.key === "leaves_open" ? bdCur.unmarked : undefined}
                  attackDir={(id) => teams.find((tm: any) => tm.team_id === id)?.attack_direction ?? 1}
                  selected={surface === "inspector_preview" ? selEl?.element_id : undefined}
                  onSelect={surface === "inspector_preview" ? setSelEl : undefined}
                  highlight={surface === "inspector_preview" ? highlight : undefined} teamColor={teamColor} jersey={jersey} followed={followed}
                  quietLabels={audience === "fan" ? FAN_QUIET : undefined} showFree={showFree}
                  onPlayer={surface === "inspector_preview" ? (pid) => setFollowed(followed === pid ? null : pid) : undefined} />}
          </div>
          {/* the caption is the approved revision's own, chosen by the server; a newer revision waits for its approval */}
          {surface === "broadcast" && approvedRev !== null && !!overlay?.on_air?.length &&
            <div className="caption">{overlay.on_air.map((t, i) => <p key={i}>{t}</p>)}</div>}
          {surface === "broadcast" && approvedRev !== null && insight && insight.insight.revision !== approvedRev &&
            <p className="air-note">On air: approved revision {approvedRev}. Revision {insight.insight.revision} is newer and waits for approval in Inspector.</p>}
          {!bd && <Timeline episodes={onlyFollowed && followed ? episodes.filter((e) => e.player_ids?.includes(followed)) : episodes}
            empty={onlyFollowed && followed ? `No moments involving #${jersey(followed)} yet` : "No moments yet"}
            myClub={myClub} t0={t0} t1={t1} clock={clock} selected={selEp} selRev={selRev}
            onSelect={(id) => { setUserPicked(true); setSelEp(id); setSelRev(null); setSelEl(null); }}
            onRevision={(id, r) => { setUserPicked(true); setSelEp(id); setSelRev(r); setView("replay"); setSurface("inspector_preview"); }} />}
          {surface === "inspector_preview" && !bd && <div className="layers">
            <label className="lt-free"><input type="checkbox" checked={showFree} onChange={() => setShowFree(!showFree)} /> Unmarked players</label>
            {layersPresent.map((l) => (
              <label key={l} className={`lt-${l}`}><input type="checkbox" checked={!hidden.has(l)} onChange={() => {
                const n = new Set(hidden); n.has(l) ? n.delete(l) : n.add(l); setHidden(n);
              }} /> {LAYER_NAMES[l]}</label>
            ))}
            {details && <span className="legend">{stylesPresent.filter((s) => STYLE_LEGEND[s]).map((s) => <span key={s}><i className={`sw ${s}`} />{STYLE_LEGEND[s]}</span>)}</span>}
          </div>}
        </section>

        <aside className="rail">
          {bd && bdPanel}
          {!bd && (ep ? <div className="card">
            <h2>Moment at {matchClock(ep.detected_at_s)} <span className={`chip ${ep.analysis_status}`}>{STATUS_TEXT[ep.analysis_status] ?? ep.analysis_status}</span>
              {myClub && ep.defending_team_id && <span className="chip club">{ep.defending_team_id === myClub
                ? (language === "ko" ? "우리 팀 수비 장면" : "Your club is defending here")
                : (language === "ko" ? "우리 팀 공격 장면" : "Your club is attacking here")}</span>}</h2>
            {details
              ? <p className="meta">{ep.episode_id} · rev {ep.revision} · {ep.lifecycle} · priority {ep.story_priority.toFixed(2)} (heuristic evidence strength)</p>
              : <p className="meta">{ep.revision} update{ep.revision === 1 ? "" : "s"} so far · {ep.lifecycle === "closed" ? "finished" : clock >= range[1] - 0.1 ? "clip ended" : "in progress"}</p>}
            {cardEp !== ep && <p className="meta asof">Showing revision {selRev}, as the analysis had it then</p>}
            <div className="cols">
              <div><h3>What is restricted</h3>{cardEp!.restricted.length ? cardEp!.restricted.map((c) => <div key={c} className={`claim r ${liveClaims.includes(c) ? "live" : ""}`}>{claimLabel(c)}</div>) : <div className="none">No supported claim</div>}</div>
              <div><h3>What is exposed</h3>{cardEp!.exposed.length ? cardEp!.exposed.map((c) => <div key={c} className={`claim x ${liveClaims.includes(c) ? "live" : ""}`}>{claimLabel(c)}</div>) : <div className="none">Nothing left open is supported yet</div>}</div>
            </div>
            <h3>Observed so far</h3>
            <div className="outcome">{cardEp!.changes.map((c) => <span key={c} className="chip ch">{c.replace(/_/g, " ")}</span>)} <span className="chip oc">{cardEp!.outcome === "not_yet_observed"
              && (cardEp!.lifecycle === "closed" || clock >= range[1] - 0.1) ? "No outcome recorded" : OUTCOME_TEXT[cardEp!.outcome]}</span></div>
            <div className="bf">
              <button className={view === "replay" && selRev === 1 ? "on" : ""}
                onClick={() => { setUserPicked(true); setSelRev(1); setView("replay"); setSurface("inspector_preview"); }}>Before (rev 1)</button>
              <button className={view === "replay" && selRev !== 1 && (selRev ?? ep.revision) === ep.revision ? "on" : ""}
                onClick={() => { setUserPicked(true); setSelRev(ep.revision); setView("replay"); setSurface("inspector_preview"); }}>After (rev {ep.revision})</button>
            </div>
            <h3>Narration{viewingPast ? ` at rev ${selRev}` : ""}</h3>
            {/* the narration's numbers belong to its revision; the live pitch labels follow the current frame */}
            {asOf != null && <p className="meta asof">Numbers as of {matchClockTenths(asOf)}</p>}
            <div className="texts" data-audience={audience}>{texts.map((t, i) => <p key={i}>{t}</p>)}</div>
            {insight && <div className="producer">
              {details
                ? <p className="meta">Insight rev {insight.insight.revision} · source {insight.insight.source_mode.replace("_", " ")} · <b className={`rs ${insight.insight.review_status}`}>{insight.insight.review_status}</b> · broadcast {insight.broadcast_visibility}</p>
                : <p className="meta">{viewingPast ? `Latest revision ${ep.revision}: written` : "Written"} by {SOURCE_TEXT[insight.insight.source_mode] ?? insight.insight.source_mode}{insight.provenance ? ` (${insight.provenance.model})` : ""} · <b className={`rs ${insight.insight.review_status}`}>{insight.insight.review_status === "suggested" ? "awaiting approval" : insight.insight.review_status}</b></p>}
              {details && insight.provenance && <p className="meta">Trace {insight.provenance.trace_id} · {insight.provenance.model} · {insight.provenance.calls} model call(s), {insight.provenance.repairs} repair · {(insight.provenance.total_ms / 1000).toFixed(1)} s</p>}
              {insight.agent_changes && <AgentChanges ch={insight.agent_changes} />}
              {insight.provenance && <details className="steps"><summary>Agent steps (Analyst → code check → Producer → code check)</summary>
                <ol>{insight.provenance.stages.map((st: any, i: number) => (
                  <li key={i} className={st.ok ? "ok" : "bad"}>{STAGE_TEXT[st.stage] ?? st.stage}{st.ms != null ? ` · ${(st.ms / 1000).toFixed(1)} s` : ""}{st.error ? ` · ${st.error}` : ""}</li>
                ))}</ol></details>}
              {/* approval is for the latest revision and its evidence: an older one on screen is read, not approved */}
              {viewingPast
                ? <p className="meta past-note">You are reading revision {selRev}. Approval is for the latest, revision {ep.revision}.{" "}
                    <button className="link" onClick={() => setSelRev(ep.revision)}>Show revision {ep.revision}</button></p>
                : <div className="row">
                    <button className="ok" onClick={() => decide("approve")} disabled={approved}>{approved ? `Revision ${ep.revision} approved` : `Approve revision ${ep.revision}`}</button>
                    <button className="no" onClick={() => decide("reject")}>Reject</button>
                    <button onClick={exportJson} disabled={approvedRev === null}
                      title={approvedRev === null ? "Export is for an approved revision" : undefined}>Export JSON</button>
                  </div>}
            </div>}
          </div> : <div className="card"><p>No moment yet. Press Play: pink marks the passes the defence takes away, cyan the space it
            leaves open.</p></div>)}
          {!bd && <Inspector el={selEl} claimById={claimById} metricById={metricById} packet={packet} t0={t0} details={details} />}
          {session && <AskPanel key={session.replay_id} session={session} audience={audience} language={language}
            episodes={episodes} t0={t0} onError={(e) => setError(e)} onAnswered={refreshHealth}
            onShow={(id) => { setUserPicked(true); setSelEp(id); setSelRev(null); setSelEl(null); setView("replay"); setSurface("inspector_preview"); }} />}
          {session && <RecapCard key={`recap-${session.replay_id}`} session={session} audience={audience}
            language={language} myClub={myClub} followed={followed} t0={t0} episodes={episodes} jersey={jersey}
            onError={(e) => setError(e)} onDone={refreshHealth}
            onShow={(id) => { setUserPicked(true); setSelEp(id); setSelRev(null); setSelEl(null); setView("replay"); setSurface("inspector_preview"); }} />}
        </aside>
      </main>
    </div>
  );
}

function BreakdownPanel({ bd, step, lang, keys, texts, insight, approvedRev, surface, latestRev, asOf, onStep, onExit,
  onApprove, moments, busy, onPick, teamName, teamColor }: {
  bd: Breakdown; step: number; lang: Lang; keys: string[]; texts: string[]; insight?: InsightView;
  approvedRev: number | null; surface: Surface; latestRev: number | null; asOf: number | null;
  onStep: (n: number) => void; onExit: () => void; onApprove: () => void;
  moments: EpisodeSummary[]; busy: boolean; onPick: (id: string) => void;
  teamName: (id: string) => string; teamColor: (id: string) => string;
}) {
  const ui = BD_UI[lang];
  const cur = step < bd.steps.length ? bd.steps[step] : null;
  const meta = BD_STEPS[step];
  const onAir = surface === "broadcast" && approvedRev !== null;
  const lines = cur ? cur.sentences : texts;
  const held = cur?.held_back?.filter((h, i, a) => HELD_TEXT[h.reason]
    && a.findIndex((x) => x.reason === h.reason && x.player_id === h.player_id) === i) ?? [];
  const np = cur?.next_pass;
  const pf = cur?.press_facts;
  const extra = [
    pf && ui.press_facts.replace("{c}", playerName(pf.carrier_id)).replace("{d}", playerName(pf.defender_id))
      .replace("{m}", pf.distance_m.toFixed(1)),
    np && ({ pass: ui.next_pass, interception: ui.next_interception, save: ui.next_save, block: ui.next_block })[np.kind]
      .replace("{t}", matchClock(np.release_s)).replace("{p}", playerName(np.from_id)).replace("{q}", playerName(np.to_id)),
    cur?.through_blocked && ui.through_blocked,
    cur?.clip_ends && ui.clip_ends,
    ...(cur?.other_runs ?? []).map((o) => (o.in_time && o.cover_id ? ui.other_run : ui.other_run_late)
      .replace("{r}", playerName(o.runner_id)).replace("{c}", o.cover_id ? playerName(o.cover_id) : "")),
    ...(() => { const more = (cur?.unmarked ?? []).filter((p) => !cur?.player_ids.includes(p));
      return more.length ? [(cur?.layers.length ? ui.unmarked : ui.unmarked_partial).replace("{p}", more.map(playerName).join(", "))] : []; })(),
  ].filter((t): t is string => !!t);
  return (
    <div className={`bd bd-${meta.key}`}>
      {moments.length > 1 && <div className="bd-moments" role="group" aria-label={ui.moments}>
        <span>{ui.moments}</span>
        {moments.map((e) => <button key={e.episode_id} className={e.episode_id === bd.episode_id ? "on" : ""}
          aria-pressed={e.episode_id === bd.episode_id} disabled={busy}
          style={{ "--kit": e.defending_team_id ? teamColor(e.defending_team_id) : "#999" } as CSSProperties}
          onClick={() => e.episode_id !== bd.episode_id && onPick(e.episode_id)}>
          {matchClock(e.detected_at_s)} · {ui.defending.replace("{team}", e.defending_team_id ? teamName(e.defending_team_id) : "?")}
        </button>)}
      </div>}
      <ol className="bd-steps">{BD_STEPS.map((s, i) => (
        <li key={s.key}><button className={`${i === step ? "on" : i < step ? "done" : ""} k-${s.key}`} onClick={() => onStep(i)}
          aria-current={i === step ? "step" : undefined}><b>{i + 1}</b> {s.title[lang]}</button></li>))}</ol>
      <div className="bd-body">
        <h3 className="bd-title">{meta.title[lang]}</h3>
        <p className="bd-q">{meta.q[lang]}</p>
        <div className="bd-text">{lines.length || extra.length ? [...lines, ...extra].map((t, i) => <p key={i}>{t}</p>)
          : <p className="meta">{ui.none}</p>}</div>
        {held.map((h, i) => <p key={i} className="bd-held">{HELD_TEXT[h.reason][lang].replace("{p}",
          h.player_id.includes("_") ? playerName(h.player_id) : "")}</p>)}
        {!cur && latestRev != null && asOf != null && <p className="meta">{ui.onairNote.replace("{rev}", String(latestRev))
          .replace("{t}", matchClockTenths(asOf))}</p>}
        {(keys.length > 0 || (cur?.ball_path?.length ?? 0) > 1 || !!cur?.marks?.length) && <ul className="bd-key">
          {cur?.key === "what_happened" && (cur.ball_path?.length ?? 0) > 1 && <li><i className="sw ball_path" />{ui.ball}</li>}
          {!!cur?.marks?.length && <li><i className="sw bd_mark" />{ui.marks}</li>}
          {keys.map((k) => <li key={k}><i className={`sw ${k}`} />{BD_KEY[k][lang]}</li>)}
        </ul>}
        {!cur && !insight && <p className="meta">{ui.waiting}</p>}
        {!cur && insight && <p className="meta">{SOURCE_TEXT[insight.insight.source_mode] ?? insight.insight.source_mode}
          {insight.provenance ? ` (${insight.provenance.model})` : ""} · {onAir ? ui.onair : insight.insight.review_status === "approved"
            ? "approved" : "awaiting approval"}</p>}
      </div>
      <div className="row">
        <button onClick={() => onStep(step - 1)} disabled={step === 0}>◀ {ui.back}</button>
        {cur ? <button className="primary" onClick={() => onStep(step + 1)}>{ui.next} ▶</button>
          : <button className="ok" onClick={onApprove} disabled={!insight || onAir}>{onAir ? `✓ ${ui.onair}` : ui.approve}</button>}
        <button onClick={onExit}>{cur ? ui.exit : ui.explore}</button>
      </div>
    </div>
  );
}

function Timeline({ episodes, empty, myClub, t0, t1, clock, selected, selRev, onSelect, onRevision }: {
  episodes: EpisodeSummary[]; empty: string; myClub: string; t0: number; t1: number; clock: number; selected: string | null; selRev: number | null;
  onSelect: (id: string) => void; onRevision: (id: string, rev: number) => void;
}) {
  const x = (t: number) => `${((t - t0) / Math.max(t1 - t0, 1e-6)) * 100}%`;
  // overlapping episodes go on separate rows, so every one stays visible and clickable (bars have a minimum width,
  // hence the small gap in time)
  const gap = 0.06 * Math.max(t1 - t0, 1e-6);
  const row: Record<string, number> = {};
  const rowEnds: number[] = [];
  for (const e of [...episodes].sort((a, b) => a.detected_at_s - b.detected_at_s)) {
    let r = rowEnds.findIndex((end) => end + gap <= e.detected_at_s);
    if (r < 0) { r = rowEnds.length; rowEnds.push(0); }
    rowEnds[r] = e.analyzed_until_s;
    row[e.episode_id] = r;
  }
  return (
    <div className="timeline" aria-label="Episode timeline" style={{ height: 34 + Math.max(rowEnds.length, 1) * 30 }}>
      <div className="axis">{[0, 0.25, 0.5, 0.75, 1].map((f) => <span key={f} style={{ left: `${f * 100}%` }}>{matchClock(t0 + (t1 - t0) * f)}</span>)}</div>
      {!episodes.length && <span className="tl-empty">{empty}</span>}
      {episodes.map((e) => (
        <div key={e.episode_id} className={`ep ${e.analysis_status} ${selected === e.episode_id ? "sel" : ""}${
            myClub && e.defending_team_id ? (e.defending_team_id === myClub ? " mine-def" : " mine-att") : ""}`}
          style={{ left: x(e.detected_at_s), top: 8 + row[e.episode_id] * 30,
                   width: `calc(${x(e.analyzed_until_s)} - ${x(e.detected_at_s)} + 6px)` }}
          onClick={() => onSelect(e.episode_id)} title={`${e.episode_id}: ${e.analysis_status}`}>
          {Array.from({ length: e.revision }, (_, i) => i + 1).map((r) => (
            <button key={r} className={`rev ${selRev === r && selected === e.episode_id ? "on" : ""}`}
              onClick={(ev) => { ev.stopPropagation(); onRevision(e.episode_id, r); }}>{r}</button>
          ))}
        </div>
      ))}
      <div className="now" style={{ left: x(clock) }} />
    </div>
  );
}

// plain names for the measured values; neutral where a sign convention would need explaining (margins)
const METRIC_TEXT: Record<string, string> = {
  attacker_arrival_s: "Attacker arrival", attacker_drop_m: "Attacker dropped deeper", attacker_move_m: "Attacker moved",
  back_line_depth_m: "Back line from its own goal", back_line_spread_m: "Back line width", back_line_step_m: "Back line step",
  behind_depth_change_m: "Change in the line's depth", buildup_count_attack: "Attackers around the ball",
  buildup_count_defense: "Defenders around the ball", carrier_nearest_defender_m: "Nearest defender to the ball carrier", carrier_closing_mps: "Fastest defender running at the carrier",
  central_candidates: "Central passing routes", central_count_attack: "Attackers in the central zone",
  central_count_defense: "Defenders in the central zone", central_screened: "Central routes screened",
  cover_arrival_s: "Cover arrival", cover_margin_s: "Cover margin", defender_step_m: "Defender step",
  front_back_span_m: "Team length, front to back", gk_cover_margin_s: "Goalkeeper cover margin",
  lane_arrival_margin_s: "Lane arrival margin", marking_follow_support: "Marking follow strength",
  mid_back_gap_m: "Gap, midfield to back line", mid_line_shift_m: "Midfield line shift", nearest_defender_m: "Nearest defender",
  press_cut_lanes: "Lanes the presser cuts", presser_closing_speed_mps: "Presser closing speed",
  presser_distance_m: "Presser to ball carrier", receiver_margin_s: "Receiver margin",
  rest_count_attack: "Attackers further up", rest_count_defense: "Defenders further up",
  runner_forward_speed_mps: "Runner speed towards goal", short_option_candidates: "Short passing options",
  short_options_open: "Short options open", short_options_screened: "Short options screened",
  supported_marking_edges: "Sustained marking pairs", wide_open_outlets: "Open wide outlets",
  zone_marking_coverage: "Attackers with a sustained marker", zone_opponents: "Attackers in the zone",
};
// What the narration agents changed, next to the rule-based plan for the same evidence. Computed by the server in
// code from the ids the agents chose (claims led with, left out or added, caveats, sentences kept, dropped, moved).
function AgentChanges({ ch }: { ch: NonNullable<InsightView["agent_changes"]> }) {
  const claim = (c: string | null) => (c ? CLAIM_TEXT[c] ?? c.replace(/_/g, " ") : "none");
  const counts = (a: "fan" | "analyst") => {
    const v = ch.sentences[a];
    return v ? `${a === "fan" ? "Fan" : "Analyst"} kept ${v.kept}, dropped ${v.dropped}, added ${v.added}, moved ${v.moved}` : "";
  };
  const caveats = [...ch.caveats_added.map((c) => `+ ${c.replace(/_/g, " ")}`),
                   ...ch.caveats_dropped.map((c) => `− ${c.replace(/_/g, " ")}`)];
  return <div className="agent-diff">
    <p className="meta"><b>What the agents changed</b> · against the rule-based plan for the same evidence</p>
    {ch.same_as_rules ? <p>Nothing: the agents made the same choices as the rules.</p> : <ul>
      <li>Led with {claim(ch.lead.agents)}{ch.lead.agents === ch.lead.rules ? " (as the rules do)" : ` (rules: ${claim(ch.lead.rules)})`}</li>
      {ch.left_out.length > 0 && <li>Left out {ch.left_out.map(claim).join(", ")}</li>}
      {ch.added.length > 0 && <li>Added {ch.added.map(claim).join(", ")}</li>}
      {caveats.length > 0 && <li>Caveats {caveats.join(", ")}</li>}
      <li>Sentences: {counts("fan")} · {counts("analyst")}</li>
    </ul>}
  </div>;
}

function metricValue(m: any): string {
  if (m.value === null) return "n/a";
  const v = m.value as number;
  switch (m.unit) {
    case "m": return `${v.toFixed(1)} m`;
    case "s": return `${v.toFixed(1)} s`;
    case "mps": return `${v.toFixed(1)} m/s`;
    case "count": return v.toFixed(0);
    case "ratio": return `${Math.round(v * 100)}%`;
    default: return v.toFixed(2);
  }
}
const playerName = (pid: string) => `${pid.split("_")[0].slice(0, 3).toUpperCase()} #${parseInt(pid.split("_").pop() ?? "0", 10)}`;

function Inspector({ el, claimById, metricById, packet, t0, details }: {
  el: OverlayElement | null; claimById: Record<string, any>; metricById: Record<string, any>;
  packet: EvidencePacket | null; t0: number; details: boolean;
}) {
  if (!packet) return null;
  if (!el) return (
    <div className="card insp"><h2>Evidence</h2>
      <p className="meta">Click a line, area or label on the pitch.</p>
      <p className="meta">{details ? `Packet ${packet.evidence_hash.slice(0, 19)}… · ` : ""}{packet.claims.length} claim{packet.claims.length === 1 ? "" : "s"} · {packet.metrics.length} measurement{packet.metrics.length === 1 ? "" : "s"} · analysed up to {matchClock(packet.episode.analyzed_until_s)}</p>
      {packet.forbidden_inferences.filter((f) => f.includes(":")).map((f) => <p key={f} className="supp">Not claimed: {f.split(":").slice(0, 2).join(" — ").replace(/_/g, " ")}</p>)}
    </div>
  );
  const mids = new Set<string>([...el.metric_ids, ...el.claim_ids.flatMap((c) => claimById[c]?.metric_ids ?? [])]);
  return (
    <div className="card insp">
      <h2>Evidence · {LAYER_NAMES[el.layer] ?? el.layer}</h2>
      {details && <p className="meta">{el.element_id} · {el.style_token} · relations {el.relation_ids.join(", ") || "—"}</p>}
      {el.claim_ids.map((c) => claimById[c] && (
        <div key={c} className="ev">
          <b>{CLAIM_TEXT[claimById[c].code]}</b> <span className="meta">{claimById[c].dimension} · {claimById[c].epistemic.replace(/_/g, " ")} · strength {claimById[c].support_score.toFixed(2)} · {(claimById[c].window.start_s - t0).toFixed(1)}–{(claimById[c].window.end_s - t0).toFixed(1)}s</span>
          <div className="meta">players {claimById[c].player_ids.map((pid: string) => details ? pid : playerName(pid)).join(", ")}</div>
        </div>
      ))}
      <table><tbody>{[...mids].map((m) => metricById[m]).filter(Boolean).map((m: any) => (
        <tr key={m.metric_id}><td title={m.key}>{METRIC_TEXT[m.key] ?? m.key.replace(/_/g, " ")}{details ? <span className="meta"> · {m.key}</span> : null}</td>
          <td><b>{metricValue(m)}</b></td>
          {details && <td className="meta">{m.status} · frame {m.frame_refs.map((f: any) => f.frame_index).join(",")} · {m.method_version}</td>}</tr>
      ))}</tbody></table>
    </div>
  );
}

function AskPanel({ session, audience, language, episodes, t0, onShow, onError, onAnswered }: {
  session: Session; audience: "fan" | "analyst"; language: "en" | "ko"; episodes: EpisodeSummary[]; t0: number;
  onShow: (episodeId: string) => void; onError: (e: string) => void; onAnswered: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [since, setSince] = useState(0);
  const [now, setNow] = useState(0);
  const [asks, setAsks] = useState<AskResponse[]>([]);
  useEffect(() => {
    if (!pending) return;
    const id = setInterval(() => setNow(performance.now()), 200);
    return () => clearInterval(id);
  }, [pending]);
  const send = async (q: string) => {
    const text = q.trim();
    if (!text || pending) return;
    setPending(text); setSince(performance.now()); setNow(performance.now());
    try {
      const r = await api.ask(session, text, audience, language);
      setAsks((prev) => [r, ...prev].slice(0, 5));
      setQuestion("");
      onAnswered();
    } catch (e) {
      onError(e instanceof ApiError && e.status === 429 ? "One question at a time." : String(e));
    } finally { setPending(null); }
  };
  const momentAt = (id: string | undefined) => {
    const e = episodes.find((x) => x.episode_id === id);
    return e ? `moment at ${matchClock(e.detected_at_s)}` : id ?? "";
  };
  return (
    <div className="card ask">
      <h2>Ask PitchIQ</h2>
      <p className="meta">Knows only the play so far · every sentence checked against the evidence</p>
      <form className="ask-row" onSubmit={(e) => { e.preventDefault(); send(question); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={280} aria-label="Question"
          placeholder={language === "ko" ? "질문을 입력하세요" : "Type a question"} disabled={!!pending} />
        <button type="submit" disabled={!!pending || !question.trim()}>Ask</button>
      </form>
      <div className="sugg">{SUGGESTIONS[language].map((q) => <button key={q} onClick={() => send(q)} disabled={!!pending}>{q}</button>)}</div>
      {pending && <p className="meta working">Ask agent is working on “{pending}” · {((now - since) / 1000).toFixed(1)} s</p>}
      {asks.map((a, i) => (
        <div key={a.ask_id} className={`answer ${i ? "old" : ""}`}>
          <p className="q">“{a.question}”</p>
          <p className="meta">As of {matchClock(t0 + a.answer.analyzed_through_s)}{a.answer.kind === "moment" && a.answer.detected_at_s != null ? ` · about the moment at ${matchClock(t0 + a.answer.detected_at_s)}` : " · declined"}</p>
          <div className="texts">{a.answer.sentences[language].map((t, k) => <p key={k}>{t}</p>)}</div>
          <p className="meta">{a.source_mode === "foundry_live"
            ? <>Answered by the Ask agent · {a.trace.model} · {a.trace.runs} run(s), {a.trace.tool_calls} tool call(s){a.trace.repairs ? `, ${a.trace.repairs} repair` : ""} · {(a.trace.total_ms / 1000).toFixed(1)} s{tokenText(a.trace.usage)}</>
            : <span title={a.trace.reason ?? undefined}>Deterministic fallback (main moment)</span>}</p>
          <div className="row">
            {a.answer.episode_id && <button onClick={() => onShow(a.answer.episode_id!)}
              disabled={!episodes.some((e) => e.episode_id === a.answer.episode_id)}>Show on pitch</button>}
          </div>
          {a.trace.steps.length > 0 && <details className="steps" open={i === 0}><summary>How the agent answered</summary>
            <ol>{a.trace.steps.map((st, k) => <li key={k} className={st.ok === false ? "bad" : st.step === "tool" ? "tool" : "ok"}>{stepText(st, momentAt)}</li>)}</ol>
          </details>}
        </div>
      ))}
    </div>
  );
}

function tokenText(usage: Record<string, number>): string {
  const inp = usage.input_token_count, out = usage.output_token_count;
  return inp != null || out != null ? ` · tokens ${inp ?? "?"} in / ${out ?? "?"} out` : "";
}

function stepText(st: AskStep, momentAt: (id: string | undefined) => string): string {
  const ms = st.ms != null ? (st.ms >= 1000 ? ` · ${(st.ms / 1000).toFixed(1)} s` : ` · ${st.ms.toFixed(0)} ms`) : "";
  if (st.step === "agent" || st.step === "agent_repair") {
    const what = st.step === "agent" ? "Ask agent run" : "Repair run (told why)";
    return `${what}${ms}${st.error ? ` · ${st.error}` : ""}`;
  }
  if (st.step === "tool") return `${st.name}(${momentAt(st.args?.episode_id)}) · ${st.summary ?? ""}${ms}`;
  return st.ok ? "Code check passed: every sentence and number comes from the evidence" : `Code check refused: ${st.error}`;
}

const RECAP_STEP_TEXT: Record<string, string> = {
  gather: "Gather the moments found so far (code)", recap_agent: "Recap agent run", check: "Code check of the choice",
  render: "Render the chosen validated sentences", fallback: "Deterministic recap (fallback)",
};

function RecapCard({ session, audience, language, myClub, followed, t0, episodes, jersey, onShow, onError, onDone }: {
  session: Session; audience: "fan" | "analyst"; language: "en" | "ko"; myClub: string; followed: string | null;
  t0: number; episodes: EpisodeSummary[]; jersey: (pid: string) => string; onShow: (episodeId: string) => void;
  onError: (e: string) => void; onDone: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [res, setRes] = useState<RecapResponse | null>(null);
  const run = async () => {
    setPending(true);
    try {
      setRes(await api.recap(session, { audience, language, my_club: myClub || null, follow_player: followed }));
      onDone();
    } catch (e) {
      onError(e instanceof ApiError && e.status === 429 ? "One question or recap at a time." : String(e));
    } finally { setPending(false); }
  };
  const r = res?.recap;
  const who = followed ? ` · following #${jersey(followed)}` : "";
  return (
    <div className="card recap">
      <h2>Recap so far</h2>
      <p className="meta">Up to three moments for you{myClub ? " and your club" : ""}{who} · Microsoft Agent Framework · checked line by line</p>
      <div className="row"><button onClick={run} disabled={pending}>{pending ? "Building the recap…" : res ? "Update the recap" : "Recap so far"}</button></div>
      {r && <>
        <p className="meta">As of {matchClock(t0 + r.analyzed_through_s)} · {res!.source_mode === "foundry_live"
          ? `built by the Recap agent · ${res!.trace.model} · ${(res!.trace.total_ms / 1000).toFixed(1)} s${tokenText(res!.trace.usage)}`
          : <span title={res!.trace.reason ?? undefined}>deterministic recap</span>}</p>
        {r.kind === "empty" && <div className="texts">{r.note_sentences[language].map((t, k) => <p key={k}>{t}</p>)}</div>}
        {r.items.map((it) => (
          <div key={it.episode_id} className="recap-item">
            <p className="meta">Moment at {matchClock(t0 + it.detected_at_s)}
              {it.my_club_role && <span className="chip club">{it.my_club_role === "defending"
                ? (language === "ko" ? "우리 팀 수비" : "your club defending") : (language === "ko" ? "우리 팀 공격" : "your club attacking")}</span>}
              {it.involves_followed && followed && <span className="chip">#{jersey(followed)} involved</span>}
              <button className="link" onClick={() => onShow(it.episode_id)}
                disabled={!episodes.some((e) => e.episode_id === it.episode_id)}>Show on pitch</button></p>
            <div className="texts">{it.sentences[language].map((t, k) => <p key={k}>{t}</p>)}</div>
          </div>
        ))}
        {res!.trace.steps.length > 0 && <details className="steps"><summary>How the recap was built</summary>
          <ol>{res!.trace.steps.map((st, k) => (
            <li key={k} className={st.ok === false ? "bad" : st.step === "tool" ? "tool" : "ok"}>
              {st.step === "tool" ? `${st.name}(moment ${st.args?.episode_id?.slice(-5) ?? ""}) · ${st.summary ?? ""}`
                : `${RECAP_STEP_TEXT[st.step] ?? st.step}${st.error ? ` · ${st.error}` : ""}`}
              {st.ms != null ? ` · ${st.ms >= 1000 ? `${(st.ms / 1000).toFixed(1)} s` : `${st.ms.toFixed(0)} ms`}` : ""}
            </li>))}</ol></details>}
      </>}
    </div>
  );
}
