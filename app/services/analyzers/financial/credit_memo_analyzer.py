import logging
from typing import Dict, Any, List
from datetime import datetime

from .base_financial_analyzer import BaseFinancialAnalyzer

logger = logging.getLogger(__name__)


class CreditMemoAnalyzer(BaseFinancialAnalyzer):
    """
    Domain-specific analyzer for Credit Memos.
    - Refunds, adjustments, returns
    - Line items (qty, unit price, refund amount)
    - Category breakdown
    - High-value refunds
    - Duplicate refunds
    - Recommendations
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"↩️ Advanced Credit Memo analysis: {file_path}")

        memo = self._extract_credit_memo(file_path, text, metadata)

        items = memo.get("line_items", [])
        items = self.normalize_line_items(
            items,
            desc_key="description",
            qty_key="quantity",
            unit_price_key="unit_price",
            total_key="refund_amount",
            category_key="category",
            tax_key="tax",
        )

        memo["line_items"] = items

        aggregates = self.aggregate_line_items(items)
        category_breakdown = self.build_category_breakdown(items)
        amount_range_breakdown = self.build_amount_range_breakdown(
            [i["line_total"] for i in items]
        )

        high_value = self.detect_high_value_transactions(
            items, amount_key="line_total", threshold=1000
        )
        duplicates = self.detect_duplicates(
            items, keys=["description", "line_total"]
        )

        insights = self._generate_insights(memo, aggregates, high_value)
        recommendations = self._generate_recommendations(memo, aggregates, high_value, duplicates)

        result = {
            "type": "credit_memo",
            "summary": self._generate_summary(memo, aggregates),
            "confidence": 0.95,

            # Header
            "credit_memo_number": memo.get("credit_memo_number"),
            "invoice_number": memo.get("invoice_number"),
            "customer_id": memo.get("customer_id"),
            "vendor_name": memo.get("vendor_name"),
            "date_issued": memo.get("date_issued"),
            "currency": memo.get("currency", "USD"),

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

        result["ui_config"] = self.build_credit_memo_ui_config(result)
        result["has_advanced_analytics"] = True

        return result

    # ============================================================
    # EXTRACTION
    # ============================================================

    def _extract_credit_memo(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_credit_memo_parser import UniversalCreditMemoParser
            parser = UniversalCreditMemoParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Credit memo parsing failed: {e}")
            parsed = {}

        return {
            "credit_memo_number": parsed.get("credit_memo_number"),
            "invoice_number": parsed.get("invoice_number"),
            "customer_id": parsed.get("customer_id"),
            "vendor_name": parsed.get("vendor_name"),
            "date_issued": parsed.get("date_issued"),
            "currency": parsed.get("currency", "USD"),
            "line_items": parsed.get("line_items", []),
        }

    # ============================================================
    # SUMMARY & INSIGHTS
    # ============================================================

    def _generate_summary(self, memo, aggregates):
        total = aggregates.get("total_amount", 0)
        cm = memo.get("credit_memo_number", "N/A")
        vendor = memo.get("vendor_name", "Vendor")
        return f"Credit Memo {cm} issued by {vendor} totals a refund of ${total:,.2f}."

    def _generate_insights(self, memo, aggregates, high_value):
        insights = []
        total = aggregates.get("total_amount", 0)
        count = aggregates.get("line_item_count", 0)

        insights.append(f"Total refund amount: ${total:,.2f}")
        insights.append(f"Refund line items: {count}")

        if high_value:
            insights.append(f"{len(high_value)} high-value refund items detected.")

        return insights

    def _generate_recommendations(self, memo, aggregates, high_value, duplicates):
        recs = []

        total = aggregates.get("total_amount", 0)

        if total > 5000:
            recs.append({
                "priority": "high",
                "category": "refund",
                "title": "Large Refund Amount",
                "description": f"Total refund of ${total:,.2f} may require approval.",
                "potential_savings": 0,
            })

        if high_value:
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "High-Value Refunds",
                "description": f"{len(high_value)} refund items exceed the high-value threshold.",
                "potential_savings": 0,
            })

        if duplicates:
            recs.append({
                "priority": "medium",
                "category": "quality",
                "title": "Duplicate Refund Items",
                "description": f"{len(duplicates)} items appear duplicated.",
                "potential_savings": 0,
            })

        return recs

    # ============================================================
    # UI CONFIG
    # ============================================================

    def build_credit_memo_ui_config(self, result):
        hero_metrics = [
            self.metric_def("total_amount", "Total Refund", True),
            self.metric_def("line_item_count", "Refund Items", True),
            self.metric_def("high_value_count", "High-Value Items", True),
            self.metric_def("duplicate_item_count", "Duplicate Items", True),
        ]

        secondary_metrics = [
            self.metric_def("average_line_total", "Avg Refund"),
            self.metric_def("max_line_total", "Max Refund"),
            self.metric_def("min_line_total", "Min Refund"),
        ]

        charts = [
            self.chart_def(
                "category_breakdown",
                "Refunds by Category",
                "category_breakdown.all_items",
                "bar",
                label_field="category",
                value_field="total",
            ),
            self.chart_def(
                "amount_range_breakdown",
                "Refund Amount Ranges",
                "amount_range_breakdown.all_items",
                "bar",
                label_field="category",
                value_field="total",
            ),
        ]

        tables = [
            self.table_def("line_items", "Refund Line Items", "line_items"),
            self.table_def("high_value_items", "High-Value Refunds", "high_value_items"),
            self.table_def("duplicate_items", "Duplicate Refunds", "duplicate_items"),
            self.table_def("recommendations", "Recommendations", "recommendations"),
        ]

        sections = [
            self.section_def(
                "summary",
                "Credit Memo Summary",
                metrics=["total_amount", "line_item_count", "high_value_count", "duplicate_item_count"],
            ),
            self.section_def(
                "refunds",
                "Refund Details",
                metrics=["average_line_total", "max_line_total", "min_line_total"],
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
