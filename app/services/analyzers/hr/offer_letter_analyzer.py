# app/services/analyzers/hr/offer_letter_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class OfferLetterAnalyzer(BaseAnalyzer):
    """
    Analyzer for offer letters:
    - Candidate, role, start date
    - Salary, bonus, equity, benefits
    - Employment type (FT/PT/Contract)
    - Risks: missing details, vague compensation terms
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📄 Advanced offer letter analysis: {file_path}")

        offer = self._extract_offer(file_path, text, metadata)
        metrics = self._salary_metrics(offer)
        risks = self._risk_checks(offer, metrics)
        insights = self._generate_insights(offer, metrics, risks)
        recommendations = self._generate_recommendations(offer, metrics, risks)

        result: Dict[str, Any] = {
            "type": "offer_letter",
            "summary": self._generate_summary(offer, metrics),
            "confidence": 0.9,

            # Header
            "candidate_name": offer.get("candidate_name"),
            "position_title": offer.get("position_title"),
            "employment_type": offer.get("employment_type"),
            "start_date": offer.get("start_date"),
            "location": offer.get("location"),

            # Compensation
            **metrics,

            # Risks / insights
            "offer_risks": risks,
            "offer_risk_count": len(risks),
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_offer_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACT ----------------

    def _extract_offer(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_offer_parser import UniversalOfferParser
            parser = UniversalOfferParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Offer letter parsing failed: {e}")
            parsed = {}

        return {
            "candidate_name": parsed.get("candidate_name"),
            "position_title": parsed.get("position_title"),
            "employment_type": parsed.get("employment_type"),  # Full-time, Part-time, Contractor
            "start_date": parsed.get("start_date"),
            "location": parsed.get("location"),
            "base_salary": self._safe_float(parsed.get("base_salary")),
            "currency": parsed.get("currency", "USD"),
            "bonus_target": self._safe_float(parsed.get("bonus_target")),
            "equity_grant": self._safe_float(parsed.get("equity_grant")),
            "benefits_summary": parsed.get("benefits_summary", []),
        }

    def _safe_float(self, v, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).replace(",", "").replace("$", "").strip()
            return float(s)
        except Exception:
            return default

    # ---------------- METRICS / RISKS ----------------

    def _salary_metrics(self, offer: Dict[str, Any]) -> Dict[str, Any]:
        base = offer.get("base_salary", 0.0)
        bonus = offer.get("bonus_target", 0.0)
        equity = offer.get("equity_grant", 0.0)
        total_cash = base + bonus

        return {
            "base_salary": round(base, 2),
            "bonus_target": round(bonus, 2),
            "equity_grant": round(equity, 2),
            "total_cash_comp": round(total_cash, 2),
        }

    def _risk_checks(self, offer: Dict[str, Any], metrics: Dict[str, Any]) -> List[str]:
        risks: List[str] = []

        if not offer.get("employment_type"):
            risks.append("Employment type not clearly specified.")

        if not offer.get("start_date"):
            risks.append("Start date not clearly specified.")

        if metrics.get("base_salary", 0) == 0:
            risks.append("Base salary not clearly stated.")

        return risks

    # ---------------- SUMMARY / INSIGHTS ----------------

    def _generate_summary(self, offer: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        name = offer.get("candidate_name", "Candidate")
        title = offer.get("position_title", "a role")
        base = metrics.get("base_salary", 0.0)
        currency = offer.get("currency", "USD")
        return (
            f"Offer letter for {name} to join as {title} with a base salary "
            f"of {currency} {base:,.2f}."
        )

    def _generate_insights(self, offer, metrics, risks) -> List[str]:
        insights = []

        base = metrics.get("base_salary", 0.0)
        bonus = metrics.get("bonus_target", 0.0)
        equity = metrics.get("equity_grant", 0.0)

        insights.append(f"Base salary: ${base:,.2f}")
        if bonus > 0:
            insights.append(f"Bonus target: ${bonus:,.2f}")
        if equity > 0:
            insights.append(f"Equity grant value: ${equity:,.2f}")

        if risks:
            insights.append(f"Risks identified: {len(risks)} issues.")

        return insights

    def _generate_recommendations(self, offer, metrics, risks) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for r in risks:
            recs.append({
                "priority": "medium",
                "category": "clarity",
                "title": "Clarify Offer Terms",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    # ---------------- UI CONFIG ----------------

    def build_offer_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "base_salary", "label": "Base Salary", "value_path": "base_salary", "is_hero": True},
            {"key": "bonus_target", "label": "Bonus Target", "value_path": "bonus_target", "is_hero": True},
            {"key": "equity_grant", "label": "Equity Grant", "value_path": "equity_grant", "is_hero": True},
            {"key": "total_cash_comp", "label": "Total Cash Comp", "value_path": "total_cash_comp", "is_hero": True},
        ]

        secondary_metrics = [
            {"key": "employment_type", "label": "Employment Type", "value_path": "employment_type", "is_hero": False},
            {"key": "offer_risk_count", "label": "Risk Count", "value_path": "offer_risk_count", "is_hero": False},
        ]

        charts: List[Dict[str, Any]] = []  # No numeric chart needed for now

        tables = [
            {"id": "benefits", "name": "benefits", "title": "Benefits Summary", "data_path": "benefits_summary"},
            {"id": "offer_risks", "name": "offer_risks", "title": "Risks", "data_path": "offer_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Offer Summary",
                "metrics": ["base_salary", "bonus_target", "equity_grant", "total_cash_comp", "employment_type"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "benefits",
                "title": "Benefits & Perks",
                "metrics": [],
                "charts": [],
                "tables": ["benefits"],
            },
            {
                "id": "risk",
                "title": "Risks & Recommendations",
                "metrics": ["offer_risk_count"],
                "charts": [],
                "tables": ["offer_risks", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
