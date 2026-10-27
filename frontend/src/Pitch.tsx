import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { Anchor, ObservationFrame, OverlayElement, OverlaySnapshot, XY } from "./contracts";

const sx = (p: XY) => p.x_m;
const sy = (p: XY) => 68 - p.y_m;

type Props = {
  frame: ObservationFrame;
  overlay?: OverlaySnapshot | null;
  labels?: Record<string, string>;
  hiddenLayers: Set<string>;
  selected?: string | null;
  onSelect?: (el: OverlayElement) => void;
  highlight?: Set<string>;
  teamColor: (teamId: string) => string;
  jersey: (playerId: string) => string;
  followed?: string | null;  // the player the viewer follows: ring and current speed
  showFree?: boolean;  // ring the team on the ball's free players (FREE_M)
  freeIds?: string[];  // the unmarked players the server measured for this frame (a breakdown step), instead of FREE_M
  attackDir?: (teamId: string) => number;  // +1 attacks toward x = 105 (a player in an offside position is not ringed)
  onPlayer?: (playerId: string) => void;
  quietLabels?: Set<string>;  // label layers not drawn for this audience (their lines and areas stay)
  ballPath?: number[][];  // where the ball went (world metres), drawn as a white arrow
  tags?: Record<string, { text: string; role: string }>;  // a tag over the players a step is about (presser, runner...)
  marks?: string[][];  // [defender, attacker]: who follows whom, drawn as a white tether
};
// An unmarked player: an outfield player of the team on the ball with real room, no opponent within FREE_M, and no
// defender following him (a marking pair the analysis drew). Measured on the frame drawn, with hysteresis: a player
// already ringed keeps it until an opponent comes within FREE_KEEP_M, so a ring does not flicker on a defender at the
// edge. The keeper, the man on the ball and anyone in an offside position are left out, and only FREE_MAX of them (within FREE_REACH_M of the ball:
// a switch to the far side counts) are ringed, the most room first. Whether a pass to him is on is the analysis'
// call (lanes), not this ring's.
const FREE_M = 10.0;
const FREE_KEEP_M = 8.5;
const FREE_REACH_M = 60.0;
const FREE_MAX = 3;
// tags of the player who acts go first, so they get the spot above the player
const TAG_ORDER = ["presser", "runner", "passer", "shooter", "carrier", "cover", "receiver", "interceptor", "saver", "blocker"];
const GETS_BALL = new Set(["receiver", "interceptor", "saver", "blocker"]);  // tagged where the ball's path ends

// a run at or above the defender speed the analysis assumes (analytics_config.yaml: defender_speed_mps) is a sprint
const SPRINT_MPS = 7.0;

function resolve(a: Anchor, pos: Record<string, XY>): XY | null {
  return a.kind === "player" ? pos[a.player_id] ?? null : a.point;
}

// ---- label placement: no label on another label, off the pitch, or on a player or the ball when avoidable
const LABEL_H = 2.8;
const PLAYER_R = 1.25;
// box width from the text: Hangul is about twice as wide as Latin at this size
const labelWidth = (s: string) =>
  Math.max(5, 1.5 * ([...s].reduce((w, c) => w + (/[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]/.test(c) ? 0.95 : 0.5), 0) + 1.2));
type Placed = { x: number; y: number; leader: [number, number] | null };

