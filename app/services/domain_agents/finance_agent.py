"""
finance_agent.py — Finance & Audit daily intelligence report.

Morning briefing for finance teams:
  1. Anomaly snapshot      — unresolved financial anomalies by type
  2. High-value anomalies  — anomalies above threshold, needing review
  3. Approval queue        — finance approvals pending
  4. Three-way match gaps  — invoices not reconciled (finance_matches)
  5. Recent docs processed — finance docs in last 24h
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

FINANCE_DOC_TYPES = ('invoice','bank_statement','expense_report','tax_return',
                     'audit_report','financial_statement','purchase_order','receipt',
                     'payroll','budget')


class FinanceAgent(BaseDomainAgent):
    agent_type = "finance"
    task_name  = "Finance & Audit Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Anomaly snapshot ───────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT anomaly_type, COUNT(*) AS cnt,
                       ROUND(SUM(amount::numeric)) AS total_amount
                FROM finance_anomalies
                GROUP BY anomaly_type ORDER BY total_amount DESC
            """)).fetchall()
            total = sum(r[1] for r in rows)
            data["anomaly_snapshot"] = {
                "total_anomalies": total,
                "by_type": {r[0]: {"count": r[1], "total_amount": float(r[2] or 0)} for r in rows},
            }
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] anomaly snapshot failed: {e}")
            db.rollback()
            data["anomaly_snapshot"] = {}

        # ── 2. Top anomalies needing review ───────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT title, anomaly_type, ref_code, amount,
                       created_at::date AS detected_on,
                       CURRENT_DATE - created_at::date AS age_days
                FROM finance_anomalies
                ORDER BY amount::numeric DESC NULLS LAST
            """)).fetchall()
            data["top_anomalies"] = [{
                "title":       r[0],
                "type":        r[1],
                "ref":         r[2] or "—",
                "amount":      float(r[3] or 0),
                "detected_on": r[4].isoformat() if r[4] else "—",
                "age_days":    int(r[5] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] top anomalies failed: {e}")
            db.rollback()
            data["top_anomalies"] = []

        # ── 3. Three-way match — unreconciled items ────────────────────────────
        try:
            row = db.execute(text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE match_status = 'matched')    AS matched,
                    COUNT(*) FILTER (WHERE match_status = 'mismatch')   AS mismatched,
                    COUNT(*) FILTER (WHERE match_status = 'pending')    AS pending
                FROM finance_matches
            """)).fetchone()
            data["three_way_match"] = {
                "total":     int(row[0] or 0),
                "matched":   int(row[1] or 0),
                "mismatched": int(row[2] or 0),
                "pending":   int(row[3] or 0),
            }
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] three-way match failed: {e}")
            db.rollback()
            data["three_way_match"] = {}

        # ── 4. Pending finance approvals ──────────────────────────────────────
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
            logger.warning(f"⚠️ [FinanceAgent] approvals failed: {e}")
            db.rollback()
            data["pending_approvals"] = []

        # ── 5. Finance docs processed last 24h ────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM documents
                WHERE doc_type = ANY(:types)
                  AND status = 'completed'
                  AND created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {"types": list(FINANCE_DOC_TYPES)}).fetchall()
            data["docs_last_24h"] = {r[0]: r[1] for r in rows}
            data["docs_last_24h"]["total"] = sum(r[1] for r in rows)
        except Exception as e:
            logger.warning(f"⚠️ [FinanceAgent] docs last 24h failed: {e}")
            db.rollback()
            data["docs_last_24h"] = {"total": 0}

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today   = date.today().isoformat()
        snap    = data.get("anomaly_snapshot", {})
        match   = data.get("three_way_match", {})
        approvals = data.get("pending_approvals", [])

        summary = (
            f"{snap.get('total_anomalies', 0)} financial anomalies flagged | "
            f"{match.get('mismatched', 0)} three-way match failures | "
            f"{match.get('pending', 0)} pending reconciliations | "
            f"{len(approvals)} approvals awaiting decision"
        )
        return {
            "title":   f"Finance & Audit Report — {today}",
            "summary": summary,
            "sections": {
                "anomaly_snapshot":    snap,
                "top_anomalies":       data.get("top_anomalies", []),
                "three_way_match":     match,
                "pending_approvals":   approvals,
                "documents_last_24h":  data.get("docs_last_24h", {}),
            },
        }
