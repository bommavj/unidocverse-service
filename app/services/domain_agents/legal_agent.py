"""
legal_agent.py — Legal & Compliance daily intelligence report.

Morning briefing for legal teams:
  1. Matters snapshot         — open matters by stage and priority
  2. Deadline alerts          — matters with deadlines in next 14 days
  3. Stalled matters          — open matters with no update in >30 days
  4. Contracts expiring       — legal contracts expiring in 90 days
  5. High-risk contracts      — contracts with risk_rating = high/critical
  6. CLM pipeline             — contract lifecycle by stage
  7. Pending approvals        — approvals awaiting decision
"""

import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)


class LegalAgent(BaseDomainAgent):
    agent_type = "legal"
    task_name  = "Legal & Compliance Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Matters snapshot ───────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    COUNT(*)                                               AS total_open,
                    COUNT(*) FILTER (WHERE priority = 'high')             AS high_priority,
                    COUNT(*) FILTER (WHERE priority = 'urgent')           AS urgent,
                    COUNT(*) FILTER (WHERE deadline < CURRENT_DATE
                                      AND status NOT IN ('closed','resolved')) AS overdue
                FROM (SELECT * FROM matters WHERE tenant_id::text = :tenant_id) matters
                WHERE status NOT IN ('closed','resolved')
            """), self.tp).fetchone()
            data["matters_snapshot"] = {
                "total_open":    int(rows[0] or 0),
                "high_priority": int(rows[1] or 0),
                "urgent":        int(rows[2] or 0),
                "overdue":       int(rows[3] or 0),
            }
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] matters snapshot failed: {e}")
            db.rollback()
            data["matters_snapshot"] = {}

        # ── 2. Deadline alerts (next 14 days) ─────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT title, client_name, matter_type, priority,
                       deadline, deadline - CURRENT_DATE AS days_left, stage
                FROM (SELECT * FROM matters WHERE tenant_id::text = :tenant_id) matters
                WHERE status NOT IN ('closed','resolved')
                  AND deadline IS NOT NULL
                  AND deadline <= CURRENT_DATE + 14
                ORDER BY deadline
            """), self.tp).fetchall()
            data["deadline_alerts"] = [{
                "matter":      r[0],
                "client":      r[1] or "—",
                "type":        r[2] or "—",
                "priority":    r[3] or "—",
                "deadline":    r[4].isoformat() if r[4] else "—",
                "days_left":   int(r[5] or 0),
                "stage":       r[6] or "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] deadline alerts failed: {e}")
            db.rollback()
            data["deadline_alerts"] = []

        # ── 3. Stalled matters (no update >30 days) ───────────────────────────
        try:
            rows = db.execute(text("""
                SELECT title, client_name, matter_type, stage,
                       CURRENT_DATE - updated_at::date AS days_stale,
                       assigned_counsel
                FROM (SELECT * FROM matters WHERE tenant_id::text = :tenant_id) matters
                WHERE status NOT IN ('closed','resolved')
                  AND updated_at < NOW() - INTERVAL '30 days'
                ORDER BY days_stale DESC
            """), self.tp).fetchall()
            data["stalled_matters"] = [{
                "matter":    r[0],
                "client":    r[1] or "—",
                "type":      r[2] or "—",
                "stage":     r[3] or "—",
                "days_stale": int(r[4] or 0),
                "counsel":   r[5] or "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] stalled matters failed: {e}")
            db.rollback()
            data["stalled_matters"] = []

        # ── 4. Contracts expiring ≤90 days ────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT contract_name, counterparty, risk_rating, status,
                       expiry_date, expiry_date - CURRENT_DATE AS days_left,
                       notice_period
                FROM (SELECT * FROM legal_contracts WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) legal_contracts
                WHERE expiry_date IS NOT NULL
                  AND expiry_date >= CURRENT_DATE
                  AND expiry_date <= CURRENT_DATE + 90
                ORDER BY expiry_date
            """), self.tp).fetchall()
            data["contracts_expiring"] = [{
                "contract":     r[0],
                "counterparty": r[1] or "—",
                "risk":         r[2] or "—",
                "status":       r[3] or "—",
                "expires_on":   r[4].isoformat() if r[4] else "—",
                "days_left":    int(r[5] or 0),
                "notice_days":  r[6],
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] contracts expiring failed: {e}")
            db.rollback()
            data["contracts_expiring"] = []

        # ── 5. High-risk contracts ────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT contract_name, counterparty, risk_rating, status,
                       expiry_date
                FROM (SELECT * FROM legal_contracts WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) legal_contracts
                WHERE risk_rating IN ('high','critical')
                  AND status NOT IN ('expired','terminated')
                ORDER BY risk_rating DESC, expiry_date
            """), self.tp).fetchall()
            data["high_risk_contracts"] = [{
                "contract":     r[0],
                "counterparty": r[1] or "—",
                "risk":         r[2],
                "status":       r[3] or "—",
                "expires_on":   r[4].isoformat() if r[4] else "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] high risk contracts failed: {e}")
            db.rollback()
            data["high_risk_contracts"] = []

        # ── 6. CLM pipeline by stage ──────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT stage, COUNT(*) AS cnt,
                       ROUND(AVG(contract_value::numeric)) AS avg_value
                FROM (SELECT * FROM clm_entries WHERE doc_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) clm_entries
                GROUP BY stage ORDER BY cnt DESC
            """), self.tp).fetchall()
            data["clm_pipeline"] = [{
                "stage":     r[0],
                "count":     int(r[1] or 0),
                "avg_value": float(r[2] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [LegalAgent] CLM pipeline failed: {e}")
            db.rollback()
            data["clm_pipeline"] = []

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today    = date.today().isoformat()
        snap     = data.get("matters_snapshot", {})
        deadlines = data.get("deadline_alerts", [])
        contracts = data.get("contracts_expiring", [])

        summary = (
            f"{snap.get('total_open', 0)} open matters "
            f"({snap.get('urgent', 0)} urgent, {snap.get('overdue', 0)} overdue) | "
            f"{len(deadlines)} deadlines in 14 days | "
            f"{len(contracts)} contracts expiring in 90 days"
        )
        return {
            "title":   f"Legal & Compliance Report — {today}",
            "summary": summary,
            "sections": {
                "matters_snapshot":          data.get("matters_snapshot", {}),
                "deadline_alerts_14_days":   deadlines,
                "stalled_matters_30_days":   data.get("stalled_matters", []),
                "contracts_expiring_90_days": contracts,
                "high_risk_contracts":       data.get("high_risk_contracts", []),
                "clm_pipeline_by_stage":     data.get("clm_pipeline", []),
            },
        }
