import logging
from typing import Dict, Any, List
from datetime import datetime

from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class MedicalReportAnalyzer(BaseAnalyzer):
    """
    Analyzer for clinical/medical reports:
    - Patient identifiers, report type, clinician, dates
    - Diagnoses, procedures, medications, lab results, vitals
    - Risk flags: abnormal labs, critical vitals, missing follow-up
    - Outputs: metrics, insights, recommendations, ui_config
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"🩺 Medical report analysis: {file_path}")

        rpt = self._extract_report(file_path, text, metadata)

        vitals = rpt.get("vitals", {})
        labs = rpt.get("labs", [])
        diagnoses = rpt.get("diagnoses", [])
        meds = rpt.get("medications", [])

        vitals_metrics = self._vitals_metrics(vitals)
        lab_metrics = self._lab_metrics(labs)
        med_metrics = self._med_metrics(meds)
        risk_metrics = self._risk_checks(vitals_metrics, lab_metrics, diagnoses)

        insights = self._generate_insights(rpt, vitals_metrics, lab_metrics, med_metrics, risk_metrics)
        recommendations = self._generate_recommendations(rpt, vitals_metrics, lab_metrics, med_metrics, risk_metrics)

        result = {
            "type": "medical_report",
            "summary": self._generate_summary(rpt, vitals_metrics, lab_metrics),
            "confidence": 0.95,

            "patient_id": rpt.get("patient_id"),
            "patient_name": rpt.get("patient_name"),
            "report_type": rpt.get("report_type"),
            "clinician": rpt.get("clinician"),
            "report_date": rpt.get("report_date"),

            # clinical data
            "vitals": vitals,
            **vitals_metrics,
            "labs": labs,
            **lab_metrics,
            "diagnoses": diagnoses,
            "medications": meds,
            **med_metrics,

            # risks & insights
            "medical_risks": risk_metrics.get("medical_risks", []),
            "medical_risk_count": risk_metrics.get("medical_risk_count", 0),
            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_medical_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_report(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_medical_report_parser import UniversalMedicalReportParser
            parser = UniversalMedicalReportParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Medical report parsing failed: {e}")
            parsed = {}

        return {
            "patient_id": parsed.get("patient_id"),
            "patient_name": parsed.get("patient_name"),
            "report_type": parsed.get("report_type"),
            "clinician": parsed.get("clinician"),
            "report_date": parsed.get("report_date"),
            "vitals": parsed.get("vitals", {}),  # {bp_systolic, bp_diastolic, hr, rr, temp, spo2}
            "labs": parsed.get("labs", []),      # [{name, value, unit, reference_range, flag}]
            "diagnoses": parsed.get("diagnoses", []),  # [{code, description}]
            "medications": parsed.get("medications", []),  # [{name, dose, route, frequency}]
            "recommendations_raw": parsed.get("recommendations", []),
        }

    # ---------------- METRICS ----------------

    def _vitals_metrics(self, vitals: Dict[str, Any]) -> Dict[str, Any]:
        def safe(v):
            try:
                return float(v)
            except Exception:
                return None

        bp_sys = safe(vitals.get("bp_systolic"))
        bp_dia = safe(vitals.get("bp_diastolic"))
        hr = safe(vitals.get("hr"))
        rr = safe(vitals.get("rr"))
        temp = safe(vitals.get("temp"))
        spo2 = safe(vitals.get("spo2"))

        return {
            "bp_systolic": bp_sys,
            "bp_diastolic": bp_dia,
            "heart_rate": hr,
            "respiratory_rate": rr,
            "temperature": temp,
            "spo2": spo2,
        }

    def _lab_metrics(self, labs: List[Dict[str, Any]]) -> Dict[str, Any]:
        abnormal = [l for l in labs if str(l.get("flag", "")).lower() in ("high", "low", "abnormal", "critical")]
        critical = [l for l in labs if str(l.get("flag", "")).lower() == "critical"]
        return {
            "lab_count": len(labs),
            "abnormal_lab_count": len(abnormal),
            "critical_lab_count": len(critical),
        }

    def _med_metrics(self, meds: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "medication_count": len(meds),
            "active_medications": [m for m in meds if not m.get("discontinued")],
        }

    # ---------------- RISK CHECKS ----------------

    def _risk_checks(self, vitals_metrics: Dict[str, Any], lab_metrics: Dict[str, Any], diagnoses: List[Dict[str, Any]]) -> Dict[str, Any]:
        risks: List[str] = []

        # vitals thresholds (heuristic)
        sys = vitals_metrics.get("bp_systolic")
        dia = vitals_metrics.get("bp_diastolic")
        hr = vitals_metrics.get("heart_rate")
        temp = vitals_metrics.get("temperature")
        spo2 = vitals_metrics.get("spo2")

        if sys and sys >= 180 or dia and dia >= 120:
            risks.append("Hypertensive emergency range blood pressure detected.")
        elif sys and sys >= 140 or dia and dia >= 90:
            risks.append("Elevated blood pressure (hypertension range).")

        if hr and (hr < 50 or hr > 120):
            risks.append("Abnormal heart rate detected.")

        if temp and temp >= 39.0:
            risks.append("High fever detected.")

        if spo2 and spo2 < 90:
            risks.append("Low oxygen saturation; hypoxemia risk.")

        if lab_metrics.get("critical_lab_count", 0) > 0:
            risks.append("Critical laboratory values detected.")

        # missing follow-up if serious diagnosis present but no plan
        serious_dx = [d for d in diagnoses if any(k in (d.get("description") or "").lower() for k in ("sepsis", "myocardial", "stroke", "cancer", "pulmonary embolism"))]
        if serious_dx and not risks:
            risks.append("Serious diagnosis present; ensure documented follow-up and care plan.")

        return {"medical_risks": risks, "medical_risk_count": len(risks)}

    # ---------------- SUMMARY / INSIGHTS / RECOMMENDATIONS ----------------

    def _generate_summary(self, rpt: Dict[str, Any], vitals_metrics: Dict[str, Any], lab_metrics: Dict[str, Any]) -> str:
        name = rpt.get("patient_name", "Patient")
        date = rpt.get("report_date", "N/A")
        return f"Medical report for {name} dated {date}. Vitals and labs summarized."

    def _generate_insights(self, rpt, vitals_metrics, lab_metrics, med_metrics, risk_metrics) -> List[str]:
        insights: List[str] = []
        if vitals_metrics.get("bp_systolic") is not None:
            insights.append(f"BP: {vitals_metrics.get('bp_systolic')}/{vitals_metrics.get('bp_diastolic')}")
        if lab_metrics.get("abnormal_lab_count", 0) > 0:
            insights.append(f"Abnormal labs: {lab_metrics.get('abnormal_lab_count')}")
        insights.append(f"Medications: {med_metrics.get('medication_count', 0)}")
        if risk_metrics.get("medical_risk_count", 0) > 0:
            insights.append(f"Risks identified: {risk_metrics['medical_risk_count']}.")
        return insights

    def _generate_recommendations(self, rpt, vitals_metrics, lab_metrics, med_metrics, risk_metrics) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []

        for r in risk_metrics.get("medical_risks", []):
            recs.append({
                "priority": "high" if "critical" in r.lower() or "hypertensive emergency" in r.lower() or "hypoxemia" in r.lower() else "medium",
                "category": "clinical",
                "title": "Clinical Alert",
                "description": r,
                "potential_savings": 0,
            })

        if lab_metrics.get("abnormal_lab_count", 0) > 0:
            recs.append({
                "priority": "medium",
                "category": "diagnostics",
                "title": "Review Abnormal Labs",
                "description": "Follow up on abnormal laboratory results and consider repeat testing or specialist referral.",
                "potential_savings": 0,
            })

        if med_metrics.get("medication_count", 0) > 0:
            recs.append({
                "priority": "low",
                "category": "medication",
                "title": "Medication Reconciliation",
                "description": "Confirm active medication list and check for interactions or duplications.",
                "potential_savings": 0,
            })

        return recs

    # ---------------- UI CONFIG ----------------

    def build_medical_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "medical_risk_count", "label": "Risk Flags", "value_path": "medical_risk_count", "is_hero": True},
            {"key": "lab_count", "label": "Lab Tests", "value_path": "lab_count", "is_hero": True},
            {"key": "abnormal_lab_count", "label": "Abnormal Labs", "value_path": "abnormal_lab_count", "is_hero": True},
            {"key": "medication_count", "label": "Medications", "value_path": "medication_count", "is_hero": True},
        ]

        charts = [
            {
                "id": "vitals_overview",
                "type": "bar",
                "title": "Vitals Overview",
                "data_path": "vitals",
                "label_field": "metric",
                "value_field": "value",
            }
        ]

        tables = [
            {"id": "labs", "name": "labs", "title": "Laboratory Results", "data_path": "labs"},
            {"id": "diagnoses", "name": "diagnoses", "title": "Diagnoses", "data_path": "diagnoses"},
            {"id": "medications", "name": "medications", "title": "Medications", "data_path": "medications"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Report Summary", "metrics": ["medical_risk_count", "lab_count", "medication_count"], "charts": [], "tables": []},
            {"id": "clinical", "title": "Clinical Data", "metrics": [], "charts": ["vitals_overview"], "tables": ["labs", "diagnoses", "medications"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["medical_risk_count"], "charts": [], "tables": ["recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": charts, "tables": tables, "sections": sections}
