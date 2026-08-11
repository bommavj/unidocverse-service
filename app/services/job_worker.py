"""
Job Queue Worker — async document processing pipeline.

Why:
  Document processing (parse → chunk → embed) takes 10-120s.
  Running it synchronously in the upload endpoint causes:
    - Request timeouts for large files
    - Server blocked for all other users during processing
    - No retry on failure — partial processing leaves doc in broken state

How:
  Upload endpoint:  saves file + enqueues job → returns immediately (< 1s)
  Worker loop:      polls job_queue every 5s → processes one job at a time
                    retries up to max_attempts on failure
                    marks exact failure stage so resume is possible

Usage:
  Start worker:   python -m app.services.job_worker
  Or via FastAPI lifespan: starts as background thread automatically
"""

import json
import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5
WORKER_TIMEOUT_SECONDS = 300  # job running > 5min = stuck, release it


# ── Job registration ──────────────────────────────────────────────────────────

def enqueue(job_type: str, payload: dict, priority: int = 5, max_attempts: int = 3) -> str:
    """
    Add a job to the queue. Returns the job id.
    Call this from upload endpoints instead of processing inline.

    Priority: 1=urgent, 5=normal, 10=background
    """
    db = SessionLocal()
    try:
        row = db.execute(text("""
            INSERT INTO job_queue (job_type, payload, priority, max_attempts)
            VALUES (:job_type, CAST(:payload AS JSONB), :priority, :max_attempts)
            RETURNING id
        """), {
            "job_type":     job_type,
            "payload":      json.dumps(payload),
            "priority":     priority,
            "max_attempts": max_attempts,
        }).fetchone()
        db.commit()
        job_id = str(row.id)
        logger.info(f"[JobQueue] Enqueued {job_type} job={job_id} priority={priority}")
        return job_id
    finally:
        db.close()


def get_job_status(job_id: str) -> Optional[dict]:
    """Poll job status from the API — used by frontend to show progress."""
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT id, job_type, status, attempts, max_attempts,
                   error_message, created_at, started_at, completed_at, payload
            FROM job_queue WHERE id = :id
        """), {"id": job_id}).fetchone()
        if not row:
            return None
        return {
            "id":            str(row.id),
            "job_type":      row.job_type,
            "status":        row.status,
            "attempts":      row.attempts,
            "max_attempts":  row.max_attempts,
            "error_message": row.error_message,
            "created_at":    row.created_at.isoformat() if row.created_at else None,
            "started_at":    row.started_at.isoformat() if row.started_at else None,
            "completed_at":  row.completed_at.isoformat() if row.completed_at else None,
            "payload":       row.payload or {},
        }
    finally:
        db.close()


# ── Job handlers ──────────────────────────────────────────────────────────────

def _handle_process_document(payload: dict, db: Session) -> None:
    """
    Re-process a document through the full pipeline.
    payload: { "document_id": "uuid", "reembed": false }
    """
    doc_id = payload.get("document_id")
    if not doc_id:
        raise ValueError("Missing document_id in payload")

    # Import here to avoid circular imports at module load time
    from app.agents.langgraph_agents import run_document_pipeline

    logger.info(f"[JobWorker] Processing document {doc_id}")

    # Update processing stage on the document
    db.execute(text("""
        UPDATE documents SET processing_stage = 'queued', status = 'processing'
        WHERE id = :id
    """), {"id": doc_id})
    db.commit()

    try:
        run_document_pipeline(doc_id)
        db.execute(text("""
            UPDATE documents SET processing_stage = 'done', status = 'completed'
            WHERE id = :id
        """), {"id": doc_id})
        db.commit()
    except Exception as e:
        db.execute(text("""
            UPDATE documents SET processing_stage = 'error', status = 'failed'
            WHERE id = :id
        """), {"id": doc_id})
        db.commit()
        raise


def _handle_reembed_document(payload: dict, db: Session) -> None:
    """Re-embed chunks for a document (e.g. after model change)."""
    doc_id = payload.get("document_id")
    if not doc_id:
        raise ValueError("Missing document_id in payload")

    from app.config.model_factory import EmbeddingModelFactory
    model = EmbeddingModelFactory.get_model()

    logger.info(f"[JobWorker] Re-embedding document {doc_id}")
    rows = db.execute(text("""
        SELECT id, chunk_text FROM document_chunks WHERE document_id = :doc_id
    """), {"doc_id": doc_id}).fetchall()

    for row in rows:
        emb = model.encode(row.chunk_text).tolist()
        db.execute(text("""
            UPDATE document_chunks SET embedding = CAST(:emb AS vector)
            WHERE id = :id
        """), {"emb": str(emb), "id": row.id})
    db.commit()
    logger.info(f"[JobWorker] Re-embedded {len(rows)} chunks for doc {doc_id}")


# Map job_type → handler function
JOB_HANDLERS = {
    "process_document":  _handle_process_document,
    "reembed_document":  _handle_reembed_document,
}


# ── Worker loop ───────────────────────────────────────────────────────────────

def _claim_next_job(db: Session) -> Optional[dict]:
    """
    Atomically claim the next available job.
    FOR UPDATE SKIP LOCKED ensures two workers never grab the same job.
    Also releases jobs that have been running too long (stuck worker).
    """
    now = datetime.now(timezone.utc)

    # First: release stuck jobs (running > WORKER_TIMEOUT_SECONDS)
    db.execute(text("""
        UPDATE job_queue
        SET status = 'failed',
            error_message = 'Worker timeout — job exceeded max runtime',
            next_retry_at = NOW() + INTERVAL '30 seconds'
        WHERE status = 'running'
          AND started_at < NOW() - INTERVAL ':timeout seconds'
          AND attempts < max_attempts
    """.replace(":timeout seconds", f"{WORKER_TIMEOUT_SECONDS} seconds")))
    db.commit()

    # Claim next pending/retryable job
    row = db.execute(text("""
        SELECT id, job_type, payload, attempts, max_attempts
        FROM job_queue
        WHERE status IN ('pending', 'failed')
          AND attempts < max_attempts
          AND next_retry_at <= NOW()
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """)).fetchone()

    if not row:
        return None

    db.execute(text("""
        UPDATE job_queue
        SET status = 'running',
            started_at = NOW(),
            attempts = attempts + 1
        WHERE id = :id
    """), {"id": row.id})
    db.commit()

    return {
        "id":           str(row.id),
        "job_type":     row.job_type,
        "payload":      row.payload or {},
        "attempts":     row.attempts + 1,
        "max_attempts": row.max_attempts,
    }


def _complete_job(db: Session, job_id: str) -> None:
    db.execute(text("""
        UPDATE job_queue
        SET status = 'completed', completed_at = NOW()
        WHERE id = :id
    """), {"id": job_id})
    db.commit()


def _fail_job(db: Session, job_id: str, error: str, attempts: int, max_attempts: int) -> None:
    if attempts >= max_attempts:
        # Exhausted retries — mark permanently failed
        db.execute(text("""
            UPDATE job_queue
            SET status = 'failed', error_message = :err, completed_at = NOW()
            WHERE id = :id
        """), {"id": job_id, "err": error[:2000]})
    else:
        # Schedule retry with exponential backoff: 30s, 2min, 8min
        backoff = 30 * (4 ** (attempts - 1))
        db.execute(text(f"""
            UPDATE job_queue
            SET status = 'failed',
                error_message = :err,
                next_retry_at = NOW() + INTERVAL '{backoff} seconds'
            WHERE id = :id
        """), {"id": job_id, "err": error[:2000]})
    db.commit()


def run_worker(stop_event=None) -> None:
    """
    Main worker loop. Runs until stop_event is set (or forever if None).
    Designed to run in a background thread or as a standalone process.
    """
    logger.info("[JobWorker] Started — polling every %ds", POLL_INTERVAL_SECONDS)

    while True:
        if stop_event and stop_event.is_set():
            logger.info("[JobWorker] Stop requested — shutting down")
            break

        db = SessionLocal()
        try:
            job = _claim_next_job(db)
            if not job:
                # Nothing to do — sleep then poll again
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            job_id   = job["id"]
            job_type = job["job_type"]
            logger.info(f"[JobWorker] Running job={job_id} type={job_type} attempt={job['attempts']}/{job['max_attempts']}")

            handler = JOB_HANDLERS.get(job_type)
            if not handler:
                _fail_job(db, job_id, f"Unknown job type: {job_type}", job["attempts"], job["max_attempts"])
                continue

            try:
                handler(job["payload"], db)
                _complete_job(db, job_id)
                logger.info(f"[JobWorker] Completed job={job_id} type={job_type}")
            except Exception as e:
                err = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
                logger.error(f"[JobWorker] Failed job={job_id}: {err}")
                _fail_job(db, job_id, err, job["attempts"], job["max_attempts"])

        except Exception as e:
            logger.error(f"[JobWorker] Worker loop error: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)
        finally:
            db.close()


# ── FastAPI integration ───────────────────────────────────────────────────────

import threading

_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def start_background_worker() -> None:
    """Call from FastAPI lifespan to start the worker as a daemon thread."""
    global _worker_thread
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=run_worker,
        args=(_stop_event,),
        daemon=True,
        name="job-worker",
    )
    _worker_thread.start()
    logger.info("[JobWorker] Background worker thread started")


def stop_background_worker() -> None:
    """Call from FastAPI lifespan shutdown."""
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=10)
    logger.info("[JobWorker] Background worker stopped")


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_worker()
