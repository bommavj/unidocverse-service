"""
Progress API Endpoints
Provides real-time progress updates via Server-Sent Events (SSE)
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import Dict, Optional
import asyncio
import json
import logging

from app.agents.progress_tracker import get_progress_tracker

logger = logging.getLogger(__name__)

router = APIRouter()


class ProgressEventStream:
    """Server-Sent Events stream for progress updates"""

    def __init__(self, document_id: str):
        self.document_id = document_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self._active = True

    async def send_update(self, data: Dict):
        """Send a progress update to the stream"""
        if self._active:
            await self.queue.put(data)

    async def close(self):
        """Close the stream"""
        self._active = False
        await self.queue.put(None)  # Sentinel to stop iteration

    async def generate(self):
        """Generate SSE-formatted events"""
        try:
            while self._active:
                try:
                    # Wait for next update with timeout
                    data = await asyncio.wait_for(self.queue.get(), timeout=30.0)

                    if data is None:  # Sentinel - stream closed
                        break

                    # Format as SSE
                    event_data = json.dumps(data)
                    yield f"data: {event_data}\n\n"

                except asyncio.TimeoutError:
                    # Send keepalive every 30 seconds
                    yield ": keepalive\n\n"

        except asyncio.CancelledError:
            logger.info(f"SSE stream cancelled for document: {self.document_id}")
        except Exception as e:
            logger.error(f"SSE stream error: {e}", exc_info=True)
        finally:
            self._active = False


# Store active streams per document
_active_streams: Dict[str, list[ProgressEventStream]] = {}


def register_stream(document_id: str, stream: ProgressEventStream):
    """Register a new event stream for a document"""
    if document_id not in _active_streams:
        _active_streams[document_id] = []
    _active_streams[document_id].append(stream)
    logger.info(f"📡 Registered SSE stream for: {document_id}")


def unregister_stream(document_id: str, stream: ProgressEventStream):
    """Remove an event stream"""
    if document_id in _active_streams:
        if stream in _active_streams[document_id]:
            _active_streams[document_id].remove(stream)
        if not _active_streams[document_id]:
            del _active_streams[document_id]


async def broadcast_progress(document_id: str, progress: float, stage: str, status: str):
    """Broadcast progress update to all active streams for this document"""
    if document_id not in _active_streams:
        return

    update_data = {
        'document_id': document_id,
        'progress': round(progress, 1),
        'stage': stage,
        'status': status,
        'timestamp': asyncio.get_event_loop().time(),
    }

    # Send to all active streams
    streams = _active_streams.get(document_id, [])
    for stream in streams:
        try:
            await stream.send_update(update_data)
        except Exception as e:
            logger.error(f"Failed to send update to stream: {e}")


async def close_all_streams(document_id: str):
    """Close all streams for a document when processing completes"""
    if document_id not in _active_streams:
        return

    streams = _active_streams.get(document_id, []).copy()
    for stream in streams:
        try:
            await stream.close()
        except Exception as e:
            logger.error(f"Failed to close stream: {e}")

    if document_id in _active_streams:
        del _active_streams[document_id]


@router.get("/api/documents/{document_id}/progress")
async def stream_progress(document_id: str):
    """
    Stream real-time progress updates for a document processing task

    Returns Server-Sent Events (SSE) stream with progress updates:
    {
        "document_id": "abc123",
        "progress": 45.5,
        "stage": "analyze",
        "status": "Running AI analysis - Invoice processing",
        "timestamp": 1234567890.123
    }
    """
    stream = ProgressEventStream(document_id)
    register_stream(document_id, stream)

    async def event_generator():
        try:
            async for event in stream.generate():
                yield event
        finally:
            unregister_stream(document_id, stream)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@router.get("/api/documents/progress/active")
async def get_active_processing():
    """Get list of all documents currently being processed"""
    from app.services.progress_tracker import get_progress_tracker

    tracker = get_progress_tracker()
    active_tasks = tracker.get_all_active()

    return {
        "active_count": len(active_tasks),
        "tasks": active_tasks
    }


@router.get("/api/documents/{document_id}/progress/status")
async def get_progress_status(document_id: str):
        """Get current progress status"""
        tracker = get_progress_tracker()
        progress = tracker.get_progress(document_id)

        if not progress:
            return {"document_id": document_id, "progress": 0, "stage": "unknown", "status": "not_found"}

        return {
            "document_id": document_id,
            "progress": progress.get_overall_progress(),
            "stage": progress.current_stage,
            "status": "processing" if progress.get_overall_progress() < 100 else "complete"
        }