function placeLabels(items: { id: string; at: [number, number]; w: number }[], obstacles: [number, number, number][]) {
  const placed: Record<string, Placed> = {};
  const boxes: [number, number, number, number][] = [];  // x0, y0, x1, y1 in SVG units
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
  for (const it of items) {
    const [ax, ay] = it.at;
    const cands: [number, number][] = [[0, -3.2], [0, 3.2]];
    for (const d of [6.2, 9.2, 12.2]) cands.push([0, -d], [0, d]);
    cands.push([-(it.w / 2 + 2), 0], [it.w / 2 + 2, 0]);
    let best = { cost: Infinity, x: ax, y: ay };
    for (const [i, [dx, dy]] of cands.entries()) {
      const x = clamp(ax + dx, -2.8 + it.w / 2, 107.8 - it.w / 2);
      const y = clamp(ay + dy, -2.8 + LABEL_H / 2, 70.8 - LABEL_H / 2);
      const b: [number, number, number, number] = [x - it.w / 2, y - LABEL_H / 2, x + it.w / 2, y + LABEL_H / 2];
      let cost = i * 0.5 + Math.abs(x - ax - dx) * 0.2;  // prefer the first spots and little clamping
      for (const o of boxes) if (b[0] < o[2] + 0.3 && o[0] < b[2] + 0.3 && b[1] < o[3] + 0.3 && o[1] < b[3] + 0.3) cost += 1000;
      for (const [cx, cy, r] of obstacles) {
        const nx = clamp(cx, b[0], b[2]), ny = clamp(cy, b[1], b[3]);
        if ((nx - cx) ** 2 + (ny - cy) ** 2 < r * r) cost += 20;
      }
      if (cost < best.cost) best = { cost, x, y };
    }
    const { x, y } = best;
    boxes.push([x - it.w / 2, y - LABEL_H / 2, x + it.w / 2, y + LABEL_H / 2]);
    const far = Math.abs(x - ax) > it.w / 2 || Math.abs(y - ay) > 3.3;
    placed[it.id] = { x, y, leader: far ? [clamp(ax, x - it.w / 2, x + it.w / 2), clamp(ay, y - LABEL_H / 2, y + LABEL_H / 2)] : null };
  }
  return placed;
}

function PitchLines() {
  const L = { fill: "none", stroke: "var(--line)", strokeWidth: 0.24 } as const;
  const DOT = { fill: "var(--line)" } as const;
  return (
    <g>
      <rect x={0} y={0} width={105} height={68} className="field" />
      <rect x={0} y={0} width={105} height={68} {...L} />
      <line x1={52.5} y1={0} x2={52.5} y2={68} {...L} />
      <circle cx={52.5} cy={34} r={9.15} {...L} />
      <circle cx={52.5} cy={34} r={0.35} {...DOT} />
      {[[0, 1], [105, -1]].map(([x0, s]) => (
        <g key={x0}>
          <path d={`M${x0} 13.84 H${x0 + s * 16.5} V54.16 H${x0}`} {...L} />
          <path d={`M${x0} 24.84 H${x0 + s * 5.5} V43.16 H${x0}`} {...L} />
          <path d={`M${x0} 30.34 H${x0 - s * 1.5} V37.66 H${x0}`} {...L} />
          <circle cx={x0 + s * 11} cy={34} r={0.3} {...DOT} />
          {/* the D: 9.15 m from the penalty spot, outside the box */}
          <path d={`M${x0 + s * 16.5} 26.69 A9.15 9.15 0 0 ${s > 0 ? 1 : 0} ${x0 + s * 16.5} 41.31`} {...L} />
        </g>
      ))}
      {["M0 1A1 1 0 0 0 1 0", "M104 0A1 1 0 0 0 105 1", "M105 67A1 1 0 0 0 104 68", "M1 68A1 1 0 0 0 0 67"].map((d) => <path key={d} d={d} {...L} />)}
    </g>
  );
}

