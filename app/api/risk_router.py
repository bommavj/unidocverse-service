"""Risk Scoring + Document Health — AI-powered per-document intelligence."""
import json
import re
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db

router = APIRouter(prefix="/api/risk", tags=["risk"])

# ── Risk factor definitions by doc type ─────────────────────────────────────
HEALTH_FIELDS: dict[str, list[dict]] = {
    "insurance": [
        {"key": "policy_number",  "label": "Policy Number",   "weight": 15},
        {"key": "expiry_date",    "label": "Expiry Date",      "weight": 20},
        {"key": "insured_name",   "label": "Insured Name",     "weight": 15},
        {"key": "premium",        "label": "Premium Amount",   "weight": 10},
        {"key": "coverage_amount","label": "Coverage Amount",  "weight": 15},
        {"key": "effective_date", "label": "Effective Date",   "weight": 10},
        {"key": "insurer",        "label": "Insurance Company","weight": 15},
    ],
    "contract": [
        {"key": "parties",        "label": "Party Names",      "weight": 20},
        {"key": "effective_date", "label": "Effective Date",   "weight": 15},
        {"key": "expiry_date",    "label": "Expiration Date",  "weight": 15},
        {"key": "governing_law",  "label": "Governing Law",    "weight": 10},
        {"key": "value",          "label": "Contract Value",   "weight": 20},
        {"key": "signatures",     "label": "Signatures",       "weight": 20},
    ],
    "invoice": [
        {"key": "invoice_number", "label": "Invoice Number",   "weight": 15},
        {"key": "vendor",         "label": "Vendor Name",      "weight": 15},
        {"key": "amount",         "label": "Total Amount",     "weight": 20},
        {"key": "due_date",       "label": "Due Date",         "weight": 20},
        {"key": "payment_terms",  "label": "Payment Terms",    "weight": 15},
        {"key": "line_items",     "label": "Line Items",       "weight": 15},
    ],
    "medical": [
        {"key": "patient_name",   "label": "Patient Name",     "weight": 20},
        {"key": "date_of_birth",  "label": "Date of Birth",    "weight": 15},
        {"key": "diagnosis",      "label": "Diagnosis",        "weight": 20},
        {"key": "provider",       "label": "Provider Name",    "weight": 15},
        {"key": "date_of_service","label": "Date of Service",  "weight": 15},
        {"key": "icd_code",       "label": "ICD Code",         "weight": 15},
    ],
    "default": [
        {"key": "title",          "label": "Title",            "weight": 20},
        {"key": "date",           "label": "Date",             "weight": 20},
        {"key": "author",         "label": "Author/Issuer",    "weight": 20},
        {"key": "summary",        "label": "Summary/Content",  "weight": 40},
    ],
}

RISK_PATTERNS = [
    {"label": "Missing expiry date",    "pattern": r"expir",          "score_add": 20, "severity": "high"},
    {"label": "No signature block",     "pattern": r"sign(ature|ed)", "score_add": 15, "severity": "high"},
    {"label": "Broad indemnification",  "pattern": r"indemnif",       "score_add": 10, "severity": "medium"},
    {"label": "Unlimited liability",    "pattern": r"unlimited.{0,20}liabilit", "score_add": 20, "severity": "high"},
    {"label": "Auto-renewal clause",    "pattern": r"auto.{0,10}renew", "score_add": 8, "severity": "medium"},
    {"label": "Termination for convenience", "pattern": r"terminat.{0,30}convenience", "score_add": 5, "severity": "low"},
    {"label": "Jurisdiction issues",    "pattern": r"arbitrat",       "score_add": 5, "severity": "low"},
    {"label": "Confidentiality clause", "pattern": r"confidential",   "score_add": 3, "severity": "info"},
    {"label": "Personal data / PII",    "pattern": r"(ssn|social security|date of birth|passport)", "score_add": 15, "severity": "high"},
    {"label": "HIPAA-sensitive content","pattern": r"(diagnosis|patient|medical record|phi)", "score_add": 12, "severity": "high"},
    {"label": "Financial obligation",   "pattern": r"\$[\d,]+",       "score_add": 5, "severity": "low"},
    {"label": "Late payment penalty",   "pattern": r"late.{0,20}(fee|penalt|interest)", "score_add": 8, "severity": "medium"},
]

