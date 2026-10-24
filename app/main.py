import multiprocessing
import sys

# Frozen desktop build: worker processes spawned by torch/docling/tokenizers re-run
# this file. freeze_support() makes them run their task and exit instead of
# booting a second copy of the whole app (which then finds its port taken).
if getattr(sys, "frozen", False):
    multiprocessing.freeze_support()

from dotenv import load_dotenv
import os
load_dotenv()

# ── MUST BE FIRST — before any torch/paddle imports ──────────────────────────
os.environ["USE_PADDLEX"] = "0"
os.environ["PADDLEOCR_USE_PADDLEX"] = "0"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "true"
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_allocator_strategy"] = "naive_best_fit"
os.environ["PYTORCH_MPS_DISABLE"] = "1"
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"

import sys
import logging
import asyncio
import socket
from pathlib import Path
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware as GzipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.langgraph_agents import ollama_client
from app.agents.workflow import compile_workflow
from app.api import (
    documents, analytics, search, stats, upload, uni_license,
    llm_models, insights, ask_router, alerts_router,
    notifications_router, auth_router, clients_router,
    policies_router, features_router, agency_router,
    commission_router, gap_analysis_router, coi_router,
    loss_run_router, renewal_router, agency_settings_router,
    gmail_api, watch_folders, calendar_router, personal_router,
    domains_router, integrations_router)
from app.api import agent_reports_router

from app.api.progress import router as progress_router
from app.api.whatsapp_qr_router import router as whatsapp_qr_router
from app.api.agent_router import router as agent_router
from app.api.mcp_settings_router import router as mcp_settings_router
from app.api.guardrails_router import router as guardrails_router
from app.api.immigration_router import router as immigration_router
from app.api.vendor_compliance_router import router as vendor_compliance_router
from app.api.immigration_suite_router import router as immigration_suite_router, public as immigration_public_router
# Feature routers that existed but were never registered (their pages showed "not found" → empty)
from app.api.claims_router import router as claims_router
from app.api.matters_router import router as matters_router
from app.api.quote_router import router as quote_router
from app.api.clm_router import router as clm_router
from app.api.audit_router import router as audit_router
from app.api.risk_router import router as risk_router
from app.api.jurisdiction_router import router as jurisdiction_router
from app.api.export_router import router as export_router
from app.api.insurance_automation_router import router as insurance_automation_router
from app.api.qa_history_router import router as qa_history_router
from app.api.tts_router import router as tts_router
from app.api.research_router import router as research_router
from app.api.workflow_router import router as workflow_router
from app.api.domain_workflow_router import router as domain_workflow_router
from app.api.approvals_router import router as approvals_router
from app.api.chatbot_router import router as chatbot_router
from app.api.auth_router import get_current_user as _auth_required
from app.core.performance import llm_queue, get_perf_stats, job_registry
from app.mcp_server.server import mcp as mcp_server
from app.core import config
from app.core.config import settings
from app.core.database import engine, get_db, SessionLocal
from app.core.tesseract_config import configure_tesseract
from app.models.document import Document
from app.services.preload import preload_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

