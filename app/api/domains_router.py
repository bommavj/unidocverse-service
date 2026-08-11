from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db

router = APIRouter(prefix="/api/domains", tags=["multidomain"])

# ==============================================================================
# LEGAL & COMPLIANCE ENDPOINTS
# ==============================================================================

@router.get("/legal/clauses")
def get_legal_clauses(db: Session = Depends(get_db)):
    # Query the real legal_clauses table
    rows = db.execute(text("SELECT clause_type, clause_name, clause_text, is_deviation, governing_law FROM legal_clauses ORDER BY created_at DESC")).fetchall()
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
    rows = db.execute(text("SELECT contract_name, counterparty, notice_period, expiry_date, risk_rating, status FROM legal_contracts ORDER BY created_at DESC")).fetchall()
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
    rows = db.execute(text("SELECT biomarker_name, biomarker_value FROM patient_biomarkers ORDER BY created_at ASC")).fetchall()
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
    rows = db.execute(text("SELECT biomarker_name, biomarker_value, unit, reference_range, physician FROM patient_biomarkers WHERE is_abnormal = TRUE ORDER BY created_at DESC")).fetchall()
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
    rows = db.execute(text("SELECT invoice_id, vendor_name, invoice_val, po_val, receipt_val, match_status, is_approved FROM finance_matches ORDER BY created_at DESC")).fetchall()
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
    rows = db.execute(text("SELECT address, buyer_name, price, progress, has_appraisal, has_clear_title, is_escrow_funded FROM real_estate_properties ORDER BY created_at DESC")).fetchall()
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
    rows = db.execute(text("SELECT candidate_name, role, experience_years, match_score, skills, feedback FROM hr_candidates ORDER BY created_at DESC")).fetchall()
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
    rows = db.execute(text("SELECT bol_number, carrier_name, origin, destination, customs_status, is_reconciled FROM supply_chain_shipments ORDER BY created_at DESC")).fetchall()
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
    rows = db.execute(text("SELECT id, patient_name, patient_dob, chart_score, consent_signed, insurance_verified, history_filed FROM patient_charts ORDER BY created_at DESC")).fetchall()
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
    rows = db.execute(text("SELECT vendor_name, SUM(invoice_val) as total_spend, COUNT(CASE WHEN match_status = 'failed' THEN 1 END) as failed_matches FROM finance_matches GROUP BY vendor_name")).fetchall()
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
