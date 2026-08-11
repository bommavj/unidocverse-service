"""
Domain-aware document intelligence pipeline.
GET /api/pipeline/{doc_id}
"""
from __future__ import annotations

import re
from datetime import datetime, date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.models.document import Document

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


# ── Domain detection ──────────────────────────────────────────────────────────

def _detect_domain(doc: Document) -> str:
    dt   = (doc.doc_type or "").lower()
    txt  = (doc.raw_text  or "").lower()

    # Tax documents — checked first because their text (brokerage 1099s, GST returns, etc.)
    # contains words like "coverage", "premium", "exclusion" that trigger false insurance hits.
    _TAX_DOC_TYPES = ("tax_form", "tax form", "tax_return", "tax return",
                      "1099", "w-2", "w2", "1040",           # US
                      "itr",                                   # India
                      "p60", "p45", "p11d", "sa100",          # UK
                      "t4", "t4a",                            # Canada
                      "group certificate",                     # Australia
                      "pay_stub", "payslip")
    _TAX_TEXT_SIGNALS = (
        "form 1099", "form w-2", "irs", "federal income tax",  # US
        "employer identification number",                       # US
        "income tax return", "tax assessment", "tax certificate",
        "taxable income", "tax authority", "fiscal year",
        "vat return", "gst return", "gst/hst",                 # VAT/GST
        "paye", "hmrc", "self assessment",                     # UK
        "cra", "canada revenue",                               # Canada
        "ato ", "australian taxation office",                  # Australia
        "income tax department", "itr-",                       # India
        "steuerbescheid", "finanzamt",                         # Germany
        "agenzia delle entrate",                               # Italy
    )
    if any(k in dt for k in _TAX_DOC_TYPES) or any(k in txt for k in _TAX_TEXT_SIGNALS):
        return "tax"

    if any(k in dt for k in ("insurance","policy","auto","home","liability")) or \
       any(k in txt for k in ("policy number","named insured","coverage","premium","deductible","bodily injury")):
        return "insurance"

    if any(k in dt for k in ("contract","agreement","legal","nda","litigation")) or \
       any(k in txt for k in ("whereas","hereinafter","indemnif","governing law","party of the first")):
        return "legal"

    if any(k in dt for k in ("medical","health","clinical","prescription")) or \
       any(k in txt for k in ("patient","diagnosis","icd-","cpt code","physician")):
        return "medical"

    if any(k in dt for k in ("hr","employment","offer","payroll")) or \
       any(k in txt for k in ("employee","compensation","termination","non-compete","at-will")):
        return "hr"

    if any(k in dt for k in ("invoice","finance","receipt","purchase order")) or \
       any(k in txt for k in ("invoice","amount due","payment terms","purchase order","total amount")):
        return "finance"

    return "general"


# ── Shared helpers ────────────────────────────────────────────────────────────

def _size_str(doc: Document) -> str:
    if doc.file_size:
        kb = doc.file_size / 1024
        return f"{kb:.1f} KB" if kb < 1024 else f"{kb/1024:.1f} MB"
    return "—"


def _mime_short(doc: Document) -> str:
    m = doc.mime_type or ""
    if "pdf" in m:   return "PDF"
    if "word" in m:  return "DOCX"
    if "text" in m:  return "TXT"
    ext = (doc.filename or "").rsplit(".", 1)[-1].upper()
    return ext or "FILE"


def _find_dates(text: str) -> list[str]:
    pat = r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    return re.findall(pat, text)


def _extract_money(text: str) -> list[str]:
    return re.findall(r"\$[\d,]+(?:\.\d+)?", text)


def _parse_date(s: str) -> date | None:
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _days_until(d: date) -> int:
    return (d - date.today()).days


def _step(key: str, label: str, status: str, detail: str,
          data: dict | None = None, items: list[str] | None = None) -> dict:
    return {
        "key":    key,
        "label":  label,
        "status": status,
        "detail": detail,
        "data":   data   or {},
        "items":  items  or [],
    }


# ── Pipeline builders ─────────────────────────────────────────────────────────

