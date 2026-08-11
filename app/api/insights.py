"""
insights_router.py
==================
REST API for Actionable Insights across all document domains.

Endpoints:
  GET  /api/insights/portfolio          ← Cross-doc portfolio summary
  GET  /api/insights/alerts             ← Critical + warning alerts only
  GET  /api/insights/document/{doc_id}  ← Per-document insights
  POST /api/insights/regenerate/{doc_id}← Re-run extractor on a doc
  GET  /api/insights/tags               ← All tags with counts (for filtering)
  GET  /api/insights/stats              ← Global stats for dashboard header
  POST /api/insights/run-all            ← Batch generate for all docs
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.services.document_service import DocumentService
from app.services.insight_extractor_service import InsightExtractorService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/insights", tags=["insights"])

_extractor = InsightExtractorService()


# =============================================================================
# SCHEMA NORMALIZATION
# Handles both old LangGraph insight schema and new extractor schema
# =============================================================================

def _doc_type_to_domain(doc_type: str) -> str:
    aliases = {
        "tax_form":        "tax",
        "bank_statement":  "bank",
        "sales_report":    "sales",
        "payroll":         "payroll",
        "government_form": "compliance",
        "insurance":       "insurance",
        "document":        "insurance",
        "loan":            "loan",
        "legal":           "legal",
        "medical":         "medical",
        "medical_eob":     "medical",
        "eob":             "medical",
        "hr":              "hr",
        "hr_document":     "hr",
        "resume":          "hr",
        "invoice":         "invoice",
        "mortgage":        "loan",
        "contract":        "legal",
        "w2":              "tax",
        "1099":            "tax",
    }
    return aliases.get((doc_type or "").lower(), (doc_type or "unknown").lower())


def _normalize_insight(ins: Dict, row) -> Dict:
    """
    Normalize both old LangGraph schema and new extractor schema into one shape.

    Old schema:  { title, category, description, document_id, filename, doc_type, created_at }
    New schema:  { severity, code, title, detail, action, domain, value, tags, due_date, ... }
    """
    return {
        "severity":    ins.get("severity", "info"),
        "code":        ins.get("code", ""),
        "title":       ins.get("title", "Insight"),
        "detail":      ins.get("detail") or ins.get("description", ""),
        "action":      ins.get("action", "Review this document for details."),
        "domain":      ins.get("domain") or _doc_type_to_domain(row.doc_type or ""),
        "value":       ins.get("value"),
        "value_label": ins.get("value_label"),
        "due_date":    ins.get("due_date"),
        "tags":        ins.get("tags") or ([ins["category"]] if ins.get("category") else ["analysis"]),
        "document_id": ins.get("document_id") or str(row.id),
        "filename":    ins.get("filename") or row.filename,
        "doc_type":    ins.get("doc_type") or row.doc_type or "unknown",
        "created_at":  ins.get("created_at") or (row.created_at.isoformat() if row.created_at else None),
    }


# =============================================================================
# HELPERS
# =============================================================================

def _all_insights_from_db(db, domain_filter: Optional[str] = None,
                           severity_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Pull all insights from doc_metadata across all completed documents."""
    sql = text("""
        SELECT id, filename, doc_type, doc_metadata, created_at
        FROM documents
        WHERE status = 'completed'
          AND doc_metadata IS NOT NULL
          AND doc_metadata::text LIKE '%insights%'
    """)
    rows = db.execute(sql).fetchall()

    results = []
    for row in rows:
        meta = row.doc_metadata or {}
        insights = meta.get("insights", [])
        for ins in insights:
            normalized = _normalize_insight(ins, row)

            if domain_filter and normalized["domain"] != domain_filter:
                continue
            if severity_filter and normalized["severity"] != severity_filter:
                continue

            results.append(normalized)

    return results


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/stats")
async def get_insight_stats(db: Session = Depends(get_db)):
    """
    Global stats for the Insights Dashboard header cards.
    Returns counts by severity + risk score + docs analyzed.
    """
    try:
        all_insights = _all_insights_from_db(db)
        summary = _extractor.portfolio_summary(all_insights)

        doc_stats = db.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed,
                COUNT(CASE WHEN doc_metadata::text LIKE '%insights%' THEN 1 END) AS with_insights
            FROM documents
        """)).fetchone()

        return {
            **summary,
            "total_documents": doc_stats.total,
            "completed_documents": doc_stats.completed,
            "documents_with_insights": doc_stats.with_insights,
        }
    except Exception as e:
        logger.error(f"❌ Stats failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio")
async def get_portfolio_insights(
    domain: Optional[str] = Query(None, description="Filter by domain"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Full portfolio view — all insights across all documents.
    Used for the Insights Dashboard page.
    """
    try:
        all_insights = _all_insights_from_db(db, domain_filter=domain, severity_filter=severity)

        # Tag filter
        if tag:
            all_insights = [i for i in all_insights if tag in i.get("tags", [])]

        # Sort: critical first, then warning, info, positive
        all_insights.sort(key=lambda i: _extractor.severity_order(i.get("severity", "info")))

        summary = _extractor.portfolio_summary(all_insights)

        return {
            "summary": summary,
            "insights": all_insights[:limit],
            "total": len(all_insights),
            "filters": {"domain": domain, "severity": severity, "tag": tag},
        }
    except Exception as e:
        logger.error(f"❌ Portfolio insights failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts")
