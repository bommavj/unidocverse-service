from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONSTANTS & BENCHMARKS
# ─────────────────────────────────────────────

HIGH_DEDUCTIBLE_THRESHOLD = 2000.0
VERY_HIGH_DEDUCTIBLE_THRESHOLD = 5000.0

LOW_COVERAGE_LIMIT = 50000.0
VERY_LOW_COVERAGE_LIMIT = 25000.0

MIN_RECOMMENDED_LIABILITY = 100000.0
MIN_RECOMMENDED_PROPERTY = 100000.0

PREMIUM_HIGH_THRESHOLD = 3000.0
PREMIUM_LOW_THRESHOLD = 300.0

COVERAGE_TO_PREMIUM_GOOD = 50.0   # e.g., 50x coverage vs premium
COVERAGE_TO_PREMIUM_WEAK = 10.0

RISK_SCORE_MAX = 100

COVERAGE_TYPE_LIABILITY = {"liability", "bodily injury", "property damage"}
COVERAGE_TYPE_PROPERTY = {"property", "dwelling", "building", "contents", "personal property"}
COVERAGE_TYPE_UM = {"uninsured", "underinsured", "UM", "UIM"}

RISK_WEIGHTS = {
    "no_coverage_items": 25,
    "missing_exclusions": 10,
    "high_deductible": 15,
    "very_high_deductible": 25,
    "low_coverage_items": 10,
    "very_low_coverage_items": 15,
    "missing_liability": 20,
    "missing_property": 20,
    "missing_um": 10,
    "low_liability": 15,
    "low_property": 15,
    "high_premium_low_coverage": 15,
    "incomplete_policy": 15,
}

# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class CoverageItem:
    type: str
    limit: float
    raw: Dict[str, Any]

    @staticmethod
    def from_raw(item: Dict[str, Any]) -> "CoverageItem":
        t = (item.get("type") or item.get("coverage_type") or "").strip()
        try:
            limit_val = item.get("limit")
            if isinstance(limit_val, (int, float)):
                limit = float(limit_val)
            else:
                s = str(limit_val or "").replace(",", "").strip()
                limit = float(s) if s else 0.0
        except Exception:
            limit = 0.0
        return CoverageItem(type=t, limit=limit, raw=item)


@dataclass
class CoverageSummary:
    total_coverage_limit: float
    coverage_count: int
    low_coverage_items: int
    very_low_coverage_items: int
    liability_coverage: float
    property_coverage: float
    um_coverage: float
    missing_liability: bool
    missing_property: bool
    missing_um: bool
    low_liability: bool
    low_property: bool

    def to_kv_list(self) -> List[Dict[str, Any]]:
        return [
            {"key": "Total Coverage Limit", "value": f"{self.total_coverage_limit:,.2f}"},
            {"key": "Coverage Items", "value": self.coverage_count},
            {"key": "Low Coverage Items", "value": self.low_coverage_items},
            {"key": "Very Low Coverage Items", "value": self.very_low_coverage_items},
            {"key": "Liability Coverage", "value": f"{self.liability_coverage:,.2f}"},
            {"key": "Property Coverage", "value": f"{self.property_coverage:,.2f}"},
            {"key": "UM/UIM Coverage", "value": f"{self.um_coverage:,.2f}"},
            {"key": "Missing Liability", "value": self.missing_liability},
            {"key": "Missing Property", "value": self.missing_property},
            {"key": "Missing UM/UIM", "value": self.missing_um},
            {"key": "Low Liability vs Benchmark", "value": self.low_liability},
            {"key": "Low Property vs Benchmark", "value": self.low_property},
        ]


@dataclass
class DeductibleAnalysis:
    deductible: float
    high_deductible: bool
    very_high_deductible: bool
    deductible_to_limit_ratio: Optional[float]

    def to_kv_list(self) -> List[Dict[str, Any]]:
        return [
            {"key": "Deductible", "value": f"{self.deductible:,.2f}"},
            {"key": "High Deductible", "value": self.high_deductible},
            {"key": "Very High Deductible", "value": self.very_high_deductible},
            {
                "key": "Deductible / Total Coverage",
                "value": f"{self.deductible_to_limit_ratio:.4f}" if self.deductible_to_limit_ratio is not None else "N/A",
            },
        ]


