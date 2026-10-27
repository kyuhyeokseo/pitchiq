"""Feature sweep over every public clip (offline server, or --base-url): the guided breakdown of every moment (each
step's panel, tags and drawn marks, with a screenshot), On air with approval, then the desk (timeline moments, replay
view, focus toggles, Fan/Analyst, element inspector, Ask, Recap, Export, My club, Follow), and playback smoothness
(how far players move per animation frame while playing). Writes sweep.json and screenshots into OUT.

Usage: python evaluation/walkthrough/sweep.py OUT [BASE_URL] [clip_001,clip_002,...]
Without BASE_URL it serves frontend/dist (or $SWEEP_DIST) with an offline backend on a free local port."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1])
BASE = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else None
CLIPS = sys.argv[3].split(",") if len(sys.argv) > 3 else [f"clip_{i:03d}" for i in (1, 2, 3, 4, 5, 6, 8, 9)]  # the clips the desk offers
OUT.mkdir(parents=True, exist_ok=True)

STATE_JS = """() => ({
  badge: document.querySelector('.badge')?.textContent ?? '',
  panel: document.querySelector('.bd')?.innerText ?? '',
  tags: [...document.querySelectorAll('svg.pitch .pl-tag text')].map((t) => t.textContent),
  marks: document.querySelectorAll('svg.pitch line.bd-mark').length,
  ov: document.querySelectorAll('svg.pitch .ov').length,
  ball_path: document.querySelectorAll('svg.pitch polyline.ball-path').length,
  exposure: document.querySelectorAll('svg.pitch polygon.exposure_hatched').length,
  screened: document.querySelectorAll('svg.pitch .lane_screened, svg.pitch .press_cut_lane').length,
  players: document.querySelectorAll('svg.pitch g.pl').length,
  key: [...document.querySelectorAll('.bd-key li')].map((l) => l.textContent),
  caption: document.querySelector('.caption')?.innerText ?? '',
  empty: document.querySelector('.pitch-wrap .empty')?.innerText ?? '',
})"""

# per animation frame: the largest distance any player moved on screen (pitch metres), while the replay plays
GLIDE_JS = """(ms) => new Promise((res) => {
  const pos = () => { const m = {}; document.querySelectorAll('svg.pitch g.pl').forEach((g) => {
    const id = (g.querySelector('circle.pl-body')?.getAttribute('fill') ?? '') + '#' + (g.querySelector('text.pl-tx')?.textContent ?? '');
    const t = /translate\\(([-\\d.e]+)[ ,]+([-\\d.e]+)\\)/.exec(g.getAttribute('transform') || '');
    if (t) m[id] = [parseFloat(t[1]), parseFloat(t[2])]; }); return m; };
  let prev = pos(); const jumps = []; const t0 = performance.now();
  const tick = () => { const cur = pos(); let mx = 0;
    for (const k in cur) if (prev[k]) mx = Math.max(mx, Math.hypot(cur[k][0] - prev[k][0], cur[k][1] - prev[k][1]));
    jumps.push(mx); prev = cur;
    if (performance.now() - t0 < ms) requestAnimationFrame(tick); else res(jumps); };
  requestAnimationFrame(tick); })"""

server = None
if BASE is None:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "PUBLIC_DATA_DIR": str(ROOT / "data/public"),
           "FRONTEND_DIST": os.environ.get("SWEEP_DIST", str(ROOT / "frontend/dist")), "LLM_MODE": "offline"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api.app:app", "--host", "127.0.0.1",
                               "--port", str(port), "--workers", "1", "--log-level", "warning"], cwd=ROOT, env=env)
    BASE = f"http://127.0.0.1:{port}"
    for _ in range(600):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=3); break
        except Exception:
            time.sleep(0.5)

result: dict = {"base": BASE, "clips": []}
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("response", lambda r: errors.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)
        page.goto(BASE + "/")
        page.wait_for_function("document.querySelectorAll('svg.pitch g.pl').length === 22", timeout=60000)
        st = lambda: page.evaluate(STATE_JS)  # noqa: E731

        for cid in CLIPS:
            t_clip = time.monotonic()
            errors.clear()
            rec: dict = {"clip": cid, "moments": [], "desk": {}, "notes": []}
            def note(msg: str) -> None:
                rec["notes"].append(msg); print(f"  [{cid}] {msg}", flush=True)
            try:
                if page.locator("select[aria-label=Clip]").input_value() != cid:
                    page.select_option("select[aria-label=Clip]", cid)
                    page.wait_for_timeout(500)
                page.wait_for_function("document.querySelectorAll('svg.pitch g.pl').length === 22", timeout=60000)
                try:
                    page.wait_for_selector(".intro", timeout=10000)
                    rec["intro"] = True
                except Exception:
                    rec["intro"] = False
                    note("no question card on a fresh clip")
                # ---------------- breakdown of every moment
                (page.locator(".intro button.primary") if rec["intro"] else page.locator(".bd-go")).click()
                page.wait_for_selector(".bd", timeout=90000)
                chips = page.locator(".bd-moments button")
                names = chips.all_inner_texts()
                on = page.evaluate("[...document.querySelectorAll('.bd-moments button')].findIndex((b) => b.classList.contains('on'))")
                rec["chips"], rec["default_chip"] = names, on
                order = ([on] if on >= 0 else [0]) + [i for i in range(len(names)) if i != on] if names else [None]
                for n, mi in enumerate(order):
                    if n > 0:
                        chips.nth(mi).click()
                        page.wait_for_function(f"document.querySelectorAll('.bd-moments button')[{mi}]?.classList.contains('on')"
                                               " && document.querySelector('.bd-badge')?.textContent.startsWith('1 /')",
                                               timeout=20000)
                    m = {"chip": names[mi] if mi is not None else "(only moment)", "default": n == 0, "steps": []}
                    for sti in range(4):
                        page.locator(".bd-steps button").nth(sti).click()
                        page.wait_for_function(f"document.querySelector('.bd-badge')?.textContent.startsWith('{sti + 1} /')",
                                               timeout=10000)
                        page.wait_for_timeout(700)
                        s = st()
                        shot = f"{cid}_m{n}_s{sti + 1}.png"
                        page.screenshot(path=str(OUT / shot))
                        m["steps"].append({**s, "shot": shot})
                    page.locator(".bd-steps button").nth(4).click()
                    page.wait_for_timeout(800)
                    m["on_air"] = st()["panel"]
                    if n == 0:  # approve the default moment and see it on the broadcast view
                        try:
                            page.wait_for_function("!document.querySelector('.bd .row button.ok')?.disabled", timeout=30000)
                            page.locator(".bd .row button.ok").click()
                            page.wait_for_selector(".caption", timeout=15000)
                            page.wait_for_timeout(600)
                            m["broadcast"] = st()
                            page.screenshot(path=str(OUT / f"{cid}_onair.png"))
                        except Exception as e:
                            note(f"approve/broadcast failed: {str(e)[:120]}")
                    rec["moments"].append(m)
                page.locator(".bd-go").click()
                page.wait_for_timeout(500)
                if page.locator(".bd").count():
                    note("exit did not close the breakdown")
                # ---------------- desk
                page.get_by_role("button", name="Inspector").click()
                desk = rec["desk"]
                eps = page.locator(".timeline .ep")
                desk["timeline"] = eps.count()
                if desk["timeline"] != len(names or [1]):
                    note(f"timeline shows {desk['timeline']} moments, the breakdown {len(names)}")
                desk["moments"] = []
                for i in range(eps.count()):
                    eps.nth(i).click(position={"x": 3, "y": 12})
                    page.get_by_role("button", name="Replay moment").click()
                    page.wait_for_timeout(900)
                    card = page.locator(".rail .card").first.inner_text() if page.locator(".rail .card").count() else ""
                    s = st()
                    shot = f"{cid}_desk_{i}.png"
                    page.screenshot(path=str(OUT / shot))
                    desk["moments"].append({"card": card[:700], "ov": s["ov"], "badge": s["badge"], "shot": shot})
                    if s["ov"] == 0:
                        note(f"desk moment {i}: nothing drawn in Replay moment")
                # focus toggles on the last selected moment
                b = st()
                page.get_by_role("button", name="What it takes away").click(); page.wait_for_timeout(300)
                a = st()
                page.get_by_role("button", name="What it takes away").click()
                page.get_by_role("button", name="What it leaves open").click(); page.wait_for_timeout(300)
                c = st()
                page.get_by_role("button", name="What it leaves open").click()
                desk["focus"] = {"both": [b["screened"], b["exposure"]], "takes_away": [a["screened"], a["exposure"]],
                                 "leaves_open": [c["screened"], c["exposure"]]}
                # Fan / Analyst
                fan = page.locator(".rail .texts").first.inner_text() if page.locator(".rail .texts").count() else ""
                page.get_by_role("button", name="Analyst").click(); page.wait_for_timeout(500)
                ana = page.locator(".rail .texts").first.inner_text() if page.locator(".rail .texts").count() else ""
                page.get_by_role("button", name="Fan").click()
                desk["fan"], desk["analyst"] = fan[:500], ana[:500]
                if fan and fan == ana:
                    note("Fan and Analyst texts are identical")
                # element inspector
                el = page.locator("svg.pitch .ov.clickable, svg.pitch .ov").first
                if el.count():
                    el.click(force=True); page.wait_for_timeout(500)
                    desk["inspector"] = page.locator(".insp").inner_text()[:400] if page.locator(".insp").count() else ""
                    if not desk["inspector"]:
                        note("clicking a drawn element opened no inspector")
                # Ask and Recap
                page.get_by_label("Question").fill("What did that leave open?")
                page.get_by_role("button", name="Ask", exact=True).click()
                try:
                    page.wait_for_selector(".ask .answer", timeout=90000)
                    desk["ask"] = page.locator(".ask .answer").first.inner_text()[:500]
                except Exception:
                    note("Ask gave no answer")
                page.get_by_role("button", name="Recap so far").click()
                try:
                    page.wait_for_function("document.querySelector('.recap')?.innerText.length > 60 && "
                                           "!document.querySelector('.recap button')?.disabled", timeout=90000)
                    desk["recap"] = page.locator(".recap").inner_text()[:700]
                except Exception:
                    note("Recap gave nothing")
                # Export of the approved moment: select it on the timeline first
                approved_idx = next((i for i, dm in enumerate(desk["moments"]) if "approved" in dm["card"].lower()), None)
                exp = page.get_by_role("button", name="Export JSON")
                if exp.count() and not exp.is_disabled():
                    with page.expect_download(timeout=15000) as dl:
                        exp.click()
                    path = OUT / f"{cid}_export.json"
                    dl.value.save_as(str(path))
                    data = json.loads(path.read_text())
                    desk["export"] = {"keys": sorted(data.keys())[:12], "decision": data.get("decision")}
                else:
                    desk["export"] = "disabled for the selected moment"
                # My club and Follow
                page.select_option("select[aria-label='My club']", "blue"); page.wait_for_timeout(300)
                desk["my_club_chip"] = page.locator(".chip.club").first.inner_text() if page.locator(".chip.club").count() else ""
                desk["my_club_tl"] = page.locator(".timeline .ep.mine-def, .timeline .ep.mine-att").count()
                page.select_option("select[aria-label='My club']", "")
                page.get_by_role("button", name="Live").click()
                page.locator("svg.pitch g.pl").nth(5).click(force=True)
                page.wait_for_timeout(300)
                desk["follow"] = page.locator("svg.pitch text.follow-tx").first.text_content() if page.locator("svg.pitch text.follow-tx").count() else ""
                if page.locator(".follow-chip").count():
                    page.locator(".follow-chip").click()
                # ---------------- playback: from the start, 1x
                page.select_option("select[aria-label=Speed]", "1")
                page.get_by_role("button", name="Play", exact=True).click()
                page.get_by_role("button", name="Pause", exact=True).wait_for(timeout=10000)
                page.wait_for_timeout(1000)
                c0 = page.locator(".clock span").inner_text()
                jumps = page.evaluate(GLIDE_JS, 3000)
                c1 = page.locator(".clock span").inner_text()
                page.get_by_role("button", name="Pause", exact=True).click()
                page.wait_for_timeout(1200)
                c2 = page.locator(".clock span").inner_text()
                page.wait_for_timeout(1000)
                c3 = page.locator(".clock span").inner_text()
                moving = [j for j in jumps if j > 1e-3]
                rec["playback"] = {"clock": [c0, c1, c2, c3], "raf_frames": len(jumps), "moving_frames": len(moving),
                                   "max_jump_m": round(max(jumps or [0]), 2),
                                   "p95_jump_m": round(sorted(moving)[int(0.95 * (len(moving) - 1))] if moving else 0, 2)}
                if c2 != c3:
                    note(f"clock moved while paused: {c2} -> {c3}")
            except Exception as e:
                note(f"sweep error: {str(e)[:300]}")
                page.screenshot(path=str(OUT / f"{cid}_error.png"))
            rec["errors"] = list(errors)
            rec["seconds"] = round(time.monotonic() - t_clip, 1)
            result["clips"].append(rec)
            print(json.dumps({"clip": cid, "chips": rec.get("chips"), "notes": rec["notes"], "errors": rec["errors"][:3],
                              "playback": rec.get("playback"), "s": rec["seconds"]}), flush=True)
            (OUT / "sweep.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
        browser.close()
finally:
    if server is not None:
        server.terminate()
