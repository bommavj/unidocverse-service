"""
medical_agent.py — Healthcare & Medical Admin daily report (medical_mode)
Runs daily at 07:10.
"""
import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

MEDICAL_TYPES = ("medical_record", "lab_report", "prescription", "insurance_card",
                 "eob", "prior_authorization", "medical_claim", "hipaa_form",
                 "vaccination_record", "discharge_summary")


class MedicalAgent(BaseDomainAgent):
    agent_type = "medical"
    task_name  = "Healthcare Admin Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # --- Document activity ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 day')  AS last_24h,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS last_7,
                       COUNT(*) FILTER (WHERE status = 'failed')                        AS failed,
                       COUNT(*) FILTER (WHERE status = 'password_required')             AS locked,
                       COUNT(*) AS total
                FROM documents WHERE doc_type = ANY(:types)
            """), {"types": list(MEDICAL_TYPES)}).fetchone()
            data["activity"] = {
                "last_24h": row[0], "last_7_days": row[1],
                "failed": row[2], "locked": row[3], "total": row[4]
            }
        except Exception as e:
            logger.warning(f"⚠️ [MedicalAgent] activity: {e}")
            data["activity"] = {}

        # --- Breakdown by type ---
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM documents WHERE doc_type = ANY(:types) AND status = 'processed'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {"types": list(MEDICAL_TYPES)}).fetchall()
            data["doc_breakdown"] = [{"type": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [MedicalAgent] doc_breakdown: {e}")
            data["doc_breakdown"] = []

        # --- Docs needing review ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FROM documents
                WHERE doc_type = ANY(:types)
                  AND (doc_metadata->>'needs_manual_review')::boolean = TRUE
            """), {"types": list(MEDICAL_TYPES)}).scalar()
            data["needs_review"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [MedicalAgent] needs_review: {e}")
            data["needs_review"] = 0

        # --- Clients with medical docs ---
        try:
            row = db.execute(text("""
                SELECT COUNT(DISTINCT client_id) FROM documents
                WHERE doc_type = ANY(:types) AND client_id IS NOT NULL
            """), {"types": list(MEDICAL_TYPES)}).scalar()
            data["unique_clients"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [MedicalAgent] unique_clients: {e}")
            data["unique_clients"] = 0

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        activity = data.get("activity", {})
        summary = (
            f"{activity.get('last_24h', 0)} medical docs processed in last 24h | "
            f"{data.get('unique_clients', 0)} unique patients | "
            f"{data.get('needs_review', 0)} need review"
        )
        return {
            "title":   f"Healthcare Admin Report — {date.today().isoformat()}",
            "summary": summary,
            "sections": {
                "activity":           activity,
                "document_breakdown": data.get("doc_breakdown", []),
                "review_queue":       {"needs_review": data.get("needs_review", 0)},
                "patient_coverage":   {"unique_clients": data.get("unique_clients", 0)},
            },
        }