@dataclass
class PremiumAnalysis:
    premium_amount: float
    premium_level: str
    coverage_to_premium_ratio: float
    high_premium_low_coverage: bool

    def to_kv_list(self) -> List[Dict[str, Any]]:
        return [
            {"key": "Premium Amount", "value": f"{self.premium_amount:,.2f}"},
            {"key": "Premium Level", "value": self.premium_level},
            {
                "key": "Coverage/Premium Ratio",
                "value": f"{self.coverage_to_premium_ratio:,.2f}" if self.coverage_to_premium_ratio else "N/A",
            },
            {"key": "High Premium / Low Coverage", "value": self.high_premium_low_coverage},
        ]


@dataclass
class ExclusionAnalysis:
    exclusions_count: int
    missing_exclusions: bool
    exclusion_risk_level: str

    def to_kv_list(self) -> List[Dict[str, Any]]:
        return [
            {"key": "Exclusions Count", "value": self.exclusions_count},
            {"key": "Missing Exclusions", "value": self.missing_exclusions},
            {"key": "Exclusion Risk Level", "value": self.exclusion_risk_level},
        ]


@dataclass
class GapAnalysis:
    has_gaps: bool
    gap_descriptions: List[str]
    incomplete_policy: bool

    def to_kv_list(self) -> List[Dict[str, Any]]:
        return [
            {"key": "Has Gaps", "value": self.has_gaps},
            {"key": "Incomplete Policy", "value": self.incomplete_policy},
            {
                "key": "Gap Summary",
                "value": "; ".join(self.gap_descriptions) if self.gap_descriptions else "No major gaps detected",
            },
        ]


@dataclass
class RiskSignal:
    code: str
    message: str
    weight: int

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "weight": self.weight}


@dataclass
class Recommendation:
    priority: str
    category: str
    title: str
    description: str
    potential_savings: float = 0.0
    impact: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyScore:
    score: int
    grade: str
    label: str
    risk_score: int
    coverage_score: int
    deductible_score: int
    premium_score: int
    completeness_score: int

    def to_kv_list(self) -> List[Dict[str, Any]]:
        return [
            {"key": "Overall Score", "value": self.score},
            {"key": "Grade", "value": self.grade},
            {"key": "Label", "value": self.label},
            {"key": "Risk Score", "value": self.risk_score},
            {"key": "Coverage Score", "value": self.coverage_score},
            {"key": "Deductible Score", "value": self.deductible_score},
            {"key": "Premium Score", "value": self.premium_score},
            {"key": "Completeness Score", "value": self.completeness_score},
        ]


@dataclass
class InsuranceIntelligenceReport:
    coverage_summary: CoverageSummary
    deductible_analysis: DeductibleAnalysis
    premium_analysis: PremiumAnalysis
    exclusion_analysis: ExclusionAnalysis
    gap_analysis: GapAnalysis
    risk_signals: List[RiskSignal]
    recommendations: List[Recommendation]
    policy_score: PolicyScore

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coverage_summary": asdict(self.coverage_summary),
            "deductible_analysis": asdict(self.deductible_analysis),
            "premium_analysis": asdict(self.premium_analysis),
            "exclusion_analysis": asdict(self.exclusion_analysis),
            "gap_analysis": asdict(self.gap_analysis),
            "risk_signals": [r.to_dict() for r in self.risk_signals],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "policy_score": {
                "raw": asdict(self.policy_score),
                "kv": self.policy_score.to_kv_list(),
            },
            "ui_blocks": {
                "coverage_summary": self.coverage_summary.to_kv_list(),
                "deductible_analysis": self.deductible_analysis.to_kv_list(),
                "premium_analysis": self.premium_analysis.to_kv_list(),
                "exclusion_analysis": self.exclusion_analysis.to_kv_list(),
                "gap_analysis": self.gap_analysis.to_kv_list(),
                "policy_score": self.policy_score.to_kv_list(),
                "risk_signals": [r.to_dict() for r in self.risk_signals],
                "recommendations": [r.to_dict() for r in self.recommendations],
            },
        }

    def to_kv_list(self) -> List[Dict[str, Any]]:
        # Flattened key/value summary if needed
        kv: List[Dict[str, Any]] = []
        kv.extend(self.coverage_summary.to_kv_list())
        kv.extend(self.deductible_analysis.to_kv_list())
        kv.extend(self.premium_analysis.to_kv_list())
        kv.extend(self.exclusion_analysis.to_kv_list())
        kv.extend(self.gap_analysis.to_kv_list())
        kv.extend(self.policy_score.to_kv_list())
        return kv


