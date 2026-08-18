"""
immigration_agent.py — Immigration daily intelligence report.

Morning briefing for immigration/HR teams:
  1. Portfolio snapshot     — total cases by doc type and status
  2. Critical expiries      — documents expiring ≤30 days (urgent action)
  3. Expiring ≤90 days      — full list with person, doc type, days left
  4. Already expired        — documents past expiry date (need immediate attention)
  5. Cases by visa class    — breakdown of visa types in portfolio
"""

import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)


class ImmigrationAgent(BaseDomainAgent):
    agent_type = "immigration"
    task_name  = "Immigration Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Portfolio snapshot ─────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    COUNT(*)                                                              AS total,
                    COUNT(*) FILTER (WHERE status = 'active')                            AS active,
                    COUNT(*) FILTER (WHERE status = 'expired')                           AS expired,
                    COUNT(*) FILTER (WHERE COALESCE(valid_until, authorized_stay_until)
                                          <= CURRENT_DATE + 30
                                      AND COALESCE(valid_until, authorized_stay_until)
                                          >= CURRENT_DATE)                               AS critical_30d,
                    COUNT(*) FILTER (WHERE COALESCE(valid_until, authorized_stay_until)
                                          <= CURRENT_DATE + 90
                                      AND COALESCE(valid_until, authorized_stay_until)
                                          >= CURRENT_DATE)                               AS expiring_90d
                FROM client_immigration
            """)).fetchone()
            data["portfolio_snapshot"] = {
                "total_cases":        int(rows[0] or 0),
                "active":             int(rows[1] or 0),
                "already_expired":    int(rows[2] or 0),
                "critical_30d":       int(rows[3] or 0),
                "expiring_90d":       int(rows[4] or 0),
            }
        except Exception as e:
            logger.warning(f"⚠️ [ImmigrationAgent] portfolio snapshot failed: {e}")
            db.rollback()
            data["portfolio_snapshot"] = {}

        # ── 2. Already expired — immediate action required ────────────────────
        try:
            rows = db.execute(text("""
                SELECT beneficiary_name, doc_type, doc_label,
                       COALESCE(valid_until, authorized_stay_until) AS expiry,
                       CURRENT_DATE - COALESCE(valid_until, authorized_stay_until) AS days_expired,
                       visa_class, employer_sponsor, status
                FROM client_immigration
                WHERE COALESCE(valid_until, authorized_stay_until) < CURRENT_DATE
                ORDER BY expiry DESC
            """)).fetchall()
            data["already_expired"] = [{
                "person":        r[0] or "—",
                "doc_type":      r[1],
                "label":         r[2] or "—",
                "expired_on":    r[3].isoformat() if r[3] else "—",
                "days_expired":  int(r[4] or 0),
                "visa_class":    r[5] or "—",
                "employer":      r[6] or "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [ImmigrationAgent] already expired failed: {e}")
            db.rollback()
            data["already_expired"] = []

        # ── 3. Expiring ≤90 days ──────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT beneficiary_name, doc_type, doc_label,
                       COALESCE(valid_until, authorized_stay_until) AS expiry,
                       COALESCE(valid_until, authorized_stay_until) - CURRENT_DATE AS days_left,
                       visa_class, employer_sponsor
                FROM client_immigration
                WHERE COALESCE(valid_until, authorized_stay_until) >= CURRENT_DATE
                  AND COALESCE(valid_until, authorized_stay_until) <= CURRENT_DATE + 90
                ORDER BY expiry
            """)).fetchall()
            data["expiring_90d"] = [{
                "person":     r[0] or "—",
                "doc_type":   r[1],
                "label":      r[2] or "—",
                "expires_on": r[3].isoformat() if r[3] else "—",
                "days_left":  int(r[4] or 0),
                "visa_class": r[5] or "—",
                "employer":   r[6] or "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [ImmigrationAgent] expiring 90d failed: {e}")
            db.rollback()
            data["expiring_90d"] = []

        # ── 4. Cases by visa class ────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT COALESCE(visa_class, 'Unknown') AS visa_class,
                       COUNT(*) AS cnt,
                       COUNT(*) FILTER (WHERE status = 'active') AS active
                FROM client_immigration
                WHERE visa_class IS NOT NULL
                GROUP BY visa_class ORDER BY cnt DESC
            """)).fetchall()
            data["by_visa_class"] = [{
                "visa_class": r[0],
                "total":      int(r[1] or 0),
                "active":     int(r[2] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [ImmigrationAgent] visa class breakdown failed: {e}")
            db.rollback()
            data["by_visa_class"] = []

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today    = date.today().isoformat()
        snap     = data.get("portfolio_snapshot", {})
        expired  = data.get("already_expired", [])
        critical = [e for e in data.get("expiring_90d", []) if e["days_left"] <= 30]

        summary = (
            f"{snap.get('total_cases', 0)} immigration cases | "
            f"{len(expired)} already expired — immediate action needed | "
            f"{snap.get('critical_30d', 0)} expiring in 30 days | "
            f"{snap.get('expiring_90d', 0)} expiring in 90 days"
        )
        return {
            "title":   f"Immigration Report — {today}",
            "summary": summary,
            "sections": {
                "portfolio_snapshot":          snap,
                "already_expired_urgent":      expired,
                "expiring_next_90_days":       data.get("expiring_90d", []),
                "cases_by_visa_class":         data.get("by_visa_class", []),
            },
        }
