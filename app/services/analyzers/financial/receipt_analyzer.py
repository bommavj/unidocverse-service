import logging
from datetime import datetime
from typing import Dict, Any, List
from collections import defaultdict, Counter

from .base_financial_analyzer import BaseFinancialAnalyzer
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class ReceiptAnalyzer(BaseFinancialAnalyzer):
    """
    Domain-specific analyzer for receipts.
    - Merchant, subtotal, tax, total
    - Itemized purchases
    - Category mapping
    - Payment method detection
    - Spending patterns
    - Anomalies (high spend, duplicate items, unusual categories)
    - Recommendations
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🧾 Advanced receipt analysis: {file_path}")

        receipt = self._extract_receipt(file_path, text, metadata)

        items = receipt.get("items", [])

        monetary = self._monetary_metrics(receipt, items)
        item_metrics = self._item_metrics(items)
        category_metrics = self._category_metrics(items)
        payment_metrics = self._payment_metrics(receipt)
        anomaly_metrics = self._anomaly_metrics(receipt, items)

        insights = self._generate_insights(receipt, monetary, anomaly_metrics)
        recommendations = self._generate_recommendations(receipt, monetary, anomaly_metrics)

        result = {
            "type": "receipt",
            "summary": self._generate_summary(receipt, monetary),
            "confidence": 0.95,

            # Header
            "merchant_name": receipt.get("merchant_name"),
            "receipt_date": receipt.get("receipt_date"),
            "payment_method": receipt.get("payment_method"),
            "transaction_id": receipt.get("transaction_id"),

            # Monetary
            **monetary,

            # Items
            "items": items,
            **item_metrics,

            # Categories
            **category_metrics,

            # Payment
            **payment_metrics,

            # Anomalies
            **anomaly_metrics,

            # Insights
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_receipt_ui_config(result)
        result["has_advanced_analytics"] = True

        return result

    # ============================================================
    # EXTRACTION
    # ============================================================

    def _extract_receipt(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use a universal parser; normalize to stable schema.
        """
        try:
            from app.services.parsers.universal_receipt_parser import UniversalReceiptParser
            parser = UniversalReceiptParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Receipt parsing failed: {e}")
            parsed = {}

        items = parsed.get("items", [])

        normalized_items = []
        for item in items:
            qty = self.safe_float(item.get("quantity", 1))
            price = self.safe_float(item.get("unit_price", 0))
            total = self.safe_float(item.get("line_total", qty * price))

            normalized_items.append({
                "description": item.get("description", "").strip(),
                "category": item.get("category", "Uncategorized"),
                "quantity": qty,
                "unit_price": price,
                "line_total": total,
                "tax": self.safe_float(item.get("tax", 0)),
            })

        return {
            "merchant_name": parsed.get("merchant_name"),
            "receipt_date": parsed.get("receipt_date"),
            "payment_method": parsed.get("payment_method"),
            "transaction_id": parsed.get("transaction_id"),
            "subtotal": self.safe_float(parsed.get("subtotal")),
            "tax": self.safe_float(parsed.get("tax")),
            "tip": self.safe_float(parsed.get("tip")),
            "total": self.safe_float(parsed.get("total")),
            "items": normalized_items,
        }

    # ============================================================
    # METRICS
    # ============================================================

    def _monetary_metrics(self, receipt: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
        subtotal = receipt.get("subtotal")
        if not subtotal:
            subtotal = sum(i["line_total"] for i in items)

        tax = receipt.get("tax", 0)
        tip = receipt.get("tip", 0)
        total = receipt.get("total") or (subtotal + tax + tip)

        return {
            "subtotal": round(subtotal, 2),
            "tax": round(tax, 2),
            "tip": round(tip, 2),
            "total": round(total, 2),
            "item_count": len(items),
            "average_item_cost": round(total / len(items), 2) if items else 0,
        }

    def _item_metrics(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not items:
            return {}

        totals = [i["line_total"] for i in items]

        return {
            "max_item_cost": round(max(totals), 2),
            "min_item_cost": round(min(totals), 2),
        }

    def _category_metrics(self, items):
        by_cat = defaultdict(lambda: {"total": 0.0, "count": 0})

        for item in items:
            cat = item.get("category", "Uncategorized")
            by_cat[cat]["total"] += item["line_total"]
            by_cat[cat]["count"] += 1

        all_items = [
            {"category": k, "total": round(v["total"], 2), "count": v["count"]}
            for k, v in sorted(by_cat.items(), key=lambda x: x[1]["total"], reverse=True)
        ]
        top = all_items[:5]

        return {
            "category_breakdown": {
                "available": True,
                "dimension": "Category",
                "value_field": "total",
                "total_categories": len(by_cat),
                "top_5": top,
                "all_items": all_items,  # ← now list[{category, total, count}]
            }
        }

    def _payment_metrics(self, receipt: Dict[str, Any]) -> Dict[str, Any]:
        method = (receipt.get("payment_method") or "").lower()

        return {
            "is_cash": method == "cash",
            "is_card": method in ["credit", "debit", "card"],
            "is_mobile_payment": method in ["apple pay", "google pay", "samsung pay"],
        }

    def _anomaly_metrics(self, receipt: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
        anomalies = []

        total = receipt.get("total", 0)

        if total > 500:
            anomalies.append({"type": "High Spend", "amount": total, "severity": "high"})

        # Duplicate items
        desc_counts = Counter(i["description"] for i in items)
        duplicates = [d for d, c in desc_counts.items() if c > 1]

        if duplicates:
            anomalies.append({"type": "Duplicate Items", "items": duplicates, "severity": "medium"})

        return {
            "receipt_anomalies": anomalies,
            "receipt_anomaly_count": len(anomalies),
        }

    # ============================================================
    # SUMMARY & INSIGHTS
    # ============================================================

    def _generate_summary(self, receipt: Dict[str, Any], monetary: Dict[str, Any]) -> str:
        merchant = receipt.get("merchant_name", "the merchant")
        total = monetary.get("total", 0.0)
        date = receipt.get("receipt_date", "N/A")

        return f"Receipt from {merchant} on {date} totaling ${total:,.2f}."

    def _generate_insights(self, receipt, monetary, anomalies):
        insights = []

        total = monetary.get("total", 0)
        tax = monetary.get("tax", 0)
        tip = monetary.get("tip", 0)

        insights.append(f"Total spend: ${total:,.2f}")
        if tax > 0:
            insights.append(f"Tax paid: ${tax:,.2f}")
        if tip > 0:
            insights.append(f"Tip included: ${tip:,.2f}")

        for a in anomalies.get("receipt_anomalies", []):
            insights.append(f"Anomaly: {a['type']}")

        return insights

    def _generate_recommendations(self, receipt, monetary, anomalies):
        recs = []

        total = monetary.get("total", 0)

        if total > 200:
            recs.append({
                "priority": "medium",
                "category": "spending",
                "title": "High Receipt Spend",
                "description": f"This receipt totals ${total:,.2f}. Consider reviewing large purchases.",
                "potential_savings": round(total * 0.2, 2),
            })

        if anomalies.get("receipt_anomaly_count", 0) > 0:
            recs.append({
                "priority": "high",
                "category": "anomaly",
                "title": "Review Receipt Anomalies",
                "description": "Some items appear unusual or duplicated.",
                "potential_savings": 0,
            })

        return recs

    # ============================================================
    # UI CONFIG (Angular-compatible)
    # ============================================================

    def build_receipt_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "total", "label": "Total", "value_path": "total", "is_hero": True},
            {"key": "subtotal", "label": "Subtotal", "value_path": "subtotal", "is_hero": True},
            {"key": "tax", "label": "Tax", "value_path": "tax", "is_hero": True},
            {"key": "tip", "label": "Tip", "value_path": "tip", "is_hero": True},
        ]

        secondary_metrics = [
            {"key": "item_count", "label": "Item Count", "value_path": "item_count", "is_hero": False},
            {"key": "average_item_cost", "label": "Avg Item Cost", "value_path": "average_item_cost", "is_hero": False},
            {"key": "max_item_cost", "label": "Max Item Cost", "value_path": "max_item_cost", "is_hero": False},
            {"key": "min_item_cost", "label": "Min Item Cost", "value_path": "min_item_cost", "is_hero": False},
            {"key": "receipt_anomaly_count", "label": "Anomalies", "value_path": "receipt_anomaly_count", "is_hero": False},
        ]

        charts = [
            {
                "id": "category_breakdown",
                "type": "pie",
                "title": "Spend by Category",
                "data_path": "category_breakdown.all_items",
                "label_field": "category",
                "value_field": "total",
            }
        ]

        tables = [
            {"id": "items", "name": "items", "title": "Items", "data_path": "items"},
            {"id": "anomalies", "name": "anomalies", "title": "Anomalies", "data_path": "receipt_anomalies"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Receipt Summary",
                "metrics": ["total", "subtotal", "tax", "tip"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "items",
                "title": "Items",
                "metrics": ["item_count", "average_item_cost"],
                "charts": ["category_breakdown"],
                "tables": ["items"],
            },
            {
                "id": "risk",
                "title": "Anomalies",
                "metrics": ["receipt_anomaly_count"],
                "charts": [],
                "tables": ["anomalies"],
            },
            {
                "id": "recommendations",
                "title": "Recommendations",
                "metrics": [],
                "charts": [],
                "tables": ["recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