def _insurance_pipeline(doc: Document, db: Session) -> tuple[str, list[dict]]:
    txt   = (doc.raw_text or "").lower()
    raw   = doc.raw_text or ""
    meta  = doc.doc_metadata or {}
    steps: list[dict] = []

    # 1. extract
    pages = meta.get("page_count", "—")
    steps.append(_step("extract", "Upload & Extract", "ok",
                       f"{pages} pages · {_mime_short(doc)} · {_size_str(doc)}"))

    # 2. classify
    policy_type = "Unknown"
    for ptype in ("auto","home","commercial","life","renters","umbrella"):
        if ptype in txt:
            policy_type = ptype.title()
            break

    carrier_m = re.search(r"(carrier|insurer|company)[:\s]+([A-Za-z &,]+)", raw, re.I)
    carrier   = carrier_m.group(2).strip() if carrier_m else "—"

    state_m = re.search(r"\b([A-Z]{2})\b", raw)
    state   = state_m.group(1) if state_m else "—"

    subtype = f"{policy_type.lower()}_policy"
    steps.append(_step("classify", "Policy Classification", "ok",
                       f"{policy_type} policy · Carrier: {carrier} · State: {state}",
                       {"policy_type": policy_type, "carrier": carrier, "state": state}))

    # 3. coverage
    coverage_keys = {
        "bodily_injury":     ["bodily injury","bi limit"],
        "property_damage":   ["property damage","pd limit"],
        "pip":               ["pip","personal injury protection"],
        "deductible":        ["deductible"],
        "collision":         ["collision"],
        "comprehensive":     ["comprehensive","comp coverage"],
    }
    coverage: dict[str, str] = {}
    for ckey, keywords in coverage_keys.items():
        for kw in keywords:
            idx = txt.find(kw)
            if idx >= 0:
                snippet = raw[max(0, idx - 5):idx + 80]
                m = re.search(r"\$[\d,]+", snippet)
                if m:
                    coverage[ckey] = m.group(0)
                    break

    cov_status = "ok" if coverage else "warning"
    cov_items  = [f"{k.replace('_',' ').title()}: {v}" for k, v in coverage.items()]
    steps.append(_step("coverage", "Coverage Extraction",
                       cov_status,
                       f"{len(coverage)} coverage limits found" if coverage else "No coverage limits detected",
                       coverage, cov_items))

    # 4. insured
    insured_m = re.search(r"named insured[:\s]+([^\n\r]{2,60})", raw, re.I)
    vehicle_m = re.search(r"vehicle[:\s]+([^\n\r]{2,80})", raw, re.I)
    prop_m    = re.search(r"property address[:\s]+([^\n\r]{2,80})", raw, re.I)
    insured_data = {
        "named_insured": insured_m.group(1).strip() if insured_m else "—",
        "vehicle":       vehicle_m.group(1).strip() if vehicle_m else "—",
        "property":      prop_m.group(1).strip()    if prop_m    else "—",
    }
    steps.append(_step("insured", "Named Insured & Risk", "ok",
                       f"Insured: {insured_data['named_insured']}",
                       insured_data))

    # 5. jurisdiction
    jur_status = "ok"
    jur_items: list[str] = []
    try:
        rows = db.execute(
            text("SELECT * FROM jurisdictions WHERE country='US' AND state_code=:s"),
            {"s": state}
        ).mappings().all()
        if rows:
            for row in rows:
                min_bi = row.get("min_bodily_injury") or 0
                if coverage.get("bodily_injury"):
                    bi_val = int(re.sub(r"[$,]", "", coverage["bodily_injury"]) or 0)
                    if bi_val < min_bi:
                        jur_items.append(f"BI limit ${bi_val:,} below state minimum ${min_bi:,}")
                        jur_status = "warning"
        jur_detail = f"State {state} — {'compliance checked' if rows else 'no minimum data found'}"
    except Exception:
        jur_detail = f"State {state} — jurisdiction table not available"
    steps.append(_step("jurisdiction", "Jurisdiction Compliance", jur_status,
                       jur_detail, {"state": state}, jur_items))

    # 6. risk flags
    risk_keywords = ["auto-renew","automatic renewal","rate increase","exclusion",
                     "not covered","void","cancellation"]
    flags = [kw for kw in risk_keywords if kw in txt]
    rf_status = "warning" if flags else "ok"
    steps.append(_step("risk_flags", "Risk Flags",
                       rf_status,
                       f"{len(flags)} risk flag(s) found" if flags else "No risk flags detected",
                       {}, [f"Flag: {f}" for f in flags]))

    # 7. expiry
    exp_m = re.search(
        r"(?:expir|expiration|policy period)[^\d]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        raw, re.I
    )
    exp_status = "ok"
    exp_detail = "No expiry date found"
    exp_data: dict[str, Any] = {}
    if exp_m:
        exp_str = exp_m.group(1)
        d_obj   = _parse_date(exp_str)
        if d_obj:
            days = _days_until(d_obj)
            exp_data = {"expiry_date": exp_str, "days_until_expiry": days}
            if days < 0:
                exp_status = "error"
                exp_detail = f"Policy EXPIRED on {exp_str} ({abs(days)} days ago)"
            elif days < 30:
                exp_status = "error"
                exp_detail = f"Policy expires {exp_str} — {days} days remaining (urgent)"
            elif days < 90:
                exp_status = "warning"
                exp_detail = f"Policy expires {exp_str} — {days} days remaining"
            else:
                exp_detail = f"Policy expires {exp_str} — {days} days remaining"
        else:
            exp_detail = f"Expiry date found: {exp_str}"
    steps.append(_step("expiry", "Expiry & Renewal", exp_status, exp_detail, exp_data))

    # 8. gaps
    gap_items: list[str] = []
    if policy_type.lower() == "auto":
        if "um" not in txt and "uninsured" not in txt:
            gap_items.append("Missing UM/UIM (Uninsured/Underinsured Motorist) coverage")
    if "umbrella" not in txt:
        gap_items.append("Consider umbrella policy for additional liability protection")
    if policy_type.lower() == "commercial" and "cyber" not in txt:
        gap_items.append("No cyber liability coverage detected — consider adding")
    g_status = "warning" if gap_items else "ok"
    steps.append(_step("gaps", "Gap Analysis", g_status,
                       f"{len(gap_items)} coverage gap(s) identified" if gap_items else "No coverage gaps found",
                       {}, gap_items))

    # 9. actions
    action_items: list[str] = []
    for s in steps:
        if s["status"] in ("warning","error"):
            action_items.append(f"[{s['status'].upper()}] {s['label']}: {s['detail']}")
        action_items.extend([f"  • {it}" for it in s.get("items", [])])
    action_items = [i for i in action_items if i.strip()][:12]
    act_status = "action_required" if action_items else "ok"
    steps.append(_step("actions", "Action Required",
                       "action" if action_items else "ok",
                       f"{len(action_items)} action(s) required" if action_items else "No actions required",
                       {}, action_items))

    return subtype, steps


