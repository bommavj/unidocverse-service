# app/services/parsers/universal_wire_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalWireParser(BaseParser):
    """
    Heuristic parser for wire transfer / bank transfer instructions and confirmations.
    Returns a dict with keys commonly used by wire analyzers:
      - wire_id
      - sender_name
      - sender_account
      - sender_bank
      - receiver_name
      - receiver_account
      - receiver_bank
      - amount
      - currency
      - date
      - reference
      - routing_number
      - swift_bic
      - purpose
      - fees
      - status
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}
            parsed["wire_id"] = self._extract_wire_id()
            parsed["sender_name"] = self._extract_sender_name()
            parsed["sender_account"] = self._extract_sender_account()
            parsed["sender_bank"] = self._extract_sender_bank()
            parsed["receiver_name"] = self._extract_receiver_name()
            parsed["receiver_account"] = self._extract_receiver_account()
            parsed["receiver_bank"] = self._extract_receiver_bank()
            parsed["amount"], parsed["currency"] = self._extract_amount_and_currency()
            parsed["date"] = self._extract_date()
            parsed["reference"] = self._extract_reference()
            parsed["routing_number"] = self._extract_routing_number()
            parsed["swift_bic"] = self._extract_swift_bic()
            parsed["purpose"] = self._extract_purpose()
            parsed["fees"] = self._extract_fees()
            parsed["status"] = self._extract_status()
            return parsed
        except Exception:
            logger.exception("UniversalWireParser.parse failed")
            return {
                "wire_id": None,
                "sender_name": None,
                "sender_account": None,
                "sender_bank": None,
                "receiver_name": None,
                "receiver_account": None,
                "receiver_bank": None,
                "amount": 0.0,
                "currency": None,
                "date": None,
                "reference": None,
                "routing_number": None,
                "swift_bic": None,
                "purpose": None,
                "fees": 0.0,
                "status": None,
            }

    # ---------- extractors ----------

    def _extract_wire_id(self) -> Optional[str]:
        patterns = [
            r"Wire\s*(?:Transfer\s*)?(?:ID|No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Transaction\s*(?:ID|No\.?|Number|#)[:\s]*([A-Za-z0-9\-_/]+)",
            r"Reference\s*(?:No\.?|#)[:\s]*([A-Za-z0-9\-_/]+)",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: filename
        if self.file_path:
            import os
            return os.path.splitext(os.path.basename(self.file_path))[0]
        return None

    def _extract_sender_name(self) -> Optional[str]:
        patterns = [
            r"Sender[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"From[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Ordering\s+Customer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # fallback: top-of-doc line
        top = "\n".join(self.text.splitlines()[:8])
        m = re.search(r"([A-Z][A-Za-z0-9 &,\.-]{3,120})", top)
        if m:
            return m.group(1).strip()
        return None

    def _extract_sender_account(self) -> Optional[str]:
        patterns = [
            r"Sender\s+Account[:\s]*([A-Za-z0-9\-_/]{4,40})",
            r"Account\s+Number[:\s]*([A-Za-z0-9\-_/]{4,40})",
            r"From\s+Account[:\s]*([A-Za-z0-9\-_/]{4,40})",
        ]
        return self.find_first(patterns)

    def _extract_sender_bank(self) -> Optional[str]:
        patterns = [
            r"Sender\s+Bank[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Bank\s+Name[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Remitting\s+Bank[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        return None

    def _extract_receiver_name(self) -> Optional[str]:
        patterns = [
            r"Beneficiary[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Receiver[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"To[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        # try to find "Beneficiary Name" block
        blocks = self.find_all_blocks(r"^\s*(?:Beneficiary|Beneficiary Name)\b[:\s]*", end_regex=r"^\s*(?:Account|Bank|Reference|Amount)")
        if blocks:
            return blocks[0].split("\n")[0].strip()
        return None

    def _extract_receiver_account(self) -> Optional[str]:
        patterns = [
            r"Beneficiary\s+Account[:\s]*([A-Za-z0-9\-_/]{4,40})",
            r"Account\s+No[:\s]*([A-Za-z0-9\-_/]{4,40})",
            r"To\s+Account[:\s]*([A-Za-z0-9\-_/]{4,40})",
        ]
        return self.find_first(patterns)

    def _extract_receiver_bank(self) -> Optional[str]:
        patterns = [
            r"Beneficiary\s+Bank[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Receiving\s+Bank[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Bank\s+of\s+Beneficiary[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)
        return None

    def _extract_amount_and_currency(self) -> (float, Optional[str]):
        # look for labeled amount lines
        patterns = [
            r"Amount[:\s]*([\$\€\£]?\s?[\d,\.KkMm]+)\s*(?:([A-Z]{3}))?",
            r"Value[:\s]*([\$\€\£]?\s?[\d,\.KkMm]+)\s*(?:([A-Z]{3}))?",
            r"Transfer\s+Amount[:\s]*([\$\€\£]?\s?[\d,\.KkMm]+)\s*(?:([A-Z]{3}))?",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                # try to split currency code if present at end
                m = re.match(r"^(?P<amt>[\$\€\£]?\s?[\d,\.KkMm]+)\s*(?P<cc>[A-Z]{3})?$", v.strip())
                if m:
                    amt = self.safe_float(m.group("amt"))
                    cc = m.group("cc").upper() if m.group("cc") else None
                    if not cc:
                        # infer from symbol
                        if "$" in v:
                            cc = "USD"
                        elif "€" in v:
                            cc = "EUR"
                        elif "£" in v:
                            cc = "GBP"
                    return amt, cc
        # fallback: find first money token and infer currency
        m = re.search(r"([\$€£]?\s?[\d{1,3},]+(?:\.\d+)?\s?(?:k|K|m|M)?)", self.text)
        if m:
            amt = self.safe_float(m.group(1))
            cc = "USD" if "$" in m.group(1) else ("EUR" if "€" in m.group(1) else ("GBP" if "£" in m.group(1) else None))
            return amt, cc
        return 0.0, None

    def _extract_date(self) -> Optional[str]:
        # look for "Date", "Value Date", "Value", "Execution Date"
        patterns = [
            r"Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Value\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Execution\s+Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
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

    def _extract_reference(self) -> Optional[str]:
        patterns = [
            r"Reference[:\s]*([A-Za-z0-9\-_/ ]{3,200})",
            r"Payment\s+Reference[:\s]*([A-Za-z0-9\-_/ ]{3,200})",
            r"Remittance\s+Information[:\s]*([A-Za-z0-9\.,\-_/ ]{3,400})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)[:1000]
        # fallback: look for "Ref:" near amounts
        near = self.kv_pairs_near(r"Ref[:\s]*", window_chars=200)
        if near:
            return near[0].split("\n")[0].strip()
        return None

    def _extract_routing_number(self) -> Optional[str]:
        # US routing numbers are 9 digits; sometimes labeled "ABA" or "Routing"
        patterns = [
            r"Routing\s*(?:Number|No\.?)[:\s]*([0-9]{6,9})",
            r"ABA[:\s]*([0-9]{6,9})",
            r"Sort\s*Code[:\s]*([0-9\-]{6,8})",
        ]
        v = self.find_first(patterns)
        if v:
            return v
        # fallback: any 9-digit sequence in document
        m = re.search(r"\b(\d{9})\b", self.text)
        if m:
            return m.group(1)
        return None

    def _extract_swift_bic(self) -> Optional[str]:
        # SWIFT/BIC codes are 8 or 11 alphanumeric characters
        m = re.search(r"\b([A-Za-z]{4}[A-Za-z]{2}[A-Za-z0-9]{2}(?:[A-Za-z0-9]{3})?)\b", self.text)
        if m:
            code = m.group(1).strip()
            if 8 <= len(code) <= 11:
                return code.upper()
        # labeled patterns
        v = self.find_first([r"SWIFT[:\s]*([A-Za-z0-9]{8,11})", r"BIC[:\s]*([A-Za-z0-9]{8,11})"])
        if v:
            return v.upper()
        return None

    def _extract_purpose(self) -> Optional[str]:
        patterns = [
            r"Purpose[:\s]*([A-Za-z0-9 ,\.\-\/\(\)']{3,400})",
            r"Payment\s+Purpose[:\s]*([A-Za-z0-9 ,\.\-\/\(\)']{3,400})",
            r"Reason[:\s]*([A-Za-z0-9 ,\.\-\/\(\)']{3,400})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)[:1000]
        # fallback: use remittance info or reference
        ref = self._extract_reference()
        if ref:
            return ref
        return None

    def _extract_fees(self) -> float:
        patterns = [
            r"Fees[:\s]*([\$\d,\.KkMm]+)",
            r"Charge[:\s]*([\$\d,\.KkMm]+)",
            r"Commission[:\s]*([\$\d,\.KkMm]+)",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)
        # sometimes fees appear as "Our charges" or "Bank charges"
        v2 = self.find_first([r"Bank\s+Charges[:\s]*([\$\d,\.KkMm]+)", r"Our\s+charges[:\s]*([\$\d,\.KkMm]+)"])
        if v2:
            return self.safe_float(v2)
        return 0.0

    def _extract_status(self) -> Optional[str]:
        # common statuses: Completed, Pending, Rejected, Returned
        if re.search(r"\b(completed|settled|paid)\b", self.text, flags=re.IGNORECASE):
            return "completed"
        if re.search(r"\b(pending|processing|in progress)\b", self.text, flags=re.IGNORECASE):
            return "pending"
        if re.search(r"\b(rejected|failed|returned|cancelled|cancelled)\b", self.text, flags=re.IGNORECASE):
            return "failed"
        # fallback: look for "Status:" label
        v = self.find_first([r"Status[:\s]*([A-Za-z ]{3,40})"])
        if v:
            return v.lower()
        return None
