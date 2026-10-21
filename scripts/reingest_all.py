#!/usr/bin/env python3
"""
Re-ingest every document with page-accurate storage (app/services/page_ingest.py).

  venv-arm64/bin/python scripts/reingest_all.py              # pages + chunks + embeddings
  venv-arm64/bin/python scripts/reingest_all.py --facts      # also re-extract structured facts from the new text
  venv-arm64/bin/python scripts/reingest_all.py --doc <id>   # one document

Classification, summaries and the original files are left as they are.
"""
import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", action="store_true")
    ap.add_argument("--doc")
    ap.add_argument("--types", help="comma-separated doc types to re-ingest (e.g. w2,passport)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.services import page_ingest

    db = SessionLocal()
    q = "SELECT id::text, filename, doc_type, file_path, raw_text FROM documents WHERE status LIKE 'completed%'"
    if a.types:
        q += " AND doc_type = ANY(:types)"
    rows = db.execute(text(q + (" AND id::text = :d" if a.doc else "") + " ORDER BY created_at"),
                      {"d": a.doc, "types": (a.types or "").split(",")}).fetchall()
    db.close()
    from app.config.model_factory import EmbeddingModelFactory
    model = EmbeddingModelFactory.get_model()
    t0, totals = time.time(), {"pages": 0, "ocr_pages": 0, "chunks": 0, "failed": 0}
    for i, r in enumerate(rows, 1):
        try:
            path = r.file_path if r.file_path and Path(r.file_path).exists() else None
            s = page_ingest.ingest(r.id, path, r.filename, fallback_text=r.raw_text or "",
                                   embed=lambda xs, batch_size=32: model.encode(xs, batch_size=batch_size),
                                   doc_type=r.doc_type)
            for k in ("pages", "ocr_pages", "chunks"):
                totals[k] += s[k]
            line = f"[{i}/{len(rows)}] {s['pages']:>4} pages ({s['ocr_pages']} OCR) {s['chunks']:>4} chunks {s['seconds']['total']:>5}s  {r.filename}"
            if a.facts and r.doc_type:
                from app.services.facts_extractor import extract_and_store
                d2 = SessionLocal()
                new_text = d2.execute(text("SELECT raw_text FROM documents WHERE id::text = :d"), {"d": r.id}).scalar()
                d2.close()
                ok = extract_and_store(r.id, r.doc_type, new_text or "", r.filename)
                line += f"  facts={'ok' if ok else 'none'}"
            print(line, flush=True)
        except Exception as e:
            totals["failed"] += 1
            print(f"[{i}/{len(rows)}] FAILED {r.filename}: {e}", flush=True)
    print(f"\nDone in {time.time() - t0:.0f}s: {totals}")


if __name__ == "__main__":
    main()
