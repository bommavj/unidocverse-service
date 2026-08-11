"""
W-2 Intelligence Layer
======================
Advisory analysis on top of W2Extractor output.

Produces actionable tax guidance:
  - Federal tax bracket detection + marginal rate
  - Over/under-withholding estimate (refund vs. owe)
  - 401(k) / 403(b) / SIMPLE headroom to IRS limit
  - HSA contribution check (Code W vs. annual limit)
  - EITC eligibility flag
  - Additional Medicare Tax exposure (wages > $200k)
  - Pre-tax benefit summary (Box 1 vs. Box 3 gap)
  - W-4 adjustment recommendation
  - Form 1040 line mapping
  - Box 12 deferred compensation decoded with limits
  - Year-over-year wage growth (if prior W2Data provided)
  - Corrected W-2c action-required flag

All limits are stored per tax year.  Unknown years fall back to the most
recent known year.  No hardcoded assumptions about filing status — when
unknown we use Single brackets (most conservative, least likely to
understate liability).

Usage:
    from w2_intelligence import W2Intelligence
    intel = W2Intelligence()
    report = intel.analyze(w2_data)   # w2_data is a W2Data instance
    # report is a dict — merge into the analyzer result
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IRS limits by tax year
# ---------------------------------------------------------------------------

# 401(k) / 403(b) / 457(b) employee deferral limit
_401K_LIMIT: Dict[int, float] = {
    2019: 19_000, 2020: 19_500, 2021: 19_500, 2022: 20_500,
    2023: 22_500, 2024: 23_000, 2025: 23_500,
}
_401K_CATCHUP: Dict[int, float] = {  # age 50+
    2019: 6_000, 2020: 6_500, 2021: 6_500, 2022: 6_500,
    2023: 7_500, 2024: 7_500, 2025: 7_500,
}
# SIMPLE IRA
_SIMPLE_LIMIT: Dict[int, float] = {
    2019: 13_000, 2020: 13_500, 2021: 13_500, 2022: 14_000,
    2023: 15_500, 2024: 16_000, 2025: 16_500,
}
_SIMPLE_CATCHUP: Dict[int, float] = {
    2019: 3_000, 2020: 3_000, 2021: 3_000, 2022: 3_000,
    2023: 3_500, 2024: 3_500, 2025: 3_500,
}
# HSA limits (individual / family)
_HSA_INDIVIDUAL: Dict[int, float] = {
    2019: 3_500, 2020: 3_550, 2021: 3_600, 2022: 3_650,
    2023: 3_850, 2024: 4_150, 2025: 4_300,
}
_HSA_FAMILY: Dict[int, float] = {
    2019: 7_000, 2020: 7_100, 2021: 7_200, 2022: 7_300,
    2023: 7_750, 2024: 8_300, 2025: 8_550,
}
# Dependent care FSA limit
_DEPCARE_FSA_LIMIT: Dict[int, float] = {
    2019: 5_000, 2020: 5_000, 2021: 10_500, 2022: 5_000,
    2023: 5_000, 2024: 5_000, 2025: 5_000,
}
# EITC income thresholds (single/MFJ, 0 qualifying children)
# We use 0-children threshold as the conservative check — if wages < this,
# EITC is possible regardless of filing status.
_EITC_MAX_INCOME_SINGLE_0CHILD: Dict[int, float] = {
    2019: 15_570, 2020: 15_820, 2021: 21_430, 2022: 16_480,
    2023: 17_640, 2024: 18_591, 2025: 19_104,
}
_EITC_MAX_INCOME_MFJ_3PLUS: Dict[int, float] = {  # upper bound — widest eligibility
    2019: 55_952, 2020: 56_844, 2021: 57_414, 2022: 59_187,
    2023: 63_398, 2024: 66_819, 2025: 68_675,
}

# Federal income tax brackets (Single filer — conservative default)
# Format: [(upper_bound, marginal_rate), ...] — last tuple has upper_bound=inf
_FEDERAL_BRACKETS_SINGLE: Dict[int, List[Tuple[float, float]]] = {
    2024: [
        (11_600,   0.10),
        (47_150,   0.12),
        (100_525,  0.22),
        (191_950,  0.24),
        (243_725,  0.32),
        (609_350,  0.35),
        (float('inf'), 0.37),
    ],
    2025: [
        (11_925,   0.10),
        (48_475,   0.12),
        (103_350,  0.22),
        (197_300,  0.24),
        (250_525,  0.32),
        (626_350,  0.35),
        (float('inf'), 0.37),
    ],
}
# Married Filing Jointly brackets
_FEDERAL_BRACKETS_MFJ: Dict[int, List[Tuple[float, float]]] = {
    2024: [
        (23_200,   0.10),
        (94_300,   0.12),
        (201_050,  0.22),
        (383_900,  0.24),
        (487_450,  0.32),
        (731_200,  0.35),
        (float('inf'), 0.37),
    ],
    2025: [
        (23_850,   0.10),
        (96_950,   0.12),
        (206_700,  0.22),
        (394_600,  0.24),
        (501_050,  0.32),
        (751_600,  0.35),
        (float('inf'), 0.37),
    ],
}

# Standard deductions
_STD_DEDUCTION_SINGLE: Dict[int, float] = {
    2019: 12_200, 2020: 12_400, 2021: 12_550, 2022: 12_950,
    2023: 13_850, 2024: 14_600, 2025: 15_000,
}
_STD_DEDUCTION_MFJ: Dict[int, float] = {
    2019: 24_400, 2020: 24_800, 2021: 25_100, 2022: 25_900,
    2023: 27_700, 2024: 29_200, 2025: 30_000,
}

ADDITIONAL_MEDICARE_THRESHOLD = 200_000.0  # single; 250k MFJ


def _get(table: Dict[int, Any], year: Optional[int], default_year: int = 2025) -> Any:
    """Fetch from a year-keyed table; fall back to closest known year."""
    if year is not None and year in table:
        return table[year]
    # Use most recent year if unknown or future
    return table[default_year]


def _marginal_rate(wages: float, year: Optional[int], filing: str = "single") -> float:
    """Return marginal federal income tax bracket rate for given wages."""
    brackets = _FEDERAL_BRACKETS_SINGLE if filing != "mfj" else _FEDERAL_BRACKETS_MFJ
    year_brackets = _get(brackets, year, 2025)
    for upper, rate in year_brackets:
        if wages <= upper:
            return rate
    return 0.37


def _estimated_tax_liability(
    agi: float,
    year: Optional[int],
    filing: str = "single",
) -> float:
    """
    Approximate federal income tax liability using progressive brackets.
    Uses standard deduction.  Does NOT model credits, AMT, or investment income.
    Returns estimated tax owed (not withholding).
    """
    std = _get(
        _STD_DEDUCTION_SINGLE if filing != "mfj" else _STD_DEDUCTION_MFJ,
        year, 2025
    )
    taxable = max(0.0, agi - std)
    brackets = _get(
        _FEDERAL_BRACKETS_SINGLE if filing != "mfj" else _FEDERAL_BRACKETS_MFJ,
        year, 2025
    )
    tax = 0.0
    prev = 0.0
    for upper, rate in brackets:
        if taxable <= prev:
            break
        chunk = min(taxable, upper) - prev
        tax += chunk * rate
        prev = upper
        if upper == float('inf'):
            break
    return round(tax, 2)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RetirementAnalysis:
    codes_found: List[str] = field(default_factory=list)
    total_contributed: float = 0.0
    annual_limit: float = 0.0
    catchup_available: float = 0.0
    headroom: float = 0.0
    maxed_out: bool = False
    plan_type: str = ""            # "401k", "403b", "SIMPLE", "457b", "Roth 401k"
    note: str = ""


@dataclass
class HSAAnalysis:
    employer_contribution: float = 0.0   # Code W — employer portion only
    individual_limit: float = 0.0
    family_limit: float = 0.0
    headroom_individual: float = 0.0
    headroom_family: float = 0.0
    note: str = ""


@dataclass
class WithholdingEstimate:
    withheld: float = 0.0
    estimated_liability: float = 0.0
    delta: float = 0.0           # positive = likely refund; negative = likely owe
    direction: str = ""          # "refund", "owe", "on_track"
    confidence: str = "low"      # "high" | "medium" | "low"
    note: str = ""


@dataclass
class TaxBracket:
    marginal_rate: float = 0.0
    effective_rate: float = 0.0
    filing_assumed: str = "single"
    note: str = ""


@dataclass
class PreTaxGap:
    box1_wages: float = 0.0
    box3_ss_wages: float = 0.0
    gap: float = 0.0
    likely_pretax_benefits: float = 0.0
    note: str = ""


@dataclass
class W4Guidance:
    action: str = ""             # "increase_withholding", "decrease_withholding", "no_change"
    amount_per_paycheck: Optional[float] = None
    reason: str = ""


@dataclass
class Form1040Map:
    """Maps W-2 boxes to Form 1040 lines."""
    entries: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class EITCEligibility:
    potentially_eligible: bool = False
    income_below_threshold: bool = False
    max_income_threshold: float = 0.0
    note: str = ""


@dataclass
class W2IntelligenceReport:
    tax_year: Optional[int] = None

    bracket: TaxBracket = field(default_factory=TaxBracket)
    withholding_estimate: WithholdingEstimate = field(default_factory=WithholdingEstimate)
    retirement: RetirementAnalysis = field(default_factory=RetirementAnalysis)
    hsa: Optional[HSAAnalysis] = None
    pretax_gap: Optional[PreTaxGap] = None
    eitc: EITCEligibility = field(default_factory=EITCEligibility)
    w4_guidance: W4Guidance = field(default_factory=W4Guidance)
    form_1040_map: Form1040Map = field(default_factory=Form1040Map)

    additional_medicare_exposure: bool = False
    additional_medicare_note: str = ""

    corrected_w2_action: str = ""   # non-empty if W-2c

    # Flat narrative lists for the analyzer
    insights: List[str] = field(default_factory=list)
    recommendations: List[Dict[str, str]] = field(default_factory=list)
    filing_checklist: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tax_year": self.tax_year,
            "bracket": {
                "marginal_rate": self.bracket.marginal_rate,
                "effective_rate": self.bracket.effective_rate,
                "filing_assumed": self.bracket.filing_assumed,
                "note": self.bracket.note,
            },
            "withholding_estimate": {
                "withheld": self.withholding_estimate.withheld,
                "estimated_liability": self.withholding_estimate.estimated_liability,
                "delta": self.withholding_estimate.delta,
                "direction": self.withholding_estimate.direction,
                "confidence": self.withholding_estimate.confidence,
                "note": self.withholding_estimate.note,
            },
            "retirement": {
                "codes_found": self.retirement.codes_found,
                "total_contributed": self.retirement.total_contributed,
                "annual_limit": self.retirement.annual_limit,
                "catchup_available": self.retirement.catchup_available,
                "headroom": self.retirement.headroom,
                "maxed_out": self.retirement.maxed_out,
                "plan_type": self.retirement.plan_type,
                "note": self.retirement.note,
            },
            "hsa": {
                "employer_contribution": self.hsa.employer_contribution,
                "individual_limit": self.hsa.individual_limit,
                "family_limit": self.hsa.family_limit,
                "headroom_individual": self.hsa.headroom_individual,
                "headroom_family": self.hsa.headroom_family,
                "note": self.hsa.note,
            } if self.hsa else None,
            "pretax_gap": {
                "box1_wages": self.pretax_gap.box1_wages,
                "box3_ss_wages": self.pretax_gap.box3_ss_wages,
                "gap": self.pretax_gap.gap,
                "likely_pretax_benefits": self.pretax_gap.likely_pretax_benefits,
                "note": self.pretax_gap.note,
            } if self.pretax_gap else None,
            "eitc": {
                "potentially_eligible": self.eitc.potentially_eligible,
                "income_below_threshold": self.eitc.income_below_threshold,
                "max_income_threshold": self.eitc.max_income_threshold,
                "note": self.eitc.note,
            },
            "w4_guidance": {
                "action": self.w4_guidance.action,
                "amount_per_paycheck": self.w4_guidance.amount_per_paycheck,
                "reason": self.w4_guidance.reason,
            },
            "form_1040_map": self.form_1040_map.entries,
            "additional_medicare_exposure": self.additional_medicare_exposure,
            "additional_medicare_note": self.additional_medicare_note,
            "corrected_w2_action": self.corrected_w2_action,
            "insights": self.insights,
            "recommendations": self.recommendations,
            "filing_checklist": self.filing_checklist,
        }


# ---------------------------------------------------------------------------
# Main intelligence class
# ---------------------------------------------------------------------------

class W2Intelligence:
    """
    Advisory layer on top of W2Data.

    Usage:
        from app.parsers.extractors.w2_extractor import W2Extractor
        from app.parsers.extractors.w2_intelligence import W2Intelligence

        data = W2Extractor().extract(raw_text, tables)
        report = W2Intelligence().analyze(data)
        result_dict = report.to_dict()
    """

    def analyze(
        self,
        data,                           # W2Data instance
        prior_year_data=None,           # optional prior W2Data for YoY
        filing_status: str = "single",  # "single" | "mfj" — unknown defaults to single
        pay_periods: int = 26,          # biweekly default
    ) -> W2IntelligenceReport:
        """Run all advisory analyses and return a W2IntelligenceReport."""

        year = data.tax_year
        wages = data.wages_tips_compensation or 0.0
        fed_withheld = data.federal_income_tax_withheld or 0.0

        report = W2IntelligenceReport(tax_year=year)

        self._analyze_bracket(data, wages, year, filing_status, report)
        self._analyze_withholding(data, wages, fed_withheld, year, filing_status, pay_periods, report)
        self._analyze_retirement(data, year, report)
        self._analyze_hsa(data, year, report)
        self._analyze_pretax_gap(data, report)
        self._analyze_eitc(data, wages, year, report)
        self._analyze_additional_medicare(data, wages, year, report)
        self._build_w4_guidance(data, report, pay_periods)
        self._build_form_1040_map(data, report)
        self._corrected_w2_action(data, report)
        self._build_narratives(data, report, prior_year_data, filing_status)

        return report

    # ── Bracket ──────────────────────────────────────────────────────────────

    def _analyze_bracket(
        self, data, wages: float, year: Optional[int],
        filing: str, report: W2IntelligenceReport,
    ) -> None:
        if not wages:
            return
        fed_withheld = data.federal_income_tax_withheld or 0.0

        marginal = _marginal_rate(wages, year, filing)
        effective = round((fed_withheld / wages) * 100, 2) if wages > 0 else 0.0

        report.bracket = TaxBracket(
            marginal_rate=round(marginal * 100, 1),
            effective_rate=effective,
            filing_assumed=filing,
            note=(
                f"Marginal rate {marginal:.0%} based on ${wages:,.0f} wages "
                f"({'single' if filing == 'single' else 'married filing jointly'} filer assumed). "
                f"Effective withholding rate: {effective:.1f}%."
            ),
        )

    # ── Withholding estimate ─────────────────────────────────────────────────

    def _analyze_withholding(
        self, data, wages: float, fed_withheld: float, year: Optional[int],
        filing: str, pay_periods: int, report: W2IntelligenceReport,
    ) -> None:
        if not wages or not fed_withheld:
            return

        # Approximate AGI: wages minus pre-tax deductions (Box1 = taxable wages)
        # Box 12 pre-tax retirement reduces AGI further but Box1 already reflects it
        estimated_liability = _estimated_tax_liability(wages, year, filing)
        delta = round(fed_withheld - estimated_liability, 2)

        if delta > 500:
            direction = "refund"
        elif delta < -500:
            direction = "owe"
        else:
            direction = "on_track"

        note_parts = [
            f"Estimated federal tax liability: ~${estimated_liability:,.0f}",
            f"Federal withheld: ${fed_withheld:,.0f}",
        ]
        if direction == "refund":
            note_parts.append(
                f"Estimated refund: ~${abs(delta):,.0f} "
                f"(assumes standard deduction, {filing} filer, no credits)."
            )
        elif direction == "owe":
            note_parts.append(
                f"Estimated amount owed: ~${abs(delta):,.0f} — "
                f"consider adjusting W-4 or making estimated tax payments."
            )
        else:
            note_parts.append("Withholding appears roughly on track.")

        note_parts.append(
            "Note: This is an approximation only — actual liability depends on "
            "deductions, credits, other income, and filing status."
        )

        report.withholding_estimate = WithholdingEstimate(
            withheld=round(fed_withheld, 2),
            estimated_liability=estimated_liability,
            delta=delta,
            direction=direction,
            confidence="medium",
            note=" ".join(note_parts),
        )

    # ── Retirement ────────────────────────────────────────────────────────────

    def _analyze_retirement(
        self, data, year: Optional[int], report: W2IntelligenceReport,
    ) -> None:
        box12 = data.box_12_items or []

        # Map code → plan type and applicable limits table
        plan_map = {
            "D":  ("401(k)", _401K_LIMIT, _401K_CATCHUP),
            "AA": ("Roth 401(k)", _401K_LIMIT, _401K_CATCHUP),
            "E":  ("403(b)", _401K_LIMIT, _401K_CATCHUP),
            "BB": ("Roth 403(b)", _401K_LIMIT, _401K_CATCHUP),
            "G":  ("457(b)", _401K_LIMIT, _401K_CATCHUP),
            "EE": ("Roth 457(b)", _401K_LIMIT, _401K_CATCHUP),
            "S":  ("SIMPLE IRA", _SIMPLE_LIMIT, _SIMPLE_CATCHUP),
        }

        found_codes = []
        total = 0.0
        plan_type = ""
        limit_table = _401K_LIMIT
        catchup_table = _401K_CATCHUP

        for item in box12:
            code = item.code.upper()
            if code in plan_map:
                found_codes.append(code)
                total += item.amount
                if not plan_type:
                    plan_type, limit_table, catchup_table = plan_map[code]

        if not found_codes:
            # Check Box 13 retirement flag without Box 12 code (less common)
            if data.retirement_plan:
                report.retirement = RetirementAnalysis(
                    note="Box 13 retirement plan checked but no Box 12 deferral code found. "
                         "May be a defined benefit plan (pension) — no employee contribution limit applies.",
                )
            return

        annual_limit = _get(limit_table, year)
        catchup = _get(catchup_table, year)
        headroom = max(0.0, annual_limit - total)
        maxed_out = headroom < 1.0

        note_parts = [
            f"${total:,.2f} contributed to {plan_type}.",
            f"Annual IRS limit ({year or 'current'}): ${annual_limit:,.0f}.",
        ]
        if maxed_out:
            note_parts.append("✅ Contribution limit maxed out — excellent retirement savings.")
        else:
            note_parts.append(
                f"${headroom:,.2f} headroom remaining before limit. "
                f"Consider maximizing contributions to reduce taxable income."
            )
        note_parts.append(
            f"Catch-up contribution available (age 50+): +${catchup:,.0f}/year."
        )

        report.retirement = RetirementAnalysis(
            codes_found=found_codes,
            total_contributed=round(total, 2),
            annual_limit=annual_limit,
            catchup_available=catchup,
            headroom=round(headroom, 2),
            maxed_out=maxed_out,
            plan_type=plan_type,
            note=" ".join(note_parts),
        )

    # ── HSA ──────────────────────────────────────────────────────────────────

    def _analyze_hsa(self, data, year: Optional[int], report: W2IntelligenceReport) -> None:
        box12 = data.box_12_items or []
        employer_hsa = next(
            (item.amount for item in box12 if item.code.upper() == "W"), None
        )
        if employer_hsa is None:
            return  # No Code W — employer not contributing to HSA

        ind_limit = _get(_HSA_INDIVIDUAL, year)
        fam_limit = _get(_HSA_FAMILY, year)
        headroom_ind = max(0.0, ind_limit - employer_hsa)
        headroom_fam = max(0.0, fam_limit - employer_hsa)

        note = (
            f"Employer contributed ${employer_hsa:,.2f} to your HSA (Box 12 Code W). "
            f"Total 2025 HSA limit: ${ind_limit:,.0f} (individual) / ${fam_limit:,.0f} (family). "
            f"Remaining room for your own contributions: "
            f"${headroom_ind:,.2f} (individual) or ${headroom_fam:,.2f} (family). "
            "HSA contributions are triple-tax-advantaged: pre-tax, tax-free growth, "
            "tax-free withdrawals for qualified medical expenses."
        )

        report.hsa = HSAAnalysis(
            employer_contribution=round(employer_hsa, 2),
            individual_limit=ind_limit,
            family_limit=fam_limit,
            headroom_individual=round(headroom_ind, 2),
            headroom_family=round(headroom_fam, 2),
            note=note,
        )

    # ── Pre-tax benefit gap ───────────────────────────────────────────────────

    def _analyze_pretax_gap(self, data, report: W2IntelligenceReport) -> None:
        box1 = data.wages_tips_compensation
        box3 = data.social_security_wages
        if not box1 or not box3:
            return

        gap = box3 - box1  # positive = SS wages > taxable wages (pre-tax deductions)
        if abs(gap) < 50:  # noise threshold
            return

        if gap > 0:
            note = (
                f"Box 1 (${box1:,.2f}) is ${gap:,.2f} lower than Box 3 SS wages (${box3:,.2f}). "
                "This difference represents pre-tax deductions that reduce federal taxable income "
                "but still count toward Social Security: health insurance, dental/vision premiums, "
                "FSA contributions, or similar Section 125 cafeteria plan benefits."
            )
        else:
            # Box1 > Box3 — could be SS wage cap or other non-SS income (tips, etc.)
            note = (
                f"Box 1 (${box1:,.2f}) is ${abs(gap):,.2f} higher than Box 3 SS wages (${box3:,.2f}). "
                "If the SS wage cap is not the reason, verify Box 3 — some compensation types "
                "(e.g., certain non-cash fringe benefits) are taxable for income tax but not SS."
            )

        report.pretax_gap = PreTaxGap(
            box1_wages=box1,
            box3_ss_wages=box3,
            gap=round(gap, 2),
            likely_pretax_benefits=round(max(0.0, gap), 2),
            note=note,
        )

    # ── EITC eligibility ──────────────────────────────────────────────────────

    def _analyze_eitc(
        self, data, wages: float, year: Optional[int],
        report: W2IntelligenceReport,
    ) -> None:
        if not wages:
            return

        # Use the wide threshold (MFJ, 3+ children) — signal eligibility check
        # not a definitive determination
        upper = _get(_EITC_MAX_INCOME_MFJ_3PLUS, year)
        lower = _get(_EITC_MAX_INCOME_SINGLE_0CHILD, year)

        if wages < lower:
            report.eitc = EITCEligibility(
                potentially_eligible=True,
                income_below_threshold=True,
                max_income_threshold=upper,
                note=(
                    f"Wages of ${wages:,.0f} are below the EITC income threshold. "
                    "You may qualify for the Earned Income Tax Credit — check IRS EITC Assistant "
                    "(irs.gov/eitc) to confirm based on filing status and number of dependents."
                ),
            )
        elif wages < upper:
            report.eitc = EITCEligibility(
                potentially_eligible=True,
                income_below_threshold=False,
                max_income_threshold=upper,
                note=(
                    f"Wages of ${wages:,.0f} may be within EITC range depending on "
                    "filing status and qualifying children. "
                    "Check IRS EITC Assistant at irs.gov/eitc."
                ),
            )

    # ── Additional Medicare Tax ───────────────────────────────────────────────

    def _analyze_additional_medicare(
        self, data, wages: float, year: Optional[int],
        report: W2IntelligenceReport,
    ) -> None:
        if not wages:
            return
        dr = data.derived_rates
        threshold = ADDITIONAL_MEDICARE_THRESHOLD  # $200k single / $250k MFJ

        if wages > threshold or (dr and dr.additional_medicare_applied):
            report.additional_medicare_exposure = True
            report.additional_medicare_note = (
                f"Wages of ${wages:,.0f} exceed the ${threshold:,.0f} Additional Medicare Tax "
                f"threshold. An extra 0.9% Medicare tax applies to wages above this amount. "
                f"For single filers this is withheld automatically; for joint filers the threshold "
                f"is $250,000 — you may owe additional tax at filing if wages were below "
                f"${threshold:,.0f} per W-2 but combined household income exceeds $250,000."
            )

    # ── W-4 guidance ──────────────────────────────────────────────────────────

    def _build_w4_guidance(
        self, data, report: W2IntelligenceReport, pay_periods: int,
    ) -> None:
        we = report.withholding_estimate
        if not we.delta:
            return

        abs_delta = abs(we.delta)
        per_paycheck = round(abs_delta / pay_periods, 0)

        if we.direction == "owe" and abs_delta > 500:
            report.w4_guidance = W4Guidance(
                action="increase_withholding",
                amount_per_paycheck=per_paycheck,
                reason=(
                    f"You may owe ~${abs_delta:,.0f} at filing. "
                    f"To avoid underpayment, consider adding ~${per_paycheck:,.0f}/paycheck "
                    f"as additional withholding on your W-4 (Step 4c). "
                    "Alternatively, make a Q4 estimated tax payment before Jan 15."
                ),
            )
        elif we.direction == "refund" and abs_delta > 2_000:
            report.w4_guidance = W4Guidance(
                action="decrease_withholding",
                amount_per_paycheck=per_paycheck,
                reason=(
                    f"You're over-withheld by ~${abs_delta:,.0f} — giving the IRS an "
                    f"interest-free loan. Reducing W-4 withholding by ~${per_paycheck:,.0f}/paycheck "
                    "would put more money in each paycheck throughout the year."
                ),
            )
        else:
            report.w4_guidance = W4Guidance(
                action="no_change",
                reason="Withholding appears close to estimated liability — no W-4 change needed.",
            )

    # ── Form 1040 map ─────────────────────────────────────────────────────────

    def _build_form_1040_map(self, data, report: W2IntelligenceReport) -> None:
        entries = []

        def add(box_label: str, value: Optional[float], line_1040: str, description: str):
            if value is not None:
                entries.append({
                    "w2_box": box_label,
                    "value": f"${value:,.2f}",
                    "form_1040_line": line_1040,
                    "description": description,
                })

        add("Box 1 — Wages", data.wages_tips_compensation,
            "Line 1a", "Wages, salaries, tips")
        add("Box 2 — Federal withheld", data.federal_income_tax_withheld,
            "Schedule 3 / Line 25c", "Federal income tax withheld from W-2")
        add("Box 4 — SS tax withheld", data.social_security_tax_withheld,
            "Schedule 3 / Line 11", "Social Security tax withheld (if excess)")
        add("Box 6 — Medicare tax", data.medicare_tax_withheld,
            "Line 17 (Schedule 2)", "Additional Medicare Tax if applicable")
        add("Box 10 — Dependent care", data.dependent_care_benefits,
            "Form 2441 / Line 2", "Employer-provided dependent care benefits")
        add("Box 17 — State income tax", data.state_income_tax,
            "Schedule A / Line 5a", "State income tax paid (if itemizing)")

        # Box 12 specific
        for item in (data.box_12_items or []):
            if item.code == "DD":
                entries.append({
                    "w2_box": f"Box 12 [DD]",
                    "value": f"${item.amount:,.2f}",
                    "form_1040_line": "N/A (informational)",
                    "description": "Employer health coverage cost — not deductible",
                })
            elif item.code in {"D", "AA", "E", "BB", "G", "EE", "S"}:
                entries.append({
                    "w2_box": f"Box 12 [{item.code}]",
                    "value": f"${item.amount:,.2f}",
                    "form_1040_line": "Already reflected in Box 1",
                    "description": f"{item.description} — reduces taxable wages",
                })
            elif item.code == "W":
                entries.append({
                    "w2_box": "Box 12 [W] — HSA",
                    "value": f"${item.amount:,.2f}",
                    "form_1040_line": "Form 8889 / Line 9",
                    "description": "Employer HSA contributions",
                })

        report.form_1040_map = Form1040Map(entries=entries)

    # ── W-2c action ───────────────────────────────────────────────────────────

    def _corrected_w2_action(self, data, report: W2IntelligenceReport) -> None:
        if data.is_corrected:
            report.corrected_w2_action = (
                "⚠️ This is a W-2c (Corrected Wage Statement). "
                "If you already filed using the original W-2, you must file Form 1040-X "
                "(Amended Return). If you haven't filed yet, use the values from this W-2c. "
                "Verify corrected amounts match your final pay stubs."
            )

    # ── Narrative builder ─────────────────────────────────────────────────────

    def _build_narratives(
        self, data, report: W2IntelligenceReport,
        prior_year_data, filing_status: str,
    ) -> None:
        insights = []
        recs = []
        checklist = []

        wages = data.wages_tips_compensation or 0.0
        fed = data.federal_income_tax_withheld or 0.0

        # ── Bracket ──────────────────────────────────────────────────────
        b = report.bracket
        if b.marginal_rate:
            insights.append(
                f"📊 Marginal federal tax bracket: {b.marginal_rate:.0f}% "
                f"(effective withholding rate: {b.effective_rate:.1f}%). "
                f"Filing status assumed: {b.filing_assumed}."
            )

        # ── Withholding ───────────────────────────────────────────────────
        we = report.withholding_estimate
        if we.direction == "refund":
            insights.append(
                f"💰 Estimated refund: ~${abs(we.delta):,.0f} "
                f"(withheld ${we.withheld:,.0f} vs ~${we.estimated_liability:,.0f} liability)."
            )
        elif we.direction == "owe":
            insights.append(
                f"⚠️ Estimated amount owed at filing: ~${abs(we.delta):,.0f}."
            )
            recs.append({
                "title": "Adjust W-4 Withholding",
                "category": "tax_planning",
                "priority": "high",
                "description": report.w4_guidance.reason,
            })
        else:
            insights.append("✅ Federal withholding appears on track with estimated liability.")

        if we.direction == "refund" and abs(we.delta) > 2_000:
            recs.append({
                "title": "Consider Reducing Over-Withholding",
                "category": "cash_flow",
                "priority": "medium",
                "description": report.w4_guidance.reason,
            })

        # ── Retirement ────────────────────────────────────────────────────
        ret = report.retirement
        if ret.total_contributed > 0:
            if ret.maxed_out:
                insights.append(
                    f"🏦 {ret.plan_type}: ${ret.total_contributed:,.0f} contributed — "
                    f"limit maxed out (${ret.annual_limit:,.0f}). Excellent!"
                )
            else:
                insights.append(
                    f"🏦 {ret.plan_type}: ${ret.total_contributed:,.0f} contributed. "
                    f"${ret.headroom:,.0f} remaining before ${ret.annual_limit:,.0f} IRS limit."
                )
                recs.append({
                    "title": f"Maximize {ret.plan_type} Contributions",
                    "category": "retirement",
                    "priority": "medium",
                    "description": (
                        f"You have ${ret.headroom:,.0f} remaining before the "
                        f"${ret.annual_limit:,.0f} IRS limit. "
                        f"Each additional dollar contributed reduces your taxable income "
                        f"at your {b.marginal_rate:.0f}% marginal rate, saving "
                        f"~${ret.headroom * b.marginal_rate / 100:,.0f} in federal tax. "
                        f"Catch-up contribution: +${ret.catchup_available:,.0f} if age 50+."
                    ),
                })
        elif data.retirement_plan:
            insights.append(
                "🏦 Box 13 'Retirement Plan' is checked — likely a defined benefit (pension) plan."
            )
            checklist.append("Confirm pension plan details with your employer's HR.")

        # ── HSA ───────────────────────────────────────────────────────────
        if report.hsa:
            hsa = report.hsa
            insights.append(
                f"🏥 HSA: Employer contributed ${hsa.employer_contribution:,.0f} (Code W). "
                f"Individual limit: ${hsa.individual_limit:,.0f} — "
                f"${hsa.headroom_individual:,.0f} room for your own contributions."
            )
            if hsa.headroom_individual > 200:
                recs.append({
                    "title": "Contribute to HSA — Triple Tax Advantage",
                    "category": "tax_planning",
                    "priority": "medium",
                    "description": (
                        f"You can still contribute ${hsa.headroom_individual:,.0f} to your HSA "
                        f"(${hsa.headroom_family:,.0f} family). "
                        "HSA funds are pre-tax in, tax-free growth, and tax-free out for "
                        "medical expenses. Unused funds roll over forever — it's a de facto "
                        "second retirement account for healthcare costs."
                    ),
                })

        # ── Pre-tax gap ───────────────────────────────────────────────────
        if report.pretax_gap and report.pretax_gap.gap > 50:
            pg = report.pretax_gap
            insights.append(
                f"💼 Pre-tax benefits: Box 1 wages (${pg.box1_wages:,.0f}) are "
                f"${pg.gap:,.0f} lower than SS wages (${pg.box3_ss_wages:,.0f}) — "
                "likely health insurance, FSA, or other Section 125 benefits."
            )

        # ── EITC ──────────────────────────────────────────────────────────
        eitc = report.eitc
        if eitc.potentially_eligible:
            insights.append(
                f"💵 EITC: Based on wages of ${wages:,.0f}, you may qualify for "
                "the Earned Income Tax Credit — check irs.gov/eitc."
            )
            recs.append({
                "title": "Check EITC Eligibility",
                "category": "tax_credits",
                "priority": "high",
                "description": eitc.note,
            })

        # ── Additional Medicare ───────────────────────────────────────────
        if report.additional_medicare_exposure:
            insights.append(
                f"⚕️ Additional Medicare Tax exposure: wages ${wages:,.0f} "
                f"exceed ${ADDITIONAL_MEDICARE_THRESHOLD:,.0f} threshold."
            )
            recs.append({
                "title": "Additional Medicare Tax (0.9%)",
                "category": "tax_compliance",
                "priority": "medium",
                "description": report.additional_medicare_note,
            })

        # ── W-2c ─────────────────────────────────────────────────────────
        if report.corrected_w2_action:
            recs.insert(0, {
                "title": "Corrected W-2 — Action Required",
                "category": "compliance",
                "priority": "high",
                "description": report.corrected_w2_action,
            })

        # ── Year-over-year ────────────────────────────────────────────────
        if prior_year_data and prior_year_data.wages_tips_compensation and wages:
            prior_wages = prior_year_data.wages_tips_compensation
            yoy_change = wages - prior_wages
            yoy_pct = (yoy_change / prior_wages) * 100
            direction = "increased" if yoy_change >= 0 else "decreased"
            insights.append(
                f"📈 Year-over-year: Wages {direction} by "
                f"${abs(yoy_change):,.0f} ({abs(yoy_pct):.1f}%) vs "
                f"{prior_year_data.tax_year or 'prior year'}."
            )

        # ── Filing checklist ──────────────────────────────────────────────
        checklist.extend([
            f"Enter Box 1 (${data.wages_tips_compensation:,.2f}) on Form 1040 Line 1a."
            if data.wages_tips_compensation else "Enter wages on Form 1040 Line 1a.",
            f"Enter Box 2 federal withholding on Schedule 3 Line 25c."
            if data.federal_income_tax_withheld else "Locate federal withholding (Box 2).",
        ])
        if data.box_12_items:
            codes = ", ".join(f"[{i.code}]" for i in data.box_12_items)
            checklist.append(f"Review Box 12 codes {codes} — see Form 1040 map above.")
        if data.state_income_tax:
            checklist.append(
                f"Enter Box 17 state withholding (${data.state_income_tax:,.2f}) "
                "on your state return and Schedule A if itemizing."
            )
        if report.hsa:
            checklist.append("File Form 8889 (Health Savings Accounts) — HSA activity on W-2.")
        if data.dependent_care_benefits:
            checklist.append("File Form 2441 (Child and Dependent Care Expenses).")
        if data.nonqualified_plans:
            checklist.append(
                f"Box 11 (${data.nonqualified_plans:,.2f}) — nonqualified deferred "
                "compensation: verify with employer whether already in Box 1."
            )
        if data.statutory_employee:
            checklist.append(
                "Box 13 'Statutory Employee' checked — report income on Schedule C, "
                "not as regular wages."
            )
        if report.retirement.codes_found:
            checklist.append(
                f"Verify {report.retirement.plan_type} contributions match "
                "your benefit election paperwork."
            )
        checklist.append("Keep this W-2 for at least 4 years (IRS audit statute of limitations).")

        report.insights = insights
        report.recommendations = recs
        report.filing_checklist = checklist