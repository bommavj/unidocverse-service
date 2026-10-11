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
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)


class ImmigrationAgent(BaseDomainAgent):
    agent_type = "immigration"
    task_name  = "Immigration Daily Report"

    def collect(self) -> dict[str, Any]:
        """The run's tenant only — the same records the Visa Tracker shows (client-linked + uploaded documents)."""
        from collections import Counter
        from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
        from app.services.immigration_tracker import tenant_records
        recs = tenant_records(self.db, str(get_current_tenant_id() or DEFAULT_TENANT_ID))

        def row(r, **extra):
            return {"person": r.get("beneficiary_name") or r.get("source_file") or "—", "doc_type": r.get("doc_type"),
                    "label": r.get("doc_label") or "—", "visa_class": r.get("visa_class") or "—",
                    "employer": r.get("employer_sponsor") or "—", **extra}

        def expiry(r):
            dates = [d for d in (r.get("valid_until"), r.get("authorized_stay_until")) if d]
            return min(dates) if dates else "—"

        days = lambda r: r.get("days_to_expiry")
        expired = [r for r in recs if days(r) is not None and days(r) < 0]
        soon = [r for r in recs if days(r) is not None and 0 <= days(r) <= 90]
        classes = Counter(r["visa_class"] for r in recs if r.get("visa_class"))
        current_classes = Counter(r["visa_class"] for r in recs if r.get("visa_class") and (days(r) or -1) >= 0)
        return {
            "portfolio_snapshot": {
                "total_cases": len(recs),
                "active": sum(1 for r in recs if days(r) is None or days(r) >= 0),
                "already_expired": len(expired),
                "critical_30d": sum(1 for r in soon if days(r) <= 30),
                "expiring_90d": len(soon),
                "expiry_unreadable": sum(1 for r in recs if days(r) is None),
            },
            "already_expired": [row(r, expired_on=expiry(r), days_expired=-days(r))
                                for r in sorted(expired, key=lambda r: days(r), reverse=True)],
            "expiring_90d": [row(r, expires_on=expiry(r), days_left=days(r)) for r in soon],
            "by_visa_class": [{"visa_class": c, "total": n, "active": current_classes.get(c, 0)}
                              for c, n in classes.most_common()],
        }

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
