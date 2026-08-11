# app/services/analyzers/real_estate/mortgage_document_analyzer.py

import logging
from typing import Dict, Any, List
from datetime import datetime
from app.parsers.extractors.mortgage_intelligence import MortgageIntelligence


from ..base_analyzer import BaseAnalyzer
from ..financial.base_financial_analyzer import BaseFinancialAnalyzer

logger = logging.getLogger(__name__)


class MortgageDocumentAnalyzer(BaseFinancialAnalyzer):

    def _normalize_intelligence_for_ui(self, result: Dict[str, Any]) -> None:
        intel = result.get("intelligence", {})
        if not intel:
            return

        doc_subtype = result.get("doc_subtype", "")
        is_le = doc_subtype in ("loan_estimate", "closing_disclosure")

        def str_list_to_rows(val):
            if isinstance(val, list) and val and isinstance(val[0], str):
                return [{"key": str(i + 1), "value": v} for i, v in enumerate(val)]
            return val

        def dict_to_rows(d):
            if isinstance(d, dict):
                return [{"key": str(k).replace("_", " ").title(), "value": str(v)} for k, v in d.items() if v is not None]
            return d

        if is_le:
            ra = intel.get("rate_analysis", {})
            if isinstance(ra, dict) and ra:
                intel["rate_analysis"] = [
                    {"key": "Your Rate",                 "value": f"{ra.get('your_rate', 0):.3f}%"},
                    {"key": "Market Rate",               "value": f"{ra.get('market_rate', 0):.2f}%"},
                    {"key": "Rate vs Market",            "value": f"{ra.get('rate_vs_market', 0):+.3f}%"},
                    {"key": "Assessment",                "value": str(ra.get('assessment', '')).replace('_', ' ').title()},
                    {"key": "Monthly P&I",               "value": f"${ra.get('monthly_pi', 0):,.2f}"},
                    {"key": "Total Interest (30yr)",     "value": f"${ra.get('total_interest_30yr', 0):,.0f}"},
                    {"key": "Monthly Savings at Market", "value": f"${ra.get('monthly_savings_at_market', 0):,.2f}"},
                    {"key": "Lifetime Savings",          "value": f"${ra.get('lifetime_savings_at_market', 0):,.0f}"},
                ]

            cc = intel.get("closing_cost_analysis", {})
            if isinstance(cc, dict) and cc:
                intel["closing_cost_analysis"] = [
                    {"key": "Total Closing Costs",         "value": f"${cc.get('total_closing_costs', 0):,.2f}"},
                    {"key": "As % of Loan",                "value": f"{cc.get('as_pct_of_loan', 0):.2f}%"},
                    {"key": "Assessment",                  "value": str(cc.get('assessment', '')).title()},
                    {"key": "A. Origination Charges",      "value": f"${cc.get('origination_charges', 0):,.2f}"},
                    {"key": "B. Services (No Shop)",       "value": f"${cc.get('services_no_shop', 0):,.2f}"},
                    {"key": "C. Services (Can Shop)",      "value": f"${cc.get('services_can_shop', 0):,.2f}"},
                    {"key": "Other Costs",                 "value": f"${cc.get('other_costs', 0):,.2f}"},
                    {"key": "Lender Credits",              "value": f"-${cc.get('lender_credits', 0):,.2f}"},
                    {"key": "Net Closing Costs",           "value": f"${cc.get('net_closing_costs', 0):,.2f}"},
                    {"key": "Shoppable Savings Potential", "value": f"~${cc.get('shoppable_savings_potential', 0):,.2f}"},
                ]

            mc = intel.get("monthly_cost", {})
            if isinstance(mc, dict) and mc:
                intel["monthly_cost"] = [
                    {"key": "Principal & Interest",    "value": f"${mc.get('principal_and_interest', 0):,.2f}"},
                    {"key": "Estimated Escrow",        "value": f"${mc.get('estimated_escrow', 0):,.2f}"},
                    {"key": "Mortgage Insurance",      "value": f"${mc.get('mortgage_insurance', 0):,.2f}"},
                    {"key": "Estimated Total Monthly", "value": f"${mc.get('estimated_total', 0):,.2f}"},
                    {"key": "Escrow as % of Total",    "value": f"{mc.get('escrow_as_pct', 0):.1f}%"},
                ]

            ctc = intel.get("cash_to_close", {})
            if isinstance(ctc, dict) and ctc:
                intel["cash_to_close"] = [
                    {"key": "Cash to Close",     "value": f"${ctc.get('cash_to_close', 0):,.2f}"},
                    {"key": "Closing Costs",     "value": f"${ctc.get('closing_costs', 0):,.2f}"},
                    {"key": "Lender Credits",    "value": f"-${ctc.get('lender_credits', 0):,.2f}"},
                    {"key": "Net Out of Pocket", "value": f"${ctc.get('net_out_of_pocket', 0):,.2f}"},
                ]

            rf = intel.get("risk_flags", {})
            if isinstance(rf, dict):
                flags = rf.get("flags", [])
                intel["risk_flags"] = [{"key": str(i + 1), "value": f} for i, f in enumerate(flags)]
            elif isinstance(rf, list):
                intel["risk_flags"] = str_list_to_rows(rf) or rf

            ai = intel.get("action_items", {})
            if isinstance(ai, dict):
                questions = ai.get("questions_to_ask", [])
                if isinstance(questions, list):
                    ai["questions_to_ask"] = [
                        {"key": str(i + 1), "value": q}
                        for i, q in enumerate(questions) if isinstance(q, str)
                    ]

            ki = intel.get("key_insights", [])
            if isinstance(ki, list) and ki:
                if isinstance(ki[0], str):
                    intel["key_insights_table"] = [{"key": str(i + 1), "value": v} for i, v in enumerate(ki)]
                elif isinstance(ki[0], dict) and "value" in ki[0]:
                    intel["key_insights_table"] = ki
                    intel["key_insights"] = [item["value"] for item in ki]

        else:
            # Standard mortgage / statement — normalize all intelligence fields to rows
            for field in ["risk_flags", "key_insights"]:
                val = intel.get(field, [])
                if isinstance(val, list):
                    converted = str_list_to_rows(val)
                    if converted is not val:
                        intel[field] = converted

            # Normalize dict-based intelligence sections to rows
            for field in ["cost_analysis", "rate_analysis", "amortization", "tax_impact", "refinance"]:
                val = intel.get(field)
                if isinstance(val, dict) and val:
                    intel[field] = dict_to_rows(val)
                elif isinstance(val, list) and val and isinstance(val[0], str):
                    intel[field] = str_list_to_rows(val)

            # action_items: normalize each sub-list
            ai = intel.get("action_items", {})
            if isinstance(ai, dict):
                for sub in ["immediate", "before_closing", "questions_to_ask"]:
                    sub_val = ai.get(sub, [])
                    if isinstance(sub_val, list) and sub_val and isinstance(sub_val[0], str):
                        ai[sub] = [{"key": str(i + 1), "value": v} for i, v in enumerate(sub_val)]
                    elif isinstance(sub_val, list) and sub_val and isinstance(sub_val[0], dict):
                        # already has title/detail/priority etc — pass through for action table
                        pass
            elif isinstance(ai, list):
                intel["action_items"] = str_list_to_rows(ai) or ai


    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🏦 Mortgage document analysis: {file_path}")

        m = self._extract_mortgage(file_path, text, metadata)

        loan_metrics = self._loan_metrics(m)
        amort_metrics = self._amortization_metrics(loan_metrics, m)
        risk_metrics = self._risk_metrics(m, loan_metrics)

        insights = self._generate_insights(m, loan_metrics, amort_metrics, risk_metrics)
        recommendations = self._generate_recommendations(m, loan_metrics, risk_metrics)

        result = {
            "type": "mortgage_document",
            "summary": self._generate_summary(m, loan_metrics),
            "confidence": 0.95,
            "loan_number": m.get("loan_number"),
            "borrower_name": m.get("borrower_name"),
            "lender_name": m.get("lender_name"),
            "property_address": m.get("property_address"),
            "loan_date": m.get("loan_date"),
            **loan_metrics,
            **amort_metrics,
            **risk_metrics,
            "insights": insights,
            "recommendations": recommendations,
        }

        result["doc_subtype"] = m.get("doc_subtype") or ""
        result["closing_costs"] = m.get("closing_costs")
        result["cash_to_close"] = m.get("cash_to_close")
        result["lender_credits"] = m.get("lender_credits")
        result["escrow_payment"] = m.get("escrow_payment")
        result["estimated_total_monthly"] = m.get("estimated_total_monthly")
        result["loan_purpose"] = m.get("loan_purpose") or ""
        result["payment_due"] = m.get("payment_due")
        result["escrow_balance"] = m.get("escrow_balance")
        result["next_payment_date"] = m.get("next_payment_date")
        result["maturity_date"] = m.get("maturity_date")
        result["current_balance"] = m.get("current_balance")
        result["ytd_interest"] = m.get("ytd_interest")
        result["payoff_amount"] = m.get("payoff_amount")
        result["payoff_date"] = m.get("payoff_date")

        result["ui_config"] = self.build_mortgage_ui_config(result)
        result["has_advanced_analytics"] = True

        doc_subtype = result["doc_subtype"]
        if doc_subtype in ("loan_estimate", "closing_disclosure"):
            from app.parsers.extractors.loan_estimate_intelligence import LoanEstimateIntelligence
            result["intelligence"] = LoanEstimateIntelligence().analyze(result).to_dict()
        else:
            from app.parsers.extractors.mortgage_intelligence import MortgageIntelligence
            result["intelligence"] = MortgageIntelligence().analyze(result).to_dict()

        self._normalize_intelligence_for_ui(result)
        return result

    def _extract_mortgage(self, file_path, text, metadata):
        parsed = {}

        if text:
            try:
                from app.services.parsers.universal_mortgage_parser import UniversalMortgageParser
                parsed = UniversalMortgageParser(file_path, text=text).parse() or {}
            except Exception as e:
                logger.warning(f"⚠️ UniversalMortgageParser failed: {e}")
                parsed = {}

        if metadata and not any([parsed.get("loan_amount"), parsed.get("interest_rate")]):
            for key in ["loan_amount", "interest_rate", "term_years", "monthly_payment",
                        "borrower_name", "lender_name", "loan_number"]:
                if metadata.get(key):
                    parsed[key] = metadata[key]

        return {
            "loan_number": parsed.get("loan_number"),
            "borrower_name": parsed.get("borrower_name"),
            "lender_name": parsed.get("lender_name"),
            "property_address": parsed.get("property_address"),
            "loan_date": parsed.get("loan_date"),
            "doc_subtype": parsed.get("doc_subtype"),
            "loan_amount": self.safe_float(
                parsed.get("loan_amount") or
                parsed.get("current_balance") or
                (parsed.get("payment_due") if parsed.get("doc_subtype") == "monthly_statement" else None)
            ),
            "interest_rate": self.safe_float(parsed.get("interest_rate")),
            "term_years": self.safe_float(parsed.get("term_years")),
            "monthly_payment": self.safe_float(parsed.get("monthly_payment")),
            "apr": self.safe_float(parsed.get("apr")),
            "balloon_payment": self.safe_float(parsed.get("balloon_payment")),
            "rate_type": parsed.get("rate_type"),
            "current_balance": self.safe_float(parsed.get("current_balance")),
            "escrow_balance": self.safe_float(parsed.get("escrow_balance")),
            "next_payment_date": parsed.get("next_payment_date"),
            "ytd_interest": self.safe_float(parsed.get("ytd_interest")),
            "ytd_taxes": self.safe_float(parsed.get("ytd_taxes")),
            "payoff_amount": self.safe_float(parsed.get("payoff_amount")),
            "payoff_date": parsed.get("payoff_date"),
            "per_diem": self.safe_float(parsed.get("per_diem")),
            "payment_due": self.safe_float(parsed.get("payment_due")),
            "maturity_date": parsed.get("maturity_date"),
            "closing_costs": self.safe_float(parsed.get("closing_costs")),
            "cash_to_close": self.safe_float(parsed.get("cash_to_close")),
            "lender_credits": self.safe_float(parsed.get("lender_credits")),
            "origination_charges": self.safe_float(parsed.get("origination_charges")),
            "services_no_shop": self.safe_float(parsed.get("services_no_shop")),
            "services_can_shop": self.safe_float(parsed.get("services_can_shop")),
            "other_costs": self.safe_float(parsed.get("other_costs")),
            "escrow_payment": self.safe_float(parsed.get("escrow_payment")),
            "estimated_total_monthly": self.safe_float(parsed.get("estimated_total_monthly")),
            "loan_purpose": parsed.get("loan_purpose"),
        }

    def _loan_metrics(self, m: Dict[str, Any]) -> Dict[str, Any]:
        loan = m.get("loan_amount", 0.0)
        rate = m.get("interest_rate", 0.0)
        term = int(m.get("term_years", 0))
        monthly = m.get("monthly_payment", None)

        return {
            "loan_amount": round(loan, 2),
            "interest_rate": round(rate, 3),
            "term_years": term,
            "monthly_payment": round(monthly, 2) if monthly is not None else None,
            "apr": m.get("apr"),
            "rate_type": m.get("rate_type"),
            "balloon_payment": round(m.get("balloon_payment", 0.0), 2),
        }

    def _amortization_metrics(self, loan_metrics: Dict[str, Any], m: Dict[str, Any] = None) -> Dict[str, Any]:
        doc_subtype = (m or {}).get("doc_subtype", "")
        loan = loan_metrics.get("loan_amount", 0.0)
        term = loan_metrics.get("term_years", 0)
        monthly_payment = loan_metrics.get("monthly_payment")

        # For monthly statements, loan_amount is often just the payment due — skip total interest
        if doc_subtype == "monthly_statement" or loan < 10000:
            return {
                "total_interest_estimate": None,
                "monthly_payment": monthly_payment,
            }

        total_interest = None
        if term > 0 and monthly_payment:
            months = term * 12
            total_paid = monthly_payment * months
            total_interest = round(total_paid - loan, 2)

        return {
            "total_interest_estimate": total_interest,
            "monthly_payment": monthly_payment,
        }

    def _risk_metrics(self, m: Dict[str, Any], loan_metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks = []
        if loan_metrics.get("apr") is None and loan_metrics.get("interest_rate") is not None:
            risks.append("APR missing; borrower may not have full cost disclosure.")
        if loan_metrics.get("rate_type") and str(loan_metrics.get("rate_type")).lower() == "variable":
            risks.append("Variable rate loan; payment may change over time.")
        if loan_metrics.get("balloon_payment", 0) > 0:
            risks.append("Balloon payment present; large future payment required.")
        return {
            "mortgage_risks": risks,
            "mortgage_risk_count": len(risks),
        }

    def _generate_summary(self, m, loan_metrics) -> str:
        borrower = m.get("borrower_name", "Borrower")
        loan = loan_metrics.get("loan_amount", 0.0)
        return f"Mortgage for {borrower} with loan amount ${loan:,.2f}."

    def _generate_insights(self, m, loan_metrics, amort_metrics, risk_metrics) -> List[str]:
        insights = []
        insights.append(f"Loan amount: ${loan_metrics.get('loan_amount', 0.0):,.2f}")
        if amort_metrics.get("total_interest_estimate") is not None:
            insights.append(f"Estimated total interest: ${amort_metrics['total_interest_estimate']:,.2f}")
        if risk_metrics.get("mortgage_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['mortgage_risk_count']}.")
        return insights

    def _generate_recommendations(self, m, loan_metrics, risk_metrics) -> List[Dict[str, Any]]:
        recs = []
        for r in risk_metrics.get("mortgage_risks", []):
            recs.append({
                "priority": "medium",
                "category": "disclosure",
                "title": "Review Mortgage Risk",
                "description": r,
                "potential_savings": 0,
            })
        if loan_metrics.get("apr") is None:
            recs.append({
                "priority": "high",
                "category": "compliance",
                "title": "Confirm APR Disclosure",
                "description": "APR missing; ensure full cost disclosure to borrower.",
                "potential_savings": 0,
            })
        return recs

    def build_mortgage_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        doc_subtype = result.get("doc_subtype", "")
        is_le = doc_subtype in ("loan_estimate", "closing_disclosure")
        is_statement = doc_subtype == "monthly_statement"

        # ⭐ NEW: Payoff statement UI mode (must come BEFORE is_le / is_statement)
        if result.get("type") == "payoff_statement" or doc_subtype == "payoff_statement":
            logger.info("calling build_payoff_ui_config to build payoff ui")
            from app.services.analyzers.real_estate.payoff_statement_analyzer import PayoffStatementAnalyzer
            return PayoffStatementAnalyzer().build_payoff_ui_config(result)

        if is_le:
            hero_metrics = [
                {"key": "loan_amount",     "label": "Loan Amount",       "value_path": "loan_amount",     "is_hero": True},
                {"key": "interest_rate",   "label": "Interest Rate (%)", "value_path": "interest_rate",   "is_hero": True},
                {"key": "monthly_payment", "label": "Monthly P&I",       "value_path": "monthly_payment", "is_hero": True},
                {"key": "closing_costs",   "label": "Closing Costs",     "value_path": "closing_costs",   "is_hero": True},
                {"key": "cash_to_close",   "label": "Cash to Close",     "value_path": "cash_to_close",   "is_hero": False},
                {"key": "lender_credits",  "label": "Lender Credits",    "value_path": "lender_credits",  "is_hero": False},
                {"key": "term_years",      "label": "Term (Years)",      "value_path": "term_years",      "is_hero": False},
                {"key": "rate_type",       "label": "Rate Type",         "value_path": "rate_type",       "is_hero": False},
                {"key": "loan_purpose",    "label": "Purpose",           "value_path": "loan_purpose",    "is_hero": False},
            ]
            tables = [
                {"id": "key_insights_table", "title": "Key Insights",          "data_path": "intelligence.key_insights_table"},
                {"id": "rate_analysis",      "title": "Rate Analysis",         "data_path": "intelligence.rate_analysis"},
                {"id": "closing_costs",      "title": "Closing Cost Breakdown","data_path": "intelligence.closing_cost_analysis"},
                {"id": "monthly_cost",       "title": "Monthly Cost",          "data_path": "intelligence.monthly_cost"},
                {"id": "action_immediate",   "title": "Immediate Actions",     "data_path": "intelligence.action_items.immediate"},
                {"id": "action_closing",     "title": "Before Closing",        "data_path": "intelligence.action_items.before_closing"},
                {"id": "questions",          "title": "Questions to Ask",      "data_path": "intelligence.action_items.questions_to_ask"},
                {"id": "risk_flags",         "title": "Risk Flags",            "data_path": "intelligence.risk_flags"},
                {"id": "mortgage_risks",     "title": "Document Risks",        "data_path": "mortgage_risks"},
            ]
            sections = [
                {"id": "summary",   "title": "Loan Summary",
                 "metrics": ["loan_amount", "interest_rate", "monthly_payment", "closing_costs", "cash_to_close", "lender_credits"],
                 "charts": [], "tables": []},
                {"id": "insights",  "title": "Key Insights",           "metrics": [], "charts": [], "tables": ["key_insights_table"]},
                {"id": "rate",      "title": "Rate Analysis",          "metrics": [], "charts": [], "tables": ["rate_analysis"]},
                {"id": "costs",     "title": "Closing Cost Breakdown", "metrics": [], "charts": [], "tables": ["closing_costs"]},
                {"id": "monthly",   "title": "Monthly Cost",           "metrics": [], "charts": [], "tables": ["monthly_cost"]},
                {"id": "actions",   "title": "Action Plan",            "metrics": [], "charts": [], "tables": ["action_immediate", "action_closing"]},
                {"id": "questions", "title": "Questions to Ask",       "metrics": [], "charts": [], "tables": ["questions"]},
                {"id": "risk",      "title": "Risk Flags",             "metrics": [], "charts": [], "tables": ["risk_flags", "mortgage_risks"]},
            ]

        elif is_statement:
            hero_metrics = [
                {"key": "payment_due",       "label": "Amount Due",        "value_path": "payment_due",       "is_hero": True},
                {"key": "interest_rate",     "label": "Interest Rate (%)", "value_path": "interest_rate",     "is_hero": True},
                {"key": "monthly_payment",   "label": "Monthly Payment",   "value_path": "monthly_payment",   "is_hero": True},
                {"key": "escrow_balance",    "label": "Escrow Balance",    "value_path": "escrow_balance",    "is_hero": True},
                {"key": "next_payment_date", "label": "Next Due Date",     "value_path": "next_payment_date", "is_hero": True},
                {"key": "loan_amount",       "label": "Loan Amount",       "value_path": "loan_amount",       "is_hero": False},
                {"key": "maturity_date",     "label": "Maturity Date",     "value_path": "maturity_date",     "is_hero": False},
                {"key": "ytd_interest",      "label": "YTD Interest",      "value_path": "ytd_interest",      "is_hero": False},
            ]
            tables = [
                {"id": "key_insights",   "title": "Key Insights",   "data_path": "intelligence.key_insights"},
                {"id": "mortgage_risks", "title": "Risk Flags",      "data_path": "mortgage_risks"},
                {"id": "action_items",   "title": "Action Plan",     "data_path": "intelligence.action_items"},
                {"id": "cost_analysis",  "title": "Cost Analysis",   "data_path": "intelligence.cost_analysis"},
                {"id": "rate_analysis",  "title": "Rate Analysis",   "data_path": "intelligence.rate_analysis"},
            ]
            sections = [
                {"id": "summary", "title": "Statement Summary",
                 "metrics": ["payment_due", "interest_rate", "monthly_payment", "escrow_balance", "next_payment_date"],
                 "charts": [], "tables": []},
                {"id": "insights", "title": "Key Insights",  "metrics": [], "charts": [], "tables": ["key_insights"]},
                {"id": "cost",     "title": "Cost Analysis", "metrics": [], "charts": [], "tables": ["cost_analysis", "rate_analysis"]},
                {"id": "risk",     "title": "Risks",         "metrics": [], "charts": [], "tables": ["mortgage_risks", "action_items"]},
            ]

        else:
            hero_metrics = [
                {"key": "loan_amount",             "label": "Loan Amount",       "value_path": "loan_amount",             "is_hero": True},
                {"key": "interest_rate",           "label": "Interest Rate (%)", "value_path": "interest_rate",           "is_hero": True},
                {"key": "monthly_payment",         "label": "Monthly Payment",   "value_path": "monthly_payment",         "is_hero": True},
                {"key": "total_interest_estimate", "label": "Total Interest",    "value_path": "total_interest_estimate", "is_hero": True},
                {"key": "mortgage_risk_count",     "label": "Risk Flags",        "value_path": "mortgage_risk_count",     "is_hero": True},
                {"key": "term_years",              "label": "Term (Years)",      "value_path": "term_years",              "is_hero": False},
                {"key": "apr",                     "label": "APR",               "value_path": "apr",                     "is_hero": False},
                {"key": "rate_type",               "label": "Rate Type",         "value_path": "rate_type",               "is_hero": False},
            ]
            tables = [
                {"id": "mortgage_risks",  "title": "Risk Flags",         "data_path": "mortgage_risks"},
                {"id": "recommendations", "title": "Recommendations",    "data_path": "recommendations"},
                {"id": "key_insights",    "title": "Key Insights",       "data_path": "intelligence.key_insights"},
                {"id": "action_items",    "title": "Action Plan",        "data_path": "intelligence.action_items"},
                {"id": "cost_analysis",   "title": "Loan Cost Analysis", "data_path": "intelligence.cost_analysis"},
                {"id": "rate_analysis",   "title": "Rate Analysis",      "data_path": "intelligence.rate_analysis"},
                {"id": "amortization",    "title": "Amortization",       "data_path": "intelligence.amortization"},
                {"id": "tax_impact",      "title": "Tax Impact",         "data_path": "intelligence.tax_impact"},
                {"id": "refinance",       "title": "Refinance Analysis", "data_path": "intelligence.refinance"},
                {"id": "risk_flags",      "title": "Intelligence Risks", "data_path": "intelligence.risk_flags"},
            ]
            sections = [
                {"id": "summary",      "title": "Mortgage Summary",
                 "metrics": ["loan_amount", "interest_rate", "monthly_payment", "total_interest_estimate", "mortgage_risk_count"],
                 "charts": [], "tables": []},
                {"id": "intelligence", "title": "Loan Intelligence",   "metrics": [], "charts": [], "tables": ["cost_analysis", "rate_analysis"]},
                {"id": "amortization", "title": "Amortization",        "metrics": ["term_years"], "charts": [], "tables": ["amortization"]},
                {"id": "tax",          "title": "Tax Impact",          "metrics": [], "charts": [], "tables": ["tax_impact"]},
                {"id": "refinance",    "title": "Refinance Analysis",  "metrics": [], "charts": [], "tables": ["refinance"]},
                {"id": "risk",         "title": "Risk & Recommendations",
                 "metrics": ["mortgage_risk_count"], "charts": [],
                 "tables": ["mortgage_risks", "risk_flags", "recommendations", "action_items"]},
            ]

        return {"hero_metrics": hero_metrics, "charts": [], "tables": tables, "sections": sections}