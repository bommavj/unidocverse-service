import logging
from typing import Dict, Any, List
from datetime import datetime

from .base_financial_analyzer import BaseFinancialAnalyzer

logger = logging.getLogger(__name__)


class ExpenseReportAnalyzer(BaseFinancialAnalyzer):
    """
    Domain-specific analyzer for Expense Reports.
    - Employee, report period, totals
    - Line items (date, category, merchant, amount)
    - Category & merchant breakdowns
    - Amount range breakdowns
    - Policy flags: high daily spend, category overspend, missing receipt flags
    - Anomalies: high-value, duplicates
    - Insights & recommendations
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"💳 Advanced Expense Report analysis: {file_path}")

        report = self._extract_expense_report(file_path, text, metadata)

        items = report.get("expenses", [])

        # Normalize line items
        items = self.normalize_line_items(
            items,
            desc_key="description",
            qty_key="quantity",
            unit_price_key="unit_price",
            total_key="amount",
            category_key="category",
            tax_key="tax",
        )

        # Attach date & merchant passthrough
        for i, raw in enumerate(report.get("expenses", [])):
            if i < len(items):
                items[i]["date"] = raw.get("date")
                items[i]["merchant"] = raw.get("merchant")
                items[i]["has_receipt"] = raw.get("has_receipt", True)

        report["expenses"] = items

        # Aggregates
        aggregates = self.aggregate_line_items(items)
        category_breakdown = self.build_category_breakdown(items)
        merchant_breakdown = self.build_merchant_breakdown(
            [{"merchant": i.get("merchant"), "amount": i.get("line_total")} for i in items],
            merchant_key="merchant",
            value_key="amount",
        )
        amount_range_breakdown = self.build_amount_range_breakdown(
            [i["line_total"] for i in items]
        )

        # Policy & anomalies
        daily_spend = self._daily_spend(items)
        policy_flags = self._policy_checks(report, items, daily_spend)
        high_value = self.detect_high_value_transactions(
            items, amount_key="line_total", threshold=500
        )
        duplicates = self.detect_duplicates(
            items, keys=["date", "merchant", "line_total"]
        )

        insights = self._generate_insights(report, aggregates, category_breakdown, policy_flags)
        recommendations = self._generate_recommendations(
            report, aggregates, category_breakdown, policy_flags, high_value, duplicates
        )

        result = {
            "type": "expense_report",
            "summary": self._generate_summary(report, aggregates),
            "confidence": 0.95,

            # Header
            "employee_name": report.get("employee_name"),
            "employee_id": report.get("employee_id"),
            "report_id": report.get("report_id"),
            "report_period_start": report.get("report_period_start"),
            "report_period_end": report.get("report_period_end"),
            "status": report.get("status"),
            "currency": report.get("currency", "USD"),

            # Expenses
            "expenses": items,
            **aggregates,

            # Breakdowns
            "category_breakdown": category_breakdown,
            "merchant_breakdown": merchant_breakdown,
            "amount_range_breakdown": amount_range_breakdown,

            # Policy & anomalies
            "daily_spend": daily_spend,
            "policy_flags": policy_flags,
            "policy_flag_count": len(policy_flags),
            "high_value_items": high_value,
            "duplicate_items": duplicates,
            "high_value_count": len(high_value),
            "duplicate_item_count": len(duplicates),

            # Insights
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_expense_report_ui_config(result)
        result["has_advanced_analytics"] = True

        return result

    # ============================================================
    # EXTRACTION
    # ============================================================

    def _extract_expense_report(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use a universal parser; normalize to stable schema.
        """
        try:
            from app.services.parsers.universal_expense_report_parser import UniversalExpenseParser
            parser = UniversalExpenseParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Expense report parsing failed: {e}")
            parsed = {}

        return {
            "employee_name": parsed.get("employee_name"),
            "employee_id": parsed.get("employee_id"),
            "report_id": parsed.get("report_id") or metadata.get("expense_id"),
            "report_period_start": parsed.get("report_period_start"),
            "report_period_end": parsed.get("report_period_end"),
            "status": parsed.get("status", "Submitted"),
            "currency": parsed.get("currency", "USD"),
            "expenses": parsed.get("expenses", []),
        }

    # ============================================================
    # METRICS & POLICY
    # ============================================================

    def _daily_spend(self, items: List[Dict[str, Any]]) -> Dict[str, float]:
        daily = {}
        for item in items or []:
            d = item.get("date")
            dt = self.parse_date(d)
            if not dt:
                continue
            key = dt.date().isoformat()
            daily[key] = daily.get(key, 0.0) + self.safe_float(item.get("line_total", 0))
        return {k: round(v, 2) for k, v in daily.items()}

    def _policy_checks(
        self,
        report: Dict[str, Any],
        items: List[Dict[str, Any]],
        daily_spend: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """
        Very simple policy checks:
        - Daily spend cap (e.g. 500)
        - Category cap (e.g. meals > 50% of total)
        - Missing receipts
        """
        flags: List[Dict[str, Any]] = []

        total = sum(self.safe_float(i.get("line_total", 0)) for i in items)
        if total <= 0:
            return []

        # Daily cap
        DAILY_CAP = 500.0
        for day, amt in daily_spend.items():
            if amt > DAILY_CAP:
                flags.append({
                    "type": "daily_cap_exceeded",
                    "day": day,
                    "amount": amt,
                    "limit": DAILY_CAP,
                    "severity": "medium",
                })

        # Category cap (meals > 50% of total)
        meals_total = sum(
            self.safe_float(i.get("line_total", 0))
            for i in items
            if str(i.get("category", "")).lower() in ["meal", "meals", "food", "restaurant"]
        )
        if meals_total > total * 0.5:
            flags.append({
                "type": "excessive_meals",
                "amount": round(meals_total, 2),
                "share": round(meals_total / total * 100, 2),
                "severity": "medium",
            })

        # Missing receipts
        missing_receipts = [i for i in items if not i.get("has_receipt", True)]
        if missing_receipts:
            flags.append({
                "type": "missing_receipts",
                "count": len(missing_receipts),
                "severity": "high",
            })

        return flags

    # ============================================================
    # SUMMARY & INSIGHTS
    # ============================================================

    def _generate_summary(self, report: Dict[str, Any], aggregates: Dict[str, Any]) -> str:
        employee = report.get("employee_name", "Employee")
        rid = report.get("report_id", "N/A")
        total = aggregates.get("total_amount", 0.0)

        return (
            f"Expense report {rid} for {employee} totals ${total:,.2f} "
            f"for the period {report.get('report_period_start', 'N/A')} "
            f"to {report.get('report_period_end', 'N/A')}."
        )

    def _generate_insights(
        self,
        report: Dict[str, Any],
        aggregates: Dict[str, Any],
        category_breakdown: Dict[str, Any],
        policy_flags: List[Dict[str, Any]],
    ) -> List[str]:
        insights: List[str] = []

        total = aggregates.get("total_amount", 0.0)
        count = aggregates.get("line_item_count", 0)
        insights.append(f"Total reimbursable amount: ${total:,.2f}")
        insights.append(f"Number of expense items: {count}")

        # Top category
        top = category_breakdown.get("top_5", [])
        if top:
            c = top[0]
            insights.append(
                f"Top category: {c['category']} (${c['total']:,.2f}, {c['count']} items)."
            )

        if policy_flags:
            insights.append(f"Policy flags: {len(policy_flags)} issues detected.")

        return insights

    def _generate_recommendations(
        self,
        report: Dict[str, Any],
        aggregates: Dict[str, Any],
        category_breakdown: Dict[str, Any],
        policy_flags: List[Dict[str, Any]],
        high_value: List[Dict[str, Any]],
        duplicates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        total = aggregates.get("total_amount", 0.0)

        # High total
        if total > 2000:
            recs.append({
                "priority": "medium",
                "category": "spend",
                "title": "High Total Expense Report",
                "description": f"This report totals ${total:,.2f}. Consider closer review.",
                "potential_savings": 0,
            })

        # Policy flags
        for flag in policy_flags:
            if flag["type"] == "daily_cap_exceeded":
                recs.append({
                    "priority": "medium",
                    "category": "policy",
                    "title": "Daily Spend Cap Exceeded",
                    "description": (
                        f"On {flag['day']}, expenses totaled ${flag['amount']:,.2f}, "
                        f"exceeding the daily cap of ${flag['limit']:,.2f}."
                    ),
                    "potential_savings": 0,
                })
            elif flag["type"] == "excessive_meals":
                recs.append({
                    "priority": "medium",
                    "category": "policy",
                    "title": "Excessive Meal Spending",
                    "description": (
                        f"Meals account for {flag['share']:.1f}% of the report total "
                        f"(${flag['amount']:,.2f})."
                    ),
                    "potential_savings": 0,
                })
            elif flag["type"] == "missing_receipts":
                recs.append({
                    "priority": "high",
                    "category": "compliance",
                    "title": "Missing Receipts",
                    "description": f"{flag['count']} expense items are missing receipts.",
                    "potential_savings": 0,
                })

        # High value / duplicates
        if high_value:
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "High-Value Expenses",
                "description": f"{len(high_value)} high-value expense items identified.",
                "potential_savings": 0,
            })

        if duplicates:
            recs.append({
                "priority": "medium",
                "category": "quality",
                "title": "Possible Duplicate Expenses",
                "description": f"{len(duplicates)} items appear to be duplicates.",
                "potential_savings": 0,
            })

        return recs

    # ============================================================
    # UI CONFIG (Angular-compatible)
    # ============================================================

    def build_expense_report_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            self.metric_def("total_amount", "Total Amount", True),
            self.metric_def("line_item_count", "Line Items", True),
            self.metric_def("policy_flag_count", "Policy Flags", True),
            self.metric_def("high_value_count", "High-Value Items", True),
        ]

        secondary_metrics = [
            self.metric_def("average_line_total", "Avg Expense"),
            self.metric_def("max_line_total", "Max Expense"),
            self.metric_def("min_line_total", "Min Expense"),
            self.metric_def("total_quantity", "Total Quantity"),
            self.metric_def("duplicate_item_count", "Duplicate Items"),
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
                "merchant_breakdown",
                "Spend by Merchant",
                "merchant_breakdown.all_items",
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
            self.table_def("expenses", "Expenses", "expenses"),
            self.table_def("policy_flags", "Policy Flags", "policy_flags"),
            self.table_def("high_value_items", "High-Value Items", "high_value_items"),
            self.table_def("duplicate_items", "Duplicate Items", "duplicate_items"),
            self.table_def("recommendations", "Recommendations", "recommendations"),
        ]

        sections = [
            self.section_def(
                "summary",
                "Expense Report Summary",
                metrics=["total_amount", "line_item_count", "policy_flag_count", "high_value_count"],
            ),
            self.section_def(
                "expenses",
                "Expenses",
                metrics=["average_line_total", "max_line_total", "min_line_total"],
                charts=["category_breakdown", "merchant_breakdown"],
                tables=["expenses"],
            ),
            self.section_def(
                "risk",
                "Risk & Policy",
                metrics=["policy_flag_count", "duplicate_item_count"],
                charts=["amount_range_breakdown"],
                tables=["policy_flags", "high_value_items", "duplicate_items"],
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
