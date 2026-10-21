#!/usr/bin/env python3
"""
Tough-question evaluation: a strong cloud model writes hard questions from each page, with the
answer and a verbatim evidence quote; the quote is checked against the stored page text (no
quote on the page → question dropped); then the local research pipeline answers each question
and is scored against the verified answer.

  venv-arm64/bin/python tests/eval/tough_eval.py generate --doc california-tax-info.pdf --pages 12
  venv-arm64/bin/python tests/eval/tough_eval.py ask "question text" --doc california-tax-info.pdf   # add your own
  venv-arm64/bin/python tests/eval/tough_eval.py run                                                 # local answers + score

Question sets live in logs/eval/tough_questions.json; results in logs/eval/tough_results.json.
Document text goes to the organisation's configured cloud provider (masked identifiers), so
only run `generate` / `ask` on documents cleared for that.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
QFILE = ROOT / "logs" / "eval" / "tough_questions.json"
RFILE = ROOT / "logs" / "eval" / "tough_results.json"
TENANT = "00000000-0000-0000-0000-000000000001"

GEN_PROMPT = """You are writing a hard exam to test a document question-answering system.
Below is page {page} of "{filename}".

Write {n} DIFFICULT questions that can be answered ONLY from this page. Prefer questions that need:
- an exact figure from a specific table row/column, a comparison, a ranking (highest/lowest),
  a count of rows meeting a condition, or a small calculation from two figures on the page;
- multiple parts (2-3) in one question.
For each question give the exact answer and a VERBATIM quote (copied character-for-character
from the page, 5-30 words) that proves it.

Return JSON only: {{"questions": [{{"question": "...", "answer": "...", "key_facts": ["figure or name that must appear", ...], "evidence": "verbatim quote"}}]}}

PAGE {page}:
{text}"""

ANSWER_PROMPT = """Answer the question using ONLY the pages below from "{filename}". Give the exact answer,
key_facts (each figure or name the answer must contain), and a VERBATIM evidence quote (5-30 words,
copied exactly) with its page number. If the pages don't contain it, say so.

Return JSON only: {{"answer": "...", "key_facts": ["..."], "evidence": "verbatim quote", "page": N}}

QUESTION: {question}

{pages}"""

JUDGE_PROMPT = """Grade a system's answer against the verified reference. PASS only if every key fact is
correct (numbers may be formatted differently, e.g. $1.39 billion = $1,394,549,000 is fine only if
consistent). PARTIAL if some parts are right. FAIL otherwise.

Question: {question}
Reference answer: {answer}
Key facts: {facts}
System answer: {got}

Return JSON only: {{"grade": "PASS|PARTIAL|FAIL", "why": "one sentence"}}"""


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _cloud_cfg():
    """The organisation's stored cloud provider (active or not) — used only by this eval."""
    from sqlalchemy import text
    from app.core.secret_box import decrypt
    db = _db()
    try:
        row = db.execute(text("""SELECT provider, model, api_key_enc, base_url FROM tenant_llm_providers
            WHERE tenant_id = :t ORDER BY is_active DESC, updated_at DESC NULLS LAST LIMIT 1"""), {"t": TENANT}).fetchone()
    finally:
        db.close()
    if not row:
        sys.exit("No cloud provider stored for this organisation (Integrations → Cloud models).")
    return {"provider": row.provider, "model": row.model, "api_key": decrypt(row.api_key_enc), "base_url": row.base_url}


def _cloud_json(prompt: str, max_tokens: int = 3000) -> dict:
    from app.core import llm
    cfg = _cloud_cfg()
    msgs = [{"role": "user", "content": prompt + "\n\nReply with valid JSON only."}]
    msgs, mapping = llm._mask(msgs)                         # same masking as the app's cloud calls
    send = llm._anthropic if llm.PROVIDERS.get(cfg["provider"], {}).get("kind") == "anthropic" else llm._openai
    for attempt in range(3):
        try:
            r = send(cfg, msgs, {"temperature": 0.2, "num_predict": max_tokens}, False)
            txt = llm._unmask(r["message"]["content"], mapping)
            m = re.search(r"\{.*\}", txt, re.S)
            return json.loads(m.group(0)) if m else {}
        except Exception as e:
            print(f"   cloud call failed ({e}); retry {attempt + 1}", flush=True)
            time.sleep(5)
    return {}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.%]", "", (s or "").lower().replace(",", ""))