function Element({ el, pos, label, place, selected, onSelect }: {
  el: OverlayElement; pos: Record<string, XY>; label?: string; place?: Placed; selected: boolean;
  onSelect?: (e: OverlayElement) => void;
}) {
  const g = el.geometry;
  const cls = `ov ${el.style_token} layer-${el.layer}${selected ? " sel" : ""}`;
  const click = onSelect ? () => onSelect(el) : undefined;
  if (g.type === "ring") {
    const c = resolve(g.center, pos);
    if (!c) return null;
    return <circle className={cls} cx={sx(c)} cy={sy(c)} r={g.radius_m} onClick={click} />;
  }
  if (g.type === "connection" || g.type === "arrow") {
    const a = resolve(g.from, pos), b = resolve(g.to, pos);
    if (!a || !b) return null;
    // a screened lane ends in a stop bar before the receiver: blocked, not "pass on"
    const marker = g.type === "arrow" ? `url(#arrow-${el.style_token})` : el.style_token === "lane_screened" ? "url(#bar)" : undefined;
    // a lane the presser cuts while he presses: drawn faint, crossed out where his run shuts it
    const cut = el.style_token === "press_cut_lane";
    // the X sits past the middle of the lane, clear of the labels written next to the carrier
    const cx = sx(a) + 0.6 * (sx(b) - sx(a)), cy = sy(a) + 0.6 * (sy(b) - sy(a));
    return (
      <g onClick={click}>
        <line className="hit" x1={sx(a)} y1={sy(a)} x2={sx(b)} y2={sy(b)} />
        <line className={cls + (g.type === "arrow" && g.meaning === "inferred" ? " inferred" : "")}
          x1={sx(a)} y1={sy(a)} x2={sx(b)} y2={sy(b)} markerEnd={marker} />
        {cut && <path className="lane-x" d={`M${cx - 1.3} ${cy - 1.3}L${cx + 1.3} ${cy + 1.3}M${cx - 1.3} ${cy + 1.3}L${cx + 1.3} ${cy - 1.3}`} />}
      </g>
    );
  }
  if (g.type === "polygon") {
    return <polygon className={cls} points={g.points.map((p) => `${sx(p)},${sy(p)}`).join(" ")} onClick={click} />;
  }
  if (g.type === "polyline") {
    return <polyline className={cls} points={g.points.map((p) => `${sx(p)},${sy(p)}`).join(" ")} onClick={click} />;
  }
  if (g.type === "label") {
    const p = resolve(g.at, pos);
    if (!p || !label || !place) return null;
    const w = labelWidth(label);
    return (
      <g className={cls} onClick={click}>
        <g transform={`translate(${place.x} ${place.y})`}>
          <rect x={-w / 2} y={-LABEL_H / 2} width={w} height={LABEL_H} rx={0.2} className="label-bg" />
          <rect x={-w / 2} y={-LABEL_H / 2} width={0.45} height={LABEL_H} className="label-tab" />
          <text textAnchor="middle" dominantBaseline="central" className="label-tx">{label}</text>
        </g>
      </g>
    );
  }
  return null;
}

