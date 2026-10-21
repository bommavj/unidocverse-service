"""
hr_agent.py — HR & People daily intelligence report.

Morning briefing for HR teams:
  1. Workforce snapshot      — headcount by doc type, candidates in pipeline
  2. Onboarding tasks overdue — tasks past due_date not yet completed
  3. Immigration expiring     — work authorizations / visas expiring ≤90 days (critical)
  4. Top candidates           — highest match_score candidates awaiting decision
  5. Documents needing review — HR docs uploaded but not yet linked/reviewed
  6. Approval queue           — pending HR-related approvals
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

HR_DOC_TYPES = ('offer_letter','employment_contract','resume','nda','i9','w4',
                'performance_review','termination_letter','payroll','benefits_enrollment')


class HRAgent(BaseDomainAgent):
    agent_type = "hr"
    task_name  = "HR & People Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Workforce snapshot ─────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM documents
                WHERE doc_type = ANY(:types) AND status = 'completed'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {"types": list(HR_DOC_TYPES)}).fetchall()
            total = db.execute(text("""
                SELECT COUNT(DISTINCT client_id) FROM documents
                WHERE doc_type = ANY(:types) AND status = 'completed' AND client_id IS NOT NULL
            """), {"types": list(HR_DOC_TYPES)}).scalar()
            data["workforce_snapshot"] = {
                "employees_tracked": int(total or 0),
                "documents_by_type": {r[0]: r[1] for r in rows},
            }
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] workforce snapshot failed: {e}")
            db.rollback()
            data["workforce_snapshot"] = {}

        # ── 2. Onboarding tasks overdue ───────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT employee_name, title,
                       due_date,
                       CURRENT_DATE - due_date AS days_overdue
                FROM hr_onboarding_tasks
                WHERE is_done = FALSE AND due_date < CURRENT_DATE
                ORDER BY due_date
            """)).fetchall()
            data["onboarding_overdue"] = [{
                "employee":     r[0] or "—",
                "task":         r[1],
                "due_date":     r[2].isoformat() if r[2] else "—",
                "days_overdue": int(r[3] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] onboarding overdue failed: {e}")
            db.rollback()
            data["onboarding_overdue"] = []

        # ── 3. Immigration / work authorization expiring ──────────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    beneficiary_name,
                    doc_type,
                    doc_label,
                    COALESCE(valid_until, authorized_stay_until) AS expiry,
                    COALESCE(valid_until, authorized_stay_until) - CURRENT_DATE AS days_left,
                    status
                FROM client_immigration
                WHERE COALESCE(valid_until, authorized_stay_until) IS NOT NULL
                  AND COALESCE(valid_until, authorized_stay_until) >= CURRENT_DATE
                  AND COALESCE(valid_until, authorized_stay_until) <= CURRENT_DATE + 90
                ORDER BY expiry
            """)).fetchall()
            data["immigration_expiring"] = [{
                "person":    r[0] or "—",
                "doc_type":  r[1],
                "label":     r[2] or "—",
                "expires_on": r[3].isoformat() if r[3] else "—",
                "days_left": int(r[4] or 0),
                "status":    r[5],
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] immigration expiring failed: {e}")
            db.rollback()
            data["immigration_expiring"] = []

        # ── 4. Top candidates in pipeline ────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT candidate_name, role, experience_years, match_score, feedback
                FROM hr_candidates
                WHERE match_score IS NOT NULL
                ORDER BY match_score DESC
            """)).fetchall()
            data["top_candidates"] = [{
                "candidate":       r[0] or "—",
                "role":            r[1] or "—",
                "experience_yrs":  r[2],
                "match_score":     r[3],
                "feedback":        r[4] or "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] candidates failed: {e}")
            db.rollback()
            data["top_candidates"] = []

        # ── 5. Pending HR approvals ───────────────────────────────────────────
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
                "priority": r[1] or "—",
                "due_date": r[2].isoformat() if r[2] else "—",
                "age_days": int(r[3] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [HRAgent] approvals failed: {e}")
            db.rollback()
            data["pending_approvals"] = []

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today    = date.today().isoformat()
        snap     = data.get("workforce_snapshot", {})
        overdue  = data.get("onboarding_overdue", [])
        immig    = data.get("immigration_expiring", [])
        critical_immig = [i for i in immig if i["days_left"] <= 30]

        summary = (
            f"{snap.get('employees_tracked', 0)} employees tracked | "
            f"{len(overdue)} onboarding tasks overdue | "
            f"{len(critical_immig)} immigration docs expiring in 30 days"
        )
        return {
            "title":   f"HR & People Report — {today}",
            "summary": summary,
            "sections": {
                "workforce_snapshot":             data.get("workforce_snapshot", {}),
                "onboarding_tasks_overdue":       overdue,
                "immigration_expiring_90_days":   immig,
                "top_candidates_in_pipeline":     data.get("top_candidates", []),
                "pending_approvals":              data.get("pending_approvals", []),
            },
        }
