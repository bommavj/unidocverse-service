# app/services/parsers/universal_payroll_parser.py

import logging
import re
from typing import Dict, Any, List, Optional

from .base_parser import BaseParser

logger = logging.getLogger(__name__)


class UniversalPayrollParser(BaseParser):
    """
    Heuristic parser for payroll statements / pay stubs.
    Returns a dict with keys commonly used by payroll analyzers:
      - employee_name
      - employee_id
      - employer_name
      - pay_period_start
      - pay_period_end
      - pay_date
      - pay_frequency
      - gross_pay
      - net_pay
      - currency
      - hours_worked
      - rate
      - earnings: list[{type, hours, rate, amount}]
      - deductions: list[{type, amount}]
      - taxes: list[{type, amount}]
      - ytd_gross
      - ytd_net
      - ytd_taxes
    The parser is defensive and returns sensible defaults on noisy input.
    """

    def parse(self) -> Dict[str, Any]:
        try:
            parsed: Dict[str, Any] = {}

            parsed["employee_name"] = self._extract_employee_name()
            parsed["employee_id"] = self._extract_employee_id()
            parsed["employer_name"] = self._extract_employer_name()
            parsed["pay_period_start"], parsed["pay_period_end"] = self._extract_pay_period()
            parsed["pay_date"] = self._extract_pay_date()
            parsed["pay_frequency"] = self._extract_pay_frequency()
            parsed["currency"] = self._extract_currency()

            earnings = self._extract_earnings(parsed["currency"])
            deductions = self._extract_deductions(parsed["currency"])
            taxes = self._extract_taxes(parsed["currency"])

            parsed["earnings"] = earnings
            parsed["deductions"] = deductions
            parsed["taxes"] = taxes

            parsed["gross_pay"] = self._extract_gross_pay(parsed["currency"], earnings)
            parsed["net_pay"] = self._extract_net_pay(parsed["currency"])
            parsed["hours_worked"], parsed["rate"] = self._extract_hours_and_rate(earnings)

            parsed["ytd_gross"], parsed["ytd_net"], parsed["ytd_taxes"] = self._extract_ytd()

            return parsed

        except Exception:
            logger.exception("UniversalPayrollParser.parse failed")
            return {
                "employee_name": None,
                "employee_id": None,
                "employer_name": None,
                "pay_period_start": None,
                "pay_period_end": None,
                "pay_date": None,
                "pay_frequency": None,
                "currency": None,
                "gross_pay": 0.0,
                "net_pay": 0.0,
                "hours_worked": 0.0,
                "rate": 0.0,
                "earnings": [],
                "deductions": [],
                "taxes": [],
                "ytd_gross": 0.0,
                "ytd_net": 0.0,
                "ytd_taxes": 0.0,
            }

    # ---------- identity ----------

    def _extract_employee_name(self) -> Optional[str]:
        patterns = [
            r"Employee\s*Name[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
            r"Name[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)

        # fallback: look for name near "Employee"
        m = re.search(r"Employee[:\s]*([A-Z][A-Za-z ,.'\-]{2,120})", self.text)
        if m:
            return self.normalize_whitespace(m.group(1))
        return None

    def _extract_employee_id(self) -> Optional[str]:
        patterns = [
            r"Employee\s*(?:ID|No\.?|Number)[:\s]*([A-Za-z0-9\-_/]{3,40})",
            r"Emp\s*ID[:\s]*([A-Za-z0-9\-_/]{3,40})",
        ]
        return self.find_first(patterns)

    def _extract_employer_name(self) -> Optional[str]:
        patterns = [
            r"Employer[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
            r"Company[:\s]*([A-Z][A-Za-z0-9 ,.&'/-]{3,200})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.normalize_whitespace(v)

        # fallback: top lines with Inc/LLC
        for ln in self.text.splitlines()[:10]:
            if re.search(r"\b(Inc|LLC|Ltd|Corporation|Corp|Company)\b", ln):
                return ln.strip()
        return None

    # ---------- dates & period ----------

    def _extract_pay_period(self) -> (Optional[str], Optional[str]):
        patterns = [
            r"Pay\s*Period[:\s]*([A-Za-z0-9 ,\-\/]{6,40})\s*-\s*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Period\s*Start[:\s]*([A-Za-z0-9 ,\-\/]{6,40}).*?Period\s*End[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        for p in patterns:
            m = re.search(p, self.text, flags=re.IGNORECASE | re.DOTALL)
            if m:
                start = self.parse_date(m.group(1))
                end = self.parse_date(m.group(2))
                return start, end
        # fallback: single "Pay Period Ending"
        v = self.find_first([r"Pay\s*Period\s*Ending[:\s]*([A-Za-z0-9 ,\-\/]{6,40})"])
        if v:
            end = self.parse_date(v)
            return None, end
        return None, None

    def _extract_pay_date(self) -> Optional[str]:
        patterns = [
            r"Pay\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Check\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
            r"Payment\s*Date[:\s]*([A-Za-z0-9 ,\-\/]{6,40})",
        ]
        v = self.find_first(patterns)
        if v:
            return self.parse_date(v)
        # fallback: first date in document
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b)", self.text)
        if m:
            return self.parse_date(m.group(1))
        return None

    def _extract_pay_frequency(self) -> Optional[str]:
        patterns = [
            r"Pay\s*Frequency[:\s]*([A-Za-z ]{3,40})",
            r"Pay\s*Schedule[:\s]*([A-Za-z ]{3,40})",
        ]
        v = self.find_first(patterns)
        if v:
            return v.strip().lower()
        # keyword inference
        if re.search(r"\bbi[- ]?weekly\b", self.text, flags=re.IGNORECASE):
            return "biweekly"
        if re.search(r"\bsemi[- ]?monthly\b", self.text, flags=re.IGNORECASE):
            return "semimonthly"
        if re.search(r"\bmonthly\b", self.text, flags=re.IGNORECASE):
            return "monthly"
        if re.search(r"\bweekly\b", self.text, flags=re.IGNORECASE):
            return "weekly"
        return None

    def _extract_currency(self) -> Optional[str]:
        m = re.search(r"\b(USD|EUR|GBP|AUD|CAD|JPY|CHF|CNY)\b", self.text, flags=re.IGNORECASE)
        if m:
            return m.group(1).upper()
        if "$" in self.text:
            return "USD"
        if "€" in self.text:
            return "EUR"
        if "£" in self.text:
            return "GBP"
        return None

    # ---------- earnings / deductions / taxes ----------

    def _extract_earnings(self, currency: Optional[str]) -> List[Dict[str, Any]]:
        """
        Try to parse an Earnings section or table:
        Description | Hours | Rate | Current | YTD
        """
        items: List[Dict[str, Any]] = []

        # Block under "Earnings"
        blocks = self.find_all_blocks(
            r"^\s*(Earnings|Hours and Earnings)\b[:\s]*",
            end_regex=r"^\s*(Deductions|Taxes|Withholdings|Net Pay|Summary)"
        )
        text_block = blocks[0] if blocks else self.text

        # Table-like rows
        row_re = re.compile(
            r"^(?P<desc>[A-Za-z0-9 ,.&'/-]{3,60})\s+"
            r"(?P<hours>[\d\.]{1,6})?\s+"
            r"(?P<rate>[\$\d,\.KkMm]{1,15})?\s+"
            r"(?P<amount>[\$\d,\.KkMm]{1,15})",
            flags=re.MULTILINE
        )
        for m in row_re.finditer(text_block):
            desc = self.normalize_whitespace(m.group("desc"))
            hours = self.safe_float(m.group("hours")) if m.group("hours") else 0.0
            rate = self.safe_float(m.group("rate")) if m.group("rate") else 0.0
            amt = self.safe_float(m.group("amount"))
            items.append({
                "type": desc,
                "hours": hours,
                "rate": round(rate, 2),
                "amount": round(amt, 2),
                "currency": currency,
            })

        return items

    def _extract_deductions(self, currency: Optional[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        blocks = self.find_all_blocks(
            r"^\s*(Deductions|Pre-Tax Deductions|After-Tax Deductions)\b[:\s]*",
            end_regex=r"^\s*(Taxes|Withholdings|Net Pay|Summary|Earnings)"
        )
        if not blocks:
            return items
        text_block = blocks[0]

        row_re = re.compile(
            r"^(?P<desc>[A-Za-z0-9 ,.&'/-]{3,80})\s+(?P<amount>[\$\d,\.KkMm]{1,15})",
            flags=re.MULTILINE
        )
        for m in row_re.finditer(text_block):
            desc = self.normalize_whitespace(m.group("desc"))
            amt = self.safe_float(m.group("amount"))
            items.append({
                "type": desc,
                "amount": round(amt, 2),
                "currency": currency,
            })
        return items

    def _extract_taxes(self, currency: Optional[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        blocks = self.find_all_blocks(
            r"^\s*(Taxes|Tax Withholdings|Withholdings)\b[:\s]*",
            end_regex=r"^\s*(Deductions|Net Pay|Summary|Earnings)"
        )
        if not blocks:
            return items
        text_block = blocks[0]

        row_re = re.compile(
            r"^(?P<desc>[A-Za-z0-9 ,.&'/-]{3,80})\s+(?P<amount>[\$\d,\.KkMm]{1,15})",
            flags=re.MULTILINE
        )
        for m in row_re.finditer(text_block):
            desc = self.normalize_whitespace(m.group("desc"))
            amt = self.safe_float(m.group("amount"))
            items.append({
                "type": desc,
                "amount": round(amt, 2),
                "currency": currency,
            })
        return items

    # ---------- totals ----------

    def _extract_gross_pay(self, currency: Optional[str], earnings: List[Dict[str, Any]]) -> float:
        patterns = [
            r"Gross\s*Pay[:\s]*([\$\d,\.KkMm]+)",
            r"Total\s*Earnings[:\s]*([\$\d,\.KkMm]+)",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)

        if earnings:
            return round(sum(self.safe_float(e.get("amount", 0.0)) for e in earnings), 2)
        return 0.0

    def _extract_net_pay(self, currency: Optional[str]) -> float:
        patterns = [
            r"Net\s*Pay[:\s]*([\$\d,\.KkMm]+)",
            r"Net\s*Amount[:\s]*([\$\d,\.KkMm]+)",
            r"Take\s*Home\s*Pay[:\s]*([\$\d,\.KkMm]+)",
        ]
        for p in patterns:
            v = self.find_first([p])
            if v:
                return self.safe_float(v)

        # fallback: last money token
        m = re.findall(r"(\$?\s?[\d{1,3},]+(?:\.\d+)?\s?(?:k|K|m|M)?)", self.text)
        if m:
            return self.safe_float(m[-1])
        return 0.0

    def _extract_hours_and_rate(self, earnings: List[Dict[str, Any]]) -> (float, float):
        if not earnings:
            return 0.0, 0.0
        total_hours = sum(self.safe_float(e.get("hours", 0.0)) for e in earnings)
        # heuristic: pick first non-zero rate
        rate = 0.0
        for e in earnings:
            r = self.safe_float(e.get("rate", 0.0))
            if r:
                rate = r
                break
        return round(total_hours, 2), round(rate, 2)

    # ---------- YTD ----------

    def _extract_ytd(self) -> (float, float, float):
        ytd_gross = 0.0
        ytd_net = 0.0
        ytd_taxes = 0.0

        patterns_gross = [
            r"YTD\s*Gross[:\s]*([\$\d,\.KkMm]+)",
            r"Gross\s*Pay\s*YTD[:\s]*([\$\d,\.KkMm]+)",
        ]
        patterns_net = [
            r"YTD\s*Net[:\s]*([\$\d,\.KkMm]+)",
            r"Net\s*Pay\s*YTD[:\s]*([\$\d,\.KkMm]+)",
        ]
        patterns_tax = [
            r"YTD\s*Tax(?:es)?[:\s]*([\$\d,\.KkMm]+)",
            r"Total\s*Tax\s*YTD[:\s]*([\$\d,\.KkMm]+)",
        ]

        for p in patterns_gross:
            v = self.find_first([p])
            if v:
                ytd_gross = self.safe_float(v)
                break

        for p in patterns_net:
            v = self.find_first([p])
            if v:
                ytd_net = self.safe_float(v)
                break

        for p in patterns_tax:
            v = self.find_first([p])
            if v:
                ytd_taxes = self.safe_float(v)
                break

        return ytd_gross, ytd_net, ytd_taxes
