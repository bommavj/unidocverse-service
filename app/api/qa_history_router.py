"""
qa_history_router.py
====================
CRUD + search endpoints for persistent Q&A history.

GET  /api/documents/{doc_id}/qa-history        — last N Q&A turns for a doc
GET  /api/qa-history                           — global ask history
GET  /api/qa-history/docs                      — docs that have history + last turn preview
GET  /api/qa-history/search?q=...              — keyword + semantic search across all history
DELETE /api/documents/{doc_id}/qa-history      — clear history for a doc
DELETE /api/qa-history                         — clear global history
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, text, func
from typing import Optional

from app.core.database import get_db
from app.models.document import DocumentQAHistory, Document
from app.core.pii_redactor import redact_text, redact_value

router = APIRouter(tags=["qa-history"])


def _serialize(row: DocumentQAHistory) -> dict:
    return {
        "id":          row.id,
        "document_id": row.document_id,
        "question":    redact_text(row.question or ""),
        "answer":      redact_text(row.answer or ""),
        "sources":     redact_value(row.sources or []),
        "doc_type":    row.doc_type,
        "lang":        row.lang,
        "confidence":  float(row.confidence) if row.confidence is not None else None,
        "created_at":  row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/api/documents/{doc_id}/qa-history")
def get_doc_history(
    doc_id: str,
    limit: int = Query(30, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.query(func.count(DocumentQAHistory.id)).filter(DocumentQAHistory.document_id == doc_id).scalar() or 0
    rows = (
        db.query(DocumentQAHistory)
        .filter(DocumentQAHistory.document_id == doc_id)
        .order_by(desc(DocumentQAHistory.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [_serialize(r) for r in reversed(rows)]
    return {
        "history":  items,
        "total":    total,
        "offset":   offset,
        "limit":    limit,
        "has_more": (offset + len(items)) < total,
    }


@router.get("/api/qa-history")
def get_global_history(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    rows = (
        db.query(DocumentQAHistory)
        .order_by(desc(DocumentQAHistory.created_at))
        .limit(limit)
        .all()
    )
    return {"history": [_serialize(r) for r in reversed(rows)]}


@router.get("/api/qa-history/docs")
def get_docs_with_history(db: Session = Depends(get_db)):
    """
    Returns list of documents that have at least one Q&A turn, with:
    - document metadata (id, filename, doc_type)
    - last question asked
    - turn count
    - last activity timestamp
    """
    rows = db.execute(text("""
        SELECT
            h.document_id,
            d.filename,
            d.doc_type,
            COUNT(h.id)                            AS turn_count,
            MAX(h.created_at)                      AS last_at,
            (SELECT question FROM document_qa_history
             WHERE document_id = h.document_id
             ORDER BY created_at DESC LIMIT 1)     AS last_question
        FROM document_qa_history h
        LEFT JOIN documents d ON d.id = h.document_id
        WHERE h.document_id IS NOT NULL
        GROUP BY h.document_id, d.filename, d.doc_type
        ORDER BY last_at DESC
    """)).fetchall()

    return {
        "docs": [
            {
                "document_id":   r.document_id,
                "filename":      r.filename or r.document_id,
                "doc_type":      r.doc_type or "document",
                "turn_count":    r.turn_count,
                "last_at":       r.last_at.isoformat() if r.last_at else None,
                "last_question": r.last_question,
            }
            for r in rows
        ]
    }


@router.get("/api/qa-history/search")
def search_history(
    q: str = Query(..., min_length=1),
    doc_id: Optional[str] = Query(None),
    limit: int = Query(40, le=200),
    db: Session = Depends(get_db),
):
    """
    Keyword search across questions + answers.
    Optionally scoped to a single document.
    Results ranked by relevance (question match > answer match) then recency.
    """
    terms = [t.strip() for t in q.lower().split() if t.strip()]
    if not terms:
        return {"results": []}

    base_q = db.query(DocumentQAHistory)
    if doc_id:
        base_q = base_q.filter(DocumentQAHistory.document_id == doc_id)

    # Build OR filter across all terms across question + answer
    filters = []
    for term in terms:
        like = f"%{term}%"
        filters.append(DocumentQAHistory.question.ilike(like))
        filters.append(DocumentQAHistory.answer.ilike(like))

    rows = (
        base_q
        .filter(or_(*filters))
        .order_by(desc(DocumentQAHistory.created_at))
        .limit(limit)
        .all()
    )

    # Score: count how many terms appear in question (higher weight) + answer
    def _score(row: DocumentQAHistory) -> int:
        q_lower = (row.question or "").lower()
        a_lower = (row.answer or "").lower()
        return sum(2 if t in q_lower else (1 if t in a_lower else 0) for t in terms)

    results = sorted([_serialize(r) for r in rows], key=lambda x: _score(
        next(r for r in rows if r.id == x["id"])
    ), reverse=True)

    # Attach filename for cross-doc search context
    doc_cache: dict = {}
    for item in results:
        did = item.get("document_id")
        if did and did not in doc_cache:
            doc = db.query(Document).filter(Document.id == did).first()
            doc_cache[did] = doc.filename if doc else did
        item["filename"] = doc_cache.get(did, did)

    return {"results": results, "total": len(results)}


@router.delete("/api/documents/{doc_id}/qa-history/{turn_id}")
def delete_turn(doc_id: str, turn_id: str, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    deleted = (
        db.query(DocumentQAHistory)
        .filter(DocumentQAHistory.document_id == doc_id, DocumentQAHistory.id == turn_id)
        .delete()
    )
    db.commit()
    if not deleted:
        raise HTTPException(404, "Turn not found")
    return {"deleted": turn_id}


@router.delete("/api/documents/{doc_id}/qa-history")
def clear_doc_history(doc_id: str, db: Session = Depends(get_db)):
    deleted = db.query(DocumentQAHistory).filter(DocumentQAHistory.document_id == doc_id).delete()
    db.commit()
    return {"deleted": deleted}


@router.delete("/api/qa-history")
def clear_global_history(db: Session = Depends(get_db)):
    deleted = db.query(DocumentQAHistory).delete()
    db.commit()
    return {"deleted": deleted}
