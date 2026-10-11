#!/usr/bin/env python3
"""
Research agent evaluation.

  venv/bin/python tests/eval/run_eval.py                  # routing + answers
  venv/bin/python tests/eval/run_eval.py --tier routing   # fast, no LLM (~seconds)
  venv/bin/python tests/eval/run_eval.py --tier answers --cat spreadsheet
  venv/bin/python tests/eval/run_eval.py --save-baseline  # accept current results

Reports go to logs/eval/<timestamp>.json. When logs/eval/baseline.json exists,
cases that passed there and fail now are listed as REGRESSIONS and the script
exits non-zero.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
logging.disable(logging.WARNING)

import yaml  # noqa: E402

CASES = Path(__file__).with_name("research_cases.yaml")
OUT_DIR = ROOT / "logs" / "eval"
BASELINE = OUT_DIR / "baseline.json"

_NOT_FOUND_PHRASES = (
    "not found", "no information", "does not contain", "do not contain", "doesn't contain",
    "couldn't find", "could not find", "not available", "no record", "not mentioned",
    "not provided", "no data", "unable to find", "cannot be determined", "can't be answered",
    "not in the", "isn't in", "no mention", "cannot determine", "can't determine", "unable to determine",
    "no relevant", "no pay stubs were paid", "not stated", "not specified", "does not include", "doesn't include",
)


def _norm(s: str) -> str:
    s = str(s).lower().replace("$", "")
    return re.sub(r"(?<=\d),(?=\d{3})", "", s)


def case_id(section: str, case: dict) -> str:
    return f"{section}:{case['q']}"


# ── Routing tier ─────────────────────────────────────────────────────────────

def run_routing(cases):
    from app.services.supervisor_agent import classify_question
    from app.services.document_react_agent import _apply_doc_type_overrides, _predetect_action_intent

    results = []
    for c in cases:
        t0 = time.time()
        got = {}
        errors = []
        if "domain" in c:
            got["domain"] = classify_question(c["q"])
            if not set(got["domain"]) & set(c["domain"]):
                errors.append(f"domain {got['domain']} not in {c['domain']}")
        if "doc_type" in c:
            plan = _apply_doc_type_overrides(
                {"intent": "info_retrieve", "doc_type_hint": "general", "search_queries": []}, c["q"])
            dt = plan.get("doc_type_hint") or "general"
            got["doc_type"] = "none" if dt == "general" else dt
            if got["doc_type"] not in c["doc_type"]:
                errors.append(f"doc_type {got['doc_type']!r} not in {c['doc_type']}")
        if "action" in c:
            got["action"] = _predetect_action_intent(c["q"]) or "none"
            if got["action"] != c["action"]:
                errors.append(f"action {got['action']!r} != {c['action']!r}")
        results.append({
            "id": case_id("routing", c), "tier": "routing", "cat": c.get("cat", "misc"),
            "q": c["q"], "pass": not errors, "errors": errors, "got": got,
            "ms": int((time.time() - t0) * 1000),
        })
    return results


# ── Answer tier ──────────────────────────────────────────────────────────────

def grade_answer(case: dict, answer: str):
    a = _norm(answer)
    errors = []
    for s in case.get("all", []):
        if _norm(s) not in a:
            errors.append(f"missing {s!r}")
    if case.get("any") and not any(_norm(s) in a for s in case["any"]):
        errors.append(f"none of {case['any']}")
    for s in case.get("none", []):
        if _norm(s) in a:
            errors.append(f"contains forbidden {s!r}")
    if case.get("not_found") and not any(p in a for p in _NOT_FOUND_PHRASES):
        errors.append("should say the information is not available")
    return errors


def _ask_http(question: str, base: str, token: str, tenant: str = None, prior: list = None) -> dict:
    """Same path as the browser: GET /api/research/ask/stream, read SSE events."""
    import httpx, urllib.parse
    qs = {"question": question, "jwt": token, **({"prior_qa": json.dumps(prior)} if prior else {})}
    url = f"{base}/api/research/ask/stream?" + urllib.parse.urlencode(qs)
    t0 = time.time(); first = None; tokens = []; steps = []; answer = ""; max_gap = 0.0; last = t0
    headers = {"Authorization": f"Bearer {token}", **({"X-Tenant-ID": tenant} if tenant else {})}
    with httpx.stream("GET", url, headers=headers, timeout=None) as r:
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            now = time.time(); max_gap = max(max_gap, now - last); last = now
            msg = json.loads(line[5:])
            if msg["type"] == "token":
                first = first or now; tokens.append(msg.get("text", ""))
            elif msg["type"] == "step":
                steps.append((msg.get("step") or {}).get("tool"))
            elif msg["type"] in ("done", "error"):
                answer = msg.get("answer") or "".join(tokens) or msg.get("message", "")
                break
    return {"answer": answer or "".join(tokens), "tools": steps,
            "first_token_s": round((first or last) - t0, 1), "max_gap_s": round(max_gap, 1)}


def run_answers(cases, http_base: str = None):
    from app.services.document_react_agent import run_research_agent
    token = None
    if http_base:
        import httpx
        token = httpx.post(f"{http_base}/api/auth/login", timeout=60, json={
            "username": os.environ.get("EVAL_USER", "admin"),
            "password": os.environ.get("EVAL_PASSWORD", "admin")}).json()["access_token"]

    results = []
    for i, c in enumerate(cases, 1):
        t0 = time.time()
        extra = {}
        try:
            if http_base:
                r = _ask_http(c["q"], http_base, token, c.get("tenant"), c.get("prior"))
                answer, tools = r["answer"], r["tools"]
                extra = {"first_token_s": r["first_token_s"], "max_gap_s": r["max_gap_s"]}
            else:
                from app.core.tenant import set_current_tenant_id, DEFAULT_TENANT_ID
                set_current_tenant_id(c.get("tenant") or DEFAULT_TENANT_ID)
                q = c["q"]
                if c.get("prior"):  # follow-up: same rewrite the stream endpoint applies
                    from app.services.followup import rewrite_followup
                    q = rewrite_followup(q, c["prior"])
                r = run_research_agent(q)
                answer = str(r.get("answer") or "")
                tools = [s.get("tool") for s in r.get("steps", [])]
            errors = grade_answer(c, answer)
        except Exception as e:
            answer, tools, errors = "", [], [f"crashed: {type(e).__name__}: {e}"]
        secs = time.time() - t0
        ok = not errors
        timing = f" first {extra['first_token_s']:4.1f}s" if extra else ""
        print(f"  [{i}/{len(cases)}] {'PASS' if ok else 'FAIL'} {secs:5.1f}s{timing}  {c['q'][:66]}", flush=True)
        results.append({
            "id": case_id("answers", c), "tier": "answers", "cat": c.get("cat", "misc"),
            "q": c["q"], "pass": ok, "errors": errors, "tools": tools,
            "answer": answer[:6000], "ms": int(secs * 1000), **extra,
        })
    return results


# ── Reporting ────────────────────────────────────────────────────────────────

def summarize(results):
    by = defaultdict(lambda: [0, 0])
    for r in results:
        key = (r["tier"], r["cat"])
        by[key][0] += r["pass"]
        by[key][1] += 1
    print("\n" + "=" * 64)
    for tier in ("routing", "answers"):
        rows = [(cat, p, n) for (t, cat), (p, n) in sorted(by.items()) if t == tier]
        if not rows:
            continue
        tp, tn = sum(p for _, p, _ in rows), sum(n for _, _, n in rows)
        print(f"{tier.upper():10} {tp}/{tn}  ({100 * tp / tn:.0f}%)")
        for cat, p, n in rows:
            bar = "█" * round(10 * p / n) + "·" * (10 - round(10 * p / n))
            print(f"   {cat:14} {bar} {p}/{n}")
    timed = [r for r in results if r.get("ms") and r["tier"] == "answers"]
    if timed:
        secs = sorted(r["ms"] / 1000 for r in timed)
        print(f"\nLATENCY  median {secs[len(secs)//2]:.1f}s · p90 {secs[int(len(secs)*0.9)]:.1f}s · "
              f"max {secs[-1]:.1f}s · under 15s: {sum(1 for x in secs if x < 15)}/{len(secs)}")
    fails = [r for r in results if not r["pass"]]
    if fails:
        print("\nFAILURES")
        for r in fails:
            print(f" ✗ [{r['tier']}/{r['cat']}] {r['q']}")
            for e in r["errors"]:
                print(f"     - {e}")
            if r["tier"] == "routing":
                print(f"     got: {r['got']}")
            else:
                print(f"     tools: {r['tools']}")
                print(f"     answer: {r['answer'][:220].replace(chr(10), ' ')}")


def compare_baseline(results):
    if not BASELINE.exists():
        return []
    base = {r["id"]: r["pass"] for r in json.loads(BASELINE.read_text())["results"]}
    regressions = [r for r in results if base.get(r["id"]) is True and not r["pass"]]
    fixed = [r for r in results if base.get(r["id"]) is False and r["pass"]]
    if fixed:
        print(f"\nFIXED since baseline: {len(fixed)}")
        for r in fixed:
            print(f" ✓ {r['q']}")
    if regressions:
        print(f"\nREGRESSIONS since baseline: {len(regressions)}")
        for r in regressions:
            print(f" ✗ {r['q']}  — {'; '.join(r['errors'])}")
    return regressions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["routing", "answers", "all"], default="all")
    ap.add_argument("--cat", help="only run this category")
    ap.add_argument("--save-baseline", action="store_true", help="store this run as the baseline")
    ap.add_argument("--http", nargs="?", const="http://localhost:8000", default=None,
                    help="run answers through the live server's stream endpoint (what the UI uses)")
    args = ap.parse_args()

    cases = yaml.safe_load(CASES.read_text())

    def pick(section):
        return [c for c in cases.get(section, []) if not args.cat or c.get("cat") == args.cat]

    results = []
    if args.tier in ("routing", "all"):
        print(f"Routing: {len(pick('routing'))} cases")
        results += run_routing(pick("routing"))
    if args.tier in ("answers", "all"):
        print(f"Answers: {len(pick('answers'))} cases (full agent, LLM)")
        results += run_answers(pick("answers"), args.http)

    summarize(results)
    regressions = compare_baseline(results)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tier": args.tier, "cat": args.cat, "results": results}
    path = OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1))
    print(f"\nReport: {path}")
    if args.save_baseline:
        if BASELINE.exists() and (args.cat or args.tier != "all"):
            # Merge a partial run into the existing baseline
            base = json.loads(BASELINE.read_text())
            keep = {r["id"]: r for r in base["results"]}
            keep.update({r["id"]: r for r in results})
            report["results"] = list(keep.values())
        BASELINE.write_text(json.dumps(report, indent=1))
        print(f"Baseline saved: {BASELINE}")
    sys.exit(1 if regressions else 0)


if __name__ == "__main__":
    main()
