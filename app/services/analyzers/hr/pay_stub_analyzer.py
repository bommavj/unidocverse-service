"""
Pay Stub Analyzer
==================
Extraction + Intelligence for UniDocVerse payroll documents.

Supported formats (table-first, works on any layout):
  ADP Workforce Now / Run / iPayStatements
  Paychex Flex / Preview
  Workday, Gusto, QuickBooks Payroll
  SAP, Oracle HCM, Ceridian Dayforce
  Generic / in-house payroll systems
  International formats with spaced-decimal numbers

Architecture:
  PayStubExtractor   — table-first extraction, spaced-number parser
  PayStubData        — clean dataclass, no business logic
  PayStubIntelligence — advisory layer (rates, projections, actions)
  PayStubAnalyzer    — orchestrator called by DocumentAnalysisNode

IRS limits read from app/resources/irs_limits.json — zero hardcoding here.

Drop at: app/services/analyzers/hr/pay_stub_analyzer.py
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── IRS limits — loaded from JSON, not hardcoded ────────────────────────────

try:
    from app.services.irs_limits import get_limits, get_ss_wage_limit, get_tax_rates
except ImportError:
    # Dev / test fallback — warns but doesn't crash
    logger.warning("app.services.irs_limits not found — using inline fallback values")

    def get_limits(year: Optional[int] = None) -> Dict[str, float]:
        return {"401k": 23_500, "401k_catchup": 31_000, "ira": 7_000,
                "ira_catchup": 8_000, "hsa_single": 4_300, "hsa_family": 8_550,
                "fsa": 3_300, "dependent_fsa": 5_000}

    def get_ss_wage_limit(year: Optional[int] = None) -> float:
        return 176_100.0

    def get_tax_rates() -> Dict[str, float]:
        return {"ss_tax_rate": 0.062, "medicare_tax_rate": 0.0145,
                "additional_medicare_rate": 0.009,
                "additional_medicare_threshold": 200_000}


# ─── Constants ────────────────────────────────────────────────────────────────

PAY_FREQUENCIES: Dict[str, int] = {
    "weekly": 52, "biweekly": 26, "semimonthly": 24,
    "monthly": 12, "quarterly": 4, "annual": 1,
}

NO_STATE_TAX = {"AK", "FL", "NV", "NH", "SD", "TN", "TX", "WA", "WY"}

# Deduction label → (category, is_pretax)
# Matched via substring — longer/more specific keys must come first
DEDUCTION_MAP: List[Tuple[str, str, bool]] = [
    # ── Taxes (post-tax) ────────────────────────────────────────────────────
    ("federal income tax",  "federal_tax",      False),
    ("fed income tax",      "federal_tax",      False),
    ("federal tax",         "federal_tax",      False),
    ("federal withholding", "federal_tax",      False),
    (" fit ",               "federal_tax",      False),
    ("social security",     "social_security",  False),
    ("oasdi",               "social_security",  False),
    ("ss tax",              "social_security",  False),
    ("medicare",            "medicare",         False),
    ("state income tax",    "state_tax",        False),
    ("state withholding",   "state_tax",        False),
    (" sit ",               "state_tax",        False),
    ("local income tax",    "local_tax",        False),
    ("local tax",           "local_tax",        False),
    ("city tax",            "local_tax",        False),
    ("county tax",          "local_tax",        False),
    ("township",            "local_tax",        False),
    ("twp ",                "local_tax",        False),
    (" sui ",               "sui_tax",          False),
    (" sdi ",               "sdi_tax",          False),
    ("state ui",            "sui_tax",          False),
    ("state di",            "sdi_tax",          False),
    ("pfml",                "pfml_tax",         False),
    # ── Retirement (pre-tax) ────────────────────────────────────────────────
    ("roth 401k",           "retirement_roth",  False),
    ("roth 403b",           "retirement_roth",  False),
    ("roth 457",            "retirement_roth",  False),
    ("401(k)",              "retirement_401k",  True),
    ("401k",                "retirement_401k",  True),
    ("403(b)",              "retirement_403b",  True),
    ("403b",                "retirement_403b",  True),
    ("457",                 "retirement_457",   True),
    ("simple ira",          "retirement_simple",True),
    ("sep ira",             "retirement_sep",   True),
    # ── Health (pre-tax) ────────────────────────────────────────────────────
    ("health savings",      "hsa",              True),
    (" hsa",                "hsa",              True),
    ("hsa ",                "hsa",              True),
    ("flex spending",       "fsa",              True),
    ("dependent care fsa",  "dependent_care_fsa", True),
    ("dependent care",      "dependent_care_fsa", True),
    (" fsa",                "fsa",              True),
    ("medical ins",         "health_insurance", True),
    ("medical",             "health_insurance", True),
    ("health ins",          "health_insurance", True),
    ("health",              "health_insurance", True),
    ("dental",              "dental_insurance", True),
    ("vision",              "vision_insurance", True),
    # ── Protection (mix) ────────────────────────────────────────────────────
    ("life insurance",      "life_insurance",   True),
    ("basic life",          "life_insurance",   True),
    ("supp life",           "life_insurance",   False),
    ("long term dis",       "ltd",              True),
    ("short term dis",      "std",              True),
    (" ltd",                "ltd",              True),
    (" std",                "std",              True),
    ("disability",          "disability",       True),
    # ── Misc (post-tax) ─────────────────────────────────────────────────────
    ("garnish",             "garnishment",      False),
    ("child support",       "child_support",    False),
    ("union dues",          "union_dues",       False),
    ("union",               "union_dues",       False),
    ("parking",             "commuter_parking", True),
    ("transit",             "commuter_transit", True),
    ("commuter",            "commuter",         True),
    ("advance",             "advance_repay",    False),
]

EARNINGS_CATEGORIES: List[Tuple[str, str]] = [
    ("overtime",      "overtime"),
    ("ot pay",        "overtime"),
    ("time and a half","overtime"),
    ("double time",   "overtime"),
    ("bonus",         "bonus"),
    ("incentive",     "bonus"),
    ("award",         "bonus"),
    ("commission",    "commission"),
    ("vacation",      "pto"),
    ("holiday",       "pto"),
    (" pto",          "pto"),
    ("sick",          "pto"),
    ("personal day",  "pto"),
    ("floating",      "pto"),
    ("severance",     "other"),
    ("retro",         "other"),
    ("relocation",    "other"),
    ("stipend",       "other"),
    ("draw",          "other"),
]

# Row labels that are section headers / noise — never treat as data
SKIP_LABELS = {
    "statutory", "other benefits", "co.", "file", "dept", "clock",
    "vchr", "no", "basis of pay", "company ph", "company phone",
    "rate", "hours", "this period", "to date", "year to date", "ytd",
    "description", "amount", "current", "earnings", "deductions",
    "taxes", "withholdings",
}

# State codes that are not actual US state abbreviations
NON_STATE_CODES = {"TO", "OF", "IN", "AT", "BY", "CO", "IT", "OR"}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class EarningsLine:
    description: str
    current:     float
    category:    str            = "regular"
    hours:       Optional[float] = None
    rate:        Optional[float] = None
    ytd:         Optional[float] = None


@dataclass
class TaxLine:
    description: str
    current:     float
    category:    str            = "federal_tax"
    ytd:         Optional[float] = None


@dataclass
class DeductionLine:
    description: str
    current:     float
    category:    str            = "other"
    pretax:      bool           = False
    ytd:         Optional[float] = None


@dataclass
class PayStubData:
    document_type:           str   = "pay_stub"
    extraction_confidence:   float = 0.0
    missing_fields:          List[str] = field(default_factory=list)
    extraction_timestamp:    str   = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Employer
    employer_name:           Optional[str]   = None
    employer_ein:            Optional[str]   = None
    employer_address:        Optional[str]   = None

    # Employee
    employee_name:           Optional[str]   = None
    employee_id:             Optional[str]   = None
    employee_ssn_masked:     Optional[str]   = None
    department:              Optional[str]   = None
    job_title:               Optional[str]   = None
    state:                   Optional[str]   = None

    # Pay period
    pay_period_start:        Optional[str]   = None
    pay_period_end:          Optional[str]   = None
    pay_date:                Optional[str]   = None
    pay_frequency:           Optional[str]   = None
    pay_periods_per_year:    Optional[int]   = None

    # Earnings
    earnings:                List[EarningsLine]  = field(default_factory=list)
    gross_pay:               Optional[float] = None
    gross_pay_ytd:           Optional[float] = None
    regular_hours:           Optional[float] = None
    overtime_hours:          Optional[float] = None
    total_hours:             Optional[float] = None

    # Taxes
    taxes:                   List[TaxLine]       = field(default_factory=list)
    total_taxes:             Optional[float] = None
    federal_tax:             Optional[float] = None
    federal_tax_ytd:         Optional[float] = None
    state_tax:               Optional[float] = None
    state_tax_ytd:           Optional[float] = None
    local_tax:               Optional[float] = None
    social_security_tax:     Optional[float] = None
    medicare_tax:            Optional[float] = None

    # Deductions
    deductions:              List[DeductionLine] = field(default_factory=list)
    total_deductions:        Optional[float] = None
    total_deductions_ytd:    Optional[float] = None
    pretax_total:            Optional[float] = None
    posttax_total:           Optional[float] = None

    # Benefits (convenience)
    retirement_401k:         Optional[float] = None
    retirement_401k_ytd:     Optional[float] = None
    employer_401k_match:     Optional[float] = None
    health_insurance:        Optional[float] = None
    dental_insurance:        Optional[float] = None
    vision_insurance:        Optional[float] = None
    hsa_employee:            Optional[float] = None
    hsa_employer:            Optional[float] = None
    fsa:                     Optional[float] = None

    # Net pay
    net_pay:                 Optional[float] = None
    net_pay_ytd:             Optional[float] = None

    # Derived (set by intelligence layer)
    annualized_gross:        Optional[float] = None
    annualized_net:          Optional[float] = None
    effective_federal_rate:  Optional[float] = None
    effective_total_rate:    Optional[float] = None
    take_home_pct:           Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_type":          self.document_type,
            "extraction_confidence":  self.extraction_confidence,
            "missing_fields":         self.missing_fields,
            "extraction_timestamp":   self.extraction_timestamp,
            "employer_name":          self.employer_name,
            "employer_ein":           self.employer_ein,
            "employer_address":       self.employer_address,
            "employee_name":          self.employee_name,
            "employee_id":            self.employee_id,
            "employee_ssn_masked":    self.employee_ssn_masked,
            "department":             self.department,
            "job_title":              self.job_title,
            "state":                  self.state,
            "pay_period_start":       self.pay_period_start,
            "pay_period_end":         self.pay_period_end,
            "pay_date":               self.pay_date,
            "pay_frequency":          self.pay_frequency,
            "pay_periods_per_year":   self.pay_periods_per_year,
            "gross_pay":              self.gross_pay,
            "gross_pay_ytd":          self.gross_pay_ytd,
            "regular_hours":          self.regular_hours,
            "overtime_hours":         self.overtime_hours,
            "total_hours":            self.total_hours,
            "net_pay":                self.net_pay,
            "net_pay_ytd":            self.net_pay_ytd,
            "total_taxes":            self.total_taxes,
            "total_deductions":       self.total_deductions,
            "total_deductions_ytd":   self.total_deductions_ytd,
            "pretax_total":           self.pretax_total,
            "posttax_total":          self.posttax_total,
            "federal_tax":            self.federal_tax,
            "federal_tax_ytd":        self.federal_tax_ytd,
            "state_tax":              self.state_tax,
            "state_tax_ytd":          self.state_tax_ytd,
            "local_tax":              self.local_tax,
            "social_security_tax":    self.social_security_tax,
            "medicare_tax":           self.medicare_tax,
            "retirement_401k":        self.retirement_401k,
            "retirement_401k_ytd":    self.retirement_401k_ytd,
            "employer_401k_match":    self.employer_401k_match,
            "health_insurance":       self.health_insurance,
            "dental_insurance":       self.dental_insurance,
            "vision_insurance":       self.vision_insurance,
            "hsa_employee":           self.hsa_employee,
            "hsa_employer":           self.hsa_employer,
            "fsa":                    self.fsa,
            "annualized_gross":       self.annualized_gross,
            "annualized_net":         self.annualized_net,
            "effective_federal_rate": self.effective_federal_rate,
            "effective_total_rate":   self.effective_total_rate,
            "take_home_pct":          self.take_home_pct,
            "earnings":    [{"description": e.description, "hours": e.hours,
                             "rate": e.rate, "current": e.current,
                             "ytd": e.ytd, "category": e.category}
                            for e in self.earnings],
            "taxes":       [{"description": t.description, "current": t.current,
                             "ytd": t.ytd, "category": t.category}
                            for t in self.taxes],
            "deductions":  [{"description": d.description, "current": d.current,
                             "ytd": d.ytd, "category": d.category, "pretax": d.pretax}
                            for d in self.deductions],
        }


# ─── Extractor ────────────────────────────────────────────────────────────────

class PayStubExtractor:
    """
    Table-first extractor. Works on any pay stub worldwide.

    Primary path: iterate Docling table rows → parse label + amounts.
    Fallback:     scan raw text for key:value patterns.

    Spaced-number parser handles ADP / Ceridian format:
        "3 182 80"  → 3182.80
        "-421 42"   → -421.42
        "$2 111 40" → 2111.40
        "-16 30*"   → 16.30   (* = pre-tax marker, stripped)
    """

    # ── Amount parsing ───────────────────────────────────────────────────────

    def _parse(self, raw: str) -> Optional[float]:
        """Parse any dollar/number format into a float."""
        if not raw:
            return None

        s = str(raw).strip()

        # Strip pre-tax asterisk
        s = s.rstrip('*').strip()

        # Parenthetical negative: (421.42)
        negative = s.startswith('(') and s.endswith(')')
        if negative:
            s = s[1:-1].strip()

        # Dollar sign and leading minus
        s = s.lstrip('$').strip()
        if s.startswith('-'):
            negative = True
            s = s[1:].strip()

        if not s:
            return None

        # Standard: "3,182.80" or "3182.80"
        plain = s.replace(',', '')
        try:
            v = float(plain)
            return -v if negative else v
        except ValueError:
            pass

        # Spaced decimal: "3 182 80" → last 2-digit group = cents
        # Only applies when all parts are pure digits
        parts = s.split()
        if len(parts) >= 2 and all(p.isdigit() for p in parts) and len(parts[-1]) == 2:
            integer = int(''.join(parts[:-1]))
            cents   = int(parts[-1])
            v = integer + cents / 100.0
            return -v if negative else v

        return None

    def _amounts_from_cells(self, cells: List[str]) -> List[float]:
        """Parse all valid amounts from a list of cell strings."""
        result = []
        for c in cells:
            v = self._parse(c)
            if v is not None:
                result.append(v)
        return result

    # ── Label classification ─────────────────────────────────────────────────

    def _is_skip(self, label: str) -> bool:
        return (
            not label
            or len(label) < 2
            or re.match(r'^\d+$', label)
            or label.lower().strip() in SKIP_LABELS
        )

    def _is_gross(self, label: str) -> bool:
        dl = label.lower()
        return any(k in dl for k in (
            "gross pay", "gross earnings", "gross wages",
            "total gross", "total earnings", "total pay",
        ))

    def _is_net(self, label: str) -> bool:
        # "Net Pay" yes, "Net Check" (= $0 after DD split) no
        dl = label.lower().strip()
        return bool(re.match(r'^net\s+(pay|wages|earnings|amount|this)', dl))

    def _is_earning(self, label: str) -> bool:
        dl = " " + label.lower() + " "
        return any(k in dl for k in (
            " regular ", " overtime ", " salary ", " hourly ",
            " bonus ", " commission ", " vacation ", " holiday ",
            " pto ", " sick ", " personal ", " shift diff",
            " double time ", " severance ", " retro", " stipend",
            " draw ", " award ", " relocation",
        ))

    def _earning_category(self, label: str) -> str:
        dl = label.lower()
        for keyword, cat in EARNINGS_CATEGORIES:
            if keyword in dl:
                return cat
        return "regular"

    def _deduction_category(self, label: str) -> Tuple[str, bool]:
        """Return (category, is_pretax) for a deduction label."""
        dl = " " + label.lower() + " "
        for keyword, cat, pretax in DEDUCTION_MAP:
            if keyword in dl:
                return cat, pretax
        return "other", False

    def _is_benefit_deduction(self, label: str) -> bool:
        """True if this looks like a known deduction even if category is 'other'."""
        dl = label.lower()
        return any(k in dl for k in (
            "dental", "medical", "vision", "life", "disability",
            "hsa", "fsa", "401", "403", "457", "roth", "garnish",
            "union", "child support", "sui", "sdi", "pfml", "ins",
            "transit", "parking", "commuter", "advance",
        ))

    # ── Table scanner (primary) ──────────────────────────────────────────────

    def _scan_tables(self, tables: List, data: PayStubData) -> None:
        earnings   = []
        taxes      = []
        deductions = []

        for row in tables:
            if not row or len(row) < 2:
                continue

            # Find label = first non-empty, non-skip cell
            label = ""
            rest  = []
            for i, cell in enumerate(row):
                s = str(cell).strip() if cell else ""
                if s and not self._is_skip(s):
                    label = s
                    rest  = [str(c).strip() if c else "" for c in row[i + 1:]]
                    break

            if not label:
                continue

            label_l = label.lower().strip()

            # ── Gross pay ──────────────────────────────────────────────────
            if self._is_gross(label):
                amounts = self._amounts_from_cells(rest)
                if amounts and not data.gross_pay:
                    data.gross_pay = abs(amounts[0])
                if len(amounts) > 1 and not data.gross_pay_ytd:
                    data.gross_pay_ytd = abs(amounts[1])
                continue

            # ── Net pay ────────────────────────────────────────────────────
            if self._is_net(label):
                amounts = self._amounts_from_cells(rest)
                if amounts and not data.net_pay:
                    v = abs(amounts[0])
                    if v > 0:
                        data.net_pay = v
                continue

            # ── Total hours ────────────────────────────────────────────────
            if re.search(r'tot[al\s]*hrs|hours\s+worked|total\s+hours', label_l):
                amounts = self._amounts_from_cells(rest)
                if amounts:
                    data.total_hours = abs(amounts[0])
                continue

            # ── Earnings ───────────────────────────────────────────────────
            if self._is_earning(label):
                hours        = None
                rate         = None
                earn_current = None
                earn_ytd     = None

                if rest:
                    # ADP packed cell: "3182 80 40 00" = rate=3182.80, hours=40.00
                    toks = rest[0].split()
                    if len(toks) == 4 and all(t.isdigit() for t in toks):
                        rate  = int(toks[0]) + int(toks[1]) / 100
                        hours = int(toks[2]) + int(toks[3]) / 100
                        # Current and YTD are in subsequent cells
                        amounts = self._amounts_from_cells(rest[1:])
                    else:
                        amounts = self._amounts_from_cells(rest)
                        # If first amount looks like hours (≤ 300), treat it as such
                        if len(amounts) >= 2 and 0 < amounts[0] <= 300:
                            hours   = amounts[0]
                            amounts = amounts[1:]

                    if amounts:
                        earn_current = abs(amounts[0])
                        earn_ytd     = abs(amounts[1]) if len(amounts) > 1 else None

                if earn_current and earn_current > 0:
                    cat = self._earning_category(label)
                    earnings.append(EarningsLine(
                        description=label,
                        current=earn_current,
                        category=cat,
                        hours=hours,
                        rate=rate,
                        ytd=earn_ytd,
                    ))
                    if not data.regular_hours and hours and cat == "regular":
                        data.regular_hours = hours
                    if not data.overtime_hours and hours and cat == "overtime":
                        data.overtime_hours = hours
                continue

            # ── Taxes & deductions ─────────────────────────────────────────
            amounts = self._amounts_from_cells(rest)
            if not amounts:
                continue

            amount = abs(amounts[0])
            ytd    = abs(amounts[1]) if len(amounts) > 1 else None

            if amount <= 0:
                continue

            cat, pretax = self._deduction_category(label)

            if cat in ("federal_tax", "state_tax", "local_tax",
                       "social_security", "medicare",
                       "sui_tax", "sdi_tax", "pfml_tax"):
                taxes.append(TaxLine(description=label, current=amount,
                                     ytd=ytd, category=cat))
            elif cat != "other" or self._is_benefit_deduction(label):
                deductions.append(DeductionLine(description=label, current=amount,
                                                ytd=ytd, category=cat, pretax=pretax))

        data.earnings   = earnings
        data.taxes      = taxes
        data.deductions = deductions

        # Gross fallback = sum earnings
        if not data.gross_pay and earnings:
            data.gross_pay = round(sum(e.current for e in earnings), 2)

        # Totals
        tax_total = round(sum(t.current for t in taxes), 2)
        ded_total = round(sum(d.current for d in deductions), 2)
        if tax_total > 0:
            data.total_taxes = tax_total
        if tax_total + ded_total > 0:
            data.total_deductions = round(tax_total + ded_total, 2)

    # ── Text fallbacks ───────────────────────────────────────────────────────

    def _text_employer(self, text: str, data: PayStubData) -> None:
        if not data.employer_ein:
            m = re.search(r'\b(\d{2}-\d{7})\b', text)
            if m:
                data.employer_ein = m.group(1)

        if not data.employer_name:
            for line in text.split('\n')[:10]:
                line = line.strip()
                if (3 < len(line) <= 60
                        and not re.match(r'^\d', line)
                        and not re.search(
                            r'pay.?stub|earnings|statement|period|advice|check|deposit|page',
                            line, re.I)
                        and not re.search(r'\d{5}', line)
                        and len(line.split()) <= 8):
                    data.employer_name = line
                    break

        if not data.state:
            m = re.search(r'\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b', text)
            if m and m.group(1) not in NON_STATE_CODES:
                data.state = m.group(1)

    def _text_employee(self, text: str, data: PayStubData) -> None:
        if not data.employee_ssn_masked:
            m = re.search(r'\b(\d{3}-\d{2}-\d{4})\b', text)
            if m:
                digits = re.sub(r'\D', '', m.group(1))
                data.employee_ssn_masked = f"***-**-{digits[-4:]}"

        if not data.employee_name:
            for pattern in [
                r'deposited\s+to\s+the\s+account\s+of\s+([A-Z][A-Z\s]{2,40})',
                r'(?:employee|pay\s+to)\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',
            ]:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    name = m.group(1).strip().title()
                    if 2 <= len(name.split()) <= 5:
                        data.employee_name = name
                        break

        if not data.employee_id:
            m = re.search(
                r'(?:employee\s+(?:id|#|no)|emp\.?\s*(?:id|#))\s*:?\s*([A-Z0-9\-]{3,20})',
                text, re.IGNORECASE)
            if m:
                data.employee_id = m.group(1).strip()

        if not data.department:
            m = re.search(r'(?:department|dept)\s*:?\s*([A-Za-z][^\n]{2,40})', text, re.I)
            if m:
                data.department = m.group(1).strip()

        if not data.job_title:
            m = re.search(r'(?:job\s+title|position|title)\s*:?\s*([A-Za-z][^\n]{2,50})', text, re.I)
            if m:
                data.job_title = m.group(1).strip()

    def _text_dates(self, text: str, data: PayStubData) -> None:
        dr = r'\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}-\d{2}-\d{2}'

        if not data.pay_period_start:
            m = re.search(rf'period\s+begin(?:ning)?\s*:?\s*({dr})', text, re.I)
            if m:
                data.pay_period_start = m.group(1)

        if not data.pay_period_end:
            m = re.search(rf'period\s+end(?:ing)?\s*:?\s*({dr})', text, re.I)
            if m:
                data.pay_period_end = m.group(1)

        if not data.pay_date:
            for p in [rf'pay\s*date\s*:?\s*({dr})',
                      rf'check\s*date\s*:?\s*({dr})',
                      rf'payment\s*date\s*:?\s*({dr})']:
                m = re.search(p, text, re.I)
                if m:
                    data.pay_date = m.group(1)
                    break

        if not data.pay_frequency:
            tl = text.lower()
            # Only match frequency words when near a pay-related label
            # Bare substring match is too aggressive — "semimonthly" can appear anywhere on a stub
            freq_pattern = (
                r"(?:pay\s+frequency|frequency|paid|pay\s+cycle|cycle|basis\s+of\s+pay)"
                r"\s*:?\s*(bi\s*weekly|biweekly|bi-weekly|semi[-\s]monthly|semimonthly"
                r"|weekly|monthly|quarterly|annual)"
            )
            m = re.search(freq_pattern, tl)
            if m:
                raw = m.group(1).replace(" ", "").replace("-", "")
                data.pay_frequency        = raw
                data.pay_periods_per_year = PAY_FREQUENCIES.get(raw)

    def _text_amounts(self, text: str, data: PayStubData) -> None:
        """Last-resort text scan when tables yielded nothing useful."""
        if not data.gross_pay:
            for p in [r'gross\s+(?:pay|earnings|wages)\s*[:\$]?\s*([\d,\. ]+)',
                      r'total\s+gross\s*[:\$]?\s*([\d,\. ]+)']:
                m = re.search(p, text, re.I)
                if m:
                    v = self._parse(m.group(1).strip())
                    if v and v > 0:
                        data.gross_pay = v
                        break

        if not data.net_pay:
            for p in [r'net\s+(?:pay|wages|earnings)\s*[:\$]?\s*([\d,\. ]+)',
                      r'amount\s+(?:paid|deposited)\s*[:\$]?\s*([\d,\. ]+)']:
                m = re.search(p, text, re.I)
                if m:
                    v = self._parse(m.group(1).strip())
                    if v and v > 0:
                        data.net_pay = v
                        break

    # ── Post-processing ──────────────────────────────────────────────────────

    def _derive(self, data: PayStubData) -> None:
        """Fill gaps derivable from other fields."""
        if data.gross_pay and not data.net_pay and data.total_deductions:
            data.net_pay = round(data.gross_pay - data.total_deductions, 2)
        elif data.net_pay and not data.gross_pay and data.total_deductions:
            data.gross_pay = round(data.net_pay + data.total_deductions, 2)

    def _consolidate(self, data: PayStubData) -> None:
        """Roll up line items into convenience fields."""
        for t in data.taxes:
            c = t.category
            if c == "federal_tax"    and not data.federal_tax:
                data.federal_tax     = t.current
                data.federal_tax_ytd = t.ytd
            elif c == "state_tax"    and not data.state_tax:
                data.state_tax       = t.current
                data.state_tax_ytd   = t.ytd
            elif c == "local_tax"    and not data.local_tax:
                data.local_tax       = t.current
            elif c == "social_security" and not data.social_security_tax:
                data.social_security_tax = t.current
            elif c == "medicare"     and not data.medicare_tax:
                data.medicare_tax    = t.current

        for d in data.deductions:
            c = d.category
            if c == "retirement_401k"  and not data.retirement_401k:
                data.retirement_401k     = d.current
                data.retirement_401k_ytd = d.ytd
            elif c == "health_insurance" and not data.health_insurance:
                data.health_insurance = d.current
            elif c == "dental_insurance" and not data.dental_insurance:
                data.dental_insurance = d.current
            elif c == "vision_insurance" and not data.vision_insurance:
                data.vision_insurance = d.current
            elif c == "hsa"              and not data.hsa_employee:
                data.hsa_employee = d.current
            elif c == "fsa"              and not data.fsa:
                data.fsa = d.current

        if not data.total_taxes and data.taxes:
            data.total_taxes = round(sum(t.current for t in data.taxes), 2)

        data.pretax_total  = round(sum(d.current for d in data.deductions if d.pretax),  2)
        data.posttax_total = round(sum(d.current for d in data.deductions if not d.pretax), 2)

    def _infer_frequency(self, data: PayStubData) -> None:
        # ── Primary: calculate from actual pay period dates ──────────────────
        # Dates are ground truth — ALWAYS run this, even if text scan already
        # set pay_frequency. ADP and other processors print "Semimonthly" as a
        # label even on biweekly stubs (company default vs actual period).
        # A 7-day period window cannot lie; a text label can.
        if data.pay_period_start and data.pay_period_end:
            try:
                from datetime import date as _date
                dr = r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})|(\d{4})-(\d{2})-(\d{2})"

                def _to_date(s: str) -> _date:
                    m = re.search(dr, s)
                    if not m:
                        raise ValueError
                    g = m.groups()
                    if g[3]:  # YYYY-MM-DD
                        return _date(int(g[3]), int(g[4]), int(g[5]))
                    mo, d, y = int(g[0]), int(g[1]), int(g[2])
                    if y < 100:
                        y += 2000
                    return _date(y, mo, d)

                start = _to_date(data.pay_period_start)
                end   = _to_date(data.pay_period_end)
                days  = (end - start).days + 1  # inclusive

                date_freq = None
                date_ppy  = None

                if 6 <= days <= 8:
                    date_freq = "weekly"
                    date_ppy  = 52
                elif 13 <= days <= 15:
                    # 13-15 days: biweekly vs semimonthly
                    # Semimonthly always anchors to 1st or 16th (± 1 day)
                    if days == 15 and start.day in (1, 2, 15, 16):
                        date_freq = "semimonthly"
                        date_ppy  = 24
                    else:
                        date_freq = "biweekly"
                        date_ppy  = 26
                elif days == 16:
                    # 16-day window only valid for semimonthly (e.g. Feb short month)
                    date_freq = "semimonthly"
                    date_ppy  = 24
                elif 27 <= days <= 32:
                    date_freq = "monthly"
                    date_ppy  = 12
                elif 89 <= days <= 95:
                    date_freq = "quarterly"
                    date_ppy  = 4

                if date_freq:
                    if data.pay_frequency and data.pay_frequency != date_freq:
                        logger.warning(
                            f"pay_frequency override: text said '{data.pay_frequency}' "
                            f"but period {data.pay_period_start}→{data.pay_period_end} "
                            f"is {days} days → correcting to '{date_freq}'"
                        )
                    data.pay_frequency        = date_freq
                    data.pay_periods_per_year = date_ppy
                    logger.debug(
                        f"Frequency from dates: {data.pay_period_start}→"
                        f"{data.pay_period_end} ({days}d) = {data.pay_frequency}"
                    )
                    return
            except Exception:
                pass  # fall through to YTD method

        # ── Fallback: YTD ÷ gross pay ─────────────────────────────────────────
        # Only reach here if period dates were missing or unparseable.
        # Skip if text scan already set a frequency — YTD is least reliable.
        if data.pay_frequency:
            return
        if not data.gross_pay or not data.gross_pay_ytd or data.gross_pay <= 0:
            return

        periods = round(data.gross_pay_ytd / data.gross_pay)
        # Note: biweekly (26/yr) and semimonthly (24/yr) are nearly identical
        # from period count alone — treat both ambiguous ranges as biweekly
        # (more common) and let the text label correct if needed.
        for (lo, hi), (freq, ppy) in [
            ((1,   3),  ("monthly",     12)),
            ((4,   7),  ("biweekly",    26)),   # could be semimonthly — biweekly safer default
            ((8,  14),  ("biweekly",    26)),
            ((15, 20),  ("biweekly",    26)),
            ((21, 30),  ("semimonthly", 24)),
            ((31, 55),  ("weekly",      52)),
        ]:
            if lo <= periods <= hi:
                data.pay_frequency        = freq
                data.pay_periods_per_year = ppy
                logger.debug(f"Frequency inferred from YTD: {periods} periods = {freq}")
                break

    def _score(self, data: PayStubData) -> None:
        critical = [
            ("gross_pay",   data.gross_pay),
            ("net_pay",     data.net_pay),
            ("federal_tax", data.federal_tax),
            ("pay_date",    data.pay_date),
        ]
        found = sum(1 for _, v in critical if v is not None)
        data.extraction_confidence = round(found / len(critical), 2)
        data.missing_fields = [n for n, v in critical if v is None]

    # ── Entry point ──────────────────────────────────────────────────────────

    def extract(self, raw_text: str, tables: Optional[List] = None) -> PayStubData:
        text   = (raw_text or "").replace('\r\n', '\n').replace('\r', '\n')
        tables = tables or []
        data   = PayStubData()

        self._scan_tables(tables, data)
        self._text_employer(text, data)
        self._text_employee(text, data)
        self._text_dates(text, data)

        if not data.gross_pay:
            self._text_amounts(text, data)

        self._derive(data)
        self._consolidate(data)
        self._infer_frequency(data)
        self._score(data)

        logger.info(
            f"PayStubExtractor: confidence={data.extraction_confidence:.0%} "
            f"gross={data.gross_pay} net={data.net_pay} "
            f"freq={data.pay_frequency} missing={data.missing_fields}"
        )
        return data


# ─── Intelligence Layer ───────────────────────────────────────────────────────

class PayStubIntelligence:
    """
    Advisory layer — answers the questions every employee actually has:
      1. Where does my money go this period?
      2. Am I paying the right amount of tax?
      3. Am I saving enough for retirement?
      4. What should I do next?
      5. What will my W-2 look like?
    """

    def analyze(self, data: PayStubData) -> Dict[str, Any]:
        if not data.gross_pay:
            return {"error": "Insufficient data — gross pay not extracted"}

        year   = self._tax_year(data)
        limits = get_limits(year)
        rates  = get_tax_rates()
        ppy    = data.pay_periods_per_year or 26

        result: Dict[str, Any] = {}
        result.update(self._annualize(data, ppy))
        result.update(self._breakdown(data))
        result.update(self._tax_analysis(data, rates))
        result.update(self._benefit_analysis(data, limits, ppy))
        result.update(self._retirement_analysis(data, limits, ppy))
        result.update(self._w2_projection(data, ppy))
        result["action_plan"]      = self._actions(data, result, ppy, limits)
        result["alerts"]           = self._alerts(data, result)
        result["filing_checklist"] = self._checklist(data)
        return result

    def _tax_year(self, data: PayStubData) -> int:
        """Infer tax year from pay date or default to current year."""
        if data.pay_date:
            m = re.search(r'(\d{4})', data.pay_date)
            if m:
                return int(m.group(1))
        return datetime.utcnow().year

    def _annualize(self, data: PayStubData, ppy: int) -> Dict:
        gross = data.gross_pay or 0
        net   = data.net_pay   or 0

        ann_gross = round(gross * ppy, 2)
        ann_net   = round(net   * ppy, 2)

        data.annualized_gross = ann_gross
        data.annualized_net   = ann_net

        return {
            "annualized_gross":       ann_gross,
            "annualized_net":         ann_net,
            "pay_periods_per_year":   ppy,
        }

    def _breakdown(self, data: PayStubData) -> Dict:
        gross    = data.gross_pay or 1
        taxes    = data.total_taxes or 0
        benefits = (data.pretax_total or 0) + (data.posttax_total or 0)
        net      = data.net_pay or 0

        take_home_pct = round(net    / gross * 100, 1)
        tax_pct       = round(taxes  / gross * 100, 1)
        benefit_pct   = round(benefits / gross * 100, 1)

        data.take_home_pct = take_home_pct

        segments = [
            {"label": "Take-Home Pay",     "amount": net,      "pct": take_home_pct, "color": "#4F81BD"},
            {"label": "Taxes",             "amount": taxes,    "pct": tax_pct,       "color": "#C0504D"},
            {"label": "Benefits & Other",  "amount": benefits, "pct": benefit_pct,   "color": "#9BBB59"},
        ]

        tax_detail = [
            {"label": t.description, "amount": t.current,
             "pct": round(t.current / gross * 100, 2)}
            for t in data.taxes
        ]

        return {
            "pay_breakdown":  segments,
            "tax_breakdown":  tax_detail,
            "take_home_pct":  take_home_pct,
            "total_tax_pct":  tax_pct,
            "benefits_pct":   benefit_pct,
        }

    def _tax_analysis(self, data: PayStubData, rates: Dict) -> Dict:
        gross = data.gross_pay or 1
        ann   = data.annualized_gross or (gross * 26)

        fed_rate   = round((data.federal_tax         or 0) / gross * 100, 2)
        ss_rate    = round((data.social_security_tax or 0) / gross * 100, 2)
        med_rate   = round((data.medicare_tax        or 0) / gross * 100, 2)
        state_rate = round((data.state_tax           or 0) / gross * 100, 2)
        total_rate = round((data.total_taxes         or 0) / gross * 100, 2)

        data.effective_federal_rate = fed_rate
        data.effective_total_rate   = total_rate

        # Expected marginal bracket (2024/2025 single filer)
        brackets = [
            (11_600,   0.0), (47_150,  10.0), (100_525, 12.0),
            (191_950, 22.0), (243_725, 24.0), (609_350, 32.0),
            (float('inf'), 37.0),
        ]
        expected = next(r for cap, r in brackets if ann <= cap)

        signal = "on_track"
        if expected > 0:
            if fed_rate < expected * 0.55:
                signal = "under_withheld"
            elif fed_rate > expected * 1.35:
                signal = "over_withheld"

        messages = {
            "under_withheld": "Federal withholding looks low — you may owe at filing. Consider updating your W-4.",
            "over_withheld":  "You're over-withholding — update W-4 to increase monthly take-home.",
            "on_track":       "Federal withholding looks appropriate for your income level.",
        }

        # FICA accuracy checks
        fica_notes = []
        expected_ss  = rates["ss_tax_rate"]
        expected_med = rates["medicare_tax_rate"]
        if data.social_security_tax and abs(ss_rate / 100 - expected_ss) > 0.003:
            fica_notes.append(f"SS rate {ss_rate:.2f}% differs from expected {expected_ss*100:.1f}%")
        if data.medicare_tax and abs(med_rate / 100 - expected_med) > 0.002:
            fica_notes.append(f"Medicare rate {med_rate:.2f}% differs from expected {expected_med*100:.2f}%")

        return {
            "effective_federal_rate":  fed_rate,
            "effective_ss_rate":       ss_rate,
            "effective_medicare_rate": med_rate,
            "effective_state_rate":    state_rate,
            "effective_total_rate":    total_rate,
            "expected_marginal_rate":  expected,
            "withholding_signal":      signal,
            "withholding_message":     messages[signal],
            "fica_notes":              fica_notes,
        }

    def _benefit_analysis(self, data: PayStubData, limits: Dict, ppy: int) -> Dict:
        items = []

        def benefit_item(btype, per_period, limit_key, pretax=True, note=None):
            if not per_period:
                return
            annual = round(per_period * ppy, 2)
            limit  = limits.get(limit_key)
            entry  = {
                "type":    btype,
                "current": per_period,
                "annual":  annual,
                "pretax":  pretax,
            }
            if limit:
                entry["limit"]           = limit
                entry["headroom"]        = max(0, limit - annual)
                entry["utilization_pct"] = round(annual / limit * 100, 1)
            if note:
                entry["note"] = note
            items.append(entry)

        benefit_item("Health Insurance", data.health_insurance,  None,          pretax=True)
        benefit_item("Dental Insurance", data.dental_insurance,  None,          pretax=True)
        benefit_item("Vision Insurance", data.vision_insurance,  None,          pretax=True)
        benefit_item("HSA",              data.hsa_employee,      "hsa_single",  pretax=True)
        benefit_item("FSA",              data.fsa,               "fsa",         pretax=True,
                     note="Use-it-or-lose-it — plan medical expenses before year end")

        return {"benefit_analysis": items}

    def _retirement_analysis(self, data: PayStubData, limits: Dict, ppy: int) -> Dict:
        limit = limits.get("401k", 23_500)

        if not data.retirement_401k:
            return {
                "retirement_analysis": {
                    "contributing":  False,
                    "action": "No 401k contributions detected this period. "
                              "Even 3% reduces taxes immediately.",
                }
            }

        annual   = round(data.retirement_401k * ppy, 2)
        ytd      = data.retirement_401k_ytd or annual
        headroom = max(0, limit - annual)
        util_pct = round(annual / limit * 100, 1)
        on_pace  = annual >= limit * 0.90

        contrib_rate = (
            round(data.retirement_401k / data.gross_pay * 100, 1)
            if data.gross_pay else 0
        )
        tax_savings = round(annual * 0.22, 2)   # rough 22% bracket estimate

        return {
            "retirement_analysis": {
                "contributing":        True,
                "this_period":         data.retirement_401k,
                "ytd":                 ytd,
                "annualized":          annual,
                "irs_limit":           limit,
                "headroom":            headroom,
                "utilization_pct":     util_pct,
                "contribution_rate":   contrib_rate,
                "on_pace_for_max":     on_pace,
                "employer_match":      data.employer_401k_match,
                "estimated_tax_savings": tax_savings,
                "action": (
                    f"On pace to max 401k (${limit:,.0f} limit) — great work 🎉"
                    if on_pace else
                    f"Increase by ${headroom / ppy:,.0f}/paycheck to max out "
                    f"— saves ~${headroom * 0.22:,.0f} in taxes"
                ),
            }
        }

    def _w2_projection(self, data: PayStubData, ppy: int) -> Dict:
        if not data.gross_pay_ytd or not data.gross_pay:
            return {}

        periods_done = round(data.gross_pay_ytd / data.gross_pay)
        periods_left = max(0, ppy - periods_done)

        proj_gross = round(data.gross_pay_ytd + data.gross_pay * periods_left, 2)
        proj_fed   = (
            round(data.federal_tax_ytd + data.federal_tax * periods_left, 2)
            if data.federal_tax_ytd and data.federal_tax else None
        )
        proj_401k  = (
            round(data.retirement_401k_ytd + data.retirement_401k * periods_left, 2)
            if data.retirement_401k_ytd and data.retirement_401k else None
        )

        return {
            "w2_projection": {
                "periods_completed":      periods_done,
                "periods_remaining":      periods_left,
                "ytd_gross":              data.gross_pay_ytd,
                "projected_annual_gross": proj_gross,
                "projected_federal_tax":  proj_fed,
                "projected_401k":         proj_401k,
            }
        }

    def _actions(self, data: PayStubData, result: Dict,
                 ppy: int, limits: Dict) -> List[Dict]:
        actions = []

        # W-4 withholding
        signal = result.get("withholding_signal", "on_track")
        if signal == "under_withheld":
            actions.append({
                "priority": "high", "category": "tax",
                "action":   "Update W-4 withholding",
                "detail":   "Submit a new W-4 to your employer to avoid a tax bill at filing.",
            })
        elif signal == "over_withheld":
            monthly_gain = round((data.federal_tax or 0) * ppy / 12 * 0.15, 2)
            actions.append({
                "priority": "medium", "category": "tax",
                "action":   "Reduce W-4 withholding",
                "detail":   f"Reclaim ~${monthly_gain:,.2f}/month — file a new W-4 claiming fewer allowances.",
                "monthly_impact": monthly_gain,
            })

        # 401k
        ret = result.get("retirement_analysis", {})
        if not ret.get("contributing"):
            est_savings = round((data.annualized_gross or 0) * 0.03 * 0.22 / 12, 2)
            actions.append({
                "priority": "high", "category": "retirement",
                "action":   "Start 401k contributions",
                "detail":   "Even 3% saves on taxes now and compounds over time.",
                "monthly_impact": est_savings,
            })
        elif ret.get("contributing") and not ret.get("on_pace_for_max"):
            headroom    = ret.get("headroom", 0)
            per_period  = round(headroom / ppy, 2) if headroom else 0
            tax_save    = round(headroom * 0.22 / 12, 2) if headroom else 0
            if per_period > 0:
                actions.append({
                    "priority": "medium", "category": "retirement",
                    "action":   f"Increase 401k by ${per_period:,.2f}/paycheck",
                    "detail":   f"Max out to save ~${headroom * 0.22:,.0f} in taxes this year.",
                    "monthly_impact": tax_save,
                })

        # HSA headroom
        for b in result.get("benefit_analysis", []):
            if b["type"] == "HSA" and b.get("headroom", 0) > 200:
                actions.append({
                    "priority": "low", "category": "benefits",
                    "action":   "Increase HSA contributions",
                    "detail":   f"${b['headroom']:,.0f} of pre-tax HSA room remains. "
                                f"HSA funds roll over forever.",
                    "monthly_impact": round(b["headroom"] * 0.22 / 12, 2),
                })

        return sorted(actions, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["priority"]])

    def _alerts(self, data: PayStubData, result: Dict) -> List[Dict]:
        alerts = []

        if not data.retirement_401k:
            alerts.append({
                "type": "warning", "title": "No Retirement Contributions",
                "message": "No 401k/403b contributions found this period.",
            })

        if data.state and data.state not in NO_STATE_TAX and not data.state_tax:
            alerts.append({
                "type": "warning", "title": "Missing State Tax Withholding",
                "message": f"{data.state} has state income tax — verify withholding on your W-4.",
            })

        if result.get("withholding_signal") == "under_withheld":
            alerts.append({
                "type": "error", "title": "Under-Withheld",
                "message": "Federal withholding appears too low — risk of owing at filing.",
            })

        if result.get("fica_notes"):
            for note in result["fica_notes"]:
                alerts.append({"type": "info", "title": "FICA Rate Note", "message": note})

        if data.gross_pay and data.net_pay:
            if data.net_pay / data.gross_pay < 0.50:
                alerts.append({
                    "type": "info", "title": "High Deduction Rate",
                    "message": f"Only {data.net_pay/data.gross_pay:.0%} of gross pay reaches your account — verify all deductions.",
                })

        return alerts

    def _checklist(self, data: PayStubData) -> List[str]:
        items = [
            "✅ Verify gross pay matches your employment agreement or offer letter",
            "✅ Confirm all deductions are authorized — report any unexpected deductions to HR",
        ]
        if data.retirement_401k:
            items.append("✅ 401k contributions appear on W-2 Box 12 Code D — keep stubs to verify")
        if data.hsa_employee:
            items.append("✅ HSA contributions appear on W-2 Box 12 Code W — needed for Form 8889")
        if data.federal_tax:
            items.append("✅ YTD federal tax withheld will appear on W-2 Box 2")
        if data.state_tax:
            items.append("✅ YTD state tax withheld will appear on W-2 Box 17")
        items.append("✅ Save all pay stubs for the year — cross-check against your W-2 in January")
        return items


# ─── Analyzer (called by DocumentAnalysisNode) ───────────────────────────────

class PayStubAnalyzer:
    """
    Orchestrator. Called by UniDocVerse DocumentAnalysisNode.

    Register in analyzer_registry.py:
        "payroll": PayStubAnalyzer()

    Register in document_registry.py:
        "payroll": {"parser": None, "analyzer": PayStubAnalyzer}
    """

    def analyze(self, file_path: str, text: str = "",
                metadata: Optional[Dict] = None) -> Dict[str, Any]:
        logger.info(f"📊 PayStubAnalyzer: {file_path}")
        metadata = metadata or {}

        # Flatten tables from metadata
        raw_tables = []
        for t in metadata.get("tables", []):
            raw_tables.extend(t.get("data", []))

        # Extract
        extractor = PayStubExtractor()
        data      = extractor.extract(text, raw_tables)

        # Intelligence
        intelligence = PayStubIntelligence().analyze(data)

        # Assemble result
        result: Dict[str, Any] = {
            "type":                   "payroll",
            "has_advanced_analytics": True,
        }
        result.update(data.to_dict())
        result.update(intelligence)

        result["hero_metrics"]  = self._hero_metrics(data)
        result["insights"]      = self._insights(data, intelligence)
        result["ui_config"]     = self._ui_config()

        logger.info(
            f"✅ PayStubAnalyzer: gross={data.gross_pay} net={data.net_pay} "
            f"confidence={data.extraction_confidence:.0%}"
        )
        return result

    def _hero_metrics(self, data: PayStubData) -> Dict:
        return {
            "gross_pay":          data.gross_pay,
            "net_pay":            data.net_pay,
            "total_taxes":        data.total_taxes,
            "total_deductions":   data.total_deductions,
            "take_home_pct":      data.take_home_pct,
            "annualized_gross":   data.annualized_gross,
            "annualized_net":     data.annualized_net,
            "effective_fed_rate": data.effective_federal_rate,
            "retirement_401k":    data.retirement_401k,
            "gross_pay_ytd":      data.gross_pay_ytd,
        }

    def _insights(self, data: PayStubData, intel: Dict) -> List[Dict]:
        insights = []

        if data.take_home_pct and data.gross_pay and data.net_pay:
            insights.append({
                "title":       "Pay Breakdown",
                "description": (
                    f"Of your ${data.gross_pay:,.2f} gross pay, "
                    f"{data.take_home_pct:.1f}% (${data.net_pay:,.2f}) reaches your account. "
                    f"Taxes account for {intel.get('total_tax_pct', 0):.1f}%."
                ),
                "category": "summary",
            })

        signal = intel.get("withholding_signal")
        if signal and signal != "on_track":
            insights.append({
                "title":       "Withholding Alert",
                "description": intel.get("withholding_message", ""),
                "category":    "tax",
            })

        ret = intel.get("retirement_analysis", {})
        if ret.get("contributing"):
            insights.append({
                "title":       "Retirement Savings",
                "description": ret.get("action", ""),
                "category":    "retirement",
            })

        if data.annualized_gross and data.annualized_net:
            insights.append({
                "title":       "Annualized Income",
                "description": (
                    f"On pace for ${data.annualized_gross:,.0f} gross / "
                    f"${data.annualized_net:,.0f} net this year."
                ),
                "category": "projection",
            })

        return insights

    def _ui_config(self) -> Dict:
        return {
            "document_type": "payroll",
            "hero_metrics": [
                {"key": "gross_pay",        "label": "Gross Pay",        "format": "currency", "is_hero": True},
                {"key": "net_pay",          "label": "Net Pay",          "format": "currency", "is_hero": True},
                {"key": "total_taxes",      "label": "Total Taxes",      "format": "currency", "is_hero": True},
                {"key": "take_home_pct",    "label": "Take-Home %",      "format": "percent",  "is_hero": True},
                {"key": "annualized_gross", "label": "Annualized Gross", "format": "currency", "is_hero": False},
                {"key": "annualized_net",   "label": "Annualized Net",   "format": "currency", "is_hero": False},
                {"key": "gross_pay_ytd",    "label": "YTD Gross",        "format": "currency", "is_hero": False},
                {"key": "effective_fed_rate","label": "Fed Rate",         "format": "percent",  "is_hero": False},
                {"key": "retirement_401k",  "label": "401k This Period", "format": "currency", "is_hero": False},
                {"key": "retirement_401k_ytd","label": "401k YTD",       "format": "currency", "is_hero": False},
            ],
            "charts": [
                {"id": "pay_breakdown",         "title": "Pay Breakdown",              "type": "donut",    "data_path": "pay_breakdown",        "label_key": "label", "value_key": "amount"},
                {"id": "tax_breakdown",         "title": "Tax Breakdown",              "type": "bar",      "data_path": "tax_breakdown",        "x_key": "label",     "y_key": "amount", "color": "#C0504D"},
                {"id": "earnings_breakdown",    "title": "Earnings This Period",       "type": "bar",      "data_path": "earnings",             "x_key": "description","y_key": "current","color": "#4F81BD"},
                {"id": "retirement_progress",   "title": "401k vs IRS Limit",         "type": "progress", "data_path": "retirement_analysis",  "value_key": "annualized", "max_key": "irs_limit", "color": "#9BBB59"},
            ],
            "tables": [
                {
                    "id": "earnings_table", "title": "Earnings Detail", "data_path": "earnings",
                    "columns": [
                        {"key": "description", "label": "Description"},
                        {"key": "hours",       "label": "Hours",   "format": "number"},
                        {"key": "rate",        "label": "Rate",    "format": "currency"},
                        {"key": "current",     "label": "Current", "format": "currency"},
                        {"key": "ytd",         "label": "YTD",     "format": "currency"},
                    ],
                },
                {
                    "id": "taxes_table", "title": "Taxes", "data_path": "taxes",
                    "columns": [
                        {"key": "description", "label": "Tax"},
                        {"key": "current",     "label": "Current", "format": "currency"},
                        {"key": "ytd",         "label": "YTD",     "format": "currency"},
                    ],
                },
                {
                    "id": "deductions_table", "title": "Deductions", "data_path": "deductions",
                    "columns": [
                        {"key": "description", "label": "Description"},
                        {"key": "current",     "label": "Current", "format": "currency"},
                        {"key": "ytd",         "label": "YTD",     "format": "currency"},
                        {"key": "pretax",      "label": "Pre-Tax", "format": "boolean"},
                    ],
                },
            ],
            "sections": [
                {"id": "overview",    "title": "Pay Period Summary",    "type": "metrics", "metrics": ["gross_pay", "net_pay", "total_taxes", "take_home_pct"]},
                {"id": "breakdown",   "title": "Pay Breakdown",         "type": "charts",  "charts":  ["pay_breakdown", "tax_breakdown"]},
                {"id": "detail",      "title": "Earnings & Deductions", "type": "tables",  "tables":  ["earnings_table", "taxes_table", "deductions_table"]},
                {"id": "retirement",  "title": "Retirement & Benefits", "type": "charts",  "charts":  ["retirement_progress"]},
                {"id": "projections", "title": "Annual Projections",    "type": "metrics", "metrics": ["annualized_gross", "annualized_net", "gross_pay_ytd"]},
                {"id": "actions",     "title": "Action Plan",           "type": "actions", "data_path": "action_plan"},
            ],
        }