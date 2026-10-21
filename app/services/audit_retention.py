"""
Audit trail retention: archive, then delete, rows older than each organisation's policy.

Two clocks per organisation (audit_retention):
  read_days    views, searches, AI questions        default 90   (30 / 90 / 365)
  change_days  changes and security events           default 2555 (365 / 1095 / 2555 / 0 = forever)
A legal hold pauses everything for that organisation.

Expired rows are written to <data dir>/audit-archive/<tenant>/<YYYY-MM-DD>-<kind>.jsonl.gz
first and deleted in the same transaction (the append-only trigger allows deletes only
with app.audit_purge = 'on'). Monthly partitions that end up empty are dropped, and
partitions for the coming months are created ahead of time.
"""
import gzip
import json
import logging
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text

from app.core.paths import UPLOADS_DIR

logger = logging.getLogger(__name__)

READ_CHOICES = [30, 90, 365]
CHANGE_CHOICES = [365, 1095, 2555, 0]          # 0 = keep forever
DEFAULTS = {"read_days": 90, "change_days": 2555, "legal_hold": False}
ARCHIVE_ROOT = Path(UPLOADS_DIR).parent / "audit-archive"
_BATCH = 5000

# Rows the middleware marks kind=read; everything else (explicit events, changes) is a change
_KIND_SQL = {"read": "extra->>'kind' = 'read'", "change": "coalesce(extra->>'kind', '') <> 'read'"}


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def ensure_tables(db) -> None:
    db.execute(text("""CREATE TABLE IF NOT EXISTS audit_retention (
        tenant_id TEXT PRIMARY KEY, read_days INTEGER NOT NULL DEFAULT 90, change_days INTEGER NOT NULL DEFAULT 2555,
        legal_hold BOOLEAN NOT NULL DEFAULT FALSE, last_run_at TIMESTAMPTZ, last_run_result JSONB,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by TEXT)"""))
    db.commit()


def policy(db, tenant_id: str) -> Dict[str, Any]:
    row = db.execute(text("SELECT read_days, change_days, legal_hold, last_run_at, last_run_result "
                          "FROM audit_retention WHERE tenant_id = :t"), {"t": tenant_id}).mappings().fetchone()
    return {**DEFAULTS, "last_run_at": None, "last_run_result": None, **(dict(row) if row else {})}


def _archive_and_delete(db, tid: str, kind: str, days: int) -> Dict[str, Any]:
    """Archive then delete one organisation's rows of one kind older than `days`."""
    folder = ARCHIVE_ROOT / tid
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{date.today().isoformat()}-{kind}.jsonl.gz"
    total = 0
    while True:
        rows = db.execute(text(f"""SELECT id, doc_id, action, actor, detail, ip, extra, created_at
            FROM audit_log WHERE tenant_id = CAST(:t AS uuid) AND {_KIND_SQL[kind]}
              AND created_at < now() - make_interval(days => :d)
            ORDER BY created_at LIMIT :n"""), {"t": tid, "d": days, "n": _BATCH}).mappings().fetchall()
        if not rows:
            break
        with gzip.open(path, "at", encoding="utf-8") as fh:            # append: several batches, several runs a day
            for r in rows:
                fh.write(json.dumps({**dict(r), "tenant_id": tid}, default=str) + "\n")
        # Delete exactly what was archived, in one transaction with the purge exception
        db.execute(text("SET LOCAL app.audit_purge = 'on'"))
        db.execute(text("DELETE FROM audit_log WHERE tenant_id = CAST(:t AS uuid) AND (id, created_at) IN "
                        "(SELECT unnest(CAST(:ids AS text[])), unnest(CAST(:ts AS timestamp[])))"),
                   {"t": tid, "ids": [r["id"] for r in rows], "ts": [r["created_at"] for r in rows]})
        db.commit()
        total += len(rows)
        if len(rows) < _BATCH:
            break
    return {"archived": total, "file": str(path) if total else None}


def _drop_empty_partitions(db) -> List[str]:
    """Past months with no rows left are dropped whole; the current and future months stay."""
    dropped = []
    parts = db.execute(text("""SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'audit_log' AND c.relname ~ '^audit_log_y[0-9]{4}m[0-9]{2}$'""")).fetchall()
    this_month = date.today().replace(day=1)
    for (name,) in parts:
        y, m = int(name[11:15]), int(name[16:18])
        if date(y, m, 1) >= this_month:
            continue
        if db.execute(text(f'SELECT NOT EXISTS (SELECT 1 FROM "{name}")')).scalar():
            db.execute(text(f'DROP TABLE "{name}"'))
            dropped.append(name)
    db.commit()
    return dropped


def run(tenant_id: str = None) -> Dict[str, Any]:
    """One cleanup pass for one organisation, or for every organisation with audit rows."""
    db = _db()
    try:
        ensure_tables(db)
        db.execute(text("SELECT udv_audit_ensure_partitions(now()::date, 3)"))
        db.commit()
        tenants = [tenant_id] if tenant_id else [r[0] for r in db.execute(text(
            "SELECT DISTINCT tenant_id::text FROM audit_log UNION SELECT tenant_id FROM audit_retention"))]
        report: Dict[str, Any] = {}
        for tid in tenants:
            pol = policy(db, tid)
            if pol["legal_hold"]:
                result = {"skipped": "legal hold"}
            else:
                result = {"read": _archive_and_delete(db, tid, "read", pol["read_days"])}
                result["change"] = (_archive_and_delete(db, tid, "change", pol["change_days"])
                                    if pol["change_days"] else {"archived": 0, "kept": "forever"})
            result["at"] = datetime.now(timezone.utc).isoformat()
            db.execute(text("""INSERT INTO audit_retention (tenant_id, last_run_at, last_run_result)
                VALUES (:t, now(), CAST(:r AS jsonb)) ON CONFLICT (tenant_id) DO UPDATE
                SET last_run_at = now(), last_run_result = EXCLUDED.last_run_result"""),
                {"t": tid, "r": json.dumps(result)})
            db.commit()
            report[tid] = result
        report["_dropped_partitions"] = _drop_empty_partitions(db)
        logger.info(f"🧹 Audit retention: {report}")
        return report
    finally:
        db.close()


# ── Nightly schedule ─────────────────────────────────────────────────────────

_started = False


def _loop() -> None:
    time.sleep(120)                      # let startup finish
    while True:
        try:
            run()
        except Exception as e:
            logger.error(f"❌ Audit retention run failed: {e}")
        try:
            from app.api.approvals_router import expire_stale
            from app.api.clm_router import advance_expiring
            logger.info(f"🗓 Nightly: {expire_stale()} stale approvals expired, "
                        f"{advance_expiring()} contracts moved to expiring")
        except Exception as e:
            logger.error(f"❌ Nightly approvals / contracts job failed: {e}")
        time.sleep(24 * 3600)


def start_scheduler() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, name="udv-audit-retention", daemon=True).start()
        logger.info("✅ Audit retention scheduler started (daily)")
