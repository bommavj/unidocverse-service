"""
legal_agent.py — Legal & Compliance daily report (legal_mode)
Runs daily at 07:05. Collects contracts, NDAs, leases, obligations, matters.
"""
import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

LEGAL_TYPES = ("contract", "nda", "lease_agreement", "legal_notice",
               "regulatory_filing", "terms_of_service", "legal_agreement")


class LegalAgent(BaseDomainAgent):
    agent_type = "legal"
    task_name  = "Legal & Compliance Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # --- Document counts by type ---
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt,
                       COUNT(*) FILTER (WHERE status = 'processed') AS processed,
                       COUNT(*) FILTER (WHERE status = 'failed')    AS failed
                FROM documents
                WHERE doc_type = ANY(:types)
                GROUP BY doc_type ORDER BY cnt DESC
            """), {"types": list(LEGAL_TYPES)}).fetchall()
            data["doc_breakdown"] = [
                {"type": r[0], "total": r[1], "processed": r[2], "failed": r[3]}
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] doc_breakdown: {e}")
            data["doc_breakdown"] = []

        # --- Recently processed legal docs (last 7 days) ---
        try:
            rows = db.execute(text("""
                SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS last_7,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 day')  AS last_24h,
                       COUNT(*) AS total
                FROM documents
                WHERE doc_type = ANY(:types) AND status = 'processed'
            """), {"types": list(LEGAL_TYPES)}).fetchone()
            data["activity"] = {"last_24h": rows[1], "last_7_days": rows[0], "total": rows[2]}
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] activity: {e}")
            data["activity"] = {}

        # --- Matters summary ---
        try:
            rows = db.execute(text("""
                SELECT status, COUNT(*) AS cnt
                FROM matters GROUP BY status ORDER BY cnt DESC
            """)).fetchall()
            data["matters"] = [{"status": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] matters: {e}")
            data["matters"] = []

        # --- Docs needing review ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FROM documents
                WHERE doc_type = ANY(:types)
                  AND (doc_metadata->>'needs_manual_review')::boolean = TRUE
            """), {"types": list(LEGAL_TYPES)}).scalar()
            data["needs_review"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] needs_review: {e}")
            data["needs_review"] = 0

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        activity = data.get("activity", {})
        matters  = data.get("matters",  [])
        open_matters = sum(r["count"] for r in matters if r["status"] not in ("closed", "resolved"))

        summary = (
            f"{activity.get('last_24h', 0)} legal docs processed in last 24h | "
            f"{open_matters} open matters | "
            f"{data.get('needs_review', 0)} documents need manual review"
        )
        return {
            "title":   f"Legal & Compliance Report — {date.today().isoformat()}",
            "summary": summary,
            "sections": {
                "activity":        activity,
                "document_breakdown": data.get("doc_breakdown", []),
                "matters_summary": matters,
                "needs_review":    {"count": data.get("needs_review", 0)},
            },
        }
