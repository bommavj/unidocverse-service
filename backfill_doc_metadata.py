"""
Backfill doc_metadata for all existing completed documents.

Runs _extract_doc_metadata (LLM classify + regex fact scan) on every
document that has chunks but no pay_period in doc_metadata.

Usage:
    cd unidocverse-service
    source venv/bin/activate
    python backfill_doc_metadata.py
    python backfill_doc_metadata.py --all          # reprocess even docs that already have metadata
    python backfill_doc_metadata.py --dry-run      # show what would be extracted, don't save
"""
import argparse
import json
import sys
import time

from sqlalchemy import text

from app.core.database import SessionLocal
from app.agents.langgraph_agents import _extract_doc_metadata

parser = argparse.ArgumentParser()
parser.add_argument("--all",     action="store_true", help="Reprocess all docs, not just missing ones")
parser.add_argument("--dry-run", action="store_true", help="Print extracted metadata without saving")
args = parser.parse_args()

db = SessionLocal()

try:
    # Fetch all completed documents with their chunk text
    if args.all:
        docs = db.execute(text(
            "SELECT id, filename, doc_metadata FROM documents WHERE status='completed' ORDER BY created_at DESC"
        )).fetchall()
    else:
        # Only docs missing pay_period in metadata
        docs = db.execute(text("""
            SELECT id, filename, doc_metadata
            FROM documents
            WHERE status = 'completed'
              AND (doc_metadata IS NULL
                   OR doc_metadata->>'pay_period' IS NULL)
            ORDER BY created_at DESC
        """)).fetchall()

    print(f"Found {len(docs)} document(s) to process\n")

    ok = 0
    skipped = 0
    failed = 0

    for row in docs:
        doc_id   = row.id
        filename = row.filename

        # Fetch all chunk text for this document
        chunks = db.execute(text("""
            SELECT chunk_text FROM document_chunks
            WHERE document_id = :id
              AND chunk_text IS NOT NULL
            ORDER BY chunk_index
        """), {"id": doc_id}).fetchall()

        if not chunks:
            print(f"  SKIP  {filename}  (no chunks)")
            skipped += 1
            continue

        all_text = "\n\n".join(c.chunk_text for c in chunks if c.chunk_text)

        try:
            t0 = time.time()
            extracted = _extract_doc_metadata(all_text)
            elapsed = round(time.time() - t0, 1)

            if not extracted:
                print(f"  EMPTY {filename}  ({elapsed}s — nothing extracted)")
                skipped += 1
                continue

            summary = f"subtype={extracted.get('doc_subtype')} period={extracted.get('pay_period')} amounts={list(extracted.get('key_amounts', {}).keys())}"
            print(f"  OK    {filename}  ({elapsed}s)  {summary}")

            if not args.dry_run:
                existing = row.doc_metadata or {}
                merged = {**existing, **extracted}
                db.execute(
                    text("UPDATE documents SET doc_metadata = :m WHERE id = :id"),
                    {"m": json.dumps(merged), "id": doc_id}
                )
                db.commit()
            ok += 1

        except Exception as e:
            print(f"  FAIL  {filename}  {e}")
            db.rollback()
            failed += 1

    print(f"\nDone — {ok} updated, {skipped} skipped, {failed} failed")
    if args.dry_run:
        print("(dry-run mode — no changes saved)")

finally:
    db.close()