# ─────────────────────────────────────────────
# INTELLIGENCE ENGINE
# ─────────────────────────────────────────────

class InsurancePolicyIntelligence:
    """
    Consumes InsurancePolicyAnalyzer result and produces a rich intelligence report.
    """

    # ---------- helpers ----------

    @staticmethod
    def safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return float(value)
            s = str(value).replace(",", "").strip()
            return float(s) if s else default
        except Exception:
            return default

    @staticmethod
    def _normalize_type(t: str) -> str:
        return (t or "").strip().lower()

    # ---------- public API ----------

    def analyze(self, result: Dict[str, Any]) -> InsuranceIntelligenceReport:
        coverage_items_raw = result.get("coverage_items", []) or []
        exclusions = result.get("exclusions", []) or []
        policy_risks = result.get("policy_risks", []) or []
        policy_risk_count = int(result.get("policy_risk_count", len(policy_risks)))

        premium_amount = self.safe_float(result.get("premium_amount"))
        deductible = self.safe_float(result.get("deductible"))
        total_coverage_limit = self.safe_float(result.get("total_coverage_limit"))

        coverage_items = [CoverageItem.from_raw(i) for i in coverage_items_raw]

        coverage_summary = self._build_coverage_summary(coverage_items, total_coverage_limit)
        deductible_analysis = self._build_deductible_analysis(deductible, total_coverage_limit)
        premium_analysis = self._build_premium_analysis(premium_amount, total_coverage_limit)
        exclusion_analysis = self._build_exclusion_analysis(exclusions)
        gap_analysis = self._build_gap_analysis(
            coverage_summary,
            exclusion_analysis,
            coverage_items,
        )

        risk_signals = self._build_risk_signals(
            policy_risks,
            policy_risk_count,
            coverage_summary,
            deductible_analysis,
            premium_analysis,
            exclusion_analysis,
            gap_analysis,
        )

        recommendations = self._build_recommendations(
            coverage_summary,
            deductible_analysis,
            premium_analysis,
            exclusion_analysis,
            gap_analysis,
            risk_signals,
        )

        policy_score = self._build_policy_score(
            coverage_summary,
            deductible_analysis,
            premium_analysis,
            exclusion_analysis,
            gap_analysis,
            risk_signals,
        )

        return InsuranceIntelligenceReport(
            coverage_summary=coverage_summary,
            deductible_analysis=deductible_analysis,
            premium_analysis=premium_analysis,
            exclusion_analysis=exclusion_analysis,
            gap_analysis=gap_analysis,
            risk_signals=risk_signals,
            recommendations=recommendations,
            policy_score=policy_score,
        )

    # ---------- coverage summary ----------

    def _build_coverage_summary(
        self,
        coverage_items: List[CoverageItem],
        total_coverage_limit: float,
    ) -> CoverageSummary:
        coverage_count = len(coverage_items)

        low_coverage_items = 0
        very_low_coverage_items = 0

        liability_coverage = 0.0
        property_coverage = 0.0
        um_coverage = 0.0

        for item in coverage_items:
            limit = item.limit
            t_norm = self._normalize_type(item.type)

            if 0 < limit < LOW_COVERAGE_LIMIT:
                low_coverage_items += 1
            if 0 < limit < VERY_LOW_COVERAGE_LIMIT:
                very_low_coverage_items += 1

            if any(k in t_norm for k in COVERAGE_TYPE_LIABILITY):
                liability_coverage += limit
            if any(k in t_norm for k in COVERAGE_TYPE_PROPERTY):
                property_coverage += limit
            if any(k in t_norm for k in COVERAGE_TYPE_UM):
                um_coverage += limit

        missing_liability = liability_coverage <= 0
        missing_property = property_coverage <= 0
        missing_um = um_coverage <= 0

        low_liability = liability_coverage > 0 and liability_coverage < MIN_RECOMMENDED_LIABILITY
        low_property = property_coverage > 0 and property_coverage < MIN_RECOMMENDED_PROPERTY

        # if total_coverage_limit is zero, recompute from items
        if total_coverage_limit <= 0:
            total_coverage_limit = sum(i.limit for i in coverage_items)

        return CoverageSummary(
            total_coverage_limit=round(total_coverage_limit, 2),
            coverage_count=coverage_count,
            low_coverage_items=low_coverage_items,
            very_low_coverage_items=very_low_coverage_items,
            liability_coverage=round(liability_coverage, 2),
            property_coverage=round(property_coverage, 2),
            um_coverage=round(um_coverage, 2),
            missing_liability=missing_liability,
            missing_property=missing_property,
            missing_um=missing_um,
            low_liability=low_liability,
            low_property=low_property,
        )

    # ---------- deductible analysis ----------

    def _build_deductible_analysis(
        self,
        deductible: float,
        total_coverage_limit: float,
    ) -> DeductibleAnalysis:
        high_deductible = deductible > HIGH_DEDUCTIBLE_THRESHOLD
        very_high_deductible = deductible > VERY_HIGH_DEDUCTIBLE_THRESHOLD

        if total_coverage_limit > 0:
            ratio = deductible / total_coverage_limit
        else:
            ratio = None

        return DeductibleAnalysis(
            deductible=round(deductible, 2),
            high_deductible=high_deductible,
            very_high_deductible=very_high_deductible,
            deductible_to_limit_ratio=ratio,
        )

    # ---------- premium analysis ----------

    def _build_premium_analysis(
        self,
        premium_amount: float,
        total_coverage_limit: float,
    ) -> PremiumAnalysis:
        if premium_amount <= 0:
            premium_level = "unknown"
        elif premium_amount < PREMIUM_LOW_THRESHOLD:
            premium_level = "low"
        elif premium_amount <= PREMIUM_HIGH_THRESHOLD:
            premium_level = "normal"
        else:
            premium_level = "high"

        if premium_amount > 0:
            coverage_to_premium_ratio = total_coverage_limit / premium_amount
        else:
            coverage_to_premium_ratio = 0.0

        high_premium_low_coverage = (
            premium_level == "high" and coverage_to_premium_ratio < COVERAGE_TO_PREMIUM_WEAK
        )

        return PremiumAnalysis(
            premium_amount=round(premium_amount, 2),
            premium_level=premium_level,
            coverage_to_premium_ratio=round(coverage_to_premium_ratio, 2),
            high_premium_low_coverage=high_premium_low_coverage,
        )

    # ---------- exclusion analysis ----------

    def _build_exclusion_analysis(
        self,
        exclusions: List[Any],
    ) -> ExclusionAnalysis:
        exclusions_count = len(exclusions)
        missing_exclusions = exclusions_count == 0

        if missing_exclusions:
            exclusion_risk_level = "high"
        elif exclusions_count < 3:
            exclusion_risk_level = "medium"
        else:
            exclusion_risk_level = "normal"

        return ExclusionAnalysis(
            exclusions_count=exclusions_count,
            missing_exclusions=missing_exclusions,
            exclusion_risk_level=exclusion_risk_level,
        )

    # ---------- gap analysis ----------

    def _build_gap_analysis(
        self,
        coverage_summary: CoverageSummary,
        exclusion_analysis: ExclusionAnalysis,
        coverage_items: List[CoverageItem],
    ) -> GapAnalysis:
        gap_descriptions: List[str] = []

        if coverage_summary.missing_liability:
            gap_descriptions.append("No explicit liability coverage detected.")
        if coverage_summary.missing_property:
            gap_descriptions.append("No explicit property coverage detected.")
        if coverage_summary.missing_um:
            gap_descriptions.append("No uninsured/underinsured motorist coverage detected.")
        if coverage_summary.low_liability:
            gap_descriptions.append(
                f"Liability coverage ({coverage_summary.liability_coverage:,.0f}) is below common benchmarks."
            )
        if coverage_summary.low_property:
            gap_descriptions.append(
                f"Property coverage ({coverage_summary.property_coverage:,.0f}) is below common benchmarks."
            )
        if coverage_summary.very_low_coverage_items > 0:
            gap_descriptions.append(
                f"{coverage_summary.very_low_coverage_items} coverage item(s) have very low limits."
            )

        if exclusion_analysis.missing_exclusions:
            gap_descriptions.append("No exclusions listed — policy wording may be incomplete.")

        incomplete_policy = (
            coverage_summary.coverage_count == 0
            or exclusion_analysis.missing_exclusions
        )

        has_gaps = len(gap_descriptions) > 0

        return GapAnalysis(
            has_gaps=has_gaps,
            gap_descriptions=gap_descriptions,
            incomplete_policy=incomplete_policy,
        )

    # ---------- risk signals ----------

    def _build_risk_signals(
        self,
        policy_risks: List[str],
        policy_risk_count: int,
        coverage_summary: CoverageSummary,
        deductible_analysis: DeductibleAnalysis,
        premium_analysis: PremiumAnalysis,
        exclusion_analysis: ExclusionAnalysis,
        gap_analysis: GapAnalysis,
    ) -> List[RiskSignal]:
        signals: List[RiskSignal] = []

        # Start with analyzer-provided risks
        for msg in policy_risks:
            signals.append(RiskSignal(code="analyzer_risk", message=msg, weight=5))

        # Derived risks
        if coverage_summary.coverage_count == 0:
            signals.append(
                RiskSignal(
                    code="no_coverage_items",
                    message="No coverage items detected — policy may be incomplete.",
                    weight=RISK_WEIGHTS["no_coverage_items"],
                )
            )

        if exclusion_analysis.missing_exclusions:
            signals.append(
                RiskSignal(
                    code="missing_exclusions",
                    message="No exclusions listed — policy wording may be incomplete.",
                    weight=RISK_WEIGHTS["missing_exclusions"],
                )
            )

        if deductible_analysis.very_high_deductible:
            signals.append(
                RiskSignal(
                    code="very_high_deductible",
                    message="Deductible is very high relative to typical policies.",
                    weight=RISK_WEIGHTS["very_high_deductible"],
                )
            )
        elif deductible_analysis.high_deductible:
            signals.append(
                RiskSignal(
                    code="high_deductible",
                    message="High deductible may increase out-of-pocket costs.",
                    weight=RISK_WEIGHTS["high_deductible"],
                )
            )

        if coverage_summary.low_coverage_items > 0:
            signals.append(
                RiskSignal(
                    code="low_coverage_items",
                    message=f"{coverage_summary.low_coverage_items} coverage item(s) have relatively low limits.",
                    weight=RISK_WEIGHTS["low_coverage_items"],
                )
            )

        if coverage_summary.very_low_coverage_items > 0:
            signals.append(
                RiskSignal(
                    code="very_low_coverage_items",
                    message=f"{coverage_summary.very_low_coverage_items} coverage item(s) have very low limits.",
                    weight=RISK_WEIGHTS["very_low_coverage_items"],
                )
            )

        if coverage_summary.missing_liability:
            signals.append(
                RiskSignal(
                    code="missing_liability",
                    message="Liability coverage not clearly specified.",
                    weight=RISK_WEIGHTS["missing_liability"],
                )
            )

        if coverage_summary.missing_property:
            signals.append(
                RiskSignal(
                    code="missing_property",
                    message="Property coverage not clearly specified.",
                    weight=RISK_WEIGHTS["missing_property"],
                )
            )

        if coverage_summary.missing_um:
            signals.append(
                RiskSignal(
                    code="missing_um",
                    message="Uninsured/underinsured motorist coverage not clearly specified.",
                    weight=RISK_WEIGHTS["missing_um"],
                )
            )

        if coverage_summary.low_liability:
            signals.append(
                RiskSignal(
                    code="low_liability",
                    message="Liability coverage appears low relative to common benchmarks.",
                    weight=RISK_WEIGHTS["low_liability"],
                )
            )

        if coverage_summary.low_property:
            signals.append(
                RiskSignal(
                    code="low_property",
                    message="Property coverage appears low relative to common benchmarks.",
                    weight=RISK_WEIGHTS["low_property"],
                )
            )

        if premium_analysis.high_premium_low_coverage:
            signals.append(
                RiskSignal(
                    code="high_premium_low_coverage",
                    message="Premium appears high relative to total coverage limits.",
                    weight=RISK_WEIGHTS["high_premium_low_coverage"],
                )
            )

        if gap_analysis.incomplete_policy:
            signals.append(
                RiskSignal(
                    code="incomplete_policy",
                    message="Policy appears incomplete (missing coverage items and/or exclusions).",
                    weight=RISK_WEIGHTS["incomplete_policy"],
                )
            )

        # Deduplicate by (code, message)
        unique: Dict[Tuple[str, str], RiskSignal] = {}
        for s in signals:
            key = (s.code, s.message)
            if key not in unique:
                unique[key] = s

        return list(unique.values())

    # ---------- recommendations ----------

    def _build_recommendations(
        self,
        coverage_summary: CoverageSummary,
        deductible_analysis: DeductibleAnalysis,
        premium_analysis: PremiumAnalysis,
        exclusion_analysis: ExclusionAnalysis,
        gap_analysis: GapAnalysis,
        risk_signals: List[RiskSignal],
    ) -> List[Recommendation]:
        recs: List[Recommendation] = []

        # Coverage gaps
        if coverage_summary.missing_liability or coverage_summary.low_liability:
            recs.append(
                Recommendation(
                    priority="high",
                    category="coverage",
                    title="Review liability coverage limits",
                    description="Liability coverage appears missing or below common benchmarks. Consider increasing limits to protect against large claims.",
                    potential_savings=0.0,
                    impact="high",
                )
            )

        if coverage_summary.missing_property or coverage_summary.low_property:
            recs.append(
                Recommendation(
                    priority="high",
                    category="coverage",
                    title="Review property coverage limits",
                    description="Property coverage appears missing or below common benchmarks. Ensure replacement or repair costs are adequately covered.",
                    potential_savings=0.0,
                    impact="high",
                )
            )

        if coverage_summary.missing_um:
            recs.append(
                Recommendation(
                    priority="medium",
                    category="coverage",
                    title="Consider UM/UIM coverage",
                    description="Uninsured/underinsured motorist coverage is not clearly specified. Consider adding or confirming this protection.",
                    potential_savings=0.0,
                    impact="medium",
                )
            )

        if coverage_summary.very_low_coverage_items > 0:
            recs.append(
                Recommendation(
                    priority="medium",
                    category="coverage",
                    title="Increase low coverage limits",
                    description=f"{coverage_summary.very_low_coverage_items} coverage item(s) have very low limits. Consider increasing these to reduce exposure.",
                    potential_savings=0.0,
                    impact="medium",
                )
            )

        # Deductible
        if deductible_analysis.very_high_deductible or deductible_analysis.high_deductible:
            recs.append(
                Recommendation(
                    priority="medium",
                    category="deductible",
                    title="Evaluate deductible level",
                    description="Deductible appears high. Consider whether a lower deductible would better match your risk tolerance and cash reserves.",
                    potential_savings=0.0,
                    impact="medium",
                )
            )

        # Exclusions
        if exclusion_analysis.missing_exclusions:
            recs.append(
                Recommendation(
                    priority="high",
                    category="exclusions",
                    title="Request detailed exclusions",
                    description="No exclusions are listed. Request a detailed exclusions section to avoid surprises at claim time.",
                    potential_savings=0.0,
                    impact="high",
                )
            )

        # Premium vs coverage
        if premium_analysis.high_premium_low_coverage:
            recs.append(
                Recommendation(
                    priority="medium",
                    category="premium",
                    title="Compare premium vs coverage",
                    description="Premium appears high relative to coverage limits. Consider shopping quotes or adjusting coverage to improve value.",
                    potential_savings=0.0,
                    impact="medium",
                )
            )

        # Incomplete policy
        if gap_analysis.incomplete_policy:
            recs.append(
                Recommendation(
                    priority="high",
                    category="completeness",
                    title="Clarify policy completeness",
                    description="Policy appears incomplete (missing coverage items and/or exclusions). Request a full declarations page and policy wording.",
                    potential_savings=0.0,
                    impact="high",
                )
            )

        # If no specific recs, add a generic one
        if not recs:
            recs.append(
                Recommendation(
                    priority="low",
                    category="general",
                    title="Review policy with an advisor",
                    description="No major issues detected, but a periodic review with an insurance professional is recommended.",
                    potential_savings=0.0,
                    impact="low",
                )
            )

        return recs

    # ---------- scoring ----------

    def _build_policy_score(
        self,
        coverage_summary: CoverageSummary,
        deductible_analysis: DeductibleAnalysis,
        premium_analysis: PremiumAnalysis,
        exclusion_analysis: ExclusionAnalysis,
        gap_analysis: GapAnalysis,
        risk_signals: List[RiskSignal],
    ) -> PolicyScore:
        # Risk score: based on weights
        total_weight = sum(s.weight for s in risk_signals)
        risk_score = min(RISK_SCORE_MAX, total_weight)
        risk_score_component = max(0, RISK_SCORE_MAX - risk_score)

        # Coverage score
        coverage_score = 50
        if coverage_summary.missing_liability or coverage_summary.missing_property:
            coverage_score -= 20
        if coverage_summary.missing_um:
            coverage_score -= 10
        if coverage_summary.low_liability:
            coverage_score -= 10
        if coverage_summary.low_property:
            coverage_score -= 10
        if coverage_summary.very_low_coverage_items > 0:
            coverage_score -= 10
        coverage_score = max(0, min(100, coverage_score))

        # Deductible score
        deductible_score = 50
        if deductible_analysis.very_high_deductible:
            deductible_score -= 20
        elif deductible_analysis.high_deductible:
            deductible_score -= 10
        deductible_score = max(0, min(100, deductible_score))

        # Premium score
        premium_score = 50
        if premium_analysis.high_premium_low_coverage:
            premium_score -= 15
        elif premium_analysis.premium_level == "high":
            premium_score -= 5
        premium_score = max(0, min(100, premium_score))

        # Completeness score
        completeness_score = 50
        if gap_analysis.incomplete_policy:
            completeness_score -= 20
        if exclusion_analysis.missing_exclusions:
            completeness_score -= 10
        completeness_score = max(0, min(100, completeness_score))

        # Combine into overall score (simple weighted blend)
        overall = (
            0.30 * risk_score_component
            + 0.25 * coverage_score
            + 0.15 * deductible_score
            + 0.15 * premium_score
            + 0.15 * completeness_score
        )
        overall_int = int(round(overall))

        if overall_int >= 85:
            grade, label = "A", "Strong overall policy structure."
        elif overall_int >= 70:
            grade, label = "B", "Generally solid with some areas to optimize."
        elif overall_int >= 55:
            grade, label = "C", "Mixed — several coverage or structure issues to review."
        elif overall_int >= 40:
            grade, label = "D", "Significant gaps or risks — careful review recommended."
        else:
            grade, label = "F", "High risk and/or incomplete — urgent review recommended."

        return PolicyScore(
            score=overall_int,
            grade=grade,
            label=label,
            risk_score=risk_score_component,
            coverage_score=coverage_score,
            deductible_score=deductible_score,
            premium_score=premium_score,
            completeness_score=completeness_score,
        )