langgraph_app = None


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global langgraph_app
    logger.info("🚀 Starting UniDocVerse AI Service...")

    # Preload models
    asyncio.create_task(preload_manager.preload_all())

    from app.services.classifiers import zero_shot_router
    asyncio.get_running_loop().run_in_executor(None, zero_shot_router.warm_up)
    from app.services import hybrid_search
    asyncio.get_running_loop().run_in_executor(None, hybrid_search.warm_up)
    from app.services import model_fetch
    model_fetch.start()          # Starter edition: fetch the search model on first launch

    # Start background schedulers (renewal emails, follow-ups, etc.)
    from app.services import scheduler
    scheduler.start()

    configure_tesseract()

    # DB check
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ PostgreSQL connection successful")
        
        # Initialize DB tables
        from app.core.database import init_db
        init_db()

        # Background jobs die with the process; don't leave documents stuck in
        # "processing" forever after a restart.
        with engine.begin() as conn:
            rec = conn.execute(text("""
                UPDATE documents d SET status = 'completed', processing_stage = 'enrichment_failed'
                 WHERE d.status IN ('processing', 'pending')
                   AND EXISTS (SELECT 1 FROM document_chunks c WHERE c.document_id = d.id)
            """)).rowcount
            lost = conn.execute(text("""
                UPDATE documents SET status = 'failed', processing_stage = 'interrupted',
                       doc_metadata = COALESCE(doc_metadata, '{}'::jsonb) ||
                                      '{"error": "Interrupted by a server restart - please reprocess"}'::jsonb
                 WHERE status IN ('processing', 'pending')
            """)).rowcount
            if rec or lost:
                logger.warning(f"♻️ Startup recovery: {rec} interrupted document(s) kept searchable, "
                               f"{lost} marked for reprocessing")
    except Exception as e:
        logger.error(f"❌ Database connection failed/initialization failed: {e}")
        raise

    # LangGraph workflow
    try:
        logger.info("🤖 Compiling LangGraph workflow...")
        langgraph_app = compile_workflow()
        logger.info("✅ LangGraph workflow ready")
    except Exception as e:
        logger.error(f"❌ Failed to compile LangGraph workflow: {e}")
        raise

    # Start LLM request queue worker
    await llm_queue.start_worker()
    logger.info("✅ LLM request queue worker started")

    # Start document job queue worker (async processing, retry on failure)
    from app.services.job_worker import start_background_worker
    start_background_worker()
    logger.info("✅ Document job queue worker started")
    from app.services.webhooks import start_sender
    start_sender()
    from app.services.audit_retention import start_scheduler
    start_scheduler()
    from app.services.duplicates import start_backfill
    start_backfill()

    logger.info("✅ Server ready at http://localhost:7979")

    # ── Auto-activate 90-day trial on first launch ────────────────────────
    try:
        from app.core.activation import create_trial_activation, get_trial_status
        from app.license.license_paths import get_license_path
        if not get_license_path().exists():
            trial = create_trial_activation()
            status = get_trial_status(trial)
            logger.info(f"✅ 90-day trial activated — expires: {trial['expiry_at'][:10]}")
        else:
            logger.info("✅ License already active")
    except Exception as _e:
        logger.warning(f"⚠ Trial auto-activation skipped: {_e}")
    yield
    # Stop job worker cleanly on shutdown
    from app.services.job_worker import stop_background_worker
    stop_background_worker()
    logger.info("👋 Shutting down UniDocVerse AI Service...")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compress responses > 1 KB — except SSE streams, which must flush per event
from app.core.gzip_sse import GZipExceptEventStream
app.add_middleware(GZipExceptEventStream, minimum_size=1000)

# Tenant isolation — extracts tenant_id from JWT/header, stores in context
from app.core.tenant import TenantMiddleware
app.add_middleware(TenantMiddleware)
# Every create / update / delete through the API is written to audit_log
from app.core.audit_middleware import AuditMiddleware
app.add_middleware(AuditMiddleware)

# ── Global rate limit middleware (sliding window, in-process) ─────────────────
import time as _time
from collections import defaultdict as _dd
_ip_windows: dict = _dd(list)
_RATE_LIMIT = 120           # requests per window
_RATE_WINDOW = 60           # seconds
_ASK_LIMIT   = 15           # tighter limit for LLM endpoints
_ASK_WINDOW  = 60

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    now = _time.monotonic()
    path = request.url.path

    # Choose limits
    if "/ask" in path:
        limit, window = _ASK_LIMIT, _ASK_WINDOW
    else:
        limit, window = _RATE_LIMIT, _RATE_WINDOW

    key = f"{ip}:{limit}"
    calls = _ip_windows[key]
    _ip_windows[key] = [t for t in calls if now - t < window]

    if len(_ip_windows[key]) >= limit:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please slow down."},
            headers={"Retry-After": str(window)},
        )

    _ip_windows[key].append(now)
    return await call_next(request)


# ── Routers ───────────────────────────────────────────────────────────────────

_protected = {"dependencies": [Depends(_auth_required)]}

