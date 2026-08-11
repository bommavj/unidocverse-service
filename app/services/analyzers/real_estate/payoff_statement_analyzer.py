import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, date

from ..financial.base_financial_analyzer import BaseFinancialAnalyzer

logger = logging.getLogger(__name__)


class PayoffStatementAnalyzer(BaseFinancialAnalyzer):
    """
    Analyzer for mortgage payoff statements:
    - Loan number, borrower, lender
    - Payoff amount, good-through date, per diem
    - Fee breakdown, escrow, suspense
    - Risk flags: expired quote, missing wiring, ambiguous instructions
    """

    @staticmethod
    def adapt_ui_config_to_ui_schema(ui_config: dict) -> dict:
        if not ui_config:
            return {
                "layout": {"hero": [], "sections": []},
                "metrics": [],
                "tables": [],
                "charts": []
            }

        hero = ui_config.get("hero_metrics", [])
        sections = ui_config.get("sections", [])
        tables = ui_config.get("tables", [])
        charts = ui_config.get("charts", [])
        metrics = [m["key"] for m in hero]

        return {
            "layout": {
                "hero": hero,
                "sections": sections
            },
            "metrics": metrics,
            "tables": tables,
            "charts": charts
        }

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🏦 Payoff statement analysis: {file_path}")

        payoff = self._extract_payoff(file_path, text, metadata)

        payoff_metrics = self._payoff_metrics(payoff)
        risk_metrics = self._risk_metrics(payoff, payoff_metrics)
        timing_metrics = self._timing_metrics(payoff, payoff_metrics)

        insights = self._generate_insights(payoff, payoff_metrics, risk_metrics, timing_metrics)
        recommendations = self._generate_recommendations(payoff, payoff_metrics, risk_metrics, timing_metrics)

        result: Dict[str, Any] = {
            "type": "payoff_statement",
            "summary": self._generate_summary(payoff, payoff_metrics),
            "confidence": 0.95,

            "loan_number": payoff.get("loan_number"),
            "borrower_name": payoff.get("borrower_name"),
            "lender_name": payoff.get("lender_name"),
            "property_address": payoff.get("property_address"),

            "good_through_date": payoff.get("good_through_date"),
            "statement_date": payoff.get("statement_date"),
            "generated_at": payoff.get("generated_at"),

            **payoff_metrics,
            **risk_metrics,
            **timing_metrics,

            "insights": insights,
            "recommendations": recommendations,
        }

        # Attach raw payoff structure for downstream intelligence
        result["payoff_raw"] = payoff

        # Intelligence layer (like MortgageIntelligence, but payoff-specific)
        from app.parsers.extractors.payoff_intelligence import PayoffIntelligence
        result["intelligence"] = PayoffIntelligence().analyze(result).to_dict()

        # Normalize for UI and attach config
        # Normalize like bank analyzer
        self._normalize_intelligence_for_ui(result)

        # Build new-format UI config
        result["ui_config"] = self.build_payoff_ui_config(result)

        # Build old-format UI schema (compatibility)
        result["ui_schema"] = PayoffStatementAnalyzer.adapt_ui_config_to_ui_schema(result["ui_config"])

        result["has_advanced_analytics"] = True

        logger.info("✅ Payoff statement analysis complete with intelligence")
        return result

    # ---------------- SAFE PARSING ----------------

    @staticmethod
    def safe_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return float(value)
            v = str(value).replace(",", "").replace("$", "").strip()
            return float(v) if v else default
        except Exception:
            return default

    @staticmethod
    def safe_date(value) -> Optional[str]:
        if not value:
            return None
        if isinstance(value, (datetime, date)):
            return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        s = str(value).strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except Exception:
                continue
        return None

    # ---------------- EXTRACTION ----------------

    def _extract_payoff(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        New payoff extraction pipeline:
          1. Use layered payoff extractor (form fields → pdfplumber → OCR)
          2. Enrich with metadata when available
          3. Normalize + return structured payoff fields
        """
        from app.services.parsers.payoff_extractor import extract_payoff_values

        # -------------------------------
        # 1. Extract numeric payoff values
        # -------------------------------
        vals = extract_payoff_values(file_path)

        # Base parsed dict from extractor
        parsed: Dict[str, Any] = {
            "payoff_amount": vals.payoff_amount,
            "principal_balance": vals.principal_balance,
            "interest_due": vals.interest_due,
            "total_fees": vals.total_fees,
            "escrow_balance": vals.escrow_balance,
            "suspense_balance": vals.suspense_balance,
        }

        # -------------------------------
        # 2. Metadata enrichment
        # -------------------------------
        if metadata:
            for key in [
                "loan_number", "borrower_name", "lender_name", "property_address",
                "payoff_amount", "per_diem", "good_through_date", "statement_date",
                "escrow_balance", "suspense_balance", "late_fees", "other_fees",
            ]:
                if metadata.get(key) is not None and parsed.get(key) in (None, 0, 0.0):
                    parsed[key] = metadata[key]

        # -------------------------------
        # 3. Return normalized payoff dict
        # -------------------------------
        return {
            "loan_number": parsed.get("loan_number"),
            "borrower_name": parsed.get("borrower_name"),
            "lender_name": parsed.get("lender_name"),
            "property_address": parsed.get("property_address"),

            "payoff_amount": self.safe_float(parsed.get("payoff_amount")),
            "principal_balance": self.safe_float(parsed.get("principal_balance")),
            "interest_due": self.safe_float(parsed.get("interest_due")),
            "per_diem": self.safe_float(parsed.get("per_diem")),
            "late_fees": self.safe_float(parsed.get("late_fees")),
            "other_fees": self.safe_float(parsed.get("other_fees")),
            "escrow_balance": self.safe_float(parsed.get("escrow_balance")),
            "suspense_balance": self.safe_float(parsed.get("suspense_balance")),

            "good_through_date": self.safe_date(parsed.get("good_through_date")),
            "statement_date": self.safe_date(parsed.get("statement_date")),
            "generated_at": self.safe_date(parsed.get("generated_at")),

            # These flags may come from metadata or future parsing
            "wiring_instructions_present": bool(parsed.get("wiring_instructions_present", False)),
            "mailing_instructions_present": bool(parsed.get("mailing_instructions_present", False)),
            "multiple_payoff_options": bool(parsed.get("multiple_payoff_options", False)),
        }

    # ---------------- METRICS ----------------

    def _payoff_metrics(self, p: Dict[str, Any]) -> Dict[str, Any]:
        payoff = p.get("payoff_amount", 0.0)
        principal = p.get("principal_balance", 0.0)
        interest = p.get("interest_due", 0.0)
        late_fees = p.get("late_fees", 0.0)
        other_fees = p.get("other_fees", 0.0)
        escrow = p.get("escrow_balance", 0.0)
        suspense = p.get("suspense_balance", 0.0)

        fee_total = late_fees + other_fees
        non_principal = payoff - principal
        fee_ratio = (fee_total / payoff) if payoff > 0 else 0.0

        return {
            "payoff_amount": round(payoff, 2),
            "principal_balance": round(principal, 2),
            "interest_due": round(interest, 2),
            "late_fees": round(late_fees, 2),
            "other_fees": round(other_fees, 2),
            "total_fees": round(fee_total, 2),
            "escrow_balance": round(escrow, 2),
            "suspense_balance": round(suspense, 2),
            "non_principal_amount": round(non_principal, 2),
            "fee_ratio": round(fee_ratio, 4),
        }

    def _timing_metrics(self, p: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        good_through = p.get("good_through_date")
        today = date.today()

        days_remaining = None
        expired = False

        if good_through:
            try:
                d = datetime.strptime(good_through, "%Y-%m-%d").date()
                days_remaining = (d - today).days
                expired = days_remaining < 0
            except Exception:
                pass

        return {
            "days_until_expiration": days_remaining,
            "is_expired": expired,
        }

    # ---------------- RISK ----------------

    def _risk_metrics(self, p: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []
        flags: Dict[str, bool] = {
            "expired_quote": False,
            "short_window": False,
            "high_fee_ratio": False,
            "missing_wiring": False,
            "ambiguous_instructions": False,
        }

        days = self._safe_int(metrics.get("days_until_expiration"))
        fee_ratio = metrics.get("fee_ratio", 0.0)

        if metrics.get("payoff_amount", 0.0) <= 0:
            risks.append("Payoff amount is zero or missing — statement may be incomplete.")
            flags["ambiguous_instructions"] = True

        if days is not None:
            if days < 0:
                risks.append("Payoff quote has expired — request an updated payoff statement.")
                flags["expired_quote"] = True
            elif days <= 3:
                risks.append(f"Payoff quote expires in {days} day(s) — wire funds promptly to avoid recalculation.")
                flags["short_window"] = True

        if fee_ratio > 0.05:
            risks.append("Fees represent more than 5% of the payoff amount — review fee breakdown carefully.")
            flags["high_fee_ratio"] = True

        if not p.get("wiring_instructions_present"):
            risks.append("No clear wiring instructions detected — confirm routing and account details with lender.")
            flags["missing_wiring"] = True

        if p.get("multiple_payoff_options"):
            risks.append("Multiple payoff options detected — confirm which amount and date apply to your transaction.")
            flags["ambiguous_instructions"] = True

        return {
            "payoff_risks": risks,
            "payoff_risk_count": len(risks),
            "payoff_risk_flags": flags,
        }

    @staticmethod
    def _safe_int(value) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except Exception:
            return None

    # ---------------- SUMMARY / INSIGHTS ----------------

    def _generate_summary(self, p: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        borrower = p.get("borrower_name") or "Borrower"
        payoff = metrics.get("payoff_amount", 0.0)
        good_through = p.get("good_through_date") or "N/A"
        return f"Payoff statement for {borrower} with payoff amount ${payoff:,.2f}, good through {good_through}."

    def _generate_insights(
        self,
        p: Dict[str, Any],
        metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
        timing_metrics: Dict[str, Any],
    ) -> List[str]:
        insights: List[str] = []

        insights.append(f"Payoff amount: ${metrics.get('payoff_amount', 0.0):,.2f}")
        insights.append(f"Principal balance: ${metrics.get('principal_balance', 0.0):,.2f}")
        insights.append(f"Total fees: ${metrics.get('total_fees', 0.0):,.2f}")
        if timing_metrics.get("days_until_expiration") is not None:
            insights.append(f"Days until payoff quote expiration: {timing_metrics['days_until_expiration']}")

        if risk_metrics.get("payoff_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['payoff_risk_count']}.")

        return insights

    def _generate_recommendations(
        self,
        p: Dict[str, Any],
        metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
        timing_metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for r in risk_metrics.get("payoff_risks", []):
            recs.append({
                "priority": "medium",
                "category": "risk",
                "title": "Review Payoff Risk",
                "description": r,
                "potential_savings": 0.0,
            })

        days = timing_metrics.get("days_until_expiration")
        if days is not None and days <= 3:
            recs.append({
                "priority": "high",
                "category": "timing",
                "title": "Confirm Funding Timeline",
                "description": "Coordinate with escrow/title to ensure funds are wired before the payoff quote expires.",
                "potential_savings": 0.0,
            })

        if not p.get("wiring_instructions_present"):
            recs.append({
                "priority": "high",
                "category": "operations",
                "title": "Confirm Wiring Instructions",
                "description": "Obtain written wiring instructions directly from the lender to avoid misdirected funds.",
                "potential_savings": 0.0,
            })

        return recs

    # ---------------- INTELLIGENCE NORMALIZATION ----------------

    def _normalize_intelligence_for_ui(self, result: Dict[str, Any]) -> None:
        intel = result.get("intelligence") or {}
        if not intel:
            return

        def str_list_to_rows(val):
            if isinstance(val, list) and val and isinstance(val[0], str):
                return [{"key": str(i + 1), "value": v} for i, v in enumerate(val)]
            return val

        def dict_to_rows(d):
            if isinstance(d, dict):
                return [{"key": str(k).replace("_", " ").title(), "value": str(v)} for k, v in d.items() if v is not None]
            return d

        # Normalize common sections
        for field in ["key_insights", "risk_flags"]:
            val = intel.get(field)
            if isinstance(val, list):
                intel[field] = str_list_to_rows(val)

        for field in ["cost_analysis", "timing_analysis", "wire_risk", "score"]:
            val = intel.get(field)
            if isinstance(val, dict):
                intel[field] = dict_to_rows(val)

        ai = intel.get("action_items")
        if isinstance(ai, dict):
            for sub in ["immediate", "short_term", "questions_to_ask"]:
                sub_val = ai.get(sub)
                if isinstance(sub_val, list) and sub_val and isinstance(sub_val[0], str):
                    ai[sub] = [{"key": str(i + 1), "value": v} for i, v in enumerate(sub_val)]

    # ---------------- UI CONFIG ----------------

    def build_payoff_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero = [
            {"key": "payoff_amount",        "label": "Payoff Amount",      "value_path": "payoff_amount",        "is_hero": True},
            {"key": "principal_balance",    "label": "Principal Balance",  "value_path": "principal_balance",    "is_hero": True},
            {"key": "interest_due",         "label": "Interest Due",       "value_path": "interest_due",         "is_hero": True},
            {"key": "total_fees",           "label": "Total Fees",         "value_path": "total_fees",           "is_hero": True},
            {"key": "good_through_date",    "label": "Good Through",       "value_path": "good_through_date",    "is_hero": True},
            {"key": "days_until_expiration","label": "Days Until Expiry",  "value_path": "days_until_expiration","is_hero": False},
            {"key": "payoff_risk_count",    "label": "Risk Flags",         "value_path": "payoff_risk_count",    "is_hero": True},
        ]

        tables = [
            {"id": "key_insights",   "title": "Key Insights",      "data_path": "intelligence.key_insights"},
            {"id": "payoff_risks",   "title": "Risk Flags",        "data_path": "payoff_risks"},
            {"id": "action_items",   "title": "Action Plan",       "data_path": "intelligence.action_items.immediate"},
            {"id": "cost_analysis",  "title": "Cost Breakdown",    "data_path": "intelligence.cost_analysis"},
            {"id": "timing",         "title": "Timing Analysis",   "data_path": "intelligence.timing_analysis"},
            {"id": "wire_risk",      "title": "Wire & Ops Risk",   "data_path": "intelligence.wire_risk"},
            {"id": "score",          "title": "Payoff Score",      "data_path": "intelligence.score"},
        ]

        sections = [
            {"id": "summary",  "title": "Payoff Summary",
             "metrics": ["payoff_amount", "principal_balance", "interest_due", "total_fees", "good_through_date", "days_until_expiration", "payoff_risk_count"],
             "charts": [], "tables": []},
            {"id": "insights", "title": "Key Insights",  "metrics": [], "charts": [], "tables": ["key_insights"]},
            {"id": "cost",     "title": "Cost Analysis", "metrics": [], "charts": [], "tables": ["cost_analysis"]},
            {"id": "timing",   "title": "Timing & Expiration", "metrics": [], "charts": [], "tables": ["timing"]},
            {"id": "wire",     "title": "Wire & Operational Risk", "metrics": [], "charts": [], "tables": ["wire_risk"]},
            {"id": "risk",     "title": "Risk & Actions", "metrics": ["payoff_risk_count"], "charts": [], "tables": ["payoff_risks", "action_items", "score"]},
        ]

        return {"hero_metrics": hero, "charts": [], "tables": tables, "sections": sections}