def _legal_pipeline(doc: Document, _db: Session) -> tuple[str, list[dict]]:
    txt  = (doc.raw_text or "").lower()
    raw  = doc.raw_text or ""
    meta = doc.doc_metadata or {}
    steps: list[dict] = []

    # 1. extract
    pages = meta.get("page_count", "—")
    steps.append(_step("extract", "Upload & Extract", "ok",
                       f"{pages} pages · {_mime_short(doc)} · {_size_str(doc)}"))

    # 2. classify
    doc_subtypes = {
        "nda": ["nda","non-disclosure","confidentiality agreement"],
        "msa": ["master service","msa"],
        "sow": ["statement of work","sow"],
        "employment_agreement": ["employment agreement","employment contract"],
        "lease": ["lease","landlord","tenant"],
        "settlement": ["settlement","release of claims"],
        "litigation": ["complaint","plaintiff","defendant","court"],
    }
    subtype = "legal_document"
    for st, keywords in doc_subtypes.items():
        if any(k in txt for k in keywords):
            subtype = st
            break
    steps.append(_step("classify", "Document Classification", "ok",
                       f"Type: {subtype.replace('_',' ').title()}",
                       {"doc_subtype": subtype}))

    # 3. parties
    party_m = re.search(r"between\s+(.{2,60}?)\s+and\s+(.{2,60?})", raw, re.I)
    parties = {
        "party_a": party_m.group(1).strip() if party_m else "—",
        "party_b": party_m.group(2).strip() if party_m else "—",
    }
    steps.append(_step("parties", "Party Extraction", "ok",
                       f"Party A: {parties['party_a']} / Party B: {parties['party_b']}",
                       parties))

    # 4. key dates
    dates = _find_dates(raw)
    steps.append(_step("key_dates", "Key Dates",
                       "ok" if dates else "warning",
                       f"{len(dates)} date(s) found: {', '.join(dates[:3])}" if dates else "No dates detected",
                       {"dates": dates[:10]}))

    # 5. obligations
    money = _extract_money(raw)
    steps.append(_step("obligations", "Obligations & Terms",
                       "ok" if money else "pending",
                       f"Amounts: {', '.join(money[:4])}" if money else "No financial amounts detected",
                       {"amounts": money[:10]}))

    # 6. risk flags
    risk_kws = ["indemnif","unlimited liability","sole discretion","auto-renew",
                "liquidated damages","non-compete","assigns"]
    flags = [k for k in risk_kws if k in txt]
    steps.append(_step("risk_flags", "Risk Flags",
                       "warning" if flags else "ok",
                       f"{len(flags)} risk clause(s) found" if flags else "No risk clauses detected",
                       {}, [f"Risk clause: {f}" for f in flags]))

    # 7. governing law
    glaw_m = re.search(r"governed by.{0,30}laws? of.{0,40}", raw, re.I)
    state_m = re.search(r"state of ([A-Za-z ]{3,30})", raw, re.I)
    glaw = glaw_m.group(0)[:60] if glaw_m else (state_m.group(0) if state_m else "Not specified")
    steps.append(_step("governing_law", "Governing Law", "ok", glaw, {"jurisdiction": glaw}))

    # 8. actions
    action_items: list[str] = []
    for s in steps:
        if s["status"] in ("warning","error"):
            action_items.append(f"[{s['status'].upper()}] {s['label']}: {s['detail']}")
    action_items = action_items[:10]
    steps.append(_step("actions", "Action Required",
                       "action" if action_items else "ok",
                       f"{len(action_items)} action(s) required" if action_items else "No actions required",
                       {}, action_items))

    return subtype, steps


