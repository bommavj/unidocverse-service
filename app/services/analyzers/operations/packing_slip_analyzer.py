# app/services/analyzers/operations/packing_slip_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class PackingSlipAnalyzer(BaseAnalyzer):
    """
    Analyzer for packing slips:
    - Extracts shipment id, order id, shipped items, quantities, backordered items
    - Computes shipped vs ordered quantities, identifies shortages and over-shipments
    - Flags: backorders, quantity mismatches, missing order id
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📦 Packing slip analysis: {file_path}")

        slip = self._extract_slip(file_path, text, metadata)
        items = slip.get("items", [])
        normalized = self._normalize_items(items)
        metrics = self._compute_metrics(normalized)
        mismatches = self._find_mismatches(normalized)
        risk_metrics = self._risk_checks(slip, metrics, mismatches)
        insights = self._generate_insights(slip, metrics, mismatches, risk_metrics)
        recommendations = self._generate_recommendations(slip, mismatches, risk_metrics)

        result = {
            "type": "packing_slip",
            "summary": self._generate_summary(slip, metrics),
            "confidence": 0.95,

            "shipment_id": slip.get("shipment_id"),
            "order_id": slip.get("order_id"),
            "ship_date": slip.get("ship_date"),
            "carrier": slip.get("carrier"),

            "items": normalized,
            **metrics,
            "mismatches": mismatches,
            **risk_metrics,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_packing_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_slip(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_packing_slip_parser import UniversalPackingSlipParser
            parser = UniversalPackingSlipParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Packing slip parsing failed: {e}")
            parsed = {}

        return {
            "shipment_id": parsed.get("shipment_id") or metadata.get("shipment_id"),
            "order_id": parsed.get("order_id"),
            "ship_date": parsed.get("ship_date"),
            "carrier": parsed.get("carrier"),
            "items": parsed.get("items", []),  # [{sku, description, qty_ordered, qty_shipped, backordered}]
        }

    def _normalize_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for it in items or []:
            ordered = int(it.get("qty_ordered", 0) or 0)
            shipped = int(it.get("qty_shipped", 0) or 0)
            back = int(it.get("backordered", 0) or max(0, ordered - shipped))
            out.append({
                "sku": it.get("sku"),
                "description": it.get("description"),
                "qty_ordered": ordered,
                "qty_shipped": shipped,
                "backordered": back,
                "_raw": it,
            })
        return out

    def _compute_metrics(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_ordered = sum(i.get("qty_ordered", 0) for i in items)
        total_shipped = sum(i.get("qty_shipped", 0) for i in items)
        total_backordered = sum(i.get("backordered", 0) for i in items)
        item_count = len(items)
        return {"total_ordered": total_ordered, "total_shipped": total_shipped, "total_backordered": total_backordered, "item_count": item_count}

    def _find_mismatches(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [i for i in items if i.get("qty_ordered") != i.get("qty_shipped")]

    def _risk_checks(self, slip: Dict[str, Any], metrics: Dict[str, Any], mismatches: List[Dict[str, Any]]) -> Dict[str, Any]:
        risks = []
        if not slip.get("order_id"):
            risks.append("Order ID missing; cannot reconcile with order system.")
        if metrics.get("total_backordered", 0) > 0:
            risks.append(f"{metrics['total_backordered']} units backordered.")
        if mismatches:
            risks.append(f"{len(mismatches)} line(s) with quantity mismatches.")
        return {"packing_risks": risks, "packing_risk_count": len(risks)}

    def _generate_summary(self, slip: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        sid = slip.get("shipment_id", "Shipment")
        return f"Packing slip {sid}: shipped {metrics.get('total_shipped',0)} of {metrics.get('total_ordered',0)} ordered."

    def _generate_insights(self, slip, metrics, mismatches, risk_metrics) -> List[str]:
        insights = [
            f"Total ordered: {metrics.get('total_ordered',0)}",
            f"Total shipped: {metrics.get('total_shipped',0)}",
            f"Backordered: {metrics.get('total_backordered',0)}",
        ]
        if mismatches:
            insights.append(f"Mismatched lines: {len(mismatches)}")
        if risk_metrics.get("packing_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['packing_risk_count']}.")
        return insights

    def _generate_recommendations(self, slip, mismatches, risk_metrics) -> List[Dict[str, Any]]:
        recs = []
        for r in risk_metrics.get("packing_risks", []):
            recs.append({"priority": "high" if "missing" in r.lower() or "backordered" in r.lower() else "medium", "category": "operations", "title": "Packing Issue", "description": r, "potential_savings": 0})
        if mismatches:
            recs.append({"priority": "medium", "category": "reconciliation", "title": "Reconcile Quantities", "description": "Compare packing slip to order and inventory records to resolve mismatches.", "potential_savings": 0})
        return recs

    def build_packing_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero = [
            {"key": "item_count", "label": "Line Items", "value_path": "item_count", "is_hero": True},
            {"key": "total_shipped", "label": "Total Shipped", "value_path": "total_shipped", "is_hero": True},
            {"key": "total_backordered", "label": "Backordered", "value_path": "total_backordered", "is_hero": True},
            {"key": "packing_risk_count", "label": "Risk Flags", "value_path": "packing_risk_count", "is_hero": True},
        ]
        charts = []
        tables = [
            {"id": "items", "name": "items", "title": "Items", "data_path": "items"},
            {"id": "mismatches", "name": "mismatches", "title": "Mismatches", "data_path": "mismatches"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]
        sections = [
            {"id": "summary", "title": "Packing Summary", "metrics": ["total_ordered", "total_shipped", "total_backordered", "packing_risk_count"], "charts": [], "tables": []},
            {"id": "items", "title": "Items", "metrics": [], "charts": [], "tables": ["items"]},
            {"id": "reconciliation", "title": "Reconciliation & Risks", "metrics": [], "charts": [], "tables": ["mismatches", "recommendations"]},
        ]
        return {"hero_metrics": hero, "charts": charts, "tables": tables, "sections": sections}
