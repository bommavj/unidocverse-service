"""
Duplicate upload detection: the same file (byte-for-byte, SHA-256) uploaded again by
the same organisation. Uploads from a person get a choice (open the existing document
or upload anyway); automatic imports (watch folders, Gmail, batch) skip duplicates.
"""
import hashlib
import logging
import threading
from pathlib import Path
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)
_READY = False


def ensure_column(db) -> None:
    global _READY
    if _READY:
        return
    # Normally created by migration V65. Only touch the schema when it's really missing: even an
    # "IF NOT EXISTS" ALTER queues for an exclusive lock and freezes every query on documents
    # behind it while any long read is open. Give up after 3 s rather than block the app.
    has_col = db.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name = 'documents' "
                              "AND column_name = 'content_hash'")).fetchone()
    has_idx = db.execute(text("SELECT to_regclass('public.idx_documents_tenant_hash')")).scalar()
    if not (has_col and has_idx):
        try:
            db.execute(text("SET LOCAL lock_timeout = '3s'"))
            if not has_col:
                db.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT"))
            if not has_idx:
                db.execute(text("CREATE INDEX IF NOT EXISTS idx_documents_tenant_hash ON documents "
                                "(tenant_id, content_hash) WHERE content_hash IS NOT NULL"))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"content_hash column/index not added now ({e}); will retry later")
            return
    else:
        db.commit()
    _READY = True


def fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def find(db, tenant_id: str, content_hash: str) -> Optional[dict]:
    """The organisation's existing document with these exact bytes, if any."""
    ensure_column(db)
    row = db.execute(text("""SELECT id::text AS id, filename, uploaded_at, status FROM documents
        WHERE tenant_id::text = :t AND content_hash = :h ORDER BY uploaded_at LIMIT 1"""),
        {"t": str(tenant_id), "h": content_hash}).mappings().fetchone()
    if not row:
        return None
    return {"id": row["id"], "filename": row["filename"], "status": row["status"],
            "uploaded_at": row["uploaded_at"].isoformat() if row["uploaded_at"] else None}


def remember(db, doc_id: str, content_hash: str) -> None:
    ensure_column(db)
    db.execute(text("UPDATE documents SET content_hash = :h WHERE id::text = :d"), {"h": content_hash, "d": str(doc_id)})
    db.commit()


def backfill() -> int:
    """Fingerprint existing documents whose file is still on disk (runs once at startup)."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    done = 0
    try:
        ensure_column(db)
        rows = db.execute(text("SELECT id::text, file_path FROM documents WHERE content_hash IS NULL "
                               "AND file_path IS NOT NULL")).fetchall()
        for doc_id, path in rows:
            p = Path(path)
            if p.is_file():
                h = hashlib.sha256()
                with p.open("rb") as fh:
                    for block in iter(lambda: fh.read(1 << 20), b""):
                        h.update(block)
                db.execute(text("UPDATE documents SET content_hash = :h WHERE id::text = :d"),
                           {"h": h.hexdigest(), "d": doc_id})
                done += 1
        db.commit()
        if done:
            logger.info(f"🔁 Fingerprinted {done} existing documents for duplicate detection")
        return done
    except Exception as e:
        logger.warning(f"Duplicate-detection backfill skipped: {e}")
        return done
    finally:
        db.close()


def start_backfill() -> None:
    threading.Thread(target=backfill, name="udv-dup-backfill", daemon=True).start()
