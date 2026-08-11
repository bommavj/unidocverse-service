# app/services/analyzers/immigration_receipt_analyzer.py

import logging
from typing import Any, Dict, Optional, List
from datetime import datetime

from dateutil import parser as dateutil_parser

from app.services.analyzers.base_analyzer import BaseAnalyzer
from app.parsers.pdf_table_extractor import extract_pdf_text
from app.parsers.pdf_table_extractor import ocr_pdf_to_text
from app.parsers.pdf_field_extractor import extract_pdf_fields

logger = logging.getLogger(__name__)


class ImmigrationReceiptAnalyzer(BaseAnalyzer):
    """
    Universal immigration document analyzer for receipts, notices, and official correspondence.

    Supports documents from immigration authorities worldwide:
    - USCIS (United States)
    - IRCC (Canada)
    - Home Office (United Kingdom)
    - DIBP/Department of Home Affairs (Australia)
    - INZ (New Zealand)
    - European immigration authorities
    - And other jurisdictions globally

    Hybrid behavior:
    - Document-centric: extract key fields + summary + full text
    - Dashboard-ready: generates metrics, charts, and insights
    - Light "analytics": validations, consistency checks, alerts
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
        logger.info(f"📄 ImmigrationReceiptAnalyzer: {file_path}")
        metadata = metadata or {}

        # 1) Prefer provided text (pipeline may have OCR already)
        full_text = (text or "").strip()

        # 2) If no text, try direct PDF text
        if not full_text:
            try:
                full_text = extract_pdf_text(file_path).strip()
                if full_text:
                    logger.info("📝 Extracted machine-readable PDF text in ImmigrationReceiptAnalyzer")
            except Exception as e:
                logger.warning(f"⚠️ PDF text extraction failed in ImmigrationReceiptAnalyzer: {e}")

        # 3) If still empty, OCR fallback
        if not full_text:
            try:
                full_text = ocr_pdf_to_text(file_path).strip()
                if full_text:
                    logger.info("🔍 OCR extracted text in ImmigrationReceiptAnalyzer")
            except Exception as e:
                logger.warning(f"⚠️ OCR failed in ImmigrationReceiptAnalyzer: {e}")

        if not full_text:
            logger.warning("⚠️ ImmigrationReceiptAnalyzer: no text available from PDF")
            return self._empty_result_for_receipt()

        # 4) Extract structured fields (jurisdiction-agnostic)
        self.fields = extract_pdf_fields(full_text)
        logger.info(f"📑 Extracted receipt fields: {list(self.fields.keys())}")

        # 5) Detect jurisdiction/authority
        jurisdiction = self._detect_jurisdiction(full_text, self.fields)
        self.fields["jurisdiction"] = jurisdiction

        # 6) Build universal summary
        summary = self._build_summary(self.fields)

        # 7) Run jurisdiction-aware validations + alerts
        self.validations = self._run_validations(self.fields)
        alerts = self._build_alerts(self.validations)

        # 8) Confidence heuristic
        confidence = self._estimate_confidence(self.fields, self.validations)

        # 9) Generate insights for dashboard
        insights = self._generate_insights(self.fields, self.validations)

        # 10) Build result object (document-centric + dashboard-ready)
        result: Dict[str, Any] = {
            "type": "dataset",
            "subtype": "immigration_receipt",
            "summary": summary,
            "confidence": confidence,
            "fields": self.fields,
            "raw_text": full_text,
            "validations": self.validations,
            "alerts": alerts,
            "jurisdiction": jurisdiction,
            "insights": insights,
            "has_advanced_analytics": True,
            "ui_config": self._build_ui_config(self.fields, self.validations, insights),
        }

        return result

    # ------------------------------------------------------------
    # JURISDICTION DETECTION
    # ------------------------------------------------------------

    def _detect_jurisdiction(self, text: str, fields: Dict[str, str]) -> str:
        """
        Detect which country/authority issued this document based on text patterns.
        """
        text_lower = text.lower()

        # United States
        if any(k in text_lower for k in
               ["uscis", "u.s. citizenship", "department of homeland security", "dhs", "i-797", "i-129", "i-140"]):
            return "United States (USCIS)"

        # Canada
        if any(k in text_lower for k in
               ["ircc", "immigration, refugees and citizenship canada", "citizenship and immigration canada", "cic"]):
            return "Canada (IRCC)"

        # United Kingdom
        if any(k in text_lower for k in ["home office", "uk visas and immigration", "ukvi", "border force"]):
            return "United Kingdom (Home Office)"

        # Australia
        if any(k in text_lower for k in
               ["department of home affairs", "dibp", "immi account", "australian government"]):
            return "Australia (Department of Home Affairs)"

        # New Zealand
        if any(k in text_lower for k in ["immigration new zealand", "inz", "nz immigration"]):
            return "New Zealand (INZ)"

        # European Union
        if any(k in text_lower for k in ["schengen", "european union", "eu blue card"]):
            return "European Union"

        # Germany
        if any(k in text_lower for k in ["bundesamt für migration", "bamf", "ausländerbehörde"]):
            return "Germany (BAMF)"

        # France
        if any(k in text_lower for k in ["ofii", "préfecture", "ofpra"]):
            return "France (OFII)"

        # Singapore
        if any(k in text_lower for k in ["immigration & checkpoints authority", "ica singapore"]):
            return "Singapore (ICA)"

        # United Arab Emirates
        if any(k in text_lower for k in ["ica uae", "gdrfa", "federal authority for identity"]):
            return "United Arab Emirates"

        # India
        if any(k in text_lower for k in
               ["frro", "foreigners regional registration office", "ministry of external affairs"]):
            return "India (FRRO)"

        # China
        if any(k in text_lower for k in ["exit-entry administration", "公安局", "visa affairs"]):
            return "China (Exit-Entry Administration)"

        return "Unknown Jurisdiction"

    # ------------------------------------------------------------
    # EMPTY RESULT
    # ------------------------------------------------------------

    def _empty_result_for_receipt(self) -> Dict[str, Any]:
        return {
            "type": "immigration_receipt",
            "subtype": "immigration_receipt",
            "summary": "No readable content could be extracted from this immigration document.",
            "confidence": 0.0,
            "fields": {},
            "raw_text": "",
            "validations": [],
            "alerts": [],
            "jurisdiction": "Unknown",
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
            return "This appears to be an immigration receipt or official notice, but key fields could not be reliably extracted."

        parts = []

        # Universal field names
        case_number = fields.get("case_number") or fields.get("reference_number") or fields.get("application_number")
        form_type = fields.get("form_type") or fields.get("application_type") or fields.get("visa_type")
        notice_type = fields.get("notice_type") or fields.get("document_type")
        applicant = fields.get("applicant") or fields.get("beneficiary") or fields.get("candidate")
        sponsor = fields.get("sponsor") or fields.get("petitioner") or fields.get("employer")
        receipt_date = fields.get("receipt_date") or fields.get("submission_date") or fields.get("application_date")
        authority = fields.get("authority") or fields.get("service_center") or fields.get("office")
        jurisdiction = fields.get("jurisdiction", "")

        if case_number:
            parts.append(f"Reference {case_number}")
        if form_type:
            parts.append(f"for {form_type}")
        if notice_type:
            parts.append(f"({notice_type})")

        header = " ".join(parts) if parts else "Immigration receipt or notice"

        if jurisdiction and jurisdiction != "Unknown Jurisdiction":
            header += f" from {jurisdiction}"

        details = []
        if applicant:
            details.append(f"Applicant: {applicant}")
        if sponsor:
            details.append(f"Sponsor: {sponsor}")
        if receipt_date:
            details.append(f"Date: {receipt_date}")
        if authority:
            details.append(f"Authority: {authority}")

        # Validity period — most critical for renewals
        validity_start = fields.get("validity_start")
        validity_end = fields.get("validity_end")
        if validity_start and validity_end:
            details.append(f"Valid: {validity_start} → {validity_end}")
            # Days-until-expiry warning
            try:
                exp_dt = dateutil_parser.parse(validity_end, dayfirst=False)
                days_left = (exp_dt - datetime.utcnow()).days
                if days_left < 0:
                    details.append("⛔ EXPIRED")
                elif days_left <= 180:
                    details.append(f"⚠️ Expires in {days_left} days — renewal action needed")
                else:
                    details.append(f"✅ {days_left} days remaining")
            except Exception:
                pass
        elif validity_end:
            details.append(f"Expires: {validity_end}")

        if details:
            return header + ". " + " | ".join(details)

        return header + "."

    # ------------------------------------------------------------
    # LIGHT ANALYTICS: VALIDATIONS + ALERTS
    # ------------------------------------------------------------

    def _run_validations(self, fields: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Run universal, jurisdiction-agnostic checks.
        Examples:
        - Missing critical fields
        - Suspicious reference number format
        - Missing dates
        """
        validations: List[Dict[str, Any]] = []

        def add(code: str, level: str, message: str, field: Optional[str] = None):
            validations.append(
                {
                    "code": code,
                    "level": level,  # info / warning / error
                    "message": message,
                    "field": field,
                }
            )

        # Critical fields (using universal terminology)
        case_number = (
                fields.get("case_number")
                or fields.get("reference_number")
                or fields.get("application_number")
        )
        if not case_number:
            add(
                code="missing_reference_number",
                level="error",
                message="Reference/case number could not be detected. Verify the OCR quality or upload a clearer copy.",
                field="case_number",
            )

        receipt_date = (
                fields.get("receipt_date")
                or fields.get("submission_date")
                or fields.get("application_date")
        )
        if not receipt_date:
            add(
                code="missing_date",
                level="warning",
                message="Document date is missing. This may affect timeline calculations and tracking.",
                field="receipt_date",
            )

        applicant = (
                fields.get("applicant")
                or fields.get("beneficiary")
                or fields.get("candidate")
        )
        if not applicant:
            add(
                code="missing_applicant",
                level="warning",
                message="Applicant name is missing. Confirm the document or OCR quality.",
                field="applicant",
            )

        sponsor = (
                fields.get("sponsor")
                or fields.get("petitioner")
                or fields.get("employer")
        )
        # Only warn if sponsor is typically required (e.g., work visas)
        form_type = (fields.get("form_type") or fields.get("application_type") or "").lower()
        if not sponsor and any(k in form_type for k in ["work", "employment", "sponsor", "employer"]):
            add(
                code="missing_sponsor",
                level="warning",
                message="Sponsor/employer name is missing for what appears to be a sponsored application.",
                field="sponsor",
            )

        # Reference number format check (very basic - just length)
        if case_number and len(case_number.replace(" ", "").replace("-", "")) < 6:
            add(
                code="short_reference_number",
                level="warning",
                message=f"Reference number '{case_number}' looks unusually short for an official immigration document.",
                field="case_number",
            )

        # Check if jurisdiction was detected
        jurisdiction = fields.get("jurisdiction", "Unknown Jurisdiction")
        if jurisdiction == "Unknown Jurisdiction":
            add(
                code="unknown_jurisdiction",
                level="info",
                message="Could not automatically detect the issuing country/authority. This may affect specialized validations.",
                field="jurisdiction",
            )

        # Validity / expiry checks — most critical for renewals
        validity_end = fields.get("validity_end")
        if not validity_end:
            add(
                code="missing_validity_end",
                level="warning",
                message="Expiration date could not be extracted. Manually verify the document validity period to avoid status lapses.",
                field="validity_end",
            )
        else:
            try:
                exp_dt = dateutil_parser.parse(validity_end, dayfirst=False)
                days_left = (exp_dt - datetime.utcnow()).days
                if days_left < 0:
                    add(
                        code="status_expired",
                        level="error",
                        message=f"This document EXPIRED on {validity_end} ({abs(days_left)} days ago). Immediate action required.",
                        field="validity_end",
                    )
                elif days_left <= 90:
                    add(
                        code="expiry_critical",
                        level="error",
                        message=f"Document expires in {days_left} days ({validity_end}). File renewal immediately — USCIS recommends filing 6 months before expiry.",
                        field="validity_end",
                    )
                elif days_left <= 180:
                    add(
                        code="expiry_warning",
                        level="warning",
                        message=f"Document expires in {days_left} days ({validity_end}). Begin renewal process now.",
                        field="validity_end",
                    )
                else:
                    add(
                        code="expiry_ok",
                        level="info",
                        message=f"Document valid until {validity_end} ({days_left} days remaining).",
                        field="validity_end",
                    )
            except Exception:
                add(
                    code="expiry_parse_error",
                    level="warning",
                    message=f"Could not parse expiration date '{validity_end}'. Manually verify the validity period.",
                    field="validity_end",
                )

        return validations

    def _build_alerts(self, validations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert validations into higher-level alerts for the UI.
        """
        alerts: List[Dict[str, Any]] = []

        for v in validations:
            if v["level"] == "error":
                alerts.append(
                    {
                        "severity": "high",
                        "title": "Critical Field Missing",
                        "message": v["message"],
                        "field": v.get("field"),
                    }
                )
            elif v["level"] == "warning":
                alerts.append(
                    {
                        "severity": "medium",
                        "title": "Potential Data Issue",
                        "message": v["message"],
                        "field": v.get("field"),
                    }
                )
            elif v["level"] == "info":
                alerts.append(
                    {
                        "severity": "low",
                        "title": "Information",
                        "message": v["message"],
                        "field": v.get("field"),
                    }
                )

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
        Generate actionable insights for immigration teams.
        """
        insights = []

        # Jurisdiction insight
        jurisdiction = fields.get("jurisdiction", "Unknown Jurisdiction")
        if jurisdiction == "Unknown Jurisdiction":
            insights.append({
                "type": "warning",
                "title": "Jurisdiction Detection Failed",
                "description": "Manual review recommended to identify the issuing authority",
                "color": "#FF6B6B",
                "icon": "⚠️"
            })
        else:
            insights.append({
                "type": "info",
                "title": f"Document from {jurisdiction}",
                "description": "Jurisdiction automatically identified from document content",
                "color": "#4A90E2",
                "icon": "🌍"
            })

        # Reference number insight
        case_number = (
                fields.get("case_number")
                or fields.get("reference_number")
                or fields.get("application_number")
        )
        if not case_number:
            insights.append({
                "type": "critical",
                "title": "Missing Reference Number",
                "description": "Document cannot be tracked without a reference number. OCR quality may be poor.",
                "color": "#DC3545",
                "icon": "🚫"
            })

        # Date insights
        receipt_date = (
                fields.get("receipt_date")
                or fields.get("submission_date")
                or fields.get("application_date")
        )
        if receipt_date:
            insights.append({
                "type": "success",
                "title": "Document Date Extracted",
                "description": f"Receipt/submission date: {receipt_date}",
                "color": "#00C851",
                "icon": "📅"
            })

        # Validation summary insight
        errors = len([v for v in validations if v["level"] == "error"])
        warnings = len([v for v in validations if v["level"] == "warning"])

        if errors == 0 and warnings == 0:
            insights.append({
                "type": "success",
                "title": "All Data Quality Checks Passed",
                "description": "Document appears complete and properly formatted",
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
        elif warnings > 0:
            insights.append({
                "type": "warning",
                "title": f"{warnings} Warning(s) Detected",
                "description": "Document may be missing optional information",
                "color": "#FFA726",
                "icon": "⚠️"
            })

        # Applicant/sponsor insight
        applicant = fields.get("applicant") or fields.get("beneficiary") or fields.get("candidate")
        sponsor = fields.get("sponsor") or fields.get("petitioner") or fields.get("employer")

        if applicant and sponsor:
            insights.append({
                "type": "info",
                "title": "Complete Party Information",
                "description": f"Both applicant ({applicant}) and sponsor ({sponsor}) identified",
                "color": "#4A90E2",
                "icon": "👥"
            })

        return insights

    # ------------------------------------------------------------
    # DASHBOARD HELPER METHODS
    # ------------------------------------------------------------

    def _calculate_field_completion(self, fields: Dict[str, str]) -> Dict[str, Any]:
        """Calculate what % of expected fields were extracted"""
        expected_fields = [
            "jurisdiction", "case_number", "form_type", "applicant",
            "sponsor", "receipt_date", "authority", "notice_type"
        ]

        # Check both primary and fallback field names
        found = 0
        for field in expected_fields:
            if field == "case_number":
                if fields.get("case_number") or fields.get("reference_number") or fields.get("application_number"):
                    found += 1
            elif field == "applicant":
                if fields.get("applicant") or fields.get("beneficiary") or fields.get("candidate"):
                    found += 1
            elif field == "sponsor":
                if fields.get("sponsor") or fields.get("petitioner") or fields.get("employer"):
                    found += 1
            elif field == "receipt_date":
                if fields.get("receipt_date") or fields.get("submission_date") or fields.get("application_date"):
                    found += 1
            elif field == "authority":
                if fields.get("authority") or fields.get("service_center") or fields.get("office"):
                    found += 1
            elif field == "form_type":
                if fields.get("form_type") or fields.get("application_type") or fields.get("visa_type"):
                    found += 1
            elif field == "notice_type":
                if fields.get("notice_type") or fields.get("document_type"):
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

    def _fields_to_table_data(self, fields: Dict[str, str]) -> List[Dict[str, Any]]:
        """Convert fields dict to table rows"""
        table_data = []

        # Define display order and labels
        field_config = [
            ("jurisdiction", "Jurisdiction"),
            ("case_number", "Case/Reference Number"),
            ("reference_number", "Reference Number"),
            ("application_number", "Application Number"),
            ("form_type", "Form Type"),
            ("application_type", "Application Type"),
            ("visa_type", "Visa Type"),
            ("notice_type", "Notice Type"),
            ("document_type", "Document Type"),
            ("applicant", "Applicant"),
            ("beneficiary", "Beneficiary"),
            ("candidate", "Candidate"),
            ("sponsor", "Sponsor"),
            ("petitioner", "Petitioner"),
            ("employer", "Employer"),
            ("receipt_date", "Receipt Date"),
            ("submission_date", "Submission Date"),
            ("application_date", "Application Date"),
            ("priority_date", "Priority Date"),
            ("processing_date", "Processing Date"),
            ("validity_start", "Valid From"),
            ("validity_end", "Expires / Valid To"),
            ("authority", "Authority"),
            ("service_center", "Service Center"),
            ("office", "Office"),
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
        """Get overall document status based on validations"""
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
        case_number = (
                fields.get("case_number")
                or fields.get("reference_number")
                or fields.get("application_number")
                or "N/A"
        )
        receipt_date = (
                fields.get("receipt_date")
                or fields.get("submission_date")
                or fields.get("application_date")
                or "N/A"
        )
        validity_end = fields.get("validity_end") or "N/A"
        expiry_color = "#FF6B6B"
        expiry_icon = "⛔"
        if validity_end != "N/A":
            try:
                days_left = (dateutil_parser.parse(validity_end, dayfirst=False) - datetime.utcnow()).days
                if days_left > 180:
                    expiry_color, expiry_icon = "#00C851", "✅"
                elif days_left > 90:
                    expiry_color, expiry_icon = "#FFA726", "⚠️"
                else:
                    expiry_color, expiry_icon = "#DC3545", "🚨"
            except Exception:
                pass

        return {
            # ========================================
            # DASHBOARD VIEW (for advanced analytics)
            # ========================================

                "hero_metrics": [
                    {
                        "key": "jurisdiction",
                        "label": "Jurisdiction",
                        "value": fields.get("jurisdiction", "Unknown"),
                        "icon": "🌍",
                        "color": "#4A90E2",
                        "size": "large"
                    },
                    {
                        "key": "status",
                        "label": "Document Status",
                        "value": self._get_status_from_validations(),
                        "icon": "✅" if self._all_validations_pass() else "⚠️",
                        "color": "#00C851" if self._all_validations_pass() else "#FF6B6B",
                        "size": "large"
                    },
                    {
                        "key": "reference",
                        "label": "Reference Number",
                        "value": case_number,
                        "icon": "🔢",
                        "color": "#FFA726",
                        "size": "medium"
                    },
                    {
                        "key": "date",
                        "label": "Document Date",
                        "value": receipt_date,
                        "icon": "📅",
                        "color": "#AB47BC",
                        "size": "medium"
                    },
                    {
                        "key": "expiry",
                        "label": "Expires / Valid To",
                        "value": validity_end,
                        "icon": expiry_icon,
                        "color": expiry_color,
                        "size": "large"
                    }
                ],

                "charts": [
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
                ],

                "tables": [
                    {
                        "id": "extracted_fields",
                        "title": "Extracted Document Fields",
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
                            "title": "Document Overview",
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
                            "id": "quality_metrics",
                            "title": "Data Quality Metrics",
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
                    "id": "document_summary",
                    "title": "Document Summary",
                    "type": "key_value",
                    "icon": "📋",
                    "fields": [
                        {
                            "label": "Jurisdiction",
                            "key": "jurisdiction"
                        },
                        {
                            "label": "Reference Number",
                            "key": "case_number",
                            "fallback_keys": ["reference_number", "application_number"]
                        },
                        {
                            "label": "Application Type",
                            "key": "form_type",
                            "fallback_keys": ["application_type", "visa_type"]
                        },
                        {
                            "label": "Document Type",
                            "key": "notice_type",
                            "fallback_keys": ["document_type"]
                        },
                        {
                            "label": "Date",
                            "key": "receipt_date",
                            "fallback_keys": ["submission_date", "application_date"]
                        },
                        {
                            "label": "Priority/Processing Date",
                            "key": "priority_date",
                            "fallback_keys": ["processing_date"]
                        },
                        {
                            "label": "Authority/Office",
                            "key": "authority",
                            "fallback_keys": ["service_center", "office"]
                        },
                        {
                            "label": "Applicant",
                            "key": "applicant",
                            "fallback_keys": ["beneficiary", "candidate"]
                        },
                        {
                            "label": "Sponsor/Petitioner",
                            "key": "sponsor",
                            "fallback_keys": ["petitioner", "employer"]
                        }
                    ]
                },
                {
                    "id": "document_alerts",
                    "title": "Data Quality & Alerts",
                    "type": "alerts",
                    "icon": "⚠️",
                    "field": "alerts",
                    "description": "Validation results and potential data issues"
                },
                {
                    "id": "document_validations",
                    "title": "Detailed Validations",
                    "type": "validations",
                    "icon": "✓",
                    "field": "validations",
                    "description": "All data quality checks performed on this document"
                },
                {
                    "id": "document_full_text",
                    "title": "Full Document Text",
                    "type": "text",
                    "icon": "📄",
                    "field": "raw_text",
                    "description": "Complete extracted text from the document",
                    "config": {
                        "maxHeight": "400px",
                        "copyable": True,
                        "searchable": True
                    }
                }
            ]
        }