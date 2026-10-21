"""
compliance_agent.py — Compliance & Risk daily intelligence report.

Morning briefing for compliance teams:
  1. Risk snapshot          — doc risk scores distribution
  2. High-risk documents    — docs flagged high/critical risk
  3. Audit log activity     — recent audit events by action type
  4. Pending approvals      — compliance approvals awaiting decision
  5. Docs processed last 24h
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


class ComplianceAgent(BaseDomainAgent):
    agent_type = "compliance"
    task_name  = "Compliance & Risk Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Risk snapshot ──────────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT risk_level, COUNT(*) AS cnt
                FROM doc_risk
                GROUP BY risk_level ORDER BY cnt DESC
            """)).fetchall()
            total = sum(r[1] for r in rows)
            data["risk_snapshot"] = {
                "total_assessed": total,
                "by_level": {r[0]: r[1] for r in rows},
            }
        except Exception as e:
            logger.warning(f"⚠️ [ComplianceAgent] risk snapshot failed: {e}")
            db.rollback()
            data["risk_snapshot"] = {}

        # ── 2. High-risk documents ────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT d.filename, dr.risk_level, dr.risk_score,
                       d.doc_type, d.created_at::date AS uploaded
                FROM doc_risk dr
                JOIN documents d ON d.id = dr.document_id
                WHERE dr.risk_level IN ('high','critical')
                ORDER BY dr.risk_score DESC
            """)).fetchall()
            data["high_risk_documents"] = [{
                "filename":   r[0],
                "risk_level": r[1],
                "risk_score": float(r[2] or 0),
                "doc_type":   r[3] or "—",
                "uploaded":   r[4].isoformat() if r[4] else "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [ComplianceAgent] high risk docs failed: {e}")
            db.rollback()
            data["high_risk_documents"] = []

        # ── 3. Audit log — recent activity ────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT action, COUNT(*) AS cnt
                FROM audit_log
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY action ORDER BY cnt DESC
            """)).fetchall()
            data["audit_activity_24h"] = {r[0]: r[1] for r in rows}
            data["audit_activity_24h"]["total_events"] = sum(r[1] for r in rows)
        except Exception as e:
            logger.warning(f"⚠️ [ComplianceAgent] audit log failed: {e}")
            db.rollback()
            data["audit_activity_24h"] = {"total_events": 0}

        # ── 4. Pending approvals ──────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT title, priority, due_date,
                       CURRENT_DATE - created_at::date AS age_days
                FROM approval_requests
                WHERE status = 'pending' AND tenant_id = :tid
                ORDER BY priority DESC, created_at
            """), {"tid": _approvals_tenant()}).fetchall()
            data["pending_approvals"] = [{
                "title":    r[0],
                "priority": r[1] or "normal",
                "due_date": r[2].isoformat() if r[2] else "—",
                "age_days": int(r[3] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [ComplianceAgent] approvals failed: {e}")
            db.rollback()
            data["pending_approvals"] = []

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today   = date.today().isoformat()
        snap    = data.get("risk_snapshot", {})
        high    = data.get("high_risk_documents", [])
        audit   = data.get("audit_activity_24h", {})
        approvals = data.get("pending_approvals", [])
        by_level = snap.get("by_level", {})

        summary = (
            f"{snap.get('total_assessed', 0)} docs risk-assessed | "
            f"{by_level.get('critical', 0)} critical, {by_level.get('high', 0)} high risk | "
            f"{audit.get('total_events', 0)} audit events in 24h | "
            f"{len(approvals)} approvals pending"
        )
        return {
            "title":   f"Compliance & Risk Report — {today}",
            "summary": summary,
            "sections": {
                "risk_snapshot":        snap,
                "high_risk_documents":  high,
                "audit_activity_24h":   audit,
                "pending_approvals":    approvals,
            },
        }