def _quote_on(quote: str, text: str) -> bool:
    """Verbatim check, tolerant of spacing / punctuation / line breaks only."""
    q, t = _norm(quote), _norm(text)
    if len(q) < 12:
        return False
    if q in t:
        return True
    # allow a long quote whose two halves are each on the page (line-wrapped table rows)
    half = len(q) // 2
    return q[:half] in t and q[half:] in t


def _doc(filename: str):
    from sqlalchemy import text
    db = _db()
    try:
        d = db.execute(text("SELECT id::text, filename FROM documents WHERE filename = :f"), {"f": filename}).fetchone()
        if not d:
            sys.exit(f"No document named {filename}")
        pages = db.execute(text("SELECT page_number, text FROM document_pages WHERE document_id::text = :d "
                                "ORDER BY page_number"), {"d": d.id}).fetchall()
    finally:
        db.close()
    return d, {p.page_number: p.text for p in pages}


def _load() -> list:
    return json.loads(QFILE.read_text()) if QFILE.exists() else []


def _save(qs: list) -> None:
    QFILE.parent.mkdir(parents=True, exist_ok=True)
    QFILE.write_text(json.dumps(qs, indent=1))


def generate(filename: str, n_pages: int, per_page: int) -> None:
    d, pages = _doc(filename)
    # pages dense with figures make the hardest questions
    ranked = sorted(pages, key=lambda n: -len(re.findall(r"\d[\d,.]{2,}", pages[n])))
    chosen = sorted(n for n in ranked[:n_pages] if len(pages[n]) > 400)
    qs = _load()
    for pn in chosen:
        out = _cloud_json(GEN_PROMPT.format(page=pn, filename=filename, n=per_page, text=pages[pn][:12000]))
        kept = 0
        for q in out.get("questions", []):
            ok = _quote_on(q.get("evidence", ""), pages[pn])
            if ok:
                qs.append({"doc": filename, "doc_id": d.id, "page": pn, "source": "generated", **q})
                kept += 1
        print(f"page {pn}: {kept}/{len(out.get('questions', []))} questions kept (evidence verified)", flush=True)
        _save(qs)
    print(f"{len(qs)} questions in {QFILE}")


def ask(question: str, filename: str) -> None:
    """Add a user's question: find the pages it points at, get the reference answer + verified quote."""
    d, pages = _doc(filename)
    from app.services.hybrid_search import _references
    from app.core.tenant import set_current_tenant_id
    set_current_tenant_id(TENANT)
    want = set()
    for kind, ident in _references(question):
        pat = re.compile(rf"(^|\n)\s*{kind}\s*{re.escape(ident)}(?![0-9A-Za-z])", re.I)
        want |= {n for n, t in pages.items() if pat.search(t)
                 and sum(1 for ln in t.split("\n") if re.match(r"\s*(table|chart|schedule)\s+\S+\s.*\s\d{1,3}\s*$", ln, re.I)) < 5}
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", question) if w.lower() not in
             {"which", "where", "their", "according", "specific", "listed", "maximum", "minimum", "percent", "exact"}]
    scored = sorted(pages, key=lambda n: -sum(pages[n].lower().count(w.lower()) for w in words))
    pick = sorted(want | set(scored[:6]))[:10]
    body = "\n\n".join(f"--- Page {n} ---\n{pages[n][:6000]}" for n in pick)
    out = _cloud_json(ANSWER_PROMPT.format(filename=filename, question=question, pages=body), 2000)
    pg = out.get("page")
    ok = bool(out.get("evidence")) and any(_quote_on(out["evidence"], pages[n]) for n in pick)
    print(f"reference: {out.get('answer')}\nevidence verified: {ok} (page {pg})")
    if ok:
        qs = _load()
        qs.append({"doc": filename, "doc_id": d.id, "page": pg, "source": "user", "question": question, **{
            k: out.get(k) for k in ("answer", "key_facts", "evidence")}})
        _save(qs)


