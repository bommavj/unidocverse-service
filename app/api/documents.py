import base64
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional
from app.services.universal_qa_service import UniversalQAService
from app.core.pii_redactor import redact_text, redact_metadata
from app.core.config import settings

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.langgraph_agents import ollama_client
from app.core import config
from app.core.database import get_db, SessionLocal
from app.api.auth_router import require_admin, get_current_user
from app.models.document import Document, DocumentChunk
from app.schemas.document import DocumentStatusResponse
from app.services.ai_service import generate_additional_insights, generate_additional_key_points
from app.services.document_service import DocumentService
from app.services.qa_service import QAService, _assess_confidence
from app.services.thumbnail_service import ThumbnailService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _encode_cursor(doc) -> str:
    """Encode created_at + id into an opaque base64 cursor."""
    raw = f"{doc.created_at.isoformat()}|{doc.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str):
    """Return (created_at_str, doc_id) or raise ValueError."""
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts, doc_id = raw.rsplit("|", 1)
    return ts, int(doc_id)


@router.get("")
async def get_documents(
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        cursor: Optional[str] = Query(None, description="Opaque pagination cursor from previous response"),
        doc_type: Optional[str] = None,
        status: Optional[str] = None,
        db: Session = Depends(get_db)
):
    """
    Get all documents with complete metadata including summary, insights, etc.
    Supports keyset cursor pagination via the `cursor` param (returned as `next_cursor`).
    Falls back to offset-based pagination when `cursor` is absent.
    """
    from sqlalchemy import or_, and_

    # Build query
    query = db.query(Document).filter(Document.status != 'deleted')

    # Apply filters
    if doc_type:
        query = query.filter(Document.doc_type == doc_type)
    if status:
        query = query.filter(Document.status == status)

    # Keyset cursor — more efficient than OFFSET for large tables
    if cursor:
        try:
            ts_str, last_id = _decode_cursor(cursor)
            from datetime import datetime as _dt
            last_ts = _dt.fromisoformat(ts_str)
            query = query.filter(
                or_(
                    Document.created_at < last_ts,
                    and_(Document.created_at == last_ts, Document.id < last_id)
                )
            )
            skip = 0  # cursor supersedes offset
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor value")

    # Total count (before pagination) for offset-based callers
    total_count = query.count()

    # Get documents
    documents = query.order_by(Document.created_at.desc()) \
        .offset(skip) \
        .limit(limit) \
        .all()

    # Build response with complete data
    result = []
    for doc in documents:
        doc_dict = {
            "id": doc.id,
            "filename": doc.filename,
            "doc_type": doc.doc_type,
            "file_size": doc.file_size,
            "file_path": doc.file_path,
            "status": doc.status,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,

            # ✅ Include summary from database
            "summary": redact_text(doc.summary or ""),
            "language": doc.language or "en",

            # ✅ Include embedding status
            "has_embedding": doc.embedding is not None,

            # ✅ Initialize metadata fields
            "insights": [],
            "key_points": [],  # ← add this line
            "classification": None,
            "confidence": None,
            "tables": [],
            "message": None,
            "metadata": None,
            "parent_doc_id": None,
            "child_count":   0,
        }

        # ✅ Extract from doc_metadata if available
        if doc.doc_metadata:
            if isinstance(doc.doc_metadata, dict):
                doc_dict["insights"] = doc.doc_metadata.get("insights", [])
                doc_dict["key_points"] = doc.doc_metadata.get("key_points", [])
                doc_dict["classification"] = doc.doc_metadata.get("classification")
                doc_dict["confidence"] = doc.doc_metadata.get("confidence")
                doc_dict["tables"] = doc.doc_metadata.get("tables", [])
                doc_dict["message"] = doc.doc_metadata.get("message")
                doc_dict["metadata"] = {
                    "analytics": doc.doc_metadata.get("analytics")
                }

        result.append(doc_dict)

        # ✅ Log for debugging
        # logger.info(f"Document {doc.filename}: has_summary={bool(doc.summary)}, "
        #             f"insights_count={len(doc_dict['insights'])}, "
        #             f"classification={doc_dict['classification']}")

    next_cursor = _encode_cursor(documents[-1]) if len(documents) == limit else None
    return {
        "documents": result,
        "total": total_count,
        "limit": limit,
        "next_cursor": next_cursor,
    }


# @router.get("/documents")
# async def get_all_documents(
#         skip: int = 0,
#         limit: int = 100,
#         db: Session = Depends(get_db)
# ):
#     """Get all documents (simple list)"""
#     documents = DocumentService.get_documents(db, skip, limit)
#
#     return [
#         {
#             "id": str(doc.id),
#             "filename": doc.filename,
#             "file_size": doc.file_size,
#             "doc_type": doc.doc_type,
#             "status": doc.status,
#             "summary": doc.summary,
#             "created_at": doc.created_at.isoformat() if doc.created_at else None,
#             "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
#             "metadata": doc.doc_metadata
#         }
#         for doc in documents
#     ]


