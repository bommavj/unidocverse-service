import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class DeliveryReceiptAnalyzer(BaseAnalyzer):
    """
    Analyzer for delivery receipts / proof of delivery (POD):
    - Tracking number, carrier, recipient, delivery date/time
    - Items delivered, quantities, signatures
    - Flags: missing signature, quantity mismatch, late delivery
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📦 Delivery receipt analysis: {file_path}")

        doc = self._extract_receipt(file_path, text, metadata)

        items = doc.get("delivered_items", [])
        normalized = []
        for it in items or []:
            qty_expected = self.safe_int(it.get("quantity_expected", 0))
            qty_delivered = self.safe_int(it.get("quantity_delivered", qty_expected))
            normalized.append({
                "description": it.get("description"),
                "quantity_expected": qty_expected,
                "quantity_delivered": qty_delivered,
                "unit": it.get("unit"),
                "_raw": it,
            })

        total_items = len(normalized)
        qty_mismatches = [i for i in normalized if i["quantity_expected"] != i["quantity_delivered"]]
        missing_signature = not bool(doc.get("recipient_signature"))
        delivery_date = doc.get("delivery_date")
        late_delivery = False
        try:
            promised = doc.get("promised_delivery_date")
            if promised and delivery_date:
                p = datetime.fromisoformat(promised)
                d = datetime.fromisoformat(delivery_date)
                late_delivery = d > p
        except Exception:
            pass

        insights = [
            f"Items delivered: {total_items}",
        ]
        if qty_mismatches:
            insights.append(f"{len(qty_mismatches)} quantity mismatches detected.")
        if missing_signature:
            insights.append("Recipient signature missing.")
        if late_delivery:
            insights.append("Delivery occurred after promised date.")

        recommendations = []
        if qty_mismatches:
            recommendations.append({
                "priority": "high",
                "category": "operations",
                "title": "Resolve Quantity Mismatches",
                "description": f"{len(qty_mismatches)} items have quantity discrepancies; confirm with carrier and recipient.",
                "potential_savings": 0,
            })
        if missing_signature:
            recommendations.append({
                "priority": "medium",
                "category": "compliance",
                "title": "Obtain Recipient Signature",
                "description": "Missing signature; request proof of delivery or follow up with recipient.",
                "potential_savings": 0,
            })
        if late_delivery:
            recommendations.append({
                "priority": "medium",
                "category": "service",
                "title": "Investigate Late Delivery",
                "description": "Delivery occurred after promised date; review carrier performance and SLA.",
                "potential_savings": 0,
            })

        result = {
            "type": "delivery_receipt",
            "summary": self._generate_summary(doc),
            "confidence": 0.95,

            "tracking_number": doc.get("tracking_number"),
            "carrier": doc.get("carrier"),
            "recipient_name": doc.get("recipient_name"),
            "delivery_date": doc.get("delivery_date"),
            "promised_delivery_date": doc.get("promised_delivery_date"),
            "recipient_signature": doc.get("recipient_signature"),

            "delivered_items": normalized,
            "total_items": total_items,
            "quantity_mismatch_count": len(qty_mismatches),
            "missing_signature": missing_signature,
            "late_delivery": late_delivery,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_receipt_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_receipt(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_delivery_parser import UniversalDeliveryParser
            parser = UniversalDeliveryParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Delivery receipt parsing failed: {e}")
            parsed = {}

        return {
            "tracking_number": parsed.get("tracking_number") or metadata.get("tracking_number"),
            "carrier": parsed.get("carrier"),
            "recipient_name": parsed.get("recipient_name"),
            "delivery_date": parsed.get("delivery_date"),
            "promised_delivery_date": parsed.get("promised_delivery_date"),
            "recipient_signature": parsed.get("recipient_signature"),
            "delivered_items": parsed.get("delivered_items", []),  # [{description, quantity_expected, quantity_delivered, unit}]
        }

    def _generate_summary(self, doc: Dict[str, Any]) -> str:
        tn = doc.get("tracking_number", "N/A")
        rec = doc.get("recipient_name", "Recipient")
        return f"Delivery {tn} delivered to {rec} on {doc.get('delivery_date', 'N/A')}."

    # ---------------- UI CONFIG ----------------

    def build_receipt_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "total_items", "label": "Items", "value_path": "total_items", "is_hero": True},
            {"key": "quantity_mismatch_count", "label": "Qty Mismatches", "value_path": "quantity_mismatch_count", "is_hero": True},
            {"key": "missing_signature", "label": "Missing Signature", "value_path": "missing_signature", "is_hero": True},
            {"key": "late_delivery", "label": "Late Delivery", "value_path": "late_delivery", "is_hero": True},
        ]

        charts = []

        tables = [
            {"id": "delivered_items", "name": "delivered_items", "title": "Delivered Items", "data_path": "delivered_items"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Delivery Summary", "metrics": ["total_items", "quantity_mismatch_count", "missing_signature", "late_delivery"], "charts": [], "tables": []},
            {"id": "items", "title": "Delivered Items", "metrics": [], "charts": [], "tables": ["delivered_items"]},
            {"id": "recommendations", "title": "Recommendations", "metrics": [], "charts": [], "tables": ["recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