BANK = ROOT / "logs" / "eval" / "question_bank.json"
BANK_RESULTS = ROOT / "logs" / "eval" / "question_bank_results.json"
BANK_PROMPT = """You are testing a document Q&A product used by accountants, tax agencies, immigration
lawyers and insurance agencies. For a US "{label}" document, list the {n} TOUGHEST realistic questions
a user would ask that are answerable from that one document alone.
Mix: exact field lookups (box / line numbers, codes), calculations across fields (totals, differences,
effective rates), multi-part questions, date reasoning (expiry, days remaining), and questions where a
weak system confuses similar fields (e.g. box 1 vs box 3 vs box 5 wages).
Do not use placeholders or outside information.
Return JSON only: {{"questions": [{{"question": "...", "skill": "lookup|calculation|multi_part|date|disambiguation"}}]}}"""


def bank(types: list, n: int) -> None:
    """Question bank per document TYPE. Only the type name is sent — never document text."""
    data = json.loads(BANK.read_text()) if BANK.exists() else {}
    for t in types:
        label = t.replace("_", " ").replace("w2", "W-2").replace("form ", "Form ")
        out = _cloud_json(BANK_PROMPT.format(label=label, n=n), 2000)
        data[t] = out.get("questions", [])
        print(f"{t}: {len(data[t])} questions", flush=True)
        BANK.write_text(json.dumps(data, indent=1))


def run_bank(types: list, per_type_docs: int) -> None:
    """Ask every bank question of real documents of that type (local model only, scoped to the
    document) and grade against the document's stored pages: answered + every figure on the page,
    answered but some figure not on the page, or gave up."""
    from sqlalchemy import text
    from app.services.document_react_agent import _grounding_note, _NOT_FOUND_RE
    data = json.loads(BANK.read_text())
    db = _db()
    res = json.loads(BANK_RESULTS.read_text()) if BANK_RESULTS.exists() else []
    done = {(r["doc_id"], r["question"]) for r in res}
    for t in types:
        docs = db.execute(text("""SELECT d.id::text id, d.filename, count(p.*) pages FROM documents d
            LEFT JOIN document_pages p ON p.document_id::text = d.id::text WHERE d.doc_type = :t AND d.status LIKE 'completed%'
            AND d.tenant_id::text = :tn GROUP BY 1, 2 ORDER BY 3 DESC LIMIT :k"""), {"t": t, "tn": TENANT, "k": per_type_docs}).fetchall()
        for d in docs:
            pages = "\n".join(r[0] for r in db.execute(text("SELECT text FROM document_pages WHERE document_id::text = :d "
                                                             "ORDER BY page_number"), {"d": d.id}))
            for q in data.get(t, []):
                if (d.id, q["question"]) in done:
                    continue
                got, secs = _local_answer(q["question"], doc_id=d.id)
                body = re.sub(r"\n---\n.*$", "", got, flags=re.S)
                if not body.strip() or _NOT_FOUND_RE.search(body) and len(re.findall(r"\d[\d,.]{2,}", body)) == 0:
                    grade = "GAVE_UP"
                elif _grounding_note(body, pages):
                    grade = "UNGROUNDED"
                else:
                    grade = "GROUNDED"
                res.append({"type": t, "doc_id": d.id, "filename": d.filename, "skill": q.get("skill"),
                            "question": q["question"], "answer": got, "grade": grade, "seconds": secs})
                BANK_RESULTS.write_text(json.dumps(res, indent=1))
                print(f"{grade:10} {secs:4}s  {t:18} {q.get('skill',''):14} {q['question'][:80]}", flush=True)
    n = len(res)
    if n:
        by = {g: sum(r["grade"] == g for r in res) for g in ("GROUNDED", "UNGROUNDED", "GAVE_UP")}
        print(f"\n{n} answers · grounded {by['GROUNDED']} ({by['GROUNDED']/n:.0%}) · figures not on page "
              f"{by['UNGROUNDED']} · gave up {by['GAVE_UP']} · avg {sum(r['seconds'] for r in res)/n:.0f}s")


