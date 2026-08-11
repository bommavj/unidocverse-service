"""
Universal Tax Form Analyzer
Handles: 1099-B, 1099-DIV, 1099-INT, W-2, 1040, Schedule D, and any tax document
Extracts: transactions, gains/losses, income, withholdings, wash sales

Follows the exact same pattern as GenericAnalyzer / BankStatementAnalyzer:
- Inherits BaseAnalyzer + TabularLoaderMixin
- Uses _load_dataframe_universal() for data loading
- Uses custom build_tax_ui_config() for rich charts/tables/sections
"""
import re
import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional

from app.parsers.extractors.w2_extractor import W2Extractor
from app.parsers.extractors.w2_intelligence import W2Intelligence
from app.services.analyzers.base_analyzer import BaseAnalyzer
from app.services.mixins.tabular_loader_mixin import TabularLoaderMixin

logger = logging.getLogger(__name__)


class TaxFormAnalyzer(BaseAnalyzer, TabularLoaderMixin):
    """
    Universal Tax Form Analyzer.

    Supported forms:
    - 1099-B    broker transactions, capital gains/losses, wash sales
    - 1099-DIV  dividends and distributions
    - 1099-INT  interest income
    - 1099-MISC miscellaneous income
    - 1099-NEC  nonemployee compensation
    - W-2       wages and withholdings (full advisory analysis via W2Intelligence)
    - 1040      individual tax return
    - Schedule D capital gains summary
    """

    # =========================================================================
    # COLUMN DETECTION PATTERNS
    # =========================================================================
    COLUMN_PATTERNS = {
        "date_sold": [
            r"date\s*sold", r"date\s*dispos", r"1c\b", r"sale\s*date",
            r"close\s*date", r"disposal\s*date",
        ],
        "date_acquired": [
            r"date\s*acqui", r"date\s*purch", r"1b\b",
            r"purchase\s*date", r"open\s*date", r"acquisition",
        ],
        "description": [
            r"description", r"security", r"asset", r"symbol",
            r"stock", r"property", r"cusip", r"1a\b", r"instrument",
        ],
        "quantity": [
            r"quantity", r"shares", r"units", r"qty",
            r"number.*shares", r"amount.*sold",
        ],
        "proceeds": [
            r"proceeds", r"sales\s*price", r"gross\s*proceeds",
            r"\b1d\b", r"sale\s*price", r"selling\s*price",
        ],
        "cost_basis": [
            r"cost\s*basis", r"cost\s*or\s*basis", r"adjusted\s*basis",
            r"\b1e\b", r"purchase\s*price", r"original\s*cost",
        ],
        "gain_loss": [
            r"gain\s*or\s*loss", r"net\s*gain", r"gain.loss",
            r"realized\s*gain", r"profit.*loss", r"p&l",
        ],
        "wash_sale": [
            r"wash\s*sale", r"loss\s*disallowed", r"disallowed",
            r"\b1g\b",
        ],
        "term": [
            r"^term$", r"short.*long", r"holding\s*period",
            r"short.term", r"long.term",
        ],
        "income": [
            r"^income$", r"wages", r"salary", r"earnings",
            r"compensation", r"box\s*1\b",
        ],
        "withholding": [
            r"withheld", r"federal.*tax", r"tax.*withheld", r"box\s*2\b",
        ],
        "dividends": [
            r"dividend", r"ordinary.*div", r"qualified.*div",
        ],
        "interest": [
            r"^interest$", r"interest\s*income",
        ],
        "amount": [
            r"^amount$", r"total\s*amount", r"^value$",
        ],
    }

    FORM_PATTERNS = {
        "1099-B":     [r"1099.b", r"proceeds.*broker", r"barter.*exchange"],
        "1099-DIV":   [r"1099.div", r"dividends.*distributions"],
        "1099-INT":   [r"1099.int", r"interest\s*income"],
        "1099-MISC":  [r"1099.misc", r"miscellaneous\s*income"],
        "1099-NEC":   [r"1099.nec", r"nonemployee\s*compensation"],
        "W-2":        [r"\bw.2\b", r"wages.*tips.*compensation"],
        "1040":       [r"\b1040\b", r"individual.*income.*tax.*return"],
        "Schedule-D": [r"schedule\s*d", r"capital\s*gains.*losses"],
    }

    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================

    def analyze(
        self,
        file_path: str,
        text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        parsed: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logger.info(f"🧾 TaxFormAnalyzer: {file_path}")

        try:
            form_type = self._detect_form_type(text, file_path)
            logger.info(f"📋 Detected tax form type: {form_type}")

            if self._is_w2_form(form_type):
                raw_tables = self._get_raw_tables(parsed)
                result = self._analyze_w2(text, raw_tables)
                result["ui_config"] = self.build_tax_ui_config(result)
                result["has_advanced_analytics"] = True
                logger.info(
                    f"✅ W-2 UI config: "
                    f"{len(result['ui_config']['hero_metrics'])} metrics, "
                    f"{len(result['ui_config']['charts'])} charts"
                )
                return result

            df = self._load_dataframe_universal(
                file_path=file_path,
                parsed=parsed,
                allow_pdf_tables=True,
            )

            if df is not None and not df.empty:
                df = self._map_columns(df)
                df = self._clean_numeric_columns(df)
                df = self._clean_date_columns(df)
                logger.info(f"📊 DataFrame ready: {len(df)} rows × {len(df.columns)} cols")
            else:
                df = pd.DataFrame()
                logger.warning("⚠️ No tabular data found, using text-only analysis")

            if form_type in ("1099-DIV", "1099-INT"):
                result = self._analyze_income_form(df, text, form_type)
            elif form_type in ("1099-B", "Schedule-D") or self._has_transaction_cols(df):
                result = self._analyze_1099b(df, text, form_type)
            else:
                result = self._analyze_generic_tax(df, text, form_type)

            result["ui_config"] = self.build_tax_ui_config(result)
            result["has_advanced_analytics"] = True

            logger.info(
                f"✅ Tax UI config: "
                f"{len(result['ui_config']['hero_metrics'])} metrics, "
                f"{len(result['ui_config']['charts'])} charts, "
                f"{len(result['ui_config']['tables'])} tables, "
                f"{len(result['ui_config']['sections'])} sections"
            )
            return result

        except Exception as e:
            logger.error(f"❌ TaxFormAnalyzer failed: {e}", exc_info=True)
            return self._empty_result(str(e))

    # =========================================================================
    # RAW TABLE HELPER
    # =========================================================================

    def _get_raw_tables(self, parsed: Optional[Dict[str, Any]]) -> List:
        if not parsed:
            return []
        for key in ("tables", "raw_tables", "pdf_tables"):
            tables = parsed.get(key)
            if tables and isinstance(tables, list):
                return tables
        content = parsed.get("content", {})
        if isinstance(content, dict):
            for key in ("tables", "raw_tables"):
                tables = content.get(key)
                if tables and isinstance(tables, list):
                    return tables
        return []

    # =========================================================================
    # FORM TYPE DETECTION
    # =========================================================================

    def _detect_form_type(self, text: str, file_path: str) -> str:
        combined = ((text or "") + " " + file_path).lower()
        for form_type, patterns in self.FORM_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, combined, re.IGNORECASE):
                    return form_type
        if any(kw in combined for kw in ["proceeds", "cost basis", "gain", "loss"]):
            return "1099-B"
        if any(kw in combined for kw in ["wages", "federal income tax withheld"]):
            return "W-2"
        if "dividend" in combined:
            return "1099-DIV"
        if "interest income" in combined:
            return "1099-INT"
        return "tax_form"

    # =========================================================================
    # COLUMN MAPPING
    # =========================================================================

    def _map_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        header_row_idx = self._find_header_row(df)
        if header_row_idx is not None:
            df.columns = [str(v).strip() for v in df.iloc[header_row_idx].tolist()]
            df = df.iloc[header_row_idx + 1:].reset_index(drop=True)

        mapping = {}
        used = set()
        for col in df.columns:
            sem = self._find_semantic(str(col).lower().strip())
            if sem and sem not in used:
                mapping[col] = sem
                used.add(sem)

        if mapping:
            df = df.rename(columns=mapping)
            return df

        df = self._infer_columns_from_data(df)
        return df

    def _infer_columns_from_data(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df.columns) < 3:
            return df
        cols = list(df.columns)
        ncols = len(cols)
        mapping = {}
        date_pattern   = re.compile(r'^\d{1,2}/\d{1,2}/\d{2,4}$')
        amount_pattern = re.compile(r'^-?[\d,]+\.?\d{0,2}$')
        date_cols = []
        amount_cols = []
        text_cols = []
        for col in cols:
            sample = df[col].dropna().astype(str).str.strip().head(20).tolist()
            if not sample:
                continue
            total = len(sample)
            date_hits   = sum(1 for v in sample if date_pattern.match(v))
            amount_hits = sum(1 for v in sample if amount_pattern.match(v.replace(',', '')))
            text_hits   = sum(1 for v in sample if len(v) > 3 and not amount_pattern.match(v.replace(',', '')))
            if date_hits / total > 0.4:
                date_cols.append((col, date_hits))
            elif amount_hits / total > 0.4:
                amount_cols.append((col, amount_hits))
            elif text_hits / total > 0.4:
                text_cols.append((col, text_hits))

        date_cols.sort(key=lambda x: int(str(x[0])) if str(x[0]).isdigit() else 99)
        if len(date_cols) >= 1:
            mapping[date_cols[0][0]] = "date_acquired"
        if len(date_cols) >= 2:
            mapping[date_cols[1][0]] = "date_sold"
        if text_cols:
            text_cols.sort(key=lambda x: int(str(x[0])) if str(x[0]).isdigit() else 99)
            mapping[text_cols[0][0]] = "description"
        amount_names = ["proceeds", "cost_basis", "gain_loss", "wash_sale"]
        amount_cols.sort(key=lambda x: int(str(x[0])) if str(x[0]).isdigit() else 99)
        for i, (col, _) in enumerate(amount_cols):
            if col not in mapping and i < len(amount_names):
                mapping[col] = amount_names[i]

        if mapping:
            df = df.rename(columns=mapping)
        else:
            standard = ["date_acquired", "date_sold", "description", "quantity",
                        "proceeds", "cost_basis", "gain_loss", "wash_sale"]
            pos_mapping = {cols[i]: standard[i] for i in range(min(ncols, len(standard)))}
            df = df.rename(columns=pos_mapping)
        return df

    def _find_header_row(self, df: pd.DataFrame) -> Optional[int]:
        best_score = 0
        best_idx = None
        for idx in range(min(15, len(df))):
            row = df.iloc[idx].astype(str).tolist()
            score = sum(1 for v in row if self._find_semantic(str(v).lower().strip()) is not None)
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx if best_score >= 3 else None

    def _find_semantic(self, col_name: str) -> Optional[str]:
        for semantic, patterns in self.COLUMN_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, col_name, re.IGNORECASE):
                    return semantic
        return None

    def _has_transaction_cols(self, df: pd.DataFrame) -> bool:
        if df is None or df.empty:
            return False
        cols = [str(c).lower() for c in df.columns]
        return any(kw in cols for kw in ["proceeds", "cost_basis", "gain_loss"])

    # =========================================================================
    # DATA CLEANING
    # =========================================================================

    def _clean_numeric_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        numeric_targets = {
            "proceeds", "cost_basis", "gain_loss", "wash_sale",
            "quantity", "amount", "income", "withholding", "dividends", "interest",
        }
        for col in df.columns:
            if str(col).lower() in numeric_targets:
                try:
                    cleaned = (
                        df[col].astype(str)
                        .str.replace(r'[\$,\s]', '', regex=True)
                        .str.replace(r'\((.+)\)', r'-\1', regex=True)
                        .str.replace(r'[^\d\.\-]', '', regex=True)
                        .replace('', np.nan)
                    )
                    converted = pd.to_numeric(cleaned, errors='coerce')
                    if converted.notna().sum() > len(df) * 0.2:
                        df[col] = converted
                except Exception:
                    pass
        return df

    def _clean_date_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        date_targets = {"date_sold", "date_acquired"}
        for col in df.columns:
            if str(col).lower() in date_targets:
                try:
                    converted = pd.to_datetime(df[col], errors='coerce', infer_datetime_format=True)
                    if converted.notna().sum() > len(df) * 0.2:
                        df[col] = converted
                except Exception:
                    pass
        return df

    # =========================================================================
    # W-2 ANALYSIS  (primary path — full advisory report)
    # =========================================================================

    def _analyze_w2(self, raw_text: str, raw_tables: List) -> Dict[str, Any]:
        logger.info("📊 Running W-2 analysis with intelligence layer")

        extractor = W2Extractor()
        data = extractor.extract(raw_text or "", raw_tables)
        d = data.to_dict()

        # Run full intelligence analysis
        intel_engine = W2Intelligence()
        intel = intel_engine.analyze(data)
        intel_dict = intel.to_dict()

        logger.info(f"  W-2 confidence: {data.extraction_confidence:.0%}")
        logger.info(f"  W-2 intelligence: {len(intel.insights)} insights, {len(intel.recommendations)} recs")
        if data.missing_critical_fields:
            logger.warning(f"  W-2 missing: {data.missing_critical_fields}")

        # ── Chart data ────────────────────────────────────────────────────

        # Income breakdown bar chart
        income_chart = []
        if data.wages_tips_compensation:
            income_chart.append({"label": "Wages (Box 1)",        "value": data.wages_tips_compensation})
        if data.federal_income_tax_withheld:
            income_chart.append({"label": "Federal Tax (Box 2)",  "value": data.federal_income_tax_withheld})
        if data.state_income_tax:
            income_chart.append({"label": "State Tax (Box 17)",   "value": data.state_income_tax})
        if data.total_fica_withheld:
            income_chart.append({"label": "FICA (SS + Medicare)", "value": data.total_fica_withheld})

        # FICA breakdown
        fica_chart = []
        if data.social_security_tax_withheld:
            fica_chart.append({"label": "SS Tax (Box 4)",         "value": data.social_security_tax_withheld})
        if data.medicare_tax_withheld:
            fica_chart.append({"label": "Medicare Tax (Box 6)",   "value": data.medicare_tax_withheld})

        # Box 12 chart
        box12_chart = [
            {"label": f"[{item['code']}] {item['description'][:30]}", "value": item["amount"]}
            for item in d.get("box_12_items", [])
        ]

        # Withholding vs. estimated liability gauge data
        we = intel_dict.get("withholding_estimate", {})
        withholding_comparison_chart = []
        if we.get("withheld") and we.get("estimated_liability"):
            withholding_comparison_chart = [
                {"label": "Withheld",           "value": we["withheld"]},
                {"label": "Est. Liability",     "value": we["estimated_liability"]},
            ]

        # Retirement headroom chart
        ret = intel_dict.get("retirement", {})
        retirement_chart = []
        if ret.get("total_contributed") and ret.get("annual_limit"):
            retirement_chart = [
                {"label": f"{ret.get('plan_type', 'Retirement')} Contributed", "value": ret["total_contributed"]},
                {"label": "IRS Limit",                                          "value": ret["annual_limit"]},
            ]

        # Wages breakdown (Box 1 vs Box 3 pre-tax gap)
        pretax_gap = intel_dict.get("pretax_gap")
        wages_breakdown_chart = []
        if pretax_gap and pretax_gap.get("gap", 0) > 50:
            wages_breakdown_chart = [
                {"label": "Taxable Wages (Box 1)",    "value": pretax_gap["box1_wages"]},
                {"label": "SS Wages (Box 3)",         "value": pretax_gap["box3_ss_wages"]},
                {"label": "Pre-Tax Benefits (Gap)",   "value": pretax_gap["gap"]},
            ]

        # ── Combine insights: extractor + intelligence ────────────────────
        all_insights = list(intel.insights)  # intelligence insights first (most actionable)

        dr = d.get("derived_rates", {})
        if dr.get("ss_wage_cap_hit"):
            cap = dr.get("effective_ss_cap")
            all_insights.append(
                f"📊 SS wage cap hit at ${cap:,.2f} — wages above this aren't subject to SS tax."
            )
        if dr.get("additional_medicare_applied"):
            rate = dr.get("medicare_effective_rate", 0)
            all_insights.append(
                f"⚕️ Additional Medicare Tax (0.9%) applied — effective Medicare rate {rate:.2%}."
            )
        if data.effective_state_tax_rate:
            all_insights.append(
                f"🏛️ Effective state withholding rate: {data.effective_state_tax_rate:.2f}% ({data.state})."
            )
        for warning in data.validation.warnings[:3]:
            all_insights.append(f"⚠️ {warning}")
        for flag in data.validation.flags[:3]:
            all_insights.append(f"ℹ️ {flag}")

        # ── Combine recommendations ────────────────────────────────────────
        all_recommendations = list(intel.recommendations)

        # Add extraction-quality recommendations
        if data.validation.errors:
            for err in data.validation.errors:
                all_recommendations.append({
                    "title": "Extraction Error",
                    "category": "data_quality",
                    "priority": "high",
                    "description": err,
                })
        if data.missing_critical_fields:
            all_recommendations.append({
                "title": "Missing W-2 Fields",
                "category": "data_quality",
                "priority": "high",
                "description": (
                    f"Could not extract: {', '.join(data.missing_critical_fields)}. "
                    "Document quality may be low — try a clearer scan."
                ),
            })
        if not all_recommendations:
            all_recommendations.append({
                "title": "W-2 Looks Good",
                "category": "general",
                "priority": "low",
                "description": "No issues detected. Verify all boxes match your pay stubs.",
            })

        return {
            # Identity
            "type":            "tax_form",
            "form_type":       d.get("form_type", "W-2"),
            "tax_year":        d.get("tax_year"),
            "dataset_purpose": "w2_wages",
            "is_corrected":    data.is_corrected,

            # Employer / Employee
            "employer_name":    d.get("employer_name"),
            "employer_ein":     d.get("employer_ein"),
            "employer_address": d.get("employer_address"),
            "employee_name":    d.get("employee_name"),
            "employee_ssn":     d.get("employee_ssn"),
            "employee_address": d.get("employee_address"),

            # Core box values
            "total_wages":              d.get("wages_tips_compensation"),
            "federal_tax_withheld":     d.get("federal_income_tax_withheld"),
            "social_security_wages":    d.get("social_security_wages"),
            "social_security_tax":      d.get("social_security_tax_withheld"),
            "medicare_wages":           d.get("medicare_wages_tips"),
            "medicare_tax":             d.get("medicare_tax_withheld"),
            "state_tax_withheld":       d.get("state_income_tax"),
            "state_wages":              d.get("state_wages"),
            "state":                    d.get("state"),
            "dependent_care_benefits":  d.get("dependent_care_benefits"),
            "nonqualified_plans":       d.get("nonqualified_plans"),
            "allocated_tips":           d.get("allocated_tips"),
            "social_security_tips":     d.get("social_security_tips"),

            # Derived
            "effective_tax_rate":       d.get("effective_federal_tax_rate"),
            "effective_state_tax_rate": d.get("effective_state_tax_rate"),
            "total_fica_withheld":      d.get("total_fica_withheld"),
            "total_taxes_withheld":     d.get("total_taxes_withheld"),

            # Flags
            "retirement_plan":      d.get("retirement_plan"),
            "statutory_employee":   d.get("statutory_employee"),
            "third_party_sick_pay": d.get("third_party_sick_pay"),

            # Box 12 / 14
            "box_12_items": d.get("box_12_items", []),
            "box_14_items": d.get("box_14_items", []),

            # State / local
            "state_entries": d.get("state_entries", []),
            "local_entries": d.get("local_entries", []),

            # Derived rates
            "derived_rates": d.get("derived_rates", {}),

            # ── Intelligence layer outputs ──
            "intelligence": intel_dict,

            # Advisory metrics (surfaced at top level for UI config)
            "marginal_tax_rate":        intel_dict.get("bracket", {}).get("marginal_rate"),
            "withholding_direction":    intel_dict.get("withholding_estimate", {}).get("direction"),
            "withholding_delta":        intel_dict.get("withholding_estimate", {}).get("delta"),
            "estimated_liability":      intel_dict.get("withholding_estimate", {}).get("estimated_liability"),
            "retirement_contributed":   intel_dict.get("retirement", {}).get("total_contributed"),
            "retirement_limit":         intel_dict.get("retirement", {}).get("annual_limit"),
            "retirement_headroom":      intel_dict.get("retirement", {}).get("headroom"),
            "retirement_plan_type":     intel_dict.get("retirement", {}).get("plan_type"),
            "hsa_employer_contribution": intel_dict.get("hsa", {}).get("employer_contribution") if intel_dict.get("hsa") else None,
            "hsa_individual_limit":     intel_dict.get("hsa", {}).get("individual_limit") if intel_dict.get("hsa") else None,
            "hsa_headroom":             intel_dict.get("hsa", {}).get("headroom_individual") if intel_dict.get("hsa") else None,
            "eitc_potentially_eligible": intel_dict.get("eitc", {}).get("potentially_eligible"),
            "additional_medicare_exposure": intel_dict.get("additional_medicare_exposure"),
            "w4_action":                intel_dict.get("w4_guidance", {}).get("action"),
            "w4_amount_per_paycheck":   intel_dict.get("w4_guidance", {}).get("amount_per_paycheck"),

            # Chart data
            "income_breakdown":           income_chart,
            "fica_breakdown":             fica_chart,
            "box12_breakdown":            box12_chart,
            "withholding_comparison":     withholding_comparison_chart,
            "retirement_comparison":      retirement_chart,
            "wages_breakdown":            wages_breakdown_chart,

            # Form 1040 map table
            "form_1040_map":              intel_dict.get("form_1040_map", []),

            # Filing checklist
            "filing_checklist":           intel_dict.get("filing_checklist", []),

            # Narratives
            "insight_narratives": all_insights,
            "recommendations":    all_recommendations,

            # Extraction metadata
            "extraction_confidence":    d.get("extraction_confidence"),
            "missing_critical_fields":  d.get("missing_critical_fields", []),
            "validation":               d.get("validation", {}),

            "total_rows":            1,
            "total_columns":         20,
            "data_completeness_pct": round(d.get("extraction_confidence", 0) * 100, 1),

            "summary": self._summary_w2(data, intel),
        }

    def _summary_w2(self, data, intel=None) -> str:
        parts = []
        if data.employee_name:
            parts.append(f"W-2 for {data.employee_name}")
        if data.tax_year:
            parts.append(f"tax year {data.tax_year}")
        if data.employer_name:
            parts.append(f"from {data.employer_name}")
        header = " ".join(parts) + "." if parts else "W-2 Wage Statement."

        detail_parts = []
        if data.wages_tips_compensation:
            detail_parts.append(f"Wages: ${data.wages_tips_compensation:,.2f}")
        if data.federal_income_tax_withheld:
            detail_parts.append(f"federal tax withheld: ${data.federal_income_tax_withheld:,.2f}")
        if data.effective_federal_tax_rate:
            detail_parts.append(f"effective rate {data.effective_federal_tax_rate:.1f}%")
        if intel:
            we = intel.withholding_estimate
            if we.direction == "refund":
                detail_parts.append(f"estimated refund ~${abs(we.delta):,.0f}")
            elif we.direction == "owe":
                detail_parts.append(f"estimated owed ~${abs(we.delta):,.0f}")
        detail = " | ".join(detail_parts) + "." if detail_parts else ""
        return f"{header} {detail}".strip()

    # =========================================================================
    # 1099-B ANALYSIS
    # =========================================================================

    def _analyze_1099b(self, df: pd.DataFrame, text: str, form_type: str) -> Dict[str, Any]:
        logger.info("📊 Running 1099-B analysis")
        if df.empty:
            return self._text_only_result(text, form_type)

        cols = list(df.columns)
        total_rows = len(df)
        proceeds_col = self._col(df, "proceeds")
        cost_col     = self._col(df, "cost_basis")
        gl_col       = self._col(df, "gain_loss")
        wash_col     = self._col(df, "wash_sale")
        desc_col     = self._col(df, "description")
        term_col     = self._col(df, "term")
        date_col     = self._col(df, "date_sold") or self._col(df, "date_acquired")

        total_proceeds = self._sum(df, proceeds_col)
        total_cost     = self._sum(df, cost_col)

        if gl_col is None and proceeds_col and cost_col:
            df = df.copy()
            df["gain_loss"] = (
                pd.to_numeric(df[proceeds_col], errors="coerce").fillna(0)
                - pd.to_numeric(df[cost_col],   errors="coerce").fillna(0)
            )
            gl_col = "gain_loss"
            cols   = list(df.columns)

        total_gl   = self._sum(df, gl_col)
        total_wash = self._sum(df, wash_col)
        wash_count = int(df[wash_col].notna().sum()) if wash_col else 0

        if gl_col:
            gains_mask  = df[gl_col] > 0
            losses_mask = df[gl_col] < 0
            total_gains  = float(df.loc[gains_mask,  gl_col].sum()) if gains_mask.any()  else 0.0
            total_losses = float(df.loc[losses_mask, gl_col].sum()) if losses_mask.any() else 0.0
            gain_count   = int(gains_mask.sum())
            loss_count   = int(losses_mask.sum())
        else:
            total_gains = total_losses = 0.0
            gain_count  = loss_count  = 0

        short_gl = long_gl = 0.0
        short_count = long_count = 0
        if term_col and gl_col:
            s_mask = df[term_col].astype(str).str.contains("short", case=False, na=False)
            l_mask = df[term_col].astype(str).str.contains("long",  case=False, na=False)
            short_count = int(s_mask.sum())
            long_count  = int(l_mask.sum())
            short_gl = float(df.loc[s_mask, gl_col].sum()) if s_mask.any() else 0.0
            long_gl  = float(df.loc[l_mask, gl_col].sum()) if l_mask.any() else 0.0

        top_securities = []
        if desc_col and gl_col:
            grp = df.groupby(desc_col)[gl_col].agg(["sum", "count"]).reset_index()
            grp.columns = ["security", "total_gl", "trades"]
            grp = grp.sort_values("total_gl")
            for _, row in grp[grp["total_gl"] > 0].tail(5).iterrows():
                top_securities.append({"security": str(row["security"]), "gain_loss": round(float(row["total_gl"]), 2), "trades": int(row["trades"]), "type": "gain"})
            for _, row in grp[grp["total_gl"] < 0].head(5).iterrows():
                top_securities.append({"security": str(row["security"]), "gain_loss": round(float(row["total_gl"]), 2), "trades": int(row["trades"]), "type": "loss"})

        unique_securities = int(df[desc_col].nunique()) if desc_col else 0
        monthly_activity = []
        if date_col and gl_col:
            try:
                df = df.copy()
                df["_month"] = pd.to_datetime(df[date_col], errors="coerce").dt.to_period("M")
                monthly = df.groupby("_month")[gl_col].agg(["sum", "count"]).reset_index()
                monthly.columns = ["month", "gain_loss", "trades"]
                monthly_activity = [{"month": str(r["month"]), "gain_loss": round(float(r["gain_loss"]), 2), "trades": int(r["trades"])} for _, r in monthly.iterrows()]
                df.drop(columns=["_month"], inplace=True, errors="ignore")
            except Exception as e:
                logger.warning(f"⚠️ Monthly activity failed: {e}")

        completeness = round((df.notna().sum().sum() / (total_rows * len(cols))) * 100, 2) if total_rows > 0 else 0.0
        numeric_cols   = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
        date_cols_list = [c for c in cols if pd.api.types.is_datetime64_any_dtype(df[c])]

        return {
            "type": "tax_form", "form_type": form_type, "dataset_purpose": "tax_transactions",
            "total_transactions": total_rows, "total_proceeds": round(total_proceeds, 2),
            "total_cost_basis": round(total_cost, 2), "total_gain_loss": round(total_gl, 2),
            "total_gains": round(total_gains, 2), "total_losses": round(total_losses, 2),
            "net_taxable_gain_loss": round(total_gl - total_wash, 2),
            "total_wash_sales": round(total_wash, 2), "wash_sale_count": wash_count,
            "gain_count": gain_count, "loss_count": loss_count,
            "unique_securities": unique_securities,
            "short_term_count": short_count, "long_term_count": long_count,
            "short_term_gain_loss": round(short_gl, 2), "long_term_gain_loss": round(long_gl, 2),
            "gain_loss_breakdown": [{"label": "Total Gains", "value": round(total_gains, 2)}, {"label": "Total Losses", "value": round(abs(total_losses), 2)}, {"label": "Wash Sales", "value": round(total_wash, 2)}],
            "gain_loss_by_security": [{"label": s["security"], "value": s["gain_loss"]} for s in top_securities],
            "monthly_gain_loss": [{"label": m["month"], "value": m["gain_loss"]} for m in monthly_activity],
            "term_breakdown": [{"label": "Short Term", "value": short_count}, {"label": "Long Term", "value": long_count}],
            "top_securities": top_securities, "monthly_activity": monthly_activity,
            "recommendations": self._1099b_recommendations(total_gl, total_wash, wash_count, short_gl, long_gl),
            "total_rows": total_rows, "total_columns": len(cols),
            "data_completeness_pct": completeness, "numeric_columns": numeric_cols,
            "date_columns": date_cols_list,
            "text_columns": [c for c in cols if c not in numeric_cols + date_cols_list],
            "summary": self._summary_1099b(total_rows, total_proceeds, total_cost, total_gl, total_wash, form_type),
            "insight_narratives": self._insights_1099b(total_gl, total_gains, total_losses, total_wash, short_gl, long_gl, gain_count, loss_count),
        }

    # =========================================================================
    # 1099-DIV / 1099-INT ANALYSIS
    # =========================================================================

    def _analyze_income_form(self, df: pd.DataFrame, text: str, form_type: str) -> Dict[str, Any]:
        logger.info(f"📊 Running {form_type} analysis")
        total_income = 0.0
        if not df.empty:
            for col_name in ["dividends", "interest", "income", "amount"]:
                col = self._col(df, col_name)
                if col:
                    total_income = self._sum(df, col)
                    break
        if total_income == 0.0:
            total_income = self._extract_amount(text, r"(?:total|box\s*1)[:\s\$]+([\d,]+\.?\d*)") or 0.0
        return {
            "type": "tax_form", "form_type": form_type,
            "dataset_purpose": f"tax_{form_type.lower().replace('-','_')}",
            "total_income": round(total_income, 2),
            "total_rows": len(df) if not df.empty else 1,
            "total_columns": len(df.columns) if not df.empty else 1,
            "data_completeness_pct": 100.0,
            "gain_loss_breakdown": [{"label": "Total Income", "value": round(total_income, 2)}],
            "recommendations": [],
            "summary": f"{form_type} shows ${total_income:,.2f} in income.",
            "insight_narratives": [f"Total {form_type} income: ${total_income:,.2f}"],
        }

    # =========================================================================
    # GENERIC TAX FORM FALLBACK
    # =========================================================================

    def _analyze_generic_tax(self, df: pd.DataFrame, text: str, form_type: str) -> Dict[str, Any]:
        logger.info(f"📊 Running generic tax analysis for {form_type}")
        total_rows = len(df) if not df.empty else 0
        total_cols = len(df.columns) if not df.empty else 0
        completeness = round((df.notna().sum().sum() / (total_rows * total_cols)) * 100, 2) if total_rows > 0 and total_cols > 0 else 0.0
        return {
            "type": "tax_form", "form_type": form_type, "dataset_purpose": "tax_form",
            "total_rows": total_rows, "total_columns": total_cols,
            "data_completeness_pct": completeness,
            "recommendations": [],
            "summary": f"{form_type} tax document with {total_rows} records.",
            "insight_narratives": [f"Document type: {form_type}", f"Records found: {total_rows}"],
        }

    # =========================================================================
    # UI CONFIG  (upgraded for W-2 advisory sections)
    # =========================================================================

    def _is_w2_form(self, form_type: str) -> bool:
        return form_type.startswith("W-2")

    def build_tax_ui_config(self, result: Dict[str, Any]) -> Dict[str, Any]:
        form_type = result.get("form_type", "tax_form")

        # ── Hero metrics ──────────────────────────────────────────────────
        if self._is_w2_form(form_type):
            hero_metrics = [
                # Primary financial facts
                {"key": "total_wages",          "label": "Total Wages (Box 1)",       "value_path": "total_wages",          "is_hero": True,  "format": "currency"},
                {"key": "federal_tax_withheld", "label": "Federal Tax Withheld",       "value_path": "federal_tax_withheld", "is_hero": True,  "format": "currency"},
                {"key": "effective_tax_rate",   "label": "Effective Withholding Rate", "value_path": "effective_tax_rate",   "is_hero": True,  "format": "percent"},
                {"key": "marginal_tax_rate",    "label": "Marginal Bracket",           "value_path": "marginal_tax_rate",    "is_hero": True,  "format": "percent"},
                # Withholding advisory
                {"key": "estimated_liability",  "label": "Est. Tax Liability",         "value_path": "estimated_liability",  "is_hero": False, "format": "currency"},
                {"key": "withholding_delta",    "label": "Est. Refund (+) / Owe (-)",  "value_path": "withholding_delta",    "is_hero": False, "format": "currency"},
                # State
                {"key": "state_tax_withheld",   "label": "State Tax Withheld",         "value_path": "state_tax_withheld",   "is_hero": False, "format": "currency"},
                # FICA
                {"key": "social_security_tax",  "label": "SS Tax (Box 4)",             "value_path": "social_security_tax",  "is_hero": False, "format": "currency"},
                {"key": "medicare_tax",         "label": "Medicare Tax (Box 6)",        "value_path": "medicare_tax",         "is_hero": False, "format": "currency"},
                {"key": "total_fica_withheld",  "label": "Total FICA",                  "value_path": "total_fica_withheld",  "is_hero": False, "format": "currency"},
                {"key": "total_taxes_withheld", "label": "Total All Taxes Withheld",    "value_path": "total_taxes_withheld", "is_hero": False, "format": "currency"},
                # Retirement
                {"key": "retirement_contributed", "label": "Retirement Contributed",   "value_path": "retirement_contributed", "is_hero": False, "format": "currency"},
                {"key": "retirement_headroom",    "label": "Retirement Headroom",       "value_path": "retirement_headroom",    "is_hero": False, "format": "currency"},
                # HSA
                {"key": "hsa_employer_contribution", "label": "HSA (Employer, Code W)", "value_path": "hsa_employer_contribution", "is_hero": False, "format": "currency"},
                {"key": "hsa_headroom",              "label": "HSA Contribution Room",   "value_path": "hsa_headroom",              "is_hero": False, "format": "currency"},
                # Meta
                {"key": "data_completeness_pct", "label": "Extraction Confidence (%)", "value_path": "data_completeness_pct", "is_hero": False, "format": "percent"},
            ]
        elif form_type in ("1099-DIV", "1099-INT"):
            hero_metrics = [
                {"key": "total_income",          "label": "Total Income",           "value_path": "total_income",          "is_hero": True},
                {"key": "data_completeness_pct", "label": "Data Completeness (%)",  "value_path": "data_completeness_pct", "is_hero": False},
            ]
        else:
            hero_metrics = [
                {"key": "total_transactions",    "label": "Total Transactions",    "value_path": "total_transactions",    "is_hero": True},
                {"key": "total_proceeds",        "label": "Total Proceeds",        "value_path": "total_proceeds",        "is_hero": True},
                {"key": "total_gain_loss",       "label": "Net Gain / Loss",       "value_path": "total_gain_loss",       "is_hero": True},
                {"key": "net_taxable_gain_loss", "label": "Net Taxable Gain/Loss", "value_path": "net_taxable_gain_loss", "is_hero": True},
                {"key": "total_cost_basis",      "label": "Total Cost Basis",      "value_path": "total_cost_basis",      "is_hero": False},
                {"key": "total_gains",           "label": "Total Gains",           "value_path": "total_gains",           "is_hero": False},
                {"key": "total_losses",          "label": "Total Losses",          "value_path": "total_losses",          "is_hero": False},
                {"key": "gain_count",            "label": "Winning Trades",        "value_path": "gain_count",            "is_hero": False},
                {"key": "loss_count",            "label": "Losing Trades",         "value_path": "loss_count",            "is_hero": False},
                {"key": "unique_securities",     "label": "Unique Securities",     "value_path": "unique_securities",     "is_hero": False},
                {"key": "total_wash_sales",      "label": "Wash Sales Disallowed", "value_path": "total_wash_sales",      "is_hero": False},
                {"key": "short_term_gain_loss",  "label": "Short-Term Gain/Loss",  "value_path": "short_term_gain_loss",  "is_hero": False},
                {"key": "long_term_gain_loss",   "label": "Long-Term Gain/Loss",   "value_path": "long_term_gain_loss",   "is_hero": False},
                {"key": "data_completeness_pct", "label": "Data Completeness (%)", "value_path": "data_completeness_pct", "is_hero": False},
            ]

        # ── Charts ────────────────────────────────────────────────────────
        charts = []

        if self._is_w2_form(form_type):
            if result.get("income_breakdown"):
                charts.append({"id": "income_breakdown", "type": "bar",
                    "title": "W-2 Income & Tax Breakdown",
                    "data_path": "income_breakdown", "label_field": "label", "value_field": "value"})
            if result.get("fica_breakdown"):
                charts.append({"id": "fica_breakdown", "type": "bar",
                    "title": "FICA Withholdings (SS + Medicare)",
                    "data_path": "fica_breakdown", "label_field": "label", "value_field": "value"})
            if result.get("withholding_comparison"):
                charts.append({"id": "withholding_comparison", "type": "bar",
                    "title": "Withholding vs. Estimated Tax Liability",
                    "data_path": "withholding_comparison", "label_field": "label", "value_field": "value",
                    "note": "Approximate — assumes standard deduction, single filer, no credits."})
            if result.get("retirement_comparison"):
                charts.append({"id": "retirement_comparison", "type": "bar",
                    "title": f"Retirement Contributions vs IRS Limit",
                    "data_path": "retirement_comparison", "label_field": "label", "value_field": "value"})
            if result.get("wages_breakdown"):
                charts.append({"id": "wages_breakdown", "type": "bar",
                    "title": "Wages Breakdown — Taxable vs. SS vs. Pre-Tax Benefits",
                    "data_path": "wages_breakdown", "label_field": "label", "value_field": "value"})
            if result.get("box12_breakdown"):
                charts.append({"id": "box12_breakdown", "type": "bar",
                    "title": "Box 12 — Deferred Compensation & Benefits",
                    "data_path": "box12_breakdown", "label_field": "label", "value_field": "value"})
        else:
            if result.get("gain_loss_breakdown"):
                charts.append({"id": "gain_loss_breakdown", "type": "bar",
                    "title": "Gains vs Losses vs Wash Sales",
                    "data_path": "gain_loss_breakdown", "label_field": "label", "value_field": "value"})
            if result.get("gain_loss_by_security"):
                charts.append({"id": "gain_loss_by_security", "type": "bar",
                    "title": "Top Securities by Gain / Loss",
                    "data_path": "gain_loss_by_security", "label_field": "label", "value_field": "value"})
            if result.get("monthly_gain_loss"):
                charts.append({"id": "monthly_gain_loss", "type": "line",
                    "title": "Monthly Gain / Loss",
                    "data_path": "monthly_gain_loss", "label_field": "label", "value_field": "value"})
            if result.get("term_breakdown") and (result.get("short_term_count", 0) + result.get("long_term_count", 0)) > 0:
                charts.append({"id": "term_breakdown", "type": "pie",
                    "title": "Short-Term vs Long-Term Trades",
                    "data_path": "term_breakdown", "label_field": "label", "value_field": "value"})

        # ── Tables ────────────────────────────────────────────────────────
        tables = []
        chart_ids = {c["id"] for c in charts}

        if self._is_w2_form(form_type):
            if result.get("form_1040_map"):
                tables.append({"id": "form_1040_map", "name": "form_1040_map",
                    "title": "Form 1040 Filing Map — Where Each Box Goes",
                    "data_path": "form_1040_map",
                    "columns": [
                        {"key": "w2_box",         "label": "W-2 Box"},
                        {"key": "value",          "label": "Amount"},
                        {"key": "form_1040_line", "label": "Form 1040 Line"},
                        {"key": "description",    "label": "Description"},
                    ]})
            if result.get("box_12_items"):
                tables.append({"id": "box12_items", "name": "box12_items",
                    "title": "Box 12 — Deferred Compensation & Benefits Decoded",
                    "data_path": "box_12_items",
                    "columns": [
                        {"key": "code",        "label": "Code"},
                        {"key": "amount",      "label": "Amount"},
                        {"key": "description", "label": "Description"},
                    ]})
            if result.get("box_14_items"):
                tables.append({"id": "box14_items", "name": "box14_items",
                    "title": "Box 14 — Additional Information",
                    "data_path": "box_14_items"})
            if result.get("filing_checklist"):
                tables.append({"id": "filing_checklist", "name": "filing_checklist",
                    "title": "Filing Preparation Checklist",
                    "data_path": "filing_checklist",
                    "list_mode": True})
            if result.get("recommendations"):
                tables.append({"id": "recommendations", "name": "recommendations",
                    "title": "Tax Recommendations & Action Items",
                    "data_path": "recommendations",
                    "columns": [
                        {"key": "title",       "label": "Action"},
                        {"key": "priority",    "label": "Priority"},
                        {"key": "description", "label": "Details"},
                    ]})
        else:
            if result.get("top_securities"):
                tables.append({"id": "top_securities", "name": "top_securities",
                    "title": "Top Securities by Gain / Loss", "data_path": "top_securities"})
            if result.get("monthly_activity"):
                tables.append({"id": "monthly_activity", "name": "monthly_activity",
                    "title": "Monthly Trading Activity", "data_path": "monthly_activity"})
            if result.get("recommendations"):
                tables.append({"id": "recommendations", "name": "recommendations",
                    "title": "Tax Recommendations", "data_path": "recommendations"})

        table_ids = {t["id"] for t in tables}

        # ── Sections ──────────────────────────────────────────────────────
        sections = []

        if self._is_w2_form(form_type):
            # ── Section 1: W-2 Summary (hero numbers)
            sections.append({
                "id": "w2_overview", "title": "W-2 Summary",
                "metrics": ["total_wages", "federal_tax_withheld", "effective_tax_rate", "marginal_tax_rate"],
                "charts": [], "tables": [],
            })

            # ── Section 2: Withholding Analysis (refund/owe projection)
            sections.append({
                "id": "withholding_analysis", "title": "Withholding Analysis — Refund or Owe?",
                "metrics": ["federal_tax_withheld", "estimated_liability", "withholding_delta"],
                "charts": ["withholding_comparison"] if "withholding_comparison" in chart_ids else [],
                "tables": [],
                "note": (
                    result.get("intelligence", {}).get("withholding_estimate", {}).get("note", "")
                ),
            })

            # ── Section 3: Income & FICA
            sections.append({
                "id": "w2_income", "title": "Income & Tax Withholdings",
                "metrics": ["total_taxes_withheld", "total_fica_withheld",
                            "social_security_tax", "medicare_tax", "state_tax_withheld"],
                "charts": [c for c in ["income_breakdown", "fica_breakdown", "wages_breakdown"] if c in chart_ids],
                "tables": [],
            })

            # ── Section 4: Retirement (only if data present)
            if result.get("retirement_contributed") or result.get("retirement_plan"):
                sections.append({
                    "id": "retirement_analysis", "title": "Retirement Contributions",
                    "metrics": ["retirement_contributed", "retirement_headroom"],
                    "charts": ["retirement_comparison"] if "retirement_comparison" in chart_ids else [],
                    "tables": ["box12_items"] if "box12_items" in table_ids else [],
                    "note": result.get("intelligence", {}).get("retirement", {}).get("note", ""),
                })

            # ── Section 5: Benefits & HSA (if Code W or Box 12 present)
            has_benefits = result.get("hsa_employer_contribution") or result.get("box12_breakdown")
            if has_benefits:
                hsa_note = ""
                if result.get("intelligence", {}).get("hsa"):
                    hsa_note = result["intelligence"]["hsa"].get("note", "")
                sections.append({
                    "id": "benefits_analysis", "title": "Benefits & HSA",
                    "metrics": ["hsa_employer_contribution", "hsa_headroom"],
                    "charts": ["box12_breakdown"] if "box12_breakdown" in chart_ids else [],
                    "tables": [],
                    "note": hsa_note,
                })

            # ── Section 6: Filing Guidance
            sections.append({
                "id": "filing_guidance", "title": "Filing Guidance & Recommendations",
                "metrics": [],
                "charts": [],
                "tables": [t for t in ["form_1040_map", "filing_checklist", "recommendations"] if t in table_ids],
            })

            # ── Section 7: W-4 Guidance (if action needed)
            w4_action = result.get("w4_action", "no_change")
            if w4_action and w4_action != "no_change":
                sections.append({
                    "id": "w4_guidance", "title": "W-4 Withholding Adjustment",
                    "metrics": ["w4_amount_per_paycheck"] if result.get("w4_amount_per_paycheck") else [],
                    "charts": [],
                    "tables": [],
                    "note": result.get("intelligence", {}).get("w4_guidance", {}).get("reason", ""),
                })

            # ── Section 8: Extraction Metadata
            sections.append({
                "id": "w2_metadata", "title": "Extraction Quality",
                "metrics": ["data_completeness_pct"],
                "charts": [],
                "tables": [],
            })

        else:
            # 1099-B / generic sections (unchanged from original)
            sections.append({
                "id": "overview",
                "title": f"{form_type} Summary",
                "metrics": [m["key"] for m in hero_metrics if m["is_hero"]],
                "charts": [], "tables": [],
            })
            perf_charts = [c for c in ["gain_loss_breakdown", "gain_loss_by_security"] if c in chart_ids]
            perf_tables = [t for t in ["top_securities"] if t in table_ids]
            if perf_charts or perf_tables:
                sections.append({
                    "id": "performance", "title": "Trading Performance",
                    "metrics": ["total_gains", "total_losses", "gain_count", "loss_count", "unique_securities"],
                    "charts": perf_charts, "tables": perf_tables,
                })
            if "monthly_gain_loss" in chart_ids or "monthly_activity" in table_ids:
                sections.append({
                    "id": "timeline", "title": "Monthly Activity",
                    "metrics": [],
                    "charts": ["monthly_gain_loss"] if "monthly_gain_loss" in chart_ids else [],
                    "tables": ["monthly_activity"] if "monthly_activity" in table_ids else [],
                })
            if "term_breakdown" in chart_ids:
                sections.append({
                    "id": "tax_details", "title": "Tax Details",
                    "metrics": ["total_wash_sales", "short_term_gain_loss", "long_term_gain_loss"],
                    "charts": ["term_breakdown"],
                    "tables": ["recommendations"] if "recommendations" in table_ids else [],
                })

        return {
            "hero_metrics": hero_metrics,
            "charts": charts,
            "tables": tables,
            "sections": sections,
        }

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _col(self, df: pd.DataFrame, name: str) -> Optional[str]:
        for col in df.columns:
            if str(col).lower() == name:
                return col
        for col in df.columns:
            if name in str(col).lower():
                return col
        return None

    def _sum(self, df: pd.DataFrame, col: Optional[str]) -> float:
        if not col or df.empty or col not in df.columns:
            return 0.0
        return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

    def _extract_amount(self, text: str, pattern: str) -> Optional[float]:
        if not text:
            return None
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except Exception:
                pass
        return None

    def _summary_1099b(self, rows, proceeds, cost, gl, wash, form_type) -> str:
        direction = "gain" if gl >= 0 else "loss"
        wash_str = f" Wash sale loss disallowed: ${wash:,.2f}." if wash else ""
        return (f"This {form_type} contains {rows:,} transactions. "
                f"Total proceeds: ${proceeds:,.2f}, cost basis: ${cost:,.2f}. "
                f"Net {direction}: ${abs(gl):,.2f}.{wash_str}")

    def _insights_1099b(self, total_gl, total_gains, total_losses, wash_sales, short_gl, long_gl, gain_count, loss_count) -> List[str]:
        insights = []
        if total_gl >= 0:
            insights.append(f"📈 Net capital gain of ${total_gl:,.2f} across {gain_count + loss_count} transactions.")
        else:
            insights.append(f"📉 Net capital loss of ${abs(total_gl):,.2f} — may offset other capital gains.")
        if total_gains > 0:
            insights.append(f"✅ {gain_count} profitable trades totaling ${total_gains:,.2f}.")
        if total_losses < 0:
            insights.append(f"⚠️ {loss_count} losing trades totaling ${abs(total_losses):,.2f}.")
        if wash_sales > 0:
            insights.append(f"🚫 ${wash_sales:,.2f} in wash sale losses disallowed — added back to cost basis of replacement shares.")
        if short_gl != 0:
            insights.append(f"⏱️ Short-term gain/loss: ${short_gl:,.2f} (taxed as ordinary income).")
        if long_gl != 0:
            insights.append(f"📅 Long-term gain/loss: ${long_gl:,.2f} (taxed at preferential rates).")
        return insights

    def _1099b_recommendations(self, total_gl, wash_sales, wash_count, short_gl, long_gl) -> List[Dict]:
        recs = []
        if wash_sales > 0:
            recs.append({"title": "Wash Sale Violations Detected", "category": "tax_compliance", "priority": "high",
                "description": f"${wash_sales:,.2f} in losses were disallowed due to wash sale rules ({wash_count} instances). Avoid repurchasing substantially identical securities within 30 days of a loss sale."})
        if short_gl < 0 and long_gl > 0:
            recs.append({"title": "Tax Loss Harvesting Opportunity", "category": "tax_optimization", "priority": "medium",
                "description": f"Short-term losses (${abs(short_gl):,.2f}) can offset long-term gains (${long_gl:,.2f})."})
        if total_gl > 1000:
            recs.append({"title": "Capital Gains Tax Liability", "category": "tax_planning", "priority": "medium",
                "description": f"Net capital gain of ${total_gl:,.2f} may result in tax liability. Consider quarterly estimated tax payments."})
        if not recs:
            recs.append({"title": "No Critical Tax Issues", "category": "general", "priority": "low",
                "description": "Tax form appears complete with no major compliance concerns."})
        return recs

    def _text_only_result(self, text: str, form_type: str) -> Dict[str, Any]:
        return {"type": "tax_form", "form_type": form_type, "dataset_purpose": "tax_form",
                "total_rows": 0, "total_columns": 0, "data_completeness_pct": 0.0,
                "recommendations": [], "gain_loss_breakdown": [],
                "summary": f"{form_type} processed as text only — no tabular data extracted.",
                "insight_narratives": ["Document processed as text only."]}

    def _empty_result(self, error: str = "") -> Dict[str, Any]:
        return {"type": "tax_form", "form_type": "unknown", "dataset_purpose": "tax_form",
                "total_rows": 0, "total_columns": 0, "data_completeness_pct": 0.0,
                "recommendations": [], "gain_loss_breakdown": [],
                "summary": "Analysis failed.",
                "insight_narratives": [f"Error: {error}" if error else "Analysis failed."],
                "has_advanced_analytics": False}