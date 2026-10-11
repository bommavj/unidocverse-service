from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
from app.core.tenant_scope import scope_sql, tenant_params, current_tenant

router = APIRouter(prefix="/api/domains", tags=["multidomain"])


def _tenant() -> str:
    return get_current_tenant_id() or DEFAULT_TENANT_ID

# ==============================================================================
# LEGAL & COMPLIANCE ENDPOINTS
# ==============================================================================

def scoped(sql: str, params=None):
    """Tenant-scoped query: domain tables are filtered to the current tenant's documents."""
    return text(scope_sql(sql)), tenant_params(params)


@router.get("/legal/clauses")
def get_legal_clauses(db: Session = Depends(get_db)):
    # Query the real legal_clauses table
    rows = db.execute(*scoped("SELECT clause_type, clause_name, clause_text, is_deviation, governing_law FROM legal_clauses ORDER BY created_at DESC")).fetchall()
    db_clauses = []
    for r in rows:
        db_clauses.append({
            "type": r.clause_type,
            "name": r.clause_name,
            "text": r.clause_text,
            "deviation": r.is_deviation,
            "governingLaw": r.governing_law or "Delaware"
        })
    return db_clauses

@router.get("/legal/lifecycle")
def get_legal_lifecycle(db: Session = Depends(get_db)):
    # Query the real legal_contracts table
    rows = db.execute(*scoped("SELECT contract_name, counterparty, notice_period, expiry_date, risk_rating, status FROM legal_contracts ORDER BY created_at DESC")).fetchall()
    db_contracts = []
    for r in rows:
        db_contracts.append({
            "name": r.contract_name,
            "counterparty": r.counterparty,
            "notice": r.notice_period,
            "expiry": str(r.expiry_date) if r.expiry_date else "2027-12-31",
            "risk": r.risk_rating,
            "status": r.status
        })
    return db_contracts

# ==============================================================================
# HEALTHCARE ENDPOINTS
# ==============================================================================

@router.get("/healthcare/biomarkers")
def get_healthcare_biomarkers(db: Session = Depends(get_db)):
    history = {
        "cholesterol": [],
        "glucose": [],
        "triglycerides": []
    }

    # Query the real patient_biomarkers table
    rows = db.execute(*scoped("SELECT biomarker_name, biomarker_value FROM patient_biomarkers ORDER BY created_at ASC")).fetchall()
    for r in rows:
        name = r.biomarker_name.lower()
        if "cholesterol" in name and "total" in name:
            history["cholesterol"].append(int(r.biomarker_value))
        elif "glucose" in name:
            history["glucose"].append(int(r.biomarker_value))
        elif "triglycerides" in name:
            history["triglycerides"].append(int(r.biomarker_value))
    return history

@router.get("/healthcare/alerts")
def get_healthcare_alerts(db: Session = Depends(get_db)):
    # Query the real patient_biomarkers table where is_abnormal = True
    rows = db.execute(*scoped("SELECT biomarker_name, biomarker_value, unit, reference_range, physician FROM patient_biomarkers WHERE is_abnormal = TRUE ORDER BY created_at DESC")).fetchall()
    db_alerts = []
    for r in rows:
        db_alerts.append({
            "biomarker": r.biomarker_name,
            "value": str(int(r.biomarker_value)),
            "unit": r.unit,
            "reference": r.reference_range,
            "physician": r.physician
        })
    return db_alerts

# ==============================================================================
# FINANCE ENDPOINTS
# ==============================================================================

@router.get("/finance/matches")
def get_finance_matches(db: Session = Depends(get_db)):
    # Query the real finance_matches table
    # finance_matches has no tenant column — scope through the document each match came from
    rows = db.execute(text(
        "SELECT fm.invoice_id, fm.vendor_name, fm.invoice_val, fm.po_val, fm.receipt_val, fm.match_status, fm.is_approved "
        "FROM finance_matches fm JOIN documents d ON d.id::text = fm.document_id::text "
        "WHERE d.tenant_id = :t ORDER BY fm.created_at DESC"), {"t": _tenant()}).fetchall()
    db_matches = []
    for r in rows:
        db_matches.append({
            "id": r.invoice_id,
            "vendor": r.vendor_name,
            "invoiceVal": float(r.invoice_val),
            "poVal": float(r.po_val),
            "receiptVal": float(r.receipt_val),
            "match": r.match_status,
            "approved": r.is_approved
        })
    return db_matches

