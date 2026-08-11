"""
W-2 / W-2c Wage and Tax Statement Extractor
=============================================
Production-ready extractor for UniDocVerse.

Handles:
  - Standard W-2 (single value per box)
  - W-2c Corrected W-2 (two columns: "Previously reported" | "Correct information")
    → always extracts the LAST (rightmost) value = the corrected amount

Design principles:
  - Derive rates from document math (Box4÷Box3, Box6÷Box5) — no hardcoded rates
  - SS_WAGE_LIMITS table used only for Box1==Box3 disambiguation edge case
  - Validate internal consistency (Box N vs Box M), not doc vs constant
  - SSN masked immediately, never stored raw
  - Text-first, table scan as last resort
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal IRS reference data — only what the document cannot tell you
# ---------------------------------------------------------------------------

SS_WAGE_LIMITS: Dict[int, float] = {
    2010: 106_800.00, 2011: 106_800.00, 2012: 110_100.00, 2013: 113_700.00,
    2014: 117_000.00, 2015: 118_500.00, 2016: 118_500.00, 2017: 127_200.00,
    2018: 128_400.00, 2019: 132_900.00, 2020: 137_700.00, 2021: 142_800.00,
    2022: 147_000.00, 2023: 160_200.00, 2024: 168_600.00, 2025: 176_100.00,
}

KNOWN_SS_RATES: List[float] = [0.062, 0.042]   # 6.2% standard; 4.2% for 2011–2012
KNOWN_MEDICARE_BASE_RATE = 0.0145

ADDITIONAL_MEDICARE_THRESHOLD = 200_000.00
ADDITIONAL_MEDICARE_RATE = 0.009
ADDITIONAL_MEDICARE_START_YEAR = 2013


def get_ss_wage_limit(tax_year: Optional[int]) -> Optional[float]:
    if tax_year is None:
        return None
    return SS_WAGE_LIMITS.get(tax_year)


# ---------------------------------------------------------------------------
# US state data
# ---------------------------------------------------------------------------

US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC","PR","VI","GU","AS","MP",
}

NO_STATE_TAX_STATES = {"AK","FL","NV","NH","SD","TN","TX","WA","WY"}

_LABEL_NOISE_CODES = {"ST","TE","ER","AT","RS","NU","MB","LO","YE","TA","MP"}


# ---------------------------------------------------------------------------
# Box 12 codes
# ---------------------------------------------------------------------------

BOX_12_DESCRIPTIONS: Dict[str, str] = {
    "A":  "Uncollected Social Security tax on tips",
    "B":  "Uncollected Medicare tax on tips",
    "C":  "Taxable cost of group-term life insurance over $50,000",
    "D":  "401(k) elective deferrals",
    "E":  "403(b) elective deferrals",
    "F":  "408(k)(6) SEP elective deferrals",
    "G":  "457(b) deferred compensation",
    "H":  "501(c)(18)(D) plan deferrals",
    "J":  "Nontaxable sick pay",
    "K":  "20% excise tax on excess golden parachute payments",
    "L":  "Substantiated employee business expense reimbursements",
    "M":  "Uncollected SS tax on group-term life insurance",
    "N":  "Uncollected Medicare tax on group-term life insurance",
    "P":  "Excludable moving expense reimbursements",
    "Q":  "Nontaxable combat pay",
    "R":  "Employer Archer MSA contributions",
    "S":  "408(p) SIMPLE employee salary reduction contributions",
    "T":  "Adoption benefits",
    "V":  "Income from nonstatutory stock options",
    "W":  "Employer HSA contributions",
    "Y":  "409A nonqualified deferred compensation deferrals",
    "Z":  "409A nonqualified deferred compensation income (failed plan)",
    "AA": "Roth 401(k) designated contributions",
    "BB": "Roth 403(b) designated contributions",
    "DD": "Cost of employer-sponsored health coverage",
    "EE": "Roth 457(b) governmental plan contributions",
    "FF": "Qualified small employer HRA permitted benefits",
    "GG": "Income from qualified equity grants (§83(i))",
    "HH": "Aggregate §83(i) election deferrals",
}

RETIREMENT_CODES = {"D","E","F","G","H","S","AA","BB","EE"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Box12Item:
    code: str
    amount: float
    description: str


@dataclass
class Box14Item:
    label: str
    amount: float


@dataclass
class StateEntry:
    state: Optional[str] = None
    employer_state_id: Optional[str] = None
    wages: Optional[float] = None
    income_tax: Optional[float] = None


@dataclass
class LocalEntry:
    locality_name: Optional[str] = None
    wages: Optional[float] = None
    income_tax: Optional[float] = None


@dataclass
class DerivedRates:
    ss_tax_rate: Optional[float] = None
    medicare_effective_rate: Optional[float] = None
    ss_wage_cap_hit: bool = False
    effective_ss_cap: Optional[float] = None
    additional_medicare_applied: bool = False
    known_cap_consulted: Optional[float] = None


@dataclass
class ValidationResult:
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)


@dataclass
class W2Data:
    document_type: str = "w2"
    form_type: str = "W-2"
    tax_year: Optional[int] = None
    extraction_timestamp: str = field(
        default_factory=lambda: datetime.utcnow().isoformat())
    extraction_confidence: float = 0.0
    missing_critical_fields: List[str] = field(default_factory=list)

    employer_name: Optional[str] = None
    employer_ein: Optional[str] = None
    employer_address: Optional[str] = None
    control_number: Optional[str] = None

    employee_name: Optional[str] = None
    employee_ssn_masked: Optional[str] = None
    employee_address: Optional[str] = None

    wages_tips_compensation: Optional[float] = None
    federal_income_tax_withheld: Optional[float] = None

    social_security_wages: Optional[float] = None
    social_security_tax_withheld: Optional[float] = None
    social_security_tips: Optional[float] = None
    allocated_tips: Optional[float] = None

    medicare_wages_tips: Optional[float] = None
    medicare_tax_withheld: Optional[float] = None

    dependent_care_benefits: Optional[float] = None
    nonqualified_plans: Optional[float] = None
    box_12_items: List[Box12Item] = field(default_factory=list)
    statutory_employee: bool = False
    retirement_plan: bool = False
    third_party_sick_pay: bool = False
    box_14_items: List[Box14Item] = field(default_factory=list)

    state_entries: List[StateEntry] = field(default_factory=list)
    local_entries: List[LocalEntry] = field(default_factory=list)

    derived_rates: DerivedRates = field(default_factory=DerivedRates)

    total_fica_withheld: Optional[float] = None
    total_taxes_withheld: Optional[float] = None
    effective_federal_tax_rate: Optional[float] = None
    effective_state_tax_rate: Optional[float] = None

    validation: ValidationResult = field(default_factory=ValidationResult)

    # W-2c specific — correction metadata
    is_corrected: bool = False

    @property
    def total_wages(self) -> Optional[float]:
        return self.wages_tips_compensation

    @property
    def federal_tax_withheld(self) -> Optional[float]:
        return self.federal_income_tax_withheld

    @property
    def state(self) -> Optional[str]:
        return self.state_entries[0].state if self.state_entries else None

    @property
    def state_wages(self) -> Optional[float]:
        return self.state_entries[0].wages if self.state_entries else None

    @property
    def state_income_tax(self) -> Optional[float]:
        return self.state_entries[0].income_tax if self.state_entries else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_type": self.document_type,
            "form_type": self.form_type,
            "is_corrected": self.is_corrected,
            "tax_year": self.tax_year,
            "extraction_timestamp": self.extraction_timestamp,
            "extraction_confidence": self.extraction_confidence,
            "missing_critical_fields": self.missing_critical_fields,

            "employer_name": self.employer_name,
            "employer_ein": self.employer_ein,
            "employer_address": self.employer_address,
            "control_number": self.control_number,

            "employee_name": self.employee_name,
            "employee_ssn": self.employee_ssn_masked,
            "employee_address": self.employee_address,

            "wages_tips_compensation": self.wages_tips_compensation,
            "federal_income_tax_withheld": self.federal_income_tax_withheld,
            "social_security_wages": self.social_security_wages,
            "social_security_tax_withheld": self.social_security_tax_withheld,
            "medicare_wages_tips": self.medicare_wages_tips,
            "medicare_tax_withheld": self.medicare_tax_withheld,
            "social_security_tips": self.social_security_tips,
            "allocated_tips": self.allocated_tips,
            "dependent_care_benefits": self.dependent_care_benefits,
            "nonqualified_plans": self.nonqualified_plans,

            "box_12_items": [
                {"code": b.code, "amount": b.amount, "description": b.description}
                for b in self.box_12_items
            ],
            "statutory_employee": self.statutory_employee,
            "retirement_plan": self.retirement_plan,
            "third_party_sick_pay": self.third_party_sick_pay,
            "box_14_items": [
                {"label": b.label, "amount": b.amount}
                for b in self.box_14_items
            ],

            "state_entries": [
                {"state": s.state, "employer_state_id": s.employer_state_id,
                 "wages": s.wages, "income_tax": s.income_tax}
                for s in self.state_entries
            ],
            "local_entries": [
                {"locality_name": l.locality_name, "wages": l.wages,
                 "income_tax": l.income_tax}
                for l in self.local_entries
            ],

            "total_wages": self.total_wages,
            "state": self.state,
            "state_wages": self.state_wages,
            "state_income_tax": self.state_income_tax,

            "derived_rates": {
                "ss_tax_rate": self.derived_rates.ss_tax_rate,
                "medicare_effective_rate": self.derived_rates.medicare_effective_rate,
                "ss_wage_cap_hit": self.derived_rates.ss_wage_cap_hit,
                "effective_ss_cap": self.derived_rates.effective_ss_cap,
                "additional_medicare_applied": self.derived_rates.additional_medicare_applied,
                "known_cap_consulted": self.derived_rates.known_cap_consulted,
            },

            "total_fica_withheld": self.total_fica_withheld,
            "total_taxes_withheld": self.total_taxes_withheld,
            "effective_federal_tax_rate": self.effective_federal_tax_rate,
            "effective_state_tax_rate": self.effective_state_tax_rate,

            "validation": {
                "is_valid": self.validation.is_valid,
                "errors": self.validation.errors,
                "warnings": self.validation.warnings,
                "flags": self.validation.flags,
            },
        }


# ---------------------------------------------------------------------------
# Core extractor
# ---------------------------------------------------------------------------

class W2Extractor:
    """
    W-2 and W-2c extractor — text-first, table fallback.

    W-2c handling:
        A corrected W-2 has TWO value columns per row:
            "Previously reported"  |  "Correct information"
        The extractor detects this layout and always takes the LAST
        (rightmost) numeric value on each box line — the corrected amount.
        This is the legally correct value to use.

    Usage:
        data: W2Data = W2Extractor().extract(raw_text, tables)
        result = data.to_dict()
    """

    _STRIP_RE = re.compile(r'[\$,\s]')
    _PAREN_RE = re.compile(r'^\((.+)\)$')

    # Regex to find ALL dollar amounts on a single line
    _AMT_ALL_RE = re.compile(r'([\d,]+\.?\d{0,2})(?![-\d])')

    def _preprocess_text(self, text: str) -> str:
        """
        Normalise W-2/W-2c text before box extraction.

        Some PDF renderers (especially IRS fillable forms) place the box
        number on its own line immediately before the label text, e.g.:

            2
            Federal income tax withheld
            1,040.00

        This method joins such pairs back into the standard single-line format:

            2 Federal income tax withheld
            1,040.00

        so that all downstream S1/S2 strategies — which expect "box_num label"
        on the same line — can match them correctly.
        """
        # Box number (1-20) on its own line, followed immediately by a
        # recognised W-2 label-starting keyword on the next line.
        LABEL_STARTS = (
            r"Wages|Federal|Social|Medicare|Allocated|Dependent|"
            r"Nonqualified|Non-qualified|State|Local|Advance"
        )
        text = re.sub(
            rf"^(\d{{1,2}})\n((?:{LABEL_STARTS})\b)",
            r"\1 \2",
            text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        return text

    def _parse_amount(self, raw: str) -> Optional[float]:
        """Parse a dollar string to float. W-2 boxes are never negative."""
        if not raw:
            return None
        cleaned = self._STRIP_RE.sub('', str(raw).strip())
        if self._PAREN_RE.match(cleaned):
            return None
        try:
            val = float(cleaned)
        except ValueError:
            return None
        return val if val >= 0 else None

    def _parse_dollar(self, raw: str) -> Optional[float]:
        """
        Parse a dollar amount, filtering out W-2 box numbers (1–20).
        A token is a box number if it has no decimal point AND its value <= 20.
        This prevents 'box 3' or 'box 6' from being captured as dollar amounts
        when scanning multi-line W-2c text.
        """
        if not raw:
            return None
        cleaned = self._STRIP_RE.sub('', str(raw).strip())
        if self._PAREN_RE.match(cleaned):
            return None
        try:
            val = float(cleaned)
        except ValueError:
            return None
        if val < 0:
            return None
        # Filter out bare box numbers (no decimal, value 1–20)
        if '.' not in str(raw).strip() and val <= 20:
            return None
        return val

    def _mask_ssn(self, raw: str) -> str:
        digits = re.sub(r'\D', '', raw)
        return f"***-**-{digits[-4:]}" if len(digits) >= 4 else "***-**-****"

    def _normalize(self, text: str) -> str:
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'\|{2,}', ' ', text)
        for src, dst in {
            'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬀ': 'ff', 'ﬃ': 'ffi', 'ﬄ': 'ffl',
            '\u200b': '', '\xa0': ' ',
        }.items():
            text = text.replace(src, dst)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text

    # ── W-2c detection ──────────────────────────────────────────────────────

    def _is_corrected(self, text: str) -> bool:
        """True if this is a W-2c (corrected wage statement)."""
        return bool(re.search(
            r'W-?2c\b|corrected\s+wage|previously\s+reported|correct\s+information',
            text, re.IGNORECASE
        ))

    # ── Amount extraction: single vs. two-column ───────────────────────────

    def _extract_amounts_from_line(self, line: str) -> List[float]:
        """Return all valid dollar amounts found on a single text line."""
        results = []
        for m in self._AMT_ALL_RE.finditer(line):
            v = self._parse_amount(m.group(1))
            if v is not None and v >= 0:
                results.append(v)
        return results

    def _pick_amount(self, amounts: List[float], is_corrected: bool) -> Optional[float]:
        """
        Given a list of amounts found on one line:
        - Standard W-2:  take the first (only) value
        - W-2c:          take the LAST value = "Correct information" column
        """
        if not amounts:
            return None
        return amounts[-1] if is_corrected else amounts[0]

    # ── Generic box extractor ──────────────────────────────────────────────

    def _extract_box(
        self,
        text: str,
        box_num: str,
        labels: List[str],
        tables: List,
        is_corrected: bool = False,
    ) -> Optional[float]:
        """
        7-strategy extractor for a single W-2 box.

        W-2c two-column handling:
          Standard W-2:  one value per row  → take first amount found
          W-2c same-line: "2.00  52,000.00" → take last amount on line
          W-2c multi-line: each amount on its own line below the label
                           → scan up to 5 lines, stop at next box label,
                             take last valid dollar amount (= corrected value)
          W-2c label-split: label wraps across lines or box# separated from label
                           → S2b tries partial label (first 3 words)
                           → S2c tries label-only match (no box number)
        """
        box = rf'(?<!\d){re.escape(box_num)}(?!\d)'

        # ── S1: same line ─────────────────────────────────────────────────
        for lbl in labels:
            m = re.search(
                rf'{box}\s+{re.escape(lbl)}([^\n]+)',
                text, re.IGNORECASE
            )
            if m:
                amounts = self._extract_amounts_from_line(m.group(1))
                v = self._pick_amount(amounts, is_corrected)
                if v is not None:
                    logger.debug(f"Box {box_num} S1 match: {v}")
                    return v

        # ── S2: box+label line → multi-line value scan ────────────────────
        # Handles W-2c PDFs where each amount is on its own line:
        #   4 Social security tax withheld
        #   330.00          ← previously reported
        #   3,224.00        ← correct information  ← we take this (last)
        for lbl in labels:
            m = re.search(
                rf'{box}\s+{re.escape(lbl)}[^\n]*\n((?:[^\n]*\n?){{0,5}})',
                text, re.IGNORECASE
            )
            if m:
                v = self._scan_subsequent_lines(m.group(1), is_corrected)
                if v is not None:
                    logger.debug(f"Box {box_num} S2 match: {v}")
                    return v

        # ── S2b: partial label (first 3 words) + multi-line scan ──────────
        # Handles abbreviated/truncated labels and label line wraps:
        #   "2 Federal income tax"  (label continues on next line as "withheld")
        #   "4 Social security tax" (label continues as "withheld")
        for lbl in labels:
            words = lbl.split()[:3]
            if len(words) >= 2:
                partial = r'\s+'.join(re.escape(w) for w in words)
                m = re.search(
                    rf'{box}\s+{partial}[^\n]*\n((?:[^\n]*\n?){{0,5}})',
                    text, re.IGNORECASE
                )
                if m:
                    v = self._scan_subsequent_lines(m.group(1), is_corrected)
                    if v is not None:
                        logger.debug(f"Box {box_num} S2b partial label match: {v}")
                        return v

        # ── S2c: label-only multi-line scan (no box number required) ─────
        # Handles layouts where box number is far from label text,
        # or where OCR separates them across regions
        for lbl in labels:
            m = re.search(
                rf'{re.escape(lbl)}[^\n]*\n((?:[^\n]*\n?){{0,5}})',
                text, re.IGNORECASE
            )
            if m:
                v = self._scan_subsequent_lines(m.group(1), is_corrected)
                if v is not None:
                    logger.debug(f"Box {box_num} S2c label-only match: {v}")
                    return v

        # ── S3: label proximity — scan the following 150 chars ───────────
        for lbl in labels:
            m = re.search(
                rf'{re.escape(lbl)}([^\n]{{0,150}})',
                text, re.IGNORECASE
            )
            if m:
                amounts = self._extract_amounts_from_line(m.group(1))
                v = self._pick_amount(amounts, is_corrected)
                if v is not None:
                    logger.debug(f"Box {box_num} S3 proximity match: {v}")
                    return v

        # ── S4: box number proximity (garbled/OCR label) ─────────────────
        m = re.search(
            rf'{box}([^\d\n]{{0,80}}[\d,]+\.?\d{{0,2}}[^\n]*)',
            text, re.IGNORECASE
        )
        if m:
            amounts = self._extract_amounts_from_line(m.group(1))
            v = self._pick_amount(amounts, is_corrected)
            if v is not None:
                logger.debug(f"Box {box_num} S4 box-proximity match: {v}")
                return v

        # ── S5: table fallback ────────────────────────────────────────────
        v = self._scan_tables(tables, box_num, labels) if tables else None
        if v is not None:
            logger.debug(f"Box {box_num} S5 table match: {v}")
        else:
            logger.warning(f"Box {box_num} UNMATCHED (all strategies failed, labels={labels})")
        return v

    def _extract_box_after_anchor(self, text: str, anchor_value: float) -> Optional[float]:
        """
        S6 fallback for ADP-style multi-copy W-2s where all values are stored
        in a dense block with no adjacent labels (labels are in a separate PDF region).

        Strategy: find the anchor value (e.g. Box 1 wages) in the text, then
        return the next valid dollar amount — which is always Box N+1 in these PDFs.

        Only fires when all label-based strategies have already failed.
        """
        # Format the anchor as it would appear in the text (with or without comma)
        anchor_str = f"{anchor_value:.2f}"
        anchor_no_comma = anchor_str
        anchor_with_comma = f"{anchor_value:,.2f}"

        # Find all occurrences of the anchor value, use the first one
        pos = -1
        for fmt in (anchor_no_comma, anchor_with_comma):
            idx = text.find(fmt)
            if idx != -1:
                pos = idx + len(fmt)
                break

        if pos == -1:
            return None

        # Scan forward from the anchor for the next dollar amount
        # Skip whitespace and newlines, stop after 200 chars
        remaining = text[pos:pos + 200]
        for m in re.finditer(r'[\d,]+\.?\d{0,2}', remaining):
            v = self._parse_dollar(m.group())
            if v is not None and abs(v - anchor_value) > 0.01:  # must differ from anchor
                return v
        return None

    def _extract_values_block(self, text: str) -> Optional[Dict]:
        """
        S7 rate-based holistic extraction for ADP and other payroll-processor W-2s
        where labels and values are in separate PDF regions (no label proximity).

        Uses IRS tax rates as invariants to identify boxes:
          Box3 + Box4: SS tax rate = 6.20% exactly
          Box5 + Box6: Medicare rate = 1.45% exactly
          Box1: largest wage amount not already identified
          Box2: next remaining amount (federal income tax withheld)

        This is layout-independent and works regardless of how PyMuPDF orders the text.
        """
        import itertools

        # Collect all plausible W-2 dollar amounts from the document
        amounts = []
        for m in re.finditer(r"(?<![\d-])([\d,]+\.\d{2})(?![\d-])", text):
            v = self._parse_dollar(m.group(1))
            if v is not None and 100 < v < 10_000_000:
                amounts.append(v)

        if len(amounts) < 4:
            return None

        unique = list(set(amounts))
        result: Dict = {}

        # ── Step 1: find Box3 + Box4 via SS rate (6.2%) ──────────────────
        ss_pair = None
        for a, b in itertools.permutations(unique, 2):
            if a > 1000 and abs(b / a - 0.062) < 0.0005:
                ss_pair = (a, b)
                break
        if ss_pair:
            result["social_security_wages"] = ss_pair[0]
            result["social_security_tax_withheld"] = ss_pair[1]

        # ── Step 2: find Box5 + Box6 via Medicare rate (1.45%) ───────────
        med_pair = None
        for a, b in itertools.permutations(unique, 2):
            if a > 1000 and abs(b / a - 0.0145) < 0.0002:
                med_pair = (a, b)
                break
        if med_pair:
            result["medicare_wages_tips"] = med_pair[0]
            result["medicare_tax_withheld"] = med_pair[1]

        if not ss_pair and not med_pair:
            return None  # no rates found; not an ADP-style block

        # ── Step 3: Box1 = largest amount not already assigned ───────────
        assigned = set(result.values())
        wage_candidates = sorted(
            [v for v in unique if v not in assigned], reverse=True
        )
        if wage_candidates:
            result["wages_tips_compensation"] = wage_candidates[0]
            assigned.add(wage_candidates[0])

        # ── Step 4: Box2 = next largest unassigned (fed income tax) ──────
        remaining = sorted([v for v in unique if v not in assigned], reverse=True)
        if remaining:
            result["federal_income_tax_withheld"] = remaining[0]

        logger.info(f"S7 rate-based extraction: {result}")
        return result if len(result) >= 4 else None


    def _scan_subsequent_lines(
        self, subsequent: str, is_corrected: bool, max_lines: int = 5
    ) -> Optional[float]:
        """
        Scan the lines immediately after a box label match.

        Stops when:
          - Next box label is reached (digit followed by a letter)
          - Line contains more alphabetic chars than digits (employer/employee info)
          - max_lines reached

        Returns the last valid dollar amount (W-2c corrected) or first (standard W-2).
        """
        amounts = []
        for line in subsequent.split('\n')[:max_lines]:
            line = line.strip()
            if not line:
                continue
            # Stop at next box label: "3 Social security wages", "c Employer name"
            if re.match(r'\d{1,2}\s+[A-Za-z]', line):
                break
            # Stop at lines dominated by letters (employer name, address, SSN label)
            # A pure amount line has very few letters; text lines have many.
            letter_count = sum(1 for c in line if c.isalpha())
            digit_count  = sum(1 for c in line if c.isdigit())
            if letter_count > digit_count and letter_count > 2:
                break
            for tok in re.findall(r'[\d,]+\.?\d{0,2}(?![-\d])', line):
                v = self._parse_dollar(tok)
                if v is not None:
                    amounts.append(v)
        if not amounts:
            return None
        return amounts[-1] if is_corrected else amounts[0]

    def _scan_tables(
        self, tables: List, box_num: str, labels: List[str]
    ) -> Optional[float]:
        box = rf'(?<!\d){re.escape(box_num)}(?!\d)'
        for table in tables:
            for row in table:
                cells = [str(c).strip() if c else '' for c in row]
                if len(cells) < 2:
                    continue
                for i, cell in enumerate(cells[:-1]):
                    if bool(re.search(box, cell)) or any(
                        lbl.lower() in cell.lower() for lbl in labels
                    ):
                        for j in range(i + 1, min(i + 3, len(cells))):
                            v = self._parse_amount(cells[j])
                            if v is not None:
                                return v
        return None

    # ── Main entry point ───────────────────────────────────────────────────

    def extract(
        self,
        raw_text: str,
        tables: Optional[List] = None,
    ) -> W2Data:
        tables = tables or []
        text = self._normalize(raw_text or "")
        text = self._preprocess_text(text)   # join split box-num/label lines
        data = W2Data()

        # Detect form variant first — governs all subsequent extraction
        corrected = self._is_corrected(text)
        data.is_corrected = corrected
        if corrected:
            data.form_type = "W-2c"
            logger.info("W2Extractor: W-2c (corrected) layout detected — using last-value strategy")
        else:
            logger.info(f"W2Extractor: standard W-2 layout")

        logger.info(f"W2Extractor: text={len(text)} chars, tables={len(tables)}")

        self._extract_meta(text, data)
        self._extract_employer(text, data)
        self._extract_employee(text, data)
        self._extract_federal(text, tables, data, corrected)
        self._extract_fica(text, tables, data, corrected)
        self._extract_additional(text, tables, data, corrected)
        self._extract_box12(text, data)
        self._extract_box13(text, data)
        self._extract_box14(text, data)
        self._extract_state(text, tables, data, corrected)
        self._extract_local(text, tables, data, corrected)
        # S7: rate-based holistic extraction for ADP/payroll-processor W-2s.
        # Fires when any critical field is missing OR has an implausibly small value.
        # W-2c previously-reported values (e.g. 2.00, 330.00) can slip through S1-S5
        # because they appear adjacent to the label — we must detect and override them.
        #
        # Minimum plausible values per box (conservative — catches W-2c prev-reported):
        MIN_WAGES   = 1_000.0   # Box1: wages always > $1k for any real employee
        MIN_FED_TAX = 100.0     # Box2: fed withheld always > $100 on $1k+ wages
        MIN_SS_WAGES = 1_000.0  # Box3: SS wages always mirrors Box1 magnitude
        MIN_SS_TAX   = 60.0     # Box4: SS tax = 6.2% × Box3, so > $62 on $1k wages
        MIN_MED_TAX  = 14.0     # Box6: Medicare = 1.45% × Box5, > $14.5 on $1k wages

        def _implausible(field, minval):
            v = getattr(data, field)
            return v is not None and v < minval

        bad = (
            _implausible('wages_tips_compensation',      MIN_WAGES)   or
            _implausible('federal_income_tax_withheld',  MIN_FED_TAX) or
            _implausible('social_security_wages',        MIN_SS_WAGES) or
            _implausible('social_security_tax_withheld', MIN_SS_TAX)  or
            _implausible('medicare_tax_withheld',        MIN_MED_TAX)
        )
        missing_critical = [
            f for f in ['wages_tips_compensation', 'federal_income_tax_withheld',
                        'social_security_wages', 'social_security_tax_withheld']
            if getattr(data, f) is None
        ]

        if missing_critical or bad:
            block = self._extract_values_block(text)
            if block:
                logger.info(f"S7 rate-based filled: {list(block.keys())}")
                thresholds = {
                    'wages_tips_compensation':      MIN_WAGES,
                    'federal_income_tax_withheld':  MIN_FED_TAX,
                    'social_security_wages':        MIN_SS_WAGES,
                    'social_security_tax_withheld': MIN_SS_TAX,
                    'medicare_wages_tips':          MIN_SS_WAGES,
                    'medicare_tax_withheld':        MIN_MED_TAX,
                }
                for field, value in block.items():
                    existing = getattr(data, field)
                    min_val  = thresholds.get(field, 0)
                    if existing is None or (min_val > 0 and existing < min_val):
                        if existing is not None:
                            logger.info(f"S7 overriding {field}: {existing} → {value}")
                        setattr(data, field, value)

        self._derive_rates(data)
        self._calculate_totals(data)
        self._validate(data)
        self._score(data, text)

        logger.info(
            f"W2Extractor: confidence={data.extraction_confidence:.0%} "
            f"corrected={corrected} missing={data.missing_critical_fields}"
        )
        return data

    # ── Meta ───────────────────────────────────────────────────────────────

    def _extract_meta(self, text: str, data: W2Data) -> None:
        for p in [
            r'\b(20\d{2})\s+(?:Form\s+)?W-?2',
            r'(?:Form\s+)?W-?2\b.*?\b(20\d{2})\b',
            r'(?:tax\s+year|year)\s*:?\s*(20\d{2})',
        ]:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                data.tax_year = int(m.group(1))
                break
        if not data.tax_year:
            m = re.search(r'\b(20[12]\d)\b', text)
            if m:
                data.tax_year = int(m.group(1))

        if re.search(r'W-?2\s*(?:VI|AS|GU|PR|MP)\b', text, re.IGNORECASE):
            data.form_type = "W-2 (Territory)"

    # ── Employer ───────────────────────────────────────────────────────────

    def _extract_employer(self, text: str, data: W2Data) -> None:
        # EIN — always reliable
        m = re.search(r'\b(\d{2}-\d{7})\b', text)
        if m:
            data.employer_ein = m.group(1)

        # Employer name + address from "c Employer name/address" block
        # The block contains: name (line 1), street (line 2), city/state/zip (line 3)
        c_block = re.search(
            r'c\s+Employer[\'s]*\s+name[,\s]+address[^\n]*\n((?:[^\n]+\n){1,6})',
            text, re.IGNORECASE
        )
        if not c_block:
            c_block = re.search(
                r'c\s+Employer[\'s]*\s+name[^\n]*\n((?:[^\n]+\n){1,6})',
                text, re.IGNORECASE
            )

        if c_block:
            skip = re.compile(
                r'^\d{2}-\d{7}$'            # EIN
                r'|^[a-f]\s+'               # next box label
                r'|^box\s+'
                r'|^d\s+control'
                r'|^previously\s+reported'  # W-2c column header
                r'|^correct\s+information',
                re.IGNORECASE
            )
            addr_like = re.compile(
                r'^\d+\s+[A-Z]'    # starts with street number
                r'|\bSTE\b|\bSUITE\b|\bAVE\b|\bST\b|\bBLVD\b|\bDR\b|\bRD\b'
                r'|\b[A-Z]{2}\s+\d{5}',  # state + zip at end
                re.IGNORECASE
            )

            lines = []
            for l in c_block.group(1).split('\n'):
                l = l.strip()
                if l and not skip.match(l):
                    lines.append(l)

            if lines:
                # First non-address line = employer name
                for i, l in enumerate(lines):
                    if not addr_like.search(l) and not re.match(r'^\d{2}-\d{7}$', l):
                        data.employer_name = l
                        addr_lines = [x for x in lines[i+1:] if x and x != data.employer_name]
                        if addr_lines:
                            data.employer_address = ', '.join(addr_lines[:2])
                        break
                # If every line looked like an address, take first as name
                if not data.employer_name and lines:
                    data.employer_name = lines[0]
                    if len(lines) > 1:
                        data.employer_address = ', '.join(lines[1:3])

        # Fallback: look for name before EIN
        if not data.employer_name and data.employer_ein:
            ein_pos = text.find(data.employer_ein)
            before = text[:ein_pos].strip() if ein_pos > 0 else ''
            skip = re.compile(
                r'^(?:W-?2|wage|tax|statement|20\d{2}|copy\s+[a-z]'
                r'|department|employee|employer\s+id|ein\b|b\s+employer)',
                re.IGNORECASE
            )
            addr_like2 = re.compile(r'^\d+\s+[A-Z]|\bSTE\b|\bSUITE\b', re.IGNORECASE)
            candidates = [
                l.strip() for l in before.split('\n')
                if l.strip()
                and not skip.match(l.strip())
                and not re.match(r'^\d{2}-\d{7}$', l.strip())
                and not addr_like2.search(l.strip())
                and len(l.strip()) > 3
            ]
            if candidates:
                data.employer_name = candidates[-1]

        # Control number (Box d)
        m = re.search(
            r'\bd\s+Control\s*[Nn]umber[^\n]*?([A-Z0-9\-]{4,20})',
            text, re.IGNORECASE
        )
        if m:
            data.control_number = m.group(1)

    # ── Employee ───────────────────────────────────────────────────────────

    def _extract_employee(self, text: str, data: W2Data) -> None:
        # SSN — mask immediately
        m = re.search(r'\b(\d{3}-\d{2}-\d{4})\b', text)
        if m:
            data.employee_ssn_masked = self._mask_ssn(m.group(1))
        else:
            m = re.search(r'\b(\d{9})\b', text)
            if m:
                data.employee_ssn_masked = self._mask_ssn(m.group(1))

        # Employee name — strict: reject if it contains label words or is too short
        reject = re.compile(
            r'\b(?:last|initial|name|address|suf|zip|first|previously|correct'
            r'|reported|information|contribute|section|using)\b',
            re.IGNORECASE
        )
        for p in [
            r'e\s+Employee[^\n]*\n\s*([A-Za-z][A-Za-z .,-]{2,60})',
            r'first\s+name\s+and\s+initial[^\n]*Last\s+name[^\n]*\n\s*([A-Za-z][A-Za-z .,-]{2,60})',
            r'Employee[\'s]*\s+name\s*:?\s*\n?\s*([A-Z][a-zA-Z .,-]{2,60})',
        ]:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
                if not reject.search(name) and len(name.split()) >= 2:
                    data.employee_name = name
                    break

        # Employee address
        m = re.search(
            r'f\s+Employee[\'s]*\s+address[^\n]*\n\s*(.+?)(?:\n\n|\Z)',
            text, re.IGNORECASE | re.DOTALL
        )
        if m:
            lines = [l.strip() for l in m.group(1).split('\n') if l.strip()]
            data.employee_address = ', '.join(lines[:2])

    # ── Federal ─────────────────────────────────────────────────────────────

    def _extract_federal(self, text: str, tables: List, data: W2Data, corrected: bool) -> None:
        data.wages_tips_compensation = self._extract_box(
            text, '1',
            ['Wages, tips, other compensation', 'Wages, tips, other comp', 'Wages'],
            tables, corrected
        )
        data.federal_income_tax_withheld = self._extract_box(
            text, '2',
            ['Federal income tax withheld', 'Federal income tax', 'Fed income tax withheld'],
            tables, corrected
        )

    # ── FICA ─────────────────────────────────────────────────────────────────

    def _extract_fica(self, text: str, tables: List, data: W2Data, corrected: bool) -> None:
        data.social_security_wages = self._extract_box(
            text, '3',
            ['Social security wages', 'Social sec. wages', 'SS wages'],
            tables, corrected
        )
        data.social_security_tax_withheld = self._extract_box(
            text, '4',
            ['Social security tax withheld', 'Social sec. tax withheld', 'SS tax withheld'],
            tables, corrected
        )
        # S6: Box 4 always follows Box 3 in the ADP values block.
        if data.social_security_tax_withheld is None and data.social_security_wages:
            v = self._extract_box_after_anchor(text, data.social_security_wages)
            if v is not None:
                logger.debug(f"Box 4 S6 anchor-after: {v}")
                data.social_security_tax_withheld = v
        data.medicare_wages_tips = self._extract_box(
            text, '5',
            ['Medicare wages and tips', 'Medicare wages', 'Medicare wages & tips'],
            tables, corrected
        )
        data.medicare_tax_withheld = self._extract_box(
            text, '6',
            ['Medicare tax withheld', 'Medicare tax'],
            tables, corrected
        )
        data.social_security_tips = self._extract_box(
            text, '7', ['Social security tips', 'SS tips'], tables, corrected
        )
        data.allocated_tips = self._extract_box(
            text, '8', ['Allocated tips'], tables, corrected
        )

    # ── Additional compensation ──────────────────────────────────────────────

    def _extract_additional(self, text: str, tables: List, data: W2Data, corrected: bool) -> None:
        data.dependent_care_benefits = self._extract_box(
            text, '10', ['Dependent care benefits', 'Dependent care'], tables, corrected
        )
        data.nonqualified_plans = self._extract_box(
            text, '11', ['Nonqualified plans', 'Non-qualified plans'], tables, corrected
        )

    # ── Box 12 ──────────────────────────────────────────────────────────────

    def _extract_box12(self, text: str, data: W2Data) -> None:
        items: List[Box12Item] = []
        seen: set = set()

        # For W-2c Box 12, take the LAST amount on the line (corrected value)
        for m in re.finditer(
            r'12([abcd])\s+(?:[Cc]ode\s+)?([A-Z]{1,2})((?:\s+[\d,]+\.?\d{0,2}){1,2})',
            text, re.IGNORECASE
        ):
            code = m.group(2).upper()
            amounts = self._extract_amounts_from_line(m.group(3))
            amount = amounts[-1] if amounts else None   # last = corrected
            if code not in seen and amount is not None:
                items.append(Box12Item(
                    code=code, amount=amount,
                    description=BOX_12_DESCRIPTIONS.get(code, f"Code {code}")
                ))
                seen.add(code)

        if not items:
            for m in re.finditer(
                r'(?<!\d)12(?!\d)\s+(?:[Cc]ode\s+)?([A-Z]{1,2})((?:\s+[\d,]+\.?\d{0,2}){1,2})',
                text, re.IGNORECASE
            ):
                code = m.group(1).upper()
                amounts = self._extract_amounts_from_line(m.group(2))
                amount = amounts[-1] if amounts else None
                if code not in seen and amount is not None and code in BOX_12_DESCRIPTIONS:
                    items.append(Box12Item(
                        code=code, amount=amount,
                        description=BOX_12_DESCRIPTIONS[code]
                    ))
                    seen.add(code)

        data.box_12_items = items

    # ── Box 13 ──────────────────────────────────────────────────────────────

    def _extract_box13(self, text: str, data: W2Data) -> None:
        ck = r'[Xx✓✔☑■]'
        data.statutory_employee = bool(re.search(
            rf'(?:{ck}\s*Statutory\s*[Ee]mployee|Statutory\s*[Ee]mployee\s*{ck})', text
        ))
        data.retirement_plan = bool(re.search(
            rf'(?:{ck}\s*[Rr]etirement\s*[Pp]lan|[Rr]etirement\s*[Pp]lan\s*{ck})', text
        ))
        data.third_party_sick_pay = bool(re.search(
            rf'(?:{ck}\s*[Tt]hird.?[Pp]arty\s*[Ss]ick|[Tt]hird.?[Pp]arty\s*[Ss]ick\s*{ck})',
            text
        ))

    # ── Box 14 ──────────────────────────────────────────────────────────────

    def _extract_box14(self, text: str, data: W2Data) -> None:
        items: List[Box14Item] = []
        # Box 14 items often have garbled labels from OCR; be lenient on label but
        # require the amount to be reasonable (> 0)
        for m in re.finditer(
            r'(?<!\d)14(?!\d)[^\n]{0,5}\n?\s*([A-Za-z][A-Za-z0-9\s\-/]{1,30}?)\s+([\d,]+\.?\d{0,2})',
            text, re.IGNORECASE
        ):
            label = re.sub(r'\s+', ' ', m.group(1).strip())
            # Skip if the label looks like a box number/label carry-over
            if re.match(r'^(?:12|13|15|16|17|18|19|20)\b', label):
                continue
            amount = self._parse_amount(m.group(2))
            if amount is not None and len(label) >= 2:
                items.append(Box14Item(label=label, amount=amount))
        data.box_14_items = items

    # ── State ────────────────────────────────────────────────────────────────

    def _extract_state(self, text: str, tables: List, data: W2Data, corrected: bool) -> None:
        r16 = self._extract_box(
            text, '16',
            ['State wages, tips, etc', 'State wages', 'State wages, tips'], tables, corrected
        )
        r17 = self._extract_box(
            text, '17', ['State income tax', 'State income tax withheld'], tables, corrected
        )

        found_states: List[Tuple[str, str]] = []
        for pattern in [
            r'(?<!\d)15(?!\d)[^\n]{0,40}?\b([A-Z]{2})\b\s+([\w\-]{4,25})',
            r'(?<!\d)15(?!\d)[^\n]*\n\s*\b([A-Z]{2})\b\s+([\w\-]{4,25})',
        ]:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                code = m.group(1).upper()
                if code in US_STATES and code not in _LABEL_NOISE_CODES:
                    found_states.append((code, m.group(2)))
            if found_states:
                break

        if found_states:
            for code, state_id in found_states[:2]:
                data.state_entries.append(StateEntry(
                    state=code, employer_state_id=state_id,
                    wages=r16, income_tax=r17,
                ))
        elif r16 is not None or r17 is not None:
            data.state_entries.append(StateEntry(wages=r16, income_tax=r17))

    # ── Local ────────────────────────────────────────────────────────────────

    def _extract_local(self, text: str, tables: List, data: W2Data, corrected: bool) -> None:
        r18 = self._extract_box(
            text, '18', ['Local wages, tips, etc', 'Local wages'], tables, corrected
        )
        r19 = self._extract_box(
            text, '19', ['Local income tax'], tables, corrected
        )
        locality = None
        m = re.search(r'(?<!\d)20(?!\d)\s+([A-Za-z][A-Za-z\s]{2,40}?)(?:\n|$)', text)
        if m:
            locality = m.group(1).strip()
        if r18 is not None or r19 is not None:
            data.local_entries.append(LocalEntry(
                locality_name=locality, wages=r18, income_tax=r19
            ))

    # ── Derived rates ─────────────────────────────────────────────────────────

    def _derive_rates(self, data: W2Data) -> None:
        dr = data.derived_rates

        if data.social_security_wages and data.social_security_tax_withheld:
            if data.social_security_wages > 0:
                dr.ss_tax_rate = round(
                    data.social_security_tax_withheld / data.social_security_wages, 4
                )

        if data.medicare_wages_tips and data.medicare_tax_withheld:
            if data.medicare_wages_tips > 0:
                dr.medicare_effective_rate = round(
                    data.medicare_tax_withheld / data.medicare_wages_tips, 4
                )
                dr.additional_medicare_applied = (
                    dr.medicare_effective_rate > KNOWN_MEDICARE_BASE_RATE + 0.002
                )

        wages   = data.wages_tips_compensation
        ss_wages = data.social_security_wages
        if wages is not None and ss_wages is not None:
            if wages > ss_wages + 1.00:
                dr.ss_wage_cap_hit = True
                dr.effective_ss_cap = ss_wages
            elif abs(wages - ss_wages) <= 1.00:
                known = get_ss_wage_limit(data.tax_year)
                if known is not None:
                    dr.known_cap_consulted = known
                    if abs(ss_wages - known) <= 1.00:
                        dr.ss_wage_cap_hit = True
                        dr.effective_ss_cap = known

    # ── Totals ───────────────────────────────────────────────────────────────

    def _calculate_totals(self, data: W2Data) -> None:
        ss_tax  = data.social_security_tax_withheld or 0.0
        med_tax = data.medicare_tax_withheld or 0.0
        fed_tax = data.federal_income_tax_withheld or 0.0

        fica = ss_tax + med_tax
        data.total_fica_withheld = round(fica, 2) if fica > 0 else None

        total = fed_tax + fica
        data.total_taxes_withheld = round(total, 2) if total > 0 else None

        wages = data.wages_tips_compensation
        if wages and wages > 0 and fed_tax > 0:
            data.effective_federal_tax_rate = round((fed_tax / wages) * 100, 2)

        sw = data.state_wages
        st = data.state_income_tax
        if sw and sw > 0 and st and st > 0:
            data.effective_state_tax_rate = round((st / sw) * 100, 2)

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate(self, data: W2Data) -> None:
        v = ValidationResult()
        dr = data.derived_rates

        wages     = data.wages_tips_compensation or 0
        fed_tax   = data.federal_income_tax_withheld or 0
        ss_wages  = data.social_security_wages or 0
        ss_tax    = data.social_security_tax_withheld or 0
        med_wages = data.medicare_wages_tips or 0
        med_tax   = data.medicare_tax_withheld or 0

        if not wages:
            v.errors.append("Box 1 (Wages) missing — required field")
        if not data.employer_ein:
            v.errors.append("Employer EIN missing")

        # Sanity checks — skip for W-2c if rates look absurd
        # (indicates we picked the wrong column; log as warning not error)
        rates_sane = (
            dr.ss_tax_rate is None or dr.ss_tax_rate < 1.0
        ) and (
            dr.medicare_effective_rate is None or dr.medicare_effective_rate < 1.0
        )

        if wages > 0 and fed_tax > 0:
            rate = fed_tax / wages
            if rate > 0.50:
                v.warnings.append(
                    f"Federal withholding {rate:.1%} is unusually high "
                    f"(${fed_tax:,.2f} on ${wages:,.2f} wages)"
                    + (" — verify correct-information column was used" if data.is_corrected else "")
                )
            elif rate < 0.005:
                v.warnings.append(f"Federal withholding {rate:.1%} is very low — verify Box 2")

        if dr.ss_tax_rate is not None:
            if not any(abs(dr.ss_tax_rate - r) < 0.002 for r in KNOWN_SS_RATES):
                msg = f"SS tax rate (Box4÷Box3) is {dr.ss_tax_rate:.2%}"
                if dr.ss_tax_rate > 1.0 and data.is_corrected:
                    msg += " — W-2c: check that correct-information column was extracted"
                else:
                    msg += f" — expected one of {[f'{r:.1%}' for r in KNOWN_SS_RATES]}"
                v.warnings.append(msg)

        if dr.medicare_effective_rate is not None:
            max_ok = KNOWN_MEDICARE_BASE_RATE + ADDITIONAL_MEDICARE_RATE + 0.005
            if dr.medicare_effective_rate < KNOWN_MEDICARE_BASE_RATE - 0.002:
                v.warnings.append(
                    f"Medicare rate (Box6÷Box5) is {dr.medicare_effective_rate:.2%} — "
                    f"below standard {KNOWN_MEDICARE_BASE_RATE:.2%}"
                )
            elif dr.medicare_effective_rate > max_ok:
                msg = f"Medicare rate (Box6÷Box5) is {dr.medicare_effective_rate:.2%} — exceeds expected maximum {max_ok:.2%}"
                if data.is_corrected:
                    msg += " — W-2c: verify correct-information column"
                v.warnings.append(msg)
            if dr.additional_medicare_applied and dr.medicare_effective_rate < 1.0:
                v.flags.append(
                    f"Additional Medicare Tax (0.9%) appears withheld — "
                    f"effective rate {dr.medicare_effective_rate:.2%} (Box6÷Box5)"
                )

        if dr.ss_wage_cap_hit:
            cap = f"${dr.effective_ss_cap:,.2f}" if dr.effective_ss_cap else "annual limit"
            v.flags.append(
                f"SS wage cap hit — Box3 capped at {cap} (Box1 wages: ${wages:,.2f})"
            )

        if wages > 0 and ss_wages > 0:
            diff_pct = abs(wages - ss_wages) / wages
            if diff_pct > 0.30 and not dr.ss_wage_cap_hit:
                v.flags.append(
                    f"Box1 and Box3 differ by {diff_pct:.0%} "
                    f"(${abs(wages - ss_wages):,.2f}) — likely pre-tax deductions"
                )

        if ss_wages > 0 and med_wages > 0:
            if abs(ss_wages - med_wages) > max(wages * 0.01, 1.0) and not dr.ss_wage_cap_hit:
                v.warnings.append(
                    f"Box3 SS wages (${ss_wages:,.2f}) ≠ Box5 Medicare wages "
                    f"(${med_wages:,.2f}) and SS cap not detected — verify"
                )

        if not data.employee_ssn_masked:
            v.warnings.append("Employee SSN not found — check document quality")

        if data.state_income_tax and not data.state:
            v.warnings.append("State income tax withheld but no state code found (Box 15)")

        if (data.state
                and data.state not in NO_STATE_TAX_STATES
                and data.state_wages
                and not data.state_income_tax):
            v.warnings.append(
                f"{data.state} levies state income tax but Box 17 is empty — verify"
            )

        has_retirement_code = any(item.code in RETIREMENT_CODES for item in data.box_12_items)
        if has_retirement_code and not data.retirement_plan:
            v.flags.append(
                "Box 12 has retirement deferral code but Box 13 'Retirement plan' is not checked"
            )

        if data.is_corrected:
            v.flags.append(
                "This is a W-2c (corrected statement) — "
                "values shown are the corrected amounts, not original"
            )

        v.is_valid = len(v.errors) == 0
        data.validation = v

    # ── Confidence scoring ────────────────────────────────────────────────────

    def _score(self, data: W2Data, raw_text: str = "") -> None:
        critical = [
            ("wages_tips_compensation",      data.wages_tips_compensation),
            ("federal_income_tax_withheld",  data.federal_income_tax_withheld),
            ("social_security_wages",        data.social_security_wages),
            ("social_security_tax_withheld", data.social_security_tax_withheld),
            ("medicare_wages_tips",          data.medicare_wages_tips),
            ("medicare_tax_withheld",        data.medicare_tax_withheld),
        ]
        found = sum(1 for _, v in critical if v is not None)
        data.extraction_confidence = round(found / len(critical), 2)
        data.missing_critical_fields = [n for n, v in critical if v is None]

        # Diagnostic: dump text context when critical fields are missing
        if data.missing_critical_fields and raw_text:
            self._log_missing_field_context(raw_text, data.missing_critical_fields, data.form_type)


    def _log_missing_field_context(
        self, text: str, missing: List[str], form_type: str
    ) -> None:
        """Dump raw text context for unmatched boxes to aid pattern tuning."""
        logger.warning(f"=== W2 EXTRACTION DIAGNOSTIC ({form_type}) ===")
        logger.warning(f"Missing: {missing}")
        text_lines = text.split("\n")
        num_lines = [
            (i + 1, line)
            for i, line in enumerate(text_lines)
            if re.search(r"\d+\.\d{2}", line) or re.search(r"\b\d{4,}\b", line)
        ]
        logger.warning(f"Lines with numeric values ({len(num_lines)} total):")
        for lineno, line in num_lines[:50]:
            logger.warning(f"  L{lineno:4}: {repr(line)}")
        for i, line in enumerate(text_lines):
            if "federal" in line.lower() or "income tax" in line.lower():
                ctx = text_lines[max(0, i - 1):min(len(text_lines), i + 6)]
                logger.warning(f"  [federal/income-tax context at L{i + 1}]:")
                for j, cl in enumerate(ctx):
                    logger.warning(f"    L{i + j + 1:4}: {repr(cl)}")
        logger.warning("=== END DIAGNOSTIC ===")


# =============================================================================
# CLI TEST HARNESS
# =============================================================================

def test_extract(pdf_path: str) -> None:
    """Quick CLI test: extract and print all W-2 fields from a PDF."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    raw_text = "\n".join(page.get_text() for page in doc)
    doc.close()

    extractor = W2Extractor()
    data = extractor.extract(raw_text)
    dr = data.derived_rates

    sep = "=" * 60
    print(sep)
    print(f"  W-2 Extraction: {pdf_path}")
    print(sep)
    print(f"  Form:          {data.form_type}")
    print(f"  Tax Year:      {data.tax_year or 'NOT FOUND'}")
    print(f"  Employer:      {data.employer_name or 'NOT FOUND'}")
    print(f"  EIN:           {data.employer_ein or 'NOT FOUND'}")
    print(f"  Employee:      {data.employee_name or 'NOT FOUND'}")
    print(f"  SSN:           {data.employee_ssn_masked or 'NOT FOUND'}")
    print(sep)

    def fmt(v):
        return f"  ${v:>13,.2f}" if v is not None else "     NOT FOUND"

    print(f"  Box  1  Wages:              {fmt(data.wages_tips_compensation)}")
    print(f"  Box  2  Federal Withheld:   {fmt(data.federal_income_tax_withheld)}")
    print(f"  Box  3  SS Wages:           {fmt(data.social_security_wages)}")
    print(f"  Box  4  SS Tax:             {fmt(data.social_security_tax_withheld)}")
    print(f"  Box  5  Medicare Wages:     {fmt(data.medicare_wages_tips)}")
    print(f"  Box  6  Medicare Tax:       {fmt(data.medicare_tax_withheld)}")
    print(f"  Box 16  State Wages:        {fmt(data.state_wages)}")
    print(f"  Box 17  State Tax:          {fmt(data.state_income_tax)}")
    print(sep)
    print("  Rates (document-derived):")
    if dr.ss_tax_rate is not None:
        ok = "✓" if any(abs(dr.ss_tax_rate - r) < 0.002 for r in KNOWN_SS_RATES) else "⚠"
        print(f"    SS rate:       {ok} {dr.ss_tax_rate:.4f}  (Box4÷Box3)")
    if dr.medicare_effective_rate is not None:
        ok = "✓" if dr.medicare_effective_rate <= 0.023 else "⚠"
        print(f"    Medicare rate: {ok} {dr.medicare_effective_rate:.4f}  (Box6÷Box5)")
    print(sep)
    print(f"  Confidence:    {data.extraction_confidence:.0%}")
    if data.missing_critical_fields:
        print(f"  Missing:       {data.missing_critical_fields}")
    if data.validation.errors:
        print(f"  Errors:        {data.validation.errors}")
    if data.validation.warnings:
        print(f"  Warnings:      {data.validation.warnings}")
    print(sep)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python w2_extractor.py /path/to/w2.pdf")
        sys.exit(1)
    test_extract(sys.argv[1])