"""
operations_agent.py — Operations daily intelligence report.

Morning briefing for operations teams:
  1. Matters snapshot       — open matters by stage/priority
  2. Approval queue         — pending approvals by age and priority
  3. Overdue approvals      — approvals past due_date
  4. Claims snapshot        — loss run claims by status
  5. CLM pipeline           — contract lifecycle stages
"""

import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent


def _approvals_tenant() -> str:
    from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
    return str(get_current_tenant_id() or DEFAULT_TENANT_ID)


logger = logging.getLogger(__name__)


class OperationsAgent(BaseDomainAgent):
    agent_type = "operations"
    task_name  = "Operations Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Matters snapshot ───────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT stage, priority, COUNT(*) AS cnt
                FROM matters
                WHERE status NOT IN ('closed','resolved')
                GROUP BY stage, priority ORDER BY cnt DESC
            """)).fetchall()
            total = sum(r[2] for r in rows)
            urgent = sum(r[2] for r in rows if r[1] in ('high','urgent'))
            data["matters_snapshot"] = {
                "total_open":    total,
                "urgent_high":   urgent,
                "by_stage":      {},
            }
            for r in rows:
                data["matters_snapshot"]["by_stage"].setdefault(r[0], 0)
                data["matters_snapshot"]["by_stage"][r[0]] += r[2]
        except Exception as e:
            logger.warning(f"⚠️ [OperationsAgent] matters snapshot failed: {e}")
            db.rollback()
            data["matters_snapshot"] = {}

        # ── 2. Approval queue ────────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT title, priority, status, due_date,
                       CURRENT_DATE - created_at::date AS age_days
                FROM approval_requests
                WHERE status = 'pending' AND tenant_id = :tid
                ORDER BY priority DESC, created_at
            """), {"tid": _approvals_tenant()}).fetchall()
            data["approval_queue"] = [{
                "title":    r[0],
                "priority": r[1] or "normal",
                "due_date": r[2].isoformat() if r[2] else "—",
                "age_days": int(r[4] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [OperationsAgent] approval queue failed: {e}")
            db.rollback()
            data["approval_queue"] = []

        # ── 3. Overdue approvals ──────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT title, priority,
                       due_date, CURRENT_DATE - due_date AS days_overdue
                FROM approval_requests
                WHERE status = 'pending' AND tenant_id = :tid
                  AND due_date IS NOT NULL
                  AND due_date < CURRENT_DATE
                ORDER BY days_overdue DESC
            """), {"tid": _approvals_tenant()}).fetchall()
            data["overdue_approvals"] = [{
                "title":        r[0],
                "priority":     r[1] or "normal",
                "due_date":     r[2].isoformat() if r[2] else "—",
                "days_overdue": int(r[3] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [OperationsAgent] overdue approvals failed: {e}")
            db.rollback()
            data["overdue_approvals"] = []

        # ── 4. Claims snapshot ────────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT status, COUNT(*) AS cnt,
                       ROUND(SUM(amount_incurred::numeric)) AS total_incurred
                FROM loss_run_claims
                GROUP BY status ORDER BY cnt DESC
            """)).fetchall()
            data["claims_snapshot"] = [{
                "status":          r[0],
                "count":           int(r[1] or 0),
                "total_incurred":  float(r[2] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [OperationsAgent] claims failed: {e}")
            db.rollback()
            data["claims_snapshot"] = []

        # ── 5. CLM pipeline ───────────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT stage, COUNT(*) AS cnt,
                       ROUND(SUM(contract_value::numeric)) AS total_value
                FROM clm_entries
                GROUP BY stage ORDER BY cnt DESC
            """)).fetchall()
            data["clm_pipeline"] = [{
                "stage":       r[0],
                "count":       int(r[1] or 0),
                "total_value": float(r[2] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [OperationsAgent] CLM pipeline failed: {e}")
            db.rollback()
            data["clm_pipeline"] = []

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today    = date.today().isoformat()
        snap     = data.get("matters_snapshot", {})
        approvals = data.get("approval_queue", [])
        overdue  = data.get("overdue_approvals", [])

        summary = (
            f"{snap.get('total_open', 0)} open matters ({snap.get('urgent_high', 0)} urgent/high) | "
            f"{len(approvals)} approvals pending | "
            f"{len(overdue)} overdue approvals"
        )
        return {
            "title":   f"Operations Report — {today}",
            "summary": summary,
            "sections": {
                "matters_snapshot":    snap,
                "overdue_approvals":   overdue,
                "approval_queue":      approvals,
                "claims_snapshot":     data.get("claims_snapshot", []),
                "clm_pipeline":        data.get("clm_pipeline", []),
            },
        }
