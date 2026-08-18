"""
medical_agent.py — Healthcare & Medical daily intelligence report.

Morning briefing for healthcare admin teams:
  1. Patient chart snapshot  — charts processed, consent/insurance/history status
  2. Incomplete charts        — missing consent, insurance verification, or history
  3. Biomarker alerts         — patient biomarkers needing attention
  4. Pending approvals        — medical approvals awaiting decision
  5. Docs processed last 24h  — medical docs ingested
"""

import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

MEDICAL_DOC_TYPES = ('medical_record','lab_report','prescription','discharge_summary',
                     'insurance_card','prior_authorization','radiology_report',
                     'clinical_note','intake_form','pathology_report')


class MedicalAgent(BaseDomainAgent):
    agent_type = "medical"
    task_name  = "Healthcare Admin Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Patient chart snapshot ─────────────────────────────────────────
        try:
            row = db.execute(text("""
                SELECT
                    COUNT(*)                                                  AS total_charts,
                    COUNT(*) FILTER (WHERE consent_signed = TRUE)            AS consent_ok,
                    COUNT(*) FILTER (WHERE insurance_verified = TRUE)        AS insurance_ok,
                    COUNT(*) FILTER (WHERE history_filed = TRUE)             AS history_ok,
                    ROUND(AVG(chart_score))                                  AS avg_chart_score
                FROM patient_charts
            """)).fetchone()
            data["chart_snapshot"] = {
                "total_charts":      int(row[0] or 0),
                "consent_signed":    int(row[1] or 0),
                "insurance_verified": int(row[2] or 0),
                "history_filed":     int(row[3] or 0),
                "avg_chart_score":   float(row[4] or 0),
            }
        except Exception as e:
            logger.warning(f"⚠️ [MedicalAgent] chart snapshot failed: {e}")
            db.rollback()
            data["chart_snapshot"] = {}

        # ── 2. Incomplete charts ──────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT patient_name, chart_score,
                    consent_signed, insurance_verified, history_filed
                FROM patient_charts
                WHERE NOT (consent_signed AND insurance_verified AND history_filed)
                ORDER BY chart_score ASC
            """)).fetchall()
            data["incomplete_charts"] = [{
                "patient":    r[0] or "—",
                "score":      float(r[1] or 0),
                "missing": ", ".join(filter(None, [
                    None if r[2] else "Consent",
                    None if r[3] else "Insurance",
                    None if r[4] else "History",
                ])) or "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [MedicalAgent] incomplete charts failed: {e}")
            db.rollback()
            data["incomplete_charts"] = []

        # ── 3. Biomarker summary ──────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='patient_biomarkers' LIMIT 1
            """)).fetchone()
            if rows:
                bm = db.execute(text("""
                    SELECT COUNT(*) AS total FROM patient_biomarkers
                """)).fetchone()
                data["biomarker_count"] = {"total_recorded": int(bm[0] or 0)}
            else:
                data["biomarker_count"] = {}
        except Exception as e:
            db.rollback()
            data["biomarker_count"] = {}

        # ── 4. Pending approvals ──────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT title, priority, due_date,
                       CURRENT_DATE - created_at::date AS age_days
                FROM approval_requests
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at
            """)).fetchall()
            data["pending_approvals"] = [{
                "title":    r[0],
                "priority": r[1] or "normal",
                "due_date": r[2].isoformat() if r[2] else "—",
                "age_days": int(r[3] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [MedicalAgent] approvals failed: {e}")
            db.rollback()
            data["pending_approvals"] = []

        # ── 5. Medical docs processed last 24h ────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM documents
                WHERE doc_type = ANY(:types)
                  AND status = 'completed'
                  AND created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {"types": list(MEDICAL_DOC_TYPES)}).fetchall()
            data["docs_last_24h"] = {r[0]: r[1] for r in rows}
            data["docs_last_24h"]["total"] = sum(r[1] for r in rows)
        except Exception as e:
            logger.warning(f"⚠️ [MedicalAgent] docs last 24h failed: {e}")
            db.rollback()
            data["docs_last_24h"] = {"total": 0}

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today    = date.today().isoformat()
        snap     = data.get("chart_snapshot", {})
        incomplete = data.get("incomplete_charts", [])
        approvals  = data.get("pending_approvals", [])

        summary = (
            f"{snap.get('total_charts', 0)} patient charts | "
            f"{len(incomplete)} charts incomplete | "
            f"avg chart score {snap.get('avg_chart_score', 0):.0f} | "
            f"{len(approvals)} approvals pending"
        )
        return {
            "title":   f"Healthcare Admin Report — {today}",
            "summary": summary,
            "sections": {
                "chart_snapshot":     snap,
                "incomplete_charts":  incomplete,
                "pending_approvals":  approvals,
                "documents_last_24h": data.get("docs_last_24h", {}),
            },
        }
