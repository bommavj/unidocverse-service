import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import config
from app.core.database import get_db
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
from app.models.document import Document
from app.schemas.document import DocumentUploadResponse
from app.core.paths import UPLOADS_DIR
from app.services.processing_service import ProcessingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])

# Bounded worker pool — prevents unbounded thread creation on bulk uploads.
# max_workers = CPU count capped at 4; queue is unlimited (futures just wait).
_INGEST_WORKERS = min(4, os.cpu_count() or 2)
_ingest_pool = ThreadPoolExecutor(max_workers=_INGEST_WORKERS, thread_name_prefix="ingest")
logger.info(f"🔧 Ingest pool: {_INGEST_WORKERS} workers")


storage_path = UPLOADS_DIR


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
        request: Request,
        file: UploadFile = File(...),
        lang: Optional[str] = None,
        client_id: Optional[str] = None,
        allow_duplicate: bool = False,
        db: Session = Depends(get_db)
):
    """Upload document - processing happens in background.

    The same file (byte-for-byte) already uploaded by this organisation returns 409 with
    the existing document, unless allow_duplicate=true ("Upload anyway")."""

    try:
        from app.core.language import get_request_language
        doc_lang = lang or get_request_language(request, db)

        # Validate file type
        file_extension = Path(file.filename).suffix.lower()
        # 🔍 DEBUG: Log to verify settings are loaded
        print(f"📁 File extension: {file_extension}")

        if file_extension not in config.settings.ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type. Allowed: {', '.join(config.settings.ALLOWED_EXTENSIONS)}"
            )


        logger.info(f"📤 Upload started: {file.filename} (lang={doc_lang})")

        # Read file content first
        content = await file.read()

        from app.services import duplicates
        content_hash = duplicates.fingerprint(content)
        existing = duplicates.find(db, get_current_tenant_id() or DEFAULT_TENANT_ID, content_hash)
        if existing and not allow_duplicate:
            raise HTTPException(status_code=409, detail={
                "code": "duplicate", "existing": existing,
                "message": f"This file is already uploaded as {existing['filename']}"})

        # Generate file path
        #storage_path = Path("storage/uploads")
        storage_path.mkdir(parents=True, exist_ok=True)

        # Use a temporary ID for the filename
        import uuid
        temp_id = str(uuid.uuid4())
        file_path = storage_path / f"{temp_id}_{file.filename}"

        # Save file to storage
        with file_path.open("wb") as buffer:
            buffer.write(content)

        # Create document record with file_path
        doc = Document(
            filename=file.filename,
            file_path=str(file_path),
            file_size=len(content),
            status="pending",
            doc_type=None,
            language=doc_lang,
            client_id=client_id or None,
            tenant_id=get_current_tenant_id() or DEFAULT_TENANT_ID,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        doc_id = str(doc.id)
        duplicates.remember(db, doc_id, content_hash)

        # Rename file to use actual doc ID
        final_file_path = storage_path / f"{doc_id}_{file.filename}"
        file_path.rename(final_file_path)

        # Update file path in database
        doc.file_path = str(final_file_path)
        db.commit()

        logger.info(f"💾 File saved: {file.filename} ({len(content)} bytes)")
        from app.core.audit import record as audit
        from app.core.audit_middleware import _claims
        audit("document.uploaded", actor=_claims(request).get("username") or "api-token", doc_id=doc_id,
              detail=file.filename, ip=request.client.host if request.client else None,
              extra={"file_size": len(content), "mime_type": file.content_type, "client_id": client_id})

        # Submit to bounded ingest pool — no unbounded thread creation
        _ingest_pool.submit(
            ProcessingService.process_document_background,
            doc_id, str(final_file_path), file.filename
        )

        logger.info(f"🚀 Background processing queued for: {file.filename} (pool workers={_INGEST_WORKERS})")

        return DocumentUploadResponse(
            id=doc_id,
            filename=file.filename,
            file_size=len(content),
            status="processing",
            message="File uploaded successfully. Processing in background..."
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Upload failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))