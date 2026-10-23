"""
supply_chain_agent.py — Supply Chain & Logistics daily intelligence report.

Morning briefing for supply chain teams:
  1. Shipment snapshot      — total shipments, customs status, reconciliation rate
  2. Customs holds          — shipments with customs_status != 'cleared'
  3. Unreconciled shipments — shipments with is_reconciled = FALSE
  4. Supplier risk          — suppliers with sanctions_status != 'clear' or expiring contracts
  5. Docs processed last 24h
"""

import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

SC_DOC_TYPES = ('bill_of_lading','purchase_order','customs_declaration','packing_list',
                'commercial_invoice','certificate_of_origin','freight_invoice','manifest')


class SupplyChainAgent(BaseDomainAgent):
    agent_type = "supply_chain"
    task_name  = "Supply Chain Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # ── 1. Shipment snapshot ──────────────────────────────────────────────
        try:
            row = db.execute(text("""
                SELECT
                    COUNT(*)                                                    AS total,
                    COUNT(*) FILTER (WHERE customs_status = 'cleared')         AS cleared,
                    COUNT(*) FILTER (WHERE customs_status != 'cleared')        AS customs_holds,
                    COUNT(*) FILTER (WHERE is_reconciled = FALSE)              AS unreconciled,
                    COUNT(DISTINCT carrier_name)                               AS carriers
                FROM (SELECT * FROM supply_chain_shipments WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) supply_chain_shipments
            """), self.tp).fetchone()
            data["shipment_snapshot"] = {
                "total_shipments":   int(row[0] or 0),
                "customs_cleared":   int(row[1] or 0),
                "customs_holds":     int(row[2] or 0),
                "unreconciled":      int(row[3] or 0),
                "unique_carriers":   int(row[4] or 0),
            }
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] shipment snapshot failed: {e}")
            db.rollback()
            data["shipment_snapshot"] = {}

        # ── 2. Customs holds ──────────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT bol_number, carrier_name, origin, destination,
                       customs_status, created_at::date AS date
                FROM (SELECT * FROM supply_chain_shipments WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) supply_chain_shipments
                WHERE customs_status != 'cleared'
                ORDER BY created_at DESC
            """), self.tp).fetchall()
            data["customs_holds"] = [{
                "bol":        r[0] or "—",
                "carrier":    r[1] or "—",
                "origin":     r[2] or "—",
                "destination": r[3] or "—",
                "status":     r[4] or "—",
                "date":       r[5].isoformat() if r[5] else "—",
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] customs holds failed: {e}")
            db.rollback()
            data["customs_holds"] = []

        # ── 3. Unreconciled shipments ─────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT bol_number, carrier_name, origin, destination,
                       created_at::date AS date,
                       CURRENT_DATE - created_at::date AS age_days
                FROM (SELECT * FROM supply_chain_shipments WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) supply_chain_shipments
                WHERE is_reconciled = FALSE
                ORDER BY created_at
            """), self.tp).fetchall()
            data["unreconciled_shipments"] = [{
                "bol":         r[0] or "—",
                "carrier":     r[1] or "—",
                "origin":      r[2] or "—",
                "destination": r[3] or "—",
                "date":        r[4].isoformat() if r[4] else "—",
                "age_days":    int(r[5] or 0),
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] unreconciled failed: {e}")
            db.rollback()
            data["unreconciled_shipments"] = []

        # ── 4. Supplier risk ──────────────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT name, type, sanctions_status,
                       expiry_date,
                       CASE WHEN expiry_date IS NOT NULL
                            THEN expiry_date - CURRENT_DATE END AS days_to_expiry
                FROM (SELECT * FROM supply_chain_suppliers WHERE document_id IN (SELECT id FROM documents WHERE tenant_id::text = :tenant_id)) supply_chain_suppliers
                WHERE sanctions_status != 'clear'
                   OR (expiry_date IS NOT NULL AND expiry_date <= CURRENT_DATE + 60)
                ORDER BY sanctions_status DESC, expiry_date
            """), self.tp).fetchall()
            data["supplier_risk"] = [{
                "supplier":       r[0] or "—",
                "type":           r[1] or "—",
                "sanctions":      r[2] or "—",
                "contract_expiry": r[3].isoformat() if r[3] else "—",
                "days_to_expiry": int(r[4] or 0) if r[4] is not None else None,
            } for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] supplier risk failed: {e}")
            db.rollback()
            data["supplier_risk"] = []

        # ── 5. Docs processed last 24h ────────────────────────────────────────
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM (SELECT * FROM documents WHERE tenant_id::text = :tenant_id) documents
                WHERE doc_type = ANY(:types)
                  AND status = 'completed'
                  AND created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {**self.tp, "types": list(SC_DOC_TYPES)}).fetchall()
            data["docs_last_24h"] = {r[0]: r[1] for r in rows}
            data["docs_last_24h"]["total"] = sum(r[1] for r in rows)
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] docs last 24h failed: {e}")
            db.rollback()
            data["docs_last_24h"] = {"total": 0}

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        today  = date.today().isoformat()
        snap   = data.get("shipment_snapshot", {})
        holds  = data.get("customs_holds", [])
        unrec  = data.get("unreconciled_shipments", [])
        risk   = data.get("supplier_risk", [])

        summary = (
            f"{snap.get('total_shipments', 0)} shipments | "
            f"{snap.get('customs_holds', 0)} customs holds | "
            f"{snap.get('unreconciled', 0)} unreconciled | "
            f"{len(risk)} suppliers flagged"
        )
        return {
            "title":   f"Supply Chain Report — {today}",
            "summary": summary,
            "sections": {
                "shipment_snapshot":      snap,
                "customs_holds":          holds,
                "unreconciled_shipments": unrec,
                "supplier_risk":          risk,
                "documents_last_24h":     data.get("docs_last_24h", {}),
            },
        }
