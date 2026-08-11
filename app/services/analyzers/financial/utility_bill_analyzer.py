import logging
import re
from typing import Any, Dict, List, Optional

from app.services.analyzers.base_analyzer import BaseAnalyzer
from app.services.mixins.tabular_loader_mixin import TabularLoaderMixin

logger = logging.getLogger(__name__)


class UtilityBillAnalyzer(BaseAnalyzer, TabularLoaderMixin):
    """
    UtilityBillAnalyzer v2.5
    Provider-agnostic, multi-page, multi-table, usage-aware, cost-aware.
    Uses INVOICE-STYLE UI CONFIG to ensure Angular compatibility.
    """

    TYPE = "utility_bill"

    # -----------------------------
    # Regex patterns (provider-agnostic)
    # -----------------------------
    BILLING_PERIOD_RE = re.compile(
        r"(billing period|service period)[^\d]*(\d{1,2}\/\d{1,2}\/\d{2,4}).*?(\d{1,2}\/\d{1,2}\/\d{2,4})",
        re.I | re.S
    )

    TOTAL_DUE_RE = re.compile(
        r"(total amount due|amount due)[^\d]*([\$]?\d+[\.\d+]*)",
        re.I
    )

    ELECTRIC_USAGE_RE = re.compile(r"(\d+(\.\d+)?)\s*kwh", re.I)
    GAS_USAGE_RE = re.compile(r"(\d+(\.\d+)?)\s*therms", re.I)
    WATER_USAGE_RE = re.compile(r"(\d+(\.\d+)?)\s*(ccf|gallons)", re.I)

    METER_READING_RE = re.compile(
        r"(meter|reading)[^\d]*(\d{3,8})",
        re.I
    )

    CHARGE_LINE_RE = re.compile(
        r"([a-zA-Z ]+)\s+([\$]?\d+[\.\d+]*)",
        re.I
    )

    TAX_RE = re.compile(
        r"(tax|surcharge|fee|adjustment)[^\d]*([\$]?\d+[\.\d+]*)",
        re.I
    )

    # -----------------------------
    # Main entrypoint
    # -----------------------------
    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📄 UtilityBillAnalyzer v2.5: {file_path}")

        cleaned = text.lower()

        # Load tables (multi-page)
        df = self._load_dataframe_universal(
            file_path=file_path,
            parsed=metadata.get("parsed"),
            allow_pdf_tables=True
        )

        # -----------------------------
        # Extract fields
        # -----------------------------
        billing_period = self._extract_billing_period(cleaned)
        total_due = self._extract_total_due(cleaned)
        usage = self._extract_usage(cleaned)
        meter_readings = self._extract_meter_readings(cleaned)
        charges = self._extract_charges(cleaned, metadata.get("tables", []))
        taxes = self._extract_taxes(cleaned)

        # Precompute chart data for UI
        usage_chart = self._build_usage_chart(usage)
        charge_chart = self._build_charge_chart(charges)

        # -----------------------------
        # Insights + Alerts
        # -----------------------------
        insights = self._generate_insights(usage, charges, total_due)
        alerts = self._generate_alerts(usage, total_due, meter_readings)

        # -----------------------------
        # Final result object
        # -----------------------------
        result = {
            "type": "utility_bill",
            "billing_period": billing_period,
            "total_due": total_due,
            "usage": usage,
            "meter_readings": meter_readings,
            "charges": charges,
            "taxes": taxes,
            "charge_count": len(charges),
            "tax_count": len(taxes),
            "usage_chart": usage_chart,
            "charge_chart": charge_chart,
            "insights": insights,
            "alerts": alerts,
            "has_advanced_analytics": True,
        }

        # -----------------------------
        # UI Config (Invoice-style)
        # -----------------------------
        result["ui_config"] = self.build_utility_bill_ui_config(result)

        return result

    # -----------------------------
    # Extraction helpers
    # -----------------------------
    def _extract_billing_period(self, text: str):
        m = self.BILLING_PERIOD_RE.search(text)
        if m:
            return {"start": m.group(2), "end": m.group(3)}
        return None

    def _extract_total_due(self, text: str):
        m = self.TOTAL_DUE_RE.search(text)
        if m:
            return m.group(2).replace("$", "")
        return None

    def _extract_usage(self, text: str):
        usage = {}
        if m := self.ELECTRIC_USAGE_RE.search(text):
            usage["electric_kwh"] = float(m.group(1))
        if m := self.GAS_USAGE_RE.search(text):
            usage["gas_therms"] = float(m.group(1))
        if m := self.WATER_USAGE_RE.search(text):
            usage["water"] = float(m.group(1))
        return usage

    def _extract_meter_readings(self, text: str):
        readings = []
        for m in self.METER_READING_RE.finditer(text):
            readings.append(m.group(2))
        return readings

    def _extract_charges(self, text: str, tables: list = None):
        """Extract charges from tables if available, fall back to regex."""
        charges = []

        # Prefer table data — already structured correctly by Camelot
        if tables:
            for table in tables:
                data = table.get("data", [])
                for row in data:
                    if len(row) >= 2:
                        label = str(row[0]).strip()
                        amount = str(row[-1]).strip().replace("$", "").replace(",", "")
                        # Valid charge: label is text, amount is a number
                        if (label and len(label) > 2
                                and re.match(r"^\d{1,6}(\.\d{1,4})?$", amount)
                                and float(amount) < 100000):
                            charges.append({"label": label, "amount": amount})
            if charges:
                return charges

        # Fallback regex for label\namount pattern
        pattern = re.compile(
            r"^(.{5,60})\n\s*(\$?\d{1,6}(?:\.\d{1,4})?)$",
            re.MULTILINE
        )
        for m in pattern.finditer(text):
            label = m.group(1).strip()
            amount = m.group(2).replace("$", "")
            try:
                val = float(amount)
                if 0 < val < 100000 and not label[0].isdigit():
                    charges.append({"label": label, "amount": amount})
            except ValueError:
                continue

        return charges

    def _extract_taxes(self, text: str):
        taxes = []
        for m in self.TAX_RE.finditer(text):
            taxes.append({
                "label": m.group(1),
                "amount": m.group(2).replace("$", "")
            })
        return taxes

    # -----------------------------
    # Chart builders
    # -----------------------------
    def _build_usage_chart(self, usage: Dict[str, Any]):
        return [
            {"label": "Electric (kWh)", "value": usage.get("electric_kwh", 0)},
            {"label": "Gas (Therms)", "value": usage.get("gas_therms", 0)},
            {"label": "Water", "value": usage.get("water", 0)},
        ]

    def _build_charge_chart(self, charges: List[Dict[str, Any]]):
        return [
            {"label": c["label"], "value": float(c["amount"])}
            for c in charges
        ]

    # -----------------------------
    # Insights
    # -----------------------------
    def _generate_insights(self, usage, charges, total_due):
        insights = []

        if "electric_kwh" in usage:
            insights.append(f"Electric usage: {usage['electric_kwh']} kWh")

        if "gas_therms" in usage:
            insights.append(f"Gas usage: {usage['gas_therms']} therms")

        if total_due:
            insights.append(f"Total amount due: ${total_due}")

        if charges:
            total_charges = sum(float(c["amount"]) for c in charges if c["amount"])
            insights.append(f"Total charges: ${total_charges:.2f}")

        return insights

    # -----------------------------
    # Alerts
    # -----------------------------
    def _generate_alerts(self, usage, total_due, meter_readings):
        alerts = []

        if "electric_kwh" in usage and usage["electric_kwh"] > 2000:
            alerts.append("High electric usage detected")

        if "gas_therms" in usage and usage["gas_therms"] > 200:
            alerts.append("High gas usage detected")

        if total_due and float(total_due) > 500:
            alerts.append("Unusually high bill amount")

        if len(meter_readings) < 2:
            alerts.append("Missing meter readings")

        return alerts

    # -----------------------------
    # UI CONFIG (Invoice-style)
    # -----------------------------
    def build_utility_bill_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Invoice-style UI config for Utility Bills.
        Matches EXACTLY the structure used by InvoiceAnalyzer.
        """

        hero_metrics = [
            {"key": "total_due", "label": "Total Amount Due", "value_path": "total_due", "is_hero": True},
            {"key": "electric_kwh", "label": "Electric Usage (kWh)", "value_path": "usage.electric_kwh", "is_hero": True},
            {"key": "gas_therms", "label": "Gas Usage (Therms)", "value_path": "usage.gas_therms", "is_hero": True},
            {"key": "water_usage", "label": "Water Usage", "value_path": "usage.water", "is_hero": True},
        ]

        secondary_metrics = [
            {"key": "billing_period", "label": "Billing Period", "value_path": "billing_period", "is_hero": False},
            {"key": "meter_readings", "label": "Meter Readings", "value_path": "meter_readings", "is_hero": False},
            {"key": "charge_count", "label": "Charge Lines", "value_path": "charge_count", "is_hero": False},
            {"key": "tax_count", "label": "Tax Lines", "value_path": "tax_count", "is_hero": False},
        ]

        charts = [
            {
                "id": "usage_breakdown",
                "type": "bar",
                "title": "Utility Usage Breakdown",
                "data_path": "usage_chart",
                "label_field": "label",
                "value_field": "value",
            },
            {
                "id": "charge_breakdown",
                "type": "bar",
                "title": "Charge Breakdown",
                "data_path": "charge_chart",
                "label_field": "label",
                "value_field": "value",
            },
        ]

        tables = [
            {"id": "charges", "name": "charges", "title": "Charges", "data_path": "charges"},
            {"id": "taxes", "name": "taxes", "title": "Taxes & Fees", "data_path": "taxes"},
            {"id": "meter_readings", "name": "meter_readings", "title": "Meter Readings", "data_path": "meter_readings"},
            {"id": "insights", "name": "insights", "title": "Insights", "data_path": "insights"},
            {"id": "alerts", "name": "alerts", "title": "Alerts", "data_path": "alerts"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Billing Summary",
                "metrics": ["total_due", "billing_period"],
                "charts": ["usage_breakdown"],
                "tables": [],
            },
            {
                "id": "usage",
                "title": "Usage Details",
                "metrics": ["electric_kwh", "gas_therms", "water_usage"],
                "charts": ["usage_breakdown"],
                "tables": ["meter_readings"],
            },
            {
                "id": "charges",
                "title": "Charges",
                "metrics": ["charge_count"],
                "charts": ["charge_breakdown"],
                "tables": ["charges"],
            },
            {
                "id": "taxes",
                "title": "Taxes & Fees",
                "metrics": ["tax_count"],
                "charts": [],
                "tables": ["taxes"],
            },
            {
                "id": "insights",
                "title": "Insights & Alerts",
                "metrics": [],
                "charts": [],
                "tables": ["insights", "alerts"],
            },
        ]

        return {
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
