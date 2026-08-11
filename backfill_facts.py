"""
One-time backfill script — extracts facts for existing documents that
have raw_text but no extracted_facts yet.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.services.facts_extractor import extract_and_store

SUPPORTED_DOC_TYPES = [
    "credit_card_statement", "bank_statement",
    "pay_stub", "payroll_report",
    "invoice", "purchase_order",
    "annual_report", "quarterly_report", "financial_statement",
    "contract", "legal",
    "medical", "medical_record",
]

db = SessionLocal()

rows = db.execute(text("""
    SELECT id, filename, doc_type, raw_text
    FROM documents
    WHERE extracted_facts IS NULL
      AND raw_text IS NOT NULL
      AND doc_type = ANY(:types)
    ORDER BY created_at DESC
"""), {"types": SUPPORTED_DOC_TYPES}).fetchall()

print(f"Found {len(rows)} documents to backfill")

for row in rows:
    print(f"\n→ {row.filename} ({row.doc_type})")
    ok = extract_and_store(str(row.id), row.doc_type, row.raw_text)
    print(f"  {'✅ done' if ok else '❌ failed'}")

db.close()
print("\nBackfill complete.")
