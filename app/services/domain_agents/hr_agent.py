"""
hr_agent.py — HR & People daily report (hr_mode)
Runs daily at 07:25.
"""
import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

HR_TYPES = ("offer_letter", "employment_contract", "resume", "nda",
            "i9", "w4", "performance_review", "termination_letter",
            "payroll", "benefits_enrollment")

IMMIGRATION_TYPES = ("passport", "visa_document", "i94", "h1b", "ead",
                     "i797", "i140", "uscis_receipt", "immigration_document",
                     "immigration_receipt")


class HRAgent(BaseDomainAgent):
    agent_type = "hr"
    task_name  = "HR & People Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # --- HR doc activity ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 day')  AS last_24h,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS last_7,
                       COUNT(*) AS total
                FROM documents WHERE doc_type = ANY(:types)
            """), {"types": list(HR_TYPES)}).fetchone()
            data["activity"] = {"last_24h": row[0], "last_7_days": row[1], "total": row[2]}
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] activity: {e}")
            data["activity"] = {}

        # --- Breakdown ---
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM documents WHERE doc_type = ANY(:types) AND status = 'processed'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {"types": list(HR_TYPES)}).fetchall()
            data["doc_breakdown"] = [{"type": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] doc_breakdown: {e}")
            data["doc_breakdown"] = []

        # --- Immigration expiry alerts ---
        try:
            rows = db.execute(text("""
                SELECT alert_type, COUNT(*) AS cnt
                FROM client_immigration
                WHERE expiry_alert IS NOT NULL
                GROUP BY alert_type ORDER BY cnt DESC
            """)).fetchall()
            data["immigration_alerts"] = [{"alert": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] immigration_alerts: {e}")
            data["immigration_alerts"] = []

        # --- Expiring immigration docs ---
        try:
            row = db.execute(text("""
                SELECT
                  COUNT(*) FILTER (WHERE expiry_date <= CURRENT_DATE + 30) AS exp_30,
                  COUNT(*) FILTER (WHERE expiry_date <= CURRENT_DATE + 60) AS exp_60,
                  COUNT(*) FILTER (WHERE expiry_date <= CURRENT_DATE + 90) AS exp_90
                FROM client_immigration
                WHERE expiry_date IS NOT NULL
            """)).fetchone()
            data["immigration_expiry"] = {"exp_30": row[0], "exp_60": row[1], "exp_90": row[2]}
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] immigration_expiry: {e}")
            data["immigration_expiry"] = {}

        # --- Unique employees ---
        try:
            row = db.execute(text("""
                SELECT COUNT(DISTINCT client_id) FROM documents
                WHERE doc_type = ANY(:types) AND client_id IS NOT NULL
            """), {"types": list(HR_TYPES)}).scalar()
            data["unique_employees"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] unique_employees: {e}")
            data["unique_employees"] = 0

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        activity = data.get("activity", {})
        expiry   = data.get("immigration_expiry", {})
        summary = (
            f"{activity.get('last_24h', 0)} HR docs processed in last 24h | "
            f"{expiry.get('exp_30', 0)} immigration docs expiring in 30 days | "
            f"{data.get('unique_employees', 0)} employees tracked"
        )
        return {
            "title":   f"HR & People Report — {date.today().isoformat()}",
            "summary": summary,
            "sections": {
                "activity":            activity,
                "document_breakdown":  data.get("doc_breakdown", []),
                "immigration_expiry":  expiry,
                "immigration_alerts":  data.get("immigration_alerts", []),
                "employee_coverage":   {"unique_employees": data.get("unique_employees", 0)},
            },
        }
