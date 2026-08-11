# app/services/parsers/universal_expense_report_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalExpenseReportParser(BaseParser):
    """
    Heuristic parser for expense reports and expense receipts.
    Returns a dict with keys commonly used by financial/expense analyzers:
      - report_id
      - employee_name
      - employee_id
      - department
      - date_submitted
      - period_start
      - period_end
      - currency
      - total_amount
      - vendor
      - payment_method
      - expense_category
      - line_items: list of {date, description, category, quantity, unit_price, amount, currency}
      - receipts: list of receipt references (text snippets or filenames if present)
      - approvals: list of {approver, title, date, status}
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["report_id"] = self._extract_report_id()
            parsed["employee_name"] = self._extract_employee_name()
            parsed["employee_id"] = self._extract_employee_id()
            parsed["department"] = self._extract_department()
            parsed["date_submitted"] = self._extract_date_submitted()
            parsed["period_start"], parsed["period_end"] = self._extract_period()
            parsed["currency"] = self._extract_currency()
            parsed["total_amount"] = self._extract_total_amount(parsed["currency"])
            parsed["vendor"] = self._extract_vendor()
            parsed["payment_method"] = self._extract_payment_method()
            parsed["expense_category"] = self._extract_expense_category()
            parsed["line_items"] = self._extract_line_items(parsed["currency"])
            parsed["receipts"] = self._extract_receipts()
            parsed["approvals"] = self._extract_approvals()
            return parsed
        except Exception:
            logger.exception("UniversalExpenseParser.parse failed")
            return {
                "report_id": None,
                "employee_name": None,
                "employee_id": None,
                "department": None,
                "date_submitted": None,
                "period_start": None,
                "period_end": None,
                "currency": None,
                "total_amount": 0.0,
                "vendor": None,
                "payment_method": None,
                "expense_category": None,
                "line_items": [],
                "receipts": [],
                "approvals": [],
            }

    # ---------- top-level extractors ----------

    def _extract_report_id(self) -> Optional[str]:
        patterns = [
            r"Expense\s*(?:Report|Rpt|Rpt\.)\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Report\s*ID[:\s]*([A-Za-z0-9\-_/]+)",
            r"Reference[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: filename
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_employee_name(self) -> Optional[str]:
        patterns = [
            r"Employee\s+Name[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"Submitted\s+by[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
            r"Name[:\s]*([A-Z][A-Za-z ,.'\-]{3,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: top lines
        top_lines = [ln.strip() for ln in self.text.splitlines()[:8] if ln.strip()]
        for ln in top_lines:
            if re.match(r"^[A-Z][A-Za-z ,.'\-]{3,120}$", ln):
                return self.normalize_whitespace(ln)
        return None

    def _extract_employee_id(self) -> Optional[str]:
        patterns = [
            r"Employee\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Emp\s*ID[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        return None

    def _extract_department(self) -> Optional[str]:
        patterns = [
            r"Department[:\s]*([A-Za-z0-9 &,\-]{2,80})",
            r"Dept[:\s]*([A-Za-z0-9 &,\-]{2,80})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        return None

    def _extract_date_submitted(self) -> Optional[str]:
        patterns = [
            r"Date\s+Submitted[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Submitted[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # fallback: first date in document
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)", self.text)
        if m:
            parsed = self.parse_date(m.group(1))
            if parsed:
                return parsed
        return None

    def _extract_period(self) -> (Optional[str], Optional[str]):
        # look for "Period: start - end" or "For period ... to ..."
        m = re.search(r"Period[:\s]*([A-Za-z0-9 ,\/\-]+?)(?:\n|$)", self.text, flags=re.IGNORECASE)
        if m:
            txt = m.group(1)
            parts = re.split(r"\bto\b|-", txt, flags=re.IGNORECASE)
            if len(parts) >= 2:
                return self.parse_date(parts[0].strip()), self.parse_date(parts[1].strip())
        m2 = re.search(r"for the period\s+([A-Za-z0-9 ,\/\-]+?)\s+(?:to|-)\s+([A-Za-z0-9 ,\/\-]+)", self.text, flags=re.IGNORECASE)
        if m2:
            return self.parse_date(m2.group(1).strip()), self.parse_date(m2.group(2).strip())
        return None, None

    def _extract_currency(self) -> Optional[str]:
        m = re.search(r"\b(USD|EUR|GBP|AUD|CAD|JPY|CHF|CNY)\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(1).upper()
        if "$" in self.text:
            return "USD"
        if "€" in self.text:
            return "EUR"
        return None

    def _extract_total_amount(self, currency: Optional[str]) -> float:
        # look for "Total", "Amount", "Total Amount"
        patterns = [
            r"Total\s+Amount[:\s]*([\$\d,\.KkMm]+)",
            r"Total[:\s]*([\$\d,\.KkMm]+)",
            r"Amount\s+Due[:\s]*([\$\d,\.KkMm]+)",
            r"Grand\s+Total[:\s]*([\$\d,\.KkMm]+)",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)
        # fallback: last money-like token
        m = re.findall(r"(\$?\s?[\d{1,3},]+(?:\.\d+)?\s?(?:k|K|m|M)?)", self.text)
        if m:
            return self.safe_float(m[-1])
        return 0.0

    def _extract_vendor(self) -> Optional[str]:
        patterns = [
            r"Vendor[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Merchant[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Payee[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Supplier[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: look for "To:" near top
        top = "\n".join(self.text.splitlines()[:12])
        m = re.search(r"To[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})", top)
        if m:
            return m.group(1).strip()
        return None

    def _extract_payment_method(self) -> Optional[str]:
        patterns = [
            r"Payment\s+Method[:\s]*(Credit Card|Corporate Card|Personal Card|Cash|Check|Bank Transfer|Wire|ACH)",
            r"Paid\s+by[:\s]*(Credit Card|Corporate Card|Personal Card|Cash|Check|Bank Transfer|Wire|ACH)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # look for card last 4 digits
        m = re.search(r"(?:Card|Visa|Mastercard|Amex)[^\d]*(\d{4})", self.text, flags=re.IGNORECASE)
        if m:
            return f"Card ****{m.group(1)}"
        return None

    def _extract_expense_category(self) -> Optional[str]:
        patterns = [
            r"Expense\s+Category[:\s]*([A-Za-z &,\-]{3,80})",
            r"Category[:\s]*([A-Za-z &,\-]{3,80})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: infer from top keywords
        if re.search(r"\b(meal|lodging|hotel|airfare|taxi|uber|lyft|mileage|parking|fuel|gas)\b", self.text, flags=re.IGNORECASE):
            return "Travel & Entertainment"
        return None

    # ---------- line items extraction ----------

    def _extract_line_items(self, currency: Optional[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []

        # 1) Table-like rows: Date | Description | Category | Qty | Unit Price | Amount
        table_rows = re.findall(
            r"^(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{4}-\d{2}-\d{2})\s*(?:\||\-|\s{2,})\s*([^\|\n]{5,120}?)\s*(?:\||\-|\s{2,})\s*([A-Za-z &,\-]{2,40})?\s*(?:\||\-|\s{2,})?\s*([\d\.]+)?\s*(?:\||\-|\s{2,})?\s*([\$\d,\.KkMm]+)",
            self.text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if table_rows:
            for r in table_rows:
                date_raw = r[0].strip()
                desc = self.normalize_whitespace(r[1])
                category = r[2].strip() if r[2] else None
                qty = self.safe_float(r[3]) if r[3] else 1.0
                amount = self.safe_float(r[4])
                date_parsed = self.parse_date(date_raw)
                unit_price = (amount / qty) if qty else amount
                items.append({
                    "date": date_parsed,
                    "description": desc,
                    "category": category,
                    "quantity": qty,
                    "unit_price": round(unit_price, 2),
                    "amount": round(amount, 2),
                    "currency": currency,
                })
            if items:
                return items

        # 2) Lines like "2024-01-05 Taxi - 1 x $25 = $25"
        line_pattern = re.compile(
            r"(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\s*[-:]\s*(?P<desc>.+?)\s*[-–—]\s*(?P<qty>[\d\.]+)\s*[xX]\s*([\$]?[\d,\.KkMm]+)\s*(?:=|:)\s*([\$]?[\d,\.KkMm]+)",
            flags=re.IGNORECASE,
        )
        for m in line_pattern.finditer(self.text):
            date_parsed = self.parse_date(m.group("date"))
            desc = self.normalize_whitespace(m.group("desc"))
            qty = self.safe_float(m.group("qty"))
            unit_price = self.safe_float(m.group(4))
            amount = self.safe_float(m.group(5))
            items.append({
                "date": date_parsed,
                "description": desc,
                "category": None,
                "quantity": qty,
                "unit_price": round(unit_price, 2),
                "amount": round(amount, 2),
                "currency": currency,
            })
        if items:
            return items

        # 3) Bulleted lists under "Expenses", "Line Items", "Details"
        blocks = self.find_all_blocks(r"^\s*(?:Expenses|Line Items|Details|Items)[:\s]*", end_regex=r"^\s*(?:Total|Grand Total|Summary|Notes)")
        if blocks:
            for blk in blocks:
                lines = [ln.strip() for ln in re.split(r"\n|;|•|-•|•", blk) if ln.strip()]
                for ln in lines:
                    # try to extract amount at end
                    m = re.search(r"([\$]?[\d,\.KkMm]+)\s*$", ln)
                    amount = self.safe_float(m.group(1)) if m else 0.0
                    # try to extract date at start
                    m2 = re.match(r"^(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\b", ln)
                    date_parsed = self.parse_date(m2.group("date")) if m2 else None
                    # qty/unit price
                    m3 = re.search(r"([\d\.]+)\s*[xX]\s*([\$]?[\d,\.KkMm]+)", ln)
                    if m3:
                        qty = self.safe_float(m3.group(1))
                        unit_price = self.safe_float(m3.group(2))
                    else:
                        qty = 1.0
                        unit_price = amount
                    # category heuristics
                    cat = None
                    if re.search(r"\b(meal|dinner|lunch|breakfast)\b", ln, flags=re.IGNORECASE):
                        cat = "Meals"
                    elif re.search(r"\b(hotel|lodging|accommodation)\b", ln, flags=re.IGNORECASE):
                        cat = "Lodging"
                    elif re.search(r"\b(airfare|flight|ticket)\b", ln, flags=re.IGNORECASE):
                        cat = "Airfare"
                    items.append({
                        "date": date_parsed,
                        "description": self.normalize_whitespace(re.sub(r"([\$]?[\d,\.KkMm]+)\s*$", "", ln)),
                        "category": cat,
                        "quantity": qty,
                        "unit_price": round(unit_price, 2),
                        "amount": round(amount, 2),
                        "currency": currency,
                    })
            if items:
                return items

        # 4) Fallback: find any money-bearing lines and create minimal items
        lines = [ln.strip() for ln in self.text.splitlines() if ln.strip()]
        for ln in lines:
            m = re.search(r"([\$]?[\d,\.KkMm]+)", ln)
            if m:
                amount = self.safe_float(m.group(1))
                desc = re.sub(r"([\$]?[\d,\.KkMm]+)", "", ln).strip()
                if desc and amount:
                    items.append({
                        "date": None,
                        "description": self.normalize_whitespace(desc)[:200],
                        "category": None,
                        "quantity": 1.0,
                        "unit_price": round(amount, 2),
                        "amount": round(amount, 2),
                        "currency": currency,
                    })
        return items

    # ---------- receipts & approvals ----------

    def _extract_receipts(self) -> List[str]:
        # Look for "Receipt" headings or filenames (e.g., IMG_1234.jpg)
        receipts: List[str] = []
        blocks = self.find_all_blocks(r"^\s*(?:Receipt|Receipts|Attached Receipts)[:\s]*", end_regex=r"^\s*(?:Total|Summary|Notes|Approvals)")
        if blocks:
            parts = re.split(r"\n|;|,", blocks[0])
            for p in parts:
                p = p.strip()
                if p:
                    receipts.append(p)
        # find image-like filenames
        imgs = re.findall(r"([A-Za-z0-9_\-]{3,50}\.(?:jpg|jpeg|png|pdf))", self.text, flags=re.IGNORECASE)
        for i in imgs:
            if i not in receipts:
                receipts.append(i)
        return receipts

    def _extract_approvals(self) -> List[Dict[str, Any]]:
        approvals: List[Dict[str, Any]] = []
        blocks = self.find_all_blocks(r"^\s*(?:Approved by|Approvals|Approval)[:\s]*", end_regex=r"^\s*(?:Notes|Signature|Date|Total)")
        for blk in blocks:
            lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
            for ln in lines:
                m = re.search(r"(?P<name>[A-Z][A-Za-z ,.'\-]{2,80})(?:[,|-]\s*(?P<title>[A-Za-z &\-/]{2,80}))?(?:[,|-]?\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}))?(?:[,|-]?\s*(?P<status>approved|rejected|pending))?", ln, flags=re.IGNORECASE)
                if m:
                    name = m.group("name").strip()
                    title = m.group("title").strip() if m.group("title") else None
                    date = self.parse_date(m.group("date")) if m.group("date") else None
                    status = m.group("status").lower() if m.group("status") else None
                    approvals.append({"approver": name, "title": title, "date": date, "status": status})
        # fallback: signature lines
        sigs = re.findall(r"^Signature[:\s]*([A-Z][A-Za-z ,.'\-]{2,80})", self.text, flags=re.MULTILINE)
        for s in sigs:
            approvals.append({"approver": s.strip(), "title": None, "date": None, "status": None})
        return approvals
