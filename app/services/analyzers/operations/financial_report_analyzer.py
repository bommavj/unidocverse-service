# app/services/analyzers/operations/financial_report_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class FinancialReportAnalyzer(BaseAnalyzer):
    """
    Analyzer for financial reports (P&L, balance sheet, cash flow):
    - Extracts company, fiscal period, key line items
    - Computes sums, margins, growth, and variance where prior period data exists
    - Flags: negative margins, missing key lines, large one-time items
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📊 Financial report analysis: {file_path}")

        report = self._extract_report(file_path, text, metadata)
        metrics = self._compute_metrics(report)
        variance = self._compute_variance(report, metrics)
        risk_metrics = self._risk_checks(report, metrics, variance)
        insights = self._generate_insights(report, metrics, variance, risk_metrics)
        recommendations = self._generate_recommendations(report, metrics, variance, risk_metrics)

        result = {
            "type": "financial_report",
            "summary": self._generate_summary(report, metrics),
            "confidence": 0.95,

            "company_name": report.get("company_name"),
            "fiscal_period": report.get("fiscal_period"),
            "report_type": report.get("report_type"),

            **metrics,
            **variance,
            **risk_metrics,

            "line_items": report.get("line_items", []),
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_financial_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # Extraction
    def _extract_report(self, file_path, text, metadata) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_financial_report_parser import UniversalFinancialReportParser
            parser = UniversalFinancialReportParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Financial report parsing failed: {e}")
            parsed = {}

        return {
            "company_name": parsed.get("company_name"),
            "fiscal_period": parsed.get("fiscal_period"),
            "report_type": parsed.get("report_type"),
            "line_items": parsed.get("line_items", []),  # [{name, amount, prior_amount, category}]
        }

    # Metrics
    def _compute_metrics(self, report: Dict[str, Any]) -> Dict[str, Any]:
        items = report.get("line_items", [])
        totals = {}
        for it in items:
            cat = it.get("category", "other")
            totals[cat] = totals.get(cat, 0.0) + self.safe_float(it.get("amount", 0.0))
        revenue = totals.get("revenue", 0.0)
        expense = totals.get("expense", 0.0)
        profit = revenue - expense
        margin = round((profit / revenue) * 100, 2) if revenue else None
        return {
            "category_totals": totals,
            "total_revenue": round(revenue, 2),
            "total_expense": round(expense, 2),
            "net_profit": round(profit, 2),
            "profit_margin_percent": margin,
            "line_item_count": len(items),
        }

    def _compute_variance(self, report: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        items = report.get("line_items", [])
        variances = []
        for it in items:
            prior = self.safe_float(it.get("prior_amount", None))
            current = self.safe_float(it.get("amount", 0))
            if prior is not None:
                pct = round(((current - prior) / prior) * 100, 2) if prior != 0 else None
                variances.append({"name": it.get("name"), "current": current, "prior": prior, "pct_change": pct})
        top_changes = sorted([v for v in variances if v.get("pct_change") is not None], key=lambda x: abs(x["pct_change"]), reverse=True)[:10]
        return {"line_item_variances": variances, "top_variances": top_changes}

    # Risk checks
    def _risk_checks(self, report: Dict[str, Any], metrics: Dict[str, Any], variance: Dict[str, Any]) -> Dict[str, Any]:
        risks = []
        if metrics.get("profit_margin_percent") is not None and metrics["profit_margin_percent"] < 0:
            risks.append("Negative profit margin detected.")
        if metrics.get("line_item_count", 0) == 0:
            risks.append("No line items extracted; report may be incomplete.")
        large_one_time = [v for v in variance.get("top_variances", []) if abs(v.get("pct_change", 0)) > 100]
        if large_one_time:
            risks.append(f"{len(large_one_time)} large one-time variances detected.")
        return {"financial_risks": risks, "financial_risk_count": len(risks)}

    # Summary / Insights / Recommendations
    def _generate_summary(self, report: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        comp = report.get("company_name", "Company")
        period = report.get("fiscal_period", "Period")
        return f"{comp} financial report for {period}: revenue ${metrics.get('total_revenue',0):,.2f}."

    def _generate_insights(self, report, metrics, variance, risk_metrics) -> List[str]:
        insights = [
            f"Revenue: ${metrics.get('total_revenue',0):,.2f}",
            f"Net profit: ${metrics.get('net_profit',0):,.2f}",
        ]
        if variance.get("top_variances"):
            insights.append(f"Top variances: {len(variance['top_variances'])}")
        if risk_metrics.get("financial_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['financial_risk_count']}.")
        return insights

    def _generate_recommendations(self, report, metrics, variance, risk_metrics) -> List[Dict[str, Any]]:
        recs = []
        for r in risk_metrics.get("financial_risks", []):
            recs.append({"priority": "high" if "Negative profit" in r else "medium", "category": "finance", "title": "Financial Risk", "description": r, "potential_savings": 0})
        if variance.get("top_variances"):
            recs.append({"priority": "medium", "category": "analysis", "title": "Investigate Variances", "description": "Review top line item changes for one-time items or errors.", "potential_savings": 0})
        return recs

    # UI config
    def build_financial_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero = [
            {"key": "total_revenue", "label": "Revenue", "value_path": "total_revenue", "is_hero": True},
            {"key": "net_profit", "label": "Net Profit", "value_path": "net_profit", "is_hero": True},
            {"key": "profit_margin_percent", "label": "Profit Margin %", "value_path": "profit_margin_percent", "is_hero": True},
            {"key": "financial_risk_count", "label": "Risk Flags", "value_path": "financial_risk_count", "is_hero": True},
        ]
        charts = [
            {"id": "category_totals", "type": "bar", "title": "Category Totals", "data_path": "category_totals", "label_field": "category", "value_field": "amount"}
        ]
        tables = [
            {"id": "line_items", "name": "line_items", "title": "Line Items", "data_path": "line_items"},
            {"id": "top_variances", "name": "top_variances", "title": "Top Variances", "data_path": "top_variances"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]
        sections = [
            {"id": "summary", "title": "Financial Summary", "metrics": ["total_revenue", "net_profit", "profit_margin_percent", "financial_risk_count"], "charts": [], "tables": []},
            {"id": "details", "title": "Line Items & Variances", "metrics": ["line_item_count"], "charts": ["category_totals"], "tables": ["line_items", "top_variances"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["financial_risk_count"], "charts": [], "tables": ["recommendations"]},
        ]
        return {"hero_metrics": hero, "charts": charts, "tables": tables, "sections": sections}