def _medical_pipeline(doc: Document, _db: Session) -> tuple[str, list[dict]]:
    txt  = (doc.raw_text or "").lower()
    raw  = doc.raw_text or ""
    meta = doc.doc_metadata or {}
    steps: list[dict] = []

    pages = meta.get("page_count", "—")
    steps.append(_step("extract", "Upload & Extract", "ok",
                       f"{pages} pages · {_mime_short(doc)} · {_size_str(doc)}"))

    subtypes_map = {
        "prescription":   ["prescription","rx","refill"],
        "lab_result":     ["lab result","laboratory","blood test","urinalysis"],
        "clinical_notes": ["clinical notes","progress note","soap"],
        "eob":            ["explanation of benefits","eob","claim number"],
        "referral":       ["referral","specialist","referred to"],
    }
    subtype = "medical_document"
    for st, kws in subtypes_map.items():
        if any(k in txt for k in kws):
            subtype = st
            break
    steps.append(_step("classify", "Document Classification", "ok",
                       f"Type: {subtype.replace('_',' ').title()}",
                       {"doc_subtype": subtype}))

    # patient
    name_m  = re.search(r"patient[:\s]+([A-Za-z ,]{2,50})", raw, re.I)
    dob_m   = re.search(r"(?:dob|date of birth)[:\s]+(\S+)", raw, re.I)
    mrn_m   = re.search(r"mrn[:\s]+(\S+)", raw, re.I)
    patient = {
        "name": (name_m.group(1).strip()[:4] + "****") if name_m else "—",
        "dob":  "****" if dob_m else "—",
        "mrn":  ("MRN-" + mrn_m.group(1)[-3:]) if mrn_m else "—",
    }
    steps.append(_step("patient", "Patient Information", "ok",
                       f"Patient on record — PII masked for privacy", patient))

    # clinical
    icd_codes = re.findall(r"[A-Z]\d{2}\.?\d*", raw)
    cpt_codes = re.findall(r"(?:cpt[:\s]*)(\d{5})", raw, re.I)
    meds      = re.findall(r"(?:medication|drug|rx)[:\s]+([A-Za-z ]{2,30})", raw, re.I)
    steps.append(_step("clinical", "Clinical Data",
                       "ok" if (icd_codes or cpt_codes) else "warning",
                       f"ICD codes: {', '.join(icd_codes[:4]) or 'none'} · CPT: {', '.join(cpt_codes[:4]) or 'none'}",
                       {"icd_codes": icd_codes[:10], "cpt_codes": cpt_codes[:10], "medications": meds[:5]}))

    # provider
    phys_m = re.search(r"(?:physician|provider|dr\.?)[:\s]+([A-Za-z \.]{2,60})", raw, re.I)
    npi_m  = re.search(r"npi[:\s]+(\d{10})", raw, re.I)
    steps.append(_step("provider", "Provider Details", "ok",
                       f"Provider: {phys_m.group(1).strip() if phys_m else '—'} · NPI: {npi_m.group(1) if npi_m else '—'}",
                       {"physician": phys_m.group(1).strip() if phys_m else "—",
                        "npi": npi_m.group(1) if npi_m else "—"}))

    # hipaa
    hipaa_flags: list[str] = []
    if re.search(r"\b\d{3}-\d{2}-\d{4}\b", raw):
        hipaa_flags.append("SSN pattern detected — high PII risk")
    if re.search(r"\b\d{5}(?:-\d{4})?\b", raw) and name_m:
        hipaa_flags.append("Name + ZIP code present — possible PII exposure")
    if dob_m and name_m:
        hipaa_flags.append("Full DOB alongside name — PHI exposure risk")
    steps.append(_step("hipaa", "HIPAA Compliance",
                       "error" if hipaa_flags else "ok",
                       f"{len(hipaa_flags)} HIPAA risk(s) detected" if hipaa_flags else "No obvious HIPAA violations",
                       {}, hipaa_flags))

    action_items: list[str] = []
    for s in steps:
        if s["status"] in ("warning","error"):
            action_items.append(f"[{s['status'].upper()}] {s['label']}: {s['detail']}")
    steps.append(_step("actions", "Action Required",
                       "action" if action_items else "ok",
                       f"{len(action_items)} action(s) required" if action_items else "No actions required",
                       {}, action_items))

    return subtype, steps


