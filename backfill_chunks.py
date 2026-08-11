"""
Re-chunk existing documents using the new table-aware chunker.
Run after updating _chunk_text in qa_service.py.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.services.qa_service import store_document_chunks

db = SessionLocal()

rows = db.execute(text("""
    SELECT id, filename, doc_type, raw_text
    FROM documents
    WHERE raw_text IS NOT NULL
    ORDER BY created_at DESC
""")).fetchall()

print(f"Found {len(rows)} documents to re-chunk")

for row in rows:
    print(f"\n→ {row.filename} ({row.doc_type})")
    n = store_document_chunks(str(row.id), row.raw_text)
    print(f"  ✅ {n} chunks stored")

db.close()
print("\nRe-chunk complete.")
