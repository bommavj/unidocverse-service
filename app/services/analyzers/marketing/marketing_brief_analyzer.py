# app/services/analyzers/marketing/marketing_brief_analyzer.py

import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class MarketingBriefAnalyzer(BaseAnalyzer):
    """
    Analyzer for marketing briefs:
    - Extracts campaign objectives, target audience, budget, timeline, KPIs, channels
    - Computes readiness metrics, budget sufficiency, KPI clarity
    - Flags: missing objectives, unclear KPIs, unrealistic timelines, missing budget
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"📣 Marketing brief analysis: {file_path}")

        brief = self._extract_brief(file_path, text, metadata)
        metrics = self._compute_metrics(brief)
        risk_metrics = self._risk_metrics(brief, metrics)
        insights = self._generate_insights(brief, metrics, risk_metrics)
        recommendations = self._generate_recommendations(brief, metrics, risk_metrics)

        result = {
            "type": "marketing_brief",
            "summary": self._generate_summary(brief),
            "confidence": 0.93,

            "campaign_name": brief.get("campaign_name"),
            "owner": brief.get("owner"),
            "start_date": brief.get("start_date"),
            "end_date": brief.get("end_date"),
            "budget": metrics.get("budget", 0.0),
            "channels": brief.get("channels", []),

            **metrics,
            **risk_metrics,

            "objectives": brief.get("objectives", []),
            "target_audience": brief.get("target_audience"),
            "kpis": brief.get("kpis", []),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_brief_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # Extraction
    def _extract_brief(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_marketing_brief_parser import UniversalMarketingBriefParser
            parser = UniversalMarketingBriefParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Marketing brief parsing failed: {e}")
            parsed = {}

        return {
            "campaign_name": parsed.get("campaign_name") or metadata.get("campaign_name"),
            "owner": parsed.get("owner"),
            "start_date": parsed.get("start_date"),
            "end_date": parsed.get("end_date"),
            "budget": self.safe_float(parsed.get("budget")),
            "channels": parsed.get("channels", []),
            "objectives": parsed.get("objectives", []),
            "target_audience": parsed.get("target_audience"),
            "kpis": parsed.get("kpis", []),  # [{kpi, target, timeframe}]
            "creative_brief": parsed.get("creative_brief"),
        }

    # Metrics
    def _compute_metrics(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        kpis = brief.get("kpis", [])
        budget = brief.get("budget", 0.0)
        channel_count = len(brief.get("channels", []))
        objective_count = len(brief.get("objectives", []))
        kpi_clarity = sum(1 for k in kpis if k.get("kpi") and k.get("target")) / (len(kpis) or 1)
        return {
            "objective_count": objective_count,
            "kpi_count": len(kpis),
            "kpi_clarity_score": round(kpi_clarity, 2),
            "channel_count": channel_count,
            "budget": round(budget, 2),
        }

    # Risk checks
    def _risk_metrics(self, brief: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        risks: List[str] = []
        if metrics.get("objective_count", 0) == 0:
            risks.append("No campaign objectives defined.")
        if metrics.get("kpi_count", 0) == 0 or metrics.get("kpi_clarity_score", 0) < 0.5:
            risks.append("KPIs missing or not specific enough.")
        if metrics.get("budget", 0) == 0:
            risks.append("Budget not specified.")
        # timeline realism heuristic
        try:
            from datetime import datetime
            s = brief.get("start_date")
            e = brief.get("end_date")
            if s and e:
                sd = datetime.fromisoformat(s)
                ed = datetime.fromisoformat(e)
                if (ed - sd).days < 7:
                    risks.append("Very short campaign window; may limit impact.")
        except Exception:
            pass
        return {"marketing_risks": risks, "marketing_risk_count": len(risks)}

    # Summary, insights, recommendations
    def _generate_summary(self, brief: Dict[str, Any]) -> str:
        name = brief.get("campaign_name", "Campaign")
        owner = brief.get("owner", "Owner")
        return f"Marketing brief for {name} owned by {owner}."

    def _generate_insights(self, brief: Dict[str, Any], metrics: Dict[str, Any], risk_metrics: Dict[str, Any]) -> List[str]:
        insights: List[str] = []
        insights.append(f"Objectives: {metrics.get('objective_count', 0)}")
        insights.append(f"Channels: {metrics.get('channel_count', 0)}")
        insights.append(f"KPIs: {metrics.get('kpi_count', 0)} (clarity {metrics.get('kpi_clarity_score', 0)})")
        if risk_metrics.get("marketing_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['marketing_risk_count']}.")
        return insights

    def _generate_recommendations(self, brief: Dict[str, Any], metrics: Dict[str, Any], risk_metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        for r in risk_metrics.get("marketing_risks", []):
            recs.append({"priority": "high" if "No campaign objectives" in r or "Budget not specified" in r else "medium", "category": "planning", "title": "Marketing Risk", "description": r, "potential_savings": 0})
        if metrics.get("kpi_clarity_score", 0) < 0.5:
            recs.append({"priority": "high", "category": "measurement", "title": "Clarify KPIs", "description": "Define measurable KPIs with numeric targets and timeframes.", "potential_savings": 0})
        if metrics.get("budget", 0) == 0:
            recs.append({"priority": "high", "category": "budget", "title": "Define Budget", "description": "Specify budget to enable channel planning and media buys.", "potential_savings": 0})
        return recs

    # UI config
    def build_brief_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "objective_count", "label": "Objectives", "value_path": "objective_count", "is_hero": True},
            {"key": "kpi_count", "label": "KPIs", "value_path": "kpi_count", "is_hero": True},
            {"key": "budget", "label": "Budget", "value_path": "budget", "is_hero": True},
            {"key": "marketing_risk_count", "label": "Risk Flags", "value_path": "marketing_risk_count", "is_hero": True},
        ]

        charts = [
            {"id": "kpi_clarity", "type": "gauge", "title": "KPI Clarity", "data_path": "kpi_clarity_score", "label_field": "KPI Clarity", "value_field": "kpi_clarity_score"}
        ]

        tables = [
            {"id": "objectives", "name": "objectives", "title": "Objectives", "data_path": "objectives"},
            {"id": "kpis", "name": "kpis", "title": "KPIs", "data_path": "kpis"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Brief Summary", "metrics": ["objective_count", "kpi_count", "budget", "marketing_risk_count"], "charts": [], "tables": []},
            {"id": "strategy", "title": "Strategy", "metrics": ["channel_count"], "charts": [], "tables": ["objectives", "kpis"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["marketing_risk_count"], "charts": ["kpi_clarity"], "tables": ["recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