def _hr_pipeline(doc: Document, _db: Session) -> tuple[str, list[dict]]:
    txt  = (doc.raw_text or "").lower()
    raw  = doc.raw_text or ""
    meta = doc.doc_metadata or {}
    steps: list[dict] = []

    pages = meta.get("page_count", "—")
    steps.append(_step("extract", "Upload & Extract", "ok",
                       f"{pages} pages · {_mime_short(doc)} · {_size_str(doc)}"))

    subtypes_map = {
        "offer_letter":          ["offer letter","pleased to offer"],
        "employment_contract":   ["employment contract","terms of employment"],
        "termination":           ["termination letter","last day","separation"],
        "performance_review":    ["performance review","annual review","self-assessment"],
        "nda":                   ["non-disclosure","confidentiality"],
    }
    subtype = "hr_document"
    for st, kws in subtypes_map.items():
        if any(k in txt for k in kws):
            subtype = st
            break
    steps.append(_step("classify", "Document Classification", "ok",
                       f"Type: {subtype.replace('_',' ').title()}",
                       {"doc_subtype": subtype}))

    # employee
    emp_m  = re.search(r"(?:employee|candidate|dear)\s+([A-Za-z \.]{2,50})", raw, re.I)
    role_m = re.search(r"(?:role|position|title)[:\s]+([^\n\r]{2,60})", raw, re.I)
    sal_m  = re.search(r"(?:salary|compensation|annual)[:\s]*(\$[\d,]+)", raw, re.I)
    emp_data = {
        "name":         emp_m.group(1).strip()  if emp_m  else "—",
        "role":         role_m.group(1).strip() if role_m else "—",
        "compensation": sal_m.group(1)          if sal_m  else "—",
    }
    steps.append(_step("employee", "Employee Details", "ok",
                       f"{emp_data['name']} · {emp_data['role']} · {emp_data['compensation']}",
                       emp_data))

    # key clauses
    nc_dur  = re.search(r"non-compete.{0,60}(\d+)\s*(?:month|year)", raw, re.I)
    sev_m   = re.search(r"severance.{0,40}(\$[\d,]+|\d+\s*week)", raw, re.I)
    notice_m= re.search(r"notice period.{0,40}(\d+\s*(?:day|week|month))", raw, re.I)
    clauses = {
        "non_compete_duration": nc_dur.group(1)  + " " + (nc_dur.group(0).split()[-1] if nc_dur else "") if nc_dur else "—",
        "severance":            sev_m.group(1)   if sev_m   else "—",
        "notice_period":        notice_m.group(1) if notice_m else "—",
    }
    steps.append(_step("key_clauses", "Key Clauses", "ok",
                       "Non-compete, severance, notice period extracted",
                       clauses))

    # risk flags
    risk_items: list[str] = []
    if nc_dur:
        try:
            months = int(nc_dur.group(1))
            if "year" in nc_dur.group(0).lower():
                months *= 12
            if months > 24:
                risk_items.append(f"Overly broad non-compete: {months} months")
        except ValueError:
            pass
    if "at-will" not in txt and "at will" not in txt:
        risk_items.append("Missing at-will employment statement")
    if "signature" not in txt and "signed" not in txt:
        risk_items.append("Document may be unsigned — verify execution")
    steps.append(_step("risk_flags", "Risk Flags",
                       "warning" if risk_items else "ok",
                       f"{len(risk_items)} risk(s) identified" if risk_items else "No risk flags",
                       {}, risk_items))

    action_items: list[str] = []
    for s in steps:
        if s["status"] in ("warning","error"):
            action_items.append(f"[{s['status'].upper()}] {s['label']}: {s['detail']}")
    steps.append(_step("actions", "Action Required",
                       "action" if action_items else "ok",
                       f"{len(action_items)} action(s) required" if action_items else "No actions required",
                       {}, action_items))

    return subtype, steps


