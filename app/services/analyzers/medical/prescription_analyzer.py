import logging
from typing import Dict, Any, List
from ..base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class PrescriptionAnalyzer(BaseAnalyzer):
    """
    Analyzer for prescriptions:
    - Patient, prescriber, medication name, dose, route, frequency, duration
    - Checks: potential interactions, high dose, duplicate therapy, missing directions
    - Outputs: metrics, risk flags, insights, recommendations, ui_config
    """

    def analyze(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"💊 Prescription analysis: {file_path}")

        rx = self._extract_prescription(file_path, text, metadata)

        meds = rx.get("medications", [])
        med_metrics = self._med_metrics(meds)
        interaction_flags = self._interaction_checks(meds)
        dosing_flags = self._dosing_checks(meds)
        risk_list = interaction_flags + dosing_flags

        insights = self._generate_insights(rx, med_metrics, risk_list)
        recommendations = self._generate_recommendations(rx, med_metrics, risk_list)

        result = {
            "type": "prescription",
            "summary": self._generate_summary(rx),
            "confidence": 0.95,

            "patient_id": rx.get("patient_id"),
            "patient_name": rx.get("patient_name"),
            "prescriber": rx.get("prescriber"),
            "date_issued": rx.get("date_issued"),

            "medications": meds,
            **med_metrics,

            "prescription_risks": risk_list,
            "prescription_risk_count": len(risk_list),

            "insights": insights,
            "recommendations": recommendations,
        }

        result["ui_config"] = self.build_prescription_ui_config(result)
        result["has_advanced_analytics"] = True
        return result

    # ---------------- EXTRACTION ----------------

    def _extract_prescription(self, file_path: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.services.parsers.universal_prescription_parser import UniversalPrescriptionParser
            parser = UniversalPrescriptionParser(file_path, text=text)
            parsed = parser.parse() or {}
        except Exception as e:
            logger.error(f"❌ Prescription parsing failed: {e}")
            parsed = {}

        return {
            "patient_id": parsed.get("patient_id"),
            "patient_name": parsed.get("patient_name"),
            "prescriber": parsed.get("prescriber"),
            "date_issued": parsed.get("date_issued"),
            "medications": parsed.get("medications", []),  # [{name, dose, unit, route, frequency, duration, notes}]
        }

    # ---------------- METRICS ----------------

    def _med_metrics(self, meds: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "medication_count": len(meds),
            "unique_medications": list({m.get("name") for m in meds}),
        }

    # ---------------- SAFETY CHECKS ----------------

    def _interaction_checks(self, meds: List[Dict[str, Any]]) -> List[str]:
        # Lightweight heuristic checks; real implementation should call drug interaction service
        names = [str(m.get("name", "")).lower() for m in meds]
        flags = []
        if "warfarin" in names and any(x in names for x in ("ciprofloxacin", "trimethoprim", "metronidazole")):
            flags.append("Potential interaction: warfarin with antibiotic (bleeding risk).")
        if "ace inhibitor" in " ".join(names) and "spironolactone" in names:
            flags.append("Potential hyperkalemia risk with ACE inhibitor + spironolactone.")
        # duplicate therapy
        duplicates = [n for n in set(names) if names.count(n) > 1]
        for d in duplicates:
            flags.append(f"Duplicate therapy detected for {d}.")
        return flags

    def _dosing_checks(self, meds: List[Dict[str, Any]]) -> List[str]:
        flags = []
        for m in meds or []:
            try:
                dose = float(m.get("dose", 0))
                unit = (m.get("unit") or "").lower()
                name = m.get("name", "Medication")
                # heuristic: flag very high single doses for common meds
                if unit in ("mg", "mcg") and dose > 10000:
                    flags.append(f"Unusually high dose for {name}: {dose}{unit}.")
            except Exception:
                continue
        # missing directions
        for m in meds or []:
            if not m.get("frequency") or not m.get("route"):
                flags.append(f"Missing directions for {m.get('name', 'medication')}.")
        return flags

    # ---------------- SUMMARY / INSIGHTS / RECOMMENDATIONS ----------------

    def _generate_summary(self, rx: Dict[str, Any]) -> str:
        name = rx.get("patient_name", "Patient")
        count = len(rx.get("medications", []))
        return f"Prescription for {name} with {count} medication(s)."

    def _generate_insights(self, rx, med_metrics, risk_list) -> List[str]:
        insights = []
        insights.append(f"Medications: {med_metrics.get('medication_count', 0)}")
        if risk_list:
            insights.append(f"Issues detected: {len(risk_list)}")
        return insights

    def _generate_recommendations(self, rx, med_metrics, risk_list) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        for r in risk_list:
            recs.append({
                "priority": "high" if "interaction" in r.lower() or "unusually high dose" in r.lower() else "medium",
                "category": "safety",
                "title": "Prescription Issue",
                "description": r,
                "potential_savings": 0,
            })
        if med_metrics.get("medication_count", 0) > 0:
            recs.append({
                "priority": "low",
                "category": "process",
                "title": "Confirm Directions",
                "description": "Ensure frequency, route, and duration are documented for each medication.",
                "potential_savings": 0,
            })
        return recs

    # ---------------- UI CONFIG ----------------

    def build_prescription_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        hero_metrics = [
            {"key": "medication_count", "label": "Medications", "value_path": "medication_count", "is_hero": True},
            {"key": "prescription_risk_count", "label": "Risk Flags", "value_path": "prescription_risk_count", "is_hero": True},
        ]

        tables = [
            {"id": "medications", "name": "medications", "title": "Medications", "data_path": "medications"},
            {"id": "prescription_risks", "name": "prescription_risks", "title": "Risks", "data_path": "prescription_risks"},
            {"id": "recommendations", "name": "recommendations", "title": "Recommendations", "data_path": "recommendations"},
        ]

        sections = [
            {"id": "summary", "title": "Prescription Summary", "metrics": ["medication_count", "prescription_risk_count"], "charts": [], "tables": []},
            {"id": "meds", "title": "Medications", "metrics": [], "charts": [], "tables": ["medications"]},
            {"id": "risk", "title": "Risks & Recommendations", "metrics": ["prescription_risk_count"], "charts": [], "tables": ["prescription_risks", "recommendations"]},
        ]

        return {"hero_metrics": hero_metrics, "charts": [], "tables": tables, "sections": sections}
