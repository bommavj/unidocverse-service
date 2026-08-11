"""
insurance_agent.py
──────────────────
Scheduled domain agent for Insurance Agency Management (agency_mode).

Runs daily at 07:00. Collects:
  - Policy renewal pipeline (expiring in 30/60/90 days)
  - Open claims summary
  - COI expirations
  - Quote conversion rates
  - Top clients by premium volume

Produces a structured daily report stored in agent_reports.
"""

import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)


class InsuranceAgent(BaseDomainAgent):
    agent_type = "insurance"
    task_name  = "Insurance Agency Daily Report"

    def collect(self) -> dict[str, Any]:
        db = self.db
        data: dict[str, Any] = {}

        # --- Expiring policies (30 / 60 / 90 days) ---
        try:
            rows = db.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE expiry_date <= CURRENT_DATE + 30)  AS exp_30,
                    COUNT(*) FILTER (WHERE expiry_date <= CURRENT_DATE + 60)  AS exp_60,
                    COUNT(*) FILTER (WHERE expiry_date <= CURRENT_DATE + 90)  AS exp_90,
                    COUNT(*)                                                    AS total
                FROM policies
                WHERE status NOT IN ('cancelled', 'expired')
            """)).fetchone()
            data["renewals"] = {"exp_30": rows[0], "exp_60": rows[1],
                                 "exp_90": rows[2], "total": rows[3]}
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] renewals query failed: {e}")
            data["renewals"] = {}

        # --- Open claims ---
        try:
            rows = db.execute(text("""
                SELECT status, COUNT(*) AS cnt,
                       ROUND(AVG(EXTRACT(DAY FROM NOW() - created_at))) AS avg_age_days
                FROM claims
                WHERE status NOT IN ('closed', 'denied')
                GROUP BY status
                ORDER BY cnt DESC
            """)).fetchall()
            data["claims"] = [{"status": r[0], "count": r[1], "avg_age_days": r[2]}
                               for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] claims query failed: {e}")
            data["claims"] = []

        # --- COI expirations ---
        try:
            rows = db.execute(text("""
                SELECT COUNT(*) FILTER (WHERE expiry_date <= CURRENT_DATE + 30) AS exp_30,
                       COUNT(*) FILTER (WHERE expiry_date <= CURRENT_DATE + 60) AS exp_60
                FROM documents
                WHERE doc_type = 'coi'
                  AND status = 'processed'
            """)).fetchone()
            data["coi"] = {"exp_30": rows[0], "exp_60": rows[1]}
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] COI query failed: {e}")
            data["coi"] = {}

        # --- Quote pipeline ---
        try:
            rows = db.execute(text("""
                SELECT status, COUNT(*) AS cnt
                FROM quotes
                GROUP BY status
                ORDER BY cnt DESC
            """)).fetchall()
            data["quotes"] = [{"status": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] quotes query failed: {e}")
            data["quotes"] = []

        # --- Top 5 clients by document count (proxy for activity) ---
        try:
            rows = db.execute(text("""
                SELECT c.id, COUNT(d.id) AS doc_count
                FROM clients c
                JOIN documents d ON d.client_id = c.id
                WHERE d.status = 'processed'
                GROUP BY c.id
                ORDER BY doc_count DESC
                LIMIT 5
            """)).fetchall()
            data["top_clients"] = [{"client_id": str(r[0]), "doc_count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [InsuranceAgent] top clients query failed: {e}")
            data["top_clients"] = []

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today = date.today().isoformat()
        renewals = data.get("renewals", {})
        claims   = data.get("claims",   [])
        coi      = data.get("coi",      {})
        quotes   = data.get("quotes",   [])

        open_claims = sum(r["count"] for r in claims)
        bound = next((q["count"] for q in quotes if q["status"] == "bound"), 0)
        total_quotes = sum(q["count"] for q in quotes)
        conversion = round(bound / total_quotes * 100, 1) if total_quotes else 0

        summary = (
            f"{renewals.get('exp_30', 0)} policies expiring in 30 days | "
            f"{open_claims} open claims | "
            f"Quote conversion {conversion}%"
        )

        return {
            "title": f"Insurance Agency Report — {today}",
            "summary": summary,
            "report_date": today,
            "sections": {
                "renewal_pipeline": {
                    "expiring_30_days": renewals.get("exp_30", 0),
                    "expiring_60_days": renewals.get("exp_60", 0),
                    "expiring_90_days": renewals.get("exp_90", 0),
                    "total_active":     renewals.get("total",  0),
                },
                "claims_summary": claims,
                "coi_expirations": {
                    "expiring_30_days": coi.get("exp_30", 0),
                    "expiring_60_days": coi.get("exp_60", 0),
                },
                "quote_pipeline": {
                    "breakdown":   quotes,
                    "conversion_pct": conversion,
                },
                "top_clients": data.get("top_clients", []),
            },
        }
