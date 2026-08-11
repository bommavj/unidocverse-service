import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
from sqlalchemy.orm import Session

from app.models.document import Document
from app.agents.workflow import compile_workflow
from app.core.pii_redactor import redact_text, redact_metadata
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

langgraph_app = None


def initialize_workflow():
    global langgraph_app
    if langgraph_app is None:
        logger.info("🤖 Compiling LangGraph workflow...")
        langgraph_app = compile_workflow()
        logger.info("✅ LangGraph workflow ready")
    return langgraph_app


def _clean(val) -> str:
    """Safely convert any value to a clean string — handles str, list, dict, None."""
    if val is None:
        return ""
    if isinstance(val, list):
        val = " ".join(str(v) for v in val if v)
    elif isinstance(val, dict):
        val = val.get("text") or val.get("summary") or str(val)
    return sanitize_unicode(val)


def sanitize_unicode(val) -> str:
    """
    Convert Unicode escape sequences to actual characters, then strip non-ASCII.
    Handles \\u2019, \\n, \\t, etc. safely for SQL_ASCII databases.
    """
    if val is None:
        return ""

    val = str(val)

    # Remove null characters
    val = val.replace("\x00", "").replace("\u0000", "")

    # Decode Unicode escape sequences (e.g., \u2019 → ')
    try:
        val = val.encode('utf-8').decode('unicode_escape')
    except (UnicodeDecodeError, UnicodeEncodeError):
        # Fallback: remove unknown escapes
        val = re.sub(r'\\u[0-9a-fA-F]{4}', '', val)

    # Strip non-ASCII characters (SQL_ASCII can't handle them)
    val = val.encode('ascii', 'ignore').decode('ascii')

    return val.strip()


