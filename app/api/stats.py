"""
Statistics & Dashboard API
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, cast, String
from datetime import datetime, timedelta
from typing import Dict, Any, List
import logging

from app.core.database import get_db
from app.models.document import Document

router = APIRouter(prefix="/api/stats", tags=["statistics"])
logger = logging.getLogger(__name__)


@router.get("/summary")
async def get_summary_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get summary statistics for dashboard cards

    Returns:
        - Total documents
        - Documents by status
        - Documents by type
        - Processing stats
        - Recent activity
    """
    try:
        # Total documents
        total_docs = db.query(func.count(Document.id)).scalar()

        # By status
        status_counts = db.query(
            Document.status,
            func.count(Document.id).label('count')
        ).group_by(Document.status).all()

        status_breakdown = {status: count for status, count in status_counts}

        # By document type
        type_counts = db.query(
            Document.doc_type,
            func.count(Document.id).label('count')
        ).filter(Document.doc_type.isnot(None)) \
            .group_by(Document.doc_type).all()

        type_breakdown = {doc_type: count for doc_type, count in type_counts}

        # Processing success rate
        completed = status_breakdown.get('completed', 0)
        failed = status_breakdown.get('failed', 0)
        needs_review = status_breakdown.get('needs_review', 0)

        total_processed = completed + failed + needs_review
        success_rate = (completed / total_processed * 100) if total_processed > 0 else 0

        # Recent activity (last 7 days)
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_docs = db.query(func.count(Document.id)) \
            .filter(Document.created_at >= seven_days_ago) \
            .scalar()

        # Average processing time
        avg_processing = db.query(
            func.avg(
                func.extract('epoch', Document.processed_at - Document.created_at)
            )
        ).filter(
            and_(
                Document.processed_at.isnot(None),
                Document.status == 'completed'
            )
        ).scalar()

        avg_processing_seconds = float(avg_processing) if avg_processing else 0

        # Storage stats
        total_size = db.query(func.sum(Document.file_size)).scalar() or 0
        avg_size = db.query(func.avg(Document.file_size)).scalar() or 0

        return {
            "total_documents": total_docs,
            "status_breakdown": status_breakdown,
            "type_breakdown": type_breakdown,
            "processing": {
                "success_rate": round(success_rate, 2),
                "avg_processing_time_seconds": round(avg_processing_seconds, 2),
                "completed": completed,
                "failed": failed,
                "needs_review": needs_review
            },
            "activity": {
                "last_7_days": recent_docs,
                "today": db.query(func.count(Document.id))
                .filter(func.date(Document.created_at) == datetime.utcnow().date())
                .scalar()
            },
            "storage": {
                "total_bytes": total_size,
                "total_mb": round(total_size / (1024 * 1024), 2),
                "avg_file_size_bytes": round(avg_size, 2)
            }
        }

    except Exception as e:
        logger.error(f"❌ Failed to get summary stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/activity-timeline")
async def get_activity_timeline(days: int = 30, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """Get daily document activity for the last N days"""
    try:
        from sqlalchemy import func, cast, Date

        start_date = datetime.utcnow() - timedelta(days=days)

        # Group by date
        daily_stats = db.query(
            cast(Document.created_at, Date).label('date'),
            Document.status,
            func.count(Document.id).label('count')
        ).filter(Document.created_at >= start_date) \
            .group_by(cast(Document.created_at, Date), Document.status) \
            .order_by(cast(Document.created_at, Date).desc()) \
            .all()

        # Format results
        timeline = {}
        for date, status, count in daily_stats:
            date_str = date.strftime('%Y-%m-%d')
            if date_str not in timeline:
                timeline[date_str] = {
                    'date': date_str,
                    'total': 0,
                    'completed': 0,
                    'failed': 0,
                    'processing': 0,
                    'pending': 0
                }

            timeline[date_str][status] = count
            timeline[date_str]['total'] += count

        return list(timeline.values())

    except Exception as e:
        logger.error(f"❌ Failed to get activity timeline: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/top-types")
async def get_top_document_types(limit: int = 10, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """Get most common document types"""
    try:
        top_types = db.query(
            Document.doc_type,
            func.count(Document.id).label('count')
        ).filter(Document.doc_type.isnot(None)) \
            .group_by(Document.doc_type) \
            .order_by(func.count(Document.id).desc()) \
            .limit(limit) \
            .all()

        return [
            {
                'type': doc_type,
                'count': count,
                'percentage': 0  # Calculate in frontend
            }
            for doc_type, count in top_types
        ]

    except Exception as e:
        logger.error(f"❌ Failed to get top types: {e}")
        raise HTTPException(status_code=500, detail=str(e))