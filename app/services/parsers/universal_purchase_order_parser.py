# app/services/parsers/universal_purchase_order_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalPurchaseOrderParser(BaseParser):
    """
    Heuristic parser for Purchase Orders (PO).
    Returns a dict with keys commonly used by procurement/finance analyzers:
      - po_number
      - buyer_name
      - buyer_id
      - supplier_name
      - supplier_id
      - order_date
      - delivery_date
      - currency
      - subtotal
      - tax
      - total_amount
      - items: list of {line_no, sku, description, quantity, unit, unit_price, amount}
      - shipping_terms
      - payment_terms
      - references: dict of related refs (contract, project, requisition)
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["po_number"] = self._extract_po_number()
            parsed["buyer_name"] = self._extract_buyer_name()
            parsed["buyer_id"] = self._extract_buyer_id()
            parsed["supplier_name"] = self._extract_supplier_name()
            parsed["supplier_id"] = self._extract_supplier_id()
            parsed["order_date"] = self._extract_order_date()
            parsed["delivery_date"] = self._extract_delivery_date()
            parsed["currency"] = self._extract_currency()
            parsed["items"] = self._extract_line_items(parsed["currency"])
            parsed["subtotal"], parsed["tax"], parsed["total_amount"] = self._extract_totals(parsed["currency"], parsed["items"])
            parsed["shipping_terms"] = self._extract_shipping_terms()
            parsed["payment_terms"] = self._extract_payment_terms()
            parsed["references"] = self._extract_references()
            return parsed
        except Exception:
            logger.exception("UniversalPOParser.parse failed")
            return {
                "po_number": None,
                "buyer_name": None,
                "buyer_id": None,
                "supplier_name": None,
                "supplier_id": None,
                "order_date": None,
                "delivery_date": None,
                "currency": None,
                "subtotal": 0.0,
                "tax": 0.0,
                "total_amount": 0.0,
                "items": [],
                "shipping_terms": None,
                "payment_terms": None,
                "references": {},
            }

    # ---------- top-level extractors ----------

    def _extract_po_number(self) -> Optional[str]:
        patterns = [
            r"Purchase\s*Order\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"PO\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Order\s*#[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: filename
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_buyer_name(self) -> Optional[str]:
        patterns = [
            r"Buyer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Bill\s*To[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Purchaser[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: top-of-doc block
        top = "\n".join(self.text.splitlines()[:10])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,120})", top)
        if m:
            return m.group(1).strip()
        return None

    def _extract_buyer_id(self) -> Optional[str]:
        patterns = [
            r"Buyer\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Purchaser\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        return self.find_first(patterns)

    def _extract_supplier_name(self) -> Optional[str]:
        patterns = [
            r"Supplier[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Vendor[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Ship\s*From[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Sold\s*By[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: look near top lines after vendor/producer keywords
        top = "\n".join(self.text.splitlines()[:12])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,120})", top)
        if m:
            return m.group(1).strip()
        return None

    def _extract_supplier_id(self) -> Optional[str]:
        patterns = [
            r"Supplier\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Vendor\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Tax\s*ID[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        return self.find_first(patterns)

    def _extract_order_date(self) -> Optional[str]:
        patterns = [
            r"Order\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
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

    def _extract_delivery_date(self) -> Optional[str]:
        patterns = [
            r"Delivery\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Requested\s+Delivery[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Ship\s+By[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            parsed = self.parse_date(v)
            if parsed:
                return parsed
        # try to find "Deliver by" or "ETA"
        m = re.search(r"(?:Deliver(?:y)?\s+by|ETA)[:\s]*([A-Za-z0-9 ,\-\/]{6,40})", self.text, flags=re.IGNORECASE)
        if m:
            return self.parse_date(m.group(1))
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

    # ---------- line items extraction ----------

    def _extract_line_items(self, currency: Optional[str]) -> List[Dict[str, Any]]:
        """
        Extract line items using multiple heuristics:
         - Table-like rows (line | sku | description | qty | unit | unit_price | amount)
         - Lines like "1. SKU - Description - 2 x $10 = $20"
         - Blocks under "Items", "Order Details", "Line Items"
        """
        items: List[Dict[str, Any]] = []

        # 1) Table-like rows with pipes or multiple columns
        table_rows = re.findall(
            r"^(?P<line>\d{1,3})?\s*(?:\||\-|\s{2,})\s*(?P<sku>[A-Za-z0-9\-_/]{0,30})\s*(?:\||\-|\s{2,})\s*(?P<desc>[^\|\n]{5,200}?)\s*(?:\||\-|\s{2,})\s*(?P<qty>[\d\.]+)?\s*(?:\||\-|\s{2,})\s*(?P<unit>[A-Za-z%]{0,10})?\s*(?:\||\-|\s{2,})\s*(?P<unit_price>[\$\d,\.KkMm]+)?\s*(?:\||\-|\s{2,})\s*(?P<amt>[\$\d,\.KkMm]+)",
            self.text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if table_rows:
            for r in table_rows:
                line_no = r[0].strip() if r[0] else None
                sku = r[1].strip() or None
                desc = self.normalize_whitespace(r[2])
                qty = self.safe_float(r[3]) if r[3] else 1.0
                unit = r[4].strip() if r[4] else None
                unit_price = self.safe_float(r[5]) if r[5] else (self.safe_float(r[6]) / qty if qty else 0.0)
                amount = self.safe_float(r[6])
                items.append({
                    "line_no": line_no,
                    "sku": sku,
                    "description": desc,
                    "quantity": qty,
                    "unit": unit,
                    "unit_price": round(unit_price, 2),
                    "amount": round(amount, 2),
                    "currency": currency,
                })
            if items:
                return items

        # 2) Lines like "1. SKU - Description - 2 x $10 = $20"
        line_pattern = re.compile(
            r"^\s*(?P<line>\d{1,3})[.)]?\s*(?:(?P<sku>[A-Za-z0-9\-_/]{2,30})\s*[-:]\s*)?(?P<desc>.+?)\s*[-–—]\s*(?P<qty>[\d\.]+)\s*[xX]\s*([\$]?[\d,\.KkMm]+)\s*(?:=|:)\s*([\$]?[\d,\.KkMm]+)",
            flags=re.MULTILINE | re.IGNORECASE,
        )
        for m in line_pattern.finditer(self.text):
            line_no = m.group("line")
            sku = m.group("sku").strip() if m.group("sku") else None
            desc = self.normalize_whitespace(m.group("desc"))
            qty = self.safe_float(m.group("qty"))
            unit_price = self.safe_float(m.group(4))
            amount = self.safe_float(m.group(5))
            items.append({
                "line_no": line_no,
                "sku": sku,
                "description": desc,
                "quantity": qty,
                "unit": None,
                "unit_price": round(unit_price, 2),
                "amount": round(amount, 2),
                "currency": currency,
            })
        if items:
            return items

        # 3) Blocks under "Items", "Order Details", "Line Items"
        blocks = self.find_all_blocks(r"^\s*(?:Items|Order Details|Line Items|Order Lines|Order Details)[:\s]*", end_regex=r"^\s*(?:Subtotal|Total|Tax|Shipping|Notes)")
        if blocks:
            for blk in blocks:
                lines = [ln.strip() for ln in re.split(r"\n|;|•|-•|•", blk) if ln.strip()]
                for ln in lines:
                    # try to extract amount at end
                    m = re.search(r"([\$]?[\d,\.KkMm]+)\s*$", ln)
                    amount = self.safe_float(m.group(1)) if m else 0.0
                    # qty and unit price
                    m2 = re.search(r"([\d\.]+)\s*[xX]\s*([\$]?[\d,\.KkMm]+)", ln)
                    if m2:
                        qty = self.safe_float(m2.group(1))
                        unit_price = self.safe_float(m2.group(2))
                    else:
                        qty = 1.0
                        unit_price = amount
                    # sku detection
                    m3 = re.search(r"\b([A-Za-z0-9\-_/]{2,20})\b", ln)
                    sku = m3.group(1).strip() if m3 else None
                    desc = re.sub(r"([\$]?[\d,\.KkMm]+)\s*$", "", ln).strip()
                    items.append({
                        "line_no": None,
                        "sku": sku,
                        "description": self.normalize_whitespace(desc)[:200],
                        "quantity": qty,
                        "unit": None,
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
                        "line_no": None,
                        "sku": None,
                        "description": self.normalize_whitespace(desc)[:200],
                        "quantity": 1.0,
                        "unit": None,
                        "unit_price": round(amount, 2),
                        "amount": round(amount, 2),
                        "currency": currency,
                    })
        return items

    # ---------- totals extraction ----------

    def _extract_totals(self, currency: Optional[str], items: List[Dict[str, Any]]) -> (float, float, float):
        subtotal = 0.0
        tax = 0.0
        total = 0.0

        subtotal_patterns = [r"Subtotal[:\s]*([\$\d,\.KkMm]+)", r"Sub Total[:\s]*([\$\d,\.KkMm]+)"]
        tax_patterns = [r"Tax[:\s]*([\$\d,\.KkMm]+)", r"Sales\s*Tax[:\s]*([\$\d,\.KkMm]+)"]
        total_patterns = [r"Total\s+Amount[:\s]*([\$\d,\.KkMm]+)", r"Order\s+Total[:\s]*([\$\d,\.KkMm]+)", r"Total[:\s]*([\$\d,\.KkMm]+)"]

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

        # fallback: compute subtotal from items if missing
        if subtotal == 0.0 and items:
            subtotal = round(sum(self.safe_float(it.get("amount", 0.0)) for it in items), 2)

        # fallback: last money token as total
        if total == 0.0:
            m = re.findall(r"(\$?\s?[\d{1,3},]+(?:\.\d+)?\s?(?:k|K|m|M)?)", self.text)
            if m:
                total = self.safe_float(m[-1])

        # infer tax if possible
        if tax == 0.0 and subtotal and total and total != subtotal:
            tax = round(total - subtotal, 2)

        return subtotal, tax, total

    # ---------- terms & references ----------

    def _extract_shipping_terms(self) -> Optional[str]:
        blocks = self.find_all_blocks(r"^\s*(?:Shipping Terms|Ship Via|Shipping|Delivery Terms)[:\s]*", end_regex=r"^\s*(?:Payment Terms|Total|Notes|Instructions)")
        if blocks:
            return blocks[0].strip()
        # fallback: look for Incoterms like FOB, CIF
        m = re.search(r"\b(FOB|CIF|EXW|DAP|DDP|FCA)\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(1).upper()
        return None

    def _extract_payment_terms(self) -> Optional[str]:
        blocks = self.find_all_blocks(r"^\s*(?:Payment Terms|Terms|Payment)[:\s]*", end_regex=r"^\s*(?:Due Date|Total|Notes|Shipping)")
        if blocks:
            return blocks[0].strip()
        # fallback: Net X
        m = re.search(r"\b(Net\s*\d{1,3})\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
        return None

    def _extract_references(self) -> Dict[str, Optional[str]]:
        refs: Dict[str, Optional[str]] = {"contract": None, "requisition": None, "project": None}
        # contract
        v = self.find_first([r"Contract\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)", r"Agreement\s*(?:No\.?|#)[:\s]*([A-Za-z0-9\-_/]+)"])
        if v:
            refs["contract"] = v
        # requisition
        v2 = self.find_first([r"Requisition\s*(?:No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)", r"Req\s*(?:No\.?|#)[:\s]*([A-Za-z0-9\-_/]+)"])
        if v2:
            refs["requisition"] = v2
        # project
        v3 = self.find_first([r"Project[:\s]*([A-Za-z0-9 \-_/]{2,80})", r"Job[:\s]*([A-Za-z0-9 \-_/]{2,80})"])
        if v3:
            refs["project"] = v3
        return refs
