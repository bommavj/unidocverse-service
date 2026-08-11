# app/services/analyzers/insurance_claim_analyzer.py

import logging
from typing import Any, Dict, Optional, List
from datetime import datetime
import re

import pandas as pd

from app.services.analyzers.base_analyzer import BaseAnalyzer
from app.parsers.pdf_table_extractor import extract_pdf_text
from app.parsers.pdf_table_extractor import ocr_pdf_to_text
from app.parsers.insurance_claim_extractor import extract_insurance_claim_fields

logger = logging.getLogger(__name__)


class InsuranceClaimAnalyzer(BaseAnalyzer):
    """
    Universal insurance claim analyzer for all claim types and insurance providers worldwide.

    Supports claims from:
    - Health Insurance (medical, dental, vision)
    - Auto Insurance (collision, comprehensive, liability)
    - Property Insurance (home, renters, fire, flood)
    - Life Insurance (death benefits, disability)
    - Travel Insurance (trip cancellation, medical emergency)
    - Business Insurance (general liability, property, workers comp)
    - And other insurance types globally

    Hybrid behavior:
    - Document-centric: extract key fields + summary + full text
    - Dashboard-ready: generates metrics, charts, and insights
    - Financial analytics: claim amounts, approvals, denials, processing times
    """

    def __init__(self):
        super().__init__()
        self.validations: List[Dict[str, Any]] = []
        self.fields: Dict[str, str] = {}

    def analyze(
            self,
            file_path: str,
            text: str = "",
            metadata: Optional[Dict[str, Any]] = None,
            parsed: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logger.info(f"📄 InsuranceClaimAnalyzer: {file_path}")
        metadata = metadata or {}

        # 1) Prefer provided text (pipeline may have OCR already)
        full_text = (text or "").strip()

        # 2) If no text, try direct PDF text
        if not full_text:
            try:
                full_text = extract_pdf_text(file_path).strip()
                if full_text:
                    logger.info("📝 Extracted machine-readable PDF text in InsuranceClaimAnalyzer")
            except Exception as e:
                logger.warning(f"⚠️ PDF text extraction failed in InsuranceClaimAnalyzer: {e}")

        # 3) If still empty, OCR fallback
        if not full_text:
            try:
                full_text = ocr_pdf_to_text(file_path).strip()
                if full_text:
                    logger.info("🔍 OCR extracted text in InsuranceClaimAnalyzer")
            except Exception as e:
                logger.warning(f"⚠️ OCR failed in InsuranceClaimAnalyzer: {e}")

        if not full_text:
            logger.warning("⚠️ InsuranceClaimAnalyzer: no text available from PDF")
            return self._empty_result_for_claim()

        # 4) Extract structured fields (universal)
        self.fields = extract_insurance_claim_fields(full_text)
        logger.info(f"📑 Extracted claim fields: {list(self.fields.keys())}")

        # 5) Detect insurance type and provider
        claim_type = self._detect_claim_type(full_text, self.fields)
        provider = self._detect_provider(full_text, self.fields)
        self.fields["claim_type"] = claim_type
        self.fields["provider"] = provider

        # 6) Build universal summary
        summary = self._build_summary(self.fields)

        # 7) Run claim-specific validations + alerts
        self.validations = self._run_validations(self.fields)
        alerts = self._build_alerts(self.validations)

        # 8) Confidence heuristic
        confidence = self._estimate_confidence(self.fields, self.validations)

        # 9) Generate insights for dashboard
        insights = self._generate_insights(self.fields, self.validations)

        # 10) Build result object (document-centric + dashboard-ready)
        result: Dict[str, Any] = {
            "type": "dataset",
            "subtype": "insurance_claim",
            "summary": summary,
            "confidence": confidence,
            "fields": self.fields,
            "raw_text": full_text,
            "validations": self.validations,
            "alerts": alerts,
            "claim_type": claim_type,
            "provider": provider,
            "insights": insights,
            "has_advanced_analytics": True,
            "ui_config": self._build_ui_config(self.fields, self.validations, insights),
        }

        return result

    # ------------------------------------------------------------
    # CLAIM TYPE DETECTION
    # ------------------------------------------------------------

    def _detect_claim_type(self, text: str, fields: Dict[str, str]) -> str:
        """
        Detect what type of insurance claim this is based on text patterns.
        """
        text_lower = text.lower()

        # Health/Medical Insurance
        if any(k in text_lower for k in ["medical claim", "health insurance", "eob", "explanation of benefits",
                                         "patient", "provider", "diagnosis", "icd-10", "cpt code", "hospital"]):
            return "Health/Medical Insurance"

        # Dental Insurance
        if any(k in text_lower for k in ["dental claim", "dentist", "tooth", "teeth", "oral", "ada code"]):
            return "Dental Insurance"

        # Vision Insurance
        if any(k in text_lower for k in ["vision claim", "optometry", "glasses", "contact lens", "eye exam"]):
            return "Vision Insurance"

        # Auto Insurance
        if any(k in text_lower for k in ["auto claim", "vehicle", "collision", "accident", "vin number",
                                         "license plate", "driver", "traffic accident"]):
            return "Auto Insurance"

        # Homeowners/Property Insurance
        if any(k in text_lower for k in ["property claim", "homeowner", "home insurance", "fire damage",
                                         "water damage", "theft", "burglary", "property loss"]):
            return "Property Insurance"

        # Life Insurance
        if any(k in text_lower for k in ["life insurance", "death benefit", "beneficiary", "policy holder deceased",
                                         "life claim"]):
            return "Life Insurance"

        # Disability Insurance
        if any(k in text_lower for k in ["disability claim", "disability insurance", "unable to work",
                                         "disability benefits"]):
            return "Disability Insurance"

        # Travel Insurance
        if any(k in text_lower for k in ["travel claim", "trip cancellation", "travel insurance", "flight delay",
                                         "baggage loss"]):
            return "Travel Insurance"

        # Workers Compensation
        if any(k in text_lower for k in ["workers comp", "worker's compensation", "workplace injury",
                                         "occupational injury"]):
            return "Workers Compensation"

        # Business/Commercial Insurance
        if any(k in text_lower for k in ["business insurance", "commercial claim", "general liability",
                                         "professional liability"]):
            return "Business Insurance"

        return "General Insurance Claim"

    def _detect_provider(self, text: str, fields: Dict[str, str]) -> str:
        """
        Detect the insurance provider/company from the document.
        """
        text_upper = text.upper()

        # Major US Health Insurance Providers
        providers = [
            "UNITED HEALTHCARE", "UNITEDHEALTH", "UHC",
            "BLUE CROSS", "BLUE SHIELD", "BCBS",
            "AETNA",
            "CIGNA",
            "HUMANA",
            "ANTHEM",
            "KAISER PERMANENTE",
            "CENTENE",
            "MOLINA",
            "WELLCARE",
            "HEALTH NET",
            # Auto Insurance
            "STATE FARM",
            "GEICO",
            "PROGRESSIVE",
            "ALLSTATE",
            "USAA",
            "FARMERS",
            "LIBERTY MUTUAL",
            "NATIONWIDE",
            # International
            "BUPA",
            "AXA",
            "ALLIANZ",
            "PRUDENTIAL",
            "AVIVA",
            "ZURICH",
            "MANULIFE",
        ]

        for provider in providers:
            if provider in text_upper:
                return provider.title()

        # Try to extract from provider field if available
        provider_field = fields.get("provider") or fields.get("insurance_company") or fields.get("carrier")
        if provider_field and len(provider_field) > 2:
            return provider_field

        return "Unknown Provider"

    # ------------------------------------------------------------
    # EMPTY RESULT
    # ------------------------------------------------------------

    def _empty_result_for_claim(self) -> Dict[str, Any]:
        return {
            "type": "insurance_claim",
            "subtype": "insurance_claim",
            "summary": "No readable content could be extracted from this insurance claim document.",
            "confidence": 0.0,
            "fields": {},
            "raw_text": "",
            "validations": [],
            "alerts": [],
            "claim_type": "Unknown",
            "provider": "Unknown",
            "insights": [],
            "has_advanced_analytics": False,
            "ui_config": {
                "dashboard": {},
                "sections": [],
            },
        }

    # ------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------

    def _build_summary(self, fields: Dict[str, str]) -> str:
        if not fields:
            return "This appears to be an insurance claim document, but key fields could not be reliably extracted."

        parts = []

        # Universal field names
        claim_number = fields.get("claim_number") or fields.get("claim_id") or fields.get("reference_number")
        claim_type = fields.get("claim_type", "Insurance claim")
        claim_status = fields.get("claim_status") or fields.get("status")
        claimant = fields.get("claimant") or fields.get("patient") or fields.get("insured")
        provider = fields.get("provider", "")
        claim_amount = fields.get("claim_amount") or fields.get("total_charges")
        approved_amount = fields.get("approved_amount") or fields.get("allowed_amount")
        claim_date = fields.get("claim_date") or fields.get("service_date") or fields.get("date_of_service")

        if claim_number:
            parts.append(f"Claim {claim_number}")

        header = " ".join(parts) if parts else claim_type

        if provider and provider != "Unknown Provider":
            header += f" from {provider}"

        details = []
        if claimant:
            details.append(f"Claimant: {claimant}")
        if claim_status:
            details.append(f"Status: {claim_status}")
        if claim_amount:
            details.append(f"Claimed: {claim_amount}")
        if approved_amount and approved_amount != claim_amount:
            details.append(f"Approved: {approved_amount}")
        if claim_date:
            details.append(f"Date: {claim_date}")

        if details:
            return header + ". " + " | ".join(details)

        return header + "."

    # ------------------------------------------------------------
    # VALIDATIONS + ALERTS
    # ------------------------------------------------------------

    def _run_validations(self, fields: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Run universal insurance claim validations.
        """
        validations: List[Dict[str, Any]] = []

        def add(code: str, level: str, message: str, field: Optional[str] = None):
            validations.append({
                "code": code,
                "level": level,  # info / warning / error
                "message": message,
                "field": field,
            })

        # Critical fields
        claim_number = fields.get("claim_number") or fields.get("claim_id") or fields.get("reference_number")
        if not claim_number:
            add(
                code="missing_claim_number",
                level="error",
                message="Claim number could not be detected. This claim cannot be tracked without a reference number.",
                field="claim_number",
            )

        claimant = fields.get("claimant") or fields.get("patient") or fields.get("insured")
        if not claimant:
            add(
                code="missing_claimant",
                level="warning",
                message="Claimant/patient name is missing. Verify document completeness.",
                field="claimant",
            )

        claim_date = fields.get("claim_date") or fields.get("service_date") or fields.get("date_of_service")
        if not claim_date:
            add(
                code="missing_claim_date",
                level="warning",
                message="Claim or service date is missing. This may affect processing timeline verification.",
                field="claim_date",
            )

        # Financial validations
        claim_amount = fields.get("claim_amount") or fields.get("total_charges")
        if not claim_amount:
            add(
                code="missing_claim_amount",
                level="warning",
                message="Claim amount is missing. Cannot verify financial information.",
                field="claim_amount",
            )
        else:
            # Check if amount is reasonable (basic sanity check)
            try:
                amount_value = float(re.sub(r'[^0-9.]', '', claim_amount))
                if amount_value <= 0:
                    add(
                        code="invalid_claim_amount",
                        level="error",
                        message=f"Claim amount of {claim_amount} appears invalid (zero or negative).",
                        field="claim_amount",
                    )
                elif amount_value > 1000000:
                    add(
                        code="unusually_high_amount",
                        level="info",
                        message=f"Claim amount of {claim_amount} is unusually high. Please verify.",
                        field="claim_amount",
                    )
            except ValueError:
                add(
                    code="unparseable_amount",
                    level="warning",
                    message=f"Could not parse claim amount: {claim_amount}",
                    field="claim_amount",
                )

        # Status validation
        claim_status = fields.get("claim_status") or fields.get("status")
        if claim_status:
            status_lower = claim_status.lower()
            if any(k in status_lower for k in ["denied", "rejected", "declined"]):
                add(
                    code="claim_denied",
                    level="info",
                    message=f"Claim status is '{claim_status}'. Review denial reasons.",
                    field="claim_status",
                )
            elif any(k in status_lower for k in ["pending", "processing", "under review"]):
                add(
                    code="claim_pending",
                    level="info",
                    message=f"Claim status is '{claim_status}'. Awaiting final decision.",
                    field="claim_status",
                )

        # Provider validation
        provider = fields.get("provider", "Unknown Provider")
        if provider == "Unknown Provider":
            add(
                code="unknown_provider",
                level="info",
                message="Could not automatically detect insurance provider from document.",
                field="provider",
            )

        return validations

    def _build_alerts(self, validations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert validations into higher-level alerts for the UI.
        """
        alerts: List[Dict[str, Any]] = []

        for v in validations:
            if v["level"] == "error":
                alerts.append({
                    "severity": "high",
                    "title": "Critical Field Missing",
                    "message": v["message"],
                    "field": v.get("field"),
                })
            elif v["level"] == "warning":
                alerts.append({
                    "severity": "medium",
                    "title": "Potential Data Issue",
                    "message": v["message"],
                    "field": v.get("field"),
                })
            elif v["level"] == "info":
                alerts.append({
                    "severity": "low",
                    "title": "Information",
                    "message": v["message"],
                    "field": v.get("field"),
                })

        return alerts

    def _estimate_confidence(
            self,
            fields: Dict[str, str],
            validations: List[Dict[str, Any]],
    ) -> float:
        """
        Simple heuristic:
        - Start high if we have core fields
        - Reduce for each error/warning
        """
        base = 0.95 if fields else 0.6

        for v in validations:
            if v["level"] == "error":
                base -= 0.25
            elif v["level"] == "warning":
                base -= 0.1
            elif v["level"] == "info":
                base -= 0.02

        return max(0.0, min(1.0, base))

    # ------------------------------------------------------------
    # INSIGHTS GENERATION
    # ------------------------------------------------------------

    def _generate_insights(
            self,
            fields: Dict[str, str],
            validations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Generate actionable insights for insurance claim processing.
        """
        insights = []

        # Claim type and provider insight
        claim_type = fields.get("claim_type", "General Insurance Claim")
        provider = fields.get("provider", "Unknown Provider")

        if provider != "Unknown Provider":
            insights.append({
                "type": "info",
                "title": f"{claim_type} - {provider}",
                "description": "Claim type and provider automatically identified",
                "color": "#4A90E2",
                "icon": "🏥" if "Health" in claim_type or "Medical" in claim_type else "📋"
            })

        # Claim number insight
        claim_number = fields.get("claim_number") or fields.get("claim_id") or fields.get("reference_number")
        if not claim_number:
            insights.append({
                "type": "critical",
                "title": "Missing Claim Number",
                "description": "Cannot track claim without a reference number. OCR quality may be poor.",
                "color": "#DC3545",
                "icon": "🚫"
            })

        # Financial insights
        claim_amount = fields.get("claim_amount") or fields.get("total_charges")
        approved_amount = fields.get("approved_amount") or fields.get("allowed_amount")
        patient_responsibility = fields.get("patient_responsibility") or fields.get("you_owe")

        if claim_amount and approved_amount:
            try:
                claimed = float(re.sub(r'[^0-9.]', '', claim_amount))
                approved = float(re.sub(r'[^0-9.]', '', approved_amount))

                if approved < claimed:
                    diff = claimed - approved
                    pct = (diff / claimed * 100) if claimed > 0 else 0
                    insights.append({
                        "type": "warning",
                        "title": "Partial Approval",
                        "description": f"Approved ${approved:,.2f} of ${claimed:,.2f} claimed ({pct:.1f}% reduction)",
                        "color": "#FFA726",
                        "icon": "💰"
                    })
                elif approved == claimed:
                    insights.append({
                        "type": "success",
                        "title": "Full Claim Approved",
                        "description": f"Entire claim amount of ${claimed:,.2f} approved",
                        "color": "#00C851",
                        "icon": "✅"
                    })
            except (ValueError, TypeError):
                pass

        if patient_responsibility:
            insights.append({
                "type": "info",
                "title": "Patient Responsibility",
                "description": f"Amount owed by patient: {patient_responsibility}",
                "color": "#AB47BC",
                "icon": "💳"
            })

        # Status insight
        claim_status = fields.get("claim_status") or fields.get("status")
        if claim_status:
            status_lower = claim_status.lower()
            if any(k in status_lower for k in ["approved", "paid", "processed"]):
                insights.append({
                    "type": "success",
                    "title": f"Claim {claim_status}",
                    "description": "Claim has been successfully processed",
                    "color": "#00C851",
                    "icon": "✅"
                })
            elif any(k in status_lower for k in ["denied", "rejected"]):
                insights.append({
                    "type": "critical",
                    "title": f"Claim {claim_status}",
                    "description": "Review denial reasons and consider appeal if appropriate",
                    "color": "#DC3545",
                    "icon": "❌"
                })
            elif any(k in status_lower for k in ["pending", "processing"]):
                insights.append({
                    "type": "info",
                    "title": f"Claim {claim_status}",
                    "description": "Claim is currently being reviewed",
                    "color": "#4A90E2",
                    "icon": "⏳"
                })

        # Validation summary insight
        errors = len([v for v in validations if v["level"] == "error"])
        warnings = len([v for v in validations if v["level"] == "warning"])

        if errors == 0 and warnings == 0:
            insights.append({
                "type": "success",
                "title": "All Data Quality Checks Passed",
                "description": "Claim document appears complete and properly formatted",
                "color": "#00C851",
                "icon": "✅"
            })
        elif errors > 0:
            insights.append({
                "type": "critical",
                "title": f"{errors} Critical Issue(s) Found",
                "description": "Review the alerts section for details and corrective actions",
                "color": "#DC3545",
                "icon": "❌"
            })

        return insights

    # ------------------------------------------------------------
    # DASHBOARD HELPER METHODS
    # ------------------------------------------------------------

    def _calculate_field_completion(self, fields: Dict[str, str]) -> Dict[str, Any]:
        """Calculate what % of expected fields were extracted"""
        expected_fields = [
            "claim_number", "claim_type", "provider", "claimant",
            "claim_date", "claim_amount", "claim_status", "approved_amount"
        ]

        # Check both primary and fallback field names
        found = 0
        for field in expected_fields:
            if field == "claim_number":
                if fields.get("claim_number") or fields.get("claim_id") or fields.get("reference_number"):
                    found += 1
            elif field == "claimant":
                if fields.get("claimant") or fields.get("patient") or fields.get("insured"):
                    found += 1
            elif field == "claim_date":
                if fields.get("claim_date") or fields.get("service_date") or fields.get("date_of_service"):
                    found += 1
            elif field == "claim_amount":
                if fields.get("claim_amount") or fields.get("total_charges"):
                    found += 1
            elif field == "approved_amount":
                if fields.get("approved_amount") or fields.get("allowed_amount"):
                    found += 1
            elif field == "claim_status":
                if fields.get("claim_status") or fields.get("status"):
                    found += 1
            else:
                if fields.get(field):
                    found += 1

        missing = len(expected_fields) - found
        completion_rate = (found / len(expected_fields)) * 100 if expected_fields else 0

        return {
            "labels": ["Extracted", "Missing"],
            "datasets": [{
                "data": [found, missing],
                "backgroundColor": ["#00C851", "#FF6B6B"]
            }],
            "completion_rate": round(completion_rate, 1)
        }

    def _validation_summary_chart(self) -> Dict[str, Any]:
        """Chart showing validation results by severity"""
        passed = len([v for v in self.validations if v["level"] == "info"])
        warnings = len([v for v in self.validations if v["level"] == "warning"])
        errors = len([v for v in self.validations if v["level"] == "error"])

        return {
            "labels": ["Passed", "Warnings", "Errors"],
            "datasets": [{
                "data": [passed, warnings, errors],
                "backgroundColor": ["#00C851", "#FFA726", "#FF6B6B"]
            }]
        }

    def _financial_breakdown_chart(self, fields: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Chart showing financial breakdown of the claim"""
        try:
            claim_amount = fields.get("claim_amount") or fields.get("total_charges")
            approved_amount = fields.get("approved_amount") or fields.get("allowed_amount")
            patient_responsibility = fields.get("patient_responsibility") or fields.get("you_owe")
            insurance_paid = fields.get("insurance_paid") or fields.get("paid_by_insurance")

            if not claim_amount:
                return None

            claimed = float(re.sub(r'[^0-9.]', '', claim_amount))
            approved = float(re.sub(r'[^0-9.]', '', approved_amount)) if approved_amount else claimed
            patient_owes = float(re.sub(r'[^0-9.]', '', patient_responsibility)) if patient_responsibility else 0
            insurance_pays = float(re.sub(r'[^0-9.]', '', insurance_paid)) if insurance_paid else (
                        approved - patient_owes)

            return {
                "labels": ["Insurance Pays", "Patient Owes", "Not Covered"],
                "datasets": [{
                    "data": [insurance_pays, patient_owes, max(0, claimed - approved)],
                    "backgroundColor": ["#00C851", "#FFA726", "#FF6B6B"]
                }]
            }
        except (ValueError, TypeError):
            return None

    def _fields_to_table_data(self, fields: Dict[str, str]) -> List[Dict[str, Any]]:
        """Convert fields dict to table rows"""
        table_data = []

        # Define display order and labels
        field_config = [
            ("claim_type", "Claim Type"),
            ("provider", "Insurance Provider"),
            ("claim_number", "Claim Number"),
            ("claim_id", "Claim ID"),
            ("reference_number", "Reference Number"),
            ("claimant", "Claimant/Patient"),
            ("patient", "Patient"),
            ("insured", "Insured Person"),
            ("claim_status", "Claim Status"),
            ("status", "Status"),
            ("claim_date", "Claim Date"),
            ("service_date", "Service Date"),
            ("date_of_service", "Date of Service"),
            ("claim_amount", "Claim Amount"),
            ("total_charges", "Total Charges"),
            ("approved_amount", "Approved Amount"),
            ("allowed_amount", "Allowed Amount"),
            ("insurance_paid", "Insurance Paid"),
            ("paid_by_insurance", "Paid by Insurance"),
            ("patient_responsibility", "Patient Responsibility"),
            ("you_owe", "You Owe"),
            ("deductible", "Deductible"),
            ("copay", "Copay"),
            ("coinsurance", "Coinsurance"),
        ]

        seen_keys = set()
        for key, label in field_config:
            if key in fields and key not in seen_keys and fields[key]:
                table_data.append({
                    "label": label,
                    "value": fields[key],
                    "confidence": "High" if len(fields[key]) > 3 else "Medium"
                })
                seen_keys.add(key)

        # Add any remaining fields not in the config
        for key, value in fields.items():
            if key not in seen_keys and key != "raw_text" and value:
                table_data.append({
                    "label": key.replace("_", " ").title(),
                    "value": value,
                    "confidence": "Medium"
                })

        return table_data

    def _all_validations_pass(self) -> bool:
        """Check if all validations passed (no errors)"""
        return all(v["level"] != "error" for v in self.validations)

    def _get_status_from_validations(self) -> str:
        """Get overall claim status based on validations"""
        errors = len([v for v in self.validations if v["level"] == "error"])
        warnings = len([v for v in self.validations if v["level"] == "warning"])

        if errors > 0:
            return "Critical Issues"
        elif warnings > 0:
            return "Needs Review"
        else:
            return "Valid"

    # ------------------------------------------------------------
    # UI CONFIG (DASHBOARD + DOCUMENT VIEW)
    # ------------------------------------------------------------

    def _build_ui_config(
            self,
            fields: Dict[str, str],
            validations: List[Dict[str, Any]],
            insights: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Build UI config that works with both document viewer AND advanced dashboard
        """

        # Get computed values
        claim_number = (
                fields.get("claim_number")
                or fields.get("claim_id")
                or fields.get("reference_number")
                or "N/A"
        )
        claim_date = (
                fields.get("claim_date")
                or fields.get("service_date")
                or fields.get("date_of_service")
                or "N/A"
        )
        claim_amount = fields.get("claim_amount") or fields.get("total_charges") or "N/A"
        claim_status = fields.get("claim_status") or fields.get("status") or "Unknown"

        # Build charts list
        charts = [
            {
                "id": "field_completion",
                "type": "doughnut",
                "title": "Field Extraction Completeness",
                "subtitle": f"{self._calculate_field_completion(fields)['completion_rate']}% of expected fields extracted",
                "data": self._calculate_field_completion(fields),
                "config": {
                    "responsive": True,
                    "maintainAspectRatio": False,
                    "plugins": {
                        "legend": {
                            "position": "bottom"
                        }
                    }
                },
                "height": "300px"
            },
            {
                "id": "validation_summary",
                "type": "bar",
                "title": "Data Quality Validation Results",
                "subtitle": f"{len(self.validations)} total checks performed",
                "data": self._validation_summary_chart(),
                "config": {
                    "indexAxis": "y",
                    "responsive": True,
                    "maintainAspectRatio": False,
                    "plugins": {
                        "legend": {
                            "display": False
                        }
                    }
                },
                "height": "250px"
            }
        ]

        # Add financial breakdown chart if we have the data
        financial_chart = self._financial_breakdown_chart(fields)
        if financial_chart:
            charts.append({
                "id": "financial_breakdown",
                "type": "doughnut",
                "title": "Financial Breakdown",
                "subtitle": "Distribution of claim amounts",
                "data": financial_chart,
                "config": {
                    "responsive": True,
                    "maintainAspectRatio": False,
                    "plugins": {
                        "legend": {
                            "position": "bottom"
                        }
                    }
                },
                "height": "300px"
            })

        return {
            # ========================================
            # DASHBOARD VIEW (for advanced analytics)
            # ========================================
            "hero_metrics": [
                {
                    "key": "claim_type",
                    "label": "Claim Type",
                    "value": fields.get("claim_type", "General Insurance"),
                    "icon": "🏥",
                    "color": "#4A90E2",
                    "size": "large"
                },
                {
                    "key": "status",
                    "label": "Claim Status",
                    "value": claim_status,
                    "icon": "✅" if "approved" in claim_status.lower() or "paid" in claim_status.lower() else "⏳",
                    "color": "#00C851" if "approved" in claim_status.lower() else "#FFA726",
                    "size": "large"
                },
                {
                    "key": "claim_number",
                    "label": "Claim Number",
                    "value": claim_number,
                    "icon": "🔢",
                    "color": "#AB47BC",
                    "size": "medium"
                },
                {
                    "key": "claim_amount",
                    "label": "Claim Amount",
                    "value": claim_amount,
                    "icon": "💰",
                    "color": "#00C851",
                    "size": "medium"
                }
            ],

            "charts": charts,

            "tables": [
                {
                    "id": "extracted_fields",
                    "title": "Extracted Claim Fields",
                    "data": self._fields_to_table_data(fields),
                    "columns": [
                        {
                            "field": "label",
                            "header": "Field Name",
                            "sortable": True
                        },
                        {
                            "field": "value",
                            "header": "Extracted Value",
                            "sortable": True
                        },
                        {
                            "field": "confidence",
                            "header": "Confidence",
                            "sortable": True
                        }
                    ],
                    "config": {
                        "paginator": True,
                        "rows": 10,
                        "showGridlines": True,
                        "filterDelay": 0,
                        "globalFilterFields": ["label", "value"]
                    }
                }
            ],

            "insights": insights,

            "layout": {
                "type": "responsive_grid",
                "columns": 12,
                "gap": 16,
                "sections": [
                    {
                        "id": "overview",
                        "title": "Claim Overview",
                        "components": ["hero_metrics"],
                        "grid": {
                            "cols": 12,
                            "rows": 2
                        },
                        "order": 1
                    },
                    {
                        "id": "insights_section",
                        "title": "Key Insights",
                        "components": ["insights"],
                        "grid": {
                            "cols": 12,
                            "rows": 3
                        },
                        "order": 2
                    },
                    {
                        "id": "financial_analytics",
                        "title": "Financial Analytics",
                        "components": ["charts"],
                        "grid": {
                            "cols": 12,
                            "rows": 4
                        },
                        "order": 3
                    },
                    {
                        "id": "field_details",
                        "title": "Extracted Field Details",
                        "components": ["tables"],
                        "grid": {
                            "cols": 12,
                            "rows": 6
                        },
                        "order": 4
                    }
                ]
            },

            # ========================================
            # DOCUMENT VIEW (classic viewer)
            # ========================================
            "sections": [
                {
                    "id": "claim_summary",
                    "title": "Claim Summary",
                    "type": "key_value",
                    "icon": "📋",
                    "fields": [
                        {
                            "label": "Claim Type",
                            "key": "claim_type"
                        },
                        {
                            "label": "Insurance Provider",
                            "key": "provider"
                        },
                        {
                            "label": "Claim Number",
                            "key": "claim_number",
                            "fallback_keys": ["claim_id", "reference_number"]
                        },
                        {
                            "label": "Claim Status",
                            "key": "claim_status",
                            "fallback_keys": ["status"]
                        },
                        {
                            "label": "Claimant/Patient",
                            "key": "claimant",
                            "fallback_keys": ["patient", "insured"]
                        },
                        {
                            "label": "Claim Date",
                            "key": "claim_date",
                            "fallback_keys": ["service_date", "date_of_service"]
                        },
                        {
                            "label": "Claim Amount",
                            "key": "claim_amount",
                            "fallback_keys": ["total_charges"]
                        },
                        {
                            "label": "Approved Amount",
                            "key": "approved_amount",
                            "fallback_keys": ["allowed_amount"]
                        },
                        {
                            "label": "Insurance Paid",
                            "key": "insurance_paid",
                            "fallback_keys": ["paid_by_insurance"]
                        },
                        {
                            "label": "Patient Responsibility",
                            "key": "patient_responsibility",
                            "fallback_keys": ["you_owe"]
                        }
                    ]
                },
                {
                    "id": "claim_alerts",
                    "title": "Data Quality & Alerts",
                    "type": "alerts",
                    "icon": "⚠️",
                    "field": "alerts",
                    "description": "Validation results and potential data issues"
                },
                {
                    "id": "claim_validations",
                    "title": "Detailed Validations",
                    "type": "validations",
                    "icon": "✓",
                    "field": "validations",
                    "description": "All data quality checks performed on this claim"
                },
                {
                    "id": "claim_full_text",
                    "title": "Full Claim Text",
                    "type": "text",
                    "icon": "📄",
                    "field": "raw_text",
                    "description": "Complete extracted text from the claim document",
                    "config": {
                        "maxHeight": "400px",
                        "copyable": True,
                        "searchable": True
                    }
                }
            ]
        }