@router.get("/{doc_id}")
async def get_document(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Get single document with complete metadata
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # ✅ Build complete response
    doc_dict = {
        "id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "file_size": doc.file_size,
        "file_path": doc.file_path,
        "status": doc.status,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,

        # ✅ Include all fields
        "summary": redact_text(doc.summary or ""),
        "raw_text": redact_text(doc.raw_text or ""),
        "has_embedding": doc.embedding is not None,

        "insights": [],
        "classification": None,
        "confidence": None,
        "tables": [],
        "message": None,
        "metadata": {}
    }

    # ✅ Extract from doc_metadata
    if doc.doc_metadata and isinstance(doc.doc_metadata, dict):
        doc_dict["insights"] = doc.doc_metadata.get("insights", [])
        doc_dict["classification"] = doc.doc_metadata.get("classification")
        doc_dict["confidence"] = doc.doc_metadata.get("confidence")
        doc_dict["tables"] = doc.doc_metadata.get("tables", [])
        doc_dict["message"] = doc.doc_metadata.get("message")
        doc_dict["metadata"] = doc.doc_metadata

    logger.info(f"Fetched document {doc.filename}: "
                f"summary_length={len(doc.summary) if doc.summary else 0}")

    return doc_dict


# async def get_document(
#         doc_id: str,
#         db: Session = Depends(get_db)
# ):
#     """Get document by ID"""
#     doc = DocumentService.get_document_by_id(db, doc_id)
#
#     if not doc:
#         raise HTTPException(status_code=404, detail="Document not found")
#
#     return {
#         "id": str(doc.id),
#         "filename": doc.filename,
#         "file_size": doc.file_size,
#         "doc_type": doc.doc_type,
#         "status": doc.status,
#         "summary": doc.summary,
#         "raw_text": doc.raw_text,
#         "created_at": doc.created_at.isoformat() if doc.created_at else None,
#         "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
#         "metadata": doc.doc_metadata
#     }


@router.post("/{doc_id}/reprocess")
async def reprocess_document(doc_id: str, db: Session = Depends(get_db)):
    """Re-run the full LangGraph pipeline on an existing document."""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not Path(doc.file_path).exists():
        raise HTTPException(status_code=400, detail="Source file no longer exists on disk")

    doc.status = "pending"
    doc.doc_type = None
    doc.summary = None
    doc.raw_text = None
    db.commit()

    import threading
    from app.services.processing_service import ProcessingService
    threading.Thread(
        target=ProcessingService.process_document_background,
        args=(doc_id, doc.file_path, doc.filename),
        daemon=True,
    ).start()

    return {"success": True, "message": f"Reprocessing started for {doc.filename}"}


@router.post("/{doc_id}/unlock")
async def unlock_document(
        doc_id: str,
        body: dict,
        db: Session = Depends(get_db)
):
    """
    Attempt to unlock a password-protected document.
    Body: { "password": "..." }
    On success, re-queues the document for processing.
    Password is never stored — used only to decrypt in memory.
    """
    import threading
    from app.services.parsers.universal_parser import UniversalParser
    from app.services.processing_service import ProcessingService

    doc = DocumentService.get_document_by_id(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "password_required":
        raise HTTPException(status_code=400, detail="Document is not password-protected")

    password = body.get("password", "")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")

    if not UniversalParser.unlock_pdf(doc.file_path, password):
        raise HTTPException(status_code=401, detail="Incorrect password")

    # Decrypt to a temp file — password is never persisted
    import tempfile, fitz
    src = fitz.open(doc.file_path)
    src.authenticate(password)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    src.save(tmp.name)
    src.close()

    # Swap the stored file with the decrypted copy
    import os as _os
    _os.replace(tmp.name, doc.file_path)

    # Reset status and re-queue
    doc.status = "pending"
    doc.doc_metadata = {}
    db.commit()

    thread = threading.Thread(
        target=ProcessingService.process_document_background,
        args=(doc_id, doc.file_path, doc.filename),
        daemon=True
    )
    thread.start()

    return {"status": "processing", "message": "Document unlocked and re-queued for processing"}


@router.get("/{doc_id}/children")
async def get_archive_children(doc_id: str, db: Session = Depends(get_db)):
    """Return all child documents extracted from an archive."""
    parent = DocumentService.get_document_by_id(db, doc_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Document not found")

    return {"parent_id": doc_id, "child_count": 0, "done_count": 0, "all_done": False, "children": []}


@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """Get processing status"""
    doc = DocumentService.get_document_by_id(db, doc_id)

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentStatusResponse(
        id=str(doc.id),
        filename=doc.filename,
        status=doc.status,
        doc_type=doc.doc_type,
        has_summary=doc.summary is not None,
        has_embedding=doc.embedding is not None,
        processed_at=doc.processed_at,
        created_at=doc.created_at
    )


# ✅ NEW: Delete all documents
@router.delete("/all")
async def delete_all_documents(
        confirm: bool = Query(False, description="Must be true to delete all"),
        db: Session = Depends(get_db),
        _: dict = Depends(require_admin)
):
    """
    Delete ALL documents (requires confirmation).

    Cleanup order:
      1. Files from disk
      2. notifications      (no FK constraint → manual delete)
      3. document_chunks    (FK CASCADE, but explicit for safety)
      4. activity_log       (FK SET NULL, handled by DB)
      5. documents rows
    """
    logger.info("*** 🗑️ DELETING ALL DOCUMENTS ***")

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Must set confirm=true to delete all documents"
        )

    try:
        documents = db.query(Document).all()
        count = len(documents)

        if count == 0:
            return {
                "message": "No documents to delete",
                "deleted_count": 0
            }

        # ── 1. Delete files from disk ─────────────────────────────────
        deleted_files = 0
        for doc in documents:
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                    deleted_files += 1
                except Exception as e:
                    logger.warning(f"⚠️ Could not delete file {doc.file_path}: {e}")

        # ── 2. Delete notifications (no FK constraint) ────────────────
        deleted_notifs = db.execute(text("DELETE FROM notifications")).rowcount
        if deleted_notifs:
            logger.info(f"🗑️ Deleted {deleted_notifs} notifications")

        # ── 3. Delete all chunks explicitly ───────────────────────────
        deleted_chunks = db.query(DocumentChunk).delete(synchronize_session=False)
        if deleted_chunks:
            logger.info(f"🗑️ Deleted {deleted_chunks} chunks")

        # ── 4. activity_log.document_id → SET NULL (DB handles it) ────

        # ── 5. Delete all document rows ───────────────────────────────
        db.query(Document).delete(synchronize_session=False)
        db.commit()

        logger.warning(f"✅ Deleted {count} documents and {deleted_files} files")

        return {
            "message": "All documents deleted successfully",
            "deleted_count": count,
            "deleted_files": deleted_files,
            "deleted_chunks": deleted_chunks,
            "deleted_notifications": deleted_notifs
        }

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error deleting all documents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/delete-bulk")
async def delete_selected_docs(
        payload: dict,
        db: Session = Depends(get_db),
        _: dict = Depends(get_current_user)
):
    """
    Delete multiple documents by IDs.
    Accepts: { "ids": ["1","2","3"] }

    Cleanup order:
      1. File from disk
      2. notifications      (doc_id col, no FK constraint → manual delete)
      3. document_chunks    (FK CASCADE, but explicit for safety)
      4. activity_log       (FK SET NULL, already handled by DB)
      5. documents row
    """
    ids = payload.get("ids", [])

    if not ids or not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Invalid or empty 'ids' list")

    deleted    = []
    not_found  = []
    failed     = []

    for doc_id in ids:
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()

            if not doc:
                not_found.append(doc_id)
                continue

            # ── 1. Delete file from disk ──────────────────────────────
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                    logger.info(f"🗑️  Deleted file: {doc.file_path}")
                except Exception as e:
                    logger.warning(f"⚠️  Could not delete file for {doc_id}: {e}")

            # ── 2. Delete notifications (no FK constraint) ────────────
            deleted_notifs = (
                db.execute(
                    text("DELETE FROM notifications WHERE doc_id = :doc_id"),
                    {"doc_id": doc_id}
                ).rowcount
            )
            if deleted_notifs:
                logger.info(f"🗑️  Deleted {deleted_notifs} notifications for {doc_id}")

            # ── 3. Delete chunks explicitly (FK CASCADE exists but
            #       belt-and-suspenders — avoids stale data if CASCADE
            #       ever changes) ───────────────────────────────────────
            deleted_chunks = (
                db.query(DocumentChunk)
                .filter(DocumentChunk.document_id == doc_id)
                .delete(synchronize_session=False)
            )
            if deleted_chunks:
                logger.info(f"🗑️  Deleted {deleted_chunks} chunks for {doc_id}")

            # ── 4. activity_log.document_id → SET NULL (DB handles it) ─

            # ── 5. Delete document row ────────────────────────────────
            db.delete(doc)
            deleted.append(doc_id)
            logger.info(f"✅ Deleted document {doc_id}")

        except Exception as e:
            db.rollback()
            logger.error(f"❌ Failed to delete {doc_id}: {e}", exc_info=True)
            failed.append(doc_id)

    db.commit()

    return {
        "message": "Bulk delete completed",
        "deleted":   deleted,
        "not_found": not_found,
        "failed":    failed
    }


# ✅ NEW: Delete single document
@router.delete("/{doc_id}")
async def delete_document(
        doc_id: str,
        db: Session = Depends(get_db),
        _: dict = Depends(require_admin)
):
    """
    Delete a single document.

    Cleanup order:
      1. File from disk
      2. notifications      (doc_id col, no FK constraint → manual delete)
      3. document_chunks    (FK CASCADE, but explicit for safety)
      4. activity_log       (FK SET NULL, handled by DB)
      5. documents row
    """
    try:
        logger.info(f"🗑️ Deleting document: {doc_id}")

        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        filename = doc.filename

        # ── 1. Delete file from disk ──────────────────────────────────
        if doc.file_path and os.path.exists(doc.file_path):
            try:
                os.remove(doc.file_path)
                logger.info(f"✅ Deleted file: {doc.file_path}")
            except Exception as e:
                logger.warning(f"⚠️ Could not delete file: {e}")

        # ── 2. Delete notifications (no FK constraint) ────────────────
        deleted_notifs = (
            db.execute(
                text("DELETE FROM notifications WHERE doc_id = :doc_id"),
                {"doc_id": doc_id}
            ).rowcount
        )
        if deleted_notifs:
            logger.info(f"🗑️ Deleted {deleted_notifs} notifications for {doc_id}")

        # ── 3. Delete chunks explicitly ───────────────────────────────
        deleted_chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc_id)
            .delete(synchronize_session=False)
        )
        if deleted_chunks:
            logger.info(f"🗑️ Deleted {deleted_chunks} chunks for {doc_id}")

        # ── 4. activity_log.document_id → SET NULL (DB handles it) ────

        # ── 5. Delete document row ────────────────────────────────────
        db.delete(doc)
        db.commit()

        logger.info(f"✅ Document deleted: {filename}")

        return {
            "message": "Document deleted successfully",
            "filename": filename,
            "id": doc_id
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error deleting document {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ✅ NEW: Get document statistics
@router.get("/stats/count")
async def get_document_stats(db: Session = Depends(get_db)):
    """Get document statistics"""
    try:
        result = db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending,
                COUNT(CASE WHEN status = 'processing' THEN 1 END) as processing,
                COUNT(CASE WHEN status = 'error' THEN 1 END) as error,
                COUNT(embedding) as with_embeddings
            FROM documents
        """))

        stats = result.fetchone()

        return {
            "total": stats.total,
            "completed": stats.completed,
            "pending": stats.pending,
            "processing": stats.processing,
            "error": stats.error,
            "with_embeddings": stats.with_embeddings
        }

    except Exception as e:
        logger.error(f"Error getting document stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{doc_id}/thumbnail")
async def get_thumbnail(doc_id: str):
    """Get document thumbnail"""
    try:
        # Get document
        db = SessionLocal()
        doc = db.query(Document).filter(Document.id == doc_id).first()
        db.close()

        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        # Generate/get thumbnail
        thumbnail_service = ThumbnailService()
        thumbnail_path = thumbnail_service.generate_thumbnail(doc.file_path, doc.filename)

        if thumbnail_path and os.path.exists(thumbnail_path):
            return FileResponse(
                thumbnail_path,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"}
            )

        # Fallback: Return SVG placeholder
        ext = doc.filename.split('.')[-1].upper() if '.' in doc.filename else 'FILE'
        color = {
            'PDF': '#f44336',
            'XLSX': '#4caf50', 'XLS': '#4caf50',
            'DOCX': '#2196f3', 'DOC': '#2196f3',
            'PPTX': '#ff9800', 'PPT': '#ff9800',
            'JPG': '#9c27b0', 'JPEG': '#9c27b0', 'PNG': '#9c27b0'
        }.get(ext, '#757575')

        svg = f'''
        <svg width="80" height="100" xmlns="http://www.w3.org/2000/svg">
            <rect width="80" height="100" fill="{color}"/>
            <text x="40" y="50" font-family="Arial" font-size="16" fill="white" 
                  text-anchor="middle" dy=".3em" font-weight="bold">{ext}</text>
        </svg>
        '''

        return Response(content=svg, media_type="image/svg+xml")

    except Exception as e:
        logger.error(f"Thumbnail error: {e}")

        # Return generic SVG on error
        svg = '''
        <svg width="80" height="100" xmlns="http://www.w3.org/2000/svg">
            <rect width="80" height="100" fill="#757575"/>
            <text x="40" y="50" font-family="Arial" font-size="16" fill="white" 
                  text-anchor="middle" dy=".3em">?</text>
        </svg>
        '''
        return Response(content=svg, media_type="image/svg+xml")


class QuestionRequest(BaseModel):
    question: str


from fastapi.responses import StreamingResponse as FastAPIStreaming
import json


# @router.post("/{doc_id}/ask")
# async def ask_question(doc_id: str, request: QuestionRequest):
#     db = SessionLocal()
#     try:
#         doc = db.query(Document).filter(Document.id == doc_id).first()
#         if not doc:
#             raise HTTPException(status_code=404, detail="Document not found")
#
#         capabilities = doc.doc_metadata.get("capabilities", {}) if doc.doc_metadata else {}
#         if not capabilities.get("supports_qa", False):
#             return {
#                 "answer": "This document doesn't have enough text content to answer questions.",
#                 "confidence": 0.0,
#                 "supports_qa": False
#             }
#
#         # Build context + prompt using QAService helpers
#         prompt, q_type, sources = QAService.build_prompt(request.question, doc)
#         logger.info(f"🔍 PROMPT PREVIEW:\n{prompt[:1500]}")
#
#         def stream_tokens():
#             full_answer = ""
#             try:
#                 for chunk in ollama_client.generate(
#                         model=config.get_active_model(),
#                         prompt=prompt,
#                         stream=True,
#                         options={
#                             "temperature": 0.1,
#                             "num_predict": 400,
#                             "num_ctx": 4096,
#                         }
#                 ):
#                     token = chunk.get("response", "")
#                     if token:
#                         full_answer += token
#                         yield f"data: {json.dumps({'token': token})}\n\n"
#
#                 # Send final metadata
#                 confidence = _assess_confidence(full_answer)
#                 yield f"data: {json.dumps({'done': True, 'confidence': confidence, 'question_type': q_type, 'sources': sources})}\n\n"
#             except Exception as e:
#                 yield f"data: {json.dumps({'error': str(e)})}\n\n"
#
#         return FastAPIStreaming(
#             stream_tokens(),
#             media_type="text/event-stream",
#             headers={
#                 "Cache-Control": "no-cache",
#                 "X-Accel-Buffering": "no"
#             }
#         )
#     finally:
#         db.close()


# @router.post("/{doc_id}/ask")
# async def ask_question(doc_id: str, request: QuestionRequest):
#     """Ask a question about a document"""
#     db = SessionLocal()
#
#     try:
#         doc = db.query(Document).filter(Document.id == doc_id).first()
#
#         if not doc:
#             raise HTTPException(status_code=404, detail="Document not found")
#
#         # Check if document supports Q&A
#         capabilities = doc.doc_metadata.get("capabilities", {}) if doc.doc_metadata else {}
#         if not capabilities.get("supports_qa", False):
#             return {
#                 "answer": "This document doesn't have enough text content to answer questions.",
#                 "confidence": 0.0,
#                 "supports_qa": False
#             }
#
#         # Answer question
#         result = QAService.answer_question(
#             question=request.question,
#             document=doc
#         )
#
#         result["supports_qa"] = True
#         return result
#
#     finally:
#         db.close()


@router.get("/{doc_id}/download")
async def download_document(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Download original document file
    """

    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.file_path or not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=doc.file_path,
        filename=doc.filename,
        media_type='application/octet-stream'
    )


@router.get("/{doc_id}/preview")
async def preview_document(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Stream document for inline preview in browser
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.file_path or not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="Document file not found on disk")

    # Determine content type
    file_ext = Path(doc.filename).suffix.lower()
    content_type_map = {
        '.pdf': 'application/pdf',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.txt': 'text/plain',
        '.md': 'text/markdown',
        '.log': 'text/plain',
        '.csv': 'text/csv',
        '.html': 'text/html',
    }

    content_type = content_type_map.get(file_ext, 'application/octet-stream')

    # Read file
    try:
        with open(doc.file_path, 'rb') as f:
            file_content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")

    # Return file with inline disposition for browser preview
    return StreamingResponse(
        io.BytesIO(file_content),
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{doc.filename}"',
            "Cache-Control": "public, max-age=3600"
        }
    )


