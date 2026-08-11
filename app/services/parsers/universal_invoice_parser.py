# app/services/parsers/universal_invoice_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalInvoiceParser(BaseParser):
    """
    Heuristic parser for invoices.
    Returns a dict with keys commonly used by financial analyzers:
      - invoice_number
      - invoice_date
      - due_date
      - vendor_name
      - vendor_id
      - bill_to
      - ship_to
      - purchase_order
      - currency
      - subtotal
      - tax
      - total_amount
      - line_items: list of {description, quantity, unit_price, amount, sku, account_code}
      - terms
      - payments: list of {date, amount, method, reference}
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["invoice_number"] = self._extract_invoice_number()
            parsed["invoice_date"] = self._extract_invoice_date()
            parsed["due_date"] = self._extract_due_date()
            parsed["vendor_name"] = self._extract_vendor_name()
            parsed["vendor_id"] = self._extract_vendor_id()
            parsed["bill_to"] = self._extract_bill_to()
            parsed["ship_to"] = self._extract_ship_to()
            parsed["purchase_order"] = self._extract_purchase_order()
            parsed["currency"] = self._extract_currency()
            parsed["subtotal"], parsed["tax"], parsed["total_amount"] = self._extract_totals(parsed["currency"])
            parsed["line_items"] = self._extract_line_items(parsed["currency"])
            parsed["terms"] = self._extract_terms()
            parsed["payments"] = self._extract_payments()
            return parsed
        except Exception:
            logger.exception("UniversalInvoiceParser.parse failed")
            return {
                "invoice_number": None,
                "invoice_date": None,
                "due_date": None,
                "vendor_name": None,
                "vendor_id": None,
                "bill_to": None,
                "ship_to": None,
                "purchase_order": None,
                "currency": None,
                "subtotal": 0.0,
                "tax": 0.0,
                "total_amount": 0.0,
                "line_items": [],
                "terms": None,
                "payments": [],
            }

    # ---------- top-level extractors ----------

    def _extract_invoice_number(self) -> Optional[str]:
        patterns = [
            r"Invoice\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Inv(?:oice)?\s*#[:\s]*([A-Za-z0-9\-_/]+)",
            r"Invoice\s*[:\s]*([A-Za-z0-9\-_/]{3,40})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: filename
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_invoice_date(self) -> Optional[str]:
        patterns = [
            r"Invoice\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Issued[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # fallback: first date in document
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)", self.text)
        if m:
            return self.parse_date(m.group(1))
        return None

    def _extract_due_date(self) -> Optional[str]:
        patterns = [
            r"Due\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Payment\s+Due[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Terms[:\s]*.*?Due\s+in\s+(\d+\s+days)",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # try to infer from "Net X" terms
        m = re.search(r"\bNet\s*(\d{1,3})\b", self.text, flags=re.IGNORECASE)
        if m and self._extract_invoice_date():
            try:
                from datetime import datetime, timedelta
                inv = datetime.fromisoformat(self._extract_invoice_date())
                days = int(m.group(1))
                return (inv + timedelta(days=days)).date().isoformat()
            except Exception:
                pass
        return None

    def _extract_vendor_name(self) -> Optional[str]:
        patterns = [
            r"Vendor[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"From[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Supplier[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Bill From[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
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
            r"Tax\s*ID[:\s]*([A-Za-z0-9\-_/]+)",
            r"VAT\s*No[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        return None

    def _extract_bill_to(self) -> Optional[str]:
        blocks = self.find_all_blocks(r"^\s*(?:Bill To|Bill To[:\s]*|Billed To)[:\s]*", end_regex=r"^\s*(?:Ship To|Invoice|Date|Total|Description)")
        if blocks:
            return blocks[0].split("\n")[0].strip()
        # fallback: look for "Bill To:" inline
        v = self.find_first([r"Bill\s*To[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})"])
        if v:
            return v
        return None

    def _extract_ship_to(self) -> Optional[str]:
        blocks = self.find_all_blocks(r"^\s*(?:Ship To|Ship To[:\s]*|Shipped To)[:\s]*", end_regex=r"^\s*(?:Bill To|Invoice|Date|Total|Description)")
        if blocks:
            return blocks[0].split("\n")[0].strip()
        v = self.find_first([r"Ship\s*To[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})"])
        if v:
            return v
        return None

    def _extract_purchase_order(self) -> Optional[str]:
        patterns = [
            r"Purchase\s*Order[:\s]*([A-Za-z0-9\-_/]+)",
            r"PO\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"PO[:\s]*([A-Za-z0-9\-_/]{3,40})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # sometimes "Order #" near top
        v2 = self.find_first([r"Order\s*#[:\s]*([A-Za-z0-9\-_/]+)"])
        if v2:
            return v2
        return None

    def _extract_currency(self) -> Optional[str]:
        m = re.search(r"\b(USD|EUR|GBP|AUD|CAD|JPY|CHF|CNY)\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(1).upper()
        if "$" in self.text:
            return "USD"
        if "€" in self.text:
            return "EUR"
        return None

    # ---------- totals extraction ----------

    def _extract_totals(self, currency: Optional[str]) -> (float, float, float):
        subtotal = 0.0
        tax = 0.0
        total = 0.0

        # common labeled totals
        subtotal_patterns = [r"Subtotal[:\s]*([\$\d,\.KkMm]+)", r"Sub Total[:\s]*([\$\d,\.KkMm]+)"]
        tax_patterns = [r"Tax[:\s]*([\$\d,\.KkMm]+)", r"Sales\s*Tax[:\s]*([\$\d,\.KkMm]+)"]
        total_patterns = [r"Total\s+Amount[:\s]*([\$\d,\.KkMm]+)", r"Invoice\s+Total[:\s]*([\$\d,\.KkMm]+)", r"Total[:\s]*([\$\d,\.KkMm]+)"]

        for p in subtotal_patterns:
            v = self.find_first([p])
            if v:
                subtotal = self.safe_float(v)
                break

        for p in tax_patterns:
            v = self.find_first([p])
            if v:
                tax = self.safe_float(v)
                break

        for p in total_patterns:
            v = self.find_first([p])
            if v:
                total = self.safe_float(v)
                break

        # fallback: if total not found, take last money token
        if total == 0.0:
            m = re.findall(r"(\$?\s?[\d{1,3},]+(?:\.\d+)?\s?(?:k|K|m|M)?)", self.text)
            if m:
                total = self.safe_float(m[-1])

        # if subtotal missing but line items exist, compute from them
        if subtotal == 0.0:
            items = self._extract_line_items(currency)
            if items:
                subtotal = round(sum(self.safe_float(it.get("amount", 0.0)) for it in items), 2)

        # if tax missing and total/subtotal available, infer
        if tax == 0.0 and subtotal and total and total != subtotal:
            tax = round(total - subtotal, 2)

        return subtotal, tax, total

    # ---------- line items extraction ----------

    def _extract_line_items(self, currency: Optional[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []

        # 1) Table-like rows with pipes or multiple columns
        table_rows = re.findall(
            r"^(?P<sku>[A-Za-z0-9\-_/]{0,20})\s*(?:\||\-|\s{2,})\s*(?P<desc>[^\|\n]{5,200}?)\s*(?:\||\-|\s{2,})\s*(?P<qty>[\d\.]+)?\s*(?:\||\-|\s{2,})\s*(?P<unit>[\$\d,\.KkMm]+)?\s*(?:\||\-|\s{2,})\s*(?P<amt>[\$\d,\.KkMm]+)",
            self.text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if table_rows:
            for r in table_rows:
                sku = r[0].strip() or None
                desc = self.normalize_whitespace(r[1])
                qty = self.safe_float(r[2]) if r[2] else 1.0
                unit_price = self.safe_float(r[3]) if r[3] else (self.safe_float(r[4]) / qty if qty else 0.0)
                amount = self.safe_float(r[4])
                items.append({"description": desc, "quantity": qty, "unit_price": round(unit_price, 2), "amount": round(amount, 2), "sku": sku, "account_code": None})
            if items:
                return items

        # 2) Lines like "Item description - 2 x $50 = $100" or "Description | Qty | Unit | Amount"
        line_pattern = re.compile(
            r"(?P<desc>.+?)\s*[-–—\|]\s*(?P<qty>[\d\.]+)\s*[xX]\s*([\$]?[\d,\.KkMm]+)\s*(?:=|:)\s*([\$]?[\d,\.KkMm]+)",
            flags=re.IGNORECASE,
        )
        for m in line_pattern.finditer(self.text):
            desc = self.normalize_whitespace(m.group("desc"))
            qty = self.safe_float(m.group("qty"))
            unit_price = self.safe_float(m.group(3))
            amount = self.safe_float(m.group(4))
            items.append({"description": desc, "quantity": qty, "unit_price": round(unit_price, 2), "amount": round(amount, 2), "sku": None, "account_code": None})
        if items:
            return items

        # 3) Bulleted or numbered lists under headings like "Description", "Line Items", "Details"
        blocks = self.find_all_blocks(r"^\s*(?:Description|Line Items|Details|Items|Invoice Lines)[:\s]*", end_regex=r"^\s*(?:Subtotal|Total|Tax|Grand Total|Notes)")
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
                        unit_price = amount
                    desc = re.sub(r"([\$]?[\d,\.KkMm]+)\s*$", "", ln).strip()
                    items.append({"description": self.normalize_whitespace(desc), "quantity": qty, "unit_price": round(unit_price, 2), "amount": round(amount, 2), "sku": None, "account_code": None})
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
                    items.append({"description": self.normalize_whitespace(desc)[:200], "quantity": 1.0, "unit_price": round(amount, 2), "amount": round(amount, 2), "sku": None, "account_code": None})
        return items

    # ---------- terms & payments ----------

    def _extract_terms(self) -> Optional[str]:
        blocks = self.find_all_blocks(r"^\s*(?:Terms|Payment Terms|Terms and Conditions)[:\s]*", end_regex=r"^\s*(?:Due Date|Invoice|Total|Notes)")
        if blocks:
            return blocks[0].strip()
        # fallback: capture "Net X" phrase
        m = re.search(r"\b(Net\s*\d{1,3})\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
        return None

    def _extract_payments(self) -> List[Dict[str, Any]]:
        payments: List[Dict[str, Any]] = []
        # look for "Payment" or "Remittance" sections
        blocks = self.find_all_blocks(r"^\s*(?:Payments|Payment History|Remittance|Remitted)[:\s]*", end_regex=r"^\s*(?:Notes|Total|Balance)")
        for blk in blocks:
            lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
            for ln in lines:
                # try to capture "Date - $Amount - Method - Ref"
                m = re.search(r"(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\s*[-,]\s*([\$]?[\d,\.KkMm]+)(?:\s*[-,]\s*(?P<method>[A-Za-z ]{3,30}))?(?:\s*[-,]\s*(?P<ref>[A-Za-z0-9\-_/]+))?", ln)
                if m:
                    date = self.parse_date(m.group("date"))
                    amount = self.safe_float(m.group(2))
                    method = m.group("method").strip() if m.group("method") else None
                    ref = m.group("ref").strip() if m.group("ref") else None
                    payments.append({"date": date, "amount": round(amount, 2), "method": method, "reference": ref})
        # fallback: look for "Paid: $X on DATE" patterns
        for m in re.finditer(r"Paid[:\s]*([\$]?[\d,\.KkMm]+)\s*(?:on|via)?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})?", self.text, flags=re.IGNORECASE):
            amount = self.safe_float(m.group(1))
            date = self.parse_date(m.group(2)) if m.group(2) else None
            payments.append({"date": date, "amount": round(amount, 2), "method": None, "reference": None})
        return payments