# ==============================================================================
# REAL ESTATE ENDPOINTS
# ==============================================================================

@router.get("/real-estate/properties")
def get_real_estate_properties(db: Session = Depends(get_db)):
    # Query the real real_estate_properties table
    rows = db.execute(*scoped("SELECT address, buyer_name, price, progress, has_appraisal, has_clear_title, is_escrow_funded FROM real_estate_properties ORDER BY created_at DESC")).fetchall()
    db_properties = []
    for r in rows:
        db_properties.append({
            "address": r.address,
            "buyer": r.buyer_name,
            "price": float(r.price),
            "progress": r.progress,
            "appraisal": r.has_appraisal,
            "title": r.has_clear_title,
            "escrow": r.is_escrow_funded
        })
    return db_properties

# ==============================================================================
# HR & PEOPLE ENDPOINTS
# ==============================================================================

@router.get("/hr/candidates")
def get_hr_candidates(db: Session = Depends(get_db)):
    # Query the real hr_candidates table
    rows = db.execute(*scoped("SELECT candidate_name, role, experience_years, match_score, skills, feedback FROM hr_candidates ORDER BY created_at DESC")).fetchall()
    db_candidates = []
    for r in rows:
        db_candidates.append({
            "name": r.candidate_name,
            "role": r.role,
            "exp": r.experience_years,
            "match": r.match_score,
            "skills": r.skills,
            "feedback": r.feedback
        })
    return db_candidates

# ==============================================================================
# SUPPLY CHAIN ENDPOINTS
# ==============================================================================

@router.get("/supply-chain/shipments")
def get_supply_chain_shipments(db: Session = Depends(get_db)):
    # Query the real supply_chain_shipments table
    rows = db.execute(*scoped("SELECT bol_number, carrier_name, origin, destination, customs_status, is_reconciled FROM supply_chain_shipments ORDER BY created_at DESC")).fetchall()
    db_shipments = []
    for r in rows:
        db_shipments.append({
            "id": r.bol_number,
            "carrier": r.carrier_name,
            "origin": r.origin,
            "dest": r.destination,
            "customs": r.customs_status,
            "matched": r.is_reconciled
        })
    return db_shipments

@router.get("/healthcare/patients")
def get_healthcare_patients(db: Session = Depends(get_db)):
    rows = db.execute(*scoped("SELECT id, patient_name, patient_dob, chart_score, consent_signed, insurance_verified, history_filed FROM patient_charts ORDER BY created_at DESC")).fetchall()
    patients = []
    for r in rows:
        patients.append({
            "name": r.patient_name,
            "id": f"MRN-{str(r.id)[:8]}",
            "dob": r.patient_dob.strftime("%m/%d/%Y") if r.patient_dob else "01/01/1980",
            "score": r.chart_score,
            "consent": r.consent_signed,
            "insurance": r.insurance_verified,
            "eligibility": "Verified" if r.insurance_verified else "Pending",
            "lastVerifiedDate": "2026-06-05",
            "payer": "United Healthcare"
        })
    return patients

@router.get("/finance/vendors")
def get_finance_vendors(db: Session = Depends(get_db)):
    rows = db.execute(text(
        "SELECT fm.vendor_name, SUM(fm.invoice_val) AS total_spend, "
        "COUNT(CASE WHEN fm.match_status = 'failed' THEN 1 END) AS failed_matches "
        "FROM finance_matches fm JOIN documents d ON d.id::text = fm.document_id::text "
        "WHERE d.tenant_id = :t GROUP BY fm.vendor_name"), {"t": _tenant()}).fetchall()
    vendors = []
    for r in rows:
        vendors.append({
            "name": r.vendor_name,
            "category": "Vendor Services",
            "totalSpend": float(r.total_spend or 0),
            "status": "Non-Compliant" if r.failed_matches > 0 else "Compliant",
            "aging": 30 if r.failed_matches > 0 else 0
        })
    return vendors


