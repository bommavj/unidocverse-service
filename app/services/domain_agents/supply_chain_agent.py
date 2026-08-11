"""
supply_chain_agent.py — Supply Chain daily report (supply_chain_mode)
Runs daily at 07:30.
"""
import logging
from datetime import date
from typing import Any
from sqlalchemy import text
from app.services.domain_agents.base_domain_agent import BaseDomainAgent

logger = logging.getLogger(__name__)

SC_TYPES = ("purchase_order", "invoice", "bill_of_lading", "packing_slip",
            "vendor_contract", "sds", "shipping_document", "goods_receipt",
            "supplier_agreement", "customs_document")


class SupplyChainAgent(BaseDomainAgent):
    agent_type = "supply_chain"
    task_name  = "Supply Chain Daily Report"

    def collect(self) -> dict[str, Any]:
        db   = self.db
        data: dict[str, Any] = {}

        # --- Volume ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 day')  AS last_24h,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS last_7,
                       COUNT(*) FILTER (WHERE status = 'failed')                        AS failed,
                       COUNT(*) AS total
                FROM documents WHERE doc_type = ANY(:types)
            """), {"types": list(SC_TYPES)}).fetchone()
            data["activity"] = {
                "last_24h": row[0], "last_7_days": row[1],
                "failed": row[2], "total": row[3]
            }
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] activity: {e}")
            data["activity"] = {}

        # --- Breakdown by type ---
        try:
            rows = db.execute(text("""
                SELECT doc_type, COUNT(*) AS cnt
                FROM documents WHERE doc_type = ANY(:types) AND status = 'processed'
                GROUP BY doc_type ORDER BY cnt DESC
            """), {"types": list(SC_TYPES)}).fetchall()
            data["doc_breakdown"] = [{"type": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] doc_breakdown: {e}")
            data["doc_breakdown"] = []

        # --- PO vs Invoice match (3-way match anomalies) ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FROM documents
                WHERE doc_type IN ('purchase_order', 'invoice')
                  AND doc_metadata->'anomalies' IS NOT NULL
                  AND jsonb_array_length(doc_metadata->'anomalies') > 0
                  AND created_at >= NOW() - INTERVAL '7 days'
            """)).scalar()
            data["match_anomalies"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] match_anomalies: {e}")
            data["match_anomalies"] = 0

        # --- Unique vendors ---
        try:
            row = db.execute(text("""
                SELECT COUNT(DISTINCT client_id) FROM documents
                WHERE doc_type = ANY(:types) AND client_id IS NOT NULL
            """), {"types": list(SC_TYPES)}).scalar()
            data["unique_vendors"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] unique_vendors: {e}")
            data["unique_vendors"] = 0

        # --- Needs review ---
        try:
            row = db.execute(text("""
                SELECT COUNT(*) FROM documents
                WHERE doc_type = ANY(:types)
                  AND (doc_metadata->>'needs_manual_review')::boolean = TRUE
            """), {"types": list(SC_TYPES)}).scalar()
            data["needs_review"] = int(row or 0)
        except Exception as e:
            logger.warning(f"⚠️ [SupplyChainAgent] needs_review: {e}")
            data["needs_review"] = 0

        return data

    def build_report(self, data: dict[str, Any]) -> dict[str, Any]:
        activity = data.get("activity", {})
        summary = (
            f"{activity.get('last_24h', 0)} supply chain docs processed in last 24h | "
            f"{data.get('match_anomalies', 0)} PO/invoice anomalies this week | "
            f"{data.get('unique_vendors', 0)} vendors tracked"
        )
        return {
            "title":   f"Supply Chain Report — {date.today().isoformat()}",
            "summary": summary,
            "sections": {
                "activity":           activity,
                "document_breakdown": data.get("doc_breakdown", []),
                "match_anomalies":    {"flagged_last_7_days": data.get("match_anomalies", 0)},
                "review_queue":       {"needs_review": data.get("needs_review", 0)},
                "vendor_coverage":    {"unique_vendors": data.get("unique_vendors", 0)},
            },
        }
