"""Real browser walkthrough of the built app (run on a compute node, not a login node).

    PYTHONPATH=backend python evaluation/walkthrough/walkthrough.py --out docs/walkthrough
    PYTHONPATH=backend python evaluation/walkthrough/walkthrough.py --out <dir> --base-url https://<deployed host>

Starts uvicorn on a free local port (or uses --base-url), drives headless Chromium through the
P0 flow (load -> play -> live overlay -> replay moment -> evidence inspector
-> Fan/KO -> approve -> broadcast -> negative clip), saves screenshots and a
JSON report of every check, then stops the server. Nothing here is mocked:
the page talks to the real backend over REST and WebSocket.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

ROOT = Path(__file__).resolve().parents[2]


LABELS_JS = """() => {
  const r = [...document.querySelectorAll('svg.pitch rect.label-bg')].map((e) => e.getBoundingClientRect());
  const s = document.querySelector('svg.pitch').getBoundingClientRect();
  let overlaps = 0, outside = 0;
  for (let i = 0; i < r.length; i++) {
    const a = r[i];
    if (a.left < s.left - 0.5 || a.right > s.right + 0.5 || a.top < s.top - 0.5 || a.bottom > s.bottom + 0.5) outside++;
    for (let j = i + 1; j < r.length; j++) {
      const b = r[j];
      if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) overlaps++;
    }
  }
  return { n: r.length, overlaps, outside };
}"""

ON_BALL_JS = """() => {
  const b = document.querySelector('svg.pitch circle.ball').getBoundingClientRect();
  const c = (r) => [(r.left + r.right) / 2, (r.top + r.bottom) / 2];
  const [bx, by] = c(b);
  const pl = [...document.querySelectorAll('svg.pitch g.pl')];
  const d = pl.map((g) => { const [x, y] = c(g.querySelector('circle').getBoundingClientRect()); return (x - bx) ** 2 + (y - by) ** 2; });
  const nearest = d.indexOf(Math.min(...d));
  const text = (i) => i >= 0 ? pl[i].querySelector('circle').getAttribute('fill') + ' #' + pl[i].textContent : '';
  return { nearest: text(nearest), last: text(pl.length - 1) };
}"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_health(url: str, server: subprocess.Popen | None, timeout: float = 300.0) -> dict:
    # a cold compute node can take minutes to import the app from a slow shared filesystem
    t0 = time.time()
    while time.time() - t0 < timeout:
        if server is not None and server.poll() is not None:
            raise RuntimeError(f"server exited with code {server.returncode}")
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=5) as r:
                health = json.loads(r.read())
            print(f"server healthy after {time.time() - t0:.1f} s", flush=True)
            return health
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"server did not become healthy within {timeout:.0f} s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "walkthrough")
    ap.add_argument("--base-url", help="check an app that is already running (such as the deployed one) instead of "
                                       "starting a local server")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    server = None
    if args.base_url:
        base = args.base_url.rstrip("/")
    else:
        port = free_port()
        env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "PUBLIC_DATA_DIR": str(ROOT / "data" / "public"),
               "FRONTEND_DIST": str(ROOT / "frontend" / "dist"), "LLM_MODE": os.environ.get("LLM_MODE", "offline")}
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api.app:app", "--host", "127.0.0.1", "--port",
                                   str(port), "--workers", "1", "--log-level", "warning"], cwd=ROOT, env=env)
        base = f"http://127.0.0.1:{port}"
    checks: list[dict] = []
    notes: list[dict] = []  # narration checks on a Foundry-on host (see narration_check)
    cloud = False
    console: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)

    def narration_check(name: str, ok: bool, detail: str = "") -> None:
        """A check of particular template sentences. With Foundry on, the agents may choose other validated sentences
        for the card, so the result is kept as information rather than counted as a pass or a failure."""
        if not cloud:
            check(name, ok, detail)
            return
        notes.append({"check": name, "matched": bool(ok), "detail": detail})
        print(("INFO " if ok else "INFO (agents chose other sentences) ") + name, flush=True)

    def played_to_the_end() -> None:
        """The replay started (the button reads Pause) and then ended (Play again); the clock runs at the real speed."""
        page.get_by_role("button", name="Pause", exact=True).wait_for(timeout=10000)
        page.get_by_role("button", name="Play", exact=True).wait_for(timeout=60000)

    def labels_clear(page, scene: str) -> None:
        """Every pitch label sits inside the pitch and on no other label (measured in the rendered page)."""
        res = page.evaluate(LABELS_JS)
        check(f"{scene}: pitch labels do not overlap or leave the pitch",
              res["n"] > 0 and res["overlaps"] == 0 and res["outside"] == 0, json.dumps(res))

    def on_ball_on_top(page, scene: str) -> None:
        res = page.evaluate(ON_BALL_JS)
        check(f"{scene}: the player on the ball is drawn on top", res["last"] == res["nearest"], json.dumps(res))

    try:
        health = wait_health(base, server)
        cloud = bool(health.get("cloud_enabled"))
        check("health endpoint", health.get("status") == "ok", json.dumps(health))
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error",) else None)
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.on("response", lambda r: console.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)
            page.goto(base + "/")
            page.wait_for_selector("svg.pitch")
            page.wait_for_function("document.querySelectorAll('svg.pitch g.pl').length === 22")
            check("pitch renders 22 players", page.locator("svg.pitch g.pl").count() == 22)
            check("ball rendered", page.locator("svg.pitch circle.ball").count() == 1)
            check("synthetic label visible", page.get_by_text("SYNTHETIC DATA").is_visible())
            page.screenshot(path=str(args.out / "01_loaded.png"))

            page.select_option("select[aria-label=Speed]", "1")  # live at the speed a viewer watches it
            page.get_by_role("button", name="Play", exact=True).click()
            page.wait_for_selector(".ep.complete_tradeoff", timeout=30000)
            check("complete trade-off episode appears while playing", True)
            page.wait_for_function("document.querySelectorAll('svg.pitch .ov').length > 0", timeout=15000)
            n_live = page.locator("svg.pitch .ov").count()
            check("live overlay elements drawn from backend", n_live > 0, f"{n_live} elements")
            on_ball_on_top(page, "A live")
            page.screenshot(path=str(args.out / "02_live.png"))
            page.wait_for_function(
                "Array.from(document.querySelectorAll('.chip.oc')).some(e => e.textContent.includes('Exploited'))",
                timeout=30000)
            check("outcome revision shows exploitation after the long ball", True)
            # while playing, an episode that has closed is not redrawn on later frames; once the clip ends the pitch
            # shows that moment as analysed instead of going empty
            page.wait_for_function("['nothing current', 'REPLAY'].some(t => "
                                   "document.querySelector('.badge')?.textContent.includes(t))", timeout=30000)
            badge = page.locator(".badge").inner_text()
            if "nothing current" in badge:
                check("A live after the episode closed: its old claims are not drawn on the new frame",
                      page.locator("svg.pitch .ov").count() == 0, badge)
            else:
                check("A live after the episode closed: the clip ended, the moment is shown as a replay",
                      "REPLAY" in badge and page.locator("svg.pitch .ov").count() > 0, badge)
            if page.get_by_role("button", name="Pause", exact=True).count():
                page.get_by_role("button", name="Pause", exact=True).click()
            page.wait_for_function("document.querySelector('.badge')?.textContent.includes('REPLAY')", timeout=10000)
            try:  # the badge comes with the overlay; the replay frame it is drawn on arrives a moment later
                page.wait_for_function("document.querySelectorAll('svg.pitch .ov').length > 0", timeout=10000)
            except Exception:
                pass
            check("paused or ended: the pitch shows the followed moment, not an empty pitch",
                  page.locator("svg.pitch .ov").count() > 0, page.locator(".badge").inner_text())

            page.locator("button", has_text="After (rev").click()
            page.wait_for_function("document.querySelector('.badge')?.textContent.includes('REPLAY')", timeout=10000)
            check("replay moment badge", "REPLAY" in page.locator(".badge").inner_text(),
                  page.locator(".badge").inner_text())
            page.screenshot(path=str(args.out / "03_replay.png"))

            page.get_by_role("button", name="What it leaves open").click()
            page.wait_for_selector("svg.pitch polygon.exposure_hatched", timeout=10000)
            check("exposure polygon shown for the exposed side", page.locator("polygon.exposure_hatched").count() >= 1)
            labels_clear(page, "A exposed")
            page.screenshot(path=str(args.out / "04_exposed.png"))
            page.locator("svg.pitch polygon.exposure_hatched").first.click(force=True)
            page.wait_for_selector(".insp table", timeout=5000)
            insp = page.locator(".insp").inner_text()
            check("inspector shows the metrics behind the clicked area",
                  "Back line from its own goal" in insp and "Cover margin" in insp, insp[:160].replace("\n", " | "))
            page.screenshot(path=str(args.out / "05_inspector.png"))

            page.get_by_role("button", name="What it leaves open").click()
            page.get_by_role("button", name="Fan").click()
            page.get_by_role("button", name="KO", exact=True).click()
            page.wait_for_timeout(600)
            txt = page.locator(".texts").inner_text()
            check("Fan/KO narration rendered", any("가" <= ch <= "힣" for ch in txt), txt[:120].replace("\n", " | "))
            page.screenshot(path=str(args.out / "06_fan_ko.png"))

            page.get_by_role("button", name="Broadcast").click()
            page.wait_for_timeout(400)
            check("broadcast hidden before approval", page.get_by_text("No approved insight").is_visible())
            page.get_by_role("button", name="Inspector").click()
            page.get_by_role("button", name="Approve").click()
            page.wait_for_selector(".rs.approved", timeout=5000)
            check("approval recorded", page.locator(".rs.approved").count() == 1)
            page.get_by_role("button", name="Broadcast").click()
            page.wait_for_selector(".badge.bc", timeout=5000)
            page.wait_for_selector(".caption", timeout=5000)
            check("broadcast shows the approved revision with caption",
                  page.locator(".caption").inner_text().strip() != "", page.locator(".badge").inner_text())
            page.screenshot(path=str(args.out / "07_broadcast.png"))

            # Ask PitchIQ: with Foundry the agent answers; offline the main moment's narration answers and says so
            page.get_by_role("button", name="Inspector").click()
            page.get_by_role("button", name="EN", exact=True).click()
            page.get_by_label("Question").fill("What did that leave open?")
            page.get_by_role("button", name="Ask", exact=True).click()
            page.wait_for_selector(".ask .answer", timeout=90000)
            ans = page.locator(".ask .answer").first.inner_text()
            source = "Answered by the Ask agent" if health.get("cloud_enabled") else "Deterministic fallback"
            check("Ask: an answer with its source and real trace", source in ans and len(ans.splitlines()) >= 3,
                  ans[:240].replace("\n", " | "))
            page.locator(".ask .answer").first.get_by_role("button", name="Show on pitch").click()
            page.wait_for_function("document.querySelector('.badge')?.textContent.includes('REPLAY')", timeout=10000)
            check("Ask: show on pitch selects the moment in replay", page.locator(".ep.sel").count() == 1)
            page.screenshot(path=str(args.out / "07b_ask.png"), full_page=True)
            page.get_by_role("button", name="Recap so far").click()
            page.wait_for_selector(".recap .recap-item", timeout=90000)
            rc = page.locator(".recap").inner_text()
            n_items = page.locator(".recap .recap-item").count()
            src = "built by the Recap agent" if health.get("cloud_enabled") else "deterministic recap"
            check("Recap: 1-3 moments with their sentences and the source", 1 <= n_items <= 3 and src in rc,
                  f"{n_items} items | " + rc[:200].replace("\n", " | "))
            page.get_by_role("button", name="KO", exact=True).click()

            page.get_by_role("button", name="Inspector").click()
            page.select_option("select[aria-label=Clip]", "clip_004")
            page.wait_for_function("document.querySelectorAll('svg.pitch g.pl').length === 22")
            page.select_option("select[aria-label=Speed]", "2")
            page.get_by_role("button", name="Play", exact=True).click()
            page.wait_for_selector(".ep.partial", timeout=30000)
            played_to_the_end()
            page.wait_for_timeout(3000)
            card = page.locator(".rail .card").first.inner_text()
            check("negative clip: same press stays a partial analysis",
                  "partial" in card and "Nothing left open is supported yet" in card, card[:200].replace("\n", " | "))
            evid = page.locator(".insp").inner_text()
            check("negative clip: suppression reason shown", "Not claimed" in evid, evid[:200].replace("\n", " | "))
            page.screenshot(path=str(args.out / "08_negative.png"))

            page.get_by_role("button", name="Analyst").click()
            page.get_by_role("button", name="EN", exact=True).click()

            def play_clip(cid: str) -> str:
                page.select_option("select[aria-label=Clip]", cid)
                page.wait_for_function("document.querySelectorAll('svg.pitch g.pl').length === 22")
                page.select_option("select[aria-label=Speed]", "2")
                page.get_by_role("button", name="Play", exact=True).click()
                played_to_the_end()
                page.wait_for_timeout(16000)  # with Foundry on, the last plans attach after the clip has ended
                return page.locator(".rail .card").first.inner_text()

            card = play_clip("clip_003")
            check("C: central screen + wide outlet + free receiver form a complete trade-off",
                  "complete tradeoff" in card and "Central routes screened" in card and "Accessible free receiver" in card
                  and "Wide outlet available" in card, card[:220].replace("\n", " | "))
            check("C: drop then defensive step recorded", "attacker drop" in card and "defender step" in card,
                  card[card.find("OBSERVED"):][:120].replace("\n", " | "))
            check("C: outcome revision after the wide pass", "Exploited" in card)
            page.screenshot(path=str(args.out / "09_c_main.png"))
            page.locator("button", has_text="After (rev").click()
            page.wait_for_function("document.querySelector('.badge')?.textContent.includes('REPLAY')", timeout=10000)
            page.wait_for_timeout(500)
            check("C: replay draws the observed movement paths", page.locator("svg.pitch polyline.ghost_path").count() >= 1)
            page.screenshot(path=str(args.out / "10_c_after.png"))
            page.locator("button", has_text="Before (rev 1)").click()
            page.wait_for_timeout(800)
            check("C: before (rev 1) has no movement paths yet", page.locator("svg.pitch polyline.ghost_path").count() == 0)
            page.screenshot(path=str(args.out / "11_c_before.png"))
            page.get_by_role("button", name="Live", exact=True).click()

            card = play_clip("clip_002")
            check("B: dropped line + disconnected press + between-lines receiver",
                  "complete tradeoff" in card and "Depth behind reduced" in card and "Receiver between lines" in card,
                  card[:220].replace("\n", " | "))
            check("B: attacker move into the gap then midfield drop", "lane change" in card and "block shift" in card)
            page.locator("button", has_text="After (rev").click()
            page.wait_for_function("document.querySelector('.badge')?.textContent.includes('REPLAY')", timeout=10000)
            page.wait_for_timeout(500)
            check("B: replay shows the midfield line and the gap", page.locator("svg.pitch polyline.line_mid").count() >= 1
                  and page.locator("svg.pitch polygon.exposure_hatched").count() >= 1)
            page.screenshot(path=str(args.out / "12_b_main.png"))
            page.get_by_role("button", name="Live", exact=True).click()

            card = play_clip("clip_006")
            evid = page.locator(".insp").inner_text()
            check("C negative: numbers without a free man stay partial",
                  "partial" in card and "Accessible free receiver" not in card and "Not claimed: accessible free receiver"
                  in evid, evid[:200].replace("\n", " | "))
            page.screenshot(path=str(args.out / "13_c_negative.png"))

            def open_episode(prefix: str) -> str:
                page.locator(f".ep[title^='{prefix}']").click()
                page.locator("button", has_text="After (rev").click()
                page.wait_for_function("document.querySelector('.badge')?.textContent.includes('REPLAY')", timeout=10000)
                page.wait_for_timeout(600)
                return page.locator(".rail .card").first.inner_text()

            play_clip("clip_007")
            card = open_episode("ep_clip_007_red_central_screen_f0023")
            check("D: trap on the left centre-back is a restricting-only story",
                  "partial" in card and "Central routes screened" in card, card[:220].replace("\n", " | "))
            check("D: the presser stands in the passing lines (press-cut lanes drawn)",
                  page.locator("svg.pitch line.press_cut_lane").count() >= 2)
            pitch_text = " | ".join(page.locator("svg.pitch text.label-tx").all_text_contents())  # SVG: no inner_text
            check("D: press-cut and numbers labels on the pitch",
                  "press cuts 2 lanes" in pitch_text and "ball zone 4 v 5" in pitch_text, pitch_text[:160].replace("\n", " | "))
            narration_check("D: narration names the lane-cutting press and the numbers",
                  "standing in the passing line" in card and "4 defenders against 5 attackers" in card)
            labels_clear(page, "D trap")
            page.screenshot(path=str(args.out / "14_d_trap.png"))
            page.get_by_role("button", name="Live", exact=True).click()

            play_clip("clip_008")
            card = open_episode("ep_clip_008_red_press_vs_depth_f0032")
            check("D escape: screened options + free centre-back form a complete trade-off",
                  "complete tradeoff" in card and "Short options screened" in card and "Accessible free receiver" in card,
                  card[:220].replace("\n", " | "))
            check("D escape: exploited, then the striker's step is recorded", "Exploited" in card and "defender step" in card)
            i_recv, i_step = card.find("was received by"), card.find("stepped up")
            narration_check("D escape: narration tells reception before the step", 0 <= i_recv < i_step,
                  card[max(i_recv, 0):][:160].replace("\n", " | "))
            labels_clear(page, "D escape")
            page.screenshot(path=str(args.out / "15_d_escape.png"))
            page.get_by_role("button", name="Live", exact=True).click()

            play_clip("clip_009")
            card = open_episode("ep_clip_009_blue_press_vs_depth_f0011")
            check("E: passive screen, step out, free man form one complete story",
                  "complete tradeoff" in card and "Short options screened" in card and "Accessible free receiver" in card
                  and "defender step" in card and "Exploited" in card, card[:220].replace("\n", " | "))
            labels_clear(page, "E after")
            page.screenshot(path=str(args.out / "16_e_after.png"))
            page.locator("button", has_text="Before (rev 1)").click()
            page.wait_for_timeout(800)
            card = page.locator(".rail .card").first.inner_text()
            check("E: before (rev 1) is told with rev 1's own narration, without the later step",
                  "narration at rev 1" in card.lower() and "stepped up" not in card
                  and page.locator("svg.pitch circle.carrier_space_ring").count() == 1,
                  card[card.lower().find("narration at rev 1"):][:120].replace("\n", " | "))
            narration_check("E: before (rev 1) the narration says nobody presses the carrier", "Nobody presses" in card,
                            card[card.find("Nobody"):][:120].replace("\n", " | "))
            labels_clear(page, "E before")
            page.screenshot(path=str(args.out / "17_e_before.png"))
            page.get_by_role("button", name="Live", exact=True).click()

            card = play_clip("clip_010")
            check("E hold: the screen holds, nobody comes free",
                  "partial" in card and "Accessible free receiver" not in card and "defender step" not in card,
                  card[:220].replace("\n", " | "))
            page.screenshot(path=str(args.out / "18_e_hold.png"))
            page.get_by_role("button", name="Live", exact=True).click()

            play_clip("clip_011")
            card = open_episode("ep_clip_011_red_press_vs_depth_f0016")
            check("G: press on the back pass + line steps up + space behind, used",
                  "complete tradeoff" in card and "Short options screened" in card and "Space behind the line" in card
                  and "block shift" in card and "Exploited" in card, card[:220].replace("\n", " | "))
            narration_check("G: narration says the back line stepped up together", "stepped up" in card and "together" in card)
            check("G: replay draws the back line's movement", page.locator("svg.pitch polyline.ghost_path").count() >= 3)
            labels_clear(page, "G main")
            page.screenshot(path=str(args.out / "19_g_main.png"))
            page.get_by_role("button", name="Live", exact=True).click()

            play_clip("clip_012")
            card = open_episode("ep_clip_012_red_press_vs_depth_f0016")
            evid = page.locator(".insp").inner_text()
            check("G offside: same step-up, runner beyond the line is not claimed as space behind",
                  "partial" in card and "block shift" in card and "Not claimed: behind space exposed — offside geometry"
                  in evid, evid[:200].replace("\n", " | "))
            labels_clear(page, "G offside")
            page.screenshot(path=str(args.out / "20_g_offside.png"))

            # Studio view and personalisation (on the G offside clip that is loaded now)
            card = page.locator(".rail .card").first.inner_text()
            check("Studio: technical ids stay behind Details", "ep_clip_" not in card and "priority" not in card,
                  card[:140].replace("\n", " | "))
            page.get_by_role("button", name="Details", exact=True).click()
            card = page.locator(".rail .card").first.inner_text()
            check("Details shows the episode id and priority", "ep_clip_" in card and "priority" in card)
            page.get_by_role("button", name="Details", exact=True).click()
            page.select_option("select[aria-label='My club']", "red")
            page.wait_for_selector(".chip.club", timeout=5000)
            chip = page.locator(".chip.club").inner_text()
            check("My club: the card says whether your club defends or attacks here",
                  chip in ("Your club is defending here", "Your club is attacking here"), chip)
            page.select_option("select[aria-label='My club']", "")
            n_before = page.locator(".timeline .ep").count()
            page.locator("svg.pitch g.pl").nth(3).click(force=True)
            page.wait_for_selector("svg.pitch circle.follow-ring", timeout=5000)
            tx = page.locator("svg.pitch text.follow-tx").text_content() or ""
            check("Follow player: a ring and the measured speed on the followed player", "m/s" in tx, tx)
            page.locator("button", has_text="'s moments").click()
            page.wait_for_timeout(300)
            n_after = page.locator(".timeline .ep").count()
            check("Follow player: the timeline can show only that player's moments", n_after <= n_before,
                  f"{n_before} -> {n_after}")
            page.screenshot(path=str(args.out / "21_personalised.png"))
            page.locator("button", has_text="'s moments").click()
            page.locator(".follow-chip").click()

            # a public link: switching clips frees the old replay, so 25 switches never fill the store
            for i in range(25):
                page.select_option("select[aria-label=Clip]", "clip_001" if i % 2 else "clip_002")
                page.wait_for_function("document.querySelectorAll('svg.pitch g.pl').length === 22")
            page.wait_for_timeout(4500)  # a left replay is freed 3 s later, once its in-flight requests are done
            with urllib.request.urlopen(base + "/api/health", timeout=5) as r:
                h = json.loads(r.read())
            check("25 clip switches free their replays (no 429, at most 2 open)", h["sessions"] <= 2,
                  f"sessions {h['sessions']}, sockets {h['sockets']}")

            # a viewer far from the server: at 400 ms a request, several live overlay requests are in flight at once
            # and the pitch must still draw their answers
            slow = browser.new_page(viewport={"width": 1500, "height": 1000})
            slow.on("pageerror", lambda e: console.append(f"pageerror (slow link): {e}"))
            slow.context.new_cdp_session(slow).send("Network.emulateNetworkConditions", {
                "offline": False, "latency": 400, "downloadThroughput": -1, "uploadThroughput": -1})
            slow.goto(base + "/")
            slow.wait_for_function("document.querySelectorAll('svg.pitch g.pl').length === 22", timeout=60000)
            slow.select_option("select[aria-label=Speed]", "1")
            slow.get_by_role("button", name="Play", exact=True).click()
            slow.wait_for_selector(".ep.complete_tradeoff", timeout=30000)
            drawn = slow.wait_for_function("document.querySelectorAll('svg.pitch .ov').length || 0", timeout=15000)
            check("live overlay is drawn over a slow link (400 ms per request)", drawn.json_value() > 0,
                  f"{drawn.json_value()} elements")
            # paused, switch clips, press Play at once: the click must start the new clip, not vanish while the new
            # replay is still being created
            slow.get_by_role("button", name="Pause", exact=True).click()
            slow.get_by_role("button", name="Play", exact=True).wait_for()
            slow.select_option("select[aria-label=Clip]", "clip_002")
            slow.get_by_role("button", name="Play", exact=True).click()
            started = True
            try:
                slow.get_by_role("button", name="Pause", exact=True).wait_for(timeout=15000)
            except PlaywrightTimeout:
                started = False
            check("Play right after a clip switch over a slow link starts the new clip", started)
            slow.close()
            browser.close()
        check("no browser console errors", not console, "; ".join(console[:5]))
    except Exception as e:  # record where it stopped; the report is still written
        here = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__]
        check("walkthrough ran to the end", False,
              f"{type(e).__name__} at line {here[-1] if here else '?'}: {str(e).splitlines()[0][:200]}")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    report = {"base": base, "checks": checks, "console_errors": console,
              "passed": sum(c["ok"] for c in checks), "failed": sum(not c["ok"] for c in checks),
              "narration_notes": notes,
              "host": os.environ.get("PITCHIQ_HOST_LABEL") or ("hpc-compute-node" if os.environ.get("SLURM_JOB_ID") else "workstation"), "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("passed", "failed", "host")}))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
