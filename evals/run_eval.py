"""Eval harness: runs a scripted multi-turn conversation against the backend.

Unlike a Q&A pair list, this is stateful: every turn lands in the same session
so repetition detection, topic switching and memory are exercised for real.
Checks are automated (citation validity, repetition overlap, switch acks).

Usage:
    python evals/run_eval.py [--url http://localhost:8000] [--out eval_report.md]
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # emoji-safe console output

HERE = Path(__file__).parent


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def run_check(check: dict, answer: dict, answers_by_turn: dict[int, dict]) -> tuple[bool, str]:
    kind = check["type"]
    text = answer.get("answer", "")

    if kind == "contains":
        missing = [e for e in check["expected"] if e.lower() not in text.lower()]
        return (not missing), ("missing: " + ", ".join(missing) if missing else "all found")

    if kind == "contains_any":
        hits = [e for e in check["expected"] if e.lower() in text.lower()]
        ok = bool(hits)
        return ok, (f"found: {hits[0]}" if hits else f"none of {check['expected']} found")

    if kind == "citations_valid":
        cites = answer.get("citations", [])
        if not cites:
            return False, "no citations provided"
        bad = [c for c in cites if not c.get("valid", False)]
        if bad or not answer.get("citations_valid", False):
            return False, f"{len(bad)} invalid citation(s)"
        return True, f"{len(cites)} citation(s) verified"

    if kind == "not_in_kb":
        ok = bool(answer.get("not_in_kb", False))
        return ok, "flagged not-in-KB" if ok else "not flagged; answer claimed KB coverage"

    if kind == "compressed":
        ok = bool(answer.get("compressed", False))
        return ok, "compressed instead of repeating" if ok else "full repeat not avoided"

    if kind == "topic_switch_ack":
        ok = bool(answer.get("topic_switch_ack", False))
        return ok, "switch acknowledged" if ok else "switch not acknowledged"

    if kind == "no_verbatim_repeat":
        ref = answers_by_turn.get(int(check["of_turn"]), {})
        jac = _jaccard(_tokens(text), _tokens(ref.get("answer", "")))
        ok = jac <= float(check["max_jaccard"])
        return ok, f"jaccard vs turn {check['of_turn']} = {jac:.2f} (max {check['max_jaccard']})"

    if kind == "covers_prior":
        lowered = text.lower()
        hits = sum(1 for t in check["topics"] if t.lower() in lowered)
        ok = hits >= int(check["min_mentions"])
        return ok, f"{hits}/{len(check['topics'])} prior topics mentioned"

    return False, f"unknown check type {kind}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("AGENT_URL", "http://localhost:8000"))
    parser.add_argument("--out", default=str(HERE.parent / "eval_report.md"))
    parser.add_argument(
        "--pause", type=float, default=15.0,
        help="seconds between turns (stays under free-tier per-minute quotas)",
    )
    args = parser.parse_args()

    turns = yaml.safe_load((HERE / "conversation.yaml").read_text())
    session = requests.post(f"{args.url}/sessions", timeout=30).json()["session_id"]

    results = []
    answers_by_turn: dict[int, dict] = {}

    for spec in turns:
        checks = []
        if isinstance(spec.get("check"), dict):
            checks.append(spec["check"])
        extra_checks = spec.get("also", []) or []
        if isinstance(extra_checks, str):
            extra_checks = [extra_checks]
        for extra in extra_checks:
            if isinstance(extra, str):
                extra = {"type": extra}
            checks.append(extra)

        t0 = time.time()
        resp = requests.post(
            f"{args.url}/sessions/{session}/messages",
            params={"stream": "false"},
            json={"question": spec["q"]},
            timeout=900,
        )
        latency = time.time() - t0
        if resp.status_code != 200:
            results.append({"turn": spec["turn"], "q": spec["q"], "ok": False,
                            "notes": [f"HTTP {resp.status_code}"], "latency": latency})
            continue

        answer = resp.json()
        answers_by_turn[spec["turn"]] = answer
        notes = []
        turn_ok = True
        for check in checks:
            ok, note = run_check(check, answer, answers_by_turn)
            turn_ok = turn_ok and ok
            notes.append(("✅ " if ok else "❌ ") + note)
        results.append(
            {
                "turn": spec["turn"],
                "q": spec["q"],
                "ok": turn_ok,
                "notes": notes,
                "latency": latency,
                "citations": len(answer.get("citations", [])),
                "tools": sorted({r["tool"] for r in answer.get("tool_runs", [])}),
            }
        )
        time.sleep(args.pause)

    passed = sum(1 for r in results if r["ok"])
    n_citations = sum(r.get("citations", 0) for r in results)
    lines = [
        "# Multi-turn conversation eval",
        "",
        f"**Turn pass rate: {passed}/{len(results)}** · session `{session}`",
        f"**Citations verified across the conversation: {n_citations}**",
        "",
        "| # | Question | Pass | Checks | Latency | Tools |",
        "|---|----------|------|--------|---------|-------|",
    ]
    for r in results:
        lines.append(
            f"| {r['turn']} | {r['q'][:56]} | {'✅' if r['ok'] else '❌'} |"
            f" {'; '.join(r.get('notes', []))} | {r['latency']:.1f}s |"
            f" {', '.join(r.get('tools', []))} |"
        )
    report = "\n".join(lines)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\nreport written to {args.out}")


if __name__ == "__main__":
    main()