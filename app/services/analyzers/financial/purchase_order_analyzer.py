import logging
from typing import Dict, Any, List
from datetime import datetime

from .base_financial_analyzer import BaseFinancialAnalyzer

logger = logging.getLogger(__name__)


class PurchaseOrderAnalyzer(BaseFinancialAnalyzer):
    """
    Domain-specific analyzer for Purchase Orders (POs).
    - Vendor, buyer, PO number
    - Line items (qty, unit price, total)
    - Expected delivery dates
    - Category breakdown
    - Amount-range breakdown
    - High-value items
    - Duplicate line items
    - Recommendations
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📦 Advanced Purchase Order analysis: {file_path}")

        po = self._extract_po(file_path, text, metadata)

        items = po.get("line_items", [])

        # Normalize + aggregate
        items = self.normalize_line_items(items)
        aggregates = self.aggregate_line_items(items)
        category_breakdown = self.build_category_breakdown(items)
        amount_range_breakdown = self.build_amount_range_breakdown(
            [i["line_total"] for i in items]
        )

        # Anomalies
        high_value = self.detect_high_value_transactions(
            items, amount_key="line_total", threshold=5000
        )
        duplicates = self.detect_duplicates(
            items, keys=["description", "unit_price", "quantity"]
        )

        insights = self._generate_insights(po, aggregates, high_value, duplicates)
        recommendations = self._generate_recommendations(po, aggregates, high_value, duplicates)

        result = {
            "type": "purchase_order",
            "summary": self._generate_summary(po, aggregates),
            "confidence": 0.95,

            # Header
            "po_number": po.get("po_number"),
            "vendor_name": po.get("vendor_name"),
            "buyer_name": po.get("buyer_name"),
            "order_date": po.get("order_date"),
            "expected_delivery": po.get("expected_delivery"),
            "currency": po.get("currency", "USD"),

            # Items
            "line_items": items,
            **aggregates,

            # Breakdowns
            "category_breakdown": category_breakdown,
            "amount_range_breakdown": amount_range_breakdown,

            # Anomalies
            "high_value_items": high_value,
            "duplicate_items": duplicates,
            "high_value_count": len(high_value),
            "duplicate_item_count": len(duplicates),

            # Insights
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_po_ui_config(result)
        result["has_advanced_analytics"] = True

        return result

    # ============================================================
    # EXTRACTION
    # ============================================================

    def _extract_po(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use a universal parser; normalize to stable schema.
        """
        try:
            from app.services.parsers.universal_purchase_order_parser import UniversalPurchaseOrderParser
            parser = UniversalPurchaseOrderParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ PO parsing failed: {e}")
            parsed = {}

        return {
            "po_number": parsed.get("po_number") or metadata.get("po_number"),
            "vendor_name": parsed.get("vendor_name"),
            "buyer_name": parsed.get("buyer_name"),
            "order_date": parsed.get("order_date"),
            "expected_delivery": parsed.get("expected_delivery"),
            "currency": parsed.get("currency", "USD"),
            "line_items": parsed.get("line_items", []),
        }

    # ============================================================
    # SUMMARY & INSIGHTS
    # ============================================================

    def _generate_summary(self, po, aggregates):
        total = aggregates.get("total_amount", 0)
        vendor = po.get("vendor_name", "Vendor")
        po_no = po.get("po_number", "N/A")
        return f"Purchase Order {po_no} issued to {vendor} totaling ${total:,.2f}."

    def _generate_insights(self, po, aggregates, high_value, duplicates):
        insights = []

        total = aggregates.get("total_amount", 0)
        count = aggregates.get("line_item_count", 0)

        insights.append(f"Total PO value: ${total:,.2f}")
        insights.append(f"Line items: {count}")

        if high_value:
            insights.append(f"{len(high_value)} high-value items detected.")

        if duplicates:
            insights.append(f"{len(duplicates)} potential duplicate items found.")

        return insights

    def _generate_recommendations(self, po, aggregates, high_value, duplicates):
        recs = []

        total = aggregates.get("total_amount", 0)

        if total > 50000:
            recs.append({
                "priority": "high",
                "category": "spend",
                "title": "High-Value Purchase Order",
                "description": f"PO total is ${total:,.2f}. Consider additional approval.",
                "potential_savings": 0,
            })

        if high_value:
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Review High-Value Items",
                "description": f"{len(high_value)} items exceed the high-value threshold.",
                "potential_savings": 0,
            })

        if duplicates:
            recs.append({
                "priority": "medium",
                "category": "quality",
                "title": "Duplicate Line Items Detected",
                "description": f"{len(duplicates)} items appear duplicated.",
                "potential_savings": 0,
            })

        return recs

    # ============================================================
    # UI CONFIG (Angular-compatible)
    # ============================================================

    def build_po_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            self.metric_def("total_amount", "Total Amount", True),
            self.metric_def("line_item_count", "Line Items", True),
            self.metric_def("high_value_count", "High-Value Items", True),
            self.metric_def("duplicate_item_count", "Duplicate Items", True),
        ]

        secondary_metrics = [
            self.metric_def("average_line_total", "Avg Line Total"),
            self.metric_def("max_line_total", "Max Line Total"),
            self.metric_def("min_line_total", "Min Line Total"),
            self.metric_def("total_quantity", "Total Quantity"),
        ]

        charts = [
            self.chart_def(
                "category_breakdown",
                "Spend by Category",
                "category_breakdown.all_items",
                "bar",
                label_field="category",
                value_field="total",
            ),
            self.chart_def(
                "amount_range_breakdown",
                "Amount Range Breakdown",
                "amount_range_breakdown.all_items",
                "bar",
                label_field="category",
                value_field="total",
            ),
        ]

        tables = [
            self.table_def("line_items", "Line Items", "line_items"),
            self.table_def("high_value_items", "High-Value Items", "high_value_items"),
            self.table_def("duplicate_items", "Duplicate Items", "duplicate_items"),
            self.table_def("recommendations", "Recommendations", "recommendations"),
        ]

        sections = [
            self.section_def(
                "summary",
                "Purchase Order Summary",
                metrics=["total_amount", "line_item_count", "high_value_count", "duplicate_item_count"],
            ),
            self.section_def(
                "items",
                "Line Items",
                metrics=["average_line_total", "total_quantity"],
                charts=["category_breakdown"],
                tables=["line_items"],
            ),
            self.section_def(
                "risk",
                "Risk & Anomalies",
                metrics=["high_value_count", "duplicate_item_count"],
                charts=["amount_range_breakdown"],
                tables=["high_value_items", "duplicate_items"],
            ),
            self.section_def(
                "recommendations",
                "Recommendations",
                tables=["recommendations"],
            ),
        ]

        return {
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
