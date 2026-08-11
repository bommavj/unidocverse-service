# app/services/parsers/universal_receipt_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalReceiptParser(BaseParser):
    """
    Heuristic parser for retail receipts and expense receipts.
    Returns a dict with keys commonly used by expense and receipt analyzers:
      - receipt_id
      - vendor
      - merchant_address
      - date
      - currency
      - subtotal
      - tax
      - tip
      - total
      - payment_method
      - card_last4
      - line_items: list of {description, quantity, unit_price, amount}
      - raw_lines: list of cleaned lines (for debugging)
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            lines = [ln.strip() for ln in self.text.splitlines() if ln.strip()]
            parsed["raw_lines"] = lines[:200]
            parsed["receipt_id"] = self._extract_receipt_id(lines)
            parsed["vendor"] = self._extract_vendor(lines)
            parsed["merchant_address"] = self._extract_merchant_address(lines)
            parsed["date"] = self._extract_date(lines)
            parsed["currency"] = self._extract_currency()
            parsed["subtotal"] = self._extract_subtotal(lines)
            parsed["tax"] = self._extract_tax(lines)
            parsed["tip"] = self._extract_tip(lines)
            parsed["total"] = self._extract_total(lines)
            parsed["payment_method"], parsed["card_last4"] = self._extract_payment(lines)
            parsed["line_items"] = self._extract_line_items(lines, parsed["currency"])
            return parsed
        except Exception:
            logger.exception("UniversalReceiptParser.parse failed")
            return {
                "receipt_id": None,
                "vendor": None,
                "merchant_address": None,
                "date": None,
                "currency": None,
                "subtotal": 0.0,
                "tax": 0.0,
                "tip": 0.0,
                "total": 0.0,
                "payment_method": None,
                "card_last4": None,
                "line_items": [],
                "raw_lines": [],
            }

    # ---------- top-level helpers ----------

    def _extract_receipt_id(self, lines: List[str]) -> Optional[str]:
        # common labels: Receipt #, Transaction #, Ticket #
        patterns = [
            r"Receipt\s*(?:No\.?|#|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Transaction\s*(?:No\.?|#|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Ticket\s*(?:No\.?|#|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Order\s*(?:No\.?|#|Number)[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: look for short alphanumeric near top
        top = "\n".join(lines[:8])
        m = re.search(r"\b([A-Z0-9]{4,12}-?[A-Z0-9]{0,6})\b", top)
        if m:
            return m.group(1)
        return None

    def _extract_vendor(self, lines: List[str]) -> Optional[str]:
        # vendor usually appears in first 1-4 lines
        top = lines[:6]
        # prefer lines with words like "Inc", "LLC", "Store", "Market", "Restaurant"
        for ln in top:
            if re.search(r"\b(Inc|LLC|Ltd|Store|Market|Restaurant|Cafe|Co\.|Company|Supermarket|Grocery)\b", ln, flags=re.IGNORECASE):
                return ln
        # otherwise return first non-numeric line with letters
        for ln in top:
            if re.search(r"[A-Za-z]", ln) and not re.match(r"^\d+$", ln):
                return ln
        return None

    def _extract_merchant_address(self, lines: List[str]) -> Optional[str]:
        # look for address-like lines (street number + street name + city/state/zip)
        for i, ln in enumerate(lines[:20]):
            if re.search(r"\d{1,5}\s+[A-Za-z0-9\.\- ]{3,80}\,\s*[A-Za-z]{2,}\s*\d{5}", ln):
                return ln
            # multi-line address: street line followed by city/state/zip
            if i + 1 < len(lines):
                if re.search(r"\d{1,5}\s+[A-Za-z0-9\.\- ]{3,80}", ln) and re.search(r"[A-Za-z ]+,\s*[A-Za-z]{2}\s*\d{5}", lines[i + 1]):
                    return f"{ln} {lines[i+1]}"
        return None

    def _extract_date(self, lines: List[str]) -> Optional[str]:
        # search for date tokens in lines
        date_regex = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)"
        for ln in lines[:40]:
            m = re.search(date_regex, ln)
            if m:
                parsed = self.parse_date(m.group(1))
                if parsed:
                    return parsed
        # fallback: any date in document
        m2 = re.search(date_regex, self.text)
        if m2:
            return self.parse_date(m2.group(1))
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

    # ---------- totals ----------

    def _extract_subtotal(self, lines: List[str]) -> float:
        patterns = [r"Subtotal[:\s]*([\$\d,\.KkMm]+)", r"Sub Total[:\s]*([\$\d,\.KkMm]+)"]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)
        # sometimes labeled "Items" or computed from line items
        items = self._extract_line_items(lines, self._extract_currency())
        if items:
            return round(sum(self.safe_float(it.get("amount", 0.0)) for it in items), 2)
        return 0.0

    def _extract_tax(self, lines: List[str]) -> float:
        patterns = [r"Tax[:\s]*([\$\d,\.KkMm]+)", r"Sales\s*Tax[:\s]*([\$\d,\.KkMm]+)"]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)
        return 0.0

    def _extract_tip(self, lines: List[str]) -> float:
        patterns = [r"Tip[:\s]*([\$\d,\.KkMm]+)", r"Gratuity[:\s]*([\$\d,\.KkMm]+)"]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)
        return 0.0

    def _extract_total(self, lines: List[str]) -> float:
        patterns = [
            r"Total\s+Amount[:\s]*([\$\d,\.KkMm]+)",
            r"Amount\s+Due[:\s]*([\$\d,\.KkMm]+)",
            r"Grand\s+Total[:\s]*([\$\d,\.KkMm]+)",
            r"Total[:\s]*([\$\d,\.KkMm]+)",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)
        # fallback: last money token in document
        m = re.findall(r"(\$?\s?[\d{1,3},]+(?:\.\d+)?\s?(?:k|K|m|M)?)", self.text)
        if m:
            return self.safe_float(m[-1])
        return 0.0

    # ---------- payment ----------

    def _extract_payment(self, lines: List[str]) -> (Optional[str], Optional[str]):
        # look for card or cash indicators
        for ln in reversed(lines[-40:]):
            if re.search(r"\b(Visa|Mastercard|MasterCard|Amex|American Express|Discover|Debit|Credit|Cash)\b", ln, flags=re.IGNORECASE):
                # capture last4 if present
                m = re.search(r"(?:\*{2,}|\#|\b)(\d{4})\b", ln)
                last4 = m.group(1) if m else None
                method = re.search(r"\b(Visa|Mastercard|MasterCard|Amex|American Express|Discover|Debit|Credit|Cash)\b", ln, flags=re.IGNORECASE)
                return (method.group(1) if method else None, last4)
        # fallback: look for "Paid with" or "Payment Method"
        v = self.find_first([r"Paid\s+with[:\s]*([A-Za-z0-9 \-]+)", r"Payment\s+Method[:\s]*([A-Za-z0-9 \-]+)"])
        if v:
            m = re.search(r"(\d{4})", v)
            last4 = m.group(1) if m else None
            method = re.sub(r"\d{4}", "", v).strip()
            return (method or None, last4)
        return None, None

    # ---------- line items ----------

    def _extract_line_items(self, lines: List[str], currency: Optional[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []

        # Strategy A: table-like lines with qty and price e.g., "2 x Widget  $5.00  $10.00" or "Widget 2 5.00 10.00"
        table_re = re.compile(
            r"^(?P<desc>.+?)\s{2,}?(?:(?P<qty>[\d]+)\s*[xX]\s*)?(?P<unit>[\$]?[\d,\.KkMm]+)\s*(?:\s{2,}|\s*\t\s*)(?P<amt>[\$]?[\d,\.KkMm]+)$",
            flags=re.IGNORECASE,
        )
        for ln in lines:
            m = table_re.match(ln)
            if m:
                desc = self.normalize_whitespace(m.group("desc"))
                qty = self.safe_float(m.group("qty")) if m.group("qty") else 1.0
                unit = self.safe_float(m.group("unit"))
                amt = self.safe_float(m.group("amt"))
                items.append({"description": desc, "quantity": qty, "unit_price": round(unit, 2), "amount": round(amt, 2)})
        if items:
            return items

        # Strategy B: lines like "Widget - 2 x $5.00 = $10.00"
        line_pattern = re.compile(
            r"^(?P<desc>.+?)\s*[-–—]\s*(?P<qty>[\d]+)\s*[xX]\s*([\$]?[\d,\.KkMm]+)\s*(?:=|:)\s*([\$]?[\d,\.KkMm]+)$",
            flags=re.IGNORECASE,
        )
        for ln in lines:
            m = line_pattern.match(ln)
            if m:
                desc = self.normalize_whitespace(m.group("desc"))
                qty = self.safe_float(m.group("qty"))
                unit_price = self.safe_float(m.group(3))
                amount = self.safe_float(m.group(4))
                items.append({"description": desc, "quantity": qty, "unit_price": round(unit_price, 2), "amount": round(amount, 2)})
        if items:
            return items

        # Strategy C: simple "Description  $Amount" lines (no qty)
        simple_re = re.compile(r"^(?P<desc>.+?)\s{2,}([\$]?[\d,\.KkMm]+)$")
        for ln in lines:
            m = simple_re.match(ln)
            if m:
                desc = self.normalize_whitespace(m.group("desc"))
                amt = self.safe_float(m.group(2))
                items.append({"description": desc, "quantity": 1.0, "unit_price": round(amt, 2), "amount": round(amt, 2)})
        # return whatever we found (may be empty)
        return items
