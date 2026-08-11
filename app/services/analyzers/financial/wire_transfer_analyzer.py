import logging
from typing import Dict, Any, List
from datetime import datetime

from .base_financial_analyzer import BaseFinancialAnalyzer

logger = logging.getLogger(__name__)


class WireTransferAnalyzer(BaseFinancialAnalyzer):
    """
    Domain-specific analyzer for Wire Transfers.
    - Sender, beneficiary, bank details
    - Amount, fees, currency
    - SWIFT, routing, account numbers
    - Risk checks (international, high-value, missing info)
    - Recommendations
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🏦 Advanced Wire Transfer analysis: {file_path}")

        wt = self._extract_wire_transfer(file_path, text, metadata)

        amount = self.safe_float(wt.get("amount", 0))
        fee = self.safe_float(wt.get("fee", 0))
        total_cost = amount + fee

        risk_flags = self._risk_checks(wt, amount)
        insights = self._generate_insights(wt, amount, fee, risk_flags)
        recommendations = self._generate_recommendations(wt, amount, fee, risk_flags)

        result = {
            "type": "wire_transfer",
            "summary": self._generate_summary(wt, amount, fee),
            "confidence": 0.95,

            # Header
            "reference_number": wt.get("reference_number"),
            "swift_code": wt.get("swift_code"),
            "routing_number": wt.get("routing_number"),
            "sender_account": wt.get("sender_account"),
            "beneficiary_account": wt.get("beneficiary_account"),
            "beneficiary_name": wt.get("beneficiary_name"),
            "bank_name": wt.get("bank_name"),
            "transfer_date": wt.get("transfer_date"),
            "currency": wt.get("currency", "USD"),

            # Amounts
            "amount": amount,
            "fee": fee,
            "total_cost": total_cost,

            # Risks
            "risk_flags": risk_flags,
            "risk_flag_count": len(risk_flags),

            # Insights
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_wire_transfer_ui_config(result)
        result["has_advanced_analytics"] = True

        return result

    # ============================================================
    # EXTRACTION
    # ============================================================

    def _extract_wire_transfer(self, file_path, text, metadata):
        try:
            from app.services.parsers.universal_wire_parser import UniversalWireParser
            parser = UniversalWireParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Wire transfer parsing failed: {e}")
            parsed = {}

        return {
            "reference_number": parsed.get("reference_number"),
            "swift_code": parsed.get("swift_code"),
            "routing_number": parsed.get("routing_number"),
            "sender_account": parsed.get("sender_account"),
            "beneficiary_account": parsed.get("beneficiary_account"),
            "beneficiary_name": parsed.get("beneficiary_name"),
            "bank_name": parsed.get("bank_name"),
            "transfer_date": parsed.get("transfer_date"),
            "currency": parsed.get("currency", "USD"),
            "amount": parsed.get("amount"),
            "fee": parsed.get("fee"),
        }

    # ============================================================
    # RISK & INSIGHTS
    # ============================================================

    def _risk_checks(self, wt, amount):
        risks = []

        if amount > 10000:
            risks.append("High-value transfer exceeding $10,000.")

        if wt.get("swift_code") and wt["swift_code"].startswith("XXX"):
            risks.append("Unusual SWIFT code format.")

        if not wt.get("beneficiary_account"):
            risks.append("Missing beneficiary account number.")

        if not wt.get("routing_number"):
            risks.append("Missing routing number.")

        return risks

    def _generate_summary(self, wt, amount, fee):
        ref = wt.get("reference_number", "N/A")
        ben = wt.get("beneficiary_name", "Beneficiary")
        return f"Wire transfer {ref} to {ben} totaling ${amount:,.2f} with fees of ${fee:,.2f}."

    def _generate_insights(self, wt, amount, fee, risks):
        insights = [
            f"Transfer amount: ${amount:,.2f}",
            f"Transfer fee: ${fee:,.2f}",
        ]

        if risks:
            insights.append(f"Risk flags: {len(risks)} issues detected.")

        return insights

    def _generate_recommendations(self, wt, amount, fee, risks):
        recs = []

        if amount > 10000:
            recs.append({
                "priority": "high",
                "category": "risk",
                "title": "High-Value Transfer",
                "description": "Transfers above $10,000 may require compliance review.",
                "potential_savings": 0,
            })

        if fee > 50:
            recs.append({
                "priority": "medium",
                "category": "cost",
                "title": "High Transfer Fee",
                "description": f"Fee of ${fee:,.2f} is above typical thresholds.",
                "potential_savings": 0,
            })

        for r in risks:
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Risk Flag",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    # ============================================================
    # UI CONFIG
    # ============================================================

    def build_wire_transfer_ui_config(self, result):
        hero_metrics = [
            self.metric_def("amount", "Amount", True),
            self.metric_def("fee", "Fee", True),
            self.metric_def("total_cost", "Total Cost", True),
            self.metric_def("risk_flag_count", "Risk Flags", True),
        ]

        secondary_metrics = [
            self.metric_def("reference_number", "Reference Number"),
            self.metric_def("swift_code", "SWIFT Code"),
            self.metric_def("routing_number", "Routing Number"),
        ]

        charts = []  # Wire transfers are not chart-heavy

        tables = [
            self.table_def("risk_flags", "Risk Flags", "risk_flags"),
            self.table_def("recommendations", "Recommendations", "recommendations"),
        ]

        sections = [
            self.section_def(
                "summary",
                "Wire Transfer Summary",
                metrics=["amount", "fee", "total_cost", "risk_flag_count"],
            ),
            self.section_def(
                "risk",
                "Risk Analysis",
                metrics=["risk_flag_count"],
                tables=["risk_flags"],
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
