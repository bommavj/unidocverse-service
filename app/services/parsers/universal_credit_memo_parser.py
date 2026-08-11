# app/services/parsers/universal_credit_memo_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalCreditMemoParser(BaseParser):
    """
    Heuristic parser for Credit Memos.
    Returns a dict with keys commonly used by financial analyzers:
      - credit_memo_number
      - vendor_name
      - vendor_id
      - date_issued
      - currency
      - total_amount
      - invoice_reference (original invoice number)
      - reason
      - line_items: list of {description, quantity, unit_price, amount, account_code}
      - approvals: list of approval entries (name, title, date)
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["credit_memo_number"] = self._extract_credit_memo_number()
            parsed["vendor_name"] = self._extract_vendor_name()
            parsed["vendor_id"] = self._extract_vendor_id()
            parsed["date_issued"] = self._extract_date_issued()
            parsed["currency"] = self._extract_currency()
            parsed["total_amount"] = self._extract_total_amount()
            parsed["invoice_reference"] = self._extract_invoice_reference()
            parsed["reason"] = self._extract_reason()
            parsed["line_items"] = self._extract_line_items()
            parsed["approvals"] = self._extract_approvals()
            return parsed
        except Exception:
            logger.exception("UniversalCreditMemoParser.parse failed")
            return {
                "credit_memo_number": None,
                "vendor_name": None,
                "vendor_id": None,
                "date_issued": None,
                "currency": None,
                "total_amount": 0.0,
                "invoice_reference": None,
                "reason": None,
                "line_items": [],
                "approvals": [],
            }

    # ---------- top-level fields ----------

    def _extract_credit_memo_number(self) -> Optional[str]:
        patterns = [
            r"Credit\s*Memo\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Memo\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Credit\s*Note\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: filename
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_vendor_name(self) -> Optional[str]:
        patterns = [
            r"Vendor[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Supplier[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Payee[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Bill To[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: top-of-doc line
        top = "\n".join(self.text.splitlines()[:6])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,120})", top)
        if m:
            return m.group(1).strip()
        return None

    def _extract_vendor_id(self) -> Optional[str]:
        patterns = [
            r"Vendor\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Supplier\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        return None

    def _extract_date_issued(self) -> Optional[str]:
        patterns = [
            r"Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Issued[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Date\s+Issued[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
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

    def _extract_currency(self) -> Optional[str]:
        # look for currency symbol or ISO code near totals
        m = re.search(r"\b(USD|EUR|GBP|AUD|CAD|JPY|CHF|CNY)\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(1).upper()
        # symbol-based fallback
        if "$" in self.text:
            return "USD"
        if "€" in self.text:
            return "EUR"
        return None

    def _extract_total_amount(self) -> float:
        # look for "Total", "Credit Amount", "Amount Due"
        patterns = [
            r"Total\s+Credit\s+Amount[:\s]*([\$\d,\.KkMm]+)",
            r"Total[:\s]*([\$\d,\.KkMm]+)",
            r"Credit\s+Amount[:\s]*([\$\d,\.KkMm]+)",
            r"Amount\s+Credited[:\s]*([\$\d,\.KkMm]+)",
            r"Amount\s+Due[:\s]*([\$\d,\.KkMm]+)",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)
        # fallback: last money-like token in document
        m = re.findall(r"(\$?\s?[\d{1,3},]+(?:\.\d+)?\s?(?:k|K|m|M)?)", self.text)
        if m:
            return self.safe_float(m[-1])
        return 0.0

    def _extract_invoice_reference(self) -> Optional[str]:
        patterns = [
            r"Invoice\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Original\s+Invoice[:\s]*([A-Za-z0-9\-_/]+)",
            r"Ref(?:erence)?[:\s]*Invoice[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # try to find "Related Invoice" block
        blocks = self.find_all_blocks(r"^\s*(?:Related Invoice|Original Invoice|Invoice Reference)[:\s]*", end_regex=r"^\s*(?:Total|Amount|Reason|Description)")
        if blocks:
            first_line = blocks[0].split("\n")[0].strip()
            m = re.search(r"([A-Za-z0-9\-_/]+)", first_line)
            if m:
                return m.group(1)
        return None

    def _extract_reason(self) -> Optional[str]:
        # look for "Reason", "Memo", "Description", "Credit for"
        patterns = [
            r"Reason[:\s]*(.{5,300})",
            r"Memo[:\s]*(.{5,300})",
            r"Description[:\s]*(.{5,300})",
            r"Credit\s+for[:\s]*(.{5,300})",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.normalize_whitespace(v)[:1000]
        # fallback: look for short phrase near top or near totals
        m = re.search(r"(?:Reason|Memo|Description)[:\s]*([A-Za-z0-9 ,\-\(\)\./]{10,200})", self.text, flags=re.IGNORECASE)
        if m:
            return self.normalize_whitespace(m.group(1))
        return None

    # ---------- line items extraction ----------

    def _extract_line_items(self) -> List[Dict[str, Any]]:
        """
        Attempt multiple strategies to extract line items:
         - Table-like rows (Code | Description | Qty | Unit Price | Amount)
         - Lines like "Description - Qty x UnitPrice = Amount"
         - Bulleted lists under "Details" or "Line Items"
        Returns list of {description, quantity, unit_price, amount, account_code}
        """
        items: List[Dict[str, Any]] = []

        # 1) Table-like rows with pipes or multiple columns
        table_rows = re.findall(
            r"^([A-Za-z0-9\-]{0,20})\s*(?:\||\-|\s{2,})\s*([^\|\n]{5,120}?)\s*(?:\||\-|\s{2,})\s*([\d\.]+)?\s*(?:\||\-|\s{2,})\s*([\$\d,\.KkMm]+)\s*(?:\||\-|\s{2,})\s*([\$\d,\.KkMm]+)",
            self.text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if table_rows:
            for r in table_rows:
                code = r[0].strip() or None
                desc = self.normalize_whitespace(r[1])
                qty = self.safe_float(r[2]) if r[2] else 1.0
                unit_price = self.safe_float(r[3])
                amount = self.safe_float(r[4])
                items.append({"description": desc, "quantity": qty, "unit_price": unit_price, "amount": amount, "account_code": code})
            if items:
                return items

        # 2) Lines like "Item description - 2 x $50 = $100"
        line_pattern = re.compile(
            r"(?P<desc>.+?)\s*[-–—]\s*(?P<qty>[\d\.]+)\s*[xX]\s*([\$]?[\d,\.KkMm]+)\s*(?:=|:)\s*([\$]?[\d,\.KkMm]+)",
            flags=re.IGNORECASE,
        )
        for m in line_pattern.finditer(self.text):
            desc = self.normalize_whitespace(m.group("desc"))
            qty = self.safe_float(m.group("qty"))
            unit_price = self.safe_float(m.group(3))
            amount = self.safe_float(m.group(4))
            items.append({"description": desc, "quantity": qty, "unit_price": unit_price, "amount": amount, "account_code": None})
        if items:
            return items

        # 3) Bulleted or numbered lists under headings like "Details", "Line Items", "Description"
        blocks = self.find_all_blocks(r"^\s*(?:Details|Line Items|Description|Items)[:\s]*", end_regex=r"^\s*(?:Total|Amount|Summary|Notes)")
        if blocks:
            for blk in blocks:
                lines = [ln.strip() for ln in re.split(r"\n|;|•|-•|•", blk) if ln.strip()]
                for ln in lines:
                    # try to extract amount at end
                    m = re.search(r"([\$]?[\d,\.KkMm]+)\s*$", ln)
                    amount = self.safe_float(m.group(1)) if m else 0.0
                    # try to extract qty and unit price
                    m2 = re.search(r"([\d\.]+)\s*[xX]\s*([\$]?[\d,\.KkMm]+)", ln)
                    if m2:
                        qty = self.safe_float(m2.group(1))
                        unit_price = self.safe_float(m2.group(2))
                    else:
                        qty = 1.0
                        unit_price = (amount / qty) if amount else 0.0
                    desc = re.sub(r"([\$]?[\d,\.KkMm]+)\s*$", "", ln).strip()
                    items.append({"description": self.normalize_whitespace(desc), "quantity": qty, "unit_price": unit_price, "amount": amount, "account_code": None})
            if items:
                return items

        # 4) Fallback: try to find any money-bearing lines and create minimal items
        lines = [ln.strip() for ln in self.text.splitlines() if ln.strip()]
        for ln in lines:
            m = re.search(r"([\$]?[\d,\.KkMm]+)", ln)
            if m:
                amount = self.safe_float(m.group(1))
                # use preceding text as description
                desc = re.sub(r"([\$]?[\d,\.KkMm]+)", "", ln).strip()
                if desc and amount:
                    items.append({"description": self.normalize_whitespace(desc)[:200], "quantity": 1.0, "unit_price": amount, "amount": amount, "account_code": None})
        return items

    # ---------- approvals extraction ----------

    def _extract_approvals(self) -> List[Dict[str, Any]]:
        approvals: List[Dict[str, Any]] = []
        # look for "Approved by" blocks
        blocks = self.find_all_blocks(r"^\s*Approved\s+by\b[:\s]*", end_regex=r"^\s*(?:Date|Signature|Title|Notes)")
        for blk in blocks:
            lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
            for ln in lines:
                # try to capture "Name, Title - Date" or "Name (Title) Date"
                m = re.search(r"(?P<name>[A-Z][A-Za-z ,.'\-]{2,80})(?:[,|-]\s*(?P<title>[A-Za-z &\-/]{2,80}))?(?:[,|-]?\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}))?", ln)
                if m:
                    name = m.group("name").strip()
                    title = m.group("title").strip() if m.group("title") else None
                    date = self.parse_date(m.group("date")) if m.group("date") else None
                    approvals.append({"name": name, "title": title, "date": date})
        # fallback: look for signature lines
        sigs = re.findall(r"^Signature[:\s]*([A-Z][A-Za-z ,.'\-]{2,80})", self.text, flags=re.MULTILINE)
        for s in sigs:
            approvals.append({"name": s.strip(), "title": None, "date": None})
        return approvals
