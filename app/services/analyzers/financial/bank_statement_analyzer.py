"""
Advanced Bank Statement Analyzer
Extracts EVERYTHING: account info, transactions, fees, interest, patterns, anomalies
Provides institutional-grade analytics with 100+ metrics
"""
import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import List, Dict, Any

from app.parsers.extractors.bank_statement_intelligence import BankStatementIntelligence
from .base_financial_analyzer import BaseFinancialAnalyzer

logger = logging.getLogger(__name__)


class BankStatementAnalyzer(BaseFinancialAnalyzer):
    """
    Comprehensive bank statement analysis
    100+ metrics, 20+ charts, institutional-grade insights
    """

    def build_bank_statement_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exhaustive UI config for bank statements, matching the Angular AdvancedAnalyticsComponent contract.
        All value_path / data_path are relative to analyzed_data (flat dict + nested breakdown dicts).
        """

        # -----------------------------
        # HERO + SECONDARY METRICS
        # -----------------------------
        hero_metrics = [
            # Core cashflow
            {"key": "total_credits", "label": "Total Credits", "value_path": "total_credits", "is_hero": True},
            {"key": "total_debits", "label": "Total Debits", "value_path": "total_debits", "is_hero": True},
            {"key": "net_change", "label": "Net Change", "value_path": "net_change", "is_hero": True},
            {"key": "total_transactions", "label": "Total Transactions", "value_path": "total_transactions",
             "is_hero": True},

            # Spending
            {"key": "total_spending", "label": "Total Spending", "value_path": "total_spending", "is_hero": False},
            {"key": "average_spending", "label": "Average Spending", "value_path": "average_spending",
             "is_hero": False},
            {"key": "daily_average_spending", "label": "Daily Avg Spending", "value_path": "daily_average_spending",
             "is_hero": False},
            {"key": "weekly_average_spending", "label": "Weekly Avg Spending", "value_path": "weekly_average_spending",
             "is_hero": False},
            {"key": "monthly_projected_spending", "label": "Projected Monthly Spending",
             "value_path": "monthly_projected_spending", "is_hero": False},
            {"key": "spending_std_deviation", "label": "Spending Std Dev", "value_path": "spending_std_deviation",
             "is_hero": False},
            {"key": "small_purchases", "label": "Small Purchases", "value_path": "small_purchases", "is_hero": False},
            {"key": "medium_purchases", "label": "Medium Purchases", "value_path": "medium_purchases",
             "is_hero": False},
            {"key": "large_purchases", "label": "Large Purchases", "value_path": "large_purchases", "is_hero": False},
            {"key": "small_purchases_total", "label": "Small Purchases Total", "value_path": "small_purchases_total",
             "is_hero": False},
            {"key": "medium_purchases_total", "label": "Medium Purchases Total", "value_path": "medium_purchases_total",
             "is_hero": False},
            {"key": "large_purchases_total", "label": "Large Purchases Total", "value_path": "large_purchases_total",
             "is_hero": False},

            # Income
            {"key": "total_income", "label": "Total Income", "value_path": "total_income", "is_hero": True},
            {"key": "average_income", "label": "Average Income", "value_path": "average_income", "is_hero": False},
            {"key": "income_transaction_count", "label": "Income Transactions",
             "value_path": "income_transaction_count", "is_hero": False},
            {"key": "largest_income", "label": "Largest Income", "value_path": "largest_income", "is_hero": False},
            {"key": "smallest_income", "label": "Smallest Income", "value_path": "smallest_income", "is_hero": False},
            {"key": "income_sources_count", "label": "Income Sources", "value_path": "income_sources_count",
             "is_hero": False},
            {"key": "recurring_income_count", "label": "Recurring Income", "value_path": "recurring_income_count",
             "is_hero": False},
            {"key": "income_std_deviation", "label": "Income Std Dev", "value_path": "income_std_deviation",
             "is_hero": False},

            # Category / channel spending
            {"key": "transfer_spending", "label": "Transfer Spending", "value_path": "transfer_spending",
             "is_hero": False},
            {"key": "transfer_average", "label": "Transfer Avg", "value_path": "transfer_average", "is_hero": False},
            {"key": "other_spending", "label": "Other Spending", "value_path": "other_spending", "is_hero": False},
            {"key": "other_average", "label": "Other Avg", "value_path": "other_average", "is_hero": False},
            {"key": "atm_spending", "label": "ATM Spending", "value_path": "atm_spending", "is_hero": False},
            {"key": "atm_average", "label": "ATM Avg", "value_path": "atm_average", "is_hero": False},
            {"key": "bill_payment_spending", "label": "Bill Payment Spending", "value_path": "bill_payment_spending",
             "is_hero": False},
            {"key": "bill_payment_average", "label": "Bill Payment Avg", "value_path": "bill_payment_average",
             "is_hero": False},
            {"key": "insurance_spending", "label": "Insurance Spending", "value_path": "insurance_spending",
             "is_hero": False},
            {"key": "insurance_average", "label": "Insurance Avg", "value_path": "insurance_average", "is_hero": False},
            {"key": "banking_spending", "label": "Banking Spending", "value_path": "banking_spending",
             "is_hero": False},
            {"key": "utilities_spending", "label": "Utilities Spending", "value_path": "utilities_spending",
             "is_hero": False},
            {"key": "utilities_average", "label": "Utilities Avg", "value_path": "utilities_average", "is_hero": False},
            {"key": "education_spending", "label": "Education Spending", "value_path": "education_spending",
             "is_hero": False},
            {"key": "education_average", "label": "Education Avg", "value_path": "education_average", "is_hero": False},

            # Merchants
            {"key": "unique_merchants_count", "label": "Unique Merchants", "value_path": "unique_merchants_count",
             "is_hero": False},
            {"key": "recurring_merchants_count", "label": "Recurring Merchants",
             "value_path": "recurring_merchants_count", "is_hero": False},
            {"key": "top_merchant_1_name", "label": "Top Merchant 1", "value_path": "top_merchant_1_name",
             "is_hero": False},
            {"key": "top_merchant_1_spending", "label": "Top Merchant 1 Spend", "value_path": "top_merchant_1_spending",
             "is_hero": False},
            {"key": "top_merchant_2_name", "label": "Top Merchant 2", "value_path": "top_merchant_2_name",
             "is_hero": False},
            {"key": "top_merchant_2_spending", "label": "Top Merchant 2 Spend", "value_path": "top_merchant_2_spending",
             "is_hero": False},
            {"key": "top_merchant_3_name", "label": "Top Merchant 3", "value_path": "top_merchant_3_name",
             "is_hero": False},
            {"key": "top_merchant_3_spending", "label": "Top Merchant 3 Spend", "value_path": "top_merchant_3_spending",
             "is_hero": False},
            {"key": "top_merchant_4_name", "label": "Top Merchant 4", "value_path": "top_merchant_4_name",
             "is_hero": False},
            {"key": "top_merchant_4_spending", "label": "Top Merchant 4 Spend", "value_path": "top_merchant_4_spending",
             "is_hero": False},
            {"key": "top_merchant_5_name", "label": "Top Merchant 5", "value_path": "top_merchant_5_name",
             "is_hero": False},
            {"key": "top_merchant_5_spending", "label": "Top Merchant 5 Spend", "value_path": "top_merchant_5_spending",
             "is_hero": False},

            # Temporal
            {"key": "unique_transaction_days", "label": "Unique Transaction Days",
             "value_path": "unique_transaction_days", "is_hero": False},
            {"key": "average_transactions_per_day", "label": "Avg Txns/Day",
             "value_path": "average_transactions_per_day", "is_hero": False},
            {"key": "average_spending_per_day", "label": "Avg Spending/Day", "value_path": "average_spending_per_day",
             "is_hero": False},
            {"key": "average_income_per_day", "label": "Avg Income/Day", "value_path": "average_income_per_day",
             "is_hero": False},
            {"key": "busiest_day_transactions", "label": "Busiest Day (Txns)", "value_path": "busiest_day_transactions",
             "is_hero": False},
            {"key": "slowest_day_transactions", "label": "Slowest Day (Txns)", "value_path": "slowest_day_transactions",
             "is_hero": False},
            {"key": "highest_spending_day", "label": "Highest Spending Day", "value_path": "highest_spending_day",
             "is_hero": False},
            {"key": "lowest_spending_day", "label": "Lowest Spending Day", "value_path": "lowest_spending_day",
             "is_hero": False},
            {"key": "days_with_transactions", "label": "Days With Transactions", "value_path": "days_with_transactions",
             "is_hero": False},
            {"key": "days_without_transactions", "label": "Days Without Transactions",
             "value_path": "days_without_transactions", "is_hero": False},
            {"key": "positive_cashflow_days", "label": "Positive Cashflow Days", "value_path": "positive_cashflow_days",
             "is_hero": False},
            {"key": "negative_cashflow_days", "label": "Negative Cashflow Days", "value_path": "negative_cashflow_days",
             "is_hero": False},
            {"key": "neutral_cashflow_days", "label": "Neutral Cashflow Days", "value_path": "neutral_cashflow_days",
             "is_hero": False},
            {"key": "weekend_transactions", "label": "Weekend Transactions", "value_path": "weekend_transactions",
             "is_hero": False},
            {"key": "weekday_transactions", "label": "Weekday Transactions", "value_path": "weekday_transactions",
             "is_hero": False},

            # Anomalies / fraud / risk
            {"key": "anomaly_count", "label": "Anomaly Count", "value_path": "anomaly_count", "is_hero": False},
            {"key": "anomaly_total", "label": "Anomaly Total", "value_path": "anomaly_total", "is_hero": False},
            {"key": "duplicate_transactions_count", "label": "Duplicate Transactions",
             "value_path": "duplicate_transactions_count", "is_hero": False},
            {"key": "round_amount_count", "label": "Round Amount Count", "value_path": "round_amount_count",
             "is_hero": False},
            {"key": "exact_dollar_count", "label": "Exact Dollar Count", "value_path": "exact_dollar_count",
             "is_hero": False},
            {"key": "fraud_risk_score", "label": "Fraud Risk Score", "value_path": "fraud_risk_score", "is_hero": True},
            {"key": "net_cashflow", "label": "Net Cashflow", "value_path": "net_cashflow", "is_hero": False},
            {"key": "savings_rate", "label": "Savings Rate (%)", "value_path": "savings_rate", "is_hero": True},
            {"key": "financial_health_score", "label": "Financial Health Score", "value_path": "financial_health_score",
             "is_hero": True},
            {"key": "financial_health_status", "label": "Financial Health Status",
             "value_path": "financial_health_status", "is_hero": False},
            {"key": "income_to_expense_ratio", "label": "Income/Expense Ratio", "value_path": "income_to_expense_ratio",
             "is_hero": False},
            {"key": "expense_to_income_ratio", "label": "Expense/Income Ratio", "value_path": "expense_to_income_ratio",
             "is_hero": False},
            {"key": "projected_annual_savings", "label": "Projected Annual Savings",
             "value_path": "projected_annual_savings", "is_hero": False},
            {"key": "projected_monthly_savings", "label": "Projected Monthly Savings",
             "value_path": "projected_monthly_savings", "is_hero": False},

            # Recurring / subscriptions / bills
            {"key": "total_subscriptions", "label": "Total Subscriptions", "value_path": "total_subscriptions",
             "is_hero": False},
            {"key": "subscription_cost", "label": "Monthly Subscription Cost", "value_path": "subscription_cost",
             "is_hero": False},
            {"key": "total_bills", "label": "Total Bills", "value_path": "total_bills", "is_hero": False},

            # Behavioral / stress
            {"key": "impulse_purchases", "label": "Impulse Purchases", "value_path": "impulse_purchases",
             "is_hero": False},
            {"key": "discretionary_spending", "label": "Discretionary Spending", "value_path": "discretionary_spending",
             "is_hero": False},
            {"key": "essential_spending", "label": "Essential Spending", "value_path": "essential_spending",
             "is_hero": False},
        ]

        # -----------------------------
        # TABLES
        # -----------------------------
        tables = [
            {"id": "transactions", "name": "transactions", "title": "Transactions", "data_path": "transactions"},
            {"id": "anomalies", "name": "anomalies", "title": "Anomalies", "data_path": "anomalies"},
            {"id": "fraud_alerts", "name": "fraud_alerts", "title": "Fraud Alerts", "data_path": "fraud_alerts"},
            {"id": "recurring_transactions", "name": "recurring_transactions", "title": "Recurring Transactions",
             "data_path": "recurring_transactions"},
            {"id": "subscriptions", "name": "subscriptions", "title": "Subscriptions", "data_path": "subscriptions"},
            {"id": "bills", "name": "bills", "title": "Bills", "data_path": "bills"},
            {"id": "bill_analysis", "name": "bill_analysis", "title": "Bill Analysis",
             "data_path": "bill_analysis.bills_list"},
            {"id": "behavioral_insights", "name": "behavioral_insights", "title": "Behavioral Insights",
             "data_path": "behavioral_insights"},
            {"id": "spending_patterns", "name": "spending_patterns", "title": "Spending Patterns",
             "data_path": "spending_patterns"},
            {"id": "risk_factors", "name": "risk_factors", "title": "Risk Factors", "data_path": "risk_factors"},
            {"id": "financial_stress_indicators", "name": "financial_stress_indicators",
             "title": "Financial Stress Indicators", "data_path": "financial_stress_indicators"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations",
             "data_path": "recommendations"},
            {"id": "savings_opportunities", "name": "savings_opportunities", "title": "Savings Opportunities",
             "data_path": "savings_opportunities"},
            {"id": "predictions", "name": "predictions", "title": "Predictions", "data_path": "predictions"},
            {"id": "projected_end_of_month", "name": "projected_end_of_month", "title": "End-of-Month Projection",
             "data_path": "projected_end_of_month"},
            {"id": "category_comparison", "name": "category_comparison", "title": "Category Comparison",
             "data_path": "category_comparison"},
            {"id": "period_over_period", "name": "period_over_period", "title": "Period Over Period",
             "data_path": "period_over_period"},
        ]

        # -----------------------------
        # CHARTS (using breakdown dicts)
        # -----------------------------
        charts = [
            {
                "id": "category_breakdown",
                "type": "pie",
                "title": "Spending by Category",
                "data_path": "category_breakdown.all_items",
                "label_field": "category",
                "value_field": "total"
            },
            {
                "id": "merchant_breakdown",
                "type": "bar",
                "title": "Spending by Merchant",
                "data_path": "merchant_breakdown.all_items",
                "label_field": "category",
                "value_field": "total"
            },
            {
                "id": "type_breakdown",
                "type": "bar",
                "title": "Income vs Expenses",
                "data_path": "type_breakdown.all_items",
                "label_field": "category",
                "value_field": "total"
            },
            {
                "id": "daily_breakdown",
                "type": "line",
                "title": "Daily Spending",
                "data_path": "daily_breakdown.all_times",
                "x_field": "key",
                "y_field": "value"
            },
            {
                "id": "weekly_breakdown",
                "type": "bar",
                "title": "Weekly Spending",
                "data_path": "weekly_breakdown.all_items",
                "label_field": "category",
                "value_field": "total"
            },
            {
                "id": "dayofweek_breakdown",
                "type": "bar",
                "title": "Day-of-Week Spending",
                "data_path": "dayofweek_breakdown.all_items",
                "label_field": "category",
                "value_field": "total"
            },
            {
                "id": "amount_range_breakdown",
                "type": "bar",
                "title": "Amount Range Breakdown",
                "data_path": "amount_range_breakdown.all_items",
                "label_field": "category",
                "value_field": "total"
            },
            {
                "id": "anomalies_breakdown",
                "type": "bar",
                "title": "Anomaly Severity",
                "data_path": "anomalies_breakdown.top_5",
                "label_field": "category",
                "value_field": "count"
            },
            {
                "id": "recurring_breakdown",
                "type": "bar",
                "title": "Top Recurring Merchants",
                "data_path": "recurring_breakdown.top_5",
                "label_field": "category",
                "value_field": "total"
            }
        ]

        # -----------------------------
        # SECTIONS (grouping for UI)
        # -----------------------------
        sections = [
            {
                "id": "summary",
                "title": "Summary",
                "metrics": ["total_credits", "total_debits", "net_change", "total_transactions"],
                "charts": [],
                "tables": []
            },
            {
                "id": "spending",
                "title": "Spending Overview",
                "metrics": ["total_spending", "average_spending", "daily_average_spending",
                            "monthly_projected_spending"],
                "charts": ["category_breakdown", "daily_breakdown", "amount_range_breakdown"],
                "tables": ["transactions"]
            },
            {
                "id": "income",
                "title": "Income Overview",
                "metrics": ["total_income", "average_income", "income_sources_count", "recurring_income_count"],
                "charts": ["type_breakdown"],
                "tables": []
            },
            {
                "id": "merchants",
                "title": "Merchants",
                "metrics": ["unique_merchants_count", "recurring_merchants_count", "top_merchant_1_spending"],
                "charts": ["merchant_breakdown"],
                "tables": []
            },
            {
                "id": "recurring",
                "title": "Recurring & Subscriptions",
                "metrics": ["total_subscriptions", "subscription_cost", "total_bills"],
                "charts": ["recurring_breakdown"],
                "tables": ["recurring_transactions", "subscriptions", "bills"]
            },
            {
                "id": "risk",
                "title": "Risk & Anomalies",
                "metrics": ["anomaly_count", "anomaly_total", "fraud_risk_score"],
                "charts": ["anomalies_breakdown"],
                "tables": ["anomalies", "fraud_alerts"]
            },
            {
                "id": "behavior",
                "title": "Behavior & Stress",
                "metrics": ["savings_rate", "financial_health_score", "financial_health_status"],
                "charts": ["dayofweek_breakdown"],
                "tables": ["behavioral_insights", "spending_patterns", "risk_factors", "financial_stress_indicators"]
            },
            {
                "id": "projections",
                "title": "Projections & Comparisons",
                "metrics": ["projected_annual_savings", "projected_monthly_savings"],
                "charts": ["weekly_breakdown"],
                "tables": ["predictions", "projected_end_of_month", "category_comparison", "period_over_period"]
            },
            {
                "id": "recommendations",
                "title": "Recommendations",
                "metrics": [],
                "charts": [],
                "tables": ["recommendations", "savings_opportunities"]
            },
            {
                "id": "all_transactions",
                "title": "All Transactions",
                "metrics": [],
                "charts": [],
                "tables": ["transactions"]
            }
        ]

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections
        }

    def _normalize_for_ui(self, result: dict) -> None:
        """
        Mutates result in-place — normalizes all table-bound fields into
        list[{"key": str, "value": str}] so Angular's generic table renderer works.

        Call this AFTER all metrics are built, BEFORE building ui_config.
        """

        def dict_to_rows(d: dict) -> list:
            """Flatten a dict into [{key, value}] rows, recursing one level for nested dicts."""
            rows = []
            for k, v in d.items():
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        rows.append({"key": f"{k} › {k2}", "value": str(v2)})
                else:
                    rows.append({"key": k.replace("_", " ").title(), "value": str(v)})
            return rows

        def strings_to_rows(lst: list) -> list:
            """Convert list-of-strings into [{key, value}] rows."""
            return [{"key": str(i + 1), "value": str(item)} for i, item in enumerate(lst)]

        # ── spending_patterns ──────────────────────────────────────────────
        sp = result.get("spending_patterns")
        if isinstance(sp, dict):
            rows = []
            rows.append({"key": "Category Trends", "value": sp.get("category_trends", "")})
            rows.append({"key": "Morning vs Evening", "value": sp.get("morning_vs_evening", "")})
            wvw = sp.get("weekend_vs_weekday", {})
            if isinstance(wvw, dict):
                rows.append({"key": "Weekday Spending", "value": f"${wvw.get('weekday_spending', 0):,.2f}"})
                rows.append({"key": "Weekend Spending", "value": f"${wvw.get('weekend_spending', 0):,.2f}"})
                rows.append({"key": "Weekend %", "value": f"{wvw.get('weekend_percentage', 0):.1f}%"})
            result["spending_patterns"] = rows

        # ── behavioral_insights ───────────────────────────────────────────
        bi = result.get("behavioral_insights")
        if isinstance(bi, dict):
            result["behavioral_insights"] = dict_to_rows(bi)
        elif isinstance(bi, list) and bi and isinstance(bi[0], str):
            result["behavioral_insights"] = strings_to_rows(bi)

        # ── risk_factors ──────────────────────────────────────────────────
        rf = result.get("risk_factors")
        if isinstance(rf, list) and rf and isinstance(rf[0], str):
            result["risk_factors"] = strings_to_rows(rf)
        elif isinstance(rf, dict):
            result["risk_factors"] = dict_to_rows(rf)

        # ── financial_stress_indicators ───────────────────────────────────
        fsi = result.get("financial_stress_indicators")
        if isinstance(fsi, list) and (not fsi or isinstance(fsi[0], str)):
            result["financial_stress_indicators"] = strings_to_rows(fsi) if fsi else []
        elif isinstance(fsi, dict):
            result["financial_stress_indicators"] = dict_to_rows(fsi)

        # ── recommendations ───────────────────────────────────────────────
        rec = result.get("recommendations")
        if isinstance(rec, list) and (not rec or isinstance(rec[0], str)):
            result["recommendations"] = strings_to_rows(rec) if rec else []
        # if already list[dict], leave as-is

        # ── predictions ───────────────────────────────────────────────────
        pred = result.get("predictions")
        if isinstance(pred, dict):
            rows = []
            label_map = {
                "projected_monthly_spending": "Projected Monthly Spending",
                "projected_annual_spending": "Projected Annual Spending",
                "days_until_paycheck": "Days Until Paycheck",
                "confidence": "Confidence",
            }
            for k, label in label_map.items():
                v = pred.get(k)
                if v is not None:
                    if isinstance(v, float) and k.endswith("spending"):
                        v = f"${v:,.2f}"
                    rows.append({"key": label, "value": str(v)})
            result["predictions"] = rows

        # ── projected_end_of_month ────────────────────────────────────────
        eom = result.get("projected_end_of_month")
        if isinstance(eom, dict):
            rows = [
                {"key": "Current Position", "value": f"${eom.get('current_position', 0):,.2f}"},
                {"key": "Projected Position", "value": f"${eom.get('projected_position', 0):,.2f}"},
                {"key": "Days Remaining", "value": str(eom.get('days_remaining', ''))},
                {"key": "Recommendation", "value": str(eom.get('recommendation', ''))},
            ]
            result["projected_end_of_month"] = rows

        # ── category_comparison ───────────────────────────────────────────
        cc = result.get("category_comparison")
        if isinstance(cc, dict):
            rows = [
                {"key": "Highest Category", "value": str(cc.get('highest', ''))},
                {"key": "Highest Amount", "value": f"${cc.get('highest_amount', 0):,.2f}"},
                {"key": "Second Highest", "value": str(cc.get('second_highest', ''))},
                {"key": "Second Amount", "value": f"${cc.get('second_amount', 0):,.2f}"},
                {"key": "Difference", "value": f"${cc.get('difference', 0):,.2f}"},
            ]
            result["category_comparison"] = rows

        # ── period_over_period ────────────────────────────────────────────
        pop = result.get("period_over_period")
        if isinstance(pop, dict):
            rows = [
                {"key": "First Half Spending", "value": f"${pop.get('first_half_spending', 0):,.2f}"},
                {"key": "Second Half Spending", "value": f"${pop.get('second_half_spending', 0):,.2f}"},
                {"key": "Change", "value": f"${pop.get('change', 0):,.2f}"},
                {"key": "Change %", "value": f"{pop.get('change_percentage', 0):.1f}%"},
            ]
            result["period_over_period"] = rows



    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Complete analysis pipeline with full intelligence"""
        logger.info(f"🏦 Advanced bank statement analysis: {file_path}")

        transactions = self._extract_transactions(file_path)

        if not transactions:
            logger.warning("⚠️ No transactions found")
            return self._empty_result()

        logger.info(f"✅ Extracted {len(transactions)} transactions")

        # Categorize & enhance
        transactions = self._categorize_transactions(transactions)
        transactions = self._detect_patterns(transactions)

        # ✅ Detect subscriptions and bills ONCE
        subscriptions = self._detect_subscriptions(transactions)
        bills = self._detect_bills(transactions)

        result = {
            'type': 'bank_statement',
            'summary': f"Bank statement analysis: {len(transactions)} transactions processed",
            'confidence': 0.95,

            # All existing metrics...
            **self._transaction_metrics(transactions),
            **self._spending_metrics(transactions),
            **self._income_metrics(transactions),
            **self._category_metrics(transactions),
            **self._merchant_metrics(transactions),
            **self._temporal_metrics(transactions),
            **self._pattern_metrics(transactions),
            **self._financial_health_metrics(transactions),

            # All existing breakdowns...
            'category_breakdown': self._category_breakdown(transactions),
            'merchant_breakdown': self._merchant_breakdown(transactions),
            'type_breakdown': self._type_breakdown(transactions),
            'daily_breakdown': self._daily_breakdown(transactions),
            'weekly_breakdown': self._weekly_breakdown(transactions),
            'dayofweek_breakdown': self._dayofweek_breakdown(transactions),
            'amount_range_breakdown': self._amount_range_breakdown(transactions),

            # ========================================
            # INTELLIGENCE LAYERS
            # ========================================

            # Anomaly Detection
            'anomalies': self._detect_anomalies(transactions),
            'anomalies_breakdown': self._anomalies_breakdown(transactions),

            # Fraud Detection
            'fraud_alerts': self._detect_fraud(transactions),
            'fraud_risk_score': self._calculate_fraud_risk(transactions),

            # Recurring Patterns
            'recurring_transactions': self._detect_recurring_transactions(transactions),
            'recurring_breakdown': self._recurring_breakdown(transactions),

            # Subscriptions
            'subscriptions': subscriptions,
            'subscription_cost': round(sum(s.get('monthly_cost', 0) for s in subscriptions), 2),  # ✅ FIX
            'total_subscriptions': len(subscriptions),

            # Bills
            'bills': bills,
            'bill_analysis': self._analyze_bills(transactions),
            'total_bills': len(bills),

            # Recommendations
            'recommendations': self._generate_recommendations(transactions),
            'savings_opportunities': self._find_savings_opportunities(transactions),

            # Warnings & Alerts
            'warnings': self._generate_warnings(transactions),
            'alerts': self._generate_alerts(transactions),

            # Behavioral Insights
            'behavioral_insights': self._analyze_behavior(transactions),
            'spending_patterns': self._analyze_spending_patterns(transactions),

            # Predictions
            'predictions': self._generate_predictions(transactions),
            'projected_end_of_month': self._project_end_of_month(transactions),

            # Comparisons
            'category_comparison': self._compare_categories(transactions),
            'period_over_period': self._period_comparison(transactions),

            # Goals & Benchmarks
            'budget_analysis': self._analyze_budget(transactions),
            'benchmark_comparison': self._benchmark_against_averages(transactions),

            # Risk Analysis
            'risk_factors': self._identify_risk_factors(transactions),
            'financial_stress_indicators': self._detect_stress_indicators(transactions),

            # Insights
            'insights': self._generate_comprehensive_insights(transactions),
            'key_findings': self._extract_key_findings(transactions),

            'transactions': transactions[:100],
            'all_transactions_count': len(transactions)
        }

        #result = self.build_bank_statement_ui_schema(result)
        self._normalize_for_ui(result)  # ← add this line
        result["ui_config"] = self.build_bank_statement_ui_config(result)
        result["has_advanced_analytics"] = True
        result["intelligence"] = BankStatementIntelligence().analyze(result).to_dict()

        logger.info(f"✅ Generated comprehensive analytics with intelligence layers")

        return result


    # ========================================
    # EXTRACTION
    # ========================================

    # ========================================
    # ANOMALY DETECTION
    # ========================================

    def _detect_patterns(self, transactions: List[Dict]) -> List[Dict]:
        """Mark transactions with patterns"""
        # Calculate averages for anomaly detection
        debits = [t['debit'] for t in transactions if t['debit'] > 0]
        avg_debit = sum(debits) / len(debits) if debits else 0

        # Mark anomalies
        for t in transactions:
            if t['debit'] > avg_debit * 3:
                t['is_anomaly'] = True
                t['anomaly_reason'] = f'{round(t["debit"] / avg_debit, 1)}x above average'

        # Detect recurring
        merchant_freq = defaultdict(int)
        for t in transactions:
            merchant = t.get('merchant', t['description'][:30])
            merchant_freq[merchant] += 1

        for t in transactions:
            merchant = t.get('merchant', t['description'][:30])
            if merchant_freq[merchant] >= 2:
                t['is_recurring'] = True

        return transactions

    def _detect_anomalies(self, transactions: List[Dict]) -> List[Dict]:
        """Detect anomalous transactions"""
        anomalies = []

        debits = [t['debit'] for t in transactions if t['debit'] > 0]
        if not debits:
            return []

        avg = sum(debits) / len(debits)
        std = self._std_dev(debits)

        for t in transactions:
            if t['debit'] > 0:
                z_score = (t['debit'] - avg) / std if std > 0 else 0

                if abs(z_score) > 2:  # 2 standard deviations
                    severity = 'critical' if abs(z_score) > 3 else 'high' if abs(z_score) > 2.5 else 'medium'

                    anomalies.append({
                        'date': t['date'],
                        'description': t['description'],
                        'amount': t['debit'],
                        'expected_range': f"${avg - std:.2f} - ${avg + std:.2f}",
                        'deviation': round(abs(z_score), 2),
                        'severity': severity,
                        'reason': f'{round(t["debit"] / avg, 1)}x above average'
                    })

        return sorted(anomalies, key=lambda x: x['amount'], reverse=True)[:10]

    def _anomalies_breakdown(self, transactions):
        anomalies = self._detect_anomalies(transactions)
        from collections import defaultdict

        by_severity = defaultdict(lambda: {'count': 0, 'total': 0})
        for a in anomalies:
            by_severity[a['severity']]['count'] += 1
            by_severity[a['severity']]['total'] += a['amount']

        all_items = [
            {'category': sev.title(), 'total': round(data['total'], 2), 'count': data['count']}
            for sev, data in by_severity.items()
        ]

        return {
            'available': True,
            'dimension': 'Severity',
            'value_field': 'count',
            'total_categories': len(by_severity),
            'top_5': all_items,
            'all_items': all_items,
        }

    # ========================================
    # FRAUD DETECTION
    # ========================================

    def _detect_fraud(self, transactions: List[Dict]) -> List[Dict]:
        """Detect potential fraud indicators"""
        alerts = []

        # Check for duplicate transactions same day
        by_date_desc = defaultdict(list)
        for t in transactions:
            key = f"{t['date']}:{t['description'][:20]}"
            by_date_desc[key].append(t)

        for key, txns in by_date_desc.items():
            if len(txns) > 1:
                amounts = [t['amount'] for t in txns]
                if len(set(amounts)) == 1:
                    alerts.append({
                        'type': 'Duplicate Transaction',
                        'date': txns[0]['date'],
                        'description': txns[0]['description'],
                        'amount': txns[0]['amount'],
                        'count': len(txns),
                        'severity': 'high',
                        'action': 'Review with bank'
                    })

        # Check for round amounts (often fraud)
        for t in transactions:
            if t['debit'] > 500 and t['debit'] % 100 == 0:
                alerts.append({
                    'type': 'Round Amount',
                    'date': t['date'],
                    'description': t['description'],
                    'amount': t['debit'],
                    'severity': 'low',
                    'action': 'Verify transaction'
                })

        # Check for unusual location patterns (if available)
        # Check for after-hours transactions

        return alerts[:10]

    def _calculate_fraud_risk(self, transactions: List[Dict]) -> int:
        """Calculate fraud risk score 0-100"""
        alerts = self._detect_fraud(transactions)
        anomalies = self._detect_anomalies(transactions)

        score = 0
        score += len(alerts) * 10
        score += len([a for a in alerts if a['severity'] == 'high']) * 20
        score += len(anomalies) * 5

        return min(score, 100)

    # ========================================
    # RECURRING & SUBSCRIPTIONS
    # ========================================

    def _detect_recurring_transactions(self, transactions: List[Dict]) -> List[Dict]:
        """Detect recurring transactions"""
        recurring = []

        merchant_txns = defaultdict(list)
        for t in transactions:
            merchant = t.get('merchant', t['description'][:30])
            merchant_txns[merchant].append(t)

        for merchant, txns in merchant_txns.items():
            if len(txns) >= 2:
                amounts = [t['amount'] for t in txns]
                avg_amount = sum(amounts) / len(amounts)

                # Check if amounts are similar (within 20%)
                similar = all(abs(a - avg_amount) / avg_amount < 0.2 for a in amounts if avg_amount > 0)

                if similar:
                    recurring.append({
                        'merchant': merchant,
                        'frequency': len(txns),
                        'average_amount': round(avg_amount, 2),
                        'total': round(sum(amounts), 2),
                        'type': 'debit' if txns[0]['debit'] > 0 else 'credit',
                        'dates': [t['date'] for t in txns],
                        'pattern': 'Monthly' if len(txns) >= 2 else 'Irregular'
                    })

        return sorted(recurring, key=lambda x: x['total'], reverse=True)[:20]

    def _recurring_breakdown(self, transactions):
        recurring = self._detect_recurring_transactions(transactions)

        all_items = [
            {'category': r['merchant'], 'total': r['total'], 'count': r['frequency']}
            for r in recurring
        ]

        return {
            'available': True,
            'dimension': 'Merchant',
            'value_field': 'total',
            'total_categories': len(recurring),
            'top_5': all_items[:5],
            'all_items': all_items,
        }


    def _detect_subscriptions(self, transactions: List[Dict]) -> List[Dict]:
        """Detect subscription services"""
        recurring = self._detect_recurring_transactions(transactions)

        subscription_keywords = [
            'netflix', 'spotify', 'amazon prime', 'hulu', 'disney',
            'youtube', 'apple', 'google', 'microsoft', 'adobe',
            'gym', 'fitness', 'membership', 'subscription'
        ]

        subscriptions = []
        for r in recurring:
            if r['type'] == 'debit' and r['average_amount'] < 100:
                # Check if it's a subscription service
                merchant_lower = r['merchant'].lower()
                is_subscription = any(kw in merchant_lower for kw in subscription_keywords)

                if is_subscription or r['pattern'] == 'Monthly':
                    subscriptions.append({
                        'service': r['merchant'],
                        'monthly_cost': r['average_amount'],
                        'annual_cost': round(r['average_amount'] * 12, 2),
                        'last_charge': r['dates'][-1] if r['dates'] else '',
                        'frequency': r['frequency']
                    })

        return subscriptions

    def _detect_bills(self, transactions: List[Dict]) -> List[Dict]:
        """Detect recurring bills"""
        recurring = self._detect_recurring_transactions(transactions)

        bill_keywords = [
            'electric', 'gas', 'water', 'internet', 'cable',
            'phone', 'utility', 'insurance', 'mortgage', 'rent',
            'loan', 'payment', 'bill pay'
        ]

        bills = []
        for r in recurring:
            if r['type'] == 'debit':
                merchant_lower = r['merchant'].lower()
                is_bill = any(kw in merchant_lower for kw in bill_keywords)

                if is_bill:
                    bills.append({
                        'bill_type': r['merchant'],
                        'average_amount': r['average_amount'],
                        'total_paid': r['total'],
                        'frequency': r['frequency'],
                        'dates': r['dates']
                    })

        return bills

    def _analyze_bills(self, transactions: List[Dict]) -> Dict:
        """Analyze bill payments"""
        bills = self._detect_bills(transactions)

        if not bills:
            return {}

        return {
            'total_bills': len(bills),
            'monthly_bill_total': round(sum(b['average_amount'] for b in bills), 2),
            'annual_bill_total': round(sum(b['average_amount'] for b in bills) * 12, 2),
            'largest_bill': max((b['average_amount'] for b in bills), default=0),
            'bills_list': bills
        }

    # ========================================
    # RECOMMENDATIONS
    # ========================================

    def _generate_recommendations(self, transactions: List[Dict]) -> List[Dict]:
        """Generate actionable recommendations"""
        recommendations = []
        priority = 1

        # Check subscriptions
        subscriptions = self._detect_subscriptions(transactions)
        if len(subscriptions) > 5:
            total = sum(s['monthly_cost'] for s in subscriptions)
            recommendations.append({
                'priority': 'high',
                'category': 'subscriptions',
                'title': 'Review Subscriptions',
                'description': f"You have {len(subscriptions)} subscriptions costing ${total:.2f}/month. Review and cancel unused ones.",
                'potential_savings': round(total * 0.3 * 12, 2),
                'action': 'Review all subscriptions'
            })
            priority += 1

        # Check fees
        fee_keywords = ['fee', 'charge', 'overdraft', 'atm']
        fees = [t for t in transactions if any(kw in t['description'].lower() for kw in fee_keywords)]
        if fees:
            total_fees = sum(t['amount'] for t in fees)
            if total_fees > 20:
                recommendations.append({
                    'priority': 'high',
                    'category': 'fees',
                    'title': 'Reduce Banking Fees',
                    'description': f"Paid ${total_fees:.2f} in fees. Switch to a no-fee account or maintain minimum balance.",
                    'potential_savings': round(total_fees * 12, 2),
                    'action': 'Contact bank about fee-free options'
                })
                priority += 1

        # Check savings rate
        metrics = self._transaction_metrics(transactions)
        net = metrics.get('net_change', 0)
        income = metrics.get('total_credits', 1)
        savings_rate = (net / income * 100) if income > 0 else 0

        if savings_rate < 10:
            recommendations.append({
                'priority': 'high',
                'category': 'savings',
                'title': 'Increase Savings Rate',
                'description': f"Current savings rate is {savings_rate:.1f}%. Aim for at least 20% to build financial security.",
                'potential_savings': round(income * 0.2 - net, 2),
                'action': 'Set up automatic savings transfer'
            })
            priority += 1

        # Check dining spending
        dining = [t for t in transactions if t.get('category') == 'Dining' and t['debit'] > 0]
        if len(dining) > 20:
            total_dining = sum(t['debit'] for t in dining)
            recommendations.append({
                'priority': 'medium',
                'category': 'spending',
                'title': 'Reduce Dining Out',
                'description': f"Spent ${total_dining:.2f} on {len(dining)} dining transactions. Cooking at home could save 50%.",
                'potential_savings': round(total_dining * 0.5, 2),
                'action': 'Meal prep and limit dining to weekends'
            })
            priority += 1

        # Check anomalies
        anomalies = self._detect_anomalies(transactions)
        if len(anomalies) > 3:
            recommendations.append({
                'priority': 'medium',
                'category': 'spending',
                'title': 'Review Large Purchases',
                'description': f"Found {len(anomalies)} unusually large transactions. Ensure they were intentional.",
                'potential_savings': 0,
                'action': 'Review anomalous transactions'
            })

        return sorted(recommendations, key=lambda x: {'high': 0, 'medium': 1, 'low': 2}[x['priority']])

    def _find_savings_opportunities(self, transactions: List[Dict]) -> List[Dict]:
        """Find specific savings opportunities"""
        opportunities = []

        # Subscription optimization
        subscriptions = self._detect_subscriptions(transactions)
        if subscriptions:
            unused = [s for s in subscriptions if s['monthly_cost'] > 10]
            if unused:
                opportunities.append({
                    'type': 'Subscriptions',
                    'description': f'Review {len(unused)} subscriptions',
                    'monthly_savings': round(sum(s['monthly_cost'] for s in unused) * 0.3, 2),
                    'annual_savings': round(sum(s['monthly_cost'] for s in unused) * 0.3 * 12, 2)
                })

        # Category optimization
        spending = self._spending_metrics(transactions)
        if spending.get('total_spending', 0) > 0:
            # Dining
            dining_total = sum(t['debit'] for t in transactions if t.get('category') == 'Dining')
            if dining_total > 200:
                opportunities.append({
                    'type': 'Dining',
                    'description': 'Cook more meals at home',
                    'monthly_savings': round(dining_total * 0.3, 2),
                    'annual_savings': round(dining_total * 0.3 * 12, 2)
                })

        return opportunities

    # ========================================
    # WARNINGS & ALERTS
    # ========================================

    def _generate_warnings(self, transactions: List[Dict]) -> List[Dict]:
        """Generate warnings"""
        warnings = []

        # Low balance warning
        # Overspending warning
        metrics = self._transaction_metrics(transactions)
        if metrics.get('net_change', 0) < -1000:
            warnings.append({
                'type': 'Overspending',
                'severity': 'high',
                'message': f"Spending ${abs(metrics['net_change']):.2f} more than earning",
                'action': 'Review budget immediately'
            })

        # High fee warning
        fee_keywords = ['fee', 'charge']
        fees = [t for t in transactions if any(kw in t['description'].lower() for kw in fee_keywords)]
        total_fees = sum(t['amount'] for t in fees)
        if total_fees > 50:
            warnings.append({
                'type': 'Fees',
                'severity': 'medium',
                'message': f"Paid ${total_fees:.2f} in fees this period",
                'action': 'Consider fee-free banking'
            })

        return warnings

    def _generate_alerts(self, transactions: List[Dict]) -> List[str]:
        """Generate alert messages"""
        alerts = []

        anomalies = self._detect_anomalies(transactions)
        if anomalies:
            alerts.append(f"⚠️ {len(anomalies)} unusual transactions detected")

        fraud = self._detect_fraud(transactions)
        if fraud:
            alerts.append(f"🚨 {len(fraud)} potential fraud indicators found")

        metrics = self._transaction_metrics(transactions)
        if metrics.get('net_change', 0) < 0:
            alerts.append(f"💸 Negative cash flow: ${abs(metrics['net_change']):.2f}")

        return alerts

    # ========================================
    # BEHAVIORAL INSIGHTS
    # ========================================

    def _analyze_behavior(self, transactions: List[Dict]) -> Dict:
        """Analyze spending behavior"""
        return {
            'most_active_day': self._find_most_active_day(transactions),
            'preferred_payment_method': self._detect_payment_method(transactions),
            'spending_habits': self._classify_spending_habits(transactions),
            'impulse_purchases': self._detect_impulse_purchases(transactions)
        }

    def _find_most_active_day(self, transactions: List[Dict]) -> str:
        """Find most active transaction day"""
        by_date = defaultdict(int)
        for t in transactions:
            by_date[t['date']] += 1

        if not by_date:
            return 'N/A'

        most_active = max(by_date.items(), key=lambda x: x[1])
        return f"{most_active[0]} ({most_active[1]} transactions)"

    def _detect_payment_method(self, transactions: List[Dict]) -> str:
        """Detect preferred payment method"""
        # Simple heuristic
        return "Debit Card" if len(transactions) > 50 else "Mixed"

    def _classify_spending_habits(self, transactions: List[Dict]) -> str:
        """Classify spending habits"""
        spending = self._spending_metrics(transactions)
        avg = spending.get('average_spending', 0)

        if avg > 100:
            return "High spender"
        elif avg > 50:
            return "Moderate spender"
        else:
            return "Conservative spender"

    def _detect_impulse_purchases(self, transactions: List[Dict]) -> int:
        """Detect impulse purchases"""
        # Small purchases (< $50) at shopping/entertainment
        impulse = [
            t for t in transactions
            if t['debit'] > 0 and t['debit'] < 50
               and t.get('category') in ['Shopping', 'Entertainment', 'Dining']
        ]
        return len(impulse)

    def _analyze_spending_patterns(self, transactions: List[Dict]) -> Dict:
        """Analyze spending patterns"""
        return {
            'weekend_vs_weekday': self._weekend_vs_weekday_spending(transactions),
            'morning_vs_evening': 'Analysis pending',  # Would need timestamps
            'category_trends': self._category_trends(transactions)
        }

    def _weekend_vs_weekday_spending(self, transactions: List[Dict]) -> Dict:
        """Compare weekend vs weekday spending"""
        weekend = sum(t['debit'] for t in transactions if self._is_weekend(t['date']) and t['debit'] > 0)
        weekday = sum(t['debit'] for t in transactions if not self._is_weekend(t['date']) and t['debit'] > 0)

        return {
            'weekend_spending': round(weekend, 2),
            'weekday_spending': round(weekday, 2),
            'weekend_percentage': round(weekend / (weekend + weekday) * 100, 2) if (weekend + weekday) > 0 else 0
        }

    def _category_trends(self, transactions: List[Dict]) -> str:
        """Analyze category trends"""
        categories = self._category_breakdown(transactions)
        if categories['top_5']:
            top = categories['top_5'][0]
            return f"{top['category']} is your top spending category"
        return "No clear trend"

    # ========================================
    # PREDICTIONS
    # ========================================

    def _generate_predictions(self, transactions: List[Dict]) -> Dict:
        """Generate predictions"""
        spending = self._spending_metrics(transactions)
        days = len(set(t['date'] for t in transactions))

        daily_avg = spending.get('daily_average_spending', 0)

        return {
            'projected_monthly_spending': round(daily_avg * 30, 2),
            'projected_annual_spending': round(daily_avg * 365, 2),
            'days_until_paycheck': 'Unknown',  # Would need income dates
            'confidence': 'Medium'
        }

    def _project_end_of_month(self, transactions: List[Dict]) -> Dict:
        """Project end of month balance"""
        metrics = self._transaction_metrics(transactions)
        spending = self._spending_metrics(transactions)

        current_net = metrics.get('net_change', 0)
        daily_avg_spending = spending.get('daily_average_spending', 0)
        days_remaining = 15  # Estimate

        projected = current_net - (daily_avg_spending * days_remaining)

        return {
            'current_position': round(current_net, 2),
            'projected_position': round(projected, 2),
            'days_remaining': days_remaining,
            'recommendation': 'Increase savings' if projected < 0 else 'On track'
        }

    # ========================================
    # COMPARISONS
    # ========================================

    def _compare_categories(self, transactions: List[Dict]) -> Dict:
        """Compare spending across categories"""
        categories = self._category_breakdown(transactions)

        if not categories['top_5'] or len(categories['top_5']) < 2:
            return {}

        top1 = categories['top_5'][0]
        top2 = categories['top_5'][1] if len(categories['top_5']) > 1 else {'category': 'N/A', 'total': 0}

        return {
            'highest': top1['category'],
            'highest_amount': top1['total'],
            'second_highest': top2['category'],
            'second_amount': top2['total'],
            'difference': round(top1['total'] - top2['total'], 2)
        }

    def _period_comparison(self, transactions: List[Dict]) -> Dict:
        """Compare first half vs second half"""
        sorted_txns = sorted(transactions, key=lambda x: x['date'])
        mid = len(sorted_txns) // 2

        first_half = sorted_txns[:mid]
        second_half = sorted_txns[mid:]

        first_spending = sum(t['debit'] for t in first_half)
        second_spending = sum(t['debit'] for t in second_half)

        return {
            'first_half_spending': round(first_spending, 2),
            'second_half_spending': round(second_spending, 2),
            'change': round(second_spending - first_spending, 2),
            'change_percentage': round((second_spending - first_spending) / first_spending * 100,
                                       2) if first_spending > 0 else 0
        }

    # ========================================
    # GOALS & BENCHMARKS
    # ========================================

    def _analyze_budget(self, transactions: List[Dict]) -> Dict:
        """Analyze against typical budgets"""
        spending = self._spending_metrics(transactions)
        income = self._income_metrics(transactions)

        total_spending = spending.get('total_spending', 0)
        total_income = income.get('total_income', 1)

        # 50/30/20 rule
        needs_budget = total_income * 0.5
        wants_budget = total_income * 0.3
        savings_budget = total_income * 0.2

        return {
            'recommended_needs': round(needs_budget, 2),
            'recommended_wants': round(wants_budget, 2),
            'recommended_savings': round(savings_budget, 2),
            'actual_spending': round(total_spending, 2),
            'over_under': round(total_spending - (needs_budget + wants_budget), 2)
        }

    def _benchmark_against_averages(self, transactions: List[Dict]) -> Dict:
        """Benchmark against national averages"""
        spending = self._spending_metrics(transactions)

        # National average monthly spending: ~$5000
        national_avg = 5000
        your_spending = spending.get('total_spending', 0)

        return {
            'your_spending': round(your_spending, 2),
            'national_average': national_avg,
            'comparison': 'Below average' if your_spending < national_avg else 'Above average',
            'difference': round(abs(your_spending - national_avg), 2)
        }

    # ========================================
    # RISK ANALYSIS
    # ========================================

    def _identify_risk_factors(self, transactions: List[Dict]) -> List[str]:
        """Identify financial risk factors"""
        risks = []

        metrics = self._transaction_metrics(transactions)
        if metrics.get('net_change', 0) < 0:
            risks.append("Negative cash flow")

        anomalies = self._detect_anomalies(transactions)
        if len(anomalies) > 5:
            risks.append(f"{len(anomalies)} anomalous transactions")

        fraud = self._detect_fraud(transactions)
        if fraud:
            risks.append(f"{len(fraud)} potential fraud indicators")

        spending = self._spending_metrics(transactions)
        if spending.get('large_purchases_total', 0) > spending.get('total_spending', 1) * 0.5:
            risks.append("High concentration in large purchases")

        return risks

    def _detect_stress_indicators(self, transactions: List[Dict]) -> List[str]:
        """Detect financial stress indicators"""
        indicators = []

        # Multiple ATM withdrawals
        atm = [t for t in transactions if 'atm' in t['description'].lower()]
        if len(atm) > 10:
            indicators.append(f"{len(atm)} ATM withdrawals (may indicate cash flow issues)")

        # Overdraft fees
        overdraft = [t for t in transactions if 'overdraft' in t['description'].lower()]
        if overdraft:
            indicators.append(f"{len(overdraft)} overdraft fees")

        # Late payment fees
        late_fee = [t for t in transactions if 'late' in t['description'].lower()]
        if late_fee:
            indicators.append(f"{len(late_fee)} late payment fees")

        return indicators

    # ========================================
    # KEY FINDINGS
    # ========================================

    def _extract_key_findings(self, transactions: List[Dict]) -> List[str]:
        """Extract top 5 key findings"""
        findings = []

        # Finding 1: Net position
        metrics = self._transaction_metrics(transactions)
        net = metrics.get('net_change', 0)
        if net > 0:
            findings.append(f"✅ Positive cash flow of ${net:,.2f}")
        else:
            findings.append(f"⚠️ Negative cash flow of ${abs(net):,.2f}")

        # Finding 2: Top category
        categories = self._category_breakdown(transactions)
        if categories['top_5']:
            top = categories['top_5'][0]
            findings.append(f"📊 {top['category']} is top category (${top['total']:,.2f})")

        # Finding 3: Recurring
        recurring = self._detect_recurring_transactions(transactions)
        if recurring:
            findings.append(f"🔄 {len(recurring)} recurring transactions identified")

        # Finding 4: Subscriptions
        subscriptions = self._detect_subscriptions(transactions)
        if subscriptions:
            total = sum(s['monthly_cost'] for s in subscriptions)
            findings.append(f"💳 {len(subscriptions)} subscriptions costing ${total:,.2f}/month")

        # Finding 5: Anomalies
        anomalies = self._detect_anomalies(transactions)
        if anomalies:
            findings.append(f"⚡ {len(anomalies)} unusual transactions detected")

        return findings[:5]

    def _extract_transactions(self, file_path: str) -> List[Dict]:
        """Extract transactions using universal parser"""
        try:
            from app.services.parsers.universal_bank_parser import UniversalBankParser

            parser = UniversalBankParser()
            result = parser.parse(file_path)
            transactions = result.get('transactions', [])

            # Normalize
            normalized = []
            for txn in transactions:
                normalized.append({
                    'date': txn.get('date', ''),
                    'description': txn.get('description', ''),
                    'amount': txn.get('amount', 0),
                    'debit': txn.get('debit', 0),
                    'credit': txn.get('credit', 0),
                    'type': 'debit' if txn.get('debit', 0) > 0 else 'credit',
                    'category': 'Uncategorized',
                    'merchant': '',
                    'is_recurring': False,
                    'is_anomaly': False
                })

            return normalized

        except Exception as e:
            logger.error(f"❌ Extraction failed: {e}")
            return []

    def _categorize_transactions(self, transactions: List[Dict]) -> List[Dict]:
        """Categorize transactions"""
        try:
            from app.services.transaction_categorizer import get_categorizer
            categorizer = get_categorizer()
            return categorizer.categorize_batch(transactions)
        except:
            return self._simple_categorize(transactions)

    def _simple_categorize(self, transactions: List[Dict]) -> List[Dict]:
        """Rule-based categorization"""
        categories = {
            'Groceries': ['grocery', 'supermarket', 'food', 'market', 'whole foods', 'trader joe', 'safeway'],
            'Dining': ['restaurant', 'cafe', 'pizza', 'burger', 'mcdonald', 'starbucks', 'chipotle', 'subway'],
            'Shopping': ['amazon', 'walmart', 'target', 'store', 'mall', 'shop', 'ebay', 'bestbuy'],
            'Utilities': ['electric', 'gas', 'water', 'internet', 'phone', 'cable', 'utility', 'pge', 'att'],
            'Transport': ['uber', 'lyft', 'gas', 'parking', 'transit', 'metro', 'shell', 'chevron', 'bp'],
            'Entertainment': ['netflix', 'spotify', 'movie', 'game', 'steam', 'hulu', 'disney', 'youtube'],
            'Healthcare': ['pharmacy', 'doctor', 'hospital', 'medical', 'cvs', 'walgreens', 'health'],
            'Bills': ['insurance', 'mortgage', 'rent', 'loan', 'payment', 'bill pay'],
            'Income': ['deposit', 'payroll', 'salary', 'transfer in', 'direct dep', 'paycheck']
        }

        for txn in transactions:
            desc_lower = txn['description'].lower()

            # Extract merchant
            words = desc_lower.split()
            if words:
                txn['merchant'] = words[0].title()

            # Categorize
            for category, keywords in categories.items():
                if any(kw in desc_lower for kw in keywords):
                    txn['category'] = category
                    break

            if txn['category'] == 'Uncategorized':
                txn['category'] = 'Other'

        return transactions

    # ========================================
    # TRANSACTION METRICS
    # ========================================

    def _transaction_metrics(self, transactions: List[Dict]) -> Dict:
        """20+ transaction metrics"""
        total_debits = sum(t['debit'] for t in transactions)
        total_credits = sum(t['credit'] for t in transactions)

        debits = [t for t in transactions if t['debit'] > 0]
        credits = [t for t in transactions if t['credit'] > 0]

        return {
            'total_transactions': len(transactions),
            'total_debits': round(total_debits, 2),
            'total_credits': round(total_credits, 2),
            'net_change': round(total_credits - total_debits, 2),

            'debit_count': len(debits),
            'credit_count': len(credits),
            'debit_percentage': round(len(debits) / len(transactions) * 100, 2) if transactions else 0,
            'credit_percentage': round(len(credits) / len(transactions) * 100, 2) if transactions else 0,

            'average_transaction': round((total_debits + total_credits) / len(transactions), 2) if transactions else 0,
            'average_debit': round(total_debits / len(debits), 2) if debits else 0,
            'average_credit': round(total_credits / len(credits), 2) if credits else 0,

            'median_transaction': round(sorted([t['amount'] for t in transactions])[len(transactions) // 2],
                                        2) if transactions else 0,
            'median_debit': round(sorted([t['debit'] for t in debits])[len(debits) // 2], 2) if debits else 0,
            'median_credit': round(sorted([t['credit'] for t in credits])[len(credits) // 2], 2) if credits else 0,

            'largest_transaction': max((t['amount'] for t in transactions), default=0),
            'largest_debit': max((t['debit'] for t in debits), default=0),
            'largest_credit': max((t['credit'] for t in credits), default=0),

            'smallest_transaction': min((t['amount'] for t in transactions if t['amount'] > 0), default=0),
            'smallest_debit': min((t['debit'] for t in debits if t['debit'] > 0), default=0),
            'smallest_credit': min((t['credit'] for t in credits if t['credit'] > 0), default=0)
        }

    # ========================================
    # SPENDING METRICS
    # ========================================

    def _spending_metrics(self, transactions: List[Dict]) -> Dict:
        """15+ spending metrics"""
        debits = [t for t in transactions if t['debit'] > 0]

        if not debits:
            return {}

        total = sum(t['debit'] for t in debits)
        dates = [t['date'] for t in debits]
        unique_dates = len(set(dates))

        return {
            'total_spending': round(total, 2),
            'spending_transaction_count': len(debits),
            'average_spending': round(total / len(debits), 2),
            'daily_average_spending': round(total / unique_dates, 2) if unique_dates > 0 else 0,
            'weekly_average_spending': round(total / (unique_dates / 7), 2) if unique_dates > 0 else 0,
            'monthly_projected_spending': round(total / unique_dates * 30, 2) if unique_dates > 0 else 0,

            'spending_per_transaction_avg': round(total / len(debits), 2),
            'spending_std_deviation': round(self._std_dev([t['debit'] for t in debits]), 2),

            'small_purchases': len([t for t in debits if t['debit'] < 10]),
            'medium_purchases': len([t for t in debits if 10 <= t['debit'] < 100]),
            'large_purchases': len([t for t in debits if t['debit'] >= 100]),

            'small_purchases_total': round(sum(t['debit'] for t in debits if t['debit'] < 10), 2),
            'medium_purchases_total': round(sum(t['debit'] for t in debits if 10 <= t['debit'] < 100), 2),
            'large_purchases_total': round(sum(t['debit'] for t in debits if t['debit'] >= 100), 2)
        }

    # ========================================
    # INCOME METRICS
    # ========================================

    def _income_metrics(self, transactions: List[Dict]) -> Dict:
        """10+ income metrics"""
        credits = [t for t in transactions if t['credit'] > 0]

        if not credits:
            return {}

        total = sum(t['credit'] for t in credits)
        sources = set(t.get('merchant', t['description'][:30]) for t in credits)

        return {
            'total_income': round(total, 2),
            'income_transaction_count': len(credits),
            'average_income': round(total / len(credits), 2),
            'largest_income': max((t['credit'] for t in credits), default=0),
            'smallest_income': min((t['credit'] for t in credits), default=0),

            'income_sources_count': len(sources),
            'recurring_income_count': len([t for t in credits if t.get('is_recurring')]),

            'income_std_deviation': round(self._std_dev([t['credit'] for t in credits]), 2),

            'regular_deposits': len([t for t in credits if 1000 <= t['credit'] <= 10000]),
            'large_deposits': len([t for t in credits if t['credit'] > 10000])
        }

    def _category_metrics(self, transactions: List[Dict]) -> Dict:
        """Dynamic category metrics - AMOUNTS ONLY, NO COUNTS"""
        categories = defaultdict(lambda: {'total': 0, 'count': 0})

        for t in transactions:
            if t['debit'] > 0:
                cat = t.get('category', 'Other')
                categories[cat]['total'] += t['debit']
                categories[cat]['count'] += 1

        metrics = {}
        for cat, data in categories.items():
            safe_cat = cat.lower().replace(' ', '_')

            # ✅ ONLY ADD SPENDING AMOUNTS (not counts!)
            metrics[f'{safe_cat}_spending'] = round(data['total'], 2)

            # ✅ ONLY ADD AVERAGE IF SIGNIFICANT
            if data['total'] > 100:  # Only if >$100 total
                metrics[f'{safe_cat}_average'] = round(data['total'] / data['count'], 2) if data['count'] > 0 else 0

        return metrics

    def _merchant_metrics(self, transactions: List[Dict]) -> Dict:
        """Dynamic merchant metrics - TOP 5 ONLY"""
        merchants = defaultdict(lambda: {'total': 0, 'count': 0})

        for t in transactions:
            if t['debit'] > 0:
                merchant = t.get('merchant', 'Unknown')
                merchants[merchant]['total'] += t['debit']
                merchants[merchant]['count'] += 1

        # ✅ ONLY TOP 5 MERCHANTS (not top 10!)
        top_merchants = sorted(merchants.items(), key=lambda x: x[1]['total'], reverse=True)[:5]

        metrics = {}
        for idx, (merchant, data) in enumerate(top_merchants, 1):
            safe_merchant = merchant.lower().replace(' ', '_')[:20]

            # ✅ ADD NAME AND SPENDING (not count!)
            metrics[f'top_merchant_{idx}_name'] = merchant
            metrics[f'top_merchant_{idx}_spending'] = round(data['total'], 2)

        metrics['unique_merchants_count'] = len(merchants)  # This one is useful!

        return metrics

    # ========================================
    # TEMPORAL METRICS
    # ========================================

    def _temporal_metrics(self, transactions: List[Dict]) -> Dict:
        """20+ temporal metrics"""
        by_date = defaultdict(lambda: {'debits': 0, 'credits': 0, 'count': 0})

        for t in transactions:
            by_date[t['date']]['debits'] += t['debit']
            by_date[t['date']]['credits'] += t['credit']
            by_date[t['date']]['count'] += 1

        daily_debits = [data['debits'] for data in by_date.values()]
        daily_credits = [data['credits'] for data in by_date.values()]
        daily_counts = [data['count'] for data in by_date.values()]

        return {
            'unique_transaction_days': len(by_date),
            'average_transactions_per_day': round(sum(daily_counts) / len(daily_counts), 2) if daily_counts else 0,
            'average_spending_per_day': round(sum(daily_debits) / len(daily_debits), 2) if daily_debits else 0,
            'average_income_per_day': round(sum(daily_credits) / len(daily_credits), 2) if daily_credits else 0,

            'busiest_day_transactions': max(daily_counts, default=0),
            'slowest_day_transactions': min((c for c in daily_counts if c > 0), default=0),

            'highest_spending_day': round(max(daily_debits, default=0), 2),
            'lowest_spending_day': round(min((d for d in daily_debits if d > 0), default=0), 2),

            'days_with_transactions': len([d for d in daily_counts if d > 0]),
            'days_without_transactions': len([d for d in daily_counts if d == 0]),

            'positive_cashflow_days': len([d for d, data in by_date.items() if data['credits'] > data['debits']]),
            'negative_cashflow_days': len([d for d, data in by_date.items() if data['debits'] > data['credits']]),
            'neutral_cashflow_days': len([d for d, data in by_date.items() if data['credits'] == data['debits']])
        }

    # ========================================
    # PATTERN METRICS
    # ========================================

    def _pattern_metrics(self, transactions: List[Dict]) -> Dict:
        """15+ pattern metrics"""
        # Detect recurring
        merchant_frequency = Counter(t.get('merchant', t['description'][:30]) for t in transactions)
        recurring = [m for m, count in merchant_frequency.items() if count >= 2]

        # Detect anomalies
        debits = [t['debit'] for t in transactions if t['debit'] > 0]
        avg_debit = sum(debits) / len(debits) if debits else 0
        anomalies = [t for t in transactions if t['debit'] > avg_debit * 3]

        return {
            'recurring_merchants_count': len(recurring),
            'recurring_transactions_count': sum(merchant_frequency[m] for m in recurring),

            'anomaly_count': len(anomalies),
            'anomaly_total': round(sum(t['debit'] for t in anomalies), 2),

            'duplicate_transactions_count': len([m for m, count in merchant_frequency.items() if count > 5]),

            'round_amount_count': len([t for t in transactions if t['amount'] % 10 == 0]),
            'exact_dollar_count': len([t for t in transactions if t['amount'] % 1 == 0]),

            'weekend_transactions': len([t for t in transactions if self._is_weekend(t['date'])]),
            'weekday_transactions': len([t for t in transactions if not self._is_weekend(t['date'])])
        }

    # ========================================
    # FINANCIAL HEALTH METRICS
    # ========================================

    def _financial_health_metrics(self, transactions: List[Dict]) -> Dict:
        """10+ financial health metrics"""
        total_credits = sum(t['credit'] for t in transactions)
        total_debits = sum(t['debit'] for t in transactions)
        net = total_credits - total_debits

        savings_rate = (net / total_credits * 100) if total_credits > 0 else 0

        # Calculate score
        score = 0
        if savings_rate > 20:
            score += 40
        elif savings_rate > 10:
            score += 30
        elif savings_rate > 0:
            score += 20

        return {
            'net_cashflow': round(net, 2),
            'savings_rate': round(savings_rate, 2),
            'financial_health_score': score,
            'financial_health_status': 'Excellent' if score >= 80 else 'Good' if score >= 60 else 'Fair' if score >= 40 else 'Needs Improvement',

            'income_to_expense_ratio': round(total_credits / total_debits, 2) if total_debits > 0 else 0,
            'expense_to_income_ratio': round(total_debits / total_credits, 2) if total_credits > 0 else 0,

            'projected_annual_savings': round(net * 12, 2),
            'projected_monthly_savings': round(net, 2),

            'discretionary_spending': round(
                sum(t['debit'] for t in transactions if t.get('category') in ['Shopping', 'Dining', 'Entertainment']),
                2),
            'essential_spending': round(
                sum(t['debit'] for t in transactions if t.get('category') in ['Groceries', 'Utilities', 'Bills']), 2)
        }

    # ========================================
    # BREAKDOWNS (20+ charts)
    # ========================================

    def _category_breakdown(self, transactions):
        from collections import defaultdict
        categories = defaultdict(lambda: {'total': 0, 'count': 0})
        for t in transactions:
            if t['debit'] > 0:
                cat = t.get('category', 'Other')
                categories[cat]['total'] += t['debit']
                categories[cat]['count'] += 1
        for cat in categories:
            categories[cat]['total'] = round(categories[cat]['total'], 2)
            if categories[cat]['count'] > 0:
                categories[cat]['average'] = round(categories[cat]['total'] / categories[cat]['count'], 2)

        sorted_cats = sorted(categories.items(), key=lambda x: x[1]['total'], reverse=True)

        # all_items is now a LIST so label_field/value_field work
        all_items = [{'category': k, 'total': v['total'], 'count': v['count']} for k, v in sorted_cats]

        return {
            'available': True,
            'dimension': 'Category',
            'value_field': 'total',
            'total_categories': len(categories),
            'top_5': all_items[:5],
            'top_10': all_items[:10],
            'all_items': all_items,  # ← list now
        }

    def _merchant_breakdown(self, transactions):
        from collections import defaultdict
        merchants = defaultdict(lambda: {'total': 0, 'count': 0})
        for t in transactions:
            if t['debit'] > 0:
                merchant = t.get('merchant', 'Unknown')
                merchants[merchant]['total'] += t['debit']
                merchants[merchant]['count'] += 1
        for m in merchants:
            merchants[m]['total'] = round(merchants[m]['total'], 2)
            if merchants[m]['count'] > 0:
                merchants[m]['average'] = round(merchants[m]['total'] / merchants[m]['count'], 2)

        sorted_merchants = sorted(merchants.items(), key=lambda x: x[1]['total'], reverse=True)

        all_items = [{'category': k, 'total': v['total'], 'count': v['count']} for k, v in sorted_merchants]

        return {
            'available': True,
            'dimension': 'Merchant',
            'value_field': 'total',
            'total_categories': len(merchants),
            'top_5': all_items[:5],
            'top_10': all_items[:10],
            'all_items': all_items,
        }


    def _type_breakdown(self, transactions):
        debits = sum(t['debit'] for t in transactions)
        credits = sum(t['credit'] for t in transactions)

        all_items = [
            {'category': 'Expenses', 'total': round(debits, 2),
             'count': len([t for t in transactions if t['debit'] > 0])},
            {'category': 'Income', 'total': round(credits, 2),
             'count': len([t for t in transactions if t['credit'] > 0])},
        ]

        return {
            'available': True,
            'dimension': 'Type',
            'value_field': 'total',
            'total_categories': 2,
            'top_5': all_items,
            'all_items': all_items,
        }

    def _daily_breakdown(self, transactions):
        from collections import defaultdict
        by_date = defaultdict(lambda: {'debits': 0, 'credits': 0})
        for t in transactions:
            by_date[t['date']]['debits'] += t['debit']
            by_date[t['date']]['credits'] += t['credit']

        sorted_dates = sorted(by_date.items())

        # all_times is a LIST of {key, value} so x_field/y_field work
        all_times = [{'key': date, 'value': round(data['debits'], 2)} for date, data in sorted_dates]

        return {
            'available': True,
            'date_field': 'key',
            'value_field': 'value',
            'data_points': len(by_date),
            'all_times': all_times,  # ← list now
        }

    def _weekly_breakdown(self, transactions):
        from collections import defaultdict
        by_week = defaultdict(lambda: {'total': 0, 'count': 0})
        for t in transactions:
            if t['debit'] > 0:
                week = t['date'][:7]  # YYYY-MM as proxy
                by_week[week]['total'] += t['debit']
                by_week[week]['count'] += 1
        for w in by_week:
            by_week[w]['total'] = round(by_week[w]['total'], 2)

        sorted_weeks = sorted(by_week.items(), key=lambda x: x[0])  # chronological

        all_items = [{'category': k, 'total': v['total'], 'count': v['count']} for k, v in sorted_weeks]

        return {
            'available': True,
            'dimension': 'Week',
            'value_field': 'total',
            'total_categories': len(by_week),
            'top_5': sorted(all_items, key=lambda x: x['total'], reverse=True)[:5],
            'all_items': all_items,
        }

    def _dayofweek_breakdown(self, transactions):
        from collections import defaultdict
        from datetime import datetime

        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        by_dow = defaultdict(lambda: {'total': 0, 'count': 0})

        for t in transactions:
            if t['debit'] > 0:
                try:
                    d = datetime.strptime(t['date'], '%Y-%m-%d')
                    day = day_names[d.weekday()]
                except Exception:
                    day = 'Unknown'
                by_dow[day]['total'] += t['debit']
                by_dow[day]['count'] += 1

        for d in by_dow:
            by_dow[d]['total'] = round(by_dow[d]['total'], 2)

        # preserve Mon→Sun order
        all_items = [
            {'category': day, 'total': by_dow[day]['total'], 'count': by_dow[day]['count']}
            for day in day_names if day in by_dow
        ]

        return {
            'available': True,
            'dimension': 'DayOfWeek',
            'value_field': 'total',
            'total_categories': len(all_items),
            'top_5': sorted(all_items, key=lambda x: x['total'], reverse=True)[:5],
            'all_items': all_items,
        }

    def _amount_range_breakdown(self, transactions):
        ranges = {
            '$0-$10': {'total': 0, 'count': 0},
            '$10-$50': {'total': 0, 'count': 0},
            '$50-$100': {'total': 0, 'count': 0},
            '$100-$500': {'total': 0, 'count': 0},
            '$500+': {'total': 0, 'count': 0},
        }

        for t in transactions:
            amount = t['debit'] if t['debit'] > 0 else t['credit']
            if amount < 10:
                ranges['$0-$10']['total'] += amount; ranges['$0-$10']['count'] += 1
            elif amount < 50:
                ranges['$10-$50']['total'] += amount; ranges['$10-$50']['count'] += 1
            elif amount < 100:
                ranges['$50-$100']['total'] += amount; ranges['$50-$100']['count'] += 1
            elif amount < 500:
                ranges['$100-$500']['total'] += amount; ranges['$100-$500']['count'] += 1
            else:
                ranges['$500+']['total'] += amount; ranges['$500+']['count'] += 1

        all_items = [
            {'category': k, 'total': round(v['total'], 2), 'count': v['count']}
            for k, v in ranges.items()
        ]

        return {
            'available': True,
            'dimension': 'AmountRange',
            'value_field': 'total',
            'total_categories': 5,
            'top_5': all_items,
            'all_items': all_items,
        }

    # ========================================
    # INSIGHTS
    # ========================================

    def _generate_comprehensive_insights(self, transactions: List[Dict]) -> List[str]:
        """Comprehensive insights"""
        insights = []

        metrics = self._transaction_metrics(transactions)
        spending = self._spending_metrics(transactions)
        income = self._income_metrics(transactions)
        health = self._financial_health_metrics(transactions)

        # Net position
        if metrics['net_change'] > 0:
            insights.append(
                f"💰 Saved ${metrics['net_change']:,.2f} this period ({health['savings_rate']:.1f}% savings rate)")
        else:
            insights.append(f"⚠️ Spent ${abs(metrics['net_change']):,.2f} more than earned")

        # Top category
        cat_breakdown = self._category_breakdown(transactions)
        if cat_breakdown['top_5']:
            top = cat_breakdown['top_5'][0]
            insights.append(f"🛍️ Top spending: {top['category']} (${top['total']:,.2f})")

        # Spending patterns
        if spending.get('large_purchases', 0) > 0:
            insights.append(
                f"💳 {spending['large_purchases']} large purchases (>${100}) totaling ${spending.get('large_purchases_total', 0):,.2f}")

        # Income sources
        if income.get('income_sources_count', 0) > 1:
            insights.append(f"💼 {income['income_sources_count']} income sources detected")

        # Financial health
        insights.append(
            f"📊 Financial Health: {health['financial_health_status']} (Score: {health['financial_health_score']}/100)")

        return insights

    # ========================================
    # HELPERS
    # ========================================

    def _std_dev(self, values: List[float]) -> float:
        """Calculate standard deviation"""
        if not values:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5

    def _is_weekend(self, date_str: str) -> bool:
        """Check if date is weekend"""
        try:
            date = datetime.strptime(date_str, '%Y-%m-%d')
            return date.weekday() >= 5
        except:
            return False

    def _empty_result(self) -> Dict:
        """Empty result"""
        result = {
            'type': 'bank_statement',
            'summary': 'No transactions found',
            'insights': ['Unable to extract transactions'],
            'confidence': 0.5,
            'total_transactions': 0,
            'total_income': 0,
            'total_expenses': 0
        }
        result = self.add_ui_config(result, document_type='bank_statement')
        return result


# """
# Advanced Bank Statement Analyzer
# Extracts everything: account info, transactions, fees, interest, patterns, anomalies
# Provides institutional-grade analytics
# """
# import re
# import pdfplumber
# from datetime import datetime, timedelta
# from typing import List, Dict, Optional, Tuple, Any
# from collections import Counter, defaultdict
# import logging
# import ollama
#
# from ..base_analyzer import BaseAnalyzer
#
# logger = logging.getLogger(__name__)
#
#
# class BankStatementAnalyzer(BaseAnalyzer):
#     """
#     Comprehensive bank statement analysis
#     Extracts and analyzes everything for impressive insights
#     """
#
#     def __init__(self):
#         """Initialize without arguments - matches BaseAnalyzer pattern"""
#         super().__init__()
#         self.ollama_client = ollama.Client()
#
#         # Data containers (initialized per analysis)
#         self.file_path = None
#         self.raw_text = None
#         self.metadata = None
#         self.account_info = {}
#         self.transactions = []
#         self.fees = []
#         self.interest_earned = 0
#         self.statement_period = {}
#         self.balances = {}
#         self.account_summary = {}
#
#     def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
#         """
#         Complete analysis pipeline
#
#         Args:
#             file_path: Path to bank statement PDF
#             text: Raw text (not used, we parse PDF directly)
#             metadata: Document metadata
#
#         Returns:
#             Dict containing comprehensive analysis
#         """
#         # Set instance variables for this analysis
#         self.file_path = file_path
#         self.raw_text = text
#         self.metadata = metadata
#
#         # Reset containers
#         self.account_info = {}
#         self.transactions = []
#         self.fees = []
#         self.interest_earned = 0
#         self.statement_period = {}
#         self.balances = {}
#         self.account_summary = {}
#
#         logger.info(f"🏦 Advanced analysis starting: {self.file_path}")
#
#         # Step 1: Extract everything from PDF
#         self._extract_complete_data()
#
#         # Step 2: AI categorize transactions
#         self._categorize_transactions()
#
#         # Step 3: Generate comprehensive analytics
#         analytics = {
#             "account_information": self.account_info,
#             "statement_period": self.statement_period,
#             "balances": self.balances,
#
#             # Transaction analytics
#             "transactions": self.transactions,
#             "transaction_summary": self._transaction_summary(),
#
#             # Spending analytics
#             "spending_analysis": self._analyze_spending(),
#             "spending_by_category": self._spending_by_category(),
#             "spending_trends": self._spending_trends(),
#
#             # Income analytics
#             "income_analysis": self._analyze_income(),
#             "income_sources": self._income_sources(),
#
#             # Top 10 analytics
#             "top_deposits": self._top_deposits(10),
#             "top_withdrawals": self._top_withdrawals(10),
#             "top_merchants": self._top_merchants(10),
#             "top_categories": self._top_categories(10),
#
#             # Pattern detection
#             "recurring_transactions": self._detect_recurring(),
#             "recurring_bills": self._detect_bills(),
#             "subscription_services": self._detect_subscriptions(),
#
#             # Anomaly detection
#             "anomalies": self._detect_anomalies(),
#             "unusual_patterns": self._detect_unusual_patterns(),
#             "fraud_alerts": self._detect_potential_fraud(),
#
#             # Fee analysis
#             "fees": self.fees,
#             "fee_analysis": self._analyze_fees(),
#
#             # Interest
#             "interest_earned": self.interest_earned,
#
#             # Financial health
#             "financial_health": self._calculate_financial_health(),
#             "savings_analysis": self._analyze_savings(),
#             "cash_flow": self._analyze_cash_flow(),
#
#             # Insights & recommendations
#             "insights": self._generate_insights(),
#             "recommendations": self._generate_recommendations(),
#
#             # Visualizations data
#             "charts": self._prepare_chart_data()
#         }
#
#         logger.info(f"✅ Analysis complete: {len(self.transactions)} transactions analyzed")
#
#         # Normalize before returning
#         analytics = self._normalize_analytics_result(analytics)
#
#         # Build result object expected by BaseAnalyzer
#         result = {
#             "type": "bank_statement",
#             "summary": analytics.get("summary", ""),
#             "transactions": analytics.get("transactions", ""),
#             "key_metrics": analytics.get("transaction_summary", {}),
#             "insights": analytics.get("insights", []),
#             "spending_analysis": analytics.get("spending_analysis", []),
#             "spending_by_category": analytics.get("spending_by_category", []),
#             "spending_trends": analytics.get("spending_trends", []),
#             "income_analysis": analytics.get("income_analysis", []),
#             "savings_analysis": analytics.get("savings_analysis", []),
#             "structured_data": {
#                 "tables": [
#                     {
#                         "name": "transactions",
#                         "data": analytics.get("transactions", [])
#                     }
#                 ]
#             },
#             "alerts": [],
#             "confidence": 0.95
#         }
#
#         # Let BaseAnalyzer generate hero metrics, charts, ui_config
#         # ui_conf = self.attach_ui_config(result)
#         # analytics["analytics"] = ui_conf
#         # ui_display_schema = {
#         #     "metrics": [
#         #         {"name": "total_transactions", "label": "Total Transactions",
#         #          "value_path": "transaction_summary.total_transactions"},
#         #         {"name": "total_debits", "label": "Total Debits", "value_path": "transaction_summary.total_debits"},
#         #         {"name": "total_credits", "label": "Total Credits", "value_path": "transaction_summary.total_credits"},
#         #         {"name": "net_change", "label": "Net Change", "value_path": "transaction_summary.net_change"},
#         #         {"name": "avg_transaction", "label": "Average Transaction",
#         #          "value_path": "transaction_summary.avg_transaction"},
#         #         {"name": "largest_transaction", "label": "Largest Transaction",
#         #          "value_path": "transaction_summary.largest_transaction"},
#         #         {"name": "smallest_transaction", "label": "Smallest Transaction",
#         #          "value_path": "transaction_summary.smallest_transaction"},
#         #
#         #         {"name": "total_spending", "label": "Total Spending", "value_path": "spending_analysis.total_spending"},
#         #         {"name": "spending_transaction_count", "label": "Spending Transactions",
#         #          "value_path": "spending_analysis.transaction_count"},
#         #         {"name": "spending_average_transaction", "label": "Average Spend",
#         #          "value_path": "spending_analysis.average_transaction"},
#         #         {"name": "largest_purchase", "label": "Largest Purchase",
#         #          "value_path": "spending_analysis.largest_purchase"},
#         #         {"name": "smallest_purchase", "label": "Smallest Purchase",
#         #          "value_path": "spending_analysis.smallest_purchase"},
#         #         {"name": "daily_average_spending", "label": "Daily Average Spending",
#         #          "value_path": "spending_analysis.daily_average"},
#         #
#         #         {"name": "total_income", "label": "Total Income", "value_path": "income_analysis.total_income"},
#         #         {"name": "income_transactions", "label": "Income Transactions",
#         #          "value_path": "income_analysis.income_transactions"},
#         #         {"name": "average_deposit", "label": "Average Deposit",
#         #          "value_path": "income_analysis.average_deposit"},
#         #         {"name": "largest_deposit", "label": "Largest Deposit",
#         #          "value_path": "income_analysis.largest_deposit"},
#         #         {"name": "income_sources_count", "label": "Income Sources",
#         #          "value_path": "income_analysis.income_sources_count"},
#         #
#         #         {"name": "total_fees", "label": "Total Fees", "value_path": "fee_analysis.total_fees"},
#         #         {"name": "fee_count", "label": "Fee Count", "value_path": "fee_analysis.fee_count"},
#         #         {"name": "avoidable_fees", "label": "Avoidable Fees", "value_path": "fee_analysis.avoidable_fees"},
#         #
#         #         {"name": "interest_earned", "label": "Interest Earned", "value_path": "interest_earned"},
#         #
#         #         {"name": "financial_health_score", "label": "Financial Health Score",
#         #          "value_path": "financial_health.score"},
#         #         {"name": "financial_health_savings_rate", "label": "Savings Rate (%)",
#         #          "value_path": "financial_health.savings_rate"},
#         #
#         #         {"name": "saved_this_period", "label": "Saved This Period",
#         #          "value_path": "savings_analysis.saved_this_period"},
#         #         {"name": "daily_savings_rate", "label": "Daily Savings Rate",
#         #          "value_path": "savings_analysis.daily_savings_rate"},
#         #         {"name": "projected_annual_savings", "label": "Projected Annual Savings",
#         #          "value_path": "savings_analysis.projected_annual_savings"},
#         #         {"name": "savings_percentage", "label": "Savings Percentage (%)",
#         #          "value_path": "savings_analysis.savings_percentage"},
#         #
#         #         {"name": "cash_flow_positive_days", "label": "Positive Cash Flow Days",
#         #          "value_path": "cash_flow.positive_days"},
#         #         {"name": "cash_flow_negative_days", "label": "Negative Cash Flow Days",
#         #          "value_path": "cash_flow.negative_days"}
#         #     ],
#         #
#         #     "tables": [
#         #         {"name": "account_information", "data_path": "account_information"},
#         #         {"name": "statement_period", "data_path": "statement_period"},
#         #         {"name": "transactions", "data_path": "transactions"},
#         #         {"name": "spending_by_category", "data_path": "spending_by_category"},
#         #         {"name": "daily_spending", "data_path": "spending_trends.daily_spending"},
#         #         {"name": "income_sources", "data_path": "income_sources"},
#         #         {"name": "top_deposits", "data_path": "top_deposits"},
#         #         {"name": "top_withdrawals", "data_path": "top_withdrawals"},
#         #         {"name": "top_merchants", "data_path": "top_merchants"},
#         #         {"name": "top_categories", "data_path": "top_categories"},
#         #         {"name": "recurring_transactions", "data_path": "recurring_transactions"},
#         #         {"name": "subscription_services", "data_path": "subscription_services"},
#         #         {"name": "anomalies", "data_path": "anomalies"},
#         #         {"name": "fraud_alerts", "data_path": "fraud_alerts"},
#         #         {"name": "fees", "data_path": "fees"},
#         #         {"name": "daily_cash_flow", "data_path": "cash_flow.daily_cash_flow"}
#         #     ],
#         #
#         #     "charts": [
#         #         {
#         #             "id": "spending_by_category",
#         #             "type": "pie",
#         #             "title": "Spending by Category",
#         #             "data_path": "spending_by_category",
#         #             "label_field": "category",
#         #             "value_field": "amount"
#         #         },
#         #         {
#         #             "id": "daily_spending",
#         #             "type": "line",
#         #             "title": "Daily Spending",
#         #             "data_path": "spending_trends.daily_spending",
#         #             "x_field": "date",
#         #             "y_field": "amount"
#         #         },
#         #         {
#         #             "id": "cash_flow",
#         #             "type": "line",
#         #             "title": "Daily Cash Flow (Net)",
#         #             "data_path": "cash_flow.daily_cash_flow",
#         #             "x_field": "date",
#         #             "y_field": "net"
#         #         },
#         #         {
#         #             "id": "top_merchants",
#         #             "type": "bar",
#         #             "title": "Top Merchants",
#         #             "data_path": "top_merchants",
#         #             "x_field": "name",
#         #             "y_field": "total_spent"
#         #         },
#         #         {
#         #             "id": "income_vs_expenses",
#         #             "type": "bar",
#         #             "title": "Income vs Expenses",
#         #             "data_path": "income_vs_expenses",
#         #             "label_field": "label",
#         #             "value_field": "value"
#         #         }
#         #     ],
#         #
#         #     "insights": {
#         #         "data_path": "insights"
#         #     },
#         #
#         #     "recommendations": {
#         #         "data_path": "recommendations"
#         #     },
#         #
#         #     "layout": {
#         #         "hero": [
#         #             "total_income",
#         #             "total_spending",
#         #             "net_change",
#         #             "financial_health_score"
#         #         ],
#         #
#         #         "sections": [
#         #             {
#         #                 "id": "account_summary",
#         #                 "title": "Account Summary",
#         #                 "metrics": [
#         #                     "total_transactions",
#         #                     "avg_transaction",
#         #                     "largest_transaction",
#         #                     "smallest_transaction"
#         #                 ],
#         #                 "tables": ["account_information", "statement_period"],
#         #                 "charts": []
#         #             },
#         #
#         #             {
#         #                 "id": "spending_overview",
#         #                 "title": "Spending Overview",
#         #                 "metrics": [
#         #                     "total_spending",
#         #                     "spending_transaction_count",
#         #                     "spending_average_transaction",
#         #                     "daily_average_spending"
#         #                 ],
#         #                 "tables": ["spending_by_category", "daily_spending"],
#         #                 "charts": ["spending_by_category", "daily_spending"]
#         #             },
#         #
#         #             {
#         #                 "id": "income_overview",
#         #                 "title": "Income Overview",
#         #                 "metrics": [
#         #                     "total_income",
#         #                     "income_transactions",
#         #                     "average_deposit",
#         #                     "largest_deposit",
#         #                     "income_sources_count"
#         #                 ],
#         #                 "tables": ["income_sources", "top_deposits"],
#         #                 "charts": ["income_vs_expenses"]
#         #             },
#         #
#         #             {
#         #                 "id": "top_merchants_categories",
#         #                 "title": "Top Merchants & Categories",
#         #                 "metrics": [],
#         #                 "tables": ["top_merchants", "top_categories"],
#         #                 "charts": ["top_merchants"]
#         #             },
#         #
#         #             {
#         #                 "id": "recurring_and_subscriptions",
#         #                 "title": "Recurring & Subscriptions",
#         #                 "metrics": [],
#         #                 "tables": ["recurring_transactions", "subscription_services"],
#         #                 "charts": []
#         #             },
#         #
#         #             {
#         #                 "id": "risk_alerts",
#         #                 "title": "Anomalies & Fraud Alerts",
#         #                 "metrics": [],
#         #                 "tables": ["anomalies", "fraud_alerts"],
#         #                 "charts": []
#         #             },
#         #
#         #             {
#         #                 "id": "cash_flow",
#         #                 "title": "Cash Flow",
#         #                 "metrics": [
#         #                     "cash_flow_positive_days",
#         #                     "cash_flow_negative_days",
#         #                     "saved_this_period",
#         #                     "projected_annual_savings"
#         #                 ],
#         #                 "tables": ["daily_cash_flow"],
#         #                 "charts": ["cash_flow"]
#         #             },
#         #
#         #             {
#         #                 "id": "all_transactions",
#         #                 "title": "All Transactions",
#         #                 "metrics": [],
#         #                 "tables": ["transactions"],
#         #                 "charts": []
#         #             }
#         #         ]
#         #     }
#         #
#         # }
#
#         ui_display_schema = {
#             "metrics": [
#
#                 {"name": "account_name", "label": "Account Name",
#                  "value_path": "account_information.account_name"},
#                 {"name": "account_number", "label": "Account Number",
#                  "value_path": "account_information.account_number"},
#                 {"name": "bank_name", "label": "Bank Name", "value_path": "account_information.bank_name"},
#                 {"name": "account_type", "label": "Account Type", "value_path": "account_information.account_type"},
#
#                 {"name": "statement_start", "label": "Statement Start", "value_path": "statement_period.start_date"},
#                 {"name": "statement_end", "label": "Statement End", "value_path": "statement_period.end_date"},
#
#                 {"name": "total_transactions", "label": "Total Transactions",
#                  "value_path": "summary.total_transactions"},
#                 {"name": "average_transaction", "label": "Average Transaction",
#                  "value_path": "summary.average_transaction"},
#                 {"name": "total_income", "label": "Total Income", "value_path": "summary.total_income"},
#                 {"name": "total_expenses", "label": "Total Expenses", "value_path": "summary.total_expenses"},
#                 {"name": "net_cash_flow", "label": "Net Cash Flow", "value_path": "summary.net_cash_flow"},
#
#                 {"name": "cash_flow_positive_days", "label": "Positive Cash Flow Days",
#                  "value_path": "cash_flow.positive_days"},
#                 {"name": "cash_flow_negative_days", "label": "Negative Cash Flow Days",
#                  "value_path": "cash_flow.negative_days"},
#
#                 {"name": "top_spend_category", "label": "Top Spend Category",
#                  "value_path": "charts.spending_by_category.labels[0]"},
#                 {"name": "top_spend_category_amount", "label": "Top Category Amount",
#                  "value_path": "charts.spending_by_category.values[0]"},
#                 {"name": "top_merchant_name", "label": "Top Merchant", "value_path": "top_merchants[0].name"},
#                 {"name": "top_merchant_spent", "label": "Top Merchant Spend",
#                  "value_path": "top_merchants[0].total_spent"},
#
#                 {"name": "anomaly_count", "label": "Anomaly Count", "value_path": "anomalies.length"},
#                 {"name": "fraud_alert_count", "label": "Fraud Alerts", "value_path": "fraud_alerts.length"}
#             ],
#
#             "tables": [
#                 {"name": "account_information", "data_path": "account_information"},
#                 {"name": "statement_period", "data_path": "statement_period"},
#                 {"name": "transactions", "data_path": "transactions"},
#                 {"name": "spending_by_category", "data_path": "spending_by_category"},
#                 {"name": "daily_spending", "data_path": "charts.daily_spending"},
#                 {"name": "income_sources", "data_path": "income_sources"},
#                 {"name": "top_deposits", "data_path": "top_deposits"},
#                 {"name": "top_merchants", "data_path": "top_merchants"},
#                 {"name": "top_categories", "data_path": "top_categories"},
#                 {"name": "recurring_transactions", "data_path": "recurring_transactions"},
#                 {"name": "subscription_services", "data_path": "subscription_services"},
#                 {"name": "anomalies", "data_path": "anomalies"},
#                 {"name": "fraud_alerts", "data_path": "fraud_alerts"},
#                 {"name": "fees", "data_path": "fees"},
#                 {"name": "daily_cash_flow", "data_path": "cash_flow.daily_cash_flow"}
#             ],
#
#             "charts": [
#                 {
#                     "id": "spending_by_category",
#                     "type": "pie",
#                     "title": "Spending by Category",
#                     "data_path": "charts.spending_by_category",
#                     "label_field": "labels",
#                     "value_field": "values"
#                 },
#                 {
#                     "id": "daily_spending",
#                     "type": "line",
#                     "title": "Daily Spending",
#                     "data_path": "charts.daily_spending",
#                     "x_field": "date",
#                     "y_field": "amount"
#                 },
#                 {
#                     "id": "cash_flow",
#                     "type": "line",
#                     "title": "Daily Cash Flow (Net)",
#                     "data_path": "charts.cash_flow",
#                     "x_field": "date",
#                     "y_field": "net"
#                 },
#                 {
#                     "id": "income_vs_expenses",
#                     "type": "bar",
#                     "title": "Income vs Expenses",
#                     "data_path": "charts.income_vs_expenses",
#                     "label_field": "labels",
#                     "value_field": "values"
#                 },
#                 {
#                     "id": "inflow_vs_outflow",
#                     "type": "bar",
#                     "title": "Inflow vs Outflow by Day",
#                     "data_path": "cash_flow.daily_cash_flow",
#                     "label_field": "date",
#                     "value_fields": ["inflow", "outflow"]
#                 },
#                 {
#                     "id": "cumulative_spending",
#                     "type": "line",
#                     "title": "Cumulative Spending Over Time",
#                     "data_path": "charts.daily_spending",
#                     "x_field": "date",
#                     "y_field": "amount"
#                 },
#                 {
#                     "id": "top_merchants_bar",
#                     "type": "bar",
#                     "title": "Top Merchants (Bar)",
#                     "data_path": "top_merchants",
#                     "x_field": "name",
#                     "y_field": "total_spent"
#                 },
#                 {
#                     "id": "top_categories_bar",
#                     "type": "bar",
#                     "title": "Top Categories (Bar)",
#                     "data_path": "top_categories",
#                     "x_field": "category",
#                     "y_field": "total_spent"
#                 },
#                 {
#                     "id": "anomaly_timeline",
#                     "type": "line",
#                     "title": "Anomaly Timeline",
#                     "data_path": "anomalies",
#                     "x_field": "date",
#                     "y_field": "amount"
#                 },
#                 {
#                     "id": "fraud_timeline",
#                     "type": "line",
#                     "title": "Fraud Alerts Timeline",
#                     "data_path": "fraud_alerts",
#                     "x_field": "date",
#                     "y_field": "amount"
#                 }
#             ],
#
#             "insights": {
#                 "data_path": "insights"
#             },
#
#             "recommendations": {
#                 "data_path": "recommendations"
#             },
#
#             "layout": {
#                 "hero": [
#                     "account_name",
#                     "account_number",
#                     "bank_name",
#                     "account_type"
#                 ],
#
#                 "sections": [
#                     {
#                         "id": "account_summary",
#                         "title": "Account Summary",
#                         "metrics": [
#                             "account_name",
#                             "account_number",
#                             "bank_name",
#                             "account_type",
#                             "statement_start",
#                             "statement_end"
#                         ],
#                         "tables": [
#                             "account_information",
#                             "statement_period"
#                         ],
#                         "charts": []
#                     },
#                     {
#                         "id": "spending_overview",
#                         "title": "Spending Overview",
#                         "metrics": [
#                             "total_expenses",
#                             "top_spend_category",
#                             "top_spend_category_amount"
#                         ],
#                         "tables": [
#                             "spending_by_category",
#                             "daily_spending"
#                         ],
#                         "charts": [
#                             "spending_by_category",
#                             "daily_spending"
#                         ]
#                     },
#                     {
#                         "id": "income_overview",
#                         "title": "Income Overview",
#                         "metrics": [
#                             "total_income"
#                         ],
#                         "tables": [
#                             "income_sources",
#                             "top_deposits"
#                         ],
#                         "charts": [
#                             "income_vs_expenses"
#                         ]
#                     },
#                     {
#                         "id": "cash_flow",
#                         "title": "Cash Flow",
#                         "metrics": [
#                             "cash_flow_positive_days",
#                             "cash_flow_negative_days",
#                             "net_cash_flow"
#                         ],
#                         "tables": [
#                             "daily_cash_flow"
#                         ],
#                         "charts": [
#                             "cash_flow",
#                             "inflow_vs_outflow"
#                         ]
#                     },
#                     {
#                         "id": "top_merchants_categories",
#                         "title": "Top Merchants & Categories",
#                         "metrics": [
#                             "top_merchant_name",
#                             "top_merchant_spent"
#                         ],
#                         "tables": [
#                             "top_merchants",
#                             "top_categories"
#                         ],
#                         "charts": [
#                             "top_merchants_bar",
#                             "top_categories_bar"
#                         ]
#                     },
#                     {
#                         "id": "recurring_and_subscriptions",
#                         "title": "Recurring & Subscriptions",
#                         "metrics": [
#                             "recurring_count",
#                             "subscription_count"
#                         ],
#                         "tables": [
#                             "recurring_transactions",
#                             "subscription_services"
#                         ],
#                         "charts": []
#                     },
#                     {
#                         "id": "risk_alerts",
#                         "title": "Anomalies & Fraud Alerts",
#                         "metrics": [
#                             "anomaly_count",
#                             "fraud_alert_count"
#                         ],
#                         "tables": [
#                             "anomalies",
#                             "fraud_alerts"
#                         ],
#                         "charts": [
#                             "anomaly_timeline",
#                             "fraud_timeline"
#                         ]
#                     },
#                     {
#                         "id": "all_transactions",
#                         "title": "All Transactions",
#                         "metrics": [],
#                         "tables": [
#                             "transactions"
#                         ],
#                         "charts": []
#                     },
#                     {
#                         "id": "insights_and_recommendations",
#                         "title": "Insights & Recommendations",
#                         "metrics": [],
#                         "tables": [],
#                         "charts": []
#                     }
#                 ]
#             }
#         }
#
#         analytics["ui_schema"] = ui_display_schema
#         return analytics
#
#     def _normalize_analytics_result(self, analytics: Dict) -> Dict:
#         """
#         Normalize analytics to consistent format expected by frontend/API
#         """
#         normalized = analytics.copy()
#
#         # ===== Normalize top merchants =====
#         if "top_merchants" in analytics:
#             normalized_merchants = []
#             for m in analytics["top_merchants"]:
#                 normalized_merchants.append({
#                     "name": m.get("name") or m.get("merchant", "Unknown"),
#                     "total_spent": m.get("total_spent", 0),
#                     "transactions": m.get("transactions") or m.get("transaction_count") or m.get("count", 0)
#                 })
#             normalized["top_merchants"] = normalized_merchants
#
#         # ===== Normalize transaction summary =====
#         if "transaction_summary" in analytics:
#             summary = analytics["transaction_summary"]
#             normalized["summary"] = {
#                 "total_transactions": summary.get("total_transactions", 0),
#                 "total_income": summary.get("total_credits", 0),
#                 "total_expenses": summary.get("total_debits", 0),
#                 "net_cash_flow": summary.get("net_change", 0),
#                 "average_transaction": summary.get("avg_transaction", 0)
#             }
#
#         # ===== Normalize spending analysis =====
#         if "spending_analysis" in analytics:
#             spending = analytics["spending_analysis"]
#             normalized["spending_analysis"] = {
#                 "total_spending": spending.get("total_spending", 0),
#                 "transaction_count": spending.get("transaction_count", 0),
#                 "average_transaction": spending.get("average_transaction", 0),
#                 "largest_purchase": spending.get("largest_purchase", 0),
#                 "smallest_purchase": spending.get("smallest_purchase", 0),
#                 "daily_average": spending.get("daily_average", 0)
#             }
#
#         # ===== Normalize income analysis =====
#         if "income_analysis" in analytics:
#             income = analytics["income_analysis"]
#             normalized["income_analysis"] = {
#                 "total_income": income.get("total_income", 0),
#                 "income_transactions": income.get("income_transactions", 0),
#                 "average_deposit": income.get("average_deposit", 0),
#                 "largest_deposit": income.get("largest_deposit", 0),
#                 "income_sources_count": income.get("income_sources_count", 0)
#             }
#
#         # ===== Normalize top deposits =====
#         if "top_deposits" in analytics:
#             normalized_deposits = []
#             for d in analytics["top_deposits"]:
#                 normalized_deposits.append({
#                     "date": d.get("date", ""),
#                     "description": d.get("description", ""),
#                     "amount": d.get("amount", 0)
#                 })
#             normalized["top_deposits"] = normalized_deposits
#
#         # ===== Normalize top withdrawals =====
#         if "top_withdrawals" in analytics:
#             normalized_withdrawals = []
#             for w in analytics["top_withdrawals"]:
#                 normalized_withdrawals.append({
#                     "date": w.get("date", ""),
#                     "description": w.get("description", ""),
#                     "amount": w.get("amount", 0)
#                 })
#             normalized["top_withdrawals"] = normalized_withdrawals
#
#         # ===== Normalize top categories =====
#         if "top_categories" in analytics:
#             normalized_categories = []
#             for c in analytics["top_categories"]:
#                 normalized_categories.append({
#                     "category": c.get("category", "Other"),
#                     "amount": c.get("amount", 0)
#                 })
#             normalized["top_categories"] = normalized_categories
#
#         # ===== Normalize recurring transactions =====
#         if "recurring_transactions" in analytics:
#             normalized_recurring = []
#             for r in analytics["recurring_transactions"]:
#                 normalized_recurring.append({
#                     "merchant": r.get("merchant", "Unknown"),
#                     "frequency": r.get("frequency", 0),
#                     "average_amount": r.get("average_amount", 0),
#                     "total": r.get("total", 0),
#                     "type": r.get("type", "debit")
#                 })
#             normalized["recurring_transactions"] = normalized_recurring
#
#         # ===== Normalize recurring bills =====
#         if "recurring_bills" in analytics:
#             normalized_bills = []
#             for b in analytics["recurring_bills"]:
#                 normalized_bills.append({
#                     "merchant": b.get("merchant", "Unknown"),
#                     "frequency": b.get("frequency", 0),
#                     "average_amount": b.get("average_amount", 0),
#                     "total": b.get("total", 0)
#                 })
#             normalized["recurring_bills"] = normalized_bills
#
#         # ===== Normalize subscription services =====
#         if "subscription_services" in analytics:
#             normalized_subscriptions = []
#             for s in analytics["subscription_services"]:
#                 normalized_subscriptions.append({
#                     "merchant": s.get("merchant", "Unknown"),
#                     "frequency": s.get("frequency", 0),
#                     "average_amount": s.get("average_amount", 0),
#                     "total": s.get("total", 0)
#                 })
#             normalized["subscription_services"] = normalized_subscriptions
#
#         # ===== Normalize anomalies =====
#         if "anomalies" in analytics:
#             normalized_anomalies = []
#             for a in analytics["anomalies"]:
#                 normalized_anomalies.append({
#                     "date": a.get("date", ""),
#                     "description": a.get("description", ""),
#                     "amount": a.get("amount", 0),
#                     "reason": a.get("reason", ""),
#                     "severity": a.get("severity", "low")
#                 })
#             normalized["anomalies"] = normalized_anomalies
#
#         # ===== Normalize fraud alerts =====
#         if "fraud_alerts" in analytics:
#             normalized_alerts = []
#             for alert in analytics["fraud_alerts"]:
#                 normalized_alerts.append({
#                     "type": alert.get("type", "unknown"),
#                     "date": alert.get("date", ""),
#                     "description": alert.get("description", ""),
#                     "amount": alert.get("amount", 0),
#                     "severity": alert.get("severity", "low"),
#                     "count": alert.get("count", 1)
#                 })
#             normalized["fraud_alerts"] = normalized_alerts
#
#         # ===== Normalize fees =====
#         if "fees" in analytics:
#             normalized_fees = []
#             for fee in analytics["fees"]:
#                 normalized_fees.append({
#                     "type": fee.get("type", "unknown"),
#                     "amount": fee.get("amount", 0)
#                 })
#             normalized["fees"] = normalized_fees
#
#         # ===== Normalize fee analysis =====
#         if "fee_analysis" in analytics:
#             fee_analysis = analytics["fee_analysis"]
#             normalized["fee_analysis"] = {
#                 "total_fees": fee_analysis.get("total_fees", 0),
#                 "fee_count": fee_analysis.get("fee_count", 0),
#                 "by_type": fee_analysis.get("by_type", {}),
#                 "avoidable_fees": fee_analysis.get("avoidable_fees", 0)
#             }
#
#         # ===== Normalize financial health =====
#         if "financial_health" in analytics:
#             health = analytics["financial_health"]
#             normalized["financial_health"] = {
#                 "score": health.get("score", 0),
#                 "savings_rate": health.get("savings_rate", 0),
#                 "status": health.get("status", "Unknown"),
#                 "factors": health.get("factors", [])
#             }
#
#         # ===== Normalize savings analysis =====
#         if "savings_analysis" in analytics:
#             savings = analytics["savings_analysis"]
#             normalized["savings_analysis"] = {
#                 "saved_this_period": savings.get("saved_this_period", 0),
#                 "daily_savings_rate": savings.get("daily_savings_rate", 0),
#                 "projected_annual_savings": savings.get("projected_annual_savings", 0),
#                 "savings_percentage": savings.get("savings_percentage", 0)
#             }
#
#         # ===== Normalize cash flow =====
#         if "cash_flow" in analytics:
#             cash_flow = analytics["cash_flow"]
#             normalized["cash_flow"] = {
#                 "daily_cash_flow": cash_flow.get("daily_cash_flow", []),
#                 "positive_days": cash_flow.get("positive_days", 0),
#                 "negative_days": cash_flow.get("negative_days", 0)
#             }
#
#         # ===== Normalize insights =====
#         if "insights" in analytics:
#             # Insights should already be list of strings
#             normalized["insights"] = analytics["insights"]
#
#         # ===== Normalize recommendations =====
#         if "recommendations" in analytics:
#             normalized_recommendations = []
#             for rec in analytics["recommendations"]:
#                 normalized_recommendations.append({
#                     "priority": rec.get("priority", "low"),
#                     "category": rec.get("category", "general"),
#                     "title": rec.get("title", ""),
#                     "description": rec.get("description", ""),
#                     "potential_savings": rec.get("potential_savings", 0)
#                 })
#             normalized["recommendations"] = normalized_recommendations
#
#         # ===== Normalize charts =====
#         if "charts" in analytics:
#             charts = analytics["charts"]
#
#             normalized["charts"] = {}
#
#             # Spending by category (list → labels/values)
#             sbc = charts.get("spending_by_category", [])
#             if isinstance(sbc, list):
#                 normalized["charts"]["spending_by_category"] = {
#                     "labels": [item.get("category") for item in sbc],
#                     "values": [item.get("amount") for item in sbc]
#                 }
#             else:
#                 normalized["charts"]["spending_by_category"] = {
#                     "labels": sbc.get("labels", []),
#                     "values": sbc.get("values", [])
#                 }
#
#             # Income vs expenses (list → labels/values)
#             ive = charts.get("income_vs_expenses", [])
#             if isinstance(ive, list):
#                 normalized["charts"]["income_vs_expenses"] = {
#                     "labels": [item.get("label") for item in ive],
#                     "values": [item.get("value") for item in ive]
#                 }
#             else:
#                 normalized["charts"]["income_vs_expenses"] = {
#                     "labels": ive.get("labels", ["Income", "Expenses"]),
#                     "values": ive.get("values", [0, 0])
#                 }
#
#             # Daily spending (list of dicts)
#             ds = charts.get("daily_spending", [])
#             normalized["charts"]["daily_spending"] = ds
#
#             # Cash flow (list of dicts)
#             cf = charts.get("cash_flow", [])
#             normalized["charts"]["cash_flow"] = cf
#
#         # ===== Normalize transactions =====
#         if "transactions" in analytics:
#             normalized_transactions = []
#             for txn in analytics["transactions"]:
#                 normalized_transactions.append({
#                     "date": txn.get("date", ""),
#                     "description": txn.get("description", ""),
#                     "debit": txn.get("debit", 0),
#                     "credit": txn.get("credit", 0),
#                     "amount": txn.get("amount", 0),
#                     "category": txn.get("category", "Other"),
#                     "merchant": txn.get("merchant", ""),
#                     "page": txn.get("page", 1)
#                 })
#             normalized["transactions"] = normalized_transactions
#
#         # ===== Add analyzer type =====
#         normalized["analyzer_type"] = "bank_statement"
#
#         return normalized
#
#     def _extract_complete_data(self):
#         """Use universal parser and extract ALL data"""
#         from app.services.parsers.universal_bank_parser import UniversalBankParser
#
#         parser = UniversalBankParser(self.file_path)
#         result = parser.parse()
#
#         # Extract transactions
#         self.transactions = result.get('transactions', [])
#
#         # ✅ Extract account_summary (has the correct totals)
#         self.account_summary = result.get('account_summary', {})
#
#         # ✅ Extract from raw text since parser doesn't get these
#         self._extract_account_info_from_text()
#         self._extract_statement_period_from_text()
#
#         logger.info(f"✅ Parser extracted: {len(self.transactions)} transactions")
#         logger.info(f"   Account summary: {self.account_summary}")
#         logger.info(f"   Account info: {self.account_info}")
#         logger.info(f"   Statement period: {self.statement_period}")
#
#     def _extract_account_info_from_text(self):
#         """Extract account information from raw text"""
#         try:
#             import pdfplumber
#
#             with pdfplumber.open(self.file_path) as pdf:
#                 if pdf.pages:
#                     first_page = pdf.pages[0].extract_text()
#
#                     # Extract account holder
#                     holder_match = re.search(r'^([A-Z\s]+)\n', first_page, re.MULTILINE)
#                     if holder_match:
#                         self.account_info['account_holder'] = holder_match.group(1).strip()
#
#                     # Extract account number
#                     account_match = re.search(r'Account number:\s*([\d\s]+)', first_page)
#                     if account_match:
#                         self.account_info['account_number'] = account_match.group(1).strip()
#
#         except Exception as e:
#             logger.error(f"Account info extraction error: {e}")
#
#     def _extract_statement_period_from_text(self):
#         """Extract statement period from account_summary dates"""
#         try:
#             # The account_summary already has the period in the text
#             # "Beginning balance on November 4, 2025"
#             # "Ending balance on December 5, 2025"
#
#             import pdfplumber
#
#             with pdfplumber.open(self.file_path) as pdf:
#                 if pdf.pages:
#                     first_page = pdf.pages[0].extract_text()
#
#                     # Look for statement period
#                     period_match = re.search(r'for\s+(\w+ \d+, \d{4})\s+to\s+(\w+ \d+, \d{4})', first_page)
#                     if period_match:
#                         start_str = period_match.group(1)
#                         end_str = period_match.group(2)
#
#                         from datetime import datetime
#
#                         start_date = datetime.strptime(start_str, '%B %d, %Y').strftime('%Y-%m-%d')
#                         end_date = datetime.strptime(end_str, '%B %d, %Y').strftime('%Y-%m-%d')
#
#                         self.statement_period = {
#                             'start_date': start_date,
#                             'end_date': end_date
#                         }
#
#         except Exception as e:
#             logger.error(f"Statement period extraction error: {e}")
#
#     def _extract_transactions_from_page(self, text: str, page_num: int):
#         """Extract transactions from page text"""
#         lines = text.split('\n')
#
#         for line in lines:
#             line = line.strip()
#
#             if len(line) < 20:
#                 continue
#
#             # Pattern: DATE + TEXT + AMOUNT
#             date_match = re.match(r'^(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(.+)', line)
#
#             if not date_match:
#                 continue
#
#             date_str = date_match.group(1)
#             rest = date_match.group(2).strip()
#
#             # Find amount at end
#             amount_match = re.search(r'(-?[\d,]+\.\d{2})\s*$', rest)
#
#             if not amount_match:
#                 continue
#
#             amount_str = amount_match.group(1)
#             description = rest[:amount_match.start()].strip()
#
#             # Validate
#             if len(description) < 5:
#                 continue
#
#             if any(kw in description.upper() for kw in ['DATE', 'DESCRIPTION', 'BALANCE', 'TOTAL', 'CONTINUED']):
#                 continue
#
#             # Parse amount
#             amount = float(amount_str.replace(',', ''))
#
#             # Determine type
#             is_debit = amount < 0 or any(kw in description.upper() for kw in
#                                          ['WITHDRAWAL', 'DEBIT', 'PAYMENT', 'PURCHASE', 'FEE', 'CHARGE'])
#
#             txn = {
#                 'date': self._parse_date(date_str),
#                 'description': description,
#                 'amount': abs(amount),
#                 'debit': abs(amount) if is_debit else 0,
#                 'credit': abs(amount) if not is_debit else 0,
#                 'raw_amount': amount,
#                 'page': page_num
#             }
#
#             self.transactions.append(txn)
#
#     def _extract_fees_from_page(self, text: str):
#         """Extract all fees"""
#         fee_patterns = [
#             r'(overdraft fee|service charge|maintenance fee|atm fee|wire fee|foreign transaction)[\s:]*\$?([\d,]+\.\d{2})',
#         ]
#
#         for pattern in fee_patterns:
#             matches = re.finditer(pattern, text, re.I)
#             for match in matches:
#                 self.fees.append({
#                     'type': match.group(1).strip(),
#                     'amount': float(match.group(2).replace(',', ''))
#                 })
#
#     def _extract_interest(self, text: str):
#         """Extract interest earned"""
#         interest_match = re.search(r'interest\s+(?:earned|paid|credited)[\s:]*\$?([\d,]+\.\d{2})', text, re.I)
#         if interest_match:
#             self.interest_earned += float(interest_match.group(1).replace(',', ''))
#
#     def _ai_understand_statement(self, text: str):
#         """Use AI to understand statement structure and extract metadata"""
#         try:
#             prompt = f"""Analyze this bank statement and extract key information.
#
# Statement text:
# {text}
#
# Extract and return ONLY JSON:
# {{
#   "bank_name": "name of bank",
#   "account_type": "checking/savings/credit",
#   "account_holder": "name if visible",
#   "statement_date": "statement date",
#   "days_in_period": number of days covered
# }}"""
#
#             response = self.ollama_client.generate(
#                 model="phi3:mini",
#                 prompt=prompt,
#                 options={"temperature": 0.1}
#             )
#
#             import json
#             resp_text = response['response'].strip()
#             resp_text = re.sub(r'```json\s*', '', resp_text)
#             resp_text = re.sub(r'```\s*', '', resp_text)
#
#             json_match = re.search(r'\{[^}]+\}', resp_text, re.DOTALL)
#             if json_match:
#                 metadata = json.loads(json_match.group())
#                 self.account_info.update(metadata)
#                 logger.info(f"🤖 AI extracted metadata: {metadata}")
#
#         except Exception as e:
#             logger.error(f"AI extraction failed: {e}")
#
#     def _categorize_transactions(self):
#         """AI categorize all transactions"""
#         try:
#             from app.services.transaction_categorizer import get_categorizer
#
#             logger.info(f"🤖 Categorizing {len(self.transactions)} transactions...")
#
#             categorizer = get_categorizer()
#             self.transactions = categorizer.categorize_batch(self.transactions)
#
#         except Exception as e:
#             logger.error(f"Categorization failed: {e}")
#
#     # ==================== ANALYTICS ====================
#
#     def _transaction_summary(self) -> Dict:
#         """Basic transaction statistics"""
#         total_debits = sum(t['debit'] for t in self.transactions)
#         total_credits = sum(t['credit'] for t in self.transactions)
#
#         return {
#             'total_transactions': len(self.transactions),
#             'total_debits': round(total_debits, 2),
#             'total_credits': round(total_credits, 2),
#             'net_change': round(total_credits - total_debits, 2),
#             'avg_transaction': round(sum(t['amount'] for t in self.transactions) / len(self.transactions),
#                                      2) if self.transactions else 0,
#             'largest_transaction': max((t['amount'] for t in self.transactions), default=0),
#             'smallest_transaction': min((t['amount'] for t in self.transactions if t['amount'] > 0), default=0)
#         }
#
#     def _analyze_spending(self) -> Dict:
#         """Detailed spending analysis"""
#         debits = [t for t in self.transactions if t['debit'] > 0]
#
#         if not debits:
#             return {}
#
#         amounts = [t['debit'] for t in debits]
#
#         return {
#             'total_spending': round(sum(amounts), 2),
#             'transaction_count': len(debits),
#             'average_transaction': round(sum(amounts) / len(amounts), 2),
#             'median_transaction': round(sorted(amounts)[len(amounts) // 2], 2),
#             'largest_purchase': round(max(amounts), 2),
#             'smallest_purchase': round(min(amounts), 2),
#             'daily_average': round(sum(amounts) / self._days_in_period(), 2) if self._days_in_period() > 0 else 0
#         }
#
#     def _spending_by_category(self) -> Dict:
#         """Spending grouped by category"""
#         categories = defaultdict(float)
#
#         for t in self.transactions:
#             if t['debit'] > 0:
#                 cat = t.get('category', 'Other')
#                 categories[cat] += t['debit']
#
#         return {k: round(v, 2) for k, v in sorted(categories.items(), key=lambda x: x[1], reverse=True)}
#
#     def _spending_trends(self) -> Dict:
#         """Daily/weekly spending trends"""
#         by_date = defaultdict(float)
#
#         for t in self.transactions:
#             if t['debit'] > 0:
#                 by_date[t['date']] += t['debit']
#
#         return {
#             'daily_spending': {k: round(v, 2) for k, v in sorted(by_date.items())},
#             'highest_spending_day': max(by_date.items(), key=lambda x: x[1]) if by_date else None,
#             'lowest_spending_day': min(by_date.items(), key=lambda x: x[1]) if by_date else None
#         }
#
#     def _analyze_income(self) -> Dict:
#         """Income analysis"""
#         credits = [t for t in self.transactions if t['credit'] > 0]
#
#         if not credits:
#             return {}
#
#         amounts = [t['credit'] for t in credits]
#
#         return {
#             'total_income': round(sum(amounts), 2),
#             'income_transactions': len(credits),
#             'average_deposit': round(sum(amounts) / len(amounts), 2),
#             'largest_deposit': round(max(amounts), 2),
#             'income_sources_count': len(set(t.get('merchant', t['description']) for t in credits))
#         }
#
#     def _income_sources(self) -> List[Dict]:
#         """Identify income sources"""
#         sources = defaultdict(lambda: {'total': 0, 'count': 0})
#
#         for t in self.transactions:
#             if t['credit'] > 0:
#                 source = t.get('merchant', t['description'][:30])
#                 sources[source]['total'] += t['credit']
#                 sources[source]['count'] += 1
#
#         return sorted(
#             [{'source': k, 'total': round(v['total'], 2), 'count': v['count']}
#              for k, v in sources.items()],
#             key=lambda x: x['total'],
#             reverse=True
#         )
#
#     def _top_deposits(self, n: int) -> List[Dict]:
#         """Top N deposits"""
#         credits = [t for t in self.transactions if t['credit'] > 0]
#         return sorted(
#             [{'date': t['date'], 'description': t['description'], 'amount': t['credit']}
#              for t in credits],
#             key=lambda x: x['amount'],
#             reverse=True
#         )[:n]
#
#     def _top_withdrawals(self, n: int) -> List[Dict]:
#         """Top N withdrawals"""
#         debits = [t for t in self.transactions if t['debit'] > 0]
#         return sorted(
#             [{'date': t['date'], 'description': t['description'], 'amount': t['debit']}
#              for t in debits],
#             key=lambda x: x['amount'],
#             reverse=True
#         )[:n]
#
#     def _top_merchants(self, n: int) -> List[Dict]:
#         """Top N merchants by spending"""
#         merchants = defaultdict(lambda: {'total': 0, 'count': 0})
#
#         for t in self.transactions:
#             if t['debit'] > 0:
#                 merchant = t.get('merchant', t['description'][:30])
#                 merchants[merchant]['total'] += t['debit']
#                 merchants[merchant]['count'] += 1
#
#         return sorted(
#             [{'merchant': k, 'total_spent': round(v['total'], 2), 'transactions': v['count']}
#              for k, v in merchants.items()],
#             key=lambda x: x['total_spent'],
#             reverse=True
#         )[:n]
#
#     def _top_categories(self, n: int) -> List[Dict]:
#         """Top N spending categories"""
#         categories = self._spending_by_category()
#         return [{'category': k, 'amount': v} for k, v in list(categories.items())[:n]]
#
#     # ==================== PATTERN DETECTION ====================
#
#     def _detect_recurring(self) -> List[Dict]:
#         """Detect all recurring transactions"""
#         # Group similar transactions
#         groups = defaultdict(list)
#
#         for t in self.transactions:
#             merchant = t.get('merchant', t['description'][:20])
#             groups[merchant].append(t)
#
#         recurring = []
#         for merchant, txns in groups.items():
#             if len(txns) >= 2:
#                 amounts = [t['amount'] for t in txns]
#                 avg_amount = sum(amounts) / len(amounts)
#
#                 # Check if amounts are similar (within 10%)
#                 is_recurring = all(abs(a - avg_amount) / avg_amount < 0.1 for a in amounts if avg_amount > 0)
#
#                 if is_recurring:
#                     recurring.append({
#                         'merchant': merchant,
#                         'frequency': len(txns),
#                         'average_amount': round(avg_amount, 2),
#                         'total': round(sum(amounts), 2),
#                         'type': 'debit' if txns[0]['debit'] > 0 else 'credit'
#                     })
#
#         return sorted(recurring, key=lambda x: x['total'], reverse=True)
#
#     def _detect_bills(self) -> List[Dict]:
#         """Detect recurring bills"""
#         recurring = self._detect_recurring()
#
#         # Bills are recurring debits with keywords
#         bill_keywords = ['bill pay', 'payment to', 'autopay', 'utility', 'insurance', 'mortgage', 'rent', 'loan']
#
#         bills = []
#         for r in recurring:
#             if r['type'] == 'debit':
#                 desc_lower = r['merchant'].lower()
#                 if any(kw in desc_lower for kw in bill_keywords) or r['frequency'] >= 3:
#                     bills.append(r)
#
#         return bills
#
#     def _detect_subscriptions(self) -> List[Dict]:
#         """Detect subscription services"""
#         recurring = self._detect_recurring()
#
#         # Subscriptions are small recurring debits
#         subscriptions = []
#         for r in recurring:
#             if r['type'] == 'debit' and r['average_amount'] < 100 and r['frequency'] >= 2:
#                 subscriptions.append(r)
#
#         return subscriptions
#
#     # ==================== ANOMALY DETECTION ====================
#
#     def _detect_anomalies(self) -> List[Dict]:
#         """Detect unusual transactions"""
#         debits = [t for t in self.transactions if t['debit'] > 0]
#
#         if len(debits) < 5:
#             return []
#
#         amounts = [t['debit'] for t in debits]
#         avg = sum(amounts) / len(amounts)
#
#         anomalies = []
#         for t in debits:
#             if t['debit'] > avg * 3:
#                 anomalies.append({
#                     'date': t['date'],
#                     'description': t['description'],
#                     'amount': t['debit'],
#                     'reason': f'{round(t["debit"] / avg, 1)}x above average',
#                     'severity': 'high' if t['debit'] > avg * 5 else 'medium'
#                 })
#
#         return sorted(anomalies, key=lambda x: x['amount'], reverse=True)
#
#     def _detect_unusual_patterns(self) -> List[str]:
#         """Detect unusual spending patterns"""
#         patterns = []
#
#         # Multiple transactions same day
#         by_date = defaultdict(list)
#         for t in self.transactions:
#             by_date[t['date']].append(t)
#
#         for date, txns in by_date.items():
#             if len(txns) > 10:
#                 patterns.append(f"High activity on {date}: {len(txns)} transactions")
#
#         # Large balance drop
#         if self.balances.get('beginning') and self.balances.get('ending'):
#             change = self.balances['ending'] - self.balances['beginning']
#             if change < -1000:
#                 patterns.append(f"Large balance decrease: ${abs(change):,.2f}")
#
#         # Unusual merchants
#         merchants = [t.get('merchant', '') for t in self.transactions if t['debit'] > 0]
#         merchant_counts = Counter(merchants)
#
#         for merchant, count in merchant_counts.most_common(3):
#             if count > 5:
#                 patterns.append(f"Frequent transactions at {merchant}: {count} times")
#
#         return patterns
#
#     def _detect_potential_fraud(self) -> List[Dict]:
#         """Flag potential fraud indicators"""
#         alerts = []
#
#         # Duplicate transactions same day
#         by_date_desc = defaultdict(list)
#         for t in self.transactions:
#             key = f"{t['date']}:{t['description'][:20]}"
#             by_date_desc[key].append(t)
#
#         for key, txns in by_date_desc.items():
#             if len(txns) > 1:
#                 amounts = [t['amount'] for t in txns]
#                 if len(set(amounts)) == 1:  # Same amount
#                     alerts.append({
#                         'type': 'duplicate_transaction',
#                         'date': txns[0]['date'],
#                         'description': txns[0]['description'],
#                         'count': len(txns),
#                         'amount': txns[0]['amount'],
#                         'severity': 'medium'
#                     })
#
#         # Unusual location (if available)
#         # Round amounts (often fraud)
#         for t in self.transactions:
#             if t['debit'] > 0 and t['debit'] % 100 == 0 and t['debit'] > 500:
#                 alerts.append({
#                     'type': 'round_amount',
#                     'date': t['date'],
#                     'description': t['description'],
#                     'amount': t['debit'],
#                     'severity': 'low'
#                 })
#
#         return alerts
#
#     # ==================== FEE ANALYSIS ====================
#
#     def _analyze_fees(self) -> Dict:
#         """Analyze all fees"""
#         if not self.fees:
#             return {'total_fees': 0, 'fee_types': []}
#
#         by_type = defaultdict(float)
#         for fee in self.fees:
#             by_type[fee['type']] += fee['amount']
#
#         return {
#             'total_fees': round(sum(f['amount'] for f in self.fees), 2),
#             'fee_count': len(self.fees),
#             'by_type': {k: round(v, 2) for k, v in by_type.items()},
#             'avoidable_fees': round(sum(f['amount'] for f in self.fees
#                                         if 'overdraft' in f['type'].lower() or 'maintenance' in f['type'].lower()), 2)
#         }
#
#     # ==================== FINANCIAL HEALTH ====================
#
#     def _calculate_financial_health(self) -> Dict:
#         """Calculate financial health score"""
#         summary = self._transaction_summary()
#
#         if not summary or summary.get('total_credits', 0) == 0:
#             return {}
#
#         savings_rate = (summary['net_change'] / summary['total_credits']) * 100
#
#         # Health score factors
#         score = 0
#         factors = []
#
#         # Savings rate (40 points)
#         if savings_rate > 20:
#             score += 40
#             factors.append("Excellent savings rate")
#         elif savings_rate > 10:
#             score += 30
#             factors.append("Good savings rate")
#         elif savings_rate > 0:
#             score += 20
#             factors.append("Positive savings")
#         else:
#             score += 0
#             factors.append("Spending more than earning")
#
#         # Fee management (20 points)
#         total_fees = sum(f['amount'] for f in self.fees)
#         if total_fees == 0:
#             score += 20
#             factors.append("No fees paid")
#         elif total_fees < 50:
#             score += 15
#             factors.append("Low fees")
#         elif total_fees < 100:
#             score += 10
#             factors.append("Moderate fees")
#
#         # Transaction diversity (20 points)
#         categories = len(set(t.get('category', 'Other') for t in self.transactions))
#         if categories >= 8:
#             score += 20
#         elif categories >= 5:
#             score += 15
#         elif categories >= 3:
#             score += 10
#
#         # Recurring income (20 points)
#         credits = [t for t in self.transactions if t['credit'] > 0]
#         if len(credits) >= 2:
#             score += 20
#             factors.append("Multiple income sources")
#         elif len(credits) >= 1:
#             score += 10
#
#         return {
#             'score': min(score, 100),
#             'savings_rate': round(savings_rate, 2),
#             'status': 'Excellent' if score >= 80 else 'Good' if score >= 60 else 'Fair' if score >= 40 else 'Needs Improvement',
#             'factors': factors
#         }
#
#     def _analyze_savings(self) -> Dict:
#         """Savings analysis"""
#         summary = self._transaction_summary()
#
#         if not summary:
#             return {}
#
#         net_change = summary.get('net_change', 0)
#         days = self._days_in_period()
#
#         return {
#             'saved_this_period': round(net_change, 2),
#             'daily_savings_rate': round(net_change / days, 2) if days > 0 else 0,
#             'projected_annual_savings': round(net_change * (365 / days), 2) if days > 0 else 0,
#             'savings_percentage': round((net_change / summary.get('total_credits', 1)) * 100, 2) if summary.get(
#                 'total_credits', 0) > 0 else 0
#         }
#
#     def _analyze_cash_flow(self) -> Dict:
#         """Cash flow analysis"""
#         by_date = defaultdict(lambda: {'in': 0, 'out': 0})
#
#         for t in self.transactions:
#             by_date[t['date']]['in'] += t['credit']
#             by_date[t['date']]['out'] += t['debit']
#
#         daily_data = []
#         for date in sorted(by_date.keys()):
#             data = by_date[date]
#             daily_data.append({
#                 'date': date,
#                 'inflow': round(data['in'], 2),
#                 'outflow': round(data['out'], 2),
#                 'net': round(data['in'] - data['out'], 2)
#             })
#
#         return {
#             'daily_cash_flow': daily_data,
#             'positive_days': len([d for d in daily_data if d['net'] > 0]),
#             'negative_days': len([d for d in daily_data if d['net'] < 0])
#         }
#
#     # ==================== INSIGHTS & RECOMMENDATIONS ====================
#
#     def _generate_insights(self) -> List[str]:
#         """Generate key insights"""
#         insights = []
#
#         summary = self._transaction_summary()
#         spending = self._analyze_spending()
#
#         # Net position
#         if summary.get('net_change', 0) > 0:
#             insights.append(f"💰 You saved ${summary['net_change']:,.2f} this period")
#         else:
#             insights.append(f"⚠️ You spent ${abs(summary['net_change']):,.2f} more than you earned")
#
#         # Top category
#         categories = self._spending_by_category()
#         if categories:
#             top_cat = list(categories.items())[0]
#             insights.append(f"🛍️ Highest spending: {top_cat[0]} (${top_cat[1]:,.2f})")
#
#         # Fees
#         if self.fees:
#             total_fees = sum(f['amount'] for f in self.fees)
#             insights.append(f"💸 Paid ${total_fees:,.2f} in fees")
#
#         # Recurring
#         recurring = self._detect_recurring()
#         if recurring:
#             insights.append(f"🔄 {len(recurring)} recurring transactions identified")
#
#         # Anomalies
#         anomalies = self._detect_anomalies()
#         if anomalies:
#             insights.append(f"⚡ {len(anomalies)} unusual transactions detected")
#
#         return insights
#
#     def _generate_recommendations(self) -> List[Dict]:
#         """Generate actionable recommendations"""
#         recommendations = []
#
#         # Fee reduction
#         total_fees = sum(f['amount'] for f in self.fees)
#         if total_fees > 20:
#             recommendations.append({
#                 'priority': 'high',
#                 'category': 'fees',
#                 'title': 'Reduce Banking Fees',
#                 'description': f"You paid ${total_fees:,.2f} in fees. Consider switching to a no-fee account.",
#                 'potential_savings': total_fees * 12
#             })
#
#         # Subscription optimization
#         subscriptions = self._detect_subscriptions()
#         if len(subscriptions) > 5:
#             total_sub_cost = sum(s['total'] for s in subscriptions)
#             recommendations.append({
#                 'priority': 'medium',
#                 'category': 'subscriptions',
#                 'title': 'Review Subscriptions',
#                 'description': f"You have {len(subscriptions)} subscriptions costing ${total_sub_cost:,.2f}. Review and cancel unused ones.",
#                 'potential_savings': total_sub_cost * 0.3 * 12
#             })
#
#         # Savings opportunity
#         summary = self._transaction_summary()
#         if summary.get('net_change', 0) < 0:
#             recommendations.append({
#                 'priority': 'high',
#                 'category': 'savings',
#                 'title': 'Start Saving',
#                 'description': "You're spending more than earning. Create a budget and track expenses.",
#                 'potential_savings': abs(summary['net_change']) * 12
#             })
#
#         # Large purchases
#         anomalies = self._detect_anomalies()
#         if anomalies:
#             recommendations.append({
#                 'priority': 'low',
#                 'category': 'spending',
#                 'title': 'Review Large Purchases',
#                 'description': f"You had {len(anomalies)} unusually large transactions. Make sure they were intentional.",
#                 'potential_savings': 0
#             })
#
#         return sorted(recommendations, key=lambda x: {'high': 0, 'medium': 1, 'low': 2}[x['priority']])
#
#     # ==================== VISUALIZATION DATA ====================
#
#     def _prepare_chart_data(self):
#         charts = {}
#
#         # Convert daily_spending dict → list
#         daily_spending = self._spending_trends().get("daily_spending", {})
#         charts["daily_spending"] = [
#             {"date": d, "amount": v} for d, v in daily_spending.items()
#         ]
#
#         # Convert spending_by_category dict → list
#         spending_by_category = self._spending_by_category()
#         charts["spending_by_category"] = [
#             {"category": k, "amount": v} for k, v in spending_by_category.items()
#         ]
#
#         # Convert income_vs_expenses dict → list
#         income = self._analyze_income().get("total_income", 0)
#         expenses = self._analyze_spending().get("total_spending", 0)
#         charts["income_vs_expenses"] = [
#             {"label": "Income", "value": income},
#             {"label": "Expenses", "value": expenses}
#         ]
#
#         # Cash flow is already a list of dicts
#         charts["cash_flow"] = self._analyze_cash_flow().get("daily_cash_flow", [])
#
#         return charts
#
#     # def _prepare_chart_data(self) -> Dict:
#     #     """Prepare data for frontend charts"""
#     #     categories = self._spending_by_category()
#     #
#     #     return {
#     #         'spending_by_category': {
#     #             'labels': list(categories.keys()),
#     #             'values': list(categories.values())
#     #         },
#     #         'income_vs_expenses': {
#     #             'labels': ['Income', 'Expenses'],
#     #             'values': [
#     #                 self._transaction_summary().get('total_credits', 0),
#     #                 self._transaction_summary().get('total_debits', 0)
#     #             ]
#     #         },
#     #         'daily_spending': self._spending_trends().get('daily_spending', {}),
#     #         'cash_flow': self._analyze_cash_flow().get('daily_cash_flow', [])
#     #     }
#
#     # ==================== HELPERS ====================
#
#     def _days_in_period(self) -> int:
#         """Calculate days in statement period"""
#         if not self.statement_period:
#             return 30  # Default
#
#         try:
#             start = datetime.strptime(self.statement_period['start_date'], '%m/%d/%Y')
#             end = datetime.strptime(self.statement_period['end_date'], '%m/%d/%Y')
#             return (end - start).days
#         except:
#             return 30
#
#     def _parse_date(self, date_str: str) -> str:
#         """Parse date string"""
#         if not date_str:
#             return ""
#
#         formats = ['%m/%d/%Y', '%m/%d/%y', '%m/%d']
#
#         for fmt in formats:
#             try:
#                 dt = datetime.strptime(date_str.strip(), fmt)
#                 if '%Y' not in fmt and '%y' not in fmt:
#                     dt = dt.replace(year=datetime.now().year)
#                 return dt.strftime('%Y-%m-%d')
#             except:
#                 continue
#
#         return date_str
