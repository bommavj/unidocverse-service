# app/services/analyzers/hr/termination_letter_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class TerminationLetterAnalyzer(BaseAnalyzer):
    """
    Analyzer for termination letters:
    - Employee, termination date
    - Termination type (involuntary, resignation, layoff)
    - Severance, final pay, benefits continuation
    - Risk flags: missing final-pay info, missing cause/notice details
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"⚠️ Advanced termination letter analysis: {file_path}")

        term = self._extract_termination(file_path, text, metadata)
        metrics = self._termination_metrics(term)
        risks = self._risk_checks(term, metrics)
        insights = self._generate_insights(term, metrics, risks)
        recommendations = self._generate_recommendations(term, metrics, risks)

        result = {
            "type": "termination_letter",
            "summary": self._generate_summary(term, metrics),
            "confidence": 0.9,

            "employee_id": term.get("employee_id"),
            "employee_name": term.get("employee_name"),
            "termination_date": term.get("termination_date"),
            "termination_type": term.get("termination_type"),  # e.g., involuntary, resignation, layoff

            **metrics,

            "termination_risks": risks,
            "termination_risk_count": len(risks),
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_termination_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    def _extract_termination(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_termination_parser import UniversalTerminationParser
            parser = UniversalTerminationParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Termination parsing failed: {e}")
            parsed = {}

        return {
            "employee_id": parsed.get("employee_id"),
            "employee_name": parsed.get("employee_name"),
            "termination_date": parsed.get("termination_date"),
            "termination_type": parsed.get("termination_type"),
            "severance_amount": self._safe_float(parsed.get("severance_amount")),
            "unused_vacation_payout": self._safe_float(parsed.get("unused_vacation_payout")),
            "final_pay_amount": self._safe_float(parsed.get("final_pay_amount")),
            "benefits_end_date": parsed.get("benefits_end_date"),
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

    def _termination_metrics(self, term: Dict[str, Any]) -> Dict[str, Any]:
        severance = term.get("severance_amount", 0.0)
        vacation = term.get("unused_vacation_payout", 0.0)
        final_pay = term.get("final_pay_amount", 0.0)
        total_cash = severance + vacation + final_pay

        return {
            "severance_amount": round(severance, 2),
            "unused_vacation_payout": round(vacation, 2),
            "final_pay_amount": round(final_pay, 2),
            "total_cash_exit": round(total_cash, 2),
        }

    def _risk_checks(self, term: Dict[str, Any], metrics: Dict[str, Any]) -> List[str]:
        risks: List[str] = []

        if not term.get("termination_date"):
            risks.append("Termination date not clearly specified.")

        if metrics.get("final_pay_amount", 0) == 0:
            risks.append("Final pay amount not clearly stated.")

        if not term.get("termination_type"):
            risks.append("Termination type (cause, layoff, resignation) not clearly specified.")

        return risks

    def _generate_summary(self, term, metrics) -> str:
        name = term.get("employee_name", "Employee")
        date = term.get("termination_date", "N/A")
        total = metrics.get("total_cash_exit", 0.0)
        return (
            f"Termination letter for {name} effective on {date}, with total exit cash "
            f"of ${total:,.2f} including severance, vacation payout, and final pay."
        )

    def _generate_insights(self, term, metrics, risks) -> List[str]:
        insights = []

        insights.append(f"Severance: ${metrics.get('severance_amount', 0.0):,.2f}")
        insights.append(f"Unused vacation payout: ${metrics.get('unused_vacation_payout', 0.0):,.2f}")
        insights.append(f"Final pay: ${metrics.get('final_pay_amount', 0.0):,.2f}")
        insights.append(f"Total exit cash: ${metrics.get('total_cash_exit', 0.0):,.2f}")

        if risks:
            insights.append(f"Risks identified: {len(risks)} issues.")

        return insights

    def _generate_recommendations(self, term, metrics, risks) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for r in risks:
            recs.append({
                "priority": "high",
                "category": "clarity",
                "title": "Clarify Termination Details",
                "description": r,
                "potential_savings": 0,
            })

        return recs

    def build_termination_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "total_cash_exit", "label": "Total Exit Cash", "value_path": "total_cash_exit", "is_hero": True},
            {"key": "severance_amount", "label": "Severance", "value_path": "severance_amount", "is_hero": True},
            {"key": "unused_vacation_payout", "label": "Vacation Payout", "value_path": "unused_vacation_payout", "is_hero": True},
            {"key": "final_pay_amount", "label": "Final Pay", "value_path": "final_pay_amount", "is_hero": True},
        ]

        secondary_metrics = [
            {"key": "termination_type", "label": "Termination Type", "value_path": "termination_type", "is_hero": False},
            {"key": "termination_risk_count", "label": "Risk Count", "value_path": "termination_risk_count", "is_hero": False},
        ]

        charts: List[Dict[str, Any]] = []

        tables = [
            {"id": "termination_risks", "name": "termination_risks", "title": "Risks", "data_path": "termination_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {
                "id": "summary",
                "title": "Termination Summary",
                "metrics": ["total_cash_exit", "severance_amount", "unused_vacation_payout", "final_pay_amount", "termination_type"],
                "charts": [],
                "tables": [],
            },
            {
                "id": "risk",
                "title": "Risks & Recommendations",
                "metrics": ["termination_risk_count"],
                "charts": [],
                "tables": ["termination_risks", "recommendations"],
            },
        ]

        return {
            "hero_metrics": hero_metrics + secondary_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }
