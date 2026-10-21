#!/usr/bin/env python3
"""
Retrieval benchmark: can search find each minute detail in every document?

  venv-arm64/bin/python tests/eval/retrieval_bench.py --build     # sample facts from every PDF page (once)
  venv-arm64/bin/python tests/eval/retrieval_bench.py --name before
  venv-arm64/bin/python tests/eval/retrieval_bench.py --name after

A fact is a line on a real page that has a label and a value ("Countywide Tax (Secured)
1.00000000% $7,643.88"). The query is the label plus the document's type; a fact is found
when search's top 8 passages include that document, and one of them contains the value.
Page is checked too (the passage's page_number equals the fact's page). The sampled facts
are fixed in logs/eval/retrieval_facts.json so before/after compare the same questions.
No LLM is involved: this measures only storage + search.
"""
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FACTS = ROOT / "logs" / "eval" / "retrieval_facts.json"
_VALUE = re.compile(r"\$?\d[\d,]*\.\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{3,}[-\d]*\b|\b[A-Z]{2,4}\d{6,}\b")
_PER_DOC = 6


def _norm(s: str) -> str:
    return re.sub(r"[\s,$]", "", s or "").lower()


def build() -> None:
    import fitz
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.services.page_ingest import pdf_pages
    db = SessionLocal()
    docs = db.execute(text("""SELECT id::text, filename, coalesce(doc_type, '') doc_type, file_path FROM documents
        WHERE status = 'completed' AND lower(filename) LIKE '%.pdf' ORDER BY filename""")).fetchall()
    rnd, facts = random.Random(7), []
    for d in docs:
        try:
            pages = pdf_pages(d.file_path)
        except Exception as e:
            print("skip", d.filename, e)
            continue
        cands = []
        for p in pages:
            for line in p.text.split("\n"):
                if line.startswith("[Table rows]"):
                    continue
                vals = _VALUE.findall(line)
                label = _VALUE.sub(" ", line)
                label = re.sub(r"[|:$%()]+", " ", label)
                words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", label)]
                if vals and len(words) >= 2 and len(line) < 160:
                    cands.append({"page": p.number, "label": " ".join(words[:10]), "value": vals[-1], "line": line})
        uniq = {}
        for c in cands:
            uniq.setdefault(c["label"].lower(), c)
        pick = rnd.sample(list(uniq.values()), min(_PER_DOC, len(uniq)))
        for c in pick:
            facts.append({"doc_id": d.id, "filename": d.filename, "doc_type": d.doc_type, **c})
        print(f"{len(pick):2} facts  {d.filename}")
    FACTS.parent.mkdir(parents=True, exist_ok=True)
    FACTS.write_text(json.dumps(facts, indent=1))
    print(f"\n{len(facts)} facts from {len(docs)} PDFs → {FACTS}")


def run(name: str) -> None:
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.core.tenant import set_current_tenant_id
    from app.services.hybrid_search import hybrid_search
    set_current_tenant_id("00000000-0000-0000-0000-000000000001")
    facts = json.loads(FACTS.read_text())
    db = SessionLocal()
    res, t0 = [], time.time()
    tenants = {r[0]: r[1] for r in db.execute(text("SELECT id::text, tenant_id::text FROM documents"))}
    for i, f in enumerate(facts, 1):
        set_current_tenant_id(tenants.get(f["doc_id"], "00000000-0000-0000-0000-000000000001"))
        q = f"{f['label']} {f['doc_type'].replace('_', ' ')}".strip()
        hits = hybrid_search(q, top_n=8)
        doc_hit = any(h.get("doc_id") == f["doc_id"] for h in hits)
        val_hit, page_hit = False, False
        for h in hits:
            if h.get("doc_id") != f["doc_id"]:
                continue
            row = db.execute(text("SELECT chunk_text, page_number FROM document_chunks WHERE document_id = :d "
                                  "AND chunk_index = :i"), {"d": f["doc_id"], "i": h.get("chunk_index")}).fetchone()
            body = row.chunk_text if row else h.get("text", "")
            if _norm(f["value"]) in _norm(body):
                val_hit = True
                page_hit = page_hit or (row is not None and row.page_number == f["page"])
        db.rollback()                           # never hold a read transaction open between queries
        res.append({**f, "query": q, "doc_hit": doc_hit, "value_hit": val_hit, "page_hit": page_hit})
        if i % 25 == 0:
            print(f"  {i}/{len(facts)}  {time.time() - t0:.0f}s", flush=True)
    n = len(res)
    s = {k: sum(r[k] for r in res) for k in ("doc_hit", "value_hit", "page_hit")}
    print(f"\n{name}: {n} facts · right document {s['doc_hit']}/{n} ({s['doc_hit']/n:.0%}) · "
          f"exact value found {s['value_hit']}/{n} ({s['value_hit']/n:.0%}) · right page {s['page_hit']}/{n} ({s['page_hit']/n:.0%})")
    by = {}
    for r in res:
        b = by.setdefault(r["filename"], [0, 0])
        b[0] += r["value_hit"]; b[1] += 1
    for fn, (ok, tot) in sorted(by.items(), key=lambda x: x[1][0] / x[1][1]):
        print(f"  {ok}/{tot}  {fn}")
    out = ROOT / "logs" / "eval" / f"retrieval_{name}.json"
    out.write_text(json.dumps(res, indent=1, default=str))
    print(f"→ {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--name", default="run")
    a = ap.parse_args()
    import logging
    logging.disable(logging.WARNING)
    build() if a.build else run(a.name)