async def get_alerts(
    domain: Optional[str] = Query(None),
    limit: int = Query(20),
    db: Session = Depends(get_db),
):
    """
    Critical and warning alerts only — for notification panel and email digest.
    """
    try:
        all_insights = _all_insights_from_db(db, domain_filter=domain)
        alerts = [i for i in all_insights if i.get("severity") in ("critical", "warning")]
        alerts.sort(key=lambda i: _extractor.severity_order(i.get("severity", "info")))

        return {
            "alerts": alerts[:limit],
            "critical_count": sum(1 for a in alerts if a.get("severity") == "critical"),
            "warning_count": sum(1 for a in alerts if a.get("severity") == "warning"),
            "total": len(alerts),
        }
    except Exception as e:
        logger.error(f"❌ Alerts failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/document/{doc_id}")
async def get_document_insights(
    doc_id: str,
    db: Session = Depends(get_db),
):
    """
    Per-document insights — used inside the doc-sheet panel.
    If insights not yet generated, runs extractor on-demand.
    """
    try:
        doc = DocumentService.get_document_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        meta = doc.doc_metadata or {}
        raw_insights = meta.get("insights")

        # Generate on-demand if not yet done
        if not raw_insights:
            logger.info(f"⚡ Generating insights on-demand for {doc.filename}")
            _extractor.extract_and_save(doc, db)
            db.refresh(doc)
            meta = doc.doc_metadata or {}
            raw_insights = meta.get("insights", [])

        # Normalize — create a fake row-like object for _normalize_insight
        class _FakeRow:
            def __init__(self, d):
                self.id = d.id
                self.filename = d.filename
                self.doc_type = d.doc_type
                self.created_at = d.created_at

        fake_row = _FakeRow(doc)
        insights = [_normalize_insight(ins, fake_row) for ins in (raw_insights or [])]
        insights.sort(key=lambda i: _extractor.severity_order(i.get("severity", "info")))

        critical  = [i for i in insights if i.get("severity") == "critical"]
        warnings  = [i for i in insights if i.get("severity") == "warning"]
        positives = [i for i in insights if i.get("severity") == "positive"]

        return {
            "document_id": doc_id,
            "filename": doc.filename,
            "doc_type": doc.doc_type,
            "domain": insights[0].get("domain") if insights else "unknown",
            "insights": insights,
            "critical_count": len(critical),
            "warning_count": len(warnings),
            "positive_count": len(positives),
            "total": len(insights),
            "generated_at": meta.get("insights_generated_at"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Document insights failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/regenerate/{doc_id}")
async def regenerate_insights(
    doc_id: str,
    db: Session = Depends(get_db),
):
    """Force re-extraction of insights for a document."""
    try:
        doc = DocumentService.get_document_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        count = _extractor.extract_and_save(doc, db)

        return {
            "document_id": doc_id,
            "filename": doc.filename,
            "insights_generated": count,
            "status": "ok",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Regenerate failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tags")
async def get_all_tags(db: Session = Depends(get_db)):
    """All insight tags with counts — for filter chips in dashboard."""
    try:
        all_insights = _all_insights_from_db(db)
        tag_counts: Dict[str, int] = {}
        for ins in all_insights:
            for tag in ins.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        return {
            "tags": [{"tag": t, "count": c} for t, c in sorted_tags]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run-all")
async def run_insights_on_all_docs(db: Session = Depends(get_db)):
    """
    Batch job: run insight extractor on all completed documents
    that don't yet have insights. Useful for initial migration.
    """
    try:
        rows = db.execute(text("""
            SELECT id FROM documents
            WHERE status = 'completed'
              AND (doc_metadata IS NULL
                   OR doc_metadata::text NOT LIKE '%"severity"%')
        """)).fetchall()

        processed = 0
        failed = 0
        for row in rows:
            try:
                doc = DocumentService.get_document_by_id(db, str(row.id))
                if doc:
                    _extractor.extract_and_save(doc, db)
                    processed += 1
            except Exception as e:
                logger.error(f"❌ Failed for doc {row.id}: {e}")
                failed += 1

        return {
            "status": "ok",
            "processed": processed,
            "failed": failed,
            "total": len(rows),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# """
# insights.py
# ==================
# REST API for Actionable Insights across all document domains.
#
# Endpoints:
#   GET  /api/insights/portfolio          ← Cross-doc portfolio summary
#   GET  /api/insights/alerts             ← Critical + warning alerts only
#   GET  /api/insights/document/{doc_id}  ← Per-document insights
#   POST /api/insights/regenerate/{doc_id}← Re-run extractor on a doc
#   GET  /api/insights/tags               ← All tags with counts (for filtering)
#   GET  /api/insights/stats              ← Global stats for dashboard header
# """
#
# import logging
# from typing import Any, Dict, List, Optional
#
# from fastapi import APIRouter, Depends, HTTPException, Query
# from sqlalchemy import text
# from sqlalchemy.orm import Session
#
# from app.core.database import get_db, SessionLocal
# from app.services.document_service import DocumentService
# from app.services.insight_extractor_service import InsightExtractorService
#
# logger = logging.getLogger(__name__)
#
# router = APIRouter(prefix="/api/insights", tags=["insights"])
#
# _extractor = InsightExtractorService()
#
#
# # =============================================================================
# # HELPERS
# # =============================================================================
#
# def _doc_type_to_domain(doc_type: str) -> str:
#     aliases = {
#         "tax_form": "tax", "bank_statement": "bank", "sales_report": "sales",
#         "payroll": "payroll", "government_form": "compliance",
#         "insurance": "insurance", "document": "insurance",
#         "loan": "loan", "legal": "legal", "medical": "medical",
#         "hr": "hr", "invoice": "invoice",
#     }
#     return aliases.get((doc_type or "").lower(), (doc_type or "unknown").lower())
#
#
# def _normalize_insight(ins: Dict, row) -> Dict:
#     """Normalize both old LangGraph schema and new extractor schema into one shape."""
#     return {
#         "severity":    ins.get("severity", "info"),
#         "code":        ins.get("code", ""),
#         "title":       ins.get("title", "Insight"),
#         "detail":      ins.get("detail") or ins.get("description", ""),
#         "action":      ins.get("action", "Review this document for details."),
#         "domain":      ins.get("domain") or _doc_type_to_domain(row.doc_type or ""),
#         "value":       ins.get("value"),
#         "value_label": ins.get("value_label"),
#         "due_date":    ins.get("due_date"),
#         "tags":        ins.get("tags") or ([ins["category"]] if ins.get("category") else ["analysis"]),
#         "document_id": ins.get("document_id") or str(row.id),
#         "filename":    ins.get("filename") or row.filename,
#         "doc_type":    ins.get("doc_type") or row.doc_type or "unknown",
#         "created_at":  ins.get("created_at") or (row.created_at.isoformat() if row.created_at else None),
#     }
#
#
# def _all_insights_from_db(db, domain_filter: Optional[str] = None,
#                            severity_filter: Optional[str] = None) -> List[Dict[str, Any]]:
#     """Pull all insights from doc_metadata across all completed documents."""
#     sql = text("""
#         SELECT id, filename, doc_type, doc_metadata, created_at
#         FROM documents
#         WHERE status = 'completed'
#           AND doc_metadata IS NOT NULL
#           AND doc_metadata::text LIKE '%insights%'
#     """)
#     rows = db.execute(sql).fetchall()
#
#     results = []
#     for row in rows:
#         meta = row.doc_metadata or {}
#         insights = meta.get("insights", [])
#         for ins in insights:
#             normalized = _normalize_insight(ins, row)       # ← normalize here
#
#             if domain_filter and normalized["domain"] != domain_filter:
#                 continue
#             if severity_filter and normalized["severity"] != severity_filter:
#                 continue
#
#             results.append(normalized)
#
#     return results
#
#
# # =============================================================================
# # ENDPOINTS
# # =============================================================================
#
# @router.get("/stats")
# async def get_insight_stats(db: Session = Depends(get_db)):
#     """
#     Global stats for the Insights Dashboard header cards.
#     Returns counts by severity + risk score + docs analyzed.
#     """
#     try:
#         all_insights = _all_insights_from_db(db)
#         summary = _extractor.portfolio_summary(all_insights)
#
#         doc_stats = db.execute(text("""
#             SELECT
#                 COUNT(*) AS total,
#                 COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed,
#                 COUNT(CASE WHEN doc_metadata::text LIKE '%insights%' THEN 1 END) AS with_insights
#             FROM documents
#         """)).fetchone()
#
#         return {
#             **summary,
#             "total_documents": doc_stats.total,
#             "completed_documents": doc_stats.completed,
#             "documents_with_insights": doc_stats.with_insights,
#         }
#     except Exception as e:
#         logger.error(f"❌ Stats failed: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))
#
#
# @router.get("/portfolio")
# async def get_portfolio_insights(
#     domain: Optional[str] = Query(None, description="Filter by domain"),
#     severity: Optional[str] = Query(None, description="Filter by severity"),
#     tag: Optional[str] = Query(None, description="Filter by tag"),
#     limit: int = Query(50, ge=1, le=200),
#     db: Session = Depends(get_db),
# ):
#     """
#     Full portfolio view — all insights across all documents.
#     Used for the Insights Dashboard page.
#     """
#     try:
#         all_insights = _all_insights_from_db(db, domain_filter=domain, severity_filter=severity)
#
#         # Tag filter
#         if tag:
#             all_insights = [i for i in all_insights if tag in i.get("tags", [])]
#
#         # Sort: critical first, then warning, info, positive
#         all_insights.sort(key=lambda i: _extractor.severity_order(i.get("severity", "info")))
#
#         summary = _extractor.portfolio_summary(all_insights)
#
#         return {
#             "summary": summary,
#             "insights": all_insights[:limit],
#             "total": len(all_insights),
#             "filters": {"domain": domain, "severity": severity, "tag": tag},
#         }
#     except Exception as e:
#         logger.error(f"❌ Portfolio insights failed: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))
#
#
# @router.get("/alerts")
# async def get_alerts(
#     domain: Optional[str] = Query(None),
#     limit: int = Query(20),
#     db: Session = Depends(get_db),
# ):
#     """
#     Critical and warning alerts only — for notification panel and email digest.
#     """
#     try:
#         all_insights = _all_insights_from_db(db, domain_filter=domain)
#         alerts = [i for i in all_insights if i.get("severity") in ("critical", "warning")]
#         alerts.sort(key=lambda i: _extractor.severity_order(i.get("severity", "info")))
#
#         return {
#             "alerts": alerts[:limit],
#             "critical_count": sum(1 for a in alerts if a.get("severity") == "critical"),
#             "warning_count": sum(1 for a in alerts if a.get("severity") == "warning"),
#             "total": len(alerts),
#         }
#     except Exception as e:
#         logger.error(f"❌ Alerts failed: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))
#
#
# @router.get("/document/{doc_id}")
# async def get_document_insights(
#     doc_id: str,
#     db: Session = Depends(get_db),
# ):
#     """
#     Per-document insights — used inside the doc-sheet panel.
#     If insights not yet generated, runs extractor on-demand.
#     """
#     try:
#         doc = DocumentService.get_document_by_id(db, doc_id)
#         if not doc:
#             raise HTTPException(status_code=404, detail="Document not found")
#
#         meta = doc.doc_metadata or {}
#         insights = meta.get("insights")
#
#         # Generate on-demand if not yet done
#         if not insights:
#             logger.info(f"⚡ Generating insights on-demand for {doc.filename}")
#             _extractor.extract_and_save(doc, db)
#             db.refresh(doc)
#             meta = doc.doc_metadata or {}
#             insights = meta.get("insights", [])
#
#         # Sort by severity
#         insights.sort(key=lambda i: _extractor.severity_order(i.get("severity", "info")))
#
#         critical = [i for i in insights if i.get("severity") == "critical"]
#         warnings = [i for i in insights if i.get("severity") == "warning"]
#         positives = [i for i in insights if i.get("severity") == "positive"]
#
#         return {
#             "document_id": doc_id,
#             "filename": doc.filename,
#             "doc_type": doc.doc_type,
#             "domain": insights[0].get("domain") if insights else "unknown",
#             "insights": insights,
#             "critical_count": len(critical),
#             "warning_count": len(warnings),
#             "positive_count": len(positives),
#             "total": len(insights),
#             "generated_at": meta.get("insights_generated_at"),
#         }
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"❌ Document insights failed: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))
#
#
# @router.post("/regenerate/{doc_id}")
# async def regenerate_insights(
#     doc_id: str,
#     db: Session = Depends(get_db),
# ):
#     """Force re-extraction of insights for a document."""
#     try:
#         doc = DocumentService.get_document_by_id(db, doc_id)
#         if not doc:
#             raise HTTPException(status_code=404, detail="Document not found")
#
#         count = _extractor.extract_and_save(doc, db)
#
#         return {
#             "document_id": doc_id,
#             "filename": doc.filename,
#             "insights_generated": count,
#             "status": "ok",
#         }
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"❌ Regenerate failed: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))
#
#
# @router.get("/tags")
# async def get_all_tags(db: Session = Depends(get_db)):
#     """All insight tags with counts — for filter chips in dashboard."""
#     try:
#         all_insights = _all_insights_from_db(db)
#         tag_counts: Dict[str, int] = {}
#         for ins in all_insights:
#             for tag in ins.get("tags", []):
#                 tag_counts[tag] = tag_counts.get(tag, 0) + 1
#
#         sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
#         return {
#             "tags": [{"tag": t, "count": c} for t, c in sorted_tags]
#         }
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#
#
# @router.post("/run-all")
# async def run_insights_on_all_docs(db: Session = Depends(get_db)):
#     """
#     Batch job: run insight extractor on all completed documents
#     that don't yet have insights. Useful for initial migration.
#     """
#     try:
#         rows = db.execute(text("""
#             SELECT id FROM documents
#             WHERE status = 'completed'
#               AND (doc_metadata IS NULL
#                    OR doc_metadata::text NOT LIKE '%insights%')
#         """)).fetchall()
#
#         processed = 0
#         failed = 0
#         for row in rows:
#             try:
#                 doc = DocumentService.get_document_by_id(db, str(row.id))
#                 if doc:
#                     _extractor.extract_and_save(doc, db)
#                     processed += 1
#             except Exception as e:
#                 logger.error(f"❌ Failed for doc {row.id}: {e}")
#                 failed += 1
#
#         return {
#             "status": "ok",
#             "processed": processed,
#             "failed": failed,
#             "total": len(rows),
#         }
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))