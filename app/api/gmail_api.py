"""
app/api/gmail_api.py
Gmail integration API — no OAuth, app password only
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.gmail_connector import GmailService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/gmail", tags=["gmail"])

_gmail_service: Optional[GmailService] = None
_sse_subscribers: Dict[str, List[asyncio.Queue]] = {}


def get_gmail_service() -> GmailService:
    global _gmail_service
    if _gmail_service is None:
        _gmail_service = GmailService()
    return _gmail_service


async def broadcast_gmail_event(connection_id: str, event: dict):
    for q in _sse_subscribers.get(connection_id, []):
        await q.put(event)


# ── Models ────────────────────────────────────────────────────────────────────

class AddConnectionRequest(BaseModel):
    label:          str
    email_address:  str
    app_password:   str
    filter_folder:  str = "INBOX"
    filter_sender:  str = ""
    filter_subject: str = ""


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/connections")
async def list_connections():
    return get_gmail_service().list_connections()


@router.post("/connections")
async def add_connection(req: AddConnectionRequest, background_tasks: BackgroundTasks):
    svc           = get_gmail_service()
    connection_id = str(uuid.uuid4())

    # Test credentials before saving
    from app.services.gmail_connector import GmailConnection
    test_conn = GmailConnection(
        connection_id  = connection_id,
        label          = req.label,
        email_address  = req.email_address,
        app_password   = req.app_password,
        filter_folder  = req.filter_folder,
    )
    ok, err = test_conn.test_connection()
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=f"IMAP connection failed: {err}. Make sure IMAP is enabled in Gmail settings and you're using an App Password."
        )

    config = svc.add_connection(
        connection_id  = connection_id,
        label          = req.label,
        email_address  = req.email_address,
        app_password   = req.app_password,
        filter_folder  = req.filter_folder,
        filter_sender  = req.filter_sender,
        filter_subject = req.filter_subject,
    )

    # Auto-start polling
    background_tasks.add_task(svc.start_poller, connection_id)
    return config


@router.delete("/connections/{connection_id}")
async def remove_connection(connection_id: str):
    get_gmail_service().remove_connection(connection_id)
    return {"status": "removed", "connection_id": connection_id}


@router.post("/connections/{connection_id}/pause")
async def pause_polling(connection_id: str):
    get_gmail_service().pause_poller(connection_id)
    return {"status": "paused", "connection_id": connection_id}


@router.post("/connections/{connection_id}/resume")
async def resume_polling(connection_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(get_gmail_service().start_poller, connection_id)
    return {"status": "resuming", "connection_id": connection_id}


@router.post("/connections/{connection_id}/test")
async def test_connection(connection_id: str):
    ok, err = get_gmail_service().test_connection(connection_id)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return {"status": "ok"}


@router.get("/connections/{connection_id}/status")
async def get_status(connection_id: str):
    status = get_gmail_service().get_status(connection_id)
    if not status:
        raise HTTPException(status_code=404, detail="Connection not found")
    return status


@router.get("/connections/{connection_id}/progress")
async def progress_stream(connection_id: str):
    async def event_generator() -> AsyncGenerator[str, None]:
        q: asyncio.Queue = asyncio.Queue()
        _sse_subscribers.setdefault(connection_id, []).append(q)
        try:
            status = get_gmail_service().get_status(connection_id)
            if status:
                yield f"data: {json.dumps(status)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            subs = _sse_subscribers.get(connection_id, [])
            if q in subs:
                subs.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/summary")
async def summary():
    connections = get_gmail_service().list_connections()
    return {
        "total":            len(connections),
        "active":           sum(1 for c in connections if c.get("status") == "active"),
        "emails_processed": sum(c.get("emails_processed", 0) for c in connections),
    }