def _finance_pipeline(doc: Document, _db: Session) -> tuple[str, list[dict]]:
    txt  = (doc.raw_text or "").lower()
    raw  = doc.raw_text or ""
    meta = doc.doc_metadata or {}
    steps: list[dict] = []

    pages = meta.get("page_count", "—")
    steps.append(_step("extract", "Upload & Extract", "ok",
                       f"{pages} pages · {_mime_short(doc)} · {_size_str(doc)}"))

    subtypes_map = {
        "invoice":          ["invoice","bill to"],
        "purchase_order":   ["purchase order","p.o. number","po #"],
        "receipt":          ["receipt","paid","payment received"],
        "statement":        ["statement","account summary"],
        "contract":         ["contract","agreement"],
    }
    subtype = "finance_document"
    for st, kws in subtypes_map.items():
        if any(k in txt for k in kws):
            subtype = st
            break
    steps.append(_step("classify", "Document Classification", "ok",
                       f"Type: {subtype.replace('_',' ').title()}",
                       {"doc_subtype": subtype}))

    # financial data
    amounts   = _extract_money(raw)
    due_dates = _find_dates(raw)
    vendor_m  = re.search(r"(?:vendor|from|bill from|seller)[:\s]+([A-Za-z \.]{2,60})", raw, re.I)
    po_m      = re.search(r"(?:po|p\.o\.|purchase order)\s*#?\s*(\w{3,20})", raw, re.I)
    total_m   = re.search(r"(?:total|amount due|grand total)[:\s]*(\$[\d,]+(?:\.\d+)?)", raw, re.I)

    fin_data: dict[str, Any] = {
        "vendor":        vendor_m.group(1).strip() if vendor_m else "—",
        "po_number":     po_m.group(1)             if po_m    else "—",
        "total_amount":  total_m.group(1)          if total_m else (amounts[0] if amounts else "—"),
        "due_date":      due_dates[0]              if due_dates else "—",
        "amounts_found": amounts[:8],
    }
    steps.append(_step("financial", "Financial Data", "ok",
                       f"Total: {fin_data['total_amount']} · Due: {fin_data['due_date']} · Vendor: {fin_data['vendor']}",
                       fin_data))

    # validation
    val_issues: list[str] = []
    if fin_data["due_date"] != "—":
        d_obj = _parse_date(fin_data["due_date"])
        if d_obj and _days_until(d_obj) < 0:
            val_issues.append(f"Due date {fin_data['due_date']} is in the past — overdue")
    if fin_data["po_number"] == "—":
        val_issues.append("No PO number found")
    if total_m:
        total_val = float(re.sub(r"[$,]", "", total_m.group(1)) or 0)
        if total_val > 10_000:
            val_issues.append(f"Large amount: {total_m.group(1)} — requires approval workflow")
    steps.append(_step("validation", "Validation",
                       "warning" if val_issues else "ok",
                       f"{len(val_issues)} validation issue(s)" if val_issues else "Validation passed",
                       {}, val_issues))

    # risk flags
    risk_items: list[str] = []
    if fin_data["due_date"] != "—":
        d_obj = _parse_date(fin_data["due_date"])
        if d_obj and _days_until(d_obj) < 0:
            risk_items.append("Invoice overdue")
    if fin_data["po_number"] == "—":
        risk_items.append("Missing PO number — untraceable purchase")
    if total_m and float(re.sub(r"[$,]", "", total_m.group(1)) or 0) > 10_000:
        risk_items.append("Amount exceeds $10,000 — flag for senior review")
    steps.append(_step("risk_flags", "Risk Flags",
                       "warning" if risk_items else "ok",
                       f"{len(risk_items)} flag(s)" if risk_items else "No risk flags",
                       {}, risk_items))

    action_items: list[str] = []
    for s in steps:
        if s["status"] in ("warning","error"):
            action_items.append(f"[{s['status'].upper()}] {s['label']}: {s['detail']}")
    steps.append(_step("actions", "Action Required",
                       "action" if action_items else "ok",
                       f"{len(action_items)} action(s) required" if action_items else "No actions required",
                       {}, action_items))

    return subtype, steps


