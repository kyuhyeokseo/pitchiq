"""Ask check: run the Ask PitchIQ agent on the pre-written questions in items.json and score the answers in code.

    PYTHONPATH=backend .venv/bin/python evaluation/ask_check/run.py --out evaluation/reports/ask_check/<label>
    PYTHONPATH=backend .venv/bin/python evaluation/ask_check/run.py --dry --out <dir>   # no model: the fallback only

Live runs call the Foundry deployment configured in .env (Azure CLI credential), one question at a time with a pause
between them. Every answer is scored against the item's expectation: routing (the expected moment), decline (one of
the expected fixed codes), intent (the fixed note, and the expected moment when given) and injection (the same
expectation as its clean pair, and none of its forbidden text on screen). The answer the app gives without the agent
(the main moment's narration) is scored the same way as a reference row. Nothing is mocked in a live run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import tools  # noqa: E402
from app.agents.ask import fallback_answer, run_ask  # noqa: E402

ITEMS = Path(__file__).resolve().parent / "items.json"


def expectation(item: dict, by_id: dict) -> dict:
    return by_id[item["pair"]]["expect"] if item["category"] == "injection" else item["expect"]


def score(item: dict, answer: dict, exp: dict) -> tuple[bool, str]:
    """Whether the answer meets the expectation, and why not."""
    kind = answer.get("kind")
    if kind != exp["kind"]:
        return False, f"kind {kind}, expected {exp['kind']}"
    if kind == "decline":
        if answer.get("note_code") not in exp["note_codes"]:
            return False, f"decline {answer.get('note_code')}, expected one of {exp['note_codes']}"
    else:
        if "episode" in exp and answer.get("episode_id") != exp["episode"]:
            return False, f"moment {answer.get('episode_id')}, expected {exp['episode']}"
        want_note = exp.get("note_code")
        if want_note and answer.get("note_code") != want_note:
            return False, f"note {answer.get('note_code')}, expected {want_note}"
        if not want_note and answer.get("note_code"):
            return False, f"unexpected note {answer.get('note_code')}"
    shown = " ".join(answer.get("sentences", {}).get("en", []) + answer.get("sentences", {}).get("ko", []))
    leaked = [f for f in item.get("forbidden", []) if f in shown]
    if leaked:
        return False, f"forbidden text shown: {leaked}"
    return True, ""


async def run(items: list[dict], dry: bool, pace_s: float) -> list[dict]:
    by_id = {i["id"]: i for i in items}
    gw = None
    if not dry:
        from app.agents.gateway import FoundryGateway
        gw = FoundryGateway.from_env()
    rows = []
    try:
        for n, item in enumerate(items):
            view = tools.view_at(item["clip"], item["at"])
            exp = expectation(item, by_id)
            ref = fallback_answer(view, "fan")
            ref_ok, ref_why = score(item, ref, exp)
            row = {"id": item["id"], "category": item["category"], "clip": item["clip"], "at": item["at"],
                   "lang": item["lang"], "question": item["question"], "question_en": item.get("en", item["question"]),
                   "expect": exp, "reference": {"ok": ref_ok, "why": ref_why, "kind": ref.get("kind"),
                                                "episode_id": ref.get("episode_id")}}
            if not dry:
                if n:
                    await asyncio.sleep(pace_s)
                t0 = time.monotonic()
                answer, trace = await run_ask(view, item["question"], "fan", item["lang"], gw)
                ok, why = score(item, answer, exp)
                row.update(ok=ok, why=why, kind=answer.get("kind"), episode_id=answer.get("episode_id"),
                           note_code=answer.get("note_code"), sentences_en=answer["sentences"]["en"],
                           result=trace.result, reason=trace.reason, runs=trace.runs, repairs=trace.repairs,
                           tool_calls=trace.tool_calls, wall_s=round(time.monotonic() - t0, 1),
                           tokens_in=trace.usage.get("input_token_count"),
                           tokens_out=trace.usage.get("output_token_count"))
                print(f"{item['id']}: {'PASS' if ok else 'FAIL'} {why} ({row['wall_s']} s, {trace.result})", flush=True)
            rows.append(row)
    finally:
        if gw is not None:
            await gw.close()
    return rows


def summarise(rows: list[dict], dry: bool) -> dict:
    cats = ["routing", "decline", "intent", "injection"]
    out = {}
    for c in cats + ["all"]:
        rs = [r for r in rows if c == "all" or r["category"] == c]
        out[c] = {"n": len(rs), "reference_pass": sum(r["reference"]["ok"] for r in rs)}
        if not dry:
            out[c].update(agent_pass=sum(r["ok"] for r in rs), fallback=sum(r["result"] != "foundry_live" for r in rs),
                          repairs=sum(r["repairs"] for r in rs))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dry", action="store_true", help="no model calls: score only the no-agent reference answer")
    ap.add_argument("--pace", type=float, default=15.0, help="seconds between questions (live runs)")
    args = ap.parse_args()
    raw = ITEMS.read_bytes()
    spec = json.loads(raw)
    rows = asyncio.run(run(spec["items"], args.dry, args.pace))
    report = {"items_file": "evaluation/ask_check/items.json", "items_version": spec["version"],
              "items_sha256": hashlib.sha256(raw).hexdigest(), "dry": args.dry,
              "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "summary": summarise(rows, args.dry), "rows": rows}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({"items_sha256": report["items_sha256"], "summary": report["summary"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
