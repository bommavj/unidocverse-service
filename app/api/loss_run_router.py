"""
loss_run_router.py
==================
Loss Run Intelligence API
"""
import logging, uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ams/lossrun", tags=["loss_run"])

RISK_THRESHOLDS = {
    "low":      {"loss_ratio": 40, "claim_count": 2},
    "medium":   {"loss_ratio": 60, "claim_count": 5},
    "high":     {"loss_ratio": 80, "claim_count": 8},
    "critical": {"loss_ratio": 100, "claim_count": 10},
}


@router.post("/upload/{client_id}")
async def upload_loss_run(
    client_id: str,
    policy_id: Optional[str] = None,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    import tempfile, os
    from pathlib import Path

    file_bytes = await file.read()
    suffix = Path(file.filename).suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        from app.parsers.hierarchical_doc_parser import HierarchicalDocParser
        parser = HierarchicalDocParser()
        tree = parser.parse(tmp_path, file.filename, "loss_run")

        # Extract fields
        carrier = tree.get_field("carrier_name") or ""
        period_start = tree.get_field("effective_date")
        period_end = tree.get_field("expiration_date")
        total_premium = _safe_float(tree.get_field("premium_amount"))

        # Extract claims from tables
        claims = []
        for table in tree.tables:
            if table.table_type == "loss_run":
                for row in table.rows:
                    claim = _normalize_claim_row(row)
                    if claim:
                        claims.append(claim)

        # Compute metrics
        total_incurred = sum(c.get("amount_incurred", 0) for c in claims)
        total_paid = sum(c.get("amount_paid", 0) for c in claims)
        total_reserved = sum(c.get("amount_reserved", 0) for c in claims)
        claim_count = len(claims)
        loss_ratio = (total_incurred / total_premium * 100) if total_premium > 0 else 0

        # Risk classification
        risk_level = "low"
        if loss_ratio >= 100 or claim_count >= 10:
            risk_level = "critical"
        elif loss_ratio >= 80 or claim_count >= 8:
            risk_level = "high"
        elif loss_ratio >= 60 or claim_count >= 5:
            risk_level = "medium"

        risk_flags = []
        if loss_ratio > 80:
            risk_flags.append(f"high_loss_ratio_{int(loss_ratio)}pct")
        if any(c.get("amount_incurred", 0) > 25000 for c in claims):
            risk_flags.append("large_loss_present")
        if claim_count > 5:
            risk_flags.append(f"high_frequency_{claim_count}_claims")

        # Save loss run
        run_id = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO loss_runs
                (id, client_id, policy_id, carrier_name, period_start, period_end,
                 total_claims, total_incurred, total_paid, total_reserved,
                 total_premium, loss_ratio, risk_level, risk_flags)
            VALUES
                (:id, :client_id, :policy_id, :carrier, :start, :end,
                 :claims, :incurred, :paid, :reserved,
                 :premium, :loss_ratio, :risk_level, :flags)
        """), {
            "id": run_id, "client_id": client_id, "policy_id": policy_id,
            "carrier": carrier, "start": period_start, "end": period_end,
            "claims": claim_count, "incurred": total_incurred,
            "paid": total_paid, "reserved": total_reserved,
            "premium": total_premium, "loss_ratio": loss_ratio,
            "risk_level": risk_level, "flags": risk_flags,
        })

        # Save individual claims
        for claim in claims:
            db.execute(text("""
                INSERT INTO loss_run_claims
                    (id, loss_run_id, claim_number, date_of_loss, date_reported,
                     claim_type, description, status, amount_paid, amount_reserved)
                VALUES
                    (:id, :run_id, :claim_no, :dol, :reported,
                     :type, :desc, :status, :paid, :reserved)
            """), {
                "id": str(uuid.uuid4()), "run_id": run_id,
                "claim_no": claim.get("claim_number"),
                "dol": claim.get("date_of_loss"),
                "reported": claim.get("date_reported"),
                "type": claim.get("claim_type"),
                "desc": claim.get("description"),
                "status": claim.get("status", "closed"),
                "paid": claim.get("amount_paid", 0),
                "reserved": claim.get("amount_reserved", 0),
            })

        # Update policy risk score
        if policy_id:
            db.execute(text("""
                UPDATE policies
                SET risk_score = :score, risk_flags = :flags
                WHERE id = :id
            """), {
                "score": min(1.0, loss_ratio / 100),
                "flags": risk_flags,
                "id": policy_id,
            })

        db.commit()

        return {
            "loss_run_id": run_id,
            "client_id": client_id,
            "carrier": carrier,
            "claim_count": claim_count,
            "total_incurred": total_incurred,
            "total_premium": total_premium,
            "loss_ratio": round(loss_ratio, 2),
            "risk_level": risk_level,
            "risk_flags": risk_flags,
        }
    finally:
        os.unlink(tmp_path)


@router.get("/client/{client_id}")
def get_client_loss_runs(client_id: str, db: Session = Depends(get_db)):
    runs = db.execute(text("""
        SELECT lr.*, p.policy_number, p.line_of_business
        FROM loss_runs lr
        LEFT JOIN policies p ON p.id = lr.policy_id
        WHERE lr.client_id = :id
        ORDER BY lr.created_at DESC
    """), {"id": client_id}).fetchall()
    return {"loss_runs": [dict(r._mapping) for r in runs]}


@router.get("/{loss_run_id}/claims")
def get_claims(loss_run_id: str, db: Session = Depends(get_db)):
    claims = db.execute(text("""
        SELECT * FROM loss_run_claims WHERE loss_run_id = :id ORDER BY date_of_loss DESC
    """), {"id": loss_run_id}).fetchall()
    return {"claims": [dict(r._mapping) for r in claims]}


@router.get("/high-risk")
def get_high_risk_clients(threshold: str = "high", db: Session = Depends(get_db)):
    levels = {"critical": ["critical"], "high": ["critical", "high"],
              "medium": ["critical", "high", "medium"]}
    risk_levels = levels.get(threshold, ["critical", "high"])

    rows = db.execute(text("""
        SELECT lr.*, c.first_name, c.last_name, c.business_name, c.client_type, c.client_number
        FROM loss_runs lr
        JOIN clients c ON c.id = lr.client_id
        WHERE lr.risk_level = ANY(:levels)
        ORDER BY lr.loss_ratio DESC NULLS LAST
        LIMIT 50
    """), {"levels": risk_levels}).fetchall()

    return {"high_risk_clients": [dict(r._mapping) for r in rows]}


def _normalize_claim_row(row: dict) -> Optional[dict]:
    claim = {}
    for k, v in row.items():
        k_lower = k.lower()
        if "claim" in k_lower and "number" in k_lower:
            claim["claim_number"] = str(v)
        elif "date" in k_lower and "loss" in k_lower:
            claim["date_of_loss"] = str(v)
        elif "date" in k_lower and "report" in k_lower:
            claim["date_reported"] = str(v)
        elif "type" in k_lower:
            claim["claim_type"] = str(v)
        elif "description" in k_lower or "cause" in k_lower:
            claim["description"] = str(v)
        elif "status" in k_lower:
            claim["status"] = str(v)
        elif "paid" in k_lower:
            claim["amount_paid"] = _safe_float(str(v))
        elif "reserve" in k_lower:
            claim["amount_reserved"] = _safe_float(str(v))
        elif "incurred" in k_lower:
            claim["amount_incurred"] = _safe_float(str(v))
    claim["amount_incurred"] = claim.get("amount_paid", 0) + claim.get("amount_reserved", 0)
    return claim if claim.get("claim_number") or claim.get("amount_paid") else None

def _safe_float(v) -> float:
    import re
    if not v:
        return 0.0
    try:
        return float(re.sub(r'[$,\s]', '', str(v)))
    except (ValueError, TypeError):
        return 0.0