"""
real_estate_agent.py — Real Estate daily intelligence report.

Morning briefing for real estate teams:
  1. Portfolio snapshot    — total properties, closing pipeline progress
  2. Closing pipeline      — properties by progress stage, missing checklist items
  3. Stalled closings      — properties with incomplete checklist items (no appraisal / title / escrow)
  4. Shipment / delivery   — any supply chain items tied to properties (if applicable)
  5. Recent activity       — docs processed in last 24h
  6. Documents needing review — real estate docs with no property linked
"""

import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

RE_DOC_TYPES = ('lease_agreement','appraisal','inspection_report','title_deed',
                'closing_disclosure','mortgage','deed_of_trust','purchase_agreement',
                'real_estate')


class RealEstateAgent(BaseDomainAgent):
    agent_type = "real_estate"
    task_name  = "Real Estate Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Portfolio snapshot ──────────────────────────────────────────────
        try:
            row = db.execute(text("""
                SELECT
                    COUNT(*)                                               AS total,
                    COUNT(*) FILTER (WHERE is_escrow_funded = TRUE)       AS escrow_funded,
                    COUNT(*) FILTER (WHERE has_clear_title = TRUE)        AS clear_title,
                    COUNT(*) FILTER (WHERE has_appraisal = TRUE)          AS appraised,
                    ROUND(AVG(price::numeric))                             AS avg_price
                FROM (SELECT * FROM real_estate_properties WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) real_estate_properties
            """), self.tp).fetchone()
            data["portfolio_snapshot"] = {
                "total_properties":  int(row[0] or 0),
                "escrow_funded":     int(row[1] or 0),
                "clear_title":       int(row[2] or 0),
                "appraised":         int(row[3] or 0),
                "avg_price":         float(row[4] or 0),
            }
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] portfolio snapshot failed: {e}")
            db.rollback()
            data["portfolio_snapshot"] = {}

        # ── 2. Stalled closings — missing checklist items ─────────────────────
        try:
            rows = db.execute(text("""
                SELECT
                    address, buyer_name, price, progress,
                    has_appraisal, has_clear_title, is_escrow_funded,
                    created_at::date AS opened_on,
                    CURRENT_DATE - created_at::date AS days_open
                FROM (SELECT * FROM real_estate_properties WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) real_estate_properties
                WHERE NOT (has_appraisal AND has_clear_title AND is_escrow_funded)
                ORDER BY created_at
            """), self.tp).fetchall()
            data["stalled_closings"] = [{
                "address":       r[0] or "—",
                "buyer":         r[1] or "—",
                "price":         float(r[2] or 0),
                "progress":      r[3] or "—",
                "missing": ", ".join(filter(None, [
                    None if r[4] else "Appraisal",
                    None if r[5] else "Clear Title",
                    None if r[6] else "Escrow",
                ])) or "—",
                "opened_on":     r[7].isoformat() if r[7] else "—",
                "days_open":     int(r[8] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] stalled closings failed: {e}")
            db.rollback()
            data["stalled_closings"] = []

        # ── 3. Ready to close — all checklist items complete ─────────────────
        try:
            rows = db.execute(text("""
                SELECT address, buyer_name, price, progress
                FROM (SELECT * FROM real_estate_properties WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) real_estate_properties
                WHERE has_appraisal = TRUE
                  AND has_clear_title = TRUE
                  AND is_escrow_funded = TRUE
                ORDER BY price DESC
            """), self.tp).fetchall()
            data["ready_to_close"] = [{
                "address":  r[0] or "—",
                "buyer":    r[1] or "—",
                "price":    float(r[2] or 0),
                "progress": r[3] or "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] ready to close failed: {e}")
            db.rollback()
            data["ready_to_close"] = []

        # ── 4. Real estate docs processed last 24h ────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM (SELECT * FROM documents WHERE tenant_id::text = :tenant_id) documents
                WHERE doc_type = ANY(:types)
                  AND status = 'completed'
                  AND created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {**self.tp, "types": list(RE_DOC_TYPES)}).fetchall()
            data["docs_last_24h"] = {r[0]: r[1] for r in rows}
            data["docs_last_24h"]["total"] = sum(r[1] for r in rows)
        except Exception as e:
            logger.warning(f"⚠️ [RealEstateAgent] docs last 24h failed: {e}")
            db.rollback()
            data["docs_last_24h"] = {"total": 0}

        # ── 5. Milestones due soon ────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'real_estate_milestones' LIMIT 1
            """), self.tp).fetchone()
            if rows:
                ms = db.execute(text("""
                    SELECT * FROM (SELECT * FROM real_estate_milestones WHERE property_id IN (SELECT id FROM real_estate_properties WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id))) real_estate_milestones
                    WHERE due_date <= CURRENT_DATE + 14
                    ORDER BY due_date                """), self.tp).fetchall()
                data["milestones_due"] = [dict(zip([c.name for c in ms.cursor.description], r)) for r in ms] if ms else []
            else:
                data["milestones_due"] = []
        except Exception as e:
            db.rollback()
            data["milestones_due"] = []

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today   = date.today().isoformat()
        snap    = data.get("portfolio_snapshot", {})
        stalled = data.get("stalled_closings", [])
        ready   = data.get("ready_to_close", [])
        docs    = data.get("docs_last_24h", {})

        summary = (
            f"{snap.get('total_properties', 0)} properties in pipeline | "
            f"{len(ready)} ready to close | "
            f"{len(stalled)} stalled (missing checklist items) | "
            f"{docs.get('total', 0)} docs processed in last 24h"
        )
        return {
            "title":   f"Real Estate Report — {today}",
            "summary": summary,
            "sections": {
                "portfolio_snapshot":    snap,
                "ready_to_close":        ready,
                "stalled_closings":      stalled,
                "documents_last_24h":   data.get("docs_last_24h", {}),
            },
        }
