"""
Invoice / Bill Analyzer
========================
Extraction + Intelligence for UniDocVerse invoice and bill documents.

Supported invoice types:
  B2B  — vendor invoices, purchase orders, supplier bills
  B2C  — utility bills (electric, gas, water), phone, internet, subscription
  Medical bills — hospital, clinic, lab, pharmacy (separate from EOB)
  Contractor / freelance — SOW-based, hourly, milestone invoices

Architecture:
  InvoiceExtractor    — table-first extraction + text fallback
  InvoiceData         — clean dataclass, no business logic
  InvoiceIntelligence — advisory layer (payment risk, spend tracking, alerts)
  InvoiceAnalyzer     — orchestrator called by DocumentAnalysisNode

Intelligence output:
  - Payment status (paid / due / overdue / partial)
  - Days until due / days overdue
  - Duplicate detection signals (invoice number, amount, vendor, date)
  - Tax / VAT breakdown and effective rate
  - Vendor spend summary (YTD if prior invoices in DB)
  - Line item categorization (labor, materials, services, taxes, fees)
  - Early payment discount opportunity
  - Action plan (pay now, dispute, file, match to PO)

Drop at: app/services/analyzers/finance/invoice_analyzer.py
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

INVOICE_TYPES = {
    "b2b":        "B2B Invoice",
    "b2c":        "Bill / Statement",
    "medical":    "Medical Bill",
    "contractor": "Contractor Invoice",
    "utility":    "Utility Bill",
    "unknown":    "Invoice",
}

# Payment term text → days
PAYMENT_TERMS_DAYS: List[Tuple[str, int]] = [
    (r"net\s*(\d+)",                  0),    # net 30 → captured
    (r"due\s+on\s+receipt",           0),
    (r"immediate",                    0),
    (r"cod|cash\s+on\s+delivery",     0),
    (r"(\d+)\s+days?",                0),    # "30 days" → captured
    (r"end\s+of\s+month|eom",        30),
    (r"2/10\s+net\s*30",             30),    # early pay discount
    (r"1/10\s+net\s*30",             30),
    (r"net\s*60",                    60),
    (r"net\s*90",                    90),
]

# Line item category signals
LINE_CATEGORIES: List[Tuple[str, str]] = [
    # Labor
    ("labor",           "labor"),
    ("labour",          "labor"),
    ("hours",           "labor"),
    ("consulting",      "labor"),
    ("services",        "services"),
    ("professional",    "services"),
    ("management fee",  "services"),
    ("support",         "services"),
    ("maintenance",     "services"),
    # Materials / goods
    ("materials",       "materials"),
    ("supplies",        "materials"),
    ("equipment",       "materials"),
    ("hardware",        "materials"),
    ("parts",           "materials"),
    ("goods",           "materials"),
    ("product",         "materials"),
    # Shipping / freight
    ("shipping",        "shipping"),
    ("freight",         "shipping"),
    ("delivery",        "shipping"),
    ("handling",        "shipping"),
    # Tax
    ("sales tax",       "tax"),
    ("vat",             "tax"),
    ("gst",             "tax"),
    ("hst",             "tax"),
    ("pst",             "tax"),
    ("tax",             "tax"),
    # Fees / other
    ("late fee",        "late_fee"),
    ("penalty",         "late_fee"),
    ("interest",        "late_fee"),
    ("discount",        "discount"),
    ("credit",          "credit"),
    ("adjustment",      "adjustment"),
    # Utility-specific
    ("electricity",     "utility_electric"),
    ("electric",        "utility_electric"),
    ("gas",             "utility_gas"),
    ("natural gas",     "utility_gas"),
    ("water",           "utility_water"),
    ("sewer",           "utility_water"),
    ("internet",        "utility_internet"),
    ("broadband",       "utility_internet"),
    ("phone",           "utility_phone"),
    ("mobile",          "utility_phone"),
    ("wireless",        "utility_phone"),
    ("subscription",    "subscription"),
    ("license",         "subscription"),
    ("saas",            "subscription"),
    # Medical
    ("office visit",    "medical_visit"),
    ("emergency",       "medical_visit"),
    ("surgery",         "medical_procedure"),
    ("procedure",       "medical_procedure"),
    ("lab",             "medical_lab"),
    ("radiology",       "medical_lab"),
    ("imaging",         "medical_lab"),
    ("pharmacy",        "medical_rx"),
    ("prescription",    "medical_rx"),
    ("rx",              "medical_rx"),
]

# Utility company signals → invoice type = utility
UTILITY_SIGNALS = {
    "electric", "electricity", "energy", "gas", "natural gas", "water",
    "utility", "utilities", "power", "sewage", "broadband", "internet",
    "telecom", "comcast", "xfinity", "att", "verizon", "t-mobile",
    "spectrum", "cox", "pge", "pg&e", "con ed", "duke energy",
    "national grid", "dominion",
}

MEDICAL_SIGNALS = {
    "hospital", "clinic", "medical", "health", "dental", "vision",
    "laboratory", "radiology", "pharmacy", "urgent care", "er ",
    "emergency room", "physician", "doctor", "surgery center",
    "anesthesia", "pathology",
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class LineItem:
    description:  str
    quantity:     Optional[float] = None
    unit:         Optional[str]   = None
    unit_price:   Optional[float] = None
    amount:       float           = 0.0
    category:     str             = "services"
    is_tax:       bool            = False
    is_discount:  bool            = False


@dataclass
class InvoiceData:
    document_type:         str   = "invoice"
    invoice_subtype:       str   = "unknown"   # b2b | b2c | medical | contractor | utility
    extraction_confidence: float = 0.0
    missing_fields:        List[str] = field(default_factory=list)
    extraction_timestamp:  str   = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Identity
    invoice_number:        Optional[str]   = None
    po_number:             Optional[str]   = None
    account_number:        Optional[str]   = None

    # Parties
    vendor_name:           Optional[str]   = None
    vendor_address:        Optional[str]   = None
    vendor_tax_id:         Optional[str]   = None
    vendor_email:          Optional[str]   = None
    vendor_phone:          Optional[str]   = None
    client_name:           Optional[str]   = None
    client_address:        Optional[str]   = None

    # Dates
    invoice_date:          Optional[str]   = None
    due_date:              Optional[str]   = None
    service_period_start:  Optional[str]   = None
    service_period_end:    Optional[str]   = None

    # Payment terms
    payment_terms:         Optional[str]   = None
    payment_terms_days:    Optional[int]   = None
    early_pay_discount_pct: Optional[float] = None
    early_pay_discount_days: Optional[int] = None

    # Amounts
    line_items:            List[LineItem]  = field(default_factory=list)
    subtotal:              Optional[float] = None
    tax_amount:            Optional[float] = None
    tax_rate:              Optional[float] = None
    discount_amount:       Optional[float] = None
    shipping_amount:       Optional[float] = None
    total_amount:          Optional[float] = None
    amount_paid:           Optional[float] = None
    amount_due:            Optional[float] = None
    currency:              str             = "USD"

    # Payment status (derived)
    payment_status:        str             = "unknown"  # paid|due|overdue|partial|unknown
    days_until_due:        Optional[int]   = None
    days_overdue:          Optional[int]   = None
    is_overdue:            bool            = False

    # Derived intelligence fields
    effective_tax_rate:    Optional[float] = None
    labor_total:           Optional[float] = None
    materials_total:       Optional[float] = None
    services_total:        Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_type":           self.document_type,
            "invoice_subtype":         self.invoice_subtype,
            "extraction_confidence":   self.extraction_confidence,
            "missing_fields":          self.missing_fields,
            "extraction_timestamp":    self.extraction_timestamp,
            "invoice_number":          self.invoice_number,
            "po_number":               self.po_number,
            "account_number":          self.account_number,
            "vendor_name":             self.vendor_name,
            "vendor_address":          self.vendor_address,
            "vendor_tax_id":           self.vendor_tax_id,
            "vendor_email":            self.vendor_email,
            "vendor_phone":            self.vendor_phone,
            "client_name":             self.client_name,
            "client_address":          self.client_address,
            "invoice_date":            self.invoice_date,
            "due_date":                self.due_date,
            "service_period_start":    self.service_period_start,
            "service_period_end":      self.service_period_end,
            "payment_terms":           self.payment_terms,
            "payment_terms_days":      self.payment_terms_days,
            "early_pay_discount_pct":  self.early_pay_discount_pct,
            "early_pay_discount_days": self.early_pay_discount_days,
            "subtotal":                self.subtotal,
            "tax_amount":              self.tax_amount,
            "tax_rate":                self.tax_rate,
            "discount_amount":         self.discount_amount,
            "shipping_amount":         self.shipping_amount,
            "total_amount":            self.total_amount,
            "amount_paid":             self.amount_paid,
            "amount_due":              self.amount_due,
            "currency":                self.currency,
            "payment_status":          self.payment_status,
            "days_until_due":          self.days_until_due,
            "days_overdue":            self.days_overdue,
            "is_overdue":              self.is_overdue,
            "effective_tax_rate":      self.effective_tax_rate,
            "labor_total":             self.labor_total,
            "materials_total":         self.materials_total,
            "services_total":          self.services_total,
            "line_items": [
                {
                    "description": li.description,
                    "quantity":    li.quantity,
                    "unit":        li.unit,
                    "unit_price":  li.unit_price,
                    "amount":      li.amount,
                    "category":    li.category,
                    "is_tax":      li.is_tax,
                    "is_discount": li.is_discount,
                }
                for li in self.line_items
            ],
        }


# ─── Extractor ────────────────────────────────────────────────────────────────

class InvoiceExtractor:
    """
    Table-first extractor. Falls back to text patterns.

    Handles:
      - Standard invoices (QuickBooks, FreshBooks, Zoho, Wave, SAP, Oracle)
      - Utility bills (multi-section, usage + charges)
      - Medical bills (CPT codes, adjustment columns)
      - Contractor invoices (SOW reference, milestone, hourly)
      - International: comma-decimal, currency symbols, VAT/GST
    """

    # ── Amount parsing ───────────────────────────────────────────────────────

    def _parse(self, raw: Any) -> Optional[float]:
        if raw is None:
            return None
        s = str(raw).strip()
        if not s or s in ("-", "—", "N/A", "n/a"):
            return None

        negative = False
        s = s.strip()

        # Parenthetical negative (accounting style)
        if s.startswith("(") and s.endswith(")"):
            negative = True
            s = s[1:-1].strip()

        # Strip currency symbols
        s = re.sub(r"[£€¥₹$]", "", s).strip()

        # Leading minus / CR
        if s.startswith("-"):
            negative = True
            s = s[1:].strip()
        if s.upper().endswith("CR"):
            negative = True
            s = s[:-2].strip()

        # European comma-decimal: "1.234,56" → "1234.56"
        if re.match(r"^\d{1,3}(\.\d{3})+(,\d+)?$", s):
            s = s.replace(".", "").replace(",", ".")

        # Remove thousand separators then parse
        plain = re.sub(r"[,\s]", "", s)
        try:
            v = float(plain)
            return -v if negative else v
        except ValueError:
            return None

    def _parse_date(self, raw: str) -> Optional[str]:
        """Normalise various date formats to YYYY-MM-DD."""
        if not raw:
            return None
        raw = raw.strip()
        patterns = [
            r"(\d{4})-(\d{1,2})-(\d{1,2})",           # 2024-01-15
            r"(\d{1,2})/(\d{1,2})/(\d{4})",            # 01/15/2024
            r"(\d{1,2})-(\d{1,2})-(\d{4})",            # 01-15-2024
            r"(\d{1,2})\s+(\w+)\s+(\d{4})",            # 15 January 2024
            r"(\w+)\s+(\d{1,2}),?\s+(\d{4})",          # January 15, 2024
        ]
        months = {m: i+1 for i, m in enumerate([
            "january","february","march","april","may","june",
            "july","august","september","october","november","december",
            "jan","feb","mar","apr","may","jun",
            "jul","aug","sep","oct","nov","dec",
        ])}
        for p in patterns:
            m = re.search(p, raw, re.IGNORECASE)
            if m:
                g = m.groups()
                try:
                    if len(g[0]) == 4:
                        y, mo, d = int(g[0]), int(g[1]), int(g[2])
                    elif g[1].isdigit():
                        mo, d, y = int(g[0]), int(g[1]), int(g[2])
                    else:
                        mon = months.get(g[1].lower(), 0)
                        if len(g[0]) == 4:
                            y, mo, d = int(g[0]), mon, int(g[2])
                        else:
                            d, mo, y = int(g[0]), mon, int(g[2])
                    return f"{y:04d}-{mo:02d}-{d:02d}"
                except (ValueError, IndexError):
                    continue
        return raw  # return as-is if can't parse

    # ── Subtype detection ────────────────────────────────────────────────────

    def _detect_subtype(self, text: str, data: InvoiceData) -> str:
        tl = text.lower()
        vendor = (data.vendor_name or "").lower()
        combined = tl + " " + vendor

        # Medical first (most specific)
        if any(s in combined for s in MEDICAL_SIGNALS):
            return "medical"

        # Utility
        if any(s in combined for s in UTILITY_SIGNALS):
            return "utility"

        # Contractor signals
        if any(s in tl for s in (
            "statement of work", "sow", "milestone", "retainer",
            "hourly rate", "consulting agreement", "independent contractor",
        )):
            return "contractor"

        # B2B signals
        if any(s in tl for s in (
            "purchase order", "p.o.", " po#", "vendor", "net 30", "net 60",
            "remit to", "bill to", "ship to", "tax id", "ein",
        )):
            return "b2b"

        return "b2c"

    # ── Table scanner ────────────────────────────────────────────────────────

    def _scan_tables(self, tables: List, data: InvoiceData) -> None:
        line_items: List[LineItem] = []

        for row in tables:
            if not row or len(row) < 2:
                continue

            cells = [str(c).strip() if c else "" for c in row]

            # Skip obvious header rows
            first_l = cells[0].lower()
            if first_l in ("description", "item", "service", "qty", "quantity",
                           "date", "code", "#", "no"):
                continue

            # Scan for total/summary rows
            joined = " ".join(cells).lower()

            # Subtotal
            if re.search(r"\bsubtotal\b", joined):
                amounts = [self._parse(c) for c in cells[1:] if self._parse(c) is not None]
                if amounts and not data.subtotal:
                    data.subtotal = abs(amounts[-1])
                continue

            # Tax rows
            if re.search(r"\b(sales\s+tax|vat|gst|hst|pst|tax)\b", joined):
                amounts = [self._parse(c) for c in cells[1:] if self._parse(c) is not None]
                if amounts and not data.tax_amount:
                    data.tax_amount = abs(amounts[-1])
                    # Try to capture rate from cell that looks like %
                    for c in cells:
                        m = re.search(r"(\d+\.?\d*)\s*%", c)
                        if m:
                            data.tax_rate = float(m.group(1))
                continue

            # Shipping
            if re.search(r"\b(shipping|freight|delivery)\b", joined):
                amounts = [self._parse(c) for c in cells[1:] if self._parse(c) is not None]
                if amounts and not data.shipping_amount:
                    data.shipping_amount = abs(amounts[-1])
                continue

            # Discount
            if re.search(r"\b(discount|credit|adjustment)\b", joined):
                amounts = [self._parse(c) for c in cells[1:] if self._parse(c) is not None]
                if amounts and not data.discount_amount:
                    v = self._parse(cells[-1])
                    data.discount_amount = abs(v) if v else 0
                continue

            # Total / balance due / amount due
            if re.search(r"\b(total\s+due|balance\s+due|amount\s+due|total\s+amount"
                         r"|grand\s+total|invoice\s+total|total)\b", joined):
                amounts = [self._parse(c) for c in cells[1:] if self._parse(c) is not None]
                if amounts and not data.total_amount:
                    data.total_amount = abs(amounts[-1])
                continue

            # Amount paid / payment received
            if re.search(r"\b(amount\s+paid|payment\s+received|paid)\b", joined):
                amounts = [self._parse(c) for c in cells[1:] if self._parse(c) is not None]
                if amounts and not data.amount_paid:
                    data.amount_paid = abs(amounts[-1])
                continue

            # Amount due (remaining)
            if re.search(r"\b(balance|remaining|now\s+due|please\s+pay)\b", joined):
                amounts = [self._parse(c) for c in cells[1:] if self._parse(c) is not None]
                if amounts and not data.amount_due:
                    data.amount_due = abs(amounts[-1])
                continue

            # Line items: first cell is description, has at least one parseable amount
            if cells[0] and len(cells[0]) > 2 and not cells[0][0].isdigit():
                amounts = [self._parse(c) for c in cells[1:] if self._parse(c) is not None]
                if not amounts:
                    continue
                amount = abs(amounts[-1])
                if amount <= 0:
                    continue

                # Try to extract qty and unit_price
                qty        = None
                unit_price = None
                unit       = None
                if len(amounts) >= 2:
                    # Heuristic: if first amount ≤ 10000 and looks like qty
                    candidate_qty = amounts[0]
                    if 0 < candidate_qty <= 10_000 and amounts[-1] != candidate_qty:
                        qty        = candidate_qty
                        unit_price = amounts[-1] / qty if qty else None

                # Detect quantity unit from cells
                for c in cells[1:]:
                    m = re.search(r"\b(\d+\.?\d*)\s*(hrs?|hours?|days?|ea|each|units?|lbs?|kg)\b",
                                  c, re.I)
                    if m:
                        qty  = float(m.group(1))
                        unit = m.group(2).lower()
                        break

                cat, is_tax, is_disc = self._line_category(cells[0])
                line_items.append(LineItem(
                    description=cells[0],
                    quantity=qty,
                    unit=unit,
                    unit_price=unit_price,
                    amount=amount,
                    category=cat,
                    is_tax=is_tax,
                    is_discount=is_disc,
                ))

        data.line_items = line_items

        # Derive subtotal from line items if not found
        if not data.subtotal and line_items:
            data.subtotal = round(sum(
                li.amount for li in line_items if not li.is_tax and not li.is_discount
            ), 2)

        # Derive total from subtotal + tax + shipping - discount
        if not data.total_amount and data.subtotal:
            data.total_amount = round(
                (data.subtotal or 0)
                + (data.tax_amount or 0)
                + (data.shipping_amount or 0)
                - (data.discount_amount or 0),
                2
            )

    def _line_category(self, description: str) -> Tuple[str, bool, bool]:
        """Return (category, is_tax, is_discount)."""
        dl = description.lower()
        for keyword, cat in LINE_CATEGORIES:
            if keyword in dl:
                is_tax  = cat == "tax"
                is_disc = cat in ("discount", "credit", "adjustment")
                return cat, is_tax, is_disc
        return "services", False, False

    # ── Text extraction ──────────────────────────────────────────────────────

    def _text_invoice_meta(self, text: str, data: InvoiceData) -> None:
        # Invoice number
        if not data.invoice_number:
            for p in [
                r"invoice\s*(?:#|no\.?|number)?\s*:?\s*([A-Z0-9\-\/]{3,30})",
                r"inv\s*#?\s*:?\s*([A-Z0-9\-\/]{3,30})",
                r"#\s*([A-Z0-9\-]{4,20})",
            ]:
                m = re.search(p, text, re.IGNORECASE)
                if m:
                    data.invoice_number = m.group(1).strip()
                    break

        # PO number
        if not data.po_number:
            m = re.search(
                r"(?:purchase\s+order|p\.?o\.?)\s*(?:#|no\.?|number)?\s*:?\s*([A-Z0-9\-\/]{3,25})",
                text, re.IGNORECASE)
            if m:
                data.po_number = m.group(1).strip()

        # Account number
        if not data.account_number:
            m = re.search(
                r"(?:account|acct)\s*(?:#|no\.?|number)?\s*:?\s*([A-Z0-9\-]{4,25})",
                text, re.IGNORECASE)
            if m:
                data.account_number = m.group(1).strip()

        # Currency
        if re.search(r"£|\bGBP\b", text):
            data.currency = "GBP"
        elif re.search(r"€|\bEUR\b", text):
            data.currency = "EUR"
        elif re.search(r"₹|\bINR\b", text):
            data.currency = "INR"
        elif re.search(r"C\$|\bCAD\b", text):
            data.currency = "CAD"

    def _text_parties(self, text: str, data: InvoiceData) -> None:
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        # Vendor tax ID / EIN
        if not data.vendor_tax_id:
            m = re.search(r"(?:tax\s*id|ein|vat\s*(?:no|#|number)?)\s*:?\s*([A-Z0-9\-]{5,20})",
                          text, re.IGNORECASE)
            if m:
                data.vendor_tax_id = m.group(1).strip()

        # Email
        if not data.vendor_email:
            m = re.search(r"[\w.\-+]+@[\w.\-]+\.[a-z]{2,}", text)
            if m:
                data.vendor_email = m.group(0)

        # Phone
        if not data.vendor_phone:
            m = re.search(r"(?:tel|phone|ph)\.?\s*:?\s*([\d\s\(\)\-\+\.]{7,20})", text, re.I)
            if m:
                data.vendor_phone = m.group(1).strip()

        # Vendor name: first non-empty line before "invoice" keyword
        if not data.vendor_name:
            inv_pos = text.lower().find("invoice")
            search_text = text[:inv_pos] if inv_pos > 0 else text[:500]
            for line in search_text.split("\n"):
                line = line.strip()
                if (4 < len(line) <= 80
                        and not re.match(r"^\d", line)
                        and not re.search(r"@|\bwww\b|http", line)
                        and len(line.split()) <= 10):
                    data.vendor_name = line
                    break

        # Bill to / client name
        if not data.client_name:
            m = re.search(
                r"(?:bill\s+to|sold\s+to|client|customer)\s*:?\s*\n?\s*([A-Z][^\n]{2,60})",
                text, re.IGNORECASE)
            if m:
                data.client_name = m.group(1).strip()

    def _text_dates(self, text: str, data: InvoiceData) -> None:
        dr = r"(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}-\d{2}-\d{2}|\w+\s+\d{1,2},?\s+\d{4})"

        if not data.invoice_date:
            for p in [rf"invoice\s+date\s*:?\s*({dr})",
                      rf"date\s+of\s+invoice\s*:?\s*({dr})",
                      rf"(?<!\w)date\s*:?\s*({dr})"]:
                m = re.search(p, text, re.IGNORECASE)
                if m:
                    data.invoice_date = self._parse_date(m.group(1))
                    break

        if not data.due_date:
            for p in [rf"(?:due\s+date|payment\s+due|pay\s+by)\s*:?\s*({dr})",
                      rf"due\s*:?\s*({dr})"]:
                m = re.search(p, text, re.IGNORECASE)
                if m:
                    data.due_date = self._parse_date(m.group(1))
                    break

        # Service period
        if not data.service_period_start:
            m = re.search(
                rf"(?:service|billing|period)\s+(?:from|start)\s*:?\s*({dr})",
                text, re.IGNORECASE)
            if m:
                data.service_period_start = self._parse_date(m.group(1))

        if not data.service_period_end:
            m = re.search(
                rf"(?:service|billing|period)\s+(?:to|end|through)\s*:?\s*({dr})",
                text, re.IGNORECASE)
            if m:
                data.service_period_end = self._parse_date(m.group(1))

    def _text_payment_terms(self, text: str, data: InvoiceData) -> None:
        """
        Parse payment terms → (payment_terms label, payment_terms_days).

        Priority order (most specific first):
          1. Early pay discount  2/10 Net 30, 1/15 Net 45
          2. Net N               Net 30, Net 60, Net 90
          3. Due on receipt      immediate / COD / upon receipt
          4. End of Month        EOM — days to month-end from invoice date
          5. Month Following     MFI / Net EOM — days to end of NEXT month
          6. Proximo / Prox      15th prox = 15th of next month
          7. Recurring cycles    weekly / biweekly / semimonthly / monthly /
                                 quarterly / semiannual / annual
          8. N days              "30 days", "45 days"
          9. Date-derived        compute from invoice_date + due_date if both present
        """
        import calendar
        tl = text.lower()

        # ── 1. Early pay discount: "2/10 Net 30" ─────────────────────────────
        m = re.search(r"(\d+)\s*/\s*(\d+)\s+net\s*(\d+)", tl)
        if m:
            data.early_pay_discount_pct  = float(m.group(1))
            data.early_pay_discount_days = int(m.group(2))
            data.payment_terms_days      = int(m.group(3))
            data.payment_terms           = f"{m.group(1)}/{m.group(2)} Net {m.group(3)}"
            return

        # ── 2. Net N ──────────────────────────────────────────────────────────
        m = re.search(r"\bnet\s*(\d+)\b", tl)
        if m:
            data.payment_terms      = f"Net {m.group(1)}"
            data.payment_terms_days = int(m.group(1))
            return

        # ── 3. Due on receipt / COD / immediate ──────────────────────────────
        if re.search(r"due\s+on\s+receipt|payable\s+on\s+receipt|upon\s+receipt"
                     r"|cash\s+on\s+delivery|\bcod\b|immediate(ly)?", tl):
            data.payment_terms      = "Due on Receipt"
            data.payment_terms_days = 0
            return

        # ── 4. End of Month ───────────────────────────────────────────────────
        if re.search(r"\bend\s+of\s+month\b|\beom\b", tl):
            try:
                inv      = date.fromisoformat(data.invoice_date[:10]) if data.invoice_date else date.today()
                last_day = calendar.monthrange(inv.year, inv.month)[1]
                days     = (date(inv.year, inv.month, last_day) - inv).days
            except Exception:
                days = 15
            data.payment_terms      = "End of Month"
            data.payment_terms_days = max(days, 0)
            return

        # ── 5. Month Following Invoice ────────────────────────────────────────
        if re.search(r"month\s+following|mfi|\bnet\s+eom\b|following\s+month", tl):
            try:
                inv = date.fromisoformat(data.invoice_date[:10]) if data.invoice_date else date.today()
                nm  = date(inv.year + 1, 1, 1) if inv.month == 12 else date(inv.year, inv.month + 1, 1)
                last_day = calendar.monthrange(nm.year, nm.month)[1]
                days = (date(nm.year, nm.month, last_day) - inv).days
            except Exception:
                days = 45
            data.payment_terms      = "Month Following Invoice"
            data.payment_terms_days = days
            return

        # ── 6. Proximo / Prox (Nth of next month) ────────────────────────────
        m = re.search(r"(\d+)(?:st|nd|rd|th)?\s+(?:prox(?:imo)?|of\s+next\s+month)", tl)
        if m:
            target_day = int(m.group(1))
            try:
                inv = date.fromisoformat(data.invoice_date[:10]) if data.invoice_date else date.today()
                nm  = date(inv.year + 1, 1, 1) if inv.month == 12 else date(inv.year, inv.month + 1, 1)
                max_day = calendar.monthrange(nm.year, nm.month)[1]
                due  = date(nm.year, nm.month, min(target_day, max_day))
                days = (due - inv).days
            except Exception:
                days = 30
            data.payment_terms      = f"{target_day}th Proximo"
            data.payment_terms_days = days
            return

        # ── 7. Recurring billing cycle ────────────────────────────────────────
        cycle_map = [
            (r"\bsemi[-\s]?monthly\b|twice\s+(?:a|per)\s+month",  15,  "Semimonthly"),
            (r"\bbi[-\s]?weekly\b|every\s+two\s+weeks|fortnightly", 14, "Biweekly"),
            (r"\bweekly\b|every\s+week",                            7,  "Weekly"),
            (r"\bquarterly\b|every\s+(?:3|three)\s+months",        90,  "Quarterly"),
            (r"\bsemi[-\s]?annual\b|every\s+(?:6|six)\s+months",  180,  "Semiannual"),
            (r"\bannual(ly)?\b|yearly\b|every\s+year",            365,  "Annual"),
            (r"\bmonthly\b|every\s+month|per\s+month",             30,  "Monthly"),
        ]
        for pattern, days, label in cycle_map:
            if re.search(pattern, tl):
                data.payment_terms      = label
                data.payment_terms_days = days
                return

        # ── 8. "N days" ───────────────────────────────────────────────────────
        m = re.search(r"\b(\d{1,3})\s+days?\b", tl)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 365:
                data.payment_terms      = f"{n} Days"
                data.payment_terms_days = n
                return

        # ── 9. Derive from explicit invoice_date + due_date ───────────────────
        if data.due_date and data.invoice_date and not data.payment_terms_days:
            try:
                inv  = date.fromisoformat(data.invoice_date[:10])
                due  = date.fromisoformat(data.due_date[:10])
                n    = (due - inv).days
                if 0 <= n <= 365:
                    data.payment_terms      = f"Net {n}"
                    data.payment_terms_days = n
            except (ValueError, TypeError):
                pass

    def _text_amounts(self, text: str, data: InvoiceData) -> None:
        """Last-resort text scan for key totals."""
        patterns = [
            (r"(?:total\s+(?:amount\s+)?due|balance\s+due|amount\s+due|grand\s+total|invoice\s+total|total)\s*:?\s*[\$£€]?\s*([\d,\.]+)",
             "total_amount"),
            (r"(?:subtotal|sub\s+total)\s*:?\s*[\$£€]?\s*([\d,\.]+)",
             "subtotal"),
            (r"(?:tax|vat|gst)\s*:?\s*[\$£€]?\s*([\d,\.]+)",
             "tax_amount"),
            (r"(?:amount\s+paid|payment\s+received)\s*:?\s*[\$£€]?\s*([\d,\.]+)",
             "amount_paid"),
            (r"(?:balance\s+(?:due|remaining)|now\s+due|please\s+pay)\s*:?\s*[\$£€]?\s*([\d,\.]+)",
             "amount_due"),
        ]
        for pattern, field_name in patterns:
            if getattr(data, field_name) is None:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    v = self._parse(m.group(1))
                    if v and v > 0:
                        setattr(data, field_name, v)

    # ── Post-processing ──────────────────────────────────────────────────────

    def _derive_payment_status(self, data: InvoiceData) -> None:
        today = date.today()

        # Amount due / amount paid derivation
        if data.total_amount and data.amount_paid is not None:
            remaining = data.total_amount - data.amount_paid
            if remaining <= 0:
                data.payment_status = "paid"
                data.amount_due     = 0.0
                return
            else:
                data.amount_due     = round(remaining, 2)
                data.payment_status = "partial" if data.amount_paid > 0 else "due"

        # "PAID" text signal
        if re.search(r"\bPAID\b|\bCLOSED\b", str(data.__dict__)):
            data.payment_status = "paid"

        # Days calculations
        if data.due_date:
            try:
                due = date.fromisoformat(data.due_date[:10])
                delta = (due - today).days
                if delta < 0:
                    data.is_overdue     = data.payment_status != "paid"
                    data.days_overdue   = abs(delta)
                    if data.payment_status not in ("paid", "partial"):
                        data.payment_status = "overdue"
                else:
                    data.days_until_due = delta
                    if data.payment_status == "unknown":
                        data.payment_status = "due"
            except ValueError:
                pass
        elif data.invoice_date and data.payment_terms_days is not None:
            try:
                inv = date.fromisoformat(data.invoice_date[:10])
                due = inv + timedelta(days=data.payment_terms_days)
                delta = (due - today).days
                data.due_date = due.isoformat()
                if delta < 0:
                    data.is_overdue     = data.payment_status != "paid"
                    data.days_overdue   = abs(delta)
                    if data.payment_status not in ("paid", "partial"):
                        data.payment_status = "overdue"
                else:
                    data.days_until_due = delta
            except ValueError:
                pass

        if data.amount_due is None and data.total_amount:
            data.amount_due = data.total_amount

    def _derive_category_totals(self, data: InvoiceData) -> None:
        labor     = sum(li.amount for li in data.line_items if li.category == "labor")
        materials = sum(li.amount for li in data.line_items if li.category == "materials")
        services  = sum(li.amount for li in data.line_items if li.category == "services")
        data.labor_total     = round(labor,     2) if labor     else None
        data.materials_total = round(materials, 2) if materials else None
        data.services_total  = round(services,  2) if services  else None

    def _derive_tax_rate(self, data: InvoiceData) -> None:
        if data.tax_rate:
            data.effective_tax_rate = data.tax_rate
        elif data.tax_amount and data.subtotal and data.subtotal > 0:
            data.effective_tax_rate = round(data.tax_amount / data.subtotal * 100, 2)

    def _score(self, data: InvoiceData) -> None:
        critical = [
            ("vendor_name",   data.vendor_name),
            ("total_amount",  data.total_amount),
            ("invoice_date",  data.invoice_date),
            ("invoice_number",data.invoice_number),
        ]
        found = sum(1 for _, v in critical if v is not None)
        data.extraction_confidence = round(found / len(critical), 2)
        data.missing_fields        = [n for n, v in critical if v is None]

    # ── Entry point ──────────────────────────────────────────────────────────

    def extract(self, raw_text: str, tables: Optional[List] = None) -> InvoiceData:
        text   = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
        tables = tables or []
        data   = InvoiceData()

        self._scan_tables(tables, data)
        self._text_invoice_meta(text, data)
        self._text_parties(text, data)
        self._text_dates(text, data)
        self._text_payment_terms(text, data)

        if not data.total_amount:
            self._text_amounts(text, data)

        data.invoice_subtype = self._detect_subtype(text, data)
        self._derive_payment_status(data)
        self._derive_category_totals(data)
        self._derive_tax_rate(data)
        self._score(data)

        logger.info(
            f"InvoiceExtractor: confidence={data.extraction_confidence:.0%} "
            f"subtype={data.invoice_subtype} total={data.total_amount} "
            f"status={data.payment_status} due={data.due_date}"
        )
        return data


# ─── Intelligence Layer ───────────────────────────────────────────────────────

class InvoiceIntelligence:
    """
    Advisory layer — answers the questions AP teams and individuals actually have:
      1. Do I need to pay this? When? How much?
      2. Is this invoice correct? Any red flags?
      3. Does this match a PO?
      4. Have I seen this invoice before?
      5. How much have I paid this vendor YTD?
      6. Should I take the early payment discount?
    """

    def analyze(self, data: InvoiceData,
                prior_invoices: Optional[List[Dict]] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        result.update(self._payment_summary(data))
        result.update(self._amount_breakdown(data))
        result.update(self._tax_analysis(data))
        result.update(self._duplicate_signals(data, prior_invoices or []))
        result.update(self._vendor_spend(data, prior_invoices or []))
        result.update(self._early_pay_analysis(data))
        result["action_plan"]      = self._actions(data, result)
        result["alerts"]           = self._alerts(data, result)
        result["filing_checklist"] = self._checklist(data)
        return result

    def _payment_summary(self, data: InvoiceData) -> Dict:
        urgency = "normal"
        urgency_msg = ""

        if data.payment_status == "overdue":
            urgency     = "critical"
            urgency_msg = f"OVERDUE by {data.days_overdue} days — pay immediately to avoid late fees"
        elif data.payment_status == "paid":
            urgency     = "resolved"
            urgency_msg = "Invoice has been paid in full"
        elif data.days_until_due is not None:
            if data.days_until_due == 0:
                urgency     = "high"
                urgency_msg = "Due TODAY"
            elif data.days_until_due <= 3:
                urgency     = "high"
                urgency_msg = f"Due in {data.days_until_due} days — pay now"
            elif data.days_until_due <= 7:
                urgency     = "medium"
                urgency_msg = f"Due in {data.days_until_due} days"
            else:
                urgency_msg = f"Due in {data.days_until_due} days ({data.due_date})"

        return {
            "payment_status":   data.payment_status,
            "payment_urgency":  urgency,
            "payment_message":  urgency_msg,
            "amount_due":       data.amount_due,
            "total_amount":     data.total_amount,
            "days_until_due":   data.days_until_due,
            "days_overdue":     data.days_overdue,
            "is_overdue":       data.is_overdue,
        }

    def _amount_breakdown(self, data: InvoiceData) -> Dict:
        total     = data.total_amount or 1
        subtotal  = data.subtotal     or 0
        tax       = data.tax_amount   or 0
        shipping  = data.shipping_amount or 0
        discount  = data.discount_amount or 0

        segments = []
        if subtotal > 0:
            segments.append({"label": "Subtotal",  "amount": subtotal,
                             "pct": round(subtotal / total * 100, 1), "color": "#4F81BD"})
        if tax > 0:
            segments.append({"label": "Tax",       "amount": tax,
                             "pct": round(tax / total * 100, 1),      "color": "#C0504D"})
        if shipping > 0:
            segments.append({"label": "Shipping",  "amount": shipping,
                             "pct": round(shipping / total * 100, 1), "color": "#F59E0B"})
        if discount > 0:
            segments.append({"label": "Discount",  "amount": -discount,
                             "pct": round(discount / total * 100, 1), "color": "#10B981"})

        # Line item category breakdown
        category_map: Dict[str, float] = {}
        for li in data.line_items:
            if not li.is_tax and not li.is_discount:
                category_map[li.category] = category_map.get(li.category, 0) + li.amount

        category_breakdown = [
            {"label": k.replace("_", " ").title(), "amount": round(v, 2),
             "pct":   round(v / total * 100, 1)}
            for k, v in sorted(category_map.items(), key=lambda x: -x[1])
        ]

        return {
            "amount_breakdown":    segments,
            "category_breakdown":  category_breakdown,
        }

    def _tax_analysis(self, data: InvoiceData) -> Dict:
        if not data.tax_amount:
            return {"tax_analysis": {"has_tax": False}}

        total    = data.total_amount or 1
        tax_pct  = data.effective_tax_rate or round(data.tax_amount / total * 100, 2)

        # Flag unusual tax rates
        notes = []
        if tax_pct > 15:
            notes.append(f"Tax rate {tax_pct:.1f}% is higher than typical — verify")
        elif tax_pct > 0 and tax_pct < 1:
            notes.append("Tax amount seems low — confirm tax jurisdiction")

        return {
            "tax_analysis": {
                "has_tax":       True,
                "tax_amount":    data.tax_amount,
                "tax_rate":      tax_pct,
                "tax_type":      self._detect_tax_type(data),
                "notes":         notes,
            }
        }

    def _detect_tax_type(self, data: InvoiceData) -> str:
        text = " ".join(li.description for li in data.line_items).lower()
        if "vat"  in text: return "VAT"
        if "gst"  in text: return "GST"
        if "hst"  in text: return "HST"
        if "pst"  in text: return "PST"
        return "Sales Tax"

    def _duplicate_signals(self, data: InvoiceData,
                           prior_invoices: List[Dict]) -> Dict:
        """
        Check for duplicate invoice signals.
        Exact duplicate: same invoice_number + same vendor.
        Fuzzy duplicate: same vendor + same amount within 7 days.
        """
        if not prior_invoices:
            return {"duplicate_check": {"status": "no_history", "duplicates": []}}

        exact    = []
        fuzzy    = []
        inv_date = data.invoice_date[:10] if data.invoice_date else None

        for prior in prior_invoices:
            # Skip self
            if prior.get("document_id") == data.__dict__.get("document_id"):
                continue

            # Exact: same invoice number + same vendor
            if (data.invoice_number
                    and prior.get("invoice_number") == data.invoice_number
                    and (prior.get("vendor_name", "").lower()
                         == (data.vendor_name or "").lower())):
                exact.append({
                    "document_id":     prior.get("document_id"),
                    "invoice_number":  prior.get("invoice_number"),
                    "amount":          prior.get("total_amount"),
                    "date":            prior.get("invoice_date"),
                    "match_type":      "exact",
                })
                continue

            # Fuzzy: same vendor + same amount ± $0.01 within 30 days
            if (data.vendor_name
                    and prior.get("vendor_name", "").lower() == data.vendor_name.lower()
                    and data.total_amount
                    and abs((prior.get("total_amount") or 0) - data.total_amount) < 0.02):
                try:
                    if inv_date and prior.get("invoice_date"):
                        d1  = date.fromisoformat(inv_date)
                        d2  = date.fromisoformat(prior["invoice_date"][:10])
                        gap = abs((d1 - d2).days)
                        if gap <= 30:
                            fuzzy.append({
                                "document_id":  prior.get("document_id"),
                                "amount":        prior.get("total_amount"),
                                "date":          prior.get("invoice_date"),
                                "days_apart":    gap,
                                "match_type":    "fuzzy",
                            })
                except (ValueError, AttributeError):
                    pass

        status = "clean"
        if exact:
            status = "exact_duplicate"
        elif fuzzy:
            status = "possible_duplicate"

        return {
            "duplicate_check": {
                "status":     status,
                "duplicates": exact + fuzzy,
                "exact_count": len(exact),
                "fuzzy_count": len(fuzzy),
            }
        }

    def _vendor_spend(self, data: InvoiceData,
                      prior_invoices: List[Dict]) -> Dict:
        if not data.vendor_name or not prior_invoices:
            return {"vendor_spend": {"vendor": data.vendor_name, "history": []}}

        vendor_l = data.vendor_name.lower()
        matched  = [
            p for p in prior_invoices
            if (p.get("vendor_name") or "").lower() == vendor_l
        ]

        ytd_year  = date.today().year
        ytd_total = 0.0
        monthly: Dict[str, float] = {}
        invoice_count = 0

        for p in matched:
            amt = p.get("total_amount") or 0
            inv_date = p.get("invoice_date", "")
            if inv_date and str(ytd_year) in inv_date:
                ytd_total += amt
                try:
                    mo = inv_date[:7]  # "2024-03"
                    monthly[mo] = monthly.get(mo, 0) + amt
                except Exception:
                    pass
            invoice_count += 1

        ytd_total = round(ytd_total + (data.total_amount or 0), 2)

        monthly_trend = [
            {"month": k, "amount": round(v, 2)}
            for k, v in sorted(monthly.items())
        ]

        avg_invoice = round(ytd_total / max(invoice_count + 1, 1), 2)

        return {
            "vendor_spend": {
                "vendor":          data.vendor_name,
                "ytd_total":       ytd_total,
                "invoice_count":   invoice_count + 1,
                "avg_invoice":     avg_invoice,
                "monthly_trend":   monthly_trend,
                "ytd_year":        ytd_year,
            }
        }

    def _early_pay_analysis(self, data: InvoiceData) -> Dict:
        if not data.early_pay_discount_pct or not data.total_amount:
            return {"early_pay": None}

        savings    = round(data.total_amount * data.early_pay_discount_pct / 100, 2)
        pay_amount = round(data.total_amount - savings, 2)

        return {
            "early_pay": {
                "available":         True,
                "discount_pct":      data.early_pay_discount_pct,
                "discount_days":     data.early_pay_discount_days,
                "savings":           savings,
                "pay_amount":        pay_amount,
                "full_amount":       data.total_amount,
                "recommendation":    (
                    f"Pay within {data.early_pay_discount_days} days to save "
                    f"${savings:,.2f} ({data.early_pay_discount_pct}% discount)"
                ),
            }
        }

    def _actions(self, data: InvoiceData, result: Dict) -> List[Dict]:
        actions = []

        # Overdue — highest priority
        if data.is_overdue:
            actions.append({
                "priority": "high",
                "category": "payment",
                "action":   f"Pay overdue invoice immediately",
                "detail":   (
                    f"${data.amount_due:,.2f} is {data.days_overdue} days overdue. "
                    f"Contact {data.vendor_name or 'vendor'} if disputed."
                ),
            })

        # Due soon
        elif data.days_until_due is not None and data.days_until_due <= 5:
            actions.append({
                "priority": "high",
                "category": "payment",
                "action":   f"Schedule payment — due in {data.days_until_due} days",
                "detail":   f"${data.amount_due:,.2f} due to {data.vendor_name or 'vendor'} by {data.due_date}",
            })

        # Early pay discount
        ep = result.get("early_pay")
        if ep and ep.get("available") and data.payment_status != "paid":
            actions.append({
                "priority": "medium",
                "category": "savings",
                "action":   f"Take early payment discount — save ${ep['savings']:,.2f}",
                "detail":   ep["recommendation"],
                "monthly_impact": ep["savings"],
            })

        # Duplicate warning
        dup = result.get("duplicate_check", {})
        if dup.get("status") == "exact_duplicate":
            actions.append({
                "priority": "high",
                "category": "verification",
                "action":   "Verify — possible duplicate invoice",
                "detail":   f"Invoice #{data.invoice_number} from {data.vendor_name} matches a prior invoice. Do not pay until verified.",
            })
        elif dup.get("status") == "possible_duplicate":
            actions.append({
                "priority": "medium",
                "category": "verification",
                "action":   "Review for duplicate — same vendor, same amount",
                "detail":   "A similar invoice from this vendor exists. Confirm this is a new charge.",
            })

        # PO match
        if not data.po_number and data.invoice_subtype == "b2b":
            actions.append({
                "priority": "low",
                "category": "compliance",
                "action":   "Request PO number from vendor",
                "detail":   "No purchase order reference on this invoice — required for 3-way matching.",
            })

        # File for taxes
        if data.tax_amount and data.invoice_subtype in ("b2b", "contractor"):
            actions.append({
                "priority": "low",
                "category": "tax",
                "action":   "Save for tax records",
                "detail":   f"${data.tax_amount:,.2f} in tax — deductible as business expense. File with accounts payable records.",
            })

        return sorted(actions, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["priority"]])

    def _alerts(self, data: InvoiceData, result: Dict) -> List[Dict]:
        alerts = []

        if data.is_overdue:
            alerts.append({
                "type":    "error",
                "title":   f"Overdue {data.days_overdue} Days",
                "message": f"Payment of ${data.amount_due:,.2f} is past due. Late fees may apply.",
            })

        dup = result.get("duplicate_check", {})
        if dup.get("exact_count", 0) > 0:
            alerts.append({
                "type":    "error",
                "title":   "Exact Duplicate Detected",
                "message": f"Invoice #{data.invoice_number} appears to match a previously processed invoice.",
            })
        elif dup.get("fuzzy_count", 0) > 0:
            alerts.append({
                "type":    "warning",
                "title":   "Possible Duplicate",
                "message": "Similar invoice from this vendor found — verify before paying.",
            })

        tax = result.get("tax_analysis", {})
        if tax.get("notes"):
            for note in tax["notes"]:
                alerts.append({"type": "warning", "title": "Tax Rate Note", "message": note})

        if not data.invoice_number:
            alerts.append({
                "type":    "warning",
                "title":   "No Invoice Number",
                "message": "Invoice number not found — request from vendor for proper record-keeping.",
            })

        if data.invoice_subtype == "b2b" and not data.vendor_tax_id:
            alerts.append({
                "type":    "info",
                "title":   "Vendor Tax ID Missing",
                "message": "No EIN/VAT number found — required for 1099 reporting if vendor is a contractor.",
            })

        return alerts

    def _checklist(self, data: InvoiceData) -> List[str]:
        items = [
            "✅ Verify the invoice amount matches the purchase order or contract",
            "✅ Confirm goods/services were received before approving payment",
        ]

        if data.invoice_number:
            items.append(f"✅ File invoice #{data.invoice_number} in your AP records")

        if data.po_number:
            items.append(f"✅ 3-way match: invoice #{data.invoice_number} ↔ PO #{data.po_number} ↔ receipt")

        if data.tax_amount:
            items.append(f"✅ Confirm ${data.tax_amount:,.2f} tax is correct for your jurisdiction")

        if data.invoice_subtype == "contractor":
            items.append("✅ Issue 1099-NEC if this contractor is paid $600+ this year")
            items.append("✅ Confirm W-9 is on file for this contractor")

        if data.invoice_subtype == "medical":
            items.append("✅ Cross-reference against your Explanation of Benefits (EOB)")
            items.append("✅ Verify insurance processed this claim before paying")

        items.append("✅ Keep this invoice for 7 years (IRS audit window)")
        return items


# ─── Analyzer (called by DocumentAnalysisNode) ───────────────────────────────

class InvoiceAnalyzer:
    """
    Orchestrator. Called by UniDocVerse DocumentAnalysisNode.

    Register in analyzer_registry.py:
        "invoice": InvoiceAnalyzer()

    Register in document_registry.py:
        "invoice": {"parser": None, "analyzer": InvoiceAnalyzer}

    Optional: pass prior_invoices from DB for duplicate detection + vendor spend.
    """

    def analyze(self, file_path: str, text: str = "",
                metadata: Optional[Dict] = None) -> Dict[str, Any]:
        logger.info(f"📊 InvoiceAnalyzer: {file_path}")
        metadata = metadata or {}

        # Flatten tables
        raw_tables: List = []
        for t in metadata.get("tables", []):
            raw_tables.extend(t.get("data", []))

        # Prior invoices for duplicate / spend analysis
        prior_invoices: List[Dict] = metadata.get("prior_invoices", [])

        # Extract
        extractor = InvoiceExtractor()
        data      = extractor.extract(text, raw_tables)

        # Intelligence
        intelligence = InvoiceIntelligence().analyze(data, prior_invoices)

        # Assemble result
        result: Dict[str, Any] = {
            "type":                   "invoice",
            "has_advanced_analytics": True,
        }
        result.update(data.to_dict())
        result.update(intelligence)
        result["hero_metrics"]  = self._hero_metrics(data, intelligence)
        result["insights"]      = self._insights(data, intelligence)
        result["ui_config"]     = self._ui_config(data)

        logger.info(
            f"✅ InvoiceAnalyzer: subtype={data.invoice_subtype} "
            f"total={data.total_amount} status={data.payment_status} "
            f"confidence={data.extraction_confidence:.0%}"
        )
        return result

    def _hero_metrics(self, data: InvoiceData, intel: Dict) -> Dict:
        return {
            "total_amount":   data.total_amount,
            "amount_due":     data.amount_due,
            "tax_amount":     data.tax_amount,
            "payment_status": data.payment_status,
            "days_until_due": data.days_until_due,
            "days_overdue":   data.days_overdue,
            "vendor_name":    data.vendor_name,
            "invoice_number": data.invoice_number,
            "due_date":       data.due_date,
            "vendor_ytd":     intel.get("vendor_spend", {}).get("ytd_total"),
        }

    def _insights(self, data: InvoiceData, intel: Dict) -> List[Dict]:
        insights = []

        # Payment urgency
        msg = intel.get("payment_message", "")
        if msg:
            insights.append({
                "title":       "Payment Status",
                "description": msg,
                "category":    "payment",
            })

        # Vendor spend
        vs = intel.get("vendor_spend", {})
        if vs.get("ytd_total") and vs.get("invoice_count", 0) > 1:
            insights.append({
                "title":       "Vendor Spend",
                "description": (
                    f"You've paid {data.vendor_name} ${vs['ytd_total']:,.2f} across "
                    f"{vs['invoice_count']} invoices this year "
                    f"(avg ${vs['avg_invoice']:,.2f}/invoice)."
                ),
                "category": "spend",
            })

        # Early pay
        ep = intel.get("early_pay")
        if ep and ep.get("available"):
            insights.append({
                "title":       "Early Payment Discount Available",
                "description": ep["recommendation"],
                "category":    "savings",
            })

        # Line item summary
        if data.line_items:
            insights.append({
                "title":       "Invoice Breakdown",
                "description": (
                    f"{len(data.line_items)} line items totaling "
                    f"${data.subtotal or data.total_amount:,.2f} before tax."
                ),
                "category": "summary",
            })

        return insights

    def _ui_config(self, data: InvoiceData) -> Dict:
        return {
            "document_type": "invoice",
            "hero_metrics": [
                {"key": "total_amount",   "label": "Invoice Total",    "format": "currency", "is_hero": True},
                {"key": "amount_due",     "label": "Amount Due",       "format": "currency", "is_hero": True},
                {"key": "payment_status", "label": "Status",           "format": "text",     "is_hero": True},
                {"key": "due_date",       "label": "Due Date",         "format": "text",     "is_hero": True},
                {"key": "tax_amount",     "label": "Tax",              "format": "currency", "is_hero": False},
                {"key": "days_until_due", "label": "Days Until Due",   "format": "number",   "is_hero": False},
                {"key": "vendor_name",    "label": "Vendor",           "format": "text",     "is_hero": False},
                {"key": "invoice_number", "label": "Invoice #",        "format": "text",     "is_hero": False},
                {"key": "vendor_ytd",     "label": "Vendor YTD Spend", "format": "currency", "is_hero": False},
            ],
            "charts": [
                {
                    "id":        "amount_breakdown",
                    "type":      "donut",
                    "title":     "Invoice Breakdown",
                    "data_path": "amount_breakdown",
                    "label_key": "label",
                    "value_key": "amount",
                },
                {
                    "id":        "category_breakdown",
                    "type":      "bar",
                    "title":     "Charges by Category",
                    "data_path": "category_breakdown",
                    "x_key":     "label",
                    "y_key":     "amount",
                    "color":     "#4F81BD",
                },
                {
                    "id":        "vendor_monthly",
                    "type":      "bar",
                    "title":     "Monthly Vendor Spend",
                    "data_path": "vendor_spend.monthly_trend",
                    "x_key":     "month",
                    "y_key":     "amount",
                    "color":     "#9BBB59",
                },
            ],
            "tables": [
                {
                    "id":        "line_items_table",
                    "title":     "Line Items",
                    "data_path": "line_items",
                    "columns": [
                        {"key": "description", "label": "Description"},
                        {"key": "quantity",    "label": "Qty",      "format": "number"},
                        {"key": "unit_price",  "label": "Rate",     "format": "currency"},
                        {"key": "amount",      "label": "Amount",   "format": "currency"},
                        {"key": "category",    "label": "Category"},
                    ],
                },
                {
                    "id":        "filing_checklist",
                    "title":     "Filing Checklist",
                    "data_path": "filing_checklist",
                },
            ],
            "sections": [
                {"id": "overview",   "title": "Invoice Summary",      "type": "metrics",  "metrics": ["total_amount", "amount_due", "payment_status", "due_date"]},
                {"id": "breakdown",  "title": "Amount Breakdown",     "type": "charts",   "charts":  ["amount_breakdown", "category_breakdown"]},
                {"id": "line_items", "title": "Line Items & Charges",  "type": "tables",  "tables":  ["line_items_table"]},
                {"id": "vendor",     "title": "Vendor Spend History",  "type": "charts",  "charts":  ["vendor_monthly"]},
                {"id": "actions",    "title": "Action Plan",           "type": "actions", "data_path": "action_plan"},
                {"id": "checklist",  "title": "Filing Checklist",      "type": "tables",  "tables":  ["filing_checklist"]},
            ],
        }