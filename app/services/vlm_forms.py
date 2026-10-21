"""
Form reading with a local vision model.

Boxed forms (W-2, 1099, 1040, passports, receipt notices…) print each label at the top of a box
and the value inside it, often right-aligned under the next box's label. Reading the text line
by line separates labels from values ("95709.00 12896.94 6144.20" with no box names), so the
answer model can't tell box 2 from box 4. Here the page image goes to a local vision model
(VLM_MODEL, default gemma4) which returns label → value pairs.

Nothing it reads is stored on trust: every value must appear in the page's own text (text
layer, or OCR text allowing one misread character for longer values). Values that can't be
confirmed are dropped. Confirmed fields are added to the page as a "[Form fields]" block.

VLM_FORMS=0 turns this off.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

VLM_MODEL = os.getenv("VLM_MODEL", "gemma4:latest")
MAX_PAGES = 3
DPI = 150

# Document types read as forms, and what to ask for on each
FORM_TYPES: Dict[str, str] = {
    "w2": "US W-2 (Wage and Tax Statement). Give every numbered box (1-20, 12a-12d with its code letter, 13 checkboxes) and lettered box (a-f)",
    "tax_form": "US tax form. Give every numbered/lettered box or line with its value",
    "form_1040": "US Form 1040. Give every numbered line (1a, 1z, 2a, 2b, 9, 11, 15, 16, 24, 25a-d, 33, 34, 37 …) with its value",
    "form_1099_nec": "US Form 1099-NEC. Give payer/recipient names and TINs and every numbered box",
    "form_1099_int": "US Form 1099-INT. Give payer/recipient and every numbered box",
    "form_1099_div": "US Form 1099-DIV. Give payer/recipient and every numbered box (1a, 1b, 2a …)",
    "form_1099_r": "US Form 1099-R. Give payer/recipient and every numbered box (1, 2a, 4, 7 distribution code …)",
    "form_1099_b": "US Form 1099-B. Give each transaction row and summary boxes",
    "form_1065": "US Form 1065. Give partnership name, EIN and every numbered line",
    "schedule_k1": "Schedule K-1. Give partner/partnership details and every numbered box/line",
    "form_8949": "Form 8949. Give each transaction row (description, dates, proceeds, cost, gain)",
    "w9": "Form W-9. Give name, business name, tax classification, address, TIN",
    "passport": "passport data page. Give type, country code, passport number, surname, given names, nationality, date of birth, place of birth, sex, date of issue, date of expiration, authority, and both MRZ lines",
    "visa_document": "visa / immigration document. Give every labeled field",
    "uscis_receipt": "USCIS notice (I-797). Give receipt number, case type, received date, priority date, notice date, petitioner, beneficiary, notice type, class, valid from / to, and any I-94 number and dates",
    "birth_certificate": "birth certificate. Give every labeled field",
    "payroll": "pay stub / earnings statement. Give pay period begin/end, pay date, every earnings, deduction and tax line with current and year-to-date amounts, gross pay and net pay",
    "mortgage_statement": "mortgage statement. Give every labeled field: loan number, statement date, due date, amount due, principal, interest, escrow, balances, interest rate, past payments breakdown",
    "property_tax_bill": "property tax bill. Give parcel number, bill number, tax rate area, assessed values, exemptions, each tax line and amount, installments with due dates and totals",
    "invoice": "invoice. Give invoice number, dates, vendor, bill-to, each line item and amounts, subtotal, tax, total, amount due",
}

PROMPT = ("This is a {what}. Read the page image and list every field with its value EXACTLY as printed "
          "(same digits, punctuation and spelling). Use the box or line number printed in each box's corner. "
          "If the page shows the same form twice (summary and official copy), list it once. "
          "Skip empty fields. Never guess or compute.\n"
          'Return JSON only: {{"fields": [{{"label": "printed label (with box/line number)", "value": "exact value"}}]}}')


def enabled(doc_type: Optional[str]) -> bool:
    return os.getenv("VLM_FORMS", "1") != "0" and (doc_type or "") in FORM_TYPES


def _norm(s: str) -> str:
    return re.sub(r"[\s,$]", "", s or "").lower()


def _near(a: str, b: str) -> bool:
    """a appears in b with at most one wrong character (OCR misreads on scans)."""
    if len(a) < 6:
        return False
    n = len(a)
    for i in range(0, len(b) - n + 1):
        if sum(x != y for x, y in zip(a, b[i:i + n])) <= 1:
            return True
    return False


def confirmed(value: str, page_text: str, ocr: bool) -> bool:
    v = _norm(value)
    if not v or v in ("n/a", "none", "-"):
        return False
    t = _norm(page_text)
    if v in t:
        return True
    # A well-formed amount or date may be printed differently (95709.00 vs 95,709.00, 1/9/26 vs
    # 01/09/2026) — compare as numbers. Anything else must appear as written ("34.36.00" never passes).
    amt = re.fullmatch(r"[-(]?\$?\s?(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?\)?\*?", value.strip())
    if amt:
        num = value.strip().strip("()*").replace("$", "").replace(",", "").replace(" ", "").lstrip("-")
        if re.search(r"(?<![\d.])" + re.escape(num) + r"(?![\d])", re.sub(r"(?<=\d),(?=\d)", "", page_text)):
            return True
    dm = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", value.strip())
    if dm:
        m, d, y = (int(x) for x in dm.groups())
        y = y + 2000 if y < 100 else y
        for pm, pd, py in re.findall(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", page_text):
            py = int(py) + (2000 if len(py) == 2 else 0)
            if (int(pm), int(pd), py) == (m, d, y):
                return True
    return ocr and _near(v, t)


def read_page(page, doc_type: str, page_text: str, ocr: bool) -> str:
    """'[Form fields …]' block of confirmed label: value lines for one page, or ''."""
    import ollama
    png = page.get_pixmap(dpi=DPI).tobytes("png")
    t0 = time.perf_counter()
    fields = None
    for attempt, budget in enumerate((3500, 6000)):
        try:
            r = ollama.chat(model=VLM_MODEL, format="json",
                            messages=[{"role": "user", "content": PROMPT.format(what=FORM_TYPES[doc_type]), "images": [png]}],
                            options={"temperature": 0, "num_ctx": 12288, "num_predict": budget})
            fields = _parse_fields(r["message"]["content"])
            if fields is not None:
                break
        except Exception as e:
            logger.warning(f"Form reading attempt {attempt + 1} failed: {e}")
    if not fields:
        return ""
    kept, dropped = [], 0
    for f in fields:
        label, value = str(f.get("label") or "").strip(), str(f.get("value") or "").strip()
        if not label or not value or _norm(value) == _norm(label) or re.fullmatch(r"(box\s*)?\d{1,2}[a-d]?", value, re.I):
            continue
        if confirmed(value, page_text, ocr):
            kept.append(f"{label}: {value}")
        else:
            dropped += 1
    kept = list(dict.fromkeys(kept))
    logger.info(f"🧾 Form fields: {len(kept)} confirmed, {dropped} dropped ({time.perf_counter() - t0:.0f}s)")
    if not kept:
        return ""
    return "FORM FIELDS (read from the page image; every value checked against the page text):\n" + "\n".join(kept)


def _parse_fields(raw: str):
    """JSON fields; a reply cut off mid-list keeps every complete field before the cut."""
    try:
        return json.loads(raw).get("fields", [])
    except Exception:
        pass
    items = re.findall(r'\{\s*"label"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"value"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}', raw or "")
    return [{"label": a, "value": b} for a, b in items] or None


def worth_reading(page_text: str, scanned: bool = False) -> bool:
    """Pages with values on them — not the instruction pages printed on the back of forms.
    A scanned page is always read: its OCR text is the weakest evidence we have."""
    if scanned:
        return True
    nums = len(re.findall(r"\d[\d,]*\.\d{2}|\b\d{3,}\b|\d{1,2}/\d{1,2}/\d{2,4}", page_text or ""))
    words = len(re.findall(r"[A-Za-z]{3,}", page_text or ""))
    return nums >= 3 and (words == 0 or nums / max(words, 1) > 0.02)
