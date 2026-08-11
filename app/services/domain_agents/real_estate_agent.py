"""
real_estate_agent.py — Real Estate daily report (real_estate_mode)
Runs daily at 07:20.
"""
import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

RE_TYPES = ("lease_agreement", "deed", "mortgage", "title_report",
            "appraisal", "inspection_report", "purchase_agreement",
            "hoa_document", "property_tax", "closing_disclosure")


class RealEstateAgent(BaseDomainAgent):
    agent_type = "real_estate"
    task_name  = "Real Estate Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # --- Activity ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 day')  AS last_24h,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS last_7,
                       COUNT(*) FILTER (WHERE status = 'failed')                        AS failed,
                       COUNT(*) AS total
                FROM documents WHERE doc_type = ANY(:types)
            """), {"types": list(RE_TYPES)}).fetchone()
            data["activity"] = {
                "last_24h": row[0], "last_7_days": row[1],
                "failed": row[2], "total": row[3]
            }
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] activity: {e}")
            data["activity"] = {}

        # --- Breakdown by type ---
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM documents WHERE doc_type = ANY(:types) AND status = 'processed'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {"types": list(RE_TYPES)}).fetchall()
            data["doc_breakdown"] = [{"type": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] doc_breakdown: {e}")
            data["doc_breakdown"] = []

        # --- Lease expirations from doc metadata ---
        try:
            rows = db.execute(text("""
                SELECT
                  COUNT(*) FILTER (
                    WHERE (doc_metadata->>'expiry_date')::date <= CURRENT_DATE + 30
                  ) AS exp_30,
                  COUNT(*) FILTER (
                    WHERE (doc_metadata->>'expiry_date')::date <= CURRENT_DATE + 60
                  ) AS exp_60,
                  COUNT(*) FILTER (
                    WHERE (doc_metadata->>'expiry_date')::date <= CURRENT_DATE + 90
                  ) AS exp_90
                FROM documents
                WHERE doc_type = 'lease_agreement'
                  AND status = 'processed'
                  AND doc_metadata->>'expiry_date' IS NOT NULL
            """)).fetchone()
            data["lease_expiry"] = {"exp_30": rows[0], "exp_60": rows[1], "exp_90": rows[2]}
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] lease_expiry: {e}")
            data["lease_expiry"] = {}

        # --- Needs review ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FROM documents
                WHERE doc_type = ANY(:types)
                  AND (doc_metadata->>'needs_manual_review')::boolean = TRUE
            """), {"types": list(RE_TYPES)}).scalar()
            data["needs_review"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] needs_review: {e}")
            data["needs_review"] = 0

        # --- Unique properties/clients ---
        try:
            row = db.execute(text("""
                SELECT COUNT(DISTINCT client_id) FROM documents
                WHERE doc_type = ANY(:types) AND client_id IS NOT NULL
            """), {"types": list(RE_TYPES)}).scalar()
            data["unique_clients"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] unique_clients: {e}")
            data["unique_clients"] = 0

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        activity = data.get("activity", {})
        expiry   = data.get("lease_expiry", {})
        summary = (
            f"{activity.get('last_24h', 0)} property docs processed in last 24h | "
            f"{expiry.get('exp_30', 0)} leases expiring in 30 days | "
            f"{data.get('needs_review', 0)} need review"
        )
        return {
            "title":   f"Real Estate Report — {date.today().isoformat()}",
            "summary": summary,
            "sections": {
                "activity":           activity,
                "document_breakdown": data.get("doc_breakdown", []),
                "lease_expiry":       expiry,
                "review_queue":       {"needs_review": data.get("needs_review", 0)},
                "property_coverage":  {"unique_clients": data.get("unique_clients", 0)},
            },
        }
