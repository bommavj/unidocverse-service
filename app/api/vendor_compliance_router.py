"""
Vendor tax compliance API.

POST /api/vendor-compliance/w9-check   one W-9 at vendor onboarding → pass / review / fail
    multipart: file (PDF or image), vendor_name (optional — the name being set up)
The upload is checked in a temp file and deleted; only the last 4 TIN digits are returned.
"""
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.services.tax.vendor_audit import FORM_EXTS, onboard_check

router = APIRouter(prefix="/api/vendor-compliance", tags=["vendor-compliance"])

_MAX_BYTES = 20 * 1024 * 1024


@router.post("/w9-check")
async def w9_check(file: UploadFile = File(...), vendor_name: Optional[str] = Form(None)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in FORM_EXTS:
        raise HTTPException(400, f"Upload a PDF or image ({', '.join(sorted(FORM_EXTS))})")
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "File is larger than 20 MB")
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        result = onboard_check(Path(tmp), vendor_name)
    finally:
        os.unlink(tmp)                      # the W-9 (and its TIN) is never kept
    result["file"] = file.filename
    return result