from app.api.brief_router import router as brief_router
app.include_router(brief_router, **_protected)    # names and policy numbers: signed-in only
app.include_router(auth_router.router)           # public — has own auth logic
app.include_router(chatbot_router)               # public — token-based auth per endpoint
app.include_router(documents.router,             **_protected)
app.include_router(analytics.router,             **_protected)
app.include_router(search.router,                **_protected)
app.include_router(stats.router,                 **_protected)
app.include_router(upload.router,                **_protected)
app.include_router(uni_license.router,           **_protected)
app.include_router(progress_router,              **_protected)
app.include_router(watch_folders.router,         **_protected)
app.include_router(gmail_api.router,             **_protected)
app.include_router(llm_models.router,            **_protected)
app.include_router(insights.router,              **_protected)
app.include_router(ask_router.router,            **_protected)
app.include_router(alerts_router.router,         **_protected)
app.include_router(notifications_router.router,  **_protected)
app.include_router(integrations_router.router,   **_protected)
app.include_router(whatsapp_qr_router,           **_protected)
app.include_router(clients_router.router,        **_protected)
app.include_router(policies_router.router,       **_protected)
app.include_router(features_router.router,       **_protected)
from app.api import llm_providers_router
app.include_router(llm_providers_router.router,  **_protected)
from app.api import web_search_router
from app.api import setup_router
from app.api import pilot_router
from app.api import feedback_router
app.include_router(feedback_router.router, **_protected)
app.include_router(pilot_router.router, **_protected)
app.include_router(setup_router.router, **_protected)
app.include_router(web_search_router.router, **_protected)
from app.api import webhooks_router, quickbooks_router, salesforce_router, integration_sends_router
app.include_router(webhooks_router.router,        **_protected)
app.include_router(quickbooks_router.router,      **_protected)
app.include_router(quickbooks_router.public_router)   # OAuth callback: authenticated by its state token
app.include_router(salesforce_router.router,      **_protected)
app.include_router(integration_sends_router.router, **_protected)
app.include_router(salesforce_router.public_router)   # OAuth callback: authenticated by its state token
app.include_router(agency_router.router,         **_protected)
app.include_router(agent_reports_router.router,  **_protected)
app.include_router(commission_router.router,     **_protected)
app.include_router(gap_analysis_router.router,   **_protected)
app.include_router(coi_router.router,            **_protected)
app.include_router(loss_run_router.router,       **_protected)
app.include_router(renewal_router.router,        **_protected)
app.include_router(agency_settings_router.router,**_protected)
app.include_router(calendar_router.router,       **_protected)
app.include_router(personal_router.router,       **_protected)
app.include_router(domains_router.router,        **_protected)
app.include_router(agent_router,                 **_protected)
app.include_router(mcp_settings_router,          **_protected)
app.include_router(guardrails_router,            **_protected)
app.include_router(immigration_router,           **_protected)
app.include_router(qa_history_router,            **_protected)
app.include_router(tts_router,                   **_protected)
app.include_router(research_router)              # per-endpoint auth (JWT or session token for SSE)
app.include_router(workflow_router,              **_protected)
app.include_router(domain_workflow_router,       **_protected)
app.include_router(approvals_router,             **_protected)
app.include_router(vendor_compliance_router,     **_protected)
app.include_router(immigration_suite_router,     **_protected)
app.include_router(immigration_public_router)    # public — client links authenticate by token
for _r in (claims_router, matters_router, quote_router, clm_router, audit_router, risk_router, jurisdiction_router,
           export_router, insurance_automation_router):
    app.include_router(_r, **_protected)


# ── Performance stats endpoint ────────────────────────────────────────────────
@app.get("/api/perf", dependencies=[Depends(_auth_required)])
async def perf_stats():
    """Live performance stats: cache hit rates, queue depth, job registry."""
    return get_perf_stats()

@app.delete("/api/perf/cache", dependencies=[Depends(_auth_required)])
async def clear_caches():
    from app.core.performance import embedding_cache, response_cache
    embedding_cache.clear()
    response_cache.clear()
    return {"ok": True, "message": "Embedding and response caches cleared"}


# ── Background upload job status ──────────────────────────────────────────────
@app.get("/api/upload/jobs/{job_id}", dependencies=[Depends(_auth_required)])
async def get_upload_job(job_id: str):
    job = job_registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return job

# ── Persistent job queue status ───────────────────────────────────────────────
@app.get("/api/jobs/{job_id}", dependencies=[Depends(_auth_required)])
async def get_job_status(job_id: str):
    """Check status of a queued background job (document processing, reembed, etc.)"""
    from app.services.job_worker import get_job_status
    job = get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/api/jobs", dependencies=[Depends(_auth_required)])
