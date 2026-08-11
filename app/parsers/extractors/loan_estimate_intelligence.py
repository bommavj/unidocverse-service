"""
Loan Estimate Intelligence Layer
app/parsers/extractors/loan_estimate_intelligence.py

Answers the key questions a borrower needs when reviewing a Loan Estimate:
  1. Is this a good rate vs current market?
  2. What are the total closing costs and are they reasonable?
  3. What is the true monthly cost (P&I + escrow)?
  4. Should I shop for better services?
  5. What are the risks in this loan?
  6. How does this compare to a different loan amount/rate scenario?
  7. What actions should I take before closing?
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ─── BENCHMARKS ──────────────────────────────────────────────────

RATE_BENCHMARKS = {
    "excellent": 6.5,
    "good":      7.0,
    "fair":      7.5,
    "poor":      8.5,
}

# Closing cost as % of loan amount — industry benchmarks
CLOSING_COST_PCT_BENCHMARK = 0.03  # 3% is typical

# Shoppable services benchmark (section C) — should be under this % of total closing costs
SHOPPABLE_SAVINGS_THRESHOLD = 0.20  # if >20% of closing costs are shoppable, worth shopping

@dataclass
class RateAnalysis:
    your_rate: float
    market_rate: float
    rate_vs_market: float
    assessment: str
    monthly_pi: float
    total_interest_30yr: float
    monthly_savings_at_market: float
    lifetime_savings_at_market: float

@dataclass
class ClosingCostAnalysis:
    total_closing_costs: float
    as_pct_of_loan: float
    benchmark_pct: float
    assessment: str          # low/typical/high/very_high
    origination_charges: float
    services_no_shop: float
    services_can_shop: float
    other_costs: float
    lender_credits: float
    net_closing_costs: float
    shoppable_savings_potential: float
    shopping_recommended: bool

@dataclass
class MonthlyCostBreakdown:
    principal_and_interest: float
    estimated_escrow: float
    mortgage_insurance: float
    estimated_total: float
    escrow_as_pct: float

@dataclass
class CashToCloseAnalysis:
    cash_to_close: float
    closing_costs: float
    lender_credits: float
    net_out_of_pocket: float
    months_to_break_even: Optional[int]

@dataclass
class LoanRiskFlags:
    flags: List[str]
    prepayment_penalty: bool
    balloon_payment: bool
    is_arm: bool
    high_closing_costs: bool
    risk_level: str   # low/medium/high

@dataclass
class ActionItems:
    immediate: List[Dict]
    before_closing: List[Dict]
    questions_to_ask: List[str]

@dataclass
class LEIntelligenceReport:
    rate_analysis: RateAnalysis
    closing_cost_analysis: ClosingCostAnalysis
    monthly_cost: MonthlyCostBreakdown
    cash_to_close: CashToCloseAnalysis
    risk_flags: LoanRiskFlags
    action_items: ActionItems
    score: int
    score_grade: str
    score_label: str
    key_insights: List[str]

    def to_dict(self) -> Dict[str, Any]:
        import dataclasses
        def _convert(obj):
            if dataclasses.is_dataclass(obj):
                return {k: _convert(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, list):
                return [_convert(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            return obj
        return _convert(self)


class LoanEstimateIntelligence:

    def analyze(self, data: Dict[str, Any]) -> LEIntelligenceReport:
        try:
            rate    = self._rate_analysis(data)
            costs   = self._closing_cost_analysis(data)
            monthly = self._monthly_cost(data)
            ctc     = self._cash_to_close_analysis(data, costs)
            risks   = self._risk_flags(data, costs)
            actions = self._action_items(data, rate, costs, risks)
            score, grade, label = self._score(rate, costs, risks)
            insights = self._key_insights(data, rate, costs, monthly, ctc, risks, score)

            return LEIntelligenceReport(
                rate_analysis=rate,
                closing_cost_analysis=costs,
                monthly_cost=monthly,
                cash_to_close=ctc,
                risk_flags=risks,
                action_items=actions,
                score=score,
                score_grade=grade,
                score_label=label,
                key_insights=insights,
            )
        except Exception as e:
            logger.error(f"LoanEstimateIntelligence failed: {e}", exc_info=True)
            return self._empty_report()

    # ─── RATE ANALYSIS ───────────────────────────────────────────

    def _rate_analysis(self, data: Dict) -> RateAnalysis:
        rate    = float(data.get("interest_rate", 0) or 0)
        loan    = float(data.get("loan_amount", 0) or 0)
        term    = int(data.get("term_years", 30) or 30)
        monthly_pi = float(data.get("monthly_payment", 0) or 0)
        market  = RATE_BENCHMARKS["good"]
        vs_market = round(rate - market, 3)

        if vs_market <= -0.5:   assessment = "excellent"
        elif vs_market <= 0:    assessment = "good"
        elif vs_market <= 0.5:  assessment = "fair"
        elif vs_market <= 1.5:  assessment = "high"
        else:                   assessment = "very_high"

        months = term * 12
        total_interest = round(monthly_pi * months - loan, 2) if monthly_pi and loan else 0

        market_monthly = self._calc_payment(loan, market, term)
        monthly_savings = round(monthly_pi - market_monthly, 2) if monthly_pi and market_monthly else 0
        lifetime_savings = round(monthly_savings * months, 2)

        return RateAnalysis(
            your_rate=rate,
            market_rate=market,
            rate_vs_market=vs_market,
            assessment=assessment,
            monthly_pi=round(monthly_pi, 2),
            total_interest_30yr=total_interest,
            monthly_savings_at_market=monthly_savings,
            lifetime_savings_at_market=lifetime_savings,
        )

    # ─── CLOSING COST ANALYSIS ───────────────────────────────────

    def _closing_cost_analysis(self, data: Dict) -> ClosingCostAnalysis:
        loan        = float(data.get("loan_amount", 0) or 0)
        total_cc    = float(data.get("closing_costs", 0) or 0)
        orig        = float(data.get("origination_charges", 0) or 0)
        no_shop     = float(data.get("services_no_shop", 0) or 0)
        can_shop    = float(data.get("services_can_shop", 0) or 0)
        other       = float(data.get("other_costs", 0) or 0)
        credits     = float(data.get("lender_credits", 0) or 0)
        net_cc      = round(total_cc - credits, 2)
        pct         = round(total_cc / loan * 100, 2) if loan > 0 else 0

        if pct < 1.5:       assessment = "low"
        elif pct < 3.0:     assessment = "typical"
        elif pct < 4.5:     assessment = "high"
        else:               assessment = "very_high"

        shoppable_pct = can_shop / total_cc if total_cc > 0 else 0
        shopping_rec  = shoppable_pct > SHOPPABLE_SAVINGS_THRESHOLD and can_shop > 500

        return ClosingCostAnalysis(
            total_closing_costs=total_cc,
            as_pct_of_loan=pct,
            benchmark_pct=CLOSING_COST_PCT_BENCHMARK * 100,
            assessment=assessment,
            origination_charges=orig,
            services_no_shop=no_shop,
            services_can_shop=can_shop,
            other_costs=other,
            lender_credits=credits,
            net_closing_costs=net_cc,
            shoppable_savings_potential=round(can_shop * 0.15, 2),  # ~15% savings by shopping
            shopping_recommended=shopping_rec,
        )

    # ─── MONTHLY COST ────────────────────────────────────────────

    def _monthly_cost(self, data: Dict) -> MonthlyCostBreakdown:
        pi      = float(data.get("monthly_payment", 0) or 0)
        total   = float(data.get("estimated_total_monthly", 0) or 0)
        escrow  = float(data.get("escrow_payment", 0) or 0)

        # Estimate escrow from total - PI
        if total and pi and not escrow:
            escrow = round(total - pi, 2)

        # If no total, estimate
        if not total and pi:
            total = pi + escrow

        escrow_pct = round(escrow / total * 100, 1) if total > 0 else 0

        return MonthlyCostBreakdown(
            principal_and_interest=round(pi, 2),
            estimated_escrow=round(escrow, 2),
            mortgage_insurance=0,  # would need MI field
            estimated_total=round(total, 2),
            escrow_as_pct=escrow_pct,
        )

    # ─── CASH TO CLOSE ───────────────────────────────────────────

    def _cash_to_close_analysis(self, data: Dict, costs: ClosingCostAnalysis) -> CashToCloseAnalysis:
        ctc     = float(data.get("cash_to_close", 0) or 0)
        cc      = costs.total_closing_costs
        credits = costs.lender_credits
        net     = costs.net_closing_costs

        # Break-even: months to recoup closing costs via monthly savings
        monthly_pi = float(data.get("monthly_payment", 0) or 0)
        market_payment = self._calc_payment(
            float(data.get("loan_amount", 0) or 0),
            RATE_BENCHMARKS["good"],
            int(data.get("term_years", 30) or 30)
        )
        monthly_savings = round(monthly_pi - (market_payment or monthly_pi), 2) if market_payment else 0
        break_even = round(net / abs(monthly_savings)) if monthly_savings < 0 else None

        return CashToCloseAnalysis(
            cash_to_close=ctc,
            closing_costs=cc,
            lender_credits=credits,
            net_out_of_pocket=net,
            months_to_break_even=break_even,
        )

    # ─── RISK FLAGS ──────────────────────────────────────────────

    def _risk_flags(self, data: Dict, costs: ClosingCostAnalysis) -> LoanRiskFlags:
        flags = []
        prepayment = bool(data.get("prepayment_penalty"))
        balloon    = bool(data.get("balloon_payment") and float(data.get("balloon_payment") or 0) > 1000)
        is_arm     = str(data.get("rate_type", "") or "").lower() == "variable"
        high_cc    = costs.assessment in ("high", "very_high")

        if prepayment:
            flags.append("⚠️ Prepayment penalty — you'll be charged for paying off early")
        if balloon:
            flags.append(f"⚠️ Balloon payment of ${float(data.get('balloon_payment', 0)):,.0f} due at end of term")
        if is_arm:
            flags.append("⚠️ Adjustable rate — monthly payment will change when rate adjusts")
        if high_cc:
            flags.append(f"⚠️ Closing costs ({costs.as_pct_of_loan:.1f}% of loan) are above the typical 3%")
        if costs.shopping_recommended:
            flags.append(f"💡 ${costs.services_can_shop:,.0f} in shoppable services (Section C) — you could save ~${costs.shoppable_savings_potential:,.0f} by comparing providers")

        risk_level = "high" if len([f for f in flags if "⚠️" in f]) >= 2 else \
                     "medium" if flags else "low"

        return LoanRiskFlags(
            flags=flags,
            prepayment_penalty=prepayment,
            balloon_payment=balloon,
            is_arm=is_arm,
            high_closing_costs=high_cc,
            risk_level=risk_level,
        )

    # ─── ACTION ITEMS ────────────────────────────────────────────

    def _action_items(self, data, rate, costs, risks) -> ActionItems:
        immediate = []
        before_closing = []
        questions = []

        # Rate shopping
        if rate.assessment in ("high", "very_high"):
            immediate.append({
                "priority": "high",
                "title": "Shop for a better rate",
                "detail": f"Your rate of {rate.your_rate}% is {rate.rate_vs_market:.2f}% above market. "
                          f"Getting the market rate would save ${abs(rate.monthly_savings_at_market):,.0f}/month.",
                "monthly_impact": abs(rate.monthly_savings_at_market),
            })

        # Service shopping
        if costs.shopping_recommended:
            immediate.append({
                "priority": "high",
                "title": "Shop Section C services",
                "detail": f"${costs.services_can_shop:,.0f} in closing costs are for services you CAN shop for "
                          f"(title insurance, settlement, escrow). Get 3 quotes — potential savings: "
                          f"~${costs.shoppable_savings_potential:,.0f}.",
                "monthly_impact": 0,
            })

        # Lender credits
        if not data.get("lender_credits"):
            immediate.append({
                "priority": "medium",
                "title": "Ask about lender credits",
                "detail": "Lender credits can reduce your upfront closing costs in exchange for a slightly "
                          "higher rate. Ask your lender for a quote with lender credits.",
                "monthly_impact": 0,
            })

        # Lock rate
        before_closing.append({
            "priority": "high",
            "title": "Lock your interest rate",
            "detail": "Rates can change daily. Lock your rate as soon as you decide to proceed. "
                      "Ask for a 45-60 day lock to allow time for closing.",
            "monthly_impact": 0,
        })

        # Review Loan Estimate vs Closing Disclosure
        before_closing.append({
            "priority": "high",
            "title": "Compare Closing Disclosure carefully",
            "detail": "When you receive the Closing Disclosure (3 days before closing), compare every "
                      "line item to this Loan Estimate. Lenders cannot increase most fees.",
            "monthly_impact": 0,
        })

        if risks.prepayment_penalty:
            before_closing.append({
                "priority": "medium",
                "title": "Understand prepayment penalty terms",
                "detail": "Get full details on the prepayment penalty — when it applies, how long it lasts, "
                          "and the maximum amount. Consider negotiating its removal.",
                "monthly_impact": 0,
            })

        # Questions
        questions = [
            "What is the rate lock period and what does it cost to extend?",
            "Can I buy down the rate with points?",
            "Are there any other fees not shown on this Loan Estimate?",
            "What happens to my escrow account if I refinance later?",
        ]
        if costs.shopping_recommended:
            questions.append("Which title company do you recommend and why? Can I use my own?")
        if rate.assessment in ("high", "very_high"):
            questions.append("What rate can I get with 1 point paid upfront?")

        return ActionItems(
            immediate=immediate,
            before_closing=before_closing,
            questions_to_ask=questions,
        )

    # ─── SCORE ───────────────────────────────────────────────────

    def _score(self, rate, costs, risks) -> tuple:
        score = 100

        # Rate (40 pts)
        rate_map = {"excellent": 0, "good": 5, "fair": 15, "high": 30, "very_high": 40}
        score -= rate_map.get(rate.assessment, 20)

        # Closing costs (30 pts)
        cc_map = {"low": 0, "typical": 5, "high": 20, "very_high": 30}
        score -= cc_map.get(costs.assessment, 10)

        # Risks (30 pts)
        score -= min(len([f for f in risks.flags if "⚠️" in f]) * 10, 30)

        score = max(min(score, 100), 0)

        if score >= 80:   return score, "A", "Excellent loan terms"
        elif score >= 65: return score, "B", "Good terms — minor concerns"
        elif score >= 50: return score, "C", "Fair — review carefully before signing"
        elif score >= 35: return score, "D", "Poor terms — shop around"
        else:             return score, "F", "Unfavorable — strong action recommended"

    # ─── KEY INSIGHTS ────────────────────────────────────────────

    def _key_insights(self, data, rate, costs, monthly, ctc, risks, score) -> List[str]:
        insights = []

        loan = float(data.get("loan_amount", 0) or 0)

        # 1. Rate
        if rate.rate_vs_market > 0:
            insights.append(
                f"📊 Rate of {rate.your_rate}% is {rate.rate_vs_market:.2f}% above market — "
                f"saving {abs(rate.monthly_savings_at_market):,.0f}/month at market rate"
            )
        else:
            insights.append(f"✅ Rate of {rate.your_rate}% is at or below current market — favorable")

        # 2. Monthly cost
        if monthly.estimated_total:
            insights.append(
                f"💳 True monthly cost: ${monthly.estimated_total:,.0f} "
                f"(${monthly.principal_and_interest:,.0f} P&I + ${monthly.estimated_escrow:,.0f} escrow)"
            )

        # 3. Closing costs
        if costs.total_closing_costs:
            insights.append(
                f"🏦 Closing costs: ${costs.net_closing_costs:,.0f} net "
                f"(${costs.total_closing_costs:,.0f} - ${costs.lender_credits:,.0f} lender credits) — "
                f"{costs.assessment}"
            )

        # 4. Shopping opportunity
        if costs.shopping_recommended:
            insights.append(
                f"💡 ${costs.services_can_shop:,.0f} in shoppable services — "
                f"compare providers to potentially save ~${costs.shoppable_savings_potential:,.0f}"
            )

        # 5. Risk flags or clean bill
        if risks.flags:
            insights.append(f"⚠️ {len([f for f in risks.flags if '⚠️' in f])} risk flag(s): {risks.flags[0]}")
        else:
            insights.append("✅ No significant risk flags — clean loan terms")

        # 6. Score
        insights.append(f"📈 Loan estimate score: {score}/100 — {self._score_label(score)}")

        return insights[:6]

    # ─── HELPERS ─────────────────────────────────────────────────

    def _calc_payment(self, principal: float, annual_rate_pct: float, term_years: int) -> Optional[float]:
        if not (principal and annual_rate_pct and term_years):
            return None
        r = (annual_rate_pct / 100) / 12
        n = term_years * 12
        try:
            return round(principal * r * (1 + r) ** n / ((1 + r) ** n - 1), 2)
        except Exception:
            return None

    def _score_label(self, score: int) -> str:
        if score >= 80:   return "Excellent loan terms"
        elif score >= 65: return "Good terms — minor concerns"
        elif score >= 50: return "Fair — review carefully"
        elif score >= 35: return "Poor — shop around"
        else:             return "Unfavorable — action required"

    def _empty_report(self) -> LEIntelligenceReport:
        empty_rate  = RateAnalysis(0, 7.0, 0, "unknown", 0, 0, 0, 0)
        empty_cc    = ClosingCostAnalysis(0, 0, 3.0, "unknown", 0, 0, 0, 0, 0, 0, 0, False)
        empty_mo    = MonthlyCostBreakdown(0, 0, 0, 0, 0)
        empty_ctc   = CashToCloseAnalysis(0, 0, 0, 0, None)
        empty_risks = LoanRiskFlags([], False, False, False, False, "low")
        empty_acts  = ActionItems([], [], [])
        return LEIntelligenceReport(
            rate_analysis=empty_rate, closing_cost_analysis=empty_cc,
            monthly_cost=empty_mo, cash_to_close=empty_ctc,
            risk_flags=empty_risks, action_items=empty_acts,
            score=0, score_grade="F", score_label="Analysis failed",
            key_insights=["Unable to generate Loan Estimate intelligence"],
        )