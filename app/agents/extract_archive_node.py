"""
extract_archive_node — runs after precheck when the file is an archive (zip/rar/7z/tar/gz).

Extracts each entry to the documents storage dir, creates a child Document record,
and queues each child through the full processing pipeline.

The parent archive document gets:
  - status = "extracted"
  - doc_type = "archive"
  - child_count = N
  - doc_metadata.children = [list of child doc IDs]

Children are standard Document rows with parent_doc_id set. They go through the full
ingest → precheck → parse → analyze → embed pipeline independently.
"""

import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

_ARCHIVE_EXTS = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2"}
_MAX_ENTRIES  = 100   # safety cap — don't explode on huge ZIPs
_MAX_BYTES    = 500 * 1024 * 1024  # 500 MB total extracted size cap


def _is_archive(filename: str) -> bool:
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext in _ARCHIVE_EXTS


def _extract_zip(archive_path: str, dest_dir: str) -> List[Dict[str, Any]]:
    """Extract a ZIP and return list of {filename, path, size}."""
    entries = []
    total_bytes = 0
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not name or name.startswith(".") or name.startswith("__"):
                continue
            if len(entries) >= _MAX_ENTRIES:
                logger.warning(f"[ExtractNode] Capped at {_MAX_ENTRIES} entries")
                break
            total_bytes += info.file_size
            if total_bytes > _MAX_BYTES:
                logger.warning("[ExtractNode] Total size cap reached")
                break
            dest = os.path.join(dest_dir, f"{uuid.uuid4().hex}_{name}")
            with zf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            entries.append({"filename": name, "path": dest, "size": info.file_size})
    return entries


def _extract_tar(archive_path: str, dest_dir: str) -> List[Dict[str, Any]]:
    """Extract a TAR/GZ/TGZ/BZ2 and return list of {filename, path, size}."""
    import tarfile
    entries = []
    total_bytes = 0
    with tarfile.open(archive_path, "r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name
            if not name or name.startswith("."):
                continue
            if len(entries) >= _MAX_ENTRIES:
                break
            total_bytes += member.size
            if total_bytes > _MAX_BYTES:
                break
            dest = os.path.join(dest_dir, f"{uuid.uuid4().hex}_{name}")
            f = tf.extractfile(member)
            if f:
                with open(dest, "wb") as dst:
                    shutil.copyfileobj(f, dst)
                entries.append({"filename": name, "path": dest, "size": member.size})
    return entries


def _extract_7z(archive_path: str, dest_dir: str) -> List[Dict[str, Any]]:
    """Extract a 7-Zip archive using py7zr (optional dep)."""
    try:
        import py7zr
    except ImportError:
        logger.warning("[ExtractNode] py7zr not installed — cannot extract .7z files")
        return []
    entries = []
    with py7zr.SevenZipFile(archive_path, mode="r") as zf:
        for name, bio in zf.read().items():
            if not name or name.endswith("/"):
                continue
            base = Path(name).name
            dest = os.path.join(dest_dir, f"{uuid.uuid4().hex}_{base}")
            with open(dest, "wb") as dst:
                shutil.copyfileobj(bio, dst)
            size = os.path.getsize(dest)
            entries.append({"filename": base, "path": dest, "size": size})
            if len(entries) >= _MAX_ENTRIES:
                break
    return entries


def _extract_rar(archive_path: str, dest_dir: str) -> List[Dict[str, Any]]:
    """Extract a RAR archive using rarfile (optional dep)."""
    try:
        import rarfile
    except ImportError:
        logger.warning("[ExtractNode] rarfile not installed — cannot extract .rar files")
        return []
    entries = []
    with rarfile.RarFile(archive_path) as rf:
        for info in rf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            dest = os.path.join(dest_dir, f"{uuid.uuid4().hex}_{name}")
            with rf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            entries.append({"filename": name, "path": dest, "size": info.file_size})
            if len(entries) >= _MAX_ENTRIES:
                break
    return entries


def _dispatch_extraction(archive_path: str, filename: str, dest_dir: str) -> List[Dict[str, Any]]:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "zip":
        return _extract_zip(archive_path, dest_dir)
    if ext in ("tar", "gz", "tgz", "bz2"):
        return _extract_tar(archive_path, dest_dir)
    if ext == "7z":
        return _extract_7z(archive_path, dest_dir)
    if ext == "rar":
        return _extract_rar(archive_path, dest_dir)
    return []


def extract_archive_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node: extract archive and spawn child document records.
    Only runs when state["is_archive"] is True (set by precheck routing).
    Returns immediately — child processing happens in background threads.
    """
    if not state.get("is_archive"):
        return state

    file_path = state.get("file_path", "")
    filename  = state.get("filename", "")
    doc_id    = state.get("document_id", "")

    logger.info(f"📦 [ExtractNode] Extracting archive: {filename}")

    try:
        from app.core.database import SessionLocal
        from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
        from app.models.document import Document
        from app.services.processing_service import ProcessingService
        from app.core.config import settings
        import threading

        # Extract to a sibling dir next to the archive
        dest_dir = str(settings.UPLOAD_DIR)
        entries = _dispatch_extraction(file_path, filename, dest_dir)

        if not entries:
            logger.warning(f"[ExtractNode] No extractable files found in {filename}")
            return {
                **state,
                "processing_complete": True,
                "summary": f"Archive contained no supported files.",
                "doc_type": "archive",
            }

        db = SessionLocal()
        child_ids = []
        try:
            for entry in entries:
                child_id = str(uuid.uuid4())
                child_doc = Document(
                    id=child_id,
                    filename=entry["filename"],
                    file_path=entry["path"],
                    file_size=entry["size"],
                    status="pending",
                    parent_doc_id=doc_id,
                    tenant_id=get_current_tenant_id() or DEFAULT_TENANT_ID,
                )
                db.add(child_doc)
                child_ids.append(child_id)

            # Update parent archive record
            parent = db.query(Document).filter(Document.id == doc_id).first()
            if parent:
                parent.status = "extracted"
                parent.doc_type = "archive"
                parent.child_count = len(child_ids)
                parent.doc_metadata = {
                    **(parent.doc_metadata or {}),
                    "children": child_ids,
                    "child_count": len(child_ids),
                    "extracted_files": [e["filename"] for e in entries],
                }

            db.commit()
        finally:
            db.close()

        # Queue each child through the full pipeline in background threads
        for entry, child_id in zip(entries, child_ids):
            t = threading.Thread(
                target=ProcessingService.process_document_background,
                args=(child_id, entry["path"], entry["filename"]),
                daemon=True,
            )
            t.start()

        logger.info(f"✅ [ExtractNode] Extracted {len(child_ids)} files from {filename}")

        return {
            **state,
            "processing_complete": True,
            "doc_type": "archive",
            "summary": f"Archive containing {len(child_ids)} files. Each file is being analyzed individually.",
            "doc_metadata": {
                **(state.get("doc_metadata") or {}),
                "children": child_ids,
                "child_count": len(child_ids),
                "extracted_files": [e["filename"] for e in entries],
            },
        }

    except Exception as e:
        logger.error(f"❌ [ExtractNode] Failed to extract {filename}: {e}", exc_info=True)
        return {
            **state,
            "errors": [*state.get("errors", []), f"archive_extraction_failed: {e}"],
            "processing_complete": True,
        }