def ensure_tables(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS doc_risk (
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL UNIQUE,
            risk_score INTEGER DEFAULT 0,
            health_score INTEGER DEFAULT 0,
            risk_factors JSONB DEFAULT '[]',
            health_fields JSONB DEFAULT '[]',
            missing_fields JSONB DEFAULT '[]',
            computed_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()

def row_to_dict(row):
    return dict(row._mapping) if row else None

def compute_risk_health(doc_id: str, raw_text: str, doc_type: str, metadata: dict, db: Session):
    """Run risk + health scoring and upsert into doc_risk."""
    ensure_tables(db)
    text_lower = (raw_text or "").lower()
    doc_type_norm = (doc_type or "").lower()

    # ── Risk score ────────────────────────────────────────────────────────────
    risk_factors = []
    risk_score = 0
    for pat in RISK_PATTERNS:
        if re.search(pat["pattern"], text_lower, re.IGNORECASE):
            # for negative signals (missing things) check absence
            if pat["label"].startswith("Missing") or pat["label"].startswith("No "):
                if not re.search(pat["pattern"], text_lower):
                    risk_factors.append({"label": pat["label"], "severity": pat["severity"]})
                    risk_score += pat["score_add"]
            else:
                risk_factors.append({"label": pat["label"], "severity": pat["severity"]})
                risk_score += pat["score_add"]
    risk_score = min(100, risk_score)

    # ── Health / completeness score ───────────────────────────────────────────
    fields_def = None
    for key in [doc_type_norm, doc_type_norm.split()[0] if doc_type_norm else ""]:
        if key in HEALTH_FIELDS:
            fields_def = HEALTH_FIELDS[key]
            break
    if not fields_def:
        fields_def = HEALTH_FIELDS["default"]

    meta_flat = {}
    if isinstance(metadata, dict):
        meta_flat.update(metadata)
        if isinstance(metadata.get("analyzed_data"), dict):
            meta_flat.update(metadata["analyzed_data"])
            if isinstance(metadata["analyzed_data"].get("fields"), dict):
                meta_flat.update(metadata["analyzed_data"]["fields"])

    health_fields_result = []
    missing_fields = []
    total_weight = sum(f["weight"] for f in fields_def)
    earned_weight = 0

    for f in fields_def:
        present = bool(meta_flat.get(f["key"]) or re.search(f["key"].replace("_", r"\W*"), text_lower))
        health_fields_result.append({**f, "present": present})
        if present:
            earned_weight += f["weight"]
        else:
            missing_fields.append(f["label"])

    health_score = int(earned_weight / total_weight * 100) if total_weight else 0

    rid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO doc_risk (id, doc_id, risk_score, health_score, risk_factors, health_fields, missing_fields)
        VALUES (:id, :doc_id, :rs, :hs, CAST(:rf AS jsonb), CAST(:hf AS jsonb), CAST(:mf AS jsonb))
        ON CONFLICT (doc_id) DO UPDATE SET
          risk_score=:rs, health_score=:hs,
          risk_factors=CAST(:rf AS jsonb), health_fields=CAST(:hf AS jsonb),
          missing_fields=CAST(:mf AS jsonb), computed_at=NOW()
    """), {
        "id": rid, "doc_id": doc_id, "rs": risk_score, "hs": health_score,
        "rf": json.dumps(risk_factors), "hf": json.dumps(health_fields_result),
        "mf": json.dumps(missing_fields),
    })
    db.commit()
    return {"risk_score": risk_score, "health_score": health_score,
            "risk_factors": risk_factors, "health_fields": health_fields_result,
            "missing_fields": missing_fields}

# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/{doc_id}")
def get_risk(doc_id: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    row = db.execute(text("SELECT * FROM doc_risk WHERE doc_id=:id"), {"id": doc_id}).fetchone()
    if row:
        return row_to_dict(row)
    # compute on demand
    doc = db.execute(text("SELECT * FROM documents WHERE id=:id"), {"id": doc_id}).fetchone()
    if not doc:
        return {"risk_score": 0, "health_score": 0, "risk_factors": [], "health_fields": [], "missing_fields": []}
    d = row_to_dict(doc)
    return compute_risk_health(doc_id, d.get("raw_text",""), d.get("doc_type",""), d.get("doc_metadata") or {}, db)

@router.post("/{doc_id}/compute")
def compute_risk(doc_id: str, db: Session = Depends(get_db)):
    doc = db.execute(text("SELECT * FROM documents WHERE id=:id"), {"id": doc_id}).fetchone()
    if not doc:
        return {"error": "not found"}
    d = row_to_dict(doc)
    result = compute_risk_health(doc_id, d.get("raw_text",""), d.get("doc_type",""), d.get("doc_metadata") or {}, db)
    return result

@router.post("/batch")
def batch_compute(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Queue risk scoring for all unscored documents."""
    ensure_tables(db)
    docs = db.execute(text("""
        SELECT d.id, d.raw_text, d.doc_type, d.doc_metadata
        FROM documents d
        LEFT JOIN doc_risk r ON r.doc_id = d.id
        WHERE r.doc_id IS NULL AND d.status != 'deleted'
        LIMIT 50
    """)).fetchall()

    def run_batch():
        db2 = db
        for doc in docs:
            d = dict(doc._mapping)
            try:
                compute_risk_health(d["id"], d.get("raw_text",""), d.get("doc_type",""), d.get("doc_metadata") or {}, db2)
            except Exception as e:
                print(f"Risk batch error {d['id']}: {e}")

    background_tasks.add_task(run_batch)
    return {"queued": len(docs)}

@router.get("")
def list_risk_scores(limit: int = 100, db: Session = Depends(get_db)):
    ensure_tables(db)
    rows = db.execute(text("""
        SELECT r.*, d.filename, d.doc_type
        FROM doc_risk r
        LEFT JOIN documents d ON d.id = r.doc_id
        ORDER BY r.risk_score DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()
    return [row_to_dict(r) for r in rows]
