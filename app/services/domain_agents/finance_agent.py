"""
finance_agent.py — Finance, Tax & Audit daily report (finance_mode)
Runs daily at 07:15.
"""
import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

FINANCE_TYPES = ("invoice", "purchase_order", "bank_statement", "tax_return",
                 "w2", "1099", "expense_report", "financial_statement",
                 "audit_report", "credit_memo", "wire_transfer", "receipt")


class FinanceAgent(BaseDomainAgent):
    agent_type = "finance"
    task_name  = "Finance & Audit Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # --- Volume ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 day')  AS last_24h,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS last_7,
                       COUNT(*) FILTER (WHERE status = 'failed')                        AS failed,
                       COUNT(*) AS total
                FROM documents WHERE doc_type = ANY(:types)
            """), {"types": list(FINANCE_TYPES)}).fetchone()
            data["activity"] = {
                "last_24h": row[0], "last_7_days": row[1],
                "failed": row[2], "total": row[3]
            }
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] activity: {e}")
            data["activity"] = {}

        # --- Breakdown by type ---
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM documents WHERE doc_type = ANY(:types) AND status = 'processed'
                GROUP BY doc_type ORDER BY cnt DESC LIMIT 10
            """), {"types": list(FINANCE_TYPES)}).fetchall()
            data["doc_breakdown"] = [{"type": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] doc_breakdown: {e}")
            data["doc_breakdown"] = []

        # --- Anomalies flagged ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FROM documents
                WHERE doc_type = ANY(:types)
                  AND doc_metadata->'anomalies' IS NOT NULL
                  AND jsonb_array_length(doc_metadata->'anomalies') > 0
                  AND created_at >= NOW() - INTERVAL '7 days'
            """), {"types": list(FINANCE_TYPES)}).scalar()
            data["anomalies_flagged"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] anomalies: {e}")
            data["anomalies_flagged"] = 0

        # --- Needs review ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FROM documents
                WHERE doc_type = ANY(:types)
                  AND (doc_metadata->>'needs_manual_review')::boolean = TRUE
            """), {"types": list(FINANCE_TYPES)}).scalar()
            data["needs_review"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] needs_review: {e}")
            data["needs_review"] = 0

        # --- Unique vendors/clients ---
        try:
            row = db.execute(text("""
                SELECT COUNT(DISTINCT client_id) FROM documents
                WHERE doc_type = ANY(:types) AND client_id IS NOT NULL
            """), {"types": list(FINANCE_TYPES)}).scalar()
            data["unique_clients"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] unique_clients: {e}")
            data["unique_clients"] = 0

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        activity = data.get("activity", {})
        summary = (
            f"{activity.get('last_24h', 0)} finance docs processed in last 24h | "
            f"{data.get('anomalies_flagged', 0)} anomalies flagged this week | "
            f"{data.get('needs_review', 0)} need review"
        )
        return {
            "title":   f"Finance & Audit Report — {date.today().isoformat()}",
            "summary": summary,
            "sections": {
                "activity":           activity,
                "document_breakdown": data.get("doc_breakdown", []),
                "anomalies":          {"flagged_last_7_days": data.get("anomalies_flagged", 0)},
                "review_queue":       {"needs_review": data.get("needs_review", 0)},
                "client_coverage":    {"unique_clients": data.get("unique_clients", 0)},
            },
        }
