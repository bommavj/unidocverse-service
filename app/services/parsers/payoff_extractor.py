# payoff_extractor.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import pytesseract
    from pdf2image import convert_from_path
except ImportError:
    pytesseract = None
    convert_from_path = None


@dataclass
class PayoffValues:
    payoff_amount: float = 0.0
    principal_balance: float = 0.0
    interest_due: float = 0.0
    total_fees: float = 0.0
    escrow_balance: float = 0.0
    suspense_balance: float = 0.0


# ---------- helpers ----------

_money_re = re.compile(r"\$?\s*([0-9][0-9,]*\.?[0-9]{0,2})")

def _parse_money(s: str) -> Optional[float]:
    if not s:
        return None
    m = _money_re.search(s.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# ---------- layer 1: form fields ----------

def extract_form_fields(path: str) -> Dict[str, Any]:
    if not PdfReader:
        return {}
    try:
        reader = PdfReader(path)
        fields = reader.get_fields() or {}
    except Exception as e:
        logger.warning(f"Form field extraction failed: {e}")
        return {}

    out: Dict[str, Any] = {}
    for k, v in fields.items():
        val = v.get("/V")
        if isinstance(val, str) and val.strip():
            out[k.lower()] = val.strip()
    return out


# ---------- layer 2: layout / tables ----------

def extract_tables_with_plumber(path: str) -> str:
    if not pdfplumber:
        return ""
    texts: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                # raw text
                texts.append(page.extract_text() or "")
                # tables
                tables = page.extract_tables() or []
                for tbl in tables:
                    for row in tbl:
                        if not row:
                            continue
                        texts.append(" | ".join(c or "" for c in row))
    except Exception as e:
        logger.warning(f"pdfplumber extraction failed: {e}")
    return "\n".join(texts)


# ---------- layer 3: OCR fallback ----------

def ocr_full_document(path: str) -> str:
    try:
        from app.services.ocr.ocr_utils import ocr_any
        return ocr_any(path)
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return ""


# ---------- parsing from text blob ----------

def parse_payoff_from_text(text: str) -> PayoffValues:
    """
    Text may come from pdfplumber or OCR; we try to anchor on labels.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    joined = "\n".join(lines)

    def find_after(label: str) -> Optional[float]:
        # try line-based
        for ln in lines:
            if label.lower() in ln.lower():
                val = _parse_money(ln)
                if val is not None:
                    return val
        # fallback: regex around label
        m = re.search(label + r".{0,40}", joined, flags=re.IGNORECASE)
        if m:
            return _parse_money(m.group(0))
        return None

    principal = find_after("Principal Balance") or find_after("First Principal Balance")
    interest = find_after("Interest to") or find_after("Interest Due")
    total_due = find_after("Total Due") or find_after("Total Payoff")
    other_charges = find_after("Other Charges") or find_after("Other Charges Sub Total")
    escrow = find_after("Escrow") or find_after("Escrow Balance")
    suspense = find_after("Suspense") or find_after("Suspense Balance")

    # if total_due missing but principal+interest+fees present, approximate
    payoff = total_due or 0.0
    fees = other_charges or 0.0

    return PayoffValues(
        payoff_amount=payoff or 0.0,
        principal_balance=principal or 0.0,
        interest_due=interest or 0.0,
        total_fees=fees or 0.0,
        escrow_balance=escrow or 0.0,
        suspense_balance=suspense or 0.0,
    )


# ---------- main entry ----------

def extract_payoff_values(path: str) -> PayoffValues:
    """
    High-level extractor:
      1) Try form fields
      2) Try pdfplumber text/tables
      3) Try OCR
    """
    # 1) form fields
    fields = extract_form_fields(path)
    if fields:
        logger.info("Payoff extractor: using form fields")
        # very heuristic mapping; adjust to your actual field names
        principal = _parse_money(fields.get("principal", "") or fields.get("principalbalance", ""))
        payoff = _parse_money(fields.get("totaldue", "") or fields.get("payoffamount", ""))
        interest = _parse_money(fields.get("interest", "") or fields.get("interestdue", ""))
        fees = _parse_money(fields.get("fees", "") or fields.get("othercharges", ""))
        escrow = _parse_money(fields.get("escrow", "") or fields.get("escrowbalance", ""))
        suspense = _parse_money(fields.get("suspense", "") or fields.get("suspensebalance", ""))
        return PayoffValues(
            payoff_amount=payoff or 0.0,
            principal_balance=principal or 0.0,
            interest_due=interest or 0.0,
            total_fees=fees or 0.0,
            escrow_balance=escrow or 0.0,
            suspense_balance=suspense or 0.0,
        )

    # 2) pdfplumber
    text = extract_tables_with_plumber(path)
    if text.strip():
        logger.info("Payoff extractor: using pdfplumber text/tables")
        vals = parse_payoff_from_text(text)
        if any([
            vals.payoff_amount,
            vals.principal_balance,
            vals.interest_due,
            vals.total_fees,
        ]):
            return vals

    # 3) OCR
    ocr_text = ocr_full_document(path)
    if ocr_text.strip():
        logger.info("Payoff extractor: using OCR text")
        vals = parse_payoff_from_text(ocr_text)
        return vals

    logger.warning("Payoff extractor: no numeric values found; returning zeros")
    return PayoffValues()
