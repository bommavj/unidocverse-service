# app/services/analyzers/real_estate/appraisal_report_analyzer.py

import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class AppraisalReportAnalyzer(BaseAnalyzer):
    """
    Analyzer for property appraisal reports:
    - Property address, appraiser, appraisal number
    - Estimated value, comparable sales, adjustments
    - Value per square foot, effective date
    - Risk flags: low comps, high variance, missing appraisal date
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🏷️ Appraisal analysis: {file_path}")

        appraisal = self._extract_appraisal(file_path, text, metadata)

        comps = appraisal.get("comparables", [])
        comps_normalized = self._normalize_comps(comps)
        comp_metrics = self._comp_metrics(comps_normalized)
        value_metrics = self._value_metrics(appraisal, comp_metrics)
        risk_metrics = self._risk_metrics(appraisal, comp_metrics, value_metrics)

        insights = self._generate_insights(appraisal, value_metrics, risk_metrics)
        recommendations = self._generate_recommendations(appraisal, value_metrics, risk_metrics)

        result = {
            "type": "appraisal_report",
            "summary": self._generate_summary(appraisal, value_metrics),
            "confidence": 0.95,

            "appraisal_number": appraisal.get("appraisal_number"),
            "appraiser_name": appraisal.get("appraiser_name"),
            "property_address": appraisal.get("property_address"),
            "effective_date": appraisal.get("effective_date"),
            "estimated_value": value_metrics.get("estimated_value"),

            # Comparables
            "comparables": comps_normalized,
            **comp_metrics,

            # Value metrics
            **value_metrics,

            # Risks
            **risk_metrics,

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_appraisal_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # Extraction
    def _extract_appraisal(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_appraisal_parser import UniversalAppraisalParser
            parser = UniversalAppraisalParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Appraisal parsing failed: {e}")
            parsed = {}

        return {
            "appraisal_number": parsed.get("appraisal_number"),
            "appraiser_name": parsed.get("appraiser_name"),
            "property_address": parsed.get("property_address"),
            "effective_date": parsed.get("effective_date"),
            "estimated_value": self.safe_float(parsed.get("estimated_value")),
            "property_sqft": self.safe_float(parsed.get("property_sqft")),
            "comparables": parsed.get("comparables", []),  # [{address, sale_price, sale_date, sqft, adjustments}]
        }

    # Helpers
    def _normalize_comps(self, comps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for c in comps or []:
            price = self.safe_float(c.get("sale_price"))
            sqft = self.safe_float(c.get("sqft"))
            price_per_sqft = round(price / sqft, 2) if sqft > 0 else None
            normalized.append({
                "address": c.get("address"),
                "sale_price": price,
                "sale_date": c.get("sale_date"),
                "sqft": sqft,
                "price_per_sqft": price_per_sqft,
                "adjustments": c.get("adjustments", 0.0),
                "_raw": c,
            })
        return normalized

    def _comp_metrics(self, comps: List[Dict[str, Any]]) -> Dict[str, Any]:
        prices = [c["sale_price"] for c in comps if c.get("sale_price") is not None]
        pps = [c["price_per_sqft"] for c in comps if c.get("price_per_sqft") is not None]

        return {
            "comp_count": len(comps),
            "comp_average_price": round(sum(prices) / len(prices), 2) if prices else 0.0,
            "comp_average_pps": round(sum(pps) / len(pps), 2) if pps else None,
            "comp_max_price": round(max(prices), 2) if prices else 0.0,
            "comp_min_price": round(min(prices), 2) if prices else 0.0,
        }

    def _value_metrics(self, appraisal: Dict[str, Any], comp_metrics: Dict[str, Any]) -> Dict[str, Any]:
        est = appraisal.get("estimated_value", 0.0)
        sqft = appraisal.get("property_sqft", 0.0)
        est_pps = round(est / sqft, 2) if sqft > 0 else None

        return {
            "estimated_value": round(est, 2),
            "property_sqft": round(sqft, 2),
            "estimated_price_per_sqft": est_pps,
            "comp_count": comp_metrics.get("comp_count", 0),
        }

    def _risk_metrics(self, appraisal, comp_metrics, value_metrics) -> Dict[str, Any]:
        risks = []
        # High variance between estimated value and comps average
        avg_comp_price = comp_metrics.get("comp_average_price", 0.0)
        est = value_metrics.get("estimated_value", 0.0)
        if avg_comp_price and abs(est - avg_comp_price) / avg_comp_price > 0.25:
            risks.append("Estimated value deviates significantly from comparable average.")

        if value_metrics.get("estimated_price_per_sqft") is None:
            risks.append("Missing property square footage; cannot compute price per sqft.")

        if not appraisal.get("effective_date"):
            risks.append("Missing effective date on appraisal.")

        return {
            "appraisal_risks": risks,
            "appraisal_risk_count": len(risks),
        }

    # Summary, insights, recommendations
    def _generate_summary(self, appraisal, value_metrics) -> str:
        addr = appraisal.get("property_address", "Property")
        est = value_metrics.get("estimated_value", 0.0)
        return f"Appraisal for {addr} with estimated value ${est:,.2f}."

    def _generate_insights(self, appraisal, value_metrics, risk_metrics) -> List[str]:
        insights = []
        insights.append(f"Estimated value: ${value_metrics.get('estimated_value', 0.0):,.2f}")
        if value_metrics.get("estimated_price_per_sqft") is not None:
            insights.append(f"Estimated price per sqft: ${value_metrics.get('estimated_price_per_sqft'):.2f}")
        if risk_metrics.get("appraisal_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['appraisal_risk_count']}.")
        return insights

    def _generate_recommendations(self, appraisal, value_metrics, risk_metrics) -> List[Dict[str, Any]]:
        recs = []
        for r in risk_metrics.get("appraisal_risks", []):
            recs.append({
                "priority": "medium",
                "category": "valuation",
                "title": "Review Appraisal Risk",
                "description": r,
                "potential_savings": 0,
            })
        if value_metrics.get("estimated_price_per_sqft") is None:
            recs.append({
                "priority": "high",
                "category": "data",
                "title": "Add Property Square Footage",
                "description": "Square footage missing; add to improve valuation accuracy.",
                "potential_savings": 0,
            })
        return recs

    # UI config
    def build_appraisal_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "estimated_value", "label": "Estimated Value", "value_path": "estimated_value", "is_hero": True},
            {"key": "property_sqft", "label": "Square Footage", "value_path": "property_sqft", "is_hero": True},
            {"key": "estimated_price_per_sqft", "label": "Price Per Sqft", "value_path": "estimated_price_per_sqft", "is_hero": True},
            {"key": "appraisal_risk_count", "label": "Risk Flags", "value_path": "appraisal_risk_count", "is_hero": True},
        ]

        charts = [
            {
                "id": "comp_price_distribution",
                "type": "bar",
                "title": "Comparable Sale Prices",
                "data_path": "comparables",
                "label_field": "address",
                "value_field": "sale_price",
            }
        ]

        tables = [
            {"id": "comparables", "name": "comparables", "title": "Comparables", "data_path": "comparables"},
            {"id": "appraisal_risks", "name": "appraisal_risks", "title": "Risks", "data_path": "appraisal_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Appraisal Summary", "metrics": ["estimated_value", "property_sqft", "estimated_price_per_sqft", "appraisal_risk_count"], "charts": [], "tables": []},
            {"id": "comparables", "title": "Comparables", "metrics": ["comp_count", "comp_average_price"], "charts": ["comp_price_distribution"], "tables": ["comparables"]},
            {"id": "risk", "title": "Risk & Recommendations", "metrics": ["appraisal_risk_count"], "charts": [], "tables": ["appraisal_risks", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