async def list_jobs(status: str = None, limit: int = 20):
    """List recent jobs — useful for admin monitoring."""
    from app.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        where = "WHERE status = :status" if status else ""
        rows = db.execute(text(f"""
            SELECT id, job_type, status, attempts, max_attempts,
                   error_message, created_at, started_at, completed_at
            FROM job_queue
            {where}
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"status": status, "limit": limit} if status else {"limit": limit}).fetchall()
        return {"items": [dict(r._mapping) for r in rows]}
    finally:
        db.close()

# ── MCP Server (SSE transport) ────────────────────────────────────────────────
app.mount("/mcp", mcp_server.sse_app())

# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected",
                "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        return {"status": "unhealthy", "database": "disconnected",
                "error": str(e), "timestamp": datetime.utcnow().isoformat()}


# ── Upload ────────────────────────────────────────────────────────────────────

import os as _os
_CPU_COUNT   = _os.cpu_count() or 2
_UPLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=min(_CPU_COUNT, 4))


def _process_single_upload(file_bytes: bytes, filename: str, content_type: str) -> dict:
    from app.services.processing_service import ProcessingService
    return ProcessingService.process_sync(file_bytes, filename, content_type)


def _run_batch_background(job_id: str, file_data: list):
    """Runs in thread pool — updates job registry as files complete."""
    job_registry.update(job_id, status="running")
    results = []
    for fd in file_data:
        try:
            r = _process_single_upload(fd["bytes"], fd["filename"], fd["content_type"])
            results.append(r)
        except Exception as e:
            results.append({"status": "error", "filename": fd["filename"], "error": str(e)})
        job_registry.update(job_id, processed=len(results), results=results)

    succeeded = sum(1 for r in results if r.get("status") == "success")
    job_registry.update(
        job_id,
        status="done",
        results=results,
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


@app.post("/api/upload/batch", dependencies=[Depends(_auth_required)])
async def upload_batch(files: list[UploadFile] = File(...)):
    """
    Returns a job_id immediately. Processing runs in background.
    Poll GET /api/upload/jobs/{job_id} for progress.
    """
    file_data = [{"bytes": await f.read(), "filename": f.filename,
                  "content_type": f.content_type} for f in files]
    job_id = job_registry.create(
        filename=", ".join(fd["filename"] for fd in file_data),
        total_files=len(file_data),
    )
    loop = asyncio.get_event_loop()
    # Run in a copy of this request's context so the files are stored under the signed-in
    # organisation (a bare executor thread falls back to the default tenant).
    import contextvars
    ctx = contextvars.copy_context()
    loop.run_in_executor(_UPLOAD_EXECUTOR, ctx.run, _run_batch_background, job_id, file_data)
    return {
        "job_id": job_id,
        "status": "pending",
        "total": len(file_data),
        "poll_url": f"/api/upload/jobs/{job_id}",
    }


# ── Chatbot pages (before Angular catch-all) ─────────────────────────────────
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

from fastapi.responses import FileResponse as _FileResponse

@app.get("/chatbot")
async def chatbot_page():
    """Standalone banking chatbot UI — embeddable via iframe or direct link."""
    from fastapi.responses import HTMLResponse
    chatbot_html = Path(__file__).parent / "static" / "chatbot.html"
    return HTMLResponse(
        content=chatbot_html.read_text(),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
    )

@app.get("/chatbot-widget.js")
async def chatbot_widget_js():
    """Embeddable widget script — banks add one <script> tag to their site."""
    widget_js = Path(__file__).parent / "static" / "chatbot-widget.js"
    from fastapi.responses import Response
    return Response(widget_js.read_text(), media_type="application/javascript",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


# ── Serve Angular frontend — MUST BE LAST ─────────────────────────────────────

_frontend_env = os.getenv("FRONTEND_DIR", "")

if getattr(sys, 'frozen', False):
    resources_path = Path(sys.executable).parent.parent
    FRONTEND_DIR   = resources_path / "frontend"
elif _frontend_env:
    FRONTEND_DIR = Path(_frontend_env)
else:
    FRONTEND_DIR = (
        Path(__file__).parent.parent
        / "unidocverse-dashboard"
        / "dist" / "unidocverse-dashboard" / "browser"
    )

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    logger.info(f"✅ Angular frontend mounted from {FRONTEND_DIR}")
else:
    logger.warning(f"⚠ Frontend not found at {FRONTEND_DIR} — API-only mode")

    @app.get("/")
    async def root_fallback():
        return {"status": "running", "note": "Frontend not built yet"}


# ── Entry point ───────────────────────────────────────────────────────────────

def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


if __name__ == "__main__":
    import uvicorn
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "7979"))
    if _port_in_use("127.0.0.1", port):
        logger.info(f"⚠️ Port {port} already in use — skipping")
    else:
        uvicorn.run(app, host=host, port=port, reload=False,
                    workers=1, lifespan="on", log_level="info")