# ── Finance documents: bank statements and tax documents the tenant uploaded ──

_TAX_TYPES = {"w2", "w4", "w9", "w8ben", "tax_form", "tax_return", "property_tax_bill", "schedule_k1",
              "sales_tax_return", "resale_certificate", "form_1040", "form_8949", "form_1065", "form_1120",
              "form_1120s", "form_1095", "form_1098", "form_1098_t", "form_1098_e"}


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


# label … amount on the same line; dates in between are fine because the amount must have cents
_AMT = r"[^\n]{0,70}?(-?\$?\s?[\d,]+\.\d{2})"


def _statement_summary(raw: str) -> dict:
    """Balances and totals from a bank / card statement's summary block (bank and card wording)."""
    import re
    t = re.sub(r"/uni([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw or "")
    def first(*labels):
        for lab in labels:
            m = re.search(lab + _AMT, t, re.I)
            if m:
                return abs(float(re.sub(r"[^\d.]", "", m.group(1))))
        return None
    out = {"opening": first(r"beginning balance", r"previous balance", r"opening balance"),
           "closing": first(r"ending balance", r"new balance", r"closing balance"),
           "money_in": first(r"deposits and (?:other )?additions", r"total deposits", r"payments(?:,| and) credits",
                             r"total credits")}
    wd = first(r"withdrawals and other subtractions", r"total withdrawals", r"purchases", r"total debits")
    fees = first(r"service fees", r"fees charged") or 0.0
    out["money_out"] = round(wd + fees, 2) if wd is not None else None
    out["closing_calculated"] = False
    if out["closing"] is None and None not in (out["opening"], out["money_in"], out["money_out"]):
        out["closing"] = round(out["opening"] + out["money_in"] - out["money_out"], 2)
        out["closing_calculated"] = True
    m = re.search(r"\bfor\s+([A-Z][a-z]+ \d{1,2}, \d{4})\s+to\s+([A-Z][a-z]+ \d{1,2}, \d{4})", t)
    out["period"] = f"{m.group(1)} – {m.group(2)}" if m else None
    return out


def _bank_summary(r) -> dict:
    import re
    f = r.extracted_facts or {}
    tx = [t for t in (f.get("transactions") or []) if isinstance(t, dict)]
    dates = sorted(str(t.get("date")) for t in tx if t.get("date"))
    debits = sum(abs(_num(t.get("amount")) or 0) for t in tx if str(t.get("type", "")).lower() == "debit")
    credits = sum(abs(_num(t.get("amount")) or 0) for t in tx if str(t.get("type", "")).lower() == "credit")
    # The statement's own summary is authoritative; extracted transactions are often partial (debits only)
    summ = _statement_summary(r.raw_text or "")
    period = summ.get("period") or f.get("statement_period") or f.get("period") or (f"{dates[0]} – {dates[-1]}" if dates else None)
    if summ.get("money_in") is not None:
        credits = summ["money_in"]
    if summ.get("money_out") is not None:
        debits = summ["money_out"]
    bank = next((f[k] for k in ("bank_name", "institution", "bank", "issuer") if f.get(k)), None)
    if not bank:
        from collections import Counter
        hits = re.findall(r"\b(Bank of America|Chase|Wells Fargo|Citibank|Capital One|U\.?S\.? Bank|PNC|Truist|"
                          r"Ally|Discover|American Express|Schwab|Fidelity|Navy Federal|Golden 1)\b", r.raw_text or "", re.I)
        # the issuing bank is named over and over; a merchant (a card payment) only once or twice
        canon = {"bank of america": "Bank of America", "us bank": "U.S. Bank", "u.s. bank": "U.S. Bank", "pnc": "PNC"}
        m = Counter(canon.get(h.lower(), h.title()) for h in hits).most_common(1)
        m = type("M", (), {"group": lambda self, i, v=m[0][0]: v})() if m else None
        bank = m.group(1) if m else None
    return {"id": r.id, "filename": r.filename, "bank": bank, "period": period, "transactions": len(tx),
            "debits": round(debits, 2), "credits": round(credits, 2),
            "opening_balance": summ.get("opening") if summ.get("opening") is not None else _num(f.get("opening_balance") or f.get("beginning_balance")),
            "closing_balance": summ.get("closing") if summ.get("closing") is not None else _num(f.get("closing_balance") or f.get("ending_balance")),
            "closing_calculated": bool(summ.get("closing_calculated")),
            "uploaded": r.created_at.isoformat() if r.created_at else None}


def _tax_summary(r) -> dict:
    from app.services.tax.irs_forms import extract_forms
    from app.services.tax.organizer import label
    forms = [f for f in extract_forms(r.raw_text or "") if f["form"] not in ("w4", "w7")]
    f = forms[0] if forms else None
    facts = r.extracted_facts or {}
    key = [{"label": k.replace("_", " "), "amount": v} for k, v in list((f or {}).get("boxes", {}).items())[:4]]
    if not key and r.doc_type == "property_tax_bill":
        for k in ("total_due", "amount_due", "total_tax", "first_installment", "second_installment"):
            if _num(facts.get(k)) is not None:
                key.append({"label": k.replace("_", " "), "amount": _num(facts.get(k))})
    return {"id": r.id, "filename": r.filename, "doc_type": r.doc_type,
            "form": label(f["form"]) if f else (r.doc_type or "document").replace("_", " ").title(),
            "year": (f or {}).get("year") or facts.get("tax_year") or _year_from_name(r.filename),
            "payer": (f or {}).get("payer"), "key_amounts": key,
            "uploaded": r.created_at.isoformat() if r.created_at else None}


@router.get("/finance/documents")
def get_finance_documents(db: Session = Depends(get_db)):
    """The tenant's bank statements and tax documents, with key figures, plus upcoming tax deadlines."""
    from datetime import date
    from app.services.tax.rules import upcoming_deadlines
    rows = db.execute(text(
        "SELECT id::text AS id, filename, doc_type, extracted_facts, raw_text, created_at FROM documents "
        "WHERE tenant_id = :t AND status = 'completed' ORDER BY created_at DESC"), {"t": _tenant()}).fetchall()
    bank = [_bank_summary(r) for r in rows if r.doc_type in ("bank_statement", "credit_card_statement")]
    tax = [_tax_summary(r) for r in rows if (r.doc_type or "").startswith("form_") or r.doc_type in _TAX_TYPES]
    today = date.today()
    deadlines = [{"name": d["name"], "authority": d["authority"], "due": d["due"].isoformat(),
                  "days_left": (d["due"] - today).days} for d in upcoming_deadlines(today)[:8]]
    return {"bank_statements": bank, "tax_documents": sorted(tax, key=lambda x: (-(x["year"] or 0), x["form"])),
            "deadlines": deadlines}


def _year_from_name(name: str):
    import re
    ys = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", name or "")]
    return max(ys) if ys else None


# ── Uploaded documents per domain page / tab ────────────────────────────────
# The domain tables above are filled by workflow services that aren't wired into
# processing yet; this shows what the tenant actually uploaded, by type.

DOMAIN_TABS = {
    "legal": {
        "clauses": ("Contracts", ["contract", "msa", "sow", "statement_of_work", "employment_contract"]),
        "obligations": ("NDAs", ["nda"]),
        "renewals": ("Leases", ["lease_agreement"]),
        "counterparties": ("Regulatory filings", ["legal_filing", "court_order", "affidavit", "subpoena", "sec_filing"]),
    },
    "healthcare": {
        "prescriptions": ("Medical charts", ["medical_record", "prescription"]),
        "diagnoses": ("Intake & medical forms", ["medical_record", "health_insurance_claim"]),
        "procedures": ("Lab reports", ["lab_report"]),
        "billing": ("Medical bills & EOBs", ["medical_bill", "eob_medical", "insurance_eob"]),
    },
    "real-estate": {
        "leases": ("Leases & rent", ["lease_agreement", "rent_statement", "hoa_statement"]),
        "inspections": ("Deeds & title", ["property_deed"]),
        "closing": ("Mortgage & closing", ["mortgage_document", "mortgage_statement", "closing_disclosure"]),
        "tax": ("Property tax", ["property_tax_bill"]),
    },
    "hr": {
        "talent": ("Resumes", ["resume"]),
        "onboarding": ("Offers & handbooks", ["offer_letter", "employment_contract", "policy_document"]),
        "policies": ("Reviews", ["performance_review"]),
        "payroll": ("Payroll", ["payroll", "pay_stub", "w2", "timesheet"]),
    },
    "supply-chain": {
        "logistics": ("Purchase orders", ["purchase_order", "work_order", "bom"]),
        "carriers": ("Bills of lading & freight", ["bill_of_lading", "shipping_label", "freight_invoice"]),
        "sanctions": ("Manifests & delivery", ["packing_slip", "delivery_note"]),
    },
    "finance": {
        "reconciliation": ("Bank & card statements", ["bank_statement", "credit_card_statement"]),
        "tax": ("Tax documents", ["tax_form", "tax_return", "w2", "property_tax_bill", "form_1040", "form_1099_int",
                                  "form_1099_div", "form_1099_nec", "form_1099_b", "form_1099_r", "form_1098", "schedule_k1"]),
        "expenses": ("Invoices & receipts", ["invoice", "receipt", "expense_report", "credit_memo", "purchase_order"]),
        "payments": ("Payments", ["payment_confirmation", "transaction_record"]),
        "reports": ("Financial reports", ["financial_statement", "sales_report", "forecast_report", "annual_report",
                                         "inventory_report", "spreadsheet"]),
    },
}

_SKIP_FACTS = {"doc_type", "transactions", "deductions", "earnings", "line_items", "items", "raw", "text", "tables"}


def _key_facts(facts, limit: int = 4) -> list:
    out = []
    for k, v in (facts or {}).items():
        if k in _SKIP_FACTS or v in (None, "", [], {}) or isinstance(v, (dict, list)):
            continue
        import html as _html
        out.append({"label": k.replace("_", " "), "value": v if isinstance(v, (int, float)) else _html.unescape(str(v))[:80]})
        if len(out) >= limit:
            break
    return out


@router.get("/{domain}/uploaded")
def get_domain_documents(domain: str, tab: str = "", db: Session = Depends(get_db)):
    """The tenant's uploaded documents for a domain page, narrowed to the tab's document types when it has some."""
    tabs = DOMAIN_TABS.get(domain)
    if tabs is None:
        return {"label": None, "types": [], "documents": []}
    all_types = sorted({t for _, ts in tabs.values() for t in ts})
    label, types = tabs.get(tab) or ("All documents", all_types)
    rows = db.execute(text(
        "SELECT id::text AS id, filename, doc_type, extracted_facts, created_at FROM documents "
        "WHERE tenant_id = :t AND status = 'completed' AND doc_type = ANY(:types) ORDER BY created_at DESC LIMIT 200"),
        {"t": _tenant(), "types": all_types}).fetchall()
    docs = [{"id": r.id, "filename": r.filename, "doc_type": r.doc_type,
             "uploaded": r.created_at.isoformat() if r.created_at else None,
             "facts": _key_facts(r.extracted_facts)} for r in rows]
    mine = [d for d in docs if d["doc_type"] in types]
    # A tab with nothing of its own still shows the page's other documents, so the page is never blank
    return {"label": label, "types": types, "documents": mine,
            "other": [d for d in docs if d["doc_type"] not in types]}