def _tax_pipeline(doc: Document, _db: Session) -> tuple[str, list[dict]]:
    raw  = doc.raw_text or ""
    meta = doc.doc_metadata or {}
    steps: list[dict] = []

    pages = meta.get("page_count", "—")
    steps.append(_step("extract", "Upload & Extract", "ok",
                       f"{pages} pages · {_mime_short(doc)} · {_size_str(doc)}"))

    # Identify tax form subtype — US and international
    txt = raw.lower()
    SUBTYPE_RULES = [
        # US forms
        ("1099-B (Brokerage — US)",         ["1099-b", "proceeds from broker"]),
        ("1099-DIV (Dividends — US)",        ["1099-div"]),
        ("1099-INT (Interest — US)",         ["1099-int"]),
        ("1099-R (Retirement — US)",         ["1099-r", "distribution from pensions"]),
        ("1099-MISC (Miscellaneous — US)",   ["1099-misc"]),
        ("1099-NEC (Non-Employee — US)",     ["1099-nec"]),
        ("1099 (Tax Form — US)",             ["1099"]),
        ("W-2 (Wages — US)",                 ["w-2", "wages, tips"]),
        ("Form 1040 (Tax Return — US)",      ["form 1040", "1040-sr", "1040-nr"]),
        # UK
        ("P60 (Year-End Summary — UK)",      ["p60"]),
        ("P45 (Leaving Employment — UK)",    ["p45"]),
        ("SA100 (Self Assessment — UK)",     ["sa100", "self assessment tax return"]),
        # Canada
        ("T4 (Employment Income — Canada)",  ["t4 slip", "employment income"]),
        ("T4A (Other Income — Canada)",      ["t4a"]),
        # Australia
        ("Income Statement (Australia)",     ["income statement", "group certificate", "ato"]),
        # India
        ("ITR (Income Tax Return — India)",  ["itr-", "income tax department", "pan number"]),
        # VAT / GST (international)
        ("VAT Return",                       ["vat return", "value added tax return"]),
        ("GST Return",                       ["gst return", "gst/hst return"]),
        # Generic payslip / payroll
        ("Payslip / Pay Stub",               ["payslip", "pay stub", "gross pay", "net pay"]),
    ]
    subtype = "Tax Document"
    for label, keywords in SUBTYPE_RULES:
        if any(k in txt for k in keywords):
            subtype = label
            break
    steps.append(_step("classify", "Tax Form Type", "ok", subtype))

    # Taxpayer / account info
    taxpayer_m = re.search(r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+\d+\s+\w+.*(?:St|Ave|Dr|Rd|Way|Blvd|Ln)", raw)
    account_m  = re.search(r"(?:account|acct)[.#:\s]+(\w{6,20})", raw, re.I)
    tax_year_m = re.search(r"(?:tax year|year)\s*[:\-]?\s*(20\d{2})", raw, re.I) or \
                 re.search(r"\b(20\d{2})\b", raw)
    steps.append(_step("taxpayer", "Taxpayer & Account", "ok",
                       f"Account: {account_m.group(1) if account_m else '—'} · "
                       f"Tax Year: {tax_year_m.group(1) if tax_year_m else '—'}"))

    # Key financial figures
    amounts = _extract_money(raw)
    proceeds_m    = re.search(r"(?:total proceeds|gross proceeds)[:\s]*(\$[\d,]+(?:\.\d+)?)", raw, re.I)
    cost_basis_m  = re.search(r"(?:cost or other basis|total cost basis)[:\s]*(\$[\d,]+(?:\.\d+)?)", raw, re.I)
    gain_loss_m   = re.search(r"(?:net gain or loss|gain or loss)[:\s]*(-?\$[\d,]+(?:\.\d+)?)", raw, re.I)
    dividends_m   = re.search(r"(?:total dividends|ordinary dividends)[:\s]*(\$[\d,]+(?:\.\d+)?)", raw, re.I)
    wash_sale_m   = re.search(r"(?:wash sale loss disallowed)[:\s]*(\$[\d,]+(?:\.\d+)?)", raw, re.I)

    fin_summary = []
    if proceeds_m:   fin_summary.append(f"Proceeds: {proceeds_m.group(1)}")
    if cost_basis_m: fin_summary.append(f"Cost Basis: {cost_basis_m.group(1)}")
    if gain_loss_m:  fin_summary.append(f"Net G/L: {gain_loss_m.group(1)}")
    if dividends_m:  fin_summary.append(f"Dividends: {dividends_m.group(1)}")
    if wash_sale_m:  fin_summary.append(f"Wash Sales: {wash_sale_m.group(1)}")
    steps.append(_step("financials", "Key Financial Figures", "ok",
                       " · ".join(fin_summary) if fin_summary else f"{len(amounts)} amounts found"))

    # Risk flags
    risk_items: list[str] = []
    if wash_sale_m:
        risk_items.append(f"Wash sale loss disallowed: {wash_sale_m.group(1)} — review before filing")
    if re.search(r"section 1256|regulated futures|nonequity options", raw, re.I):
        risk_items.append("Section 1256 contracts detected — mark-to-market rules apply")
    if re.search(r"covered\s+lot.*not.*reported|box 12.*not.*checked", raw, re.I):
        risk_items.append("Some securities not reported to IRS — verify on Schedule D")
    if re.search(r"cryptocurrency|crypto|bitcoin|ethereum|shiba", raw, re.I):
        risk_items.append("Cryptocurrency transactions detected — Form 8949 required")
    steps.append(_step("risk_flags", "Tax Risk Flags",
                       "warning" if risk_items else "ok",
                       f"{len(risk_items)} flag(s)" if risk_items else "No tax risk flags",
                       {}, risk_items))

    # Actions
    action_items: list[str] = []
    if wash_sale_m:
        action_items.append("Review wash sale disallowances before filing — may affect loss deductions")
    if re.search(r"section 1256", raw, re.I):
        action_items.append("Report Section 1256 gains/losses on Form 6781")
    if re.search(r"short.term.*covered|long.term.*covered", raw, re.I):
        action_items.append("Transfer covered lot totals to Schedule D / Form 8949")
    steps.append(_step("actions", "Tax Filing Actions",
                       "action" if action_items else "ok",
                       f"{len(action_items)} action(s)" if action_items else "No filing actions required",
                       {}, action_items))

    return subtype, steps


def _general_pipeline(doc: Document, _db: Session) -> tuple[str, list[dict]]:
    meta  = doc.doc_metadata or {}
    pages = meta.get("page_count", "—")
    steps = [
        _step("extract",   "Upload & Extract",       "ok",     f"{pages} pages · {_mime_short(doc)} · {_size_str(doc)}"),
        _step("classify",  "Document Classification","ok",     f"Type: {(doc.doc_type or 'unknown').replace('_',' ').title()}"),
        _step("content",   "Content Analysis",       "ok",     f"{len(doc.raw_text or '')} characters extracted"),
        _step("actions",   "Action Required",        "ok",     "No specific domain detected — general analysis complete"),
    ]
    return "general_document", steps


# ── Main endpoint ─────────────────────────────────────────────────────────────

DOMAIN_BUILDERS = {
    "insurance": _insurance_pipeline,
    "legal":     _legal_pipeline,
    "medical":   _medical_pipeline,
    "hr":        _hr_pipeline,
    "finance":   _finance_pipeline,
    "tax":       _tax_pipeline,
    "general":   _general_pipeline,
}


def _overall_status(steps: list[dict], action_count: int) -> str:
    statuses = [s["status"] for s in steps]
    if "error" in statuses:
        return "error"
    if action_count > 0:
        return "action_required"
    if "warning" in statuses:
        return "warning"
    return "ok"


@router.get("/{doc_id}")
def get_pipeline(doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    domain  = _detect_domain(doc)
    builder = DOMAIN_BUILDERS.get(domain, _general_pipeline)
    subtype, steps = builder(doc, db)

    action_step   = next((s for s in steps if s["key"] == "actions"), None)
    action_count  = len(action_step["items"]) if action_step else 0
    overall       = _overall_status(steps, action_count)

    return {
        "doc_id":         doc_id,
        "domain":         domain,
        "doc_subtype":    subtype,
        "overall_status": overall,
        "action_count":   action_count,
        "steps":          steps,
    }