def sanitize_nan_to_none(obj):
    """
    Recursively makes obj JSON-safe:
    - float NaN / Inf → None
    - pd.Timestamp / datetime → ISO string
    - numpy int/float scalars → Python int/float
    - bytes → None
    """
    if isinstance(obj, dict):
        return {k: sanitize_nan_to_none(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_nan_to_none(x) for x in obj]
    elif isinstance(obj, float) or (type(obj).__name__ in ('float64', 'float32', 'floating')):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return float(obj)
    elif type(obj).__name__ in ('int64', 'int32', 'int16', 'int8', 'uint64', 'uint32', 'uint16', 'uint8', 'integer'):
        return int(obj)
    elif hasattr(obj, 'isoformat'):
        # pd.Timestamp, datetime.datetime, datetime.date
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    elif isinstance(obj, bytes):
        return None
    return obj


def sanitize_metadata_strings(obj):
    """Recursively sanitize all string values in dict/list for SQL_ASCII."""
    if isinstance(obj, dict):
        return {k: sanitize_unicode(v) if isinstance(v, str) else sanitize_metadata_strings(v)
                for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_unicode(x) if isinstance(x, str) else sanitize_metadata_strings(x)
                for x in obj]
    elif isinstance(obj, str):
        return sanitize_unicode(obj)
    return obj


class ProcessingService:

    @staticmethod
    def process_sync(file_bytes: bytes, filename: str, content_type: str) -> dict:
        """
        Synchronous upload + process — saves file, runs full pipeline, returns result.
        Used by the upload endpoints that process in a thread pool.
        """
        import uuid
        from sqlalchemy import text
        from app.core.database import SessionLocal

        doc_id = str(uuid.uuid4())
        upload_dir = Path.home() / "Library/Application Support/UniDocVerse/storage/uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix
        file_path = str(upload_dir / f"{doc_id}{ext}")

        with open(file_path, "wb") as f:
            f.write(file_bytes)

        db = SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO documents
                    (id, filename, file_path, file_size, mime_type, status, processing_stage, tenant_id)
                VALUES
                    (:id, :filename, :file_path, :file_size, :mime_type, :status, :processing_stage, :tenant_id)
            """), {
                "id": doc_id, "filename": filename, "file_path": file_path,
                "file_size": len(file_bytes), "mime_type": content_type,
                "status": "processing", "processing_stage": "parsing",
                "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID,
            })
            db.commit()
        finally:
            db.close()

        try:
            import asyncio
            asyncio.run(ProcessingService._process_async(doc_id, file_path, filename))
            return {"status": "success", "document_id": doc_id, "filename": filename}
        except Exception as e:
            return {"status": "error", "error": str(e), "filename": filename}

    @staticmethod
    def process_document_background(doc_id: str, file_path: str, filename: str):
        """Entry point from background thread — runs the async pipeline."""
        asyncio.run(ProcessingService._process_async(doc_id, file_path, filename))

    @staticmethod
    async def _process_async(doc_id: str, file_path: str, filename: str):
        """Async processing pipeline."""
        from app.core.database import SessionLocal
        db = SessionLocal()

        try:
            import time as _time
            _t0_pipeline = _time.perf_counter()
            logger.info(f"🔄 [Background] Starting: {filename}")

            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                logger.error(f"❌ Document not found: {doc_id}")
                return

            doc.status = "processing"
            doc.processing_stage = "parsing"
            db.commit()

            workflow = initialize_workflow()

            initial_state = {
                "document_id": str(doc_id),
                "filename": filename,
                "file_path": file_path,
                "file_size": doc.file_size,
                "raw_text": "",
                "cleaned_text": "",
                "summary": "",
                "classification": {},
                "insights": [],
                "anomalies": [],
                "quality_issues": [],
                "errors": [],
                "needs_manual_review": False,
                "processing_complete": False,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "doc_metadata": {},
                "reprocess_count": 0,
            }

            logger.info(f"🤖 Running AI pipeline for: {filename}")
            final_state = await workflow.ainvoke(initial_state, {"recursion_limit": 50})

            # Password-protected — surface to user, skip rest of pipeline
            if final_state.get("password_required"):
                doc = db.query(Document).filter(Document.id == doc_id).first()
                if doc:
                    doc.status = "password_required"
                    doc.doc_metadata = {"errors": ["password_required"], "password_required": True}
                    db.commit()
                logger.warning(f"🔒 [Background] Password-protected: {filename}")
                return

            # Store results FIRST — doc_type must be in DB before PostProcessingRegistry
            doc = db.query(Document).filter(Document.id == doc_id).first()
            ProcessingService._store_results(db, doc, final_state, filename)
            db.refresh(doc)

            # Post-processing hooks (AMS workflow, agents, etc.)
            try:
                from app.services.post_processing_registry import PostProcessingRegistry
                PostProcessingRegistry.run(doc_id, db)
            except Exception as pp_err:
                logger.warning(f"⚠ [PostProc] Non-fatal: {pp_err}")

            # Immigration writeback — if doc linked to a client and is immigration type
            try:
                db.refresh(doc)
                if doc.client_id and doc.doc_type in (
                    "uscis_receipt", "immigration_receipt", "immigration_document",
                    "visa_document", "i94", "h1b", "ead", "i797", "i140", "passport",
                ):
                    from app.services.immigration_service import upsert_immigration_from_doc
                    from app.services.immigration_cross_linker import reason as cross_link_reason
                    from app.models.client_immigration import ClientImmigration
                    analyzed = (doc.doc_metadata or {}).get("analyzed_data") or doc.doc_metadata or {}
                    upsert_immigration_from_doc(db, doc.client_id, doc.id, analyzed)

                    # Re-run cross-link and store result on each record in the group
                    all_recs = db.query(ClientImmigration).filter_by(client_id=doc.client_id).all()
                    if len(all_recs) > 1:
                        analysis = cross_link_reason(all_recs)
                        logger.info(
                            f"🔗 [CrossLink] {len(all_recs)} immigration docs for client {doc.client_id} — "
                            f"{len(analysis.get('insights', []))} insights, {len(analysis.get('alerts', []))} alerts"
                        )
            except Exception as imm_err:
                logger.warning(f"⚠ [Immigration] Non-fatal: {imm_err}")

            doc.processing_stage = "done"
            db.commit()
            _total = _time.perf_counter() - _t0_pipeline
            logger.info(f"✅ [Background] Completed: {filename} — total pipeline {_total:.1f}s")

        except Exception as e:
            logger.error(f"❌ [Background] Failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            try:
                doc.status = "failed"
                doc.processing_stage = "error"
                doc.doc_metadata = {"error": str(e)}
                db.commit()
            except Exception:
                pass
        finally:
            db.close()

    @staticmethod
    def _store_results(db: Session, doc: Document,
                       final_state: Dict[str, Any], filename: str):
        """Persist pipeline results to the document record."""

        # Text fields — handle any type gracefully
        raw_text = _clean(final_state.get("cleaned_text") or final_state.get("raw_text", ""))
        summary = _clean(final_state.get("summary", ""))

        if raw_text:
            doc.raw_text = redact_text(raw_text)
        if summary:
            doc.summary = redact_text(summary)

        # Classification → doc_type
        classification = final_state.get("classification") or {}
        if classification:
            doc.doc_type = classification.get("type")

        # Embedding
        embedding = final_state.get("embedding")
        if embedding is not None:
            import numpy as np
            if isinstance(embedding, np.ndarray):
                embedding = embedding.tolist()
            if isinstance(embedding, list) and len(embedding) == 768:
                try:
                    doc.embedding = [float(x) for x in embedding]
                    logger.info(f"✅ Stored embedding for: {filename}")
                except (TypeError, ValueError):
                    pass

        # Metadata
        doc_metadata = final_state.get("doc_metadata") or {}

        # Deduplicate insights by title
        seen, merged = set(), []
        for ins in (final_state.get("insights") or doc_metadata.get("insights") or []):
            title = (ins.get("title", "") if isinstance(ins, dict) else str(ins)).lower().strip()
            if title not in seen:
                seen.add(title)
                merged.append(ins)

        raw_metadata = {
            **doc_metadata,
            "classification": classification,
            "metrics": final_state.get("metrics") or {},
            "keywords": final_state.get("keywords") or [],
            "key_points": final_state.get("key_points") or [],
            "insights": merged,
            "anomalies": final_state.get("anomalies") or [],
            "quality_issues": final_state.get("quality_issues") or [],
            "errors": final_state.get("errors") or [],
            # analyzed_data — required by AMS workflow
            "analyzed_data": (
                    final_state.get("analyzed_data") or
                    doc_metadata.get("analyzed_data") or
                    {}
            ),
        }

        doc.doc_metadata = redact_metadata(sanitize_metadata_strings(sanitize_nan_to_none(raw_metadata)))

        doc.status = "completed"
        doc.processed_at = datetime.now(timezone.utc)

        if final_state.get("needs_manual_review"):
            confidence = classification.get("confidence", 0)
            logger.warning(f"⚠️ Low confidence ({confidence:.2%}): {filename}")

        db.commit()

# import asyncio
# import logging
# import math
# from datetime import datetime, timezone
# from typing import Dict, Any
# from sqlalchemy.orm import Session
#
# from app.models.document import Document
# from app.agents.workflow import compile_workflow
#
# logger = logging.getLogger(__name__)
#
# langgraph_app = None
#
#
# def initialize_workflow():
#     global langgraph_app
#     if langgraph_app is None:
#         logger.info("🤖 Compiling LangGraph workflow...")
#         langgraph_app = compile_workflow()
#         logger.info("✅ LangGraph workflow ready")
#     return langgraph_app
#
#
# def _clean(val) -> str:
#     """Safely convert any value to a clean string — handles str, list, dict, None."""
#     if val is None:
#         return ""
#     if isinstance(val, list):
#         val = " ".join(str(v) for v in val if v)
#     elif isinstance(val, dict):
#         val = val.get("text") or val.get("summary") or str(val)
#     return str(val).replace("\x00", "").replace("\u0000", "").strip()
#
#
# def sanitize_nan_to_none(obj):
#     """
#     Recursively replaces float('nan'), float('inf'), and float('-inf')
#     with None, which serializes to standard JSON 'null'.
#     """
#     if isinstance(obj, dict):
#         return {k: sanitize_nan_to_none(v) for k, v in obj.items()}
#     elif isinstance(obj, list):
#         return [sanitize_nan_to_none(x) for x in obj]
#     elif isinstance(obj, float) or (type(obj).__name__ in ('float64', 'float32', 'floating')):
#         if math.isnan(obj) or math.isinf(obj):
#             return None
#     return obj
#
#
# class ProcessingService:
#
#     @staticmethod
#     def process_document_background(doc_id: str, file_path: str, filename: str):
#         """Entry point from background thread — runs the async pipeline."""
#         asyncio.run(ProcessingService._process_async(doc_id, file_path, filename))
#
#     @staticmethod
#     async def _process_async(doc_id: str, file_path: str, filename: str):
#         """Async processing pipeline."""
#         from app.core.database import SessionLocal
#         db = SessionLocal()
#
#         try:
#             logger.info(f"🔄 [Background] Starting: {filename}")
#
#             doc = db.query(Document).filter(Document.id == doc_id).first()
#             if not doc:
#                 logger.error(f"❌ Document not found: {doc_id}")
#                 return
#
#             doc.status = "processing"
#             db.commit()
#
#             workflow = initialize_workflow()
#
#             initial_state = {
#                 "document_id":       str(doc_id),
#                 "filename":          filename,
#                 "file_path":         file_path,
#                 "file_size":         doc.file_size,
#                 "raw_text":          "",
#                 "cleaned_text":      "",
#                 "summary":           "",
#                 "classification":    {},
#                 "insights":          [],
#                 "anomalies":         [],
#                 "quality_issues":    [],
#                 "errors":            [],
#                 "needs_manual_review": False,
#                 "processing_complete": False,
#                 "started_at":        datetime.now(timezone.utc).isoformat(),
#                 "doc_metadata":      {},
#                 "reprocess_count":   0,
#             }
#
#             logger.info(f"🤖 Running AI pipeline for: {filename}")
#             final_state = await workflow.ainvoke(initial_state, {"recursion_limit": 50})
#
#             # Store results FIRST — doc_type must be in DB before PostProcessingRegistry
#             doc = db.query(Document).filter(Document.id == doc_id).first()
#             ProcessingService._store_results(db, doc, final_state, filename)
#             db.refresh(doc)
#
#             # Post-processing hooks (AMS workflow, agents, etc.)
#             try:
#                 from app.services.post_processing_registry import PostProcessingRegistry
#                 PostProcessingRegistry.run(doc_id, db)
#             except Exception as pp_err:
#                 logger.warning(f"⚠ [PostProc] Non-fatal: {pp_err}")
#
#             logger.info(f"✅ [Background] Completed: {filename}")
#
#         except Exception as e:
#             logger.error(f"❌ [Background] Failed: {e}")
#             import traceback
#             logger.error(traceback.format_exc())
#             try:
#                 doc.status = "failed"
#                 doc.doc_metadata = {"error": str(e)}
#                 db.commit()
#             except Exception:
#                 pass
#         finally:
#             db.close()
#
#     @staticmethod
#     def _store_results(db: Session, doc: Document,
#                        final_state: Dict[str, Any], filename: str):
#         """Persist pipeline results to the document record."""
#
#         # Text fields — handle any type gracefully
#         raw_text = _clean(final_state.get("cleaned_text") or final_state.get("raw_text", ""))
#         summary  = _clean(final_state.get("summary", ""))
#
#         if raw_text:
#             doc.raw_text = raw_text
#         if summary:
#             doc.summary = summary
#
#         # Classification → doc_type
#         classification = final_state.get("classification") or {}
#         if classification:
#             doc.doc_type = classification.get("type")
#
#         # Embedding
#         embedding = final_state.get("embedding")
#         if embedding is not None:
#             import numpy as np
#             if isinstance(embedding, np.ndarray):
#                 embedding = embedding.tolist()
#             if isinstance(embedding, list) and len(embedding) == 768:
#                 try:
#                     doc.embedding = [float(x) for x in embedding]
#                     logger.info(f"✅ Stored embedding for: {filename}")
#                 except (TypeError, ValueError):
#                     pass
#
#         # Metadata
#         doc_metadata = final_state.get("doc_metadata") or {}
#
#         # Deduplicate insights by title
#         seen, merged = set(), []
#         for ins in (final_state.get("insights") or doc_metadata.get("insights") or []):
#             title = (ins.get("title", "") if isinstance(ins, dict) else str(ins)).lower().strip()
#             if title not in seen:
#                 seen.add(title)
#                 merged.append(ins)
#
#         raw_metadata = {
#             **doc_metadata,
#             "classification":  classification,
#             "metrics":         final_state.get("metrics") or {},
#             "keywords":        final_state.get("keywords") or [],
#             "key_points":      final_state.get("key_points") or [],
#             "insights":        merged,
#             "anomalies":       final_state.get("anomalies") or [],
#             "quality_issues":  final_state.get("quality_issues") or [],
#             "errors":          final_state.get("errors") or [],
#             # analyzed_data — required by AMS workflow
#             "analyzed_data": (
#                 final_state.get("analyzed_data") or
#                 doc_metadata.get("analyzed_data") or
#                 {}
#             ),
#         }
#
#         doc.doc_metadata = sanitize_nan_to_none(raw_metadata)
#
#         doc.status       = "completed"
#         doc.processed_at = datetime.now(timezone.utc)
#
#         if final_state.get("needs_manual_review"):
#             confidence = classification.get("confidence", 0)
#             logger.warning(f"⚠️ Low confidence ({confidence:.2%}): {filename}")
#
#         db.commit()

# import asyncio
# import logging
# from datetime import datetime, timezone
# from typing import Dict, Any
# from sqlalchemy.orm import Session
#
# from app.models.document import Document
# from app.agents.workflow import compile_workflow
#
# logger = logging.getLogger(__name__)
#
# langgraph_app = None
#
#
# def initialize_workflow():
#     global langgraph_app
#     if langgraph_app is None:
#         logger.info("🤖 Compiling LangGraph workflow...")
#         langgraph_app = compile_workflow()
#         logger.info("✅ LangGraph workflow ready")
#     return langgraph_app
#
#
# def _clean(val) -> str:
#     """Safely convert any value to a clean string — handles str, list, dict, None."""
#     if val is None:
#         return ""
#     if isinstance(val, list):
#         val = " ".join(str(v) for v in val if v)
#     elif isinstance(val, dict):
#         val = val.get("text") or val.get("summary") or str(val)
#     return str(val).replace("\x00", "").replace("\u0000", "").strip()
#
#
# class ProcessingService:
#
#     @staticmethod
#     def process_document_background(doc_id: str, file_path: str, filename: str):
#         """Entry point from background thread — runs the async pipeline."""
#         asyncio.run(ProcessingService._process_async(doc_id, file_path, filename))
#
#     @staticmethod
#     async def _process_async(doc_id: str, file_path: str, filename: str):
#         """Async processing pipeline."""
#         from app.core.database import SessionLocal
#         db = SessionLocal()
#
#         try:
#             logger.info(f"🔄 [Background] Starting: {filename}")
#
#             doc = db.query(Document).filter(Document.id == doc_id).first()
#             if not doc:
#                 logger.error(f"❌ Document not found: {doc_id}")
#                 return
#
#             doc.status = "processing"
#             db.commit()
#
#             workflow = initialize_workflow()
#
#             initial_state = {
#                 "document_id":       str(doc_id),
#                 "filename":          filename,
#                 "file_path":         file_path,
#                 "file_size":         doc.file_size,
#                 "raw_text":          "",
#                 "cleaned_text":      "",
#                 "summary":           "",
#                 "classification":    {},
#                 "insights":          [],
#                 "anomalies":         [],
#                 "quality_issues":    [],
#                 "errors":            [],
#                 "needs_manual_review": False,
#                 "processing_complete": False,
#                 "started_at":        datetime.now(timezone.utc).isoformat(),
#                 "doc_metadata":      {},
#                 "reprocess_count":   0,
#             }
#
#             logger.info(f"🤖 Running AI pipeline for: {filename}")
#             final_state = await workflow.ainvoke(initial_state, {"recursion_limit": 50})
#
#             # Store results FIRST — doc_type must be in DB before PostProcessingRegistry
#             doc = db.query(Document).filter(Document.id == doc_id).first()
#             ProcessingService._store_results(db, doc, final_state, filename)
#             db.refresh(doc)
#
#             # Post-processing hooks (AMS workflow, agents, etc.)
#             try:
#                 from app.services.post_processing_registry import PostProcessingRegistry
#                 PostProcessingRegistry.run(doc_id, db)
#             except Exception as pp_err:
#                 logger.warning(f"⚠ [PostProc] Non-fatal: {pp_err}")
#
#             logger.info(f"✅ [Background] Completed: {filename}")
#
#         except Exception as e:
#             logger.error(f"❌ [Background] Failed: {e}")
#             import traceback
#             logger.error(traceback.format_exc())
#             try:
#                 doc.status = "failed"
#                 doc.doc_metadata = {"error": str(e)}
#                 db.commit()
#             except Exception:
#                 pass
#         finally:
#             db.close()
#
#     @staticmethod
#     def _store_results(db: Session, doc: Document,
#                        final_state: Dict[str, Any], filename: str):
#         """Persist pipeline results to the document record."""
#
#         # Text fields — handle any type gracefully
#         raw_text = _clean(final_state.get("cleaned_text") or final_state.get("raw_text", ""))
#         summary  = _clean(final_state.get("summary", ""))
#
#         if raw_text:
#             doc.raw_text = raw_text
#         if summary:
#             doc.summary = summary
#
#         # Classification → doc_type
#         classification = final_state.get("classification") or {}
#         if classification:
#             doc.doc_type = classification.get("type")
#
#         # Embedding
#         embedding = final_state.get("embedding")
#         if embedding is not None:
#             import numpy as np
#             if isinstance(embedding, np.ndarray):
#                 embedding = embedding.tolist()
#             if isinstance(embedding, list) and len(embedding) == 768:
#                 try:
#                     doc.embedding = [float(x) for x in embedding]
#                     logger.info(f"✅ Stored embedding for: {filename}")
#                 except (TypeError, ValueError):
#                     pass
#
#         # Metadata
#         doc_metadata = final_state.get("doc_metadata") or {}
#
#         # Deduplicate insights by title
#         seen, merged = set(), []
#         for ins in (final_state.get("insights") or doc_metadata.get("insights") or []):
#             title = (ins.get("title", "") if isinstance(ins, dict) else str(ins)).lower().strip()
#             if title not in seen:
#                 seen.add(title)
#                 merged.append(ins)
#
#         doc.doc_metadata = {
#             **doc_metadata,
#             "classification":  classification,
#             "metrics":         final_state.get("metrics") or {},
#             "keywords":        final_state.get("keywords") or [],
#             "key_points":      final_state.get("key_points") or [],
#             "insights":        merged,
#             "anomalies":       final_state.get("anomalies") or [],
#             "quality_issues":  final_state.get("quality_issues") or [],
#             "errors":          final_state.get("errors") or [],
#             # analyzed_data — required by AMS workflow
#             "analyzed_data": (
#                 final_state.get("analyzed_data") or
#                 doc_metadata.get("analyzed_data") or
#                 {}
#             ),
#         }
#
#         doc.status       = "completed"
#         doc.processed_at = datetime.now(timezone.utc)
#
#         if final_state.get("needs_manual_review"):
#             confidence = classification.get("confidence", 0)
#             logger.warning(f"⚠️ Low confidence ({confidence:.2%}): {filename}")
#
#         db.commit()