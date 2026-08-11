import logging
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict, Counter
from datetime import datetime

from ..base_analyzer import BaseAnalyzer  # your existing base

logger = logging.getLogger(__name__)


class BaseFinancialAnalyzer(BaseAnalyzer):
    """
    Shared utilities for all financial analyzers:
    - Bank statements
    - Invoices
    - Receipts
    - Purchase orders
    - Expense reports
    - Credit memos, etc.
    """

    # ============================================================
    # SAFE CONVERSIONS / PARSING
    # ============================================================

    def safe_float(self, value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            if isinstance(value, (int, float)):
                return float(value)
            s = str(value).replace(",", "").strip()
            if s.startswith("$"):
                s = s[1:]
            return float(s)
        except Exception:
            return default

    def safe_int(self, value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            if isinstance(value, (int, float)):
                return int(value)
            s = str(value).replace(",", "").strip()
            return int(float(s))
        except Exception:
            return default

    def parse_date(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        s = str(value).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    # ============================================================
    # LINE ITEM NORMALIZATION
    # ============================================================

    def normalize_line_items(
        self,
        items: List[Dict[str, Any]],
        *,
        desc_key: str = "description",
        qty_key: str = "quantity",
        unit_price_key: str = "unit_price",
        total_key: str = "line_total",
        category_key: str = "category",
        tax_key: str = "tax",
    ) -> List[Dict[str, Any]]:
        """
        Normalize arbitrary line-item structures into a consistent shape:
        - description, category, quantity, unit_price, line_total, tax
        """
        normalized: List[Dict[str, Any]] = []

        for raw in items or []:
            qty = self.safe_float(raw.get(qty_key, 1))
            price = self.safe_float(raw.get(unit_price_key, 0))
            total = self.safe_float(raw.get(total_key, qty * price))

            normalized.append({
                "description": (raw.get(desc_key) or "").strip(),
                "category": raw.get(category_key, "Uncategorized"),
                "quantity": qty,
                "unit_price": price,
                "line_total": total,
                "tax": self.safe_float(raw.get(tax_key, 0)),
                "_raw": raw,  # keep original for debugging if needed
            })

        return normalized

    # ============================================================
    # MONETARY AGGREGATES
    # ============================================================

    def aggregate_line_items(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Basic aggregates over normalized line items.
        """
        if not items:
            return {
                "line_item_count": 0,
                "total_amount": 0.0,
                "average_line_total": 0.0,
                "max_line_total": 0.0,
                "min_line_total": 0.0,
                "total_quantity": 0.0,
                "average_quantity_per_line": 0.0,
            }

        totals = [self.safe_float(i.get("line_total", 0)) for i in items]
        quantities = [self.safe_float(i.get("quantity", 0)) for i in items]

        total_amount = sum(totals)
        total_qty = sum(quantities)
        count = len(items)

        return {
            "line_item_count": count,
            "total_amount": round(total_amount, 2),
            "average_line_total": round(total_amount / count, 2) if count else 0.0,
            "max_line_total": round(max(totals), 2),
            "min_line_total": round(min(totals), 2),
            "total_quantity": round(total_qty, 2),
            "average_quantity_per_line": round(total_qty / count, 2) if count else 0.0,
        }

    # ============================================================
    # CATEGORY / MERCHANT BREAKDOWNS
    # ============================================================

    def build_category_breakdown(
        self,
        items: List[Dict[str, Any]],
        *,
        category_key: str = "category",
        value_key: str = "line_total",
    ) -> Dict[str, Any]:
        """
        Build category breakdown dict compatible with your chart generator.
        """
        by_cat = defaultdict(lambda: {"total": 0.0, "count": 0})

        for item in items or []:
            cat = item.get(category_key, "Uncategorized")
            val = self.safe_float(item.get(value_key, 0))
            by_cat[cat]["total"] += val
            by_cat[cat]["count"] += 1

        top = sorted(by_cat.items(), key=lambda x: x[1]["total"], reverse=True)[:5]

        return {
            "available": True,
            "dimension": "Category",
            "value_field": "total",
            "total_categories": len(by_cat),
            "top_5": [
                {"category": k, "total": round(v["total"], 2), "count": v["count"]}
                for k, v in top
            ],
            "all_items": {k: round(v["total"], 2) for k, v in by_cat.items()},
        }

    def build_merchant_breakdown(
        self,
        rows: List[Dict[str, Any]],
        *,
        merchant_key: str = "merchant",
        value_key: str = "amount",
    ) -> Dict[str, Any]:
        """
        Merchant-level breakdown for bank/receipt/expense data.
        """
        by_merchant = defaultdict(lambda: {"total": 0.0, "count": 0})

        for row in rows or []:
            name = row.get(merchant_key, "Unknown")
            val = self.safe_float(row.get(value_key, 0))
            by_merchant[name]["total"] += val
            by_merchant[name]["count"] += 1

        top = sorted(by_merchant.items(), key=lambda x: x[1]["total"], reverse=True)[:5]

        return {
            "available": True,
            "dimension": "Merchant",
            "value_field": "total",
            "total_merchants": len(by_merchant),
            "top_5": [
                {"category": k, "total": round(v["total"], 2), "count": v["count"]}
                for k, v in top
            ],
            "all_items": {k: round(v["total"], 2) for k, v in by_merchant.items()},
        }

    # ============================================================
    # AMOUNT RANGE BUCKETS
    # ============================================================

    def build_amount_range_breakdown(
        self,
        values: List[float],
        *,
        ranges: Optional[List[Tuple[float, float, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Build amount-range buckets (e.g. <10, 10-50, 50-100, 100-500, >500).
        """
        if ranges is None:
            ranges = [
                (0, 10, "< $10"),
                (10, 50, "$10–$50"),
                (50, 100, "$50–$100"),
                (100, 500, "$100–$500"),
                (500, float("inf"), "$500+"),
            ]

        bucket_counts = Counter()
        bucket_totals = defaultdict(float)

        for v in values or []:
            val = self.safe_float(v, 0)
            for low, high, label in ranges:
                if low <= val < high:
                    bucket_counts[label] += 1
                    bucket_totals[label] += val
                    break

        return {
            "available": True,
            "dimension": "Amount Range",
            "value_field": "total",
            "total_buckets": len(ranges),
            "all_items": {
                label: round(bucket_totals[label], 2)
                for _, _, label in ranges
            },
        }

    # ============================================================
    # SIMPLE ANOMALY / RISK HELPERS
    # ============================================================

    def detect_high_value_transactions(
        self,
        rows: List[Dict[str, Any]],
        *,
        amount_key: str = "amount",
        threshold: float = 1000.0,
    ) -> List[Dict[str, Any]]:
        """
        Flag rows above a certain absolute value threshold.
        """
        anomalies = []
        for row in rows or []:
            val = self.safe_float(row.get(amount_key, 0))
            if abs(val) >= threshold:
                anomalies.append({
                    "type": "High Value",
                    "amount": val,
                    "threshold": threshold,
                    "_row": row,
                })
        return anomalies

    def detect_duplicates(
        self,
        rows: List[Dict[str, Any]],
        *,
        keys: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Simple duplicate detection using selected keys (e.g. date, amount, merchant).
        """
        seen = {}
        duplicates = []

        for row in rows or []:
            signature = tuple(row.get(k) for k in keys)
            if signature in seen:
                duplicates.append({
                    "type": "Duplicate",
                    "keys": keys,
                    "signature": signature,
                    "row": row,
                    "original": seen[signature],
                })
            else:
                seen[signature] = row

        return duplicates

    # ============================================================
    # VARIANCE / CHANGE HELPERS
    # ============================================================

    def compute_variance_percentage(
        self,
        current: float,
        baseline: float,
    ) -> Optional[float]:
        """
        Returns percentage change (current vs baseline) or None if baseline is 0.
        """
        baseline = self.safe_float(baseline)
        current = self.safe_float(current)
        if baseline == 0:
            return None
        return round((current - baseline) / baseline * 100, 2)

    # ============================================================
    # UI CONFIG HELPERS
    # ============================================================

    def metric_def(self, key: str, label: str, is_hero: bool = False) -> Dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "value_path": key,
            "is_hero": is_hero,
        }

    def table_def(self, table_id: str, title: str, data_path: str) -> Dict[str, Any]:
        return {
            "id": table_id,
            "name": table_id,
            "title": title,
            "data_path": data_path,
        }

    def chart_def(
        self,
        chart_id: str,
        title: str,
        data_path: str,
        chart_type: str,
        *,
        label_field: Optional[str] = None,
        value_field: Optional[str] = None,
        x_field: Optional[str] = None,
        y_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {
            "id": chart_id,
            "type": chart_type,
            "title": title,
            "data_path": data_path,
        }
        if label_field:
            cfg["label_field"] = label_field
        if value_field:
            cfg["value_field"] = value_field
        if x_field:
            cfg["x_field"] = x_field
        if y_field:
            cfg["y_field"] = y_field
        return cfg

    def section_def(
        self,
        sec_id: str,
        title: str,
        *,
        metrics: Optional[List[str]] = None,
        charts: Optional[List[str]] = None,
        tables: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "id": sec_id,
            "title": title,
            "metrics": metrics or [],
            "charts": charts or [],
            "tables": tables or [],
        }