def _local_answer(question: str, doc_id: str = None) -> tuple:
    import requests
    base = "http://localhost:7979"
    tok = requests.post(f"{base}/api/auth/login", json={"username": "admin", "password": "admin"}, timeout=30).json()["access_token"]
    t0, parts = time.time(), []
    params = {"question": question, "jwt": tok, "cloud": "local"}
    if doc_id:
        params["doc_ids"] = doc_id
    with requests.get(f"{base}/api/research/ask/stream", params=params,
                      stream=True, timeout=900) as r:
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except Exception:
                continue
            if ev.get("type") == "token":
                parts.append(ev.get("text", ""))
            elif ev.get("type") == "done":
                break
    return "".join(parts), round(time.time() - t0)


def _facts_hit(facts: list, got: str) -> float:
    if not facts:
        return 0.0
    g = _norm(got)
    return sum(1 for f in facts if _norm(str(f)) and _norm(str(f)) in g) / len(facts)


def run(limit: int) -> None:
    qs = _load()[:limit] if limit else _load()
    res = []
    for i, q in enumerate(qs, 1):
        got, secs = _local_answer(q["question"])
        hit = _facts_hit(q.get("key_facts") or [], got)
        judge = _cloud_json(JUDGE_PROMPT.format(question=q["question"], answer=q["answer"],
                                                facts=q.get("key_facts"), got=got[:3000]), 300)
        grade = judge.get("grade", "?")
        res.append({**q, "local_answer": got, "seconds": secs, "facts_found": round(hit, 2),
                    "grade": grade, "why": judge.get("why")})
        print(f"[{i}/{len(qs)}] {grade:7} facts {hit:.0%} {secs:4}s  p{q.get('page')}  {q['question'][:90]}", flush=True)
        RFILE.write_text(json.dumps(res, indent=1))
    n = len(res)
    by = {g: sum(1 for r in res if r["grade"] == g) for g in ("PASS", "PARTIAL", "FAIL")}
    print(f"\n{n} questions · PASS {by['PASS']} ({by['PASS']/n:.0%}) · PARTIAL {by['PARTIAL']} · FAIL {by['FAIL']} · "
          f"avg {sum(r['seconds'] for r in res)/n:.0f}s per question")


if __name__ == "__main__":
    import logging
    logging.disable(logging.WARNING)
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("--doc", required=True); g.add_argument("--pages", type=int, default=10)
    g.add_argument("--per-page", type=int, default=2)
    a = sub.add_parser("ask"); a.add_argument("question"); a.add_argument("--doc", required=True)
    r = sub.add_parser("run"); r.add_argument("--limit", type=int, default=0)
    b = sub.add_parser("bank"); b.add_argument("types", nargs="+"); b.add_argument("--n", type=int, default=8)
    rb = sub.add_parser("run-bank"); rb.add_argument("types", nargs="+"); rb.add_argument("--docs", type=int, default=1)
    x = ap.parse_args()
    if x.cmd == "generate":
        generate(x.doc, x.pages, x.per_page)
    elif x.cmd == "ask":
        ask(x.question, x.doc)
    elif x.cmd == "bank":
        bank(x.types, x.n)
    elif x.cmd == "run-bank":
        run_bank(x.types, x.docs)
    else:
        run(x.limit)