export function Pitch({ frame, overlay, labels = {}, hiddenLayers, selected, onSelect, highlight, teamColor, jersey, followed, onPlayer,
  quietLabels, ballPath, tags, marks, showFree, freeIds, attackDir }: Props) {
  const pos: Record<string, XY> = {};
  for (const p of frame.players) pos[p.player_id] = p.position;
  const els = (overlay?.elements ?? []).filter((e) => !hiddenLayers.has(e.layer));
  const order = ["exposure", "structure", "numbers", "screened_lanes", "outlets", "marking", "cover", "pressure", "changes", "caption"];
  els.sort((a, b) => order.indexOf(a.layer) - order.indexOf(b.layer));
  const under = els.filter((e) => e.geometry.type !== "label");
  const over = els.filter((e) => e.geometry.type === "label" && !quietLabels?.has(e.layer));
  const ball = frame.ball.position;
  // the player on the ball, then highlighted players, are drawn last so they are never hidden by a team-mate or rival
  const d2 = (p: XY) => (p.x_m - ball.x_m) ** 2 + (p.y_m - ball.y_m) ** 2;
  const onBall = frame.players.reduce((a, b) => (d2(b.position) < d2(a.position) ? b : a), frame.players[0]);
  const rank = (id: string) => (id === onBall?.player_id ? 2 : highlight?.has(id) || id === followed ? 1 : 0);
  const attacking = frame.possession_team_id;
  const dist = (a: XY, b: XY) => Math.hypot(a.x_m - b.x_m, a.y_m - b.y_m);
  // attackers a defender follows: the breakdown's tethers and the overlay's marking lines
  const followedIds = new Set([...(marks ?? []).map(([, a]) => a), ...els.flatMap((e) =>
    e.layer === "marking" && e.geometry.type === "connection" && e.geometry.to.kind === "player" ? [e.geometry.to.player_id] : [])]);
  const prevFree = useRef<{ t: number; ids: Set<string> }>({ t: -1e9, ids: new Set() });
  const recent = Math.abs(frame.match_time_s - prevFree.current.t) <= 0.6;
  const fwd = (p: XY) => ((attacking && attackDir ? attackDir(attacking) : 1) === 1 ? p.x_m : 105 - p.x_m);
  // the offside line: the second-last defender (keeper included), or the ball if it is further forward
  const offLine = Math.max(fwd(ball), [...frame.players.filter((q) => q.team_id !== attacking).map((q) => fwd(q.position))]
    .sort((a, b) => b - a)[1] ?? -Infinity);
  const room = (p: ObservationFrame["players"][number]) =>
    Math.min(...frame.players.filter((q) => q.team_id !== attacking).map((q) => dist(q.position, p.position)));
  const free = new Set(!showFree || !attacking ? [] : freeIds ? freeIds : frame.players.filter((p) => p.team_id === attacking
    && !p.is_goalkeeper && p.player_id !== frame.ball.owner_id && dist(p.position, ball) <= FREE_REACH_M
    && !followedIds.has(p.player_id) && fwd(p.position) <= offLine + 0.5
    && room(p) >= (recent && prevFree.current.ids.has(p.player_id) ? FREE_KEEP_M : FREE_M))
    .sort((a, b) => room(b) - room(a)).slice(0, FREE_MAX).map((p) => p.player_id));
  prevFree.current = { t: frame.match_time_s, ids: free };
  const players = [...frame.players].sort((a, b) => rank(a.player_id) - rank(b.player_id));
  // player tags first: above the player, else below, else further out, so two tags never sit on each other; the
  // overlay labels then keep clear of them
  const tagBoxes: [number, number, number, number][] = [];
  const receiveAt = ballPath && ballPath.length > 1 ? ballPath[ballPath.length - 1] : null;
  const receiver = receiveAt && tags ? frame.players.find((p) => GETS_BALL.has(tags[p.player_id]?.role)) : undefined;
  const tagged = !tags ? [] : frame.players.filter((p) => tags[p.player_id])
    .sort((a, b) => TAG_ORDER.indexOf(tags[a.player_id].role) - TAG_ORDER.indexOf(tags[b.player_id].role)).map((p) => {
      const t = tags[p.player_id], w = labelWidth(t.text) * 0.85;
      // the receiver (or interceptor) of the ball's path is tagged where he gets it (the end of the path), not where
      // he stands now
      const at = GETS_BALL.has(t.role) && receiveAt ? { x_m: receiveAt[0], y_m: receiveAt[1] } : p.position;
      const x = sx(at), y0 = sy(at);
      // above or below its player, clear of the other tags and of the other players' discs (a presser 3 m off the man
      // on the ball would otherwise have his tag on that man)
      const clear = (y: number) => !tagBoxes.some((o) => x - w / 2 < o[2] && o[0] < x + w / 2 && y - 1.25 < o[3]
        && o[1] < y + 1.25) && !frame.players.some((q) => q.player_id !== p.player_id
        && Math.abs(sx(q.position) - x) < w / 2 + PLAYER_R && Math.abs(sy(q.position) - y) < 1.25 + PLAYER_R);
      const y = [-3.2, 3.2, -5.9, 5.9].map((dy) => y0 + dy).find(clear) ?? y0 - 3.2;
      tagBoxes.push([x - w / 2, y - 1.25, x + w / 2, y + 1.25]);
      return { id: p.player_id, t, w, x, y, ax: x, ay: y0 };
    });
  const obstacles: [number, number, number][] = [...frame.players.map((p): [number, number, number] =>
    [sx(p.position), sy(p.position), PLAYER_R]), [sx(ball), sy(ball), 0.8],
    ...tagged.flatMap(({ x, y, w }) => [-1, 0, 1].map((k): [number, number, number] => [x + (k * w) / 3, y, 1.4]))];
  const places = placeLabels((overlay?.elements ?? []).filter((e) => over.includes(e)).flatMap((e) => {
    const g = e.geometry, text = labels[e.element_id];
    const p = g.type === "label" ? resolve(g.at, pos) : null;
    return p && text ? [{ id: e.element_id, at: [sx(p), sy(p)] as [number, number], w: labelWidth(text) }] : [];
  }), obstacles);
  return (
    <svg className="pitch" viewBox="-3 -3 111 74" role="img" aria-label="Pitch with 22 players, ball and overlay">
      <defs>
        <pattern id="hatch" width="2.4" height="2.4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="2.4" height="2.4" className="hatch-bg" />
          <line x1="0" y1="0" x2="0" y2="2.4" className="hatch-line" />
        </pattern>
        <marker id="ghost-start" viewBox="0 0 4 4" refX="2" refY="2" markerWidth="1.3" markerHeight="1.3" markerUnits="userSpaceOnUse">
          <circle cx="2" cy="2" r="1.4" className="ghost-dot" />
        </marker>
        <marker id="bar" viewBox="0 0 10 10" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" refX="2.2" refY="5" orient="auto">
          <path d="M0 3.8V6.2" className="lane-bar" />
        </marker>
        {["press_arrow", "cover_link", "lane_open", "ball_path"].map((t) => (
          <marker key={t} id={`arrow-${t}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="3" markerHeight="3" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10z" className={`arrowhead ${t}`} />
          </marker>
        ))}
      </defs>
      <rect x={-3} y={-3} width={111} height={74} className="turf" />
      <PitchLines />
      {under.map((e) => <Element key={e.element_id} el={e} pos={pos} selected={selected === e.element_id} onSelect={onSelect} />)}
      {marks?.map(([d, a]) => {
        const p = pos[d], q = pos[a];
        return p && q ? <line key={`mk-${d}-${a}`} className="bd-mark" x1={sx(p)} y1={sy(p)} x2={sx(q)} y2={sy(q)} /> : null;
      })}
      {receiver && receiveAt && <g className="receive-g">
        <line className="receiver-run" x1={sx(receiver.position)} y1={sy(receiver.position)} x2={receiveAt[0]} y2={68 - receiveAt[1]} />
        <circle className="receiver-ghost" cx={receiveAt[0]} cy={68 - receiveAt[1]} r={1.25} />
      </g>}
      {ballPath && ballPath.length > 1 && <g className="ball-path-g">
        <polyline points={ballPath.map(([x, y]) => `${x},${68 - y}`).join(" ")} className="ball-path-shadow" />
        <polyline points={ballPath.map(([x, y]) => `${x},${68 - y}`).join(" ")} className="ball-path" markerEnd="url(#arrow-ball_path)" />
        <circle cx={ballPath[0][0]} cy={68 - ballPath[0][1]} r={0.7} className="ball-path-start" />
      </g>}
      {/* each tag points at its player (under the discs), so tags on two players close together never read swapped */}
      {tagged.map(({ id, x, y, ax, ay }) => <line key={`tl-${id}`} className="pl-tag-lead" x1={ax} y1={ay} x2={x}
        y2={y + (y < ay ? 1.25 : -1.25)} />)}
      {players.map((p) => {
        const speed = Math.hypot(p.velocity.vx_mps, p.velocity.vy_mps);
        const isFollowed = p.player_id === followed;
        return (
          <g key={p.player_id} transform={`translate(${sx(p.position)} ${sy(p.position)})`} style={{ "--kit": teamColor(p.team_id) } as CSSProperties}
            className={`pl${highlight?.has(p.player_id) ? " hl" : ""}${isFollowed ? " followed" : ""}${onPlayer ? " clickable" : ""}`}
            onClick={onPlayer ? (ev) => { ev.stopPropagation(); onPlayer(p.player_id); } : undefined}>
            {isFollowed && <circle r={2.2} className="follow-ring" />}
            {free.has(p.player_id) && <circle r={2.3} className="free-ring" />}
            <circle r={1.25} fill={teamColor(p.team_id)} className="pl-body" />
            <text textAnchor="middle" dominantBaseline="central" className="pl-tx">{jersey(p.player_id)}</text>
            {free.has(p.player_id) && !isFollowed && <text y={3.6} textAnchor="middle" className="free-tx">unmarked</text>}
            {isFollowed && <text y={-3.1} textAnchor="middle" className={`follow-tx${speed >= SPRINT_MPS ? " sprint" : ""}`}>
              {speed >= SPRINT_MPS ? `sprint ${speed.toFixed(1)} m/s` : `${speed.toFixed(1)} m/s`}</text>}
          </g>
        );
      })}
      <circle cx={sx(ball)} cy={sy(ball)} r={0.6 + Math.min(frame.ball.z_m, 12) * 0.05} className="ball" />
      {tagged.map(({ id, t, w, x, y }) => (
        <g key={`tag-${id}`} className={`pl-tag ${t.role}`} transform={`translate(${x} ${y})`}>
          <rect x={-w / 2} y={-1.25} width={w} height={2.5} rx={1.25} />
          <text textAnchor="middle" dominantBaseline="central">{t.text}</text>
        </g>
      ))}
      {over.map((e) => {  // every leader line under every tag, so no leader crosses another tag's text
        const pl = places[e.element_id], g = e.geometry, p = g.type === "label" ? resolve(g.at, pos) : null;
        return pl?.leader && p && labels[e.element_id] ? <line key={`ld-${e.element_id}`} className={`label-leader layer-${e.layer}`}
          x1={sx(p)} y1={sy(p)} x2={pl.leader[0]} y2={pl.leader[1]} /> : null;
      })}
      {over.map((e) => <Element key={e.element_id} el={e} pos={pos} label={labels[e.element_id]} place={places[e.element_id]}
        selected={selected === e.element_id} onSelect={onSelect} />)}
    </svg>
  );
}

// Tracking comes at 5 Hz, so a pitch drawn frame by frame makes the players hop up to a metre and a half at a time.
// While the replay plays, the pitch glides from what it shows to each new frame over one frame interval (one frame
// behind, 200 ms at 1x); overlay marks anchored to players move with them. A seek, a jump of more than a few frames or
// a still frame (pause, replay moment, breakdown step) is drawn as it is.
const lerp = (a: XY, b: XY, f: number): XY => ({ x_m: a.x_m + (b.x_m - a.x_m) * f, y_m: a.y_m + (b.y_m - a.y_m) * f });

function useGlide(frame: ObservationFrame, ms: number, on: boolean): ObservationFrame {
  const [shown, setShown] = useState(frame);
  const shownRef = useRef(frame);
  const lastRef = useRef(frame);  // the last real frame
  useEffect(() => {
    const prev = lastRef.current, from = shownRef.current;
    lastRef.current = frame;
    const step = frame.frame_ref.frame_index - prev.frame_ref.frame_index;
    const show = (f: ObservationFrame) => { shownRef.current = f; setShown(f); };
    if (!on || frame.frame_ref.clip_id !== prev.frame_ref.clip_id || step <= 0 || step > 3) { show(frame); return; }
    const was = Object.fromEntries(from.players.map((pl) => [pl.player_id, pl.position]));
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const f = Math.min(1, (now - t0) / ms);
      show(f >= 1 ? frame : {
        ...frame,
        players: frame.players.map((pl) => was[pl.player_id] ? { ...pl, position: lerp(was[pl.player_id], pl.position, f) } : pl),
        ball: { ...frame.ball, position: lerp(from.ball.position, frame.ball.position, f) },
      });
      if (f < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [frame, ms, on]);
  return shown;
}

export function GlidingPitch({ glideMs, glide, ...props }: Props & { glideMs: number; glide: boolean }) {
  return <Pitch {...props} frame={useGlide(props.frame, glideMs, glide)} />;
}
