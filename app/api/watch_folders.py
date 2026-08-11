"""
app/api/watch_folders.py
Watch Folders API — manage watched folders, watcher lifecycle, SSE progress
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.folder_watcher_service import FolderWatcherService, WatcherStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/watch-folders", tags=["watch-folders"])

# Singleton watcher service
_watcher_service: Optional[FolderWatcherService] = None

# SSE subscribers: folder_id → list of asyncio.Queue
_sse_subscribers: Dict[str, List[asyncio.Queue]] = {}


def get_watcher_service() -> FolderWatcherService:
    global _watcher_service
    if _watcher_service is None:
        _watcher_service = FolderWatcherService()
    return _watcher_service


# ── Models ────────────────────────────────────────────────────────────────────

class AddFolderRequest(BaseModel):
    path: str
    source_type: str = "local"          # local | sharepoint | gdrive | s3
    batch_size: Optional[int] = None    # None = auto
    max_workers: int = 3
    label: Optional[str] = None


class FolderConfig(BaseModel):
    id: str
    path: str
    label: str
    source_type: str
    batch_size: Optional[int]
    max_workers: int
    status: str                          # active | paused | error | idle
    added_at: str
    files_processed: int = 0
    files_pending: int = 0
    last_batch_at: Optional[str] = None
    last_error: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/folders", response_model=List[FolderConfig])
async def list_folders():
    """Return all configured watch folders."""
    svc = get_watcher_service()
    return svc.list_folders()


@router.post("/folders", response_model=FolderConfig)
async def add_folder(req: AddFolderRequest, background_tasks: BackgroundTasks):
    """Add a new watch folder and start watching it."""
    svc = get_watcher_service()

    # Validate path exists (local only)
    if req.source_type == "local":
        p = Path(req.path)
        if not p.exists():
            raise HTTPException(status_code=400, detail=f"Path does not exist: {req.path}")
        if not p.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {req.path}")

    folder_id = str(uuid.uuid4())
    config = svc.add_folder(
        folder_id   = folder_id,
        path        = req.path,
        source_type = req.source_type,
        batch_size  = req.batch_size,
        max_workers = req.max_workers,
        label       = req.label or Path(req.path).name,
    )

    # Start watcher in background
    background_tasks.add_task(svc.start_watcher, folder_id)
    return config


@router.delete("/folders/{folder_id}")
async def remove_folder(folder_id: str):
    """Stop and remove a watch folder."""
    svc = get_watcher_service()
    svc.stop_watcher(folder_id)
    svc.remove_folder(folder_id)
    return {"status": "removed", "folder_id": folder_id}


@router.post("/folders/{folder_id}/pause")
async def pause_folder(folder_id: str):
    """Pause watching a folder."""
    svc = get_watcher_service()
    svc.pause_watcher(folder_id)
    return {"status": "paused", "folder_id": folder_id}


@router.post("/folders/{folder_id}/resume")
async def resume_folder(folder_id: str, background_tasks: BackgroundTasks):
    """Resume watching a paused folder."""
    svc = get_watcher_service()
    background_tasks.add_task(svc.start_watcher, folder_id)
    return {"status": "resuming", "folder_id": folder_id}


@router.get("/folders/{folder_id}/reports")
async def get_reports(folder_id: str, limit: int = 20):
    """Return recent batch reports for a folder."""
    svc = get_watcher_service()
    reports = svc.get_reports(folder_id, limit=limit)
    return {"reports": reports}


@router.get("/folders/{folder_id}/status")
async def get_status(folder_id: str):
    """Return current status of a folder watcher."""
    svc = get_watcher_service()
    status = svc.get_status(folder_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return status


@router.get("/folders/{folder_id}/progress")
async def progress_stream(folder_id: str):
    """SSE stream — push live progress events for a folder."""

    async def event_generator() -> AsyncGenerator[str, None]:
        q: asyncio.Queue = asyncio.Queue()
        _sse_subscribers.setdefault(folder_id, []).append(q)
        try:
            # Send current status immediately
            svc = get_watcher_service()
            status = svc.get_status(folder_id)
            if status:
                yield f"data: {json.dumps(status)}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive ping
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            subs = _sse_subscribers.get(folder_id, [])
            if q in subs:
                subs.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def broadcast_folder_event(folder_id: str, event: dict):
    """Call this from FolderWatcherService to push events to SSE subscribers."""
    for q in _sse_subscribers.get(folder_id, []):
        await q.put(event)


@router.get("/summary")
async def summary():
    """Overall summary across all watchers."""
    svc = get_watcher_service()
    folders = svc.list_folders()
    return {
        "total_folders":    len(folders),
        "active":           sum(1 for f in folders if f["status"] == "active"),
        "paused":           sum(1 for f in folders if f["status"] == "paused"),
        "error":            sum(1 for f in folders if f["status"] == "error"),
        "total_processed":  sum(f.get("files_processed", 0) for f in folders),
        "total_pending":    sum(f.get("files_pending", 0) for f in folders),
    }