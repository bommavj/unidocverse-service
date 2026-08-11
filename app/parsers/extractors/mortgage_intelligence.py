"""
Mortgage Intelligence Layer
app/parsers/extractors/mortgage_intelligence.py

Answers the key questions for every mortgage document:
  1. What is the true cost of this loan?
  2. How does the rate compare to the market?
  3. What does the amortization look like?
  4. What are the tax and financial downstream impacts?
  5. Should this be refinanced?
  6. What actions should the borrower take?
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# BENCHMARKS (2024-2025 approximate)
# ─────────────────────────────────────────────

# Benchmark 30-year fixed rates by credit tier
RATE_BENCHMARKS = {
    "excellent": 6.5,   # 760+
    "good":      7.0,   # 700-759
    "fair":      7.5,   # 640-699
    "poor":      8.5,   # <640
}

# Standard mortgage-to-income ratio thresholds
DTI_THRESHOLDS = {
    "comfortable": 28,   # housing < 28% gross income
    "warning":     36,   # total debt < 36%
    "critical":    43,   # max for most conforming loans
}

# Average US home appreciation (annual %)
HOME_APPRECIATION_RATE = 4.0

# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class LoanCostAnalysis:
    loan_amount: float
    monthly_payment: float
    total_paid: float
    total_interest: float
    interest_to_principal_ratio: float
    effective_monthly_cost: float
    cost_per_dollar_borrowed: float
    loan_grade: str          # A/B/C/D/F
    loan_grade_summary: str


@dataclass
class RateAnalysis:
    your_rate: float
    market_rate_good_credit: float
    rate_vs_market: float       # positive = above market
    rate_assessment: str        # excellent/good/fair/high/very_high
    monthly_savings_at_market: float
    annual_savings_at_market: float
    lifetime_savings_at_market: float
    refinance_recommended: bool
    refinance_break_even_months: Optional[int]


@dataclass
class AmortizationInsights:
    months_to_50pct_equity: Optional[int]
    equity_at_year_5: float
    equity_at_year_10: float
    equity_at_year_15: float
    interest_heavy_years: int      # years where >50% of payment is interest
    principal_crossover_month: Optional[int]  # month interest < principal


@dataclass
class TaxImpact:
    annual_interest_year1: float
    estimated_deductible_amount: float
    estimated_tax_savings_28pct: float
    estimated_tax_savings_22pct: float
    note: str


@dataclass
class RefinanceAnalysis:
    current_monthly: float
    new_monthly_at_market: float
    monthly_savings: float
    annual_savings: float
    lifetime_savings: float
    break_even_months: int
    recommended: bool
    recommendation_reason: str


@dataclass
class MortgageIntelligenceReport:
    cost_analysis: LoanCostAnalysis
    rate_analysis: RateAnalysis
    amortization: AmortizationInsights
    tax_impact: TaxImpact
    refinance: RefinanceAnalysis
    risk_flags: List[str]
    action_items: List[Dict]
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


# ─────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────

class MortgageIntelligence:

    def analyze(self, data: Dict[str, Any]) -> MortgageIntelligenceReport:
        try:
            cost      = self._cost_analysis(data)
            rate      = self._rate_analysis(data, cost)
            amort     = self._amortization_insights(data, cost)
            tax       = self._tax_impact(data, cost)
            refi      = self._refinance_analysis(data, cost, rate)
            risks     = self._risk_flags(data)
            actions   = self._action_items(data, cost, rate, refi, risks)
            score, grade, label = self._score(cost, rate, risks, refi)
            insights  = self._key_insights(data, cost, rate, refi, risks, score)

            return MortgageIntelligenceReport(
                cost_analysis=cost,
                rate_analysis=rate,
                amortization=amort,
                tax_impact=tax,
                refinance=refi,
                risk_flags=risks,
                action_items=actions,
                score=score,
                score_grade=grade,
                score_label=label,
                key_insights=insights,
            )
        except Exception as e:
            logger.error(f"MortgageIntelligence.analyze failed: {e}", exc_info=True)
            return self._empty_report()

    # ─── LAYER 1: TRUE COST ───────────────────────────────────────

    def _cost_analysis(self, data: Dict) -> LoanCostAnalysis:
        loan    = float(data.get("loan_amount", 0) or 0)
        monthly = float(data.get("monthly_payment", 0) or 0)
        term    = int(data.get("term_years", 30) or 30)

        months     = term * 12

        # Only calculate total cost if we have both loan amount and monthly payment
        if loan > 0 and monthly > 0:
            total_paid = monthly * months
            total_int  = max(total_paid - loan, 0)
        else:
            total_paid = 0
            total_int  = 0

        itp_ratio = round(total_int / loan, 3) if loan > 0 else 0
        eff_cost  = round(monthly, 2) if monthly else 0
        cpd       = round(total_paid / loan, 3) if loan > 0 and total_paid > 0 else 0

        # Grade by interest-to-principal ratio (only when loan amount known)
        if loan == 0:
            grade, summary = "N/A", "Loan amount not available — upload origination document for full analysis"
        elif itp_ratio < 0.5:
            grade, summary = "A", "Excellent — low total interest burden"
        elif itp_ratio < 0.8:
            grade, summary = "B", "Good — moderate interest cost"
        elif itp_ratio < 1.2:
            grade, summary = "C", "Fair — significant interest over life of loan"
        elif itp_ratio < 1.8:
            grade, summary = "D", "Poor — interest exceeds principal"
        else:
            grade, summary = "F", "Very costly — interest far exceeds principal"

        return LoanCostAnalysis(
            loan_amount=round(loan, 2),
            monthly_payment=round(monthly, 2),
            total_paid=round(total_paid, 2),
            total_interest=round(total_int, 2),
            interest_to_principal_ratio=itp_ratio,
            effective_monthly_cost=eff_cost,
            cost_per_dollar_borrowed=cpd,
            loan_grade=grade,
            loan_grade_summary=summary,
        )

    # ─── LAYER 2: RATE ANALYSIS ───────────────────────────────────

    def _rate_analysis(self, data: Dict, cost: LoanCostAnalysis) -> RateAnalysis:
        your_rate  = float(data.get("interest_rate", 0) or 0)
        loan       = cost.loan_amount
        monthly    = cost.monthly_payment
        term       = int(data.get("term_years", 30) or 30)
        months     = term * 12

        market     = RATE_BENCHMARKS["good"]
        vs_market  = round(your_rate - market, 3)

        if vs_market <= -0.5:
            assessment = "excellent"
        elif vs_market <= 0:
            assessment = "good"
        elif vs_market <= 0.5:
            assessment = "fair"
        elif vs_market <= 1.5:
            assessment = "high"
        else:
            assessment = "very_high"

        # Monthly payment at market rate
        market_monthly = self._calc_monthly_payment(loan, market, term)
        monthly_savings = round(monthly - market_monthly, 2) if monthly and market_monthly else 0
        annual_savings  = round(monthly_savings * 12, 2)
        lifetime_savings = round(monthly_savings * months, 2)

        refi_recommended = monthly_savings > 100 and vs_market > 0.75
        break_even = round(3000 / monthly_savings) if monthly_savings > 0 else None  # assume $3k refi cost

        return RateAnalysis(
            your_rate=your_rate,
            market_rate_good_credit=market,
            rate_vs_market=vs_market,
            rate_assessment=assessment,
            monthly_savings_at_market=monthly_savings,
            annual_savings_at_market=annual_savings,
            lifetime_savings_at_market=lifetime_savings,
            refinance_recommended=refi_recommended,
            refinance_break_even_months=break_even,
        )

    # ─── LAYER 3: AMORTIZATION ────────────────────────────────────

    def _amortization_insights(self, data: Dict, cost: LoanCostAnalysis) -> AmortizationInsights:
        loan    = cost.loan_amount
        rate    = float(data.get("interest_rate", 0) or 0) / 100 / 12
        monthly = cost.monthly_payment
        term    = int(data.get("term_years", 30) or 30)
        months  = term * 12

        if not (loan and rate and monthly):
            return AmortizationInsights(None, 0, 0, 0, 0, None)

        # Walk amortization to find milestones
        balance = loan
        equity_milestones = {60: 0, 120: 0, 180: 0}  # months 60, 120, 180
        months_to_50pct = None
        principal_crossover = None
        interest_heavy_count = 0

        for m in range(1, months + 1):
            interest_payment  = balance * rate
            principal_payment = monthly - interest_payment
            balance = max(balance - principal_payment, 0)

            if interest_payment > principal_payment:
                interest_heavy_count += 1
            elif principal_crossover is None:
                principal_crossover = m

            if months_to_50pct is None and balance <= loan * 0.5:
                months_to_50pct = m

            if m in equity_milestones:
                equity_milestones[m] = round(loan - balance, 2)

        interest_heavy_years = interest_heavy_count // 12

        return AmortizationInsights(
            months_to_50pct_equity=months_to_50pct,
            equity_at_year_5=equity_milestones.get(60, 0),
            equity_at_year_10=equity_milestones.get(120, 0),
            equity_at_year_15=equity_milestones.get(180, 0),
            interest_heavy_years=interest_heavy_years,
            principal_crossover_month=principal_crossover,
        )

    # ─── LAYER 4: TAX IMPACT ──────────────────────────────────────

    def _tax_impact(self, data: Dict, cost: LoanCostAnalysis) -> TaxImpact:
        loan    = cost.loan_amount
        rate    = float(data.get("interest_rate", 0) or 0) / 100
        monthly = cost.monthly_payment

        # Year 1 interest (approximate)
        annual_rate    = rate
        year1_interest = round(loan * annual_rate * (1 - (1 / (1 + rate / 12) ** 12 / 12)), 2) if rate else 0
        # Simpler: first month interest * 12 approximation
        if not year1_interest and loan and rate:
            year1_interest = round(loan * (rate / 12) * 12, 2)

        # Mortgage interest deduction (up to $750k loan)
        deductible = min(year1_interest, year1_interest * (min(loan, 750000) / loan)) if loan else 0

        return TaxImpact(
            annual_interest_year1=round(year1_interest, 2),
            estimated_deductible_amount=round(deductible, 2),
            estimated_tax_savings_28pct=round(deductible * 0.28, 2),
            estimated_tax_savings_22pct=round(deductible * 0.22, 2),
            note="Mortgage interest is deductible if itemizing. Consult a tax advisor for your situation.",
        )

    # ─── LAYER 5: REFINANCE ───────────────────────────────────────

    def _refinance_analysis(self, data: Dict, cost: LoanCostAnalysis, rate: RateAnalysis) -> RefinanceAnalysis:
        current_monthly   = cost.monthly_payment
        market_monthly    = self._calc_monthly_payment(cost.loan_amount, rate.market_rate_good_credit, int(data.get("term_years", 30) or 30))
        monthly_savings   = round(current_monthly - market_monthly, 2) if current_monthly and market_monthly else 0
        annual_savings    = round(monthly_savings * 12, 2)
        lifetime_savings  = round(monthly_savings * int(data.get("term_years", 30) or 30) * 12, 2)
        refi_cost         = 3000  # conservative estimate
        break_even        = round(refi_cost / monthly_savings) if monthly_savings > 0 else 999
        recommended       = monthly_savings > 100 and rate.rate_vs_market > 0.75

        if recommended:
            reason = f"Your rate is {rate.rate_vs_market:.2f}% above market. Refinancing could save ${monthly_savings:,.0f}/month."
        elif monthly_savings > 0:
            reason = f"Modest savings of ${monthly_savings:,.0f}/month possible but may not justify closing costs."
        else:
            reason = "Your rate is at or below current market — refinancing not recommended."

        return RefinanceAnalysis(
            current_monthly=round(current_monthly, 2),
            new_monthly_at_market=round(market_monthly, 2) if market_monthly else 0,
            monthly_savings=monthly_savings,
            annual_savings=annual_savings,
            lifetime_savings=lifetime_savings,
            break_even_months=break_even,
            recommended=recommended,
            recommendation_reason=reason,
        )

    # ─── RISK FLAGS ───────────────────────────────────────────────

    def _risk_flags(self, data: Dict) -> List[str]:
        flags = []
        rate_type = str(data.get("rate_type", "") or "").lower()
        balloon   = float(data.get("balloon_payment", 0) or 0)
        apr       = data.get("apr")
        rate      = float(data.get("interest_rate", 0) or 0)

        if rate_type == "variable":
            flags.append("⚠️ Variable rate — monthly payment may increase significantly if rates rise")
        if balloon > 0:
            flags.append(f"⚠️ Balloon payment of ${balloon:,.2f} due at end of term — plan ahead")
        if apr is None:
            flags.append("⚠️ APR not disclosed — full cost of borrowing unclear")
        if rate > 9.0:
            flags.append(f"⚠️ Interest rate of {rate:.2f}% is significantly above market norms")
        if float(data.get("loan_amount", 0) or 0) > 1_000_000:
            flags.append("ℹ️ Jumbo loan — different underwriting standards apply")

        return flags

    # ─── ACTION ITEMS ─────────────────────────────────────────────

    def _action_items(self, data, cost, rate, refi, risks) -> List[Dict]:
        actions = []

        if refi.recommended:
            actions.append({
                "priority": "high",
                "category": "refinance",
                "title": "Consider Refinancing",
                "detail": refi.recommendation_reason,
                "monthly_impact": refi.monthly_savings,
            })

        if data.get("apr") is None:
            actions.append({
                "priority": "high",
                "category": "compliance",
                "title": "Request APR Disclosure",
                "detail": "APR was not found in this document. Request full disclosure from lender.",
                "monthly_impact": 0,
            })

        if str(data.get("rate_type", "") or "").lower() == "variable":
            actions.append({
                "priority": "medium",
                "category": "risk",
                "title": "Monitor Rate Adjustments",
                "detail": "Set calendar reminders for rate adjustment dates. Budget for potential increases.",
                "monthly_impact": 0,
            })

        if float(data.get("balloon_payment", 0) or 0) > 0:
            actions.append({
                "priority": "high",
                "category": "planning",
                "title": "Plan for Balloon Payment",
                "detail": "A balloon payment of $" + f"{float(data.get('balloon_payment', 0)):,.2f}" + " is due. Start saving or plan refinance.",
                "monthly_impact": 0,
            })

        actions.append({
            "priority": "low",
            "category": "tax",
            "title": "Maximize Mortgage Interest Deduction",
            "detail": "Track all mortgage interest paid for Schedule A. Consult a tax advisor.",
            "monthly_impact": 0,
        })

        return actions

    # ─── SCORE ────────────────────────────────────────────────────

    def _score(self, cost, rate, risks, refi) -> tuple:
        score = 100

        # Rate (40 pts)
        rate_map = {"excellent": 0, "good": 5, "fair": 15, "high": 25, "very_high": 40}
        score -= rate_map.get(rate.rate_assessment, 20)

        # Cost grade (30 pts)
        grade_map = {"A": 0, "B": 8, "C": 18, "D": 25, "F": 30}
        score -= grade_map.get(cost.loan_grade, 15)

        # Risk flags (30 pts)
        score -= min(len(risks) * 10, 30)

        score = max(min(score, 100), 0)

        if score >= 80:
            return score, "A", "Excellent — favorable loan terms"
        elif score >= 65:
            return score, "B", "Good — reasonable terms with minor concerns"
        elif score >= 50:
            return score, "C", "Fair — review terms and consider options"
        elif score >= 35:
            return score, "D", "Poor — unfavorable terms, action recommended"
        else:
            return score, "F", "Critical — significant financial risk"

    # ─── KEY INSIGHTS ─────────────────────────────────────────────

    def _key_insights(self, data, cost, rate, refi, risks, score) -> List[str]:
        insights = []

        # 1. Cost headline (only show if we have a real loan amount)
        if cost.loan_amount > 0 and cost.total_interest > 0:
            insights.append(
                f"💰 Total cost of loan: ${cost.total_paid:,.0f} "
                f"(${cost.loan_amount:,.0f} principal + ${cost.total_interest:,.0f} interest)"
            )
        elif cost.loan_amount == 0 and cost.monthly_payment > 0:
            insights.append(
                f"💳 Monthly payment: ${cost.monthly_payment:,.0f} — upload origination document for full cost analysis"
            )

        # 2. Rate assessment
        if rate.rate_vs_market > 0:
            insights.append(
                f"📊 Rate of {rate.your_rate:.2f}% is {rate.rate_vs_market:.2f}% above market "
                f"({rate.rate_assessment.replace('_', ' ')})"
            )
        else:
            insights.append(
                f"✅ Rate of {rate.your_rate:.2f}% is at or below current market — favorable terms"
            )

        # 3. Refinance opportunity
        if refi.recommended:
            insights.append(
                f"💡 Refinancing could save ${refi.monthly_savings:,.0f}/month "
                f"(${refi.lifetime_savings:,.0f} over life of loan). Break-even: {refi.break_even_months} months."
            )

        # 4. Risk flags
        if risks:
            insights.append(f"⚠️ {len(risks)} risk flag(s): {risks[0]}")
        else:
            insights.append("✅ No significant risk flags detected")

        # 5. Score
        insights.append(
            f"📈 Loan quality score: {score}/100 — {self._score(cost, rate, risks, refi)[2]}"
        )

        return insights[:5]

    # ─── HELPERS ──────────────────────────────────────────────────

    def _calc_monthly_payment(self, principal: float, annual_rate_pct: float, term_years: int) -> Optional[float]:
        if not (principal and annual_rate_pct and term_years):
            return None
        r = (annual_rate_pct / 100) / 12
        n = term_years * 12
        try:
            return round(principal * r * (1 + r) ** n / ((1 + r) ** n - 1), 2)
        except Exception:
            return None

    def _empty_report(self) -> MortgageIntelligenceReport:
        empty_cost  = LoanCostAnalysis(0, 0, 0, 0, 0, 0, 0, "F", "Analysis failed")
        empty_rate  = RateAnalysis(0, 7.0, 0, "unknown", 0, 0, 0, False, None)
        empty_amort = AmortizationInsights(None, 0, 0, 0, 0, None)
        empty_tax   = TaxImpact(0, 0, 0, 0, "Analysis failed")
        empty_refi  = RefinanceAnalysis(0, 0, 0, 0, 0, 0, False, "Analysis failed")
        return MortgageIntelligenceReport(
            cost_analysis=empty_cost, rate_analysis=empty_rate,
            amortization=empty_amort, tax_impact=empty_tax, refinance=empty_refi,
            risk_flags=[], action_items=[], score=0, score_grade="F",
            score_label="Analysis failed",
            key_insights=["Unable to generate mortgage intelligence report"],
        )