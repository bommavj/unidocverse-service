"""
Universal Medical Record Analyzer - Production Ready
Version 1.0 - Multi-format support

Handles:
- CSV/Excel patient datasets
- PDF medical records (structured and unstructured)
- Clinical notes and reports
- Lab results
- Treatment records
- Multiple medical record formats

Author: B-Box AI (Vijay Bomma)
"""

import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

import pandas as pd

logger = logging.getLogger(__name__)

# Try imports for different file types
try:
    import pdfplumber

    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    logger.warning("pdfplumber not available - PDF support disabled")

try:
    from pdf2image import convert_from_path
    import pytesseract

    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


class UniversalMedicalRecordAnalyzer:
    """
    Universal medical record analyzer - handles multiple formats
    Extracts: Patient info, treatments, diagnostics, costs, outcomes
    """

    def __init__(self):
        self.metadata: Dict[str, Any] = {}
        self.statistics: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # PUBLIC ENTRYPOINT
    # ------------------------------------------------------------------
    def analyze(self, file_path: str = None, text: str = None, **kwargs) -> Dict[str, Any]:
        """
        Analyze medical record - auto-detects format

        Args:
            file_path: Path to the medical record file (CSV, Excel, PDF, etc.)
            text: Pre-extracted text content (for compatibility with LangGraph agents)
            **kwargs: Additional parameters for compatibility

        Returns:
            Dict with analysis results in LangGraph-compatible format
        """
        logger.info("🏥 Universal Medical Record Analyzer v1.0")

        # Handle both calling conventions
        if text and not file_path:
            # Text-based analysis (from LangGraph agent)
            logger.info(f"📝 Analyzing pre-extracted text ({len(text)} characters)")
            result = self._analyze_extracted_text(text, kwargs.get("file_ext", ".txt"))
        elif file_path:
            logger.info(f"📄 File: {file_path}")

            try:
                # Detect file type
                file_ext = Path(file_path).suffix.lower()

                if file_ext in [".csv", ".tsv"]:
                    result = self._analyze_csv(file_path)
                elif file_ext in [".xlsx", ".xls", ".xlsm"]:
                    result = self._analyze_excel(file_path)
                elif file_ext == ".pdf":
                    result = self._analyze_pdf(file_path)
                elif file_ext in [".txt", ".md"]:
                    result = self._analyze_text_file(file_path)
                else:
                    result = {
                        "success": False,
                        "error": f"Unsupported file type: {file_ext}",
                        "records": [],
                    }
            except Exception as e:
                logger.error(f"❌ Analysis error: {e}")
                import traceback

                logger.error(traceback.format_exc())
                result = {
                    "success": False,
                    "error": str(e),
                    "records": [],
                }
        else:
            result = {
                "success": False,
                "error": "Either file_path or text must be provided",
                "records": [],
            }

        # Transform to LangGraph-compatible + UI config
        if result.get("success"):
            result = self._add_langgraph_compatibility(result)

        return result

    # ------------------------------------------------------------------
    # LANGGRAPH + UI CONFIG
    # ------------------------------------------------------------------
    def _add_langgraph_compatibility(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add fields expected by LangGraph agent for advanced analytics
        """
        # Generate insights from statistics
        insights = self._generate_insights(result)

        # Generate alerts/warnings
        alerts = self._generate_alerts(result)

        # Detect result type
        result_type = result.get("record_type", "unknown")

        # Add LangGraph-compatible fields
        result["result_type"] = result_type
        result["has_advanced_analytics"] = True
        result["insights"] = insights
        result["alerts"] = alerts
        result["analysis_type"] = "medical_record_analysis"

        # Add chart recommendations (for generic consumers)
        result["recommended_charts"] = self._recommend_charts(result)

        # Add summary
        result["summary"] = self._generate_summary(result)

        # Add dynamic UI config for AdvancedAnalyticsComponent
        result["ui_config"] = self._build_medical_record_ui_config(result)

        return result

    # ------------------------------------------------------------------
    # DYNAMIC UI CONFIG BUILDER
    # ------------------------------------------------------------------
    def _build_medical_record_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build UI config dynamically from the analysis result.
        Mirrors the bank statement UI schema but adapts to whatever
        statistics / fields are actually present.
        """

        def has(path: str) -> bool:
            return self._get_nested(result, path) is not None

        def metric(key: str, label: str, path: str, is_hero: bool = False) -> Dict[str, Any]:
            return {"key": key, "label": label, "value_path": path, "is_hero": is_hero}

        # -----------------------------
        # HERO + SECONDARY METRICS
        # -----------------------------
        hero_metrics: List[Dict[str, Any]] = []

        # Always-available basics
        hero_metrics.append(metric("total_records", "Total Records", "total_records", True))
        hero_metrics.append(metric("record_type", "Record Type", "record_type", True))

        if has("data_quality.quality_score"):
            hero_metrics.append(metric("quality_score", "Data Quality Score", "data_quality.quality_score", True))
        if has("data_quality.missing_values"):
            hero_metrics.append(metric("missing_values", "Missing Values", "data_quality.missing_values", False))
        if has("data_quality.duplicate_rows"):
            hero_metrics.append(metric("duplicate_rows", "Duplicate Rows", "data_quality.duplicate_rows", False))

        # Patient treatment specific (only if present)
        if has("statistics.treatment_stats.total_cost"):
            hero_metrics.append(
                metric("total_cost", "Total Treatment Cost", "statistics.treatment_stats.total_cost", True)
            )
        if has("statistics.treatment_stats.avg_cost"):
            hero_metrics.append(
                metric("avg_cost", "Avg Cost / Patient", "statistics.treatment_stats.avg_cost", False)
            )
        if has("statistics.treatment_stats.avg_stay_days"):
            hero_metrics.append(
                metric(
                    "avg_stay_days",
                    "Avg Hospital Stay (Days)",
                    "statistics.treatment_stats.avg_stay_days",
                    False,
                )
            )
        if has("statistics.treatment_stats.avg_recovery_score"):
            hero_metrics.append(
                metric(
                    "avg_recovery_score",
                    "Avg Recovery Score",
                    "statistics.treatment_stats.avg_recovery_score",
                    True,
                )
            )

        # -----------------------------
        # TABLES
        # -----------------------------
        tables: List[Dict[str, Any]] = []

        tables.append({"id": "records", "name": "records", "title": "Medical Records", "data_path": "records"})
        tables.append({"id": "alerts", "name": "alerts", "title": "Alerts & Warnings", "data_path": "alerts"})
        tables.append({"id": "insights", "name": "insights", "title": "Insights", "data_path": "insights"})

        if has("statistics.numeric_summary"):
            tables.append(
                {
                    "id": "numeric_summary",
                    "name": "numeric_summary",
                    "title": "Numeric Summary",
                    "data_path": "statistics.numeric_summary",
                }
            )

        if has("statistics.categorical_summary"):
            tables.append(
                {
                    "id": "categorical_summary",
                    "name": "categorical_summary",
                    "title": "Categorical Summary",
                    "data_path": "statistics.categorical_summary",
                }
            )

        if has("statistics.treatment_stats.departments"):
            tables.append(
                {
                    "id": "departments",
                    "name": "departments",
                    "title": "Department Distribution",
                    "data_path": "statistics.treatment_stats.departments",
                }
            )

        if has("statistics.treatment_stats.treatment_types"):
            tables.append(
                {
                    "id": "treatment_types",
                    "name": "treatment_types",
                    "title": "Treatment Types",
                    "data_path": "statistics.treatment_stats.treatment_types",
                }
            )

        if has("statistics.treatment_stats.gender_distribution"):
            tables.append(
                {
                    "id": "gender_distribution",
                    "name": "gender_distribution",
                    "title": "Gender Distribution",
                    "data_path": "statistics.treatment_stats.gender_distribution",
                }
            )

        if has("statistics.treatment_stats.age_distribution"):
            tables.append(
                {
                    "id": "age_distribution",
                    "name": "age_distribution",
                    "title": "Age Distribution",
                    "data_path": "statistics.treatment_stats.age_distribution",
                }
            )

        # -----------------------------
        # CHARTS
        # -----------------------------
        charts: List[Dict[str, Any]] = []

        if has("statistics.treatment_stats.departments"):
            charts.append(
                {
                    "id": "department_distribution",
                    "type": "pie",
                    "title": "Patients by Department",
                    "data_path": "statistics.treatment_stats.departments",
                    "label_field": "key",
                    "value_field": "value",
                }
            )

        if has("statistics.treatment_stats.treatment_types"):
            charts.append(
                {
                    "id": "treatment_type_distribution",
                    "type": "bar",
                    "title": "Treatment Types",
                    "data_path": "statistics.treatment_stats.treatment_types",
                    "label_field": "key",
                    "value_field": "value",
                }
            )

        if has("statistics.treatment_stats.gender_distribution"):
            charts.append(
                {
                    "id": "gender_distribution",
                    "type": "pie",
                    "title": "Gender Distribution",
                    "data_path": "statistics.treatment_stats.gender_distribution",
                    "label_field": "key",
                    "value_field": "value",
                }
            )

        if has("statistics.treatment_stats.age_distribution"):
            charts.append(
                {
                    "id": "age_distribution",
                    "type": "bar",
                    "title": "Age Distribution",
                    "data_path": "statistics.treatment_stats.age_distribution",
                    "label_field": "key",
                    "value_field": "value",
                }
            )

        # If numeric summary has a cost-like field, expose a box plot
        numeric_summary = result.get("statistics", {}).get("numeric_summary", {}) or {}
        cost_like_col = None
        for col in numeric_summary.keys():
            if "cost" in col.lower() or "charge" in col.lower() or "amount" in col.lower():
                cost_like_col = col
                break

        if cost_like_col:
            charts.append(
                {
                    "id": "cost_distribution",
                    "type": "box",
                    "title": f"{cost_like_col} Distribution",
                    "data_path": f"statistics.numeric_summary.{cost_like_col}",
                    "label_field": "key",
                    "value_field": "value",
                }
            )

        # Scatter: only if both fields exist in numeric summary
        if "Hospital Stay (Days)" in numeric_summary and "Recovery Score" in numeric_summary:
            charts.append(
                {
                    "id": "stay_vs_recovery",
                    "type": "scatter",
                    "title": "Hospital Stay vs Recovery Score",
                    "x_axis": "Hospital Stay (Days)",
                    "y_axis": "Recovery Score",
                    "data_path": "records",
                }
            )

        # -----------------------------
        # SECTIONS
        # -----------------------------
        sections: List[Dict[str, Any]] = []

        sections.append(
            {
                "id": "summary",
                "title": "Summary",
                "metrics": [m["key"] for m in hero_metrics if m["is_hero"]],
                "charts": [],
                "tables": ["insights", "alerts"],
            }
        )

        if any(
            k in [m["key"] for m in hero_metrics]
            for k in ["total_cost", "avg_cost", "avg_stay_days", "avg_recovery_score"]
        ) or any(c["id"] in ["department_distribution", "treatment_type_distribution"] for c in charts):
            sections.append(
                {
                    "id": "treatment_overview",
                    "title": "Treatment Overview",
                    "metrics": [
                        k
                        for k in ["total_cost", "avg_cost", "avg_stay_days", "avg_recovery_score"]
                        if any(m["key"] == k for m in hero_metrics)
                    ],
                    "charts": [
                        c["id"]
                        for c in charts
                        if c["id"] in ["department_distribution", "treatment_type_distribution"]
                    ],
                    "tables": [t["id"] for t in tables if t["id"] in ["departments", "treatment_types"]],
                }
            )

        if any(t["id"] in ["gender_distribution", "age_distribution"] for t in tables):
            sections.append(
                {
                    "id": "patient_demographics",
                    "title": "Patient Demographics",
                    "metrics": [],
                    "charts": [c["id"] for c in charts if c["id"] in ["gender_distribution", "age_distribution"]],
                    "tables": [t["id"] for t in tables if t["id"] in ["gender_distribution", "age_distribution"]],
                }
            )

        if any(t["id"] in ["numeric_summary", "categorical_summary"] for t in tables):
            sections.append(
                {
                    "id": "statistics",
                    "title": "Statistical Summary",
                    "metrics": [],
                    "charts": [c["id"] for c in charts if c["id"] == "cost_distribution"],
                    "tables": [t["id"] for t in tables if t["id"] in ["numeric_summary", "categorical_summary"]],
                }
            )

        if any(c["id"] == "stay_vs_recovery" for c in charts):
            sections.append(
                {
                    "id": "relationships",
                    "title": "Outcome Relationships",
                    "metrics": [],
                    "charts": ["stay_vs_recovery"],
                    "tables": [],
                }
            )

        sections.append(
            {
                "id": "all_records",
                "title": "All Records",
                "metrics": [],
                "charts": [],
                "tables": ["records"],
            }
        )

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }

    # ------------------------------------------------------------------
    # SMALL UTILS
    # ------------------------------------------------------------------
    def _get_nested(self, obj: Dict[str, Any], path: str) -> Any:
        """Safe nested getter using dot-separated path."""
        parts = path.split(".")
        cur: Any = obj
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
        return cur

    # ------------------------------------------------------------------
    # INSIGHTS / ALERTS / SUMMARY
    # ------------------------------------------------------------------
    def _generate_insights(self, result: Dict[str, Any]) -> List[str]:
        """Generate human-readable insights from analysis"""
        insights: List[str] = []

        try:
            stats = result.get("statistics", {})
            total = result.get("total_records", 0)

            if total == 0:
                return []

            # General insights
            insights.append(f"Analyzed {total} medical records successfully")

            # Record type specific insights
            if result.get("record_type") == "patient_treatment":
                treatment_stats = stats.get("treatment_stats", {})

                if "total_cost" in treatment_stats:
                    total_cost = treatment_stats["total_cost"]
                    avg_cost = treatment_stats.get("avg_cost", 0)
                    insights.append(
                        f"Total treatment costs: ${total_cost:,.2f} (avg: ${avg_cost:,.2f} per patient)"
                    )

                if "avg_stay_days" in treatment_stats:
                    avg_stay = treatment_stats["avg_stay_days"]
                    insights.append(f"Average hospital stay: {avg_stay:.1f} days")

                if "avg_recovery_score" in treatment_stats:
                    avg_recovery = treatment_stats["avg_recovery_score"]
                    insights.append(f"Average recovery score: {avg_recovery:.1f}/100")

                if "departments" in treatment_stats and treatment_stats["departments"]:
                    top_dept = max(treatment_stats["departments"].items(), key=lambda x: x[1])
                    insights.append(f"Busiest department: {top_dept[0]} with {top_dept[1]} patients")

                if "treatment_types" in treatment_stats and treatment_stats["treatment_types"]:
                    top_treatment = max(treatment_stats["treatment_types"].items(), key=lambda x: x[1])
                    insights.append(f"Most common treatment: {top_treatment[0]} ({top_treatment[1]} cases)")

                if "age_distribution" in treatment_stats:
                    age_dist = treatment_stats["age_distribution"]
                    pediatric = age_dist.get("pediatric (<18)", 0)
                    senior = age_dist.get("senior (>65)", 0)
                    insights.append(f"Age distribution: {pediatric} pediatric, {senior} senior patients")

                if "gender_distribution" in treatment_stats:
                    gender_dist = treatment_stats["gender_distribution"]
                    insights.append(f"Gender distribution: {dict(gender_dist)}")

            # Data quality insight
            quality = result.get("data_quality", {})
            if "quality_score" in quality:
                score = quality["quality_score"]
                if score >= 95:
                    insights.append(f"Excellent data quality: {score}/100 score")
                elif score >= 80:
                    insights.append(f"Good data quality: {score}/100 score")
                else:
                    insights.append(f"Data quality concerns: {score}/100 score")

            # Column insights
            if "columns" in result:
                col_count = len(result["columns"])
                insights.append(f"Dataset contains {col_count} data fields")

        except Exception as e:
            logger.error(f"Error generating insights: {e}")

        return insights

    def _generate_alerts(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate alerts/warnings based on analysis"""
        alerts: List[Dict[str, Any]] = []

        try:
            # Data quality alerts
            quality = result.get("data_quality", {})

            if quality.get("quality_score", 100) < 80:
                alerts.append(
                    {
                        "type": "warning",
                        "message": f"Low data quality score: {quality['quality_score']}/100",
                        "severity": "medium",
                    }
                )

            if quality.get("missing_values", 0) > 0:
                alerts.append(
                    {
                        "type": "warning",
                        "message": f"Found {quality['missing_values']} missing values",
                        "severity": "low",
                    }
                )

            if quality.get("duplicate_rows", 0) > 0:
                alerts.append(
                    {
                        "type": "warning",
                        "message": f"Found {quality['duplicate_rows']} duplicate records",
                        "severity": "medium",
                    }
                )

            # Treatment-specific alerts
            if result.get("record_type") == "patient_treatment":
                stats = result.get("statistics", {})
                treatment_stats = stats.get("treatment_stats", {})

                # High cost alert
                if "avg_cost" in treatment_stats:
                    avg_cost = treatment_stats["avg_cost"]
                    if avg_cost > 100000:
                        alerts.append(
                            {
                                "type": "info",
                                "message": f"High average treatment cost: ${avg_cost:,.2f}",
                                "severity": "low",
                            }
                        )

                # Long stay alert
                if "avg_stay_days" in treatment_stats:
                    avg_stay = treatment_stats["avg_stay_days"]
                    if avg_stay > 20:
                        alerts.append(
                            {
                                "type": "info",
                                "message": f"Extended average hospital stay: {avg_stay:.1f} days",
                                "severity": "low",
                            }
                        )

                # Low recovery score alert
                if "avg_recovery_score" in treatment_stats:
                    avg_recovery = treatment_stats["avg_recovery_score"]
                    if avg_recovery < 60:
                        alerts.append(
                            {
                                "type": "warning",
                                "message": f"Below-average recovery scores: {avg_recovery:.1f}/100",
                                "severity": "medium",
                            }
                        )

        except Exception as e:
            logger.error(f"Error generating alerts: {e}")

        return alerts

    def _recommend_charts(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Recommend appropriate visualizations (generic consumer, not UI config)"""
        charts: List[Dict[str, Any]] = []

        try:
            record_type = result.get("record_type")

            if record_type == "patient_treatment":
                stats = result.get("statistics", {})
                treatment_stats = stats.get("treatment_stats", {})

                # Department distribution pie chart
                if "departments" in treatment_stats:
                    charts.append(
                        {
                            "type": "pie",
                            "title": "Patient Distribution by Department",
                            "data_key": "statistics.treatment_stats.departments",
                            "description": "Shows the distribution of patients across different hospital departments",
                        }
                    )

                # Treatment type bar chart
                if "treatment_types" in treatment_stats:
                    charts.append(
                        {
                            "type": "bar",
                            "title": "Treatment Type Distribution",
                            "data_key": "statistics.treatment_stats.treatment_types",
                            "description": "Compares the frequency of different treatment types",
                        }
                    )

                # Age distribution histogram
                if "numeric_summary" in stats and "Age" in stats["numeric_summary"]:
                    charts.append(
                        {
                            "type": "histogram",
                            "title": "Age Distribution",
                            "data_key": "Age",
                            "description": "Shows the age distribution of patients",
                        }
                    )

                # Cost distribution box plot
                if "numeric_summary" in stats and "Treatment Cost" in stats["numeric_summary"]:
                    charts.append(
                        {
                            "type": "box",
                            "title": "Treatment Cost Distribution",
                            "data_key": "Treatment Cost",
                            "description": "Visualizes treatment cost ranges and outliers",
                        }
                    )

                # Hospital stay vs recovery scatter
                if "Hospital Stay (Days)" in stats.get("numeric_summary", {}) and "Recovery Score" in stats.get(
                    "numeric_summary", {}
                ):
                    charts.append(
                        {
                            "type": "scatter",
                            "title": "Hospital Stay vs Recovery Score",
                            "x_axis": "Hospital Stay (Days)",
                            "y_axis": "Recovery Score",
                            "description": "Analyzes relationship between length of stay and recovery outcomes",
                        }
                    )

                # Gender distribution
                if "gender_distribution" in treatment_stats:
                    charts.append(
                        {
                            "type": "pie",
                            "title": "Gender Distribution",
                            "data_key": "statistics.treatment_stats.gender_distribution",
                            "description": "Shows patient distribution by gender",
                        }
                    )

        except Exception as e:
            logger.error(f"Error recommending charts: {e}")

        return charts

    def _generate_summary(self, result: Dict[str, Any]) -> str:
        """Generate executive summary"""
        try:
            total = result.get("total_records", 0)
            record_type = result.get("record_type", "unknown")

            if record_type == "patient_treatment":
                stats = result.get("statistics", {})
                treatment_stats = stats.get("treatment_stats", {})

                summary_parts = [
                    f"Analyzed {total} patient treatment records.",
                ]

                if "total_cost" in treatment_stats:
                    total_cost = treatment_stats["total_cost"]
                    summary_parts.append(f"Total treatment costs: ${total_cost:,.2f}.")

                if "departments" in treatment_stats:
                    dept_count = len(treatment_stats["departments"])
                    summary_parts.append(f"Data covers {dept_count} departments.")

                if "avg_recovery_score" in treatment_stats:
                    avg_recovery = treatment_stats["avg_recovery_score"]
                    summary_parts.append(f"Average patient recovery score: {avg_recovery:.1f}/100.")

                quality = result.get("data_quality", {})
                if "quality_score" in quality:
                    score = quality["quality_score"]
                    summary_parts.append(f"Data quality score: {score}/100.")

                return " ".join(summary_parts)
            else:
                return f"Analyzed {total} {record_type} records with comprehensive statistics."

        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return f"Analyzed {result.get('total_records', 0)} medical records."

    # ------------------------------------------------------------------
    # TEXT / CSV / EXCEL / PDF ANALYSIS
    # ------------------------------------------------------------------
    def _analyze_extracted_text(self, text: str, file_ext: str = ".txt") -> Dict[str, Any]:
        """
        Analyze pre-extracted text (for LangGraph agent compatibility)
        This handles text that's already been extracted from files
        """
        logger.info("📊 Analyzing extracted text...")

        try:
            # Try to detect if it's CSV-like data
            if self._looks_like_csv(text):
                return self._analyze_csv_text(text)

            # Try to detect if it's structured data
            if self._looks_like_structured_data(text):
                return self._analyze_structured_text(text)

            # Otherwise treat as clinical notes
            return self._analyze_clinical_text(text)

        except Exception as e:
            logger.error(f"Text analysis error: {e}")
            return {
                "success": False,
                "error": str(e),
                "records": [],
            }

    def _looks_like_csv(self, text: str) -> bool:
        """Check if text looks like CSV data"""
        lines = text.strip().split("\n")
        if len(lines) < 2:
            return False

        # Check if first line has comma-separated headers
        first_line = lines[0]
        if "," in first_line and len(first_line.split(",")) >= 3:
            # Check if second line also has similar structure
            second_line = lines[1]
            return len(second_line.split(",")) == len(first_line.split(","))

        return False

    def _looks_like_structured_data(self, text: str) -> bool:
        """Check if text contains structured data patterns"""
        # Look for patterns like "Field: Value"
        pattern_count = len(re.findall(r"\w+\s*:\s*.+", text))
        return pattern_count > 5

    def _analyze_csv_text(self, text: str) -> Dict[str, Any]:
        """Analyze CSV-formatted text"""
        try:
            from io import StringIO

            df = pd.read_csv(StringIO(text))

            logger.info(f"✅ Parsed CSV text: {len(df)} records with {len(df.columns)} columns")

            record_type = self._detect_record_type(df.columns.tolist())
            logger.info(f"🔍 Detected record type: {record_type}")

            records = self._extract_structured_records(df, record_type)
            stats = self._calculate_statistics(df, record_type)
            metadata = self._extract_metadata(df)

            logger.info("\n" + "=" * 70)
            logger.info("✅ ANALYSIS RESULTS")
            logger.info("=" * 70)
            logger.info(f"Total Records: {len(records)}")
            logger.info(f"Record Type: {record_type}")

            return {
                "success": True,
                "records": records,
                "statistics": stats,
                "metadata": metadata,
                "record_type": record_type,
                "total_records": len(records),
                "columns": df.columns.tolist(),
                "data_quality": self._assess_data_quality(df),
            }

        except Exception as e:
            logger.error(f"CSV text analysis error: {e}")
            return {
                "success": False,
                "error": str(e),
                "records": [],
            }

    def _analyze_structured_text(self, text: str) -> Dict[str, Any]:
        """Analyze structured text data (key-value pairs)"""
        records: List[Dict[str, Any]] = []
        current_record: Dict[str, Any] = {}

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                if current_record:
                    records.append(current_record)
                    current_record = {}
                continue

            # Parse "Key: Value" pattern
            match = re.match(r"([^:]+):\s*(.+)", line)
            if match:
                key = match.group(1).strip()
                value = match.group(2).strip()
                current_record[key] = value

        if current_record:
            records.append(current_record)

        logger.info(f"✅ Extracted {len(records)} structured records")

        return {
            "success": True,
            "records": records,
            "record_type": "structured_text",
            "total_records": len(records),
        }

    def _analyze_clinical_text(self, text: str) -> Dict[str, Any]:
        """Analyze unstructured clinical text"""
        records = self._extract_clinical_notes(text)

        logger.info(f"✅ Extracted {len(records)} clinical note sections")

        return {
            "success": True,
            "records": records,
            "record_type": "clinical_notes",
            "total_records": len(records),
            "full_text": text[:1000],
        }

    def _analyze_csv(self, file_path: str) -> Dict[str, Any]:
        """Analyze CSV medical records"""
        logger.info("📊 Analyzing CSV medical records...")

        try:
            # Read CSV
            df = pd.read_csv(file_path)

            logger.info(f"✅ Loaded {len(df)} records with {len(df.columns)} columns")

            # Detect record type
            record_type = self._detect_record_type(df.columns.tolist())
            logger.info(f"🔍 Detected record type: {record_type}")

            # Extract records based on type
            records = self._extract_structured_records(df, record_type)

            # Calculate statistics
            stats = self._calculate_statistics(df, record_type)

            # Extract metadata
            metadata = self._extract_metadata(df)

            logger.info("\n" + "=" * 70)
            logger.info("✅ ANALYSIS RESULTS")
            logger.info("=" * 70)
            logger.info(f"Total Records: {len(records)}")
            logger.info(f"Record Type: {record_type}")
            logger.info(f"Columns: {len(df.columns)}")

            return {
                "success": True,
                "records": records,
                "statistics": stats,
                "metadata": metadata,
                "record_type": record_type,
                "total_records": len(records),
                "columns": df.columns.tolist(),
                "data_quality": self._assess_data_quality(df),
            }

        except Exception as e:
            logger.error(f"CSV analysis error: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e),
                "records": [],
            }

    def _analyze_excel(self, file_path: str) -> Dict[str, Any]:
        """Analyze Excel medical records"""
        logger.info("📊 Analyzing Excel medical records...")

        try:
            # Read all sheets
            excel_file = pd.ExcelFile(file_path)
            logger.info(f"   Found {len(excel_file.sheet_names)} sheets: {excel_file.sheet_names}")

            all_records: List[Dict[str, Any]] = []
            all_stats: Dict[str, Any] = {}

            # Process each sheet
            for sheet_name in excel_file.sheet_names:
                logger.info(f"\n   📄 Processing sheet: {sheet_name}")
                df = pd.read_excel(file_path, sheet_name=sheet_name)

                if df.empty:
                    logger.info("      ⚠️  Empty sheet, skipping")
                    continue

                record_type = self._detect_record_type(df.columns.tolist())
                records = self._extract_structured_records(df, record_type)

                all_records.extend(records)
                all_stats[sheet_name] = self._calculate_statistics(df, record_type)

                logger.info(f"      ✅ Extracted {len(records)} records")

            logger.info("\n" + "=" * 70)
            logger.info("✅ ANALYSIS RESULTS")
            logger.info("=" * 70)
            logger.info(f"Total Records (all sheets): {len(all_records)}")
            logger.info(f"Sheets Processed: {len(excel_file.sheet_names)}")

            return {
                "success": True,
                "records": all_records,
                "statistics": all_stats,
                "sheets": excel_file.sheet_names,
                "total_records": len(all_records),
            }

        except Exception as e:
            logger.error(f"Excel analysis error: {e}")
            return {
                "success": False,
                "error": str(e),
                "records": [],
            }

    def _analyze_pdf(self, file_path: str) -> Dict[str, Any]:
        """Analyze PDF medical records"""
        if not PDF_AVAILABLE:
            return {
                "success": False,
                "error": "PDF support not available - install pdfplumber",
                "records": [],
            }

        logger.info("📄 Analyzing PDF medical records...")

        try:
            # Extract text
            text = self._extract_pdf_text(file_path)

            if not text or len(text.strip()) < 50:
                logger.info("   Using OCR for image-based PDF...")
                text = self._extract_pdf_ocr(file_path)

            if not text:
                return {
                    "success": False,
                    "error": "Could not extract text from PDF",
                    "records": [],
                }

            logger.info(f"✅ Extracted {len(text)} characters")

            # Try to detect if it's structured (table) or unstructured (clinical notes)
            if self._has_tabular_data(text):
                records = self._extract_tabular_pdf_records(text)
                record_type = "tabular_medical_record"
            else:
                records = self._extract_clinical_notes(text)
                record_type = "clinical_notes"

            logger.info("\n" + "=" * 70)
            logger.info("✅ ANALYSIS RESULTS")
            logger.info("=" * 70)
            logger.info(f"Record Type: {record_type}")
            logger.info(f"Extracted Records: {len(records)}")

            return {
                "success": True,
                "records": records,
                "record_type": record_type,
                "total_records": len(records),
                "full_text": text[:1000],  # First 1000 chars for preview
            }

        except Exception as e:
            logger.error(f"PDF analysis error: {e}")
            return {
                "success": False,
                "error": str(e),
                "records": [],
            }

    def _analyze_text_file(self, file_path: str) -> Dict[str, Any]:
        """Analyze text-based medical records from file"""
        logger.info("📝 Analyzing text medical records...")

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()

            records = self._extract_clinical_notes(text)

            return {
                "success": True,
                "records": records,
                "record_type": "clinical_notes",
                "total_records": len(records),
                "full_text": text[:1000],
            }

        except Exception as e:
            logger.error(f"Text analysis error: {e}")
            return {
                "success": False,
                "error": str(e),
                "records": [],
            }

    # ------------------------------------------------------------------
    # STRUCTURED DATA HELPERS
    # ------------------------------------------------------------------
    def _detect_record_type(self, columns: List[str]) -> str:
        """Detect the type of medical record based on columns"""
        columns_lower = [c.lower() for c in columns]

        # Patient treatment records
        if any("patient" in c for c in columns_lower) and any("treatment" in c for c in columns_lower):
            return "patient_treatment"

        # Lab results
        if any("test" in c or "result" in c or "lab" in c for c in columns_lower):
            return "lab_results"

        # Prescription records
        if any("medication" in c or "prescription" in c or "drug" in c for c in columns_lower):
            return "prescription"

        # Billing/Claims
        if any("claim" in c or "billing" in c or "insurance" in c for c in columns_lower):
            return "billing"

        # Appointment records
        if any("appointment" in c or "visit" in c for c in columns_lower):
            return "appointment"

        # Vital signs
        if any("vital" in c or "temperature" in c or "pressure" in c for c in columns_lower):
            return "vital_signs"

        return "generic_medical"

    def _extract_structured_records(self, df: pd.DataFrame, record_type: str) -> List[Dict[str, Any]]:
        """Extract records from structured data (CSV/Excel)"""
        records: List[Dict[str, Any]] = []

        for idx, row in df.iterrows():
            record: Dict[str, Any] = {}

            # Convert row to dict
            for col in df.columns:
                value = row[col]

                # Handle different data types
                if pd.isna(value):
                    record[col] = None
                elif isinstance(value, (int, float)):
                    record[col] = float(value) if isinstance(value, float) else int(value)
                elif isinstance(value, datetime):
                    record[col] = value.strftime("%Y-%m-%d")
                else:
                    record[col] = str(value)

            # Add record type
            record["_record_type"] = record_type
            record["_record_index"] = int(idx) + 1

            records.append(record)

        logger.info(f"   ✅ Extracted {len(records)} {record_type} records")
        return records

    def _calculate_statistics(self, df: pd.DataFrame, record_type: str) -> Dict[str, Any]:
        """Calculate statistics from medical records"""
        stats: Dict[str, Any] = {
            "total_records": len(df),
            "columns": len(df.columns),
        }

        # Numeric statistics
        numeric_cols = df.select_dtypes(include=["int64", "float64"]).columns
        if len(numeric_cols) > 0:
            stats["numeric_summary"] = {}
            for col in numeric_cols:
                stats["numeric_summary"][col] = {
                    "mean": float(df[col].mean()),
                    "median": float(df[col].median()),
                    "min": float(df[col].min()),
                    "max": float(df[col].max()),
                    "std": float(df[col].std()),
                }

        # Categorical statistics
        categorical_cols = df.select_dtypes(include=["object"]).columns
        if len(categorical_cols) > 0:
            stats["categorical_summary"] = {}
            for col in categorical_cols[:10]:  # Limit to first 10
                value_counts = df[col].value_counts().head(10).to_dict()
                stats["categorical_summary"][col] = {
                    "unique_values": int(df[col].nunique()),
                    "top_values": value_counts,
                }

        # Optional: treatment-specific stats if columns exist
        treatment_stats: Dict[str, Any] = {}

        # Cost
        cost_cols = [c for c in numeric_cols if "cost" in c.lower() or "charge" in c.lower() or "amount" in c.lower()]
        if cost_cols:
            cost_col = cost_cols[0]
            treatment_stats["total_cost"] = float(df[cost_col].sum())
            treatment_stats["avg_cost"] = float(df[cost_col].mean())

        # Stay days
        stay_cols = [c for c in numeric_cols if "stay" in c.lower() or "length of stay" in c.lower()]
        if stay_cols:
            stay_col = stay_cols[0]
            treatment_stats["avg_stay_days"] = float(df[stay_col].mean())

        # Recovery score
        recovery_cols = [c for c in numeric_cols if "recovery" in c.lower() or "outcome score" in c.lower()]
        if recovery_cols:
            rec_col = recovery_cols[0]
            treatment_stats["avg_recovery_score"] = float(df[rec_col].mean())

        # Departments
        dept_cols = [c for c in categorical_cols if "department" in c.lower() or "unit" in c.lower()]
        if dept_cols:
            dept_col = dept_cols[0]
            treatment_stats["departments"] = df[dept_col].value_counts().to_dict()

        # Treatment types
        treat_cols = [c for c in categorical_cols if "treatment" in c.lower() or "procedure" in c.lower()]
        if treat_cols:
            treat_col = treat_cols[0]
            treatment_stats["treatment_types"] = df[treat_col].value_counts().to_dict()

        # Gender distribution
        gender_cols = [c for c in categorical_cols if "gender" in c.lower() or "sex" in c.lower()]
        if gender_cols:
            gender_col = gender_cols[0]
            treatment_stats["gender_distribution"] = df[gender_col].value_counts().to_dict()

        # Age distribution (bucketed)
        age_cols = [c for c in numeric_cols if "age" in c.lower()]
        if age_cols:
            age_col = age_cols[0]
            age_series = df[age_col].dropna()
            age_dist = {
                "pediatric (<18)": int((age_series < 18).sum()),
                "adult (18-65)": int(((age_series >= 18) & (age_series <= 65)).sum()),
                "senior (>65)": int((age_series > 65).sum()),
            }
            treatment_stats["age_distribution"] = age_dist

        if treatment_stats:
            stats["treatment_stats"] = treatment_stats

        return stats

    def _extract_metadata(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Extract lightweight metadata from a medical dataset.
        This is intentionally generic so it works for ANY medical record type.
        """
        try:
            metadata: Dict[str, Any] = {
                "row_count": int(len(df)),
                "column_count": int(len(df.columns)),
                "columns": list(df.columns),
                "numeric_columns": [
                    c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
                ],
                "categorical_columns": [
                    c for c in df.columns if pd.api.types.is_string_dtype(df[c])
                ],
                "generated_at": datetime.utcnow().isoformat() + "Z",
            }

            # Detect date-like columns
            date_cols: List[str] = []
            for col in df.columns:
                try:
                    sample = df[col].dropna().astype(str).head(20)
                    if sample.empty:
                        continue
                    parsed = pd.to_datetime(sample, errors="coerce")
                    if parsed.notna().sum() >= max(3, len(sample) // 2):
                        date_cols.append(col)
                except Exception:
                    pass

            metadata["date_columns"] = date_cols

            return metadata

        except Exception as e:
            logger.error(f"Metadata extraction error: {e}")
            return {
                "row_count": len(df),
                "column_count": len(df.columns),
                "columns": list(df.columns),
                "error": str(e),
            }

    def _assess_data_quality(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Assess basic data quality metrics"""
        try:
            total_cells = df.shape[0] * df.shape[1]
            missing_values = int(df.isna().sum().sum())
            duplicate_rows = int(df.duplicated().sum())

            # Simple quality score: start at 100, subtract penalties
            score = 100
            if total_cells > 0:
                missing_ratio = missing_values / total_cells
                score -= int(missing_ratio * 40)  # up to -40 for missing
            if df.shape[0] > 0:
                dup_ratio = duplicate_rows / df.shape[0]
                score -= int(dup_ratio * 30)  # up to -30 for duplicates

            score = max(0, min(100, score))

            return {
                "quality_score": score,
                "missing_values": missing_values,
                "duplicate_rows": duplicate_rows,
            }
        except Exception as e:
            logger.error(f"Data quality assessment error: {e}")
            return {
                "quality_score": 0,
                "missing_values": None,
                "duplicate_rows": None,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # PDF / OCR HELPERS
    # ------------------------------------------------------------------
    def _extract_pdf_text(self, file_path: str) -> Optional[str]:
        """Extract text from PDF using pdfplumber"""
        if not PDF_AVAILABLE:
            return None

        try:
            text_parts: List[str] = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    text_parts.append(page_text)
            text = "\n".join(text_parts)
            return text.strip() or None
        except Exception as e:
            logger.error(f"PDF text extraction error: {e}")
            return None

    def _extract_pdf_ocr(self, file_path: str) -> Optional[str]:
        """Extract text from any document using universal OCR (auto-rotate, best engine)."""
        try:
            from app.services.ocr.ocr_utils import ocr_any
            text = ocr_any(file_path)
            return text.strip() or None
        except Exception as e:
            logger.error(f"OCR extraction error: {e}")
            return None

    def _has_tabular_data(self, text: str) -> bool:
        """Heuristic to detect if PDF text contains tabular data"""
        lines = text.split("\n")
        table_like = 0
        for line in lines:
            if "," in line or "\t" in line:
                table_like += 1
        return table_like > max(5, len(lines) * 0.1)

    def _extract_tabular_pdf_records(self, text: str) -> List[Dict[str, Any]]:
        """
        Very simple heuristic: try to parse CSV-like lines from PDF text.
        This is intentionally conservative and generic.
        """
        lines = [l for l in text.split("\n") if l.strip()]
        if not lines:
            return []

        # Find a header line (most commas)
        header_line = max(lines, key=lambda l: l.count(","))
        headers = [h.strip() for h in header_line.split(",") if h.strip()]
        if len(headers) < 2:
            return []

        records: List[Dict[str, Any]] = []
        for line in lines:
            if line == header_line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != len(headers):
                continue
            record = dict(zip(headers, parts))
            records.append(record)

        return records

    # ------------------------------------------------------------------
    # CLINICAL NOTES / UNSTRUCTURED TEXT
    # ------------------------------------------------------------------
    def _extract_clinical_notes(self, text: str) -> List[Dict[str, Any]]:
        """
        Very simple clinical note segmentation:
        - Split by blank lines
        - Each block becomes a "note section"
        """
        sections: List[Dict[str, Any]] = []
        current_lines: List[str] = []

        def flush():
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    sections.append(
                        {
                            "section_text": content,
                            "length": len(content),
                        }
                    )

        for line in text.split("\n"):
            if line.strip():
                current_lines.append(line)
            else:
                flush()
                current_lines = []
        flush()

        return sections