@router.get("/{doc_id}/preview-info")
async def get_preview_info(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """
    Get document preview metadata (for spreadsheets, text, etc.)
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Determine preview type
    file_ext = Path(doc.filename).suffix.lower()

    preview_data = {
        "id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "file_size": doc.file_size,
        "created_at": doc.created_at.isoformat(),
        "preview_type": None,
        "content": None
    }

    # PDF Preview
    if file_ext == '.pdf':
        preview_data["preview_type"] = "pdf"

    # Image Preview
    elif file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        preview_data["preview_type"] = "image"

    # Excel Preview
    elif file_ext in ['.xlsx', '.xls', '.csv']:
        preview_data["preview_type"] = "spreadsheet"
        tables = doc.doc_metadata.get('tables', []) if doc.doc_metadata else []
        if tables:
            preview_data["content"] = tables[0]
        else:
            preview_data["content"] = {"error": "No table data available"}

    # Text Preview
    elif file_ext in ['.txt', '.md', '.log']:
        preview_data["preview_type"] = "text"
        preview_data["content"] = doc.raw_text[:10000] if doc.raw_text else ""

    # Word Document
    elif file_ext in ['.docx', '.doc']:
        preview_data["preview_type"] = "document"
        preview_data["content"] = doc.raw_text[:10000] if doc.raw_text else ""

    # Unsupported
    else:
        preview_data["preview_type"] = "unsupported"
        preview_data["message"] = "Preview not available for this file type"

    return preview_data


@router.get("/{doc_id}/analyze-deep/summary")
async def analyze_deep_summary(doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    from app.agents.langgraph_agents import _call_deep_summary
    result = _call_deep_summary(doc.raw_text or "", doc.doc_type or "document", lang=doc.language)
    return {"summary": result}


@router.get("/{doc_id}/summary")
async def get_document_summary(
        doc_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = 0,
        db: Session = Depends(get_db)
):
    """Get document summary with optional pagination"""
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    summary = doc.summary or "No summary available"

    return {
        "summary": summary,
        "total_length": len(summary),
        "truncated": False
    }


@router.get("/{doc_id}/analyze-deep/insights")
async def get_document_insights(
        doc_id: str,
        limit: Optional[int] = 3,
        offset: Optional[int] = 0,
        db: Session = Depends(get_db)
):
    """Get AI insights with pagination - generates more if needed"""
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Get existing insights from metadata
    existing_insights = doc.doc_metadata.get("insights", []) if doc.doc_metadata else []

    # ✅ Check if we need to generate more insights
    requested_end = offset + limit
    total_available = len(existing_insights)

    logger.info(f"📊 Insights request: offset={offset}, limit={limit}, existing={total_available}")

    # ✅ Check if document has text content (use correct attribute)
    #has_text_content = bool(doc.doc_metadata.get("extracted_text") or doc.summary)
    has_text_content = bool(doc.raw_text or doc.summary)

    # If requesting beyond what we have, generate more
    if requested_end >= total_available and has_text_content:
        logger.info(f"🤖 Generating more insights (have {total_available}, need {requested_end})")

        try:
            # Generate additional insights
            additional_insights = await generate_additional_insights(
                document=doc,
                existing_count=total_available,
                needed_count=requested_end - total_available
            )

            if additional_insights:
                # Append to existing insights
                existing_insights.extend(additional_insights)

                # Update metadata in database
                if not doc.doc_metadata:
                    doc.doc_metadata = {}
                doc.doc_metadata["insights"] = existing_insights
                db.commit()

                logger.info(f"✅ Generated {len(additional_insights)} new insights, total now: {len(existing_insights)}")
        except Exception as e:
            logger.error(f"❌ Error generating additional insights: {e}")

    # Paginate
    total = len(existing_insights)
    paginated = existing_insights[offset:offset + limit] if limit else existing_insights
    has_more = (offset + len(paginated)) < total

    # ✅ Always say there's more if we can potentially generate more
    # Limit to reasonable maximum (e.g., 50 insights total)
    max_insights = 50
    if total < max_insights and has_text_content:
        has_more = True

    return {
        "insights": paginated,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more
    }


@router.get("/{doc_id}/analyze-deep/key-points")
async def get_document_key_points(
        doc_id: str,
        limit: Optional[int] = 5,
        offset: Optional[int] = 0,
        db: Session = Depends(get_db)
):
    """Get key points with pagination - generates more if needed"""
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Get existing key points from metadata
    existing_key_points = doc.doc_metadata.get("key_points", []) if doc.doc_metadata else []

    # ✅ Check if we need to generate more key points
    requested_end = offset + limit
    total_available = len(existing_key_points)

    logger.info(f"📊 Key points request: offset={offset}, limit={limit}, existing={total_available}")

    # ✅ Check if document has text content (use correct attribute)
    #has_text_content = bool(doc.doc_metadata.get("extracted_text") or doc.summary)
    has_text_content = bool(doc.raw_text or doc.summary)

    # If requesting beyond what we have, generate more
    if requested_end > total_available and has_text_content:
        logger.info(f"🤖 Generating more key points (have {total_available}, need {requested_end})")

        try:
            # Generate additional key points
            additional_key_points = await generate_additional_key_points(
                document=doc,
                existing_count=total_available,
                needed_count=requested_end - total_available
            )

            if additional_key_points:
                # Append to existing key points
                existing_key_points.extend(additional_key_points)

                # Update metadata in database
                if not doc.doc_metadata:
                    doc.doc_metadata = {}
                doc.doc_metadata["key_points"] = existing_key_points
                db.commit()

                logger.info(
                    f"✅ Generated {len(additional_key_points)} new key points, total now: {len(existing_key_points)}")
        except Exception as e:
            logger.error(f"❌ Error generating additional key points: {e}")

    # Paginate
    total = len(existing_key_points)
    paginated = existing_key_points[offset:offset + limit] if limit else existing_key_points
    has_more = (offset + len(paginated)) < total

    # ✅ Always say there's more if we can potentially generate more
    # Limit to reasonable maximum (e.g., 100 key points total)
    max_key_points = 100
    if total < max_key_points and has_text_content:
        has_more = True

    return {
        "key_points": paginated,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more
    }


@router.get("/{doc_id}/classifications")
async def get_document_classifications(
        doc_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = 0,
        db: Session = Depends(get_db)
):
    """Get document classifications"""
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "document_type": doc.doc_type,
        "primary_classification": doc.doc_metadata.get("primary_type") if doc.doc_metadata else None,
        "confidence_score": doc.doc_metadata.get("confidence_score") if doc.doc_metadata else None,
        "all_classifications": doc.doc_metadata.get("classifications", []) if doc.doc_metadata else []
    }


# ── Cross-document intelligence ───────────────────────────────────────────────

@router.get("/cross-link")
async def cross_link_all(
    client_id: Optional[str] = Query(None, description="Scope to a specific client"),
    doc_ids:   Optional[str] = Query(None, description="Comma-separated doc IDs"),
    db: Session = Depends(get_db),
):
    """
    Cross-document intelligence across ALL document types.
    Groups docs by person/client, detects relationships (immigration + insurance,
    mortgage + bank, contract + invoice, etc.) and returns unified insights,
    alerts, actions, and expiry timeline.
    """
    from app.services.document_cross_linker import cross_link_documents

    if doc_ids:
        ids = [d.strip() for d in doc_ids.split(",") if d.strip()]
        docs = db.query(Document).filter(Document.id.in_(ids), Document.status == "completed").all()
    elif client_id:
        docs = db.query(Document).filter(Document.client_id == client_id, Document.status == "completed").all()
    else:
        raise HTTPException(400, "Provide client_id or doc_ids query parameter")

    if not docs:
        raise HTTPException(404, "No completed documents found")

    return cross_link_documents(docs)


@router.get("/cross-link-by-doc/{doc_id}")
async def cross_link_by_doc(doc_id: str, db: Session = Depends(get_db)):
    """
    Cross-link a specific document against all other documents for the same
    client or the same person (by name match across docs without a client).
    """
    from app.services.document_cross_linker import cross_link_documents, _extract_profile

    anchor = db.query(Document).filter(Document.id == doc_id).first()
    if not anchor:
        raise HTTPException(404, "Document not found")

    if anchor.client_id:
        # All docs for this client
        docs = db.query(Document).filter(
            Document.client_id == anchor.client_id,
            Document.status == "completed",
        ).all()
    else:
        # Try to match by person name across all docs
        anchor_profile = _extract_profile(anchor)
        anchor_name = (anchor_profile.person_name or "").strip().upper()
        if anchor_name:
            all_docs = db.query(Document).filter(Document.status == "completed").all()
            from app.services.document_cross_linker import _extract_profile as ep
            docs = [
                d for d in all_docs
                if ep(d).person_name and ep(d).person_name.strip().upper() == anchor_name
            ]
        else:
            docs = [anchor]

    result = cross_link_documents(docs)
    result["anchor_doc_id"] = doc_id
    return result


@router.get("/{doc_id}/details")
async def get_document_details(
        doc_id: str,
        db: Session = Depends(get_db)
):
    """Get complete document details"""
    doc = db.query(Document).filter(Document.id == doc_id).first()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "id": doc.id,
        "filename": doc.filename,
        "file_size": doc.file_size,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
        "keywords": doc.doc_metadata.get("keywords", []) if doc.doc_metadata else [],
        "entities": doc.doc_metadata.get("entities", []) if doc.doc_metadata else [],
        "metadata": doc.doc_metadata,
        "language": doc.language or "en"
    }


# async def generate_additional_insights(document, existing_count: int, needed_count: int) -> list:
#     """Generate additional insights on demand using phi3:mini."""
#     try:
#         from app.agents.langgraph_agents import _call_deep_insights
#         text = document.raw_text or document.summary or ""
#         doc_type = document.doc_type or "document"
#         insights = _call_deep_insights(text, doc_type)
#         # Skip ones we already have (by title)
#         existing_titles = {i.get("title", "") for i in (document.doc_metadata.get("insights", []) if document.doc_metadata else [])}
#         new_insights = [i for i in insights if i.get("title", "") not in existing_titles]
#         return new_insights[:needed_count]
#     except Exception as e:
#         logger.error(f"❌ generate_additional_insights failed: {e}")
#         return []
#
#
# async def generate_additional_key_points(document, existing_count: int, needed_count: int) -> list:
#     """Generate additional key points on demand using phi3:mini."""
#     try:
#         from app.agents.langgraph_agents import _call_deep_key_points
#         text = document.raw_text or document.summary or ""
#         doc_type = document.doc_type or "document"
#         key_points = _call_deep_key_points(text, doc_type)
#         # Skip duplicates
#         existing_set = set(document.doc_metadata.get("key_points", []) if document.doc_metadata else [])
#         new_kps = [kp for kp in key_points if kp not in existing_set]
#         return new_kps[:needed_count]
#     except Exception as e:
#         logger.error(f"❌ generate_additional_key_points failed: {e}")
#         return []


async def generate_additional_insights(document, existing_count: int, needed_count: int) -> list:
    """Generate additional insights on demand using phi3:mini — chunk-based."""
    try:
        from app.agents.langgraph_agents import _call_deep_insights
        text = document.raw_text or document.summary or ""
        doc_type = document.doc_type or "document"
        chunk_index = existing_count // 3  # every 3 insights → next chunk
        insights = _call_deep_insights(text, doc_type, chunk_index=chunk_index, lang=document.language)
        existing_titles = {
            i.get("title", "")
            for i in (document.doc_metadata.get("insights", []) if document.doc_metadata else [])
        }
        new_insights = [i for i in insights if i.get("title", "") not in existing_titles]
        return new_insights[:needed_count]
    except Exception as e:
        logger.error(f"❌ generate_additional_insights failed: {e}")
        return []


# async def generate_additional_key_points(document, existing_count: int, needed_count: int) -> list:
#     """Generate additional key points on demand using phi3:mini — chunk-based."""
#     try:
#         from app.agents.langgraph_agents import _call_deep_key_points
#         text = document.raw_text or document.summary or ""
#         doc_type = document.doc_type or "document"
#         chunk_index = existing_count // 5  # every 5 key points → next chunk
#         key_points = _call_deep_key_points(text, doc_type, chunk_index=chunk_index)
#         existing_set = set(document.doc_metadata.get("key_points", []) if document.doc_metadata else [])
#         new_kps = [kp for kp in key_points if kp not in existing_set]
#         return new_kps[:needed_count]
#     except Exception as e:
#         logger.error(f"❌ generate_additional_key_points failed: {e}")
#         return []

async def generate_additional_key_points(document, existing_count: int, needed_count: int) -> list:
    """Generate additional key points on demand using phi3:mini — chunk-based."""
    try:
        from app.agents.langgraph_agents import _call_deep_key_points

        text = document.raw_text or document.summary or ""
        doc_type = document.doc_type or "document"

        # every 5 key points → next chunk
        chunk_index = existing_count // 5

        key_points = _call_deep_key_points(text, doc_type, chunk_index=chunk_index, lang=document.language)

        # Normalize existing key points into hashable signatures
        existing_raw = document.doc_metadata.get("key_points", []) if document.doc_metadata else []

        def sig(kp):
            if isinstance(kp, str):
                return ("str", kp.strip())
            if isinstance(kp, dict):
                return (
                    kp.get("title", "").strip(),
                    kp.get("description", "").strip(),
                    kp.get("category", "info"),
                )
            return ("other", str(kp))

        existing_set = {sig(kp) for kp in existing_raw}

        # Filter new key points using signatures
        new_kps = []
        for kp in key_points:
            if sig(kp) not in existing_set:
                new_kps.append(kp)

        return new_kps[:needed_count]

    except Exception as e:
        logger.error(f"❌ generate_additional_key_points failed: {e}")
        return []


async def process_document_background(doc_id: str):
    """
    Universal background task to process ANY document type through LangGraph pipeline
    """
    logger.info(f"🔄 Starting background processing for: {doc_id}")

    # Create a new session for this background task
    from app.core.database import SessionLocal
    db = SessionLocal()

    try:
        from app.agents.workflow import compile_workflow
        from datetime import datetime
        from app.utils.json_utils import clean_metadata_for_json

        # Get document
        doc = db.query(Document).filter(Document.id == doc_id).first()

        if not doc:
            logger.error(f"❌ Document not found: {doc_id}")
            return

        logger.info(f"📄 Processing: {doc.filename}")

        # Create workflow
        workflow = compile_workflow()

        # Create initial state
        initial_state = {
            "document_id": doc_id,
            "filename": doc.filename,
            "file_path": doc.file_path,
            "file_size": doc.file_size,
            "file_type": Path(doc.filename).suffix.lower(),
            "mime_type": "application/pdf",
            "raw_text": None,
            "cleaned_text": None,
            "tables": [],
            "classification": None,
            "summary": None,
            "key_points": [],
            "keywords": [],
            "embedding": None,
            "confidence_score": 0.0,
            "anomalies": [],
            "quality_issues": [],
            "metrics": {},
            "insights": [],
            "visualizations": [],
            "needs_ocr": False,
            "needs_manual_review": False,
            "requires_reprocessing": False,
            "processing_complete": False,
            "doc_metadata": {},
            "errors": [],
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "bank_analytics": None,  # Will be set by analyzer if needed
            "language": doc.language or "en"
        }

        logger.info(f"🚀 Starting workflow for: {doc.filename}")

        # Run workflow
        result = await workflow.ainvoke(initial_state)

        logger.info(f"✅ Workflow completed for: {doc.filename}")

        # ✅ UNIVERSAL DATABASE SAVE - works for ALL document types
        logger.info(f"💾 Saving results to database...")

        # Refresh document
        db.refresh(doc)

        # Extract data from result
        classification = result.get("classification", {})
        doc_type = classification.get("type", "unknown")
        summary = result.get("summary", "")
        raw_text = result.get("cleaned_text") or result.get("raw_text", "")
        embedding = result.get("embedding")
        doc_metadata = result.get("doc_metadata", {})
        errors = result.get("errors", [])

        # ✅ Get analyzer-specific results (works for ANY document type)
        analyzer_results = {}

        # Check for bank statement analytics
        if "bank_statement_analytics" in doc_metadata:
            analyzer_results = doc_metadata["bank_statement_analytics"]
            logger.info(f"💰 Bank statement: {len(analyzer_results.get('transactions', []))} transactions")

        # Check for invoice analytics
        elif "invoice_analytics" in doc_metadata:
            analyzer_results = doc_metadata["invoice_analytics"]
            logger.info(f"🧾 Invoice: {len(analyzer_results.get('line_items', []))} line items")

        # Check for receipt analytics
        elif "receipt_analytics" in doc_metadata:
            analyzer_results = doc_metadata["receipt_analytics"]
            logger.info(f"🧾 Receipt: {len(analyzer_results.get('items', []))} items")

        # Check for contract analytics
        elif "contract_analytics" in doc_metadata:
            analyzer_results = doc_metadata["contract_analytics"]
            logger.info(f"📋 Contract analysis complete")

        # Check for spreadsheet analytics
        elif "spreadsheet_analytics" in doc_metadata:
            analyzer_results = doc_metadata["spreadsheet_analytics"]
            logger.info(f"📊 Spreadsheet: {len(doc_metadata.get('tables', []))} tables")

        # Check for tax document analytics
        elif "tax_document_analytics" in doc_metadata:
            analyzer_results = doc_metadata["tax_document_analytics"]
            logger.info(f"📄 Tax document analysis complete")

        # Check for financial statement analytics
        elif "financial_statement_analytics" in doc_metadata:
            analyzer_results = doc_metadata["financial_statement_analytics"]
            logger.info(f"💼 Financial statement analysis complete")

        # ✅ Clean metadata for JSON serialization
        doc_metadata = clean_metadata_for_json(doc_metadata)

        # Update document
        doc.doc_type = doc_type
        doc.summary = redact_text(summary or "")
        doc.raw_text = redact_text(raw_text or "")
        doc.doc_metadata = redact_metadata(doc_metadata)
        doc.status = "completed" if not errors else "completed_with_errors"
        doc.processed_at = datetime.utcnow()

        # Save embedding
        if embedding and isinstance(embedding, list) and len(embedding) > 0:
            doc.embedding = [float(x) for x in embedding]
            logger.info(f"📊 Saved embedding: {len(embedding)} dimensions")

        # Commit changes
        db.commit()
        db.refresh(doc)

        # ✅ Log results for ANY document type
        logger.info(f"✅ Document saved:")
        logger.info(f"   - ID: {doc.id}")
        logger.info(f"   - Type: {doc.doc_type}")
        logger.info(f"   - Status: {doc.status}")
        logger.info(f"   - Summary: {len(doc.summary or '')} chars")
        logger.info(f"   - Text: {len(doc.raw_text or '')} chars")
        logger.info(f"   - Embedding: {len(doc.embedding or [])} dims")
        logger.info(f"   - Metadata keys: {list(doc.doc_metadata.keys()) if doc.doc_metadata else []}")

        # ── Chunk ingest for RAG ─────────────────────────────────────────
        # Store overlapping chunks + per-chunk embeddings in document_chunks
        # so per-doc Q&A uses pgvector retrieval instead of full raw_text.
        if raw_text:
            try:
                from app.services.qa_service import store_document_chunks
                n_chunks = store_document_chunks(doc.id, raw_text)
                logger.info(f"📦 Stored {n_chunks} RAG chunks for doc {doc.id}")
            except Exception as chunk_err:
                logger.warning(f"⚠️ Chunk storage failed (non-fatal): {chunk_err}")

            try:
                from app.services.facts_extractor import extract_and_store
                extract_and_store(str(doc.id), doc.doc_type or "general", raw_text)
            except Exception as facts_err:
                logger.warning(f"⚠️ Facts extraction failed (non-fatal): {facts_err}")

        if errors:
            logger.warning(f"⚠️ Completed with {len(errors)} errors")
            for error in errors:
                logger.error(f"   - {error.get('node', 'unknown')}: {error.get('error', 'unknown')}")

        logger.info(f"✅ Background processing complete for: {doc_id}")

    except Exception as e:
        logger.error(f"❌ Background processing error for {doc_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())

        # Update document status to failed
        try:
            db.refresh(doc)
            doc.status = "failed"
            if not doc.doc_metadata:
                doc.doc_metadata = {}
            doc.doc_metadata["processing_error"] = str(e)
            doc.doc_metadata["error_trace"] = traceback.format_exc()
            db.commit()
            logger.error(f"❌ Document {doc_id} marked as failed")
        except Exception as update_error:
            logger.error(f"❌ Failed to update document status: {update_error}")

    finally:
        db.close()
        logger.info(f"🔒 Database session closed for: {doc_id}")


# @router.post("/ask")
# async def ask_all_documents(request: dict, db: Session = Depends(get_db)):
#     """Ask a question across ALL documents in the library."""
#     question = request.get("question", "").strip()
#     if not question:
#         from fastapi import HTTPException
#         raise HTTPException(status_code=400, detail="Question required")
#
#     # Pull summaries + insights from all completed docs
#     rows = db.execute(text("""
#         SELECT filename, doc_type, summary, doc_metadata
#         FROM documents
#         WHERE status = 'completed'
#         ORDER BY created_at DESC
#         LIMIT 20
#     """)).fetchall()
#
#     if not rows:
#         async def empty():
#             yield f"data: {json.dumps({'token': 'No documents found in your library.'})}\n\n"
#             yield f"data: {json.dumps({'done': True})}\n\n"
#         return StreamingResponse(empty(), media_type="text/event-stream")
#
#     # Build context from all docs
#     context_parts = []
#     for row in rows:
#         meta = row.doc_metadata or {}
#         insights = meta.get("insights", [])
#         insight_texts = [i.get("title", "") + ": " + i.get("detail", "")
#                         for i in insights[:3] if i.get("title")]
#
#         part = f"""
# Document: {row.filename}
# Type: {row.doc_type or 'unknown'}
# Summary: {(row.summary or '')[:300]}
# Key Insights: {'; '.join(insight_texts) if insight_texts else 'None'}
# """
#         context_parts.append(part.strip())
#
#     context = "\n\n---\n\n".join(context_parts)
#
#     prompt = f"""You are a document intelligence assistant with access to the user's document library.
#
# Here are all the documents in the library:
#
# {context}
#
# Answer the following question using ONLY the information from these documents.
# Be specific and cite document names when referencing data.
# If the answer cannot be found, say so clearly.
#
# Question: {question}
#
# Answer:"""
#
#     async def stream_response():
#         try:
#             stream = ollama_client.generate(
#                 model=config.get_active_model(),
#                 prompt=prompt,
#                 stream=True
#             )
#             for chunk in stream:
#                 token = chunk.get("response", "")
#                 if token:
#                     yield f"data: {json.dumps({'token': token})}\n\n"
#             yield f"data: {json.dumps({'done': True})}\n\n"
#         except Exception as e:
#             logger.error(f"Global ask failed: {e}")
#             yield f"data: {json.dumps({'error': str(e)})}\n\n"
#
#     return StreamingResponse(stream_response(), media_type="text/event-stream")