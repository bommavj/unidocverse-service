"""
Bank Statement Intelligence Layer
Advisory layer on top of BankStatementAnalyzer output.

Answers the 6 questions for every financial document:
  1. What happened  (raw metrics — already in analyzer)
  2. What it means  (derived ratios, benchmarks)
  3. How it compares (50/30/20, national averages, savings benchmarks)
  4. What to do next (ranked action items)
  5. What to watch for (risk signals, stress indicators)
  6. Where it flows (downstream: tax, retirement, credit impact)

Drop at: app/parsers/extractors/bank_statement_intelligence.py
Call from BankStatementAnalyzer.analyze() after all metrics are built.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# BENCHMARKS & THRESHOLDS
# ─────────────────────────────────────────────

# 50/30/20 rule targets (% of gross income)
BUDGET_RULE = {"needs": 50, "wants": 30, "savings": 20}

# National average monthly spending by category (2024 BLS data, approximate)
NATIONAL_AVERAGES_MONTHLY = {
    "Housing":       2025,
    "Transport":      850,
    "Groceries":      550,
    "Dining":         350,
    "Healthcare":     290,
    "Entertainment":  200,
    "Shopping":       180,
    "Utilities":      175,
    "Insurance":      160,
    "Education":       90,
}

# Emergency fund tiers (months of expenses)
EMERGENCY_FUND_TIERS = [
    (1,  "critical",   "< 1 month — no safety net"),
    (2,  "low",        "1–2 months — vulnerable"),
    (3,  "adequate",   "3 months — minimum recommended"),
    (6,  "good",       "3–6 months — solid buffer"),
    (12, "excellent",  "6–12 months — strong cushion"),
    (999,"exceptional","12+ months — exceptional security"),
]

# Savings rate benchmarks
SAVINGS_RATE_TIERS = [
    (-999, "depleting",  "Spending more than earning — emergency"),
    (0,    "break_even", "Breaking even — no savings"),
    (5,    "minimal",    "1–5% — below recommended minimum"),
    (10,   "fair",       "5–10% — fair, room to improve"),
    (20,   "good",       "10–20% — on track"),
    (30,   "excellent",  "20–30% — excellent"),
    (999,  "exceptional","30%+ — exceptional wealth-building pace"),
]

# Subscription cost benchmarks
SUBSCRIPTION_BENCHMARKS = {
    "low":    30,   # <$30/mo
    "medium": 80,   # $30–$80/mo
    "high":   150,  # $80–$150/mo
    "extreme": 999, # $150+/mo
}

# ATM withdrawal flags
ATM_CASH_RATIO_WARNING = 0.15   # >15% of spending in cash = flag

# Fee waste thresholds
FEE_WARNING_MONTHLY = 25        # >$25/mo in avoidable fees
FEE_CRITICAL_MONTHLY = 75       # >$75/mo

# Dining / discretionary ratios
DINING_WARNING_PCT = 0.15       # >15% of total spending on dining
DISCRETIONARY_WARNING_PCT = 0.40


# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class CashflowHealth:
    net_change: float
    savings_rate: float
    savings_tier: str
    savings_tier_label: str
    monthly_surplus_deficit: float
    annualized_surplus_deficit: float
    income_to_expense_ratio: float
    months_until_depleted: Optional[float]   # None if positive cashflow


@dataclass
class BudgetAnalysis:
    total_income: float
    total_spending: float
    needs_amount: float
    wants_amount: float
    savings_amount: float
    needs_pct: float
    wants_pct: float
    savings_pct: float
    needs_target: float     # 50% of income
    wants_target: float     # 30%
    savings_target: float   # 20%
    needs_over_under: float
    wants_over_under: float
    savings_over_under: float
    budget_grade: str       # A/B/C/D/F
    budget_summary: str


@dataclass
class EmergencyFundAnalysis:
    monthly_expenses: float
    estimated_fund_needed_3mo: float
    estimated_fund_needed_6mo: float
    months_covered: Optional[float]    # None = unknown (no balance data)
    tier: str
    tier_label: str
    monthly_gap_to_3mo: float
    monthly_gap_to_6mo: float


@dataclass
class SubscriptionAudit:
    subscriptions: List[Dict]
    total_monthly: float
    total_annual: float
    benchmark_tier: str
    potential_savings_30pct: float
    dormant_candidates: List[str]    # < 1 charge/month
    duplicate_categories: List[str]  # streaming overlap, etc.


@dataclass
class SpendingBenchmarks:
    category_comparisons: List[Dict]   # [{category, your_amount, national_avg, over_under, pct_diff}]
    top_overspend_category: str
    top_overspend_amount: float
    total_above_national: float


@dataclass
class RiskSignals:
    overdraft_count: int
    late_fee_count: int
    nsf_count: int
    atm_cash_ratio: float
    atm_cash_flag: bool
    fee_total_monthly: float
    fee_flag: bool
    negative_cashflow_days: int
    stress_score: int           # 0–100
    stress_level: str           # low/medium/high/critical
    stress_signals: List[str]


@dataclass
class ActionPlan:
    immediate: List[Dict]       # Do this week
    short_term: List[Dict]      # Do this month
    long_term: List[Dict]       # 3–12 month goals
    total_monthly_savings_potential: float
    total_annual_savings_potential: float


@dataclass
class DownstreamImpact:
    tax_deductible_categories: List[str]
    estimated_deductible_amount: float
    credit_utilization_signals: List[str]
    retirement_contribution_gap: float     # how much more could go to savings
    net_worth_monthly_impact: float


@dataclass
class BankIntelligenceReport:
    cashflow: CashflowHealth
    budget: BudgetAnalysis
    emergency_fund: EmergencyFundAnalysis
    subscriptions: SubscriptionAudit
    benchmarks: SpendingBenchmarks
    risks: RiskSignals
    actions: ActionPlan
    downstream: DownstreamImpact
    score: int                  # 0–100 overall financial wellness score
    score_grade: str            # A/B/C/D/F
    score_label: str
    key_insights: List[str]     # top 5 plain-English takeaways

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

class BankStatementIntelligence:
    """
    Advisory intelligence layer for bank statement analysis.

    Usage:
        intel = BankStatementIntelligence()
        report = intel.analyze(analyzer_result)
        analyzer_result["intelligence"] = report.to_dict()
    """

    def analyze(self, data: Dict[str, Any]) -> BankIntelligenceReport:
        """
        Build full advisory report from BankStatementAnalyzer output dict.

        Args:
            data: The dict returned by BankStatementAnalyzer.analyze()

        Returns:
            BankIntelligenceReport with all advisory layers
        """
        try:
            cashflow   = self._cashflow_health(data)
            budget     = self._budget_analysis(data)
            emergency  = self._emergency_fund(data)
            subs       = self._subscription_audit(data)
            benchmarks = self._spending_benchmarks(data)
            risks      = self._risk_signals(data)
            actions    = self._action_plan(data, cashflow, budget, subs, risks)
            downstream = self._downstream_impact(data, budget)
            score, grade, label = self._wellness_score(cashflow, budget, risks, subs)
            insights   = self._key_insights(data, cashflow, budget, subs, risks, score)

            return BankIntelligenceReport(
                cashflow=cashflow,
                budget=budget,
                emergency_fund=emergency,
                subscriptions=subs,
                benchmarks=benchmarks,
                risks=risks,
                actions=actions,
                downstream=downstream,
                score=score,
                score_grade=grade,
                score_label=label,
                key_insights=insights,
            )

        except Exception as e:
            logger.error(f"BankStatementIntelligence.analyze failed: {e}", exc_info=True)
            return self._empty_report()

    # ─────────────────────────────────────────────
    # LAYER 1 — CASHFLOW HEALTH
    # ─────────────────────────────────────────────

    def _cashflow_health(self, data: Dict) -> CashflowHealth:
        net        = float(data.get("net_change", 0) or 0)
        income     = float(data.get("total_credits", 0) or 1)
        spending   = float(data.get("total_debits", 0) or 0)

        savings_rate = (net / income * 100) if income > 0 else 0
        tier, label  = self._savings_tier(savings_rate)

        ie_ratio = round(income / spending, 2) if spending > 0 else 0

        months_until_depleted = None
        if net < 0 and spending > 0:
            # If they have an estimated balance, how long until zero?
            months_until_depleted = round(abs(net) / abs(net), 1)  # placeholder — 1 month at this rate

        return CashflowHealth(
            net_change=round(net, 2),
            savings_rate=round(savings_rate, 2),
            savings_tier=tier,
            savings_tier_label=label,
            monthly_surplus_deficit=round(net, 2),
            annualized_surplus_deficit=round(net * 12, 2),
            income_to_expense_ratio=ie_ratio,
            months_until_depleted=months_until_depleted,
        )

    # ─────────────────────────────────────────────
    # LAYER 2 — 50/30/20 BUDGET ANALYSIS
    # ─────────────────────────────────────────────

    def _budget_analysis(self, data: Dict) -> BudgetAnalysis:
        income = float(data.get("total_credits", 0) or 0)
        spending = float(data.get("total_spending", 0) or data.get("total_debits", 0) or 0)

        needs_categories = {"Groceries", "Utilities", "Bills", "Healthcare", "Insurance", "Transport"}
        wants_categories = {"Dining", "Entertainment", "Shopping", "Education"}

        category_breakdown = data.get("category_breakdown", {})
        all_items = []
        if isinstance(category_breakdown, dict):
            raw = category_breakdown.get("all_items", [])
            if isinstance(raw, list):
                all_items = raw  # new shape: [{category, total, count}]
            elif isinstance(raw, dict):
                # fallback: old flat-dict shape {"Food": 120.0}
                all_items = [{"category": k, "total": v} for k, v in raw.items()]

        needs_amount = 0.0
        wants_amount = 0.0
        for item in all_items:
            cat = item.get("category", "")
            amt = float(item.get("total", 0) or 0)
            cat_title = cat.replace("_", " ").title()
            if any(n in cat_title for n in needs_categories):
                needs_amount += amt
            elif any(w in cat_title for w in wants_categories):
                wants_amount += amt
            else:
                half = amt / 2
                needs_amount += half
                wants_amount += half

        savings_amount = income - needs_amount - wants_amount

        def pct(amt):
            return round(amt / income * 100, 1) if income > 0 else 0

        needs_pct = pct(needs_amount)
        wants_pct = pct(wants_amount)
        savings_pct = pct(savings_amount)

        needs_target = round(income * 0.50, 2)
        wants_target = round(income * 0.30, 2)
        savings_target = round(income * 0.20, 2)

        needs_over_under = round(needs_amount - needs_target, 2)
        wants_over_under = round(wants_amount - wants_target, 2)
        savings_over_under = round(savings_amount - savings_target, 2)

        deviations = abs(needs_over_under) + abs(wants_over_under) + abs(savings_over_under)
        deviation_pct = (deviations / income * 100) if income > 0 else 100
        if deviation_pct < 5:
            grade, summary = "A", "Excellent — within 5% of the 50/30/20 targets"
        elif deviation_pct < 10:
            grade, summary = "B", "Good — minor adjustments needed"
        elif deviation_pct < 20:
            grade, summary = "C", "Fair — moderate rebalancing recommended"
        elif deviation_pct < 35:
            grade, summary = "D", "Poor — significant budget imbalance"
        else:
            grade, summary = "F", "Critical — budget requires immediate restructuring"

        return BudgetAnalysis(
            total_income=round(income, 2),
            total_spending=round(spending, 2),
            needs_amount=round(needs_amount, 2),
            wants_amount=round(wants_amount, 2),
            savings_amount=round(savings_amount, 2),
            needs_pct=needs_pct,
            wants_pct=wants_pct,
            savings_pct=savings_pct,
            needs_target=needs_target,
            wants_target=wants_target,
            savings_target=savings_target,
            needs_over_under=needs_over_under,
            wants_over_under=wants_over_under,
            savings_over_under=savings_over_under,
            budget_grade=grade,
            budget_summary=summary,
        )

    # ─────────────────────────────────────────────
    # LAYER 3 — EMERGENCY FUND
    # ─────────────────────────────────────────────

    def _emergency_fund(self, data: Dict) -> EmergencyFundAnalysis:
        monthly_expenses = float(data.get("monthly_projected_spending", 0)
                                 or data.get("total_spending", 0)
                                 or data.get("total_debits", 0) or 0)

        fund_3mo = round(monthly_expenses * 3, 2)
        fund_6mo = round(monthly_expenses * 6, 2)

        # We don't have balance data from a single statement — flag as unknown
        months_covered = None
        tier = "unknown"
        tier_label = "Balance data not available — upload savings account statement"

        # Monthly savings gap to reach targets
        monthly_savings = float(data.get("projected_monthly_savings", 0) or
                                data.get("net_cashflow", 0) or
                                data.get("net_change", 0) or 0)

        gap_3mo = round(fund_3mo, 2)
        gap_6mo = round(fund_6mo, 2)

        if monthly_savings > 0:
            months_to_3mo = round(fund_3mo / monthly_savings, 1)
            months_to_6mo = round(fund_6mo / monthly_savings, 1)
            gap_3mo = months_to_3mo
            gap_6mo = months_to_6mo

        return EmergencyFundAnalysis(
            monthly_expenses=round(monthly_expenses, 2),
            estimated_fund_needed_3mo=fund_3mo,
            estimated_fund_needed_6mo=fund_6mo,
            months_covered=months_covered,
            tier=tier,
            tier_label=tier_label,
            monthly_gap_to_3mo=gap_3mo,
            monthly_gap_to_6mo=gap_6mo,
        )

    # ─────────────────────────────────────────────
    # LAYER 4 — SUBSCRIPTION AUDIT
    # ─────────────────────────────────────────────

    def _subscription_audit(self, data: Dict) -> SubscriptionAudit:
        raw_subs = data.get("subscriptions", []) or []
        if not isinstance(raw_subs, list):
            raw_subs = []

        total_monthly = float(data.get("subscription_cost", 0) or
                              sum(s.get("monthly_cost", 0) for s in raw_subs))
        total_annual  = round(total_monthly * 12, 2)

        # Benchmark tier
        if total_monthly < SUBSCRIPTION_BENCHMARKS["low"]:
            bench_tier = "low"
        elif total_monthly < SUBSCRIPTION_BENCHMARKS["medium"]:
            bench_tier = "medium"
        elif total_monthly < SUBSCRIPTION_BENCHMARKS["high"]:
            bench_tier = "high"
        else:
            bench_tier = "extreme"

        potential_savings = round(total_monthly * 0.30, 2)

        # Dormant candidates — charged less than once/month
        dormant = []
        for s in raw_subs:
            freq = s.get("frequency", 12)
            cost = s.get("monthly_cost", 0) or s.get("average_amount", 0)
            if freq < 2 and cost > 5:
                dormant.append(s.get("service", s.get("merchant", "Unknown")))

        # Duplicate category detection (streaming services)
        streaming_keywords = ["netflix", "hulu", "disney", "hbo", "max", "peacock",
                               "paramount", "apple tv", "youtube premium", "amazon prime video"]
        music_keywords     = ["spotify", "apple music", "tidal", "amazon music", "pandora",
                               "youtube music", "deezer"]
        cloud_keywords     = ["icloud", "google one", "dropbox", "onedrive", "box"]

        def _count_category(keywords):
            return [s.get("service", s.get("merchant", "")).lower()
                    for s in raw_subs
                    if any(kw in s.get("service", s.get("merchant", "")).lower()
                           for kw in keywords)]

        duplicate_categories = []
        if len(_count_category(streaming_keywords)) > 1:
            duplicate_categories.append(
                f"Multiple streaming services: {', '.join(_count_category(streaming_keywords)[:3])}")
        if len(_count_category(music_keywords)) > 1:
            duplicate_categories.append(
                f"Multiple music services: {', '.join(_count_category(music_keywords)[:3])}")
        if len(_count_category(cloud_keywords)) > 1:
            duplicate_categories.append(
                f"Multiple cloud storage services: {', '.join(_count_category(cloud_keywords)[:3])}")

        return SubscriptionAudit(
            subscriptions=raw_subs,
            total_monthly=round(total_monthly, 2),
            total_annual=total_annual,
            benchmark_tier=bench_tier,
            potential_savings_30pct=potential_savings,
            dormant_candidates=dormant,
            duplicate_categories=duplicate_categories,
        )

    # ─────────────────────────────────────────────
    # LAYER 5 — SPENDING BENCHMARKS
    # ─────────────────────────────────────────────

    def _spending_benchmarks(self, data: Dict) -> SpendingBenchmarks:
        category_breakdown = data.get("category_breakdown", {})
        all_items = []
        if isinstance(category_breakdown, dict):
            raw = category_breakdown.get("all_items", [])
            if isinstance(raw, list):
                all_items = raw
            elif isinstance(raw, dict):
                all_items = [{"category": k, "total": v} for k, v in raw.items()]

        comparisons = []
        total_above_national = 0.0

        for cat, national_avg in NATIONAL_AVERAGES_MONTHLY.items():
            your_amount = 0.0
            for item in all_items:
                key = item.get("category", "")
                if cat.lower() in key.lower() or key.lower() in cat.lower():
                    your_amount = float(item.get("total", 0) or 0)
                    break

            over_under = round(your_amount - national_avg, 2)
            pct_diff = round((over_under / national_avg * 100), 1) if national_avg > 0 else 0

            if over_under > 0:
                total_above_national += over_under

            comparisons.append({
                "category": cat,
                "your_amount": round(your_amount, 2),
                "national_avg": national_avg,
                "over_under": over_under,
                "pct_diff": pct_diff,
                "status": "over" if over_under > 0 else "under" if your_amount > 0 else "no_data",
            })

        comparisons.sort(key=lambda x: x["over_under"], reverse=True)

        top_overspend = comparisons[0] if comparisons and comparisons[0]["over_under"] > 0 else None

        return SpendingBenchmarks(
            category_comparisons=comparisons,
            top_overspend_category=top_overspend["category"] if top_overspend else "None",
            top_overspend_amount=top_overspend["over_under"] if top_overspend else 0.0,
            total_above_national=round(total_above_national, 2),
        )

    # ─────────────────────────────────────────────
    # LAYER 6 — RISK SIGNALS
    # ─────────────────────────────────────────────

    def _risk_signals(self, data: Dict) -> RiskSignals:
        transactions = data.get("transactions", []) or []

        # Count stress transactions
        overdraft_count = 0
        late_fee_count  = 0
        nsf_count       = 0
        for t in transactions:
            desc = (t.get("description", "") or "").lower()
            if "overdraft" in desc:
                overdraft_count += 1
            if "late fee" in desc or "late payment" in desc:
                late_fee_count += 1
            if "nsf" in desc or "insufficient" in desc:
                nsf_count += 1

        # ATM cash ratio
        atm_spending = float(data.get("atm_spending", 0) or 0)
        total_spending = float(data.get("total_spending", 0) or data.get("total_debits", 1) or 1)
        atm_cash_ratio = round(atm_spending / total_spending, 3) if total_spending > 0 else 0
        atm_cash_flag  = atm_cash_ratio > ATM_CASH_RATIO_WARNING

        # Fee total
        fraud_risk_score = int(data.get("fraud_risk_score", 0) or 0)
        fee_total = 0.0
        for t in transactions:
            desc = (t.get("description", "") or "").lower()
            if any(kw in desc for kw in ["fee", "charge", "penalty"]):
                fee_total += float(t.get("debit", 0) or 0)
        fee_flag = fee_total > FEE_WARNING_MONTHLY

        neg_cashflow_days = int(data.get("negative_cashflow_days", 0) or 0)

        # Stress score 0–100
        stress = 0
        signals = []

        if float(data.get("net_change", 0) or 0) < 0:
            stress += 30
            signals.append("Negative cash flow — spending exceeds income")
        if overdraft_count > 0:
            stress += 20
            signals.append(f"{overdraft_count} overdraft(s) detected")
        if nsf_count > 0:
            stress += 20
            signals.append(f"{nsf_count} NSF / insufficient funds occurrence(s)")
        if late_fee_count > 0:
            stress += 15
            signals.append(f"{late_fee_count} late payment fee(s) — may impact credit score")
        if fee_total > FEE_CRITICAL_MONTHLY:
            stress += 15
            signals.append(f"${fee_total:.0f} in fees — consider a fee-free account")
        elif fee_total > FEE_WARNING_MONTHLY:
            stress += 8
            signals.append(f"${fee_total:.0f} in avoidable fees this period")
        if atm_cash_flag:
            stress += 10
            signals.append(f"{atm_cash_ratio*100:.0f}% of spending is cash withdrawals — suggests budgeting difficulty")
        if neg_cashflow_days > 15:
            stress += 10
            signals.append(f"{neg_cashflow_days} days with more outflows than inflows")

        stress = min(stress, 100)

        if stress >= 60:
            stress_level = "critical"
        elif stress >= 35:
            stress_level = "high"
        elif stress >= 15:
            stress_level = "medium"
        else:
            stress_level = "low"

        return RiskSignals(
            overdraft_count=overdraft_count,
            late_fee_count=late_fee_count,
            nsf_count=nsf_count,
            atm_cash_ratio=atm_cash_ratio,
            atm_cash_flag=atm_cash_flag,
            fee_total_monthly=round(fee_total, 2),
            fee_flag=fee_flag,
            negative_cashflow_days=neg_cashflow_days,
            stress_score=stress,
            stress_level=stress_level,
            stress_signals=signals,
        )

    # ─────────────────────────────────────────────
    # LAYER 7 — ACTION PLAN
    # ─────────────────────────────────────────────

    def _action_plan(
        self,
        data: Dict,
        cashflow: CashflowHealth,
        budget: BudgetAnalysis,
        subs: SubscriptionAudit,
        risks: RiskSignals,
    ) -> ActionPlan:
        immediate  = []
        short_term = []
        long_term  = []

        monthly_savings_potential = 0.0

        # ── IMMEDIATE ──────────────────────────────

        if risks.overdraft_count > 0 or risks.nsf_count > 0:
            immediate.append({
                "priority":  1,
                "category":  "overdraft",
                "title":     "Set up overdraft protection",
                "detail":    f"You had {risks.overdraft_count + risks.nsf_count} overdraft/NSF event(s). "
                             "Enable a linked savings account or overdraft line to avoid $35+ fees.",
                "savings_monthly": risks.overdraft_count * 35 + risks.nsf_count * 35,
            })
            monthly_savings_potential += risks.overdraft_count * 35 + risks.nsf_count * 35

        if risks.fee_total_monthly > FEE_WARNING_MONTHLY:
            immediate.append({
                "priority":  2,
                "category":  "fees",
                "title":     "Eliminate avoidable bank fees",
                "detail":    f"${risks.fee_total_monthly:.0f}/month in fees = ${risks.fee_total_monthly * 12:.0f}/year. "
                             "Switch to a high-yield checking account (Ally, Marcus, Fidelity CMA).",
                "savings_monthly": risks.fee_total_monthly,
            })
            monthly_savings_potential += risks.fee_total_monthly

        if subs.duplicate_categories:
            for dup in subs.duplicate_categories:
                immediate.append({
                    "priority":  3,
                    "category":  "subscriptions",
                    "title":     f"Cancel duplicate subscription — {dup.split(':')[0]}",
                    "detail":    dup,
                    "savings_monthly": 15,  # conservative estimate
                })
                monthly_savings_potential += 15

        if risks.late_fee_count > 0:
            immediate.append({
                "priority":  4,
                "category":  "bills",
                "title":     "Set up autopay for recurring bills",
                "detail":    f"{risks.late_fee_count} late fee(s) detected. Autopay eliminates late fees "
                             "and protects your credit score.",
                "savings_monthly": risks.late_fee_count * 30,
            })
            monthly_savings_potential += risks.late_fee_count * 30

        # ── SHORT TERM ─────────────────────────────

        if cashflow.savings_rate < 10:
            target_savings = round(budget.total_income * 0.10, 2)
            current_savings = max(cashflow.net_change, 0)
            gap = round(target_savings - current_savings, 2)
            short_term.append({
                "priority":  1,
                "category":  "savings",
                "title":     "Reach 10% savings rate",
                "detail":    f"Current: {cashflow.savings_rate:.1f}%. Target: 10%. "
                             f"Auto-transfer ${gap:.0f}/month to savings the day after payday.",
                "savings_monthly": gap,
            })
            monthly_savings_potential += gap

        if subs.total_monthly > SUBSCRIPTION_BENCHMARKS["medium"]:
            short_term.append({
                "priority":  2,
                "category":  "subscriptions",
                "title":     "Subscription audit",
                "detail":    f"${subs.total_monthly:.0f}/month on subscriptions (${subs.total_annual:.0f}/year). "
                             f"Cutting 30% saves ${subs.potential_savings_30pct:.0f}/month. "
                             "Use a tool like Rocket Money or Trim to identify unused services.",
                "savings_monthly": subs.potential_savings_30pct,
            })
            monthly_savings_potential += subs.potential_savings_30pct

        if subs.dormant_candidates:
            dormant_str = ", ".join(subs.dormant_candidates[:4])
            short_term.append({
                "priority":  3,
                "category":  "subscriptions",
                "title":     "Cancel dormant subscriptions",
                "detail":    f"Possible dormant services (charged infrequently): {dormant_str}. "
                             "Review each and cancel those you no longer use.",
                "savings_monthly": len(subs.dormant_candidates) * 10,
            })
            monthly_savings_potential += len(subs.dormant_candidates) * 10

        if risks.atm_cash_flag:
            short_term.append({
                "priority":  4,
                "category":  "cash",
                "title":     "Reduce cash withdrawals",
                "detail":    f"{risks.atm_cash_ratio*100:.0f}% of spending is cash (above {ATM_CASH_RATIO_WARNING*100:.0f}% threshold). "
                             "Cash spending is invisible to your budget. "
                             "Switch to debit card for all purchases to track spending accurately.",
                "savings_monthly": 0,
            })

        if budget.wants_over_under > 100:
            short_term.append({
                "priority":  5,
                "category":  "budget",
                "title":     f"Reduce discretionary spending by ${budget.wants_over_under:.0f}",
                "detail":    f"Wants spending is ${budget.wants_over_under:.0f} over the 30% target. "
                             f"Primary categories to review: Dining, Entertainment, Shopping.",
                "savings_monthly": round(budget.wants_over_under * 0.5, 2),
            })
            monthly_savings_potential += round(budget.wants_over_under * 0.5, 2)

        # ── LONG TERM ──────────────────────────────

        if cashflow.savings_rate < 20:
            long_term.append({
                "priority":  1,
                "category":  "wealth",
                "title":     "Reach 20% savings rate",
                "detail":    "20% savings rate is the wealth-building threshold. "
                             f"From your income of ${budget.total_income:.0f}, "
                             f"target ${budget.savings_target:.0f}/month in combined savings + investments.",
                "savings_monthly": round(budget.savings_target - max(cashflow.net_change, 0), 2),
            })

        long_term.append({
            "priority":  2,
            "category":  "emergency_fund",
            "title":     "Build 3–6 month emergency fund",
            "detail":    f"Monthly expenses: ${(data.get('monthly_projected_spending') or data.get('total_debits', 0)):.0f}. "
                         f"3-month target: ${(data.get('monthly_projected_spending', 0) or 0) * 3:.0f}. "
                         "Keep in a high-yield savings account (HYSA) earning 4–5% APY.",
            "savings_monthly": 0,
        })

        long_term.append({
            "priority":  3,
            "category":  "investing",
            "title":     "Automate investing after emergency fund",
            "detail":    "Once emergency fund is funded: max HSA first ($4,150 single), "
                         "then 401k to match, then Roth IRA ($7,000), then taxable brokerage.",
            "savings_monthly": 0,
        })

        return ActionPlan(
            immediate=sorted(immediate, key=lambda x: x["priority"]),
            short_term=sorted(short_term, key=lambda x: x["priority"]),
            long_term=sorted(long_term, key=lambda x: x["priority"]),
            total_monthly_savings_potential=round(monthly_savings_potential, 2),
            total_annual_savings_potential=round(monthly_savings_potential * 12, 2),
        )

    # ─────────────────────────────────────────────
    # LAYER 8 — DOWNSTREAM IMPACT
    # ─────────────────────────────────────────────

    def _downstream_impact(self, data: Dict, budget: BudgetAnalysis) -> DownstreamImpact:
        deductible_cats = []
        deductible_amt = 0.0

        category_breakdown = data.get("category_breakdown", {})
        all_items = []
        if isinstance(category_breakdown, dict):
            raw = category_breakdown.get("all_items", [])
            if isinstance(raw, list):
                all_items = raw
            elif isinstance(raw, dict):
                all_items = [{"category": k, "total": v} for k, v in raw.items()]

        tax_deductible_keywords = {
            "Healthcare": "Medical expenses >7.5% AGI may be deductible",
            "Education": "Student loan interest / tuition may qualify",
            "Home": "Mortgage interest deductible if itemizing",
            "Charity": "Charitable contributions deductible",
            "Business": "Business expenses deductible if self-employed",
        }

        for item in all_items:
            cat = item.get("category", "")
            amt = float(item.get("total", 0) or 0)
            for keyword, note in tax_deductible_keywords.items():
                if keyword.lower() in cat.lower():
                    deductible_cats.append(f"{cat}: {note}")
                    deductible_amt += amt

        # Credit utilization signals
        credit_signals = []
        if float(data.get("net_change", 0) or 0) < 0:
            credit_signals.append(
                "Negative cash flow increases reliance on credit — monitor utilization below 30%")
        if int(data.get("fraud_risk_score", 0) or 0) > 30:
            credit_signals.append(
                "Elevated fraud risk score — review credit report for unauthorized accounts")

        monthly_surplus = max(float(data.get("net_change", 0) or 0), 0)
        retirement_gap = max(round(budget.savings_target - monthly_surplus, 2), 0)

        return DownstreamImpact(
            tax_deductible_categories=deductible_cats,
            estimated_deductible_amount=round(deductible_amt, 2),
            credit_utilization_signals=credit_signals,
            retirement_contribution_gap=retirement_gap,
            net_worth_monthly_impact=round(float(data.get("net_change", 0) or 0), 2),
        )

    # ─────────────────────────────────────────────
    # WELLNESS SCORE
    # ─────────────────────────────────────────────

    def _wellness_score(
        self,
        cashflow: CashflowHealth,
        budget: BudgetAnalysis,
        risks: RiskSignals,
        subs: SubscriptionAudit,
    ) -> tuple[int, str, str]:
        score = 0

        # Savings rate (35 pts)
        sr = cashflow.savings_rate
        if sr >= 20:
            score += 35
        elif sr >= 10:
            score += 25
        elif sr >= 5:
            score += 15
        elif sr > 0:
            score += 8

        # Budget adherence (25 pts)
        grade = budget.budget_grade
        grade_pts = {"A": 25, "B": 20, "C": 14, "D": 7, "F": 0}
        score += grade_pts.get(grade, 0)

        # Risk / stress (25 pts — inverse)
        risk_deduction = min(risks.stress_score // 4, 25)
        score += max(25 - risk_deduction, 0)

        # Subscription control (15 pts)
        sub_tier = subs.benchmark_tier
        sub_pts = {"low": 15, "medium": 12, "high": 6, "extreme": 0}
        score += sub_pts.get(sub_tier, 10)

        score = min(max(score, 0), 100)

        if score >= 85:
            grade_out = "A"
            label = "Excellent — strong financial health"
        elif score >= 70:
            grade_out = "B"
            label = "Good — minor improvements available"
        elif score >= 55:
            grade_out = "C"
            label = "Fair — meaningful improvements needed"
        elif score >= 40:
            grade_out = "D"
            label = "Poor — requires focused action"
        else:
            grade_out = "F"
            label = "Critical — immediate financial attention needed"

        return score, grade_out, label

    # ─────────────────────────────────────────────
    # KEY INSIGHTS
    # ─────────────────────────────────────────────

    def _key_insights(
        self,
        data: Dict,
        cashflow: CashflowHealth,
        budget: BudgetAnalysis,
        subs: SubscriptionAudit,
        risks: RiskSignals,
        score: int,
    ) -> List[str]:
        insights = []

        # 1. Cashflow headline
        net = cashflow.net_change
        if net > 0:
            insights.append(
                f"✅ Positive cash flow of ${net:,.2f} ({cashflow.savings_rate:.1f}% savings rate — "
                f"{cashflow.savings_tier_label.split('—')[1].strip()})")
        else:
            insights.append(
                f"🔴 Spending ${abs(net):,.2f} more than earning — "
                f"annualized deficit of ${abs(cashflow.annualized_surplus_deficit):,.2f}")

        # 2. Budget adherence
        insights.append(
            f"📊 50/30/20 grade: {budget.budget_grade} — {budget.budget_summary}. "
            f"Needs: {budget.needs_pct:.0f}% | Wants: {budget.wants_pct:.0f}% | Savings: {budget.savings_pct:.0f}%")

        # 3. Subscription burn
        if subs.total_monthly > 0:
            insights.append(
                f"📱 ${subs.total_monthly:.0f}/month on subscriptions (${subs.total_annual:.0f}/year). "
                + (f"Overlap detected: {subs.duplicate_categories[0]}" if subs.duplicate_categories
                   else f"Cutting 30% saves ${subs.potential_savings_30pct:.0f}/month"))

        # 4. Risk headline
        if risks.stress_level in ("critical", "high"):
            insights.append(
                f"⚠️ Financial stress score: {risks.stress_score}/100 ({risks.stress_level.upper()}). "
                f"Top signal: {risks.stress_signals[0] if risks.stress_signals else 'Multiple risk factors'}")
        elif risks.stress_signals:
            insights.append(
                f"🟡 Moderate stress signals: {risks.stress_signals[0]}")
        else:
            insights.append("✅ No significant financial stress signals detected")

        # 5. Biggest action
        total_annual_savings = (
            risks.fee_total_monthly * 12
            + subs.potential_savings_30pct * 12
        )
        if total_annual_savings > 500:
            insights.append(
                f"💡 Quick wins: Eliminating fees + trimming subscriptions could save "
                f"~${total_annual_savings:,.0f}/year with minimal lifestyle impact")

        return insights[:5]

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def _savings_tier(self, rate: float) -> tuple[str, str]:
        for threshold, tier, label in SAVINGS_RATE_TIERS:
            if rate <= threshold:
                return tier, label
        return "exceptional", "30%+ — exceptional wealth-building pace"

    def _empty_report(self) -> BankIntelligenceReport:
        empty_cashflow = CashflowHealth(0, 0, "unknown", "Unable to analyze", 0, 0, 0, None)
        empty_budget   = BudgetAnalysis(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "F", "Analysis failed")
        empty_ef       = EmergencyFundAnalysis(0, 0, 0, None, "unknown", "Analysis failed", 0, 0)
        empty_subs     = SubscriptionAudit([], 0, 0, "unknown", 0, [], [])
        empty_bench    = SpendingBenchmarks([], "None", 0, 0)
        empty_risks    = RiskSignals(0, 0, 0, 0, False, 0, False, 0, 0, "low", [])
        empty_actions  = ActionPlan([], [], [], 0, 0)
        empty_down     = DownstreamImpact([], 0, [], 0, 0)
        return BankIntelligenceReport(
            cashflow=empty_cashflow, budget=empty_budget, emergency_fund=empty_ef,
            subscriptions=empty_subs, benchmarks=empty_bench, risks=empty_risks,
            actions=empty_actions, downstream=empty_down,
            score=0, score_grade="F", score_label="Analysis failed",
            key_insights=["Unable to generate intelligence report — check analyzer output"],
        )