"""
commission_router.py
====================
Commission Reconciliation API

Endpoints:
  POST /api/ams/commission/upload          — Upload carrier statement
  GET  /api/ams/commission/statements      — List statements
  GET  /api/ams/commission/statements/{id} — Get statement + line items
  POST /api/ams/commission/reconcile/{id}  — Run reconciliation
  GET  /api/ams/commission/discrepancies   — All unmatched/discrepancy lines
  PUT  /api/ams/commission/lines/{id}      — Manual override a line
"""

import logging
import uuid
from datetime import date
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ams/commission", tags=["commission"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class StatementCreate(BaseModel):
    carrier_name: str
    statement_date: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None

class LineOverride(BaseModel):
    policy_id: Optional[str] = None
    expected_commission: Optional[float] = None
    match_status: Optional[str] = None
    notes: Optional[str] = None


# ── Upload + Parse ────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_commission_statement(
    carrier_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload carrier commission statement PDF/CSV and parse it."""
    import tempfile, os
    from pathlib import Path

    file_bytes = await file.read()
    suffix = Path(file.filename).suffix.lower()

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        # Create statement record
        stmt_id = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO commission_statements
                (id, carrier_name, status)
            VALUES (:id, :carrier, 'processing')
        """), {"id": stmt_id, "carrier": carrier_name})
        db.commit()

        # Parse document
        line_items = []
        if suffix in (".pdf",):
            line_items = _parse_commission_pdf(tmp_path, file.filename)
        elif suffix in (".csv", ".xlsx", ".xls"):
            line_items = _parse_commission_spreadsheet(tmp_path, suffix)

        # Insert line items
        total = 0.0
        for item in line_items:
            item_id = str(uuid.uuid4())
            comm_amt = item.get("commission_amount") or 0.0
            total += comm_amt

            db.execute(text("""
                INSERT INTO commission_line_items
                    (id, statement_id, policy_number, insured_name,
                     line_of_business, premium_amount, commission_rate,
                     commission_amount, match_status)
                VALUES
                    (:id, :stmt_id, :pol_num, :insured,
                     :lob, :premium, :rate, :commission, 'unmatched')
            """), {
                "id": item_id,
                "stmt_id": stmt_id,
                "pol_num": item.get("policy_number"),
                "insured": item.get("insured_name"),
                "lob": item.get("line_of_business"),
                "premium": item.get("premium_amount"),
                "rate": item.get("commission_rate"),
                "commission": comm_amt,
            })

        # Update statement totals
        db.execute(text("""
            UPDATE commission_statements
            SET status = 'pending', total_amount = :total
            WHERE id = :id
        """), {"total": total, "id": stmt_id})
        db.commit()

        return {
            "statement_id": stmt_id,
            "carrier_name": carrier_name,
            "lines_extracted": len(line_items),
            "total_amount": total,
            "status": "pending",
            "message": f"Extracted {len(line_items)} commission lines. Run reconciliation to match policies."
        }

    finally:
        os.unlink(tmp_path)


# ── List Statements ───────────────────────────────────────────────────────────

@router.get("/statements")
def list_statements(
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    rows = db.execute(text("""
        SELECT s.*,
               COUNT(l.id) as line_count,
               COUNT(CASE WHEN l.match_status = 'discrepancy' THEN 1 END) as discrepancy_count,
               COUNT(CASE WHEN l.match_status = 'unmatched' THEN 1 END) as unmatched_count
        FROM commission_statements s
        LEFT JOIN commission_line_items l ON l.statement_id = s.id
        GROUP BY s.id
        ORDER BY s.created_at DESC
        LIMIT :limit OFFSET :skip
    """), {"limit": limit, "skip": skip}).fetchall()

    return {"statements": [dict(r._mapping) for r in rows]}


# ── Get Statement Detail ──────────────────────────────────────────────────────

@router.get("/statements/{statement_id}")
def get_statement(statement_id: str, db: Session = Depends(get_db)):
    stmt = db.execute(text("""
        SELECT * FROM commission_statements WHERE id = :id
    """), {"id": statement_id}).fetchone()
    if not stmt:
        raise HTTPException(status_code=404, detail="Statement not found")

    lines = db.execute(text("""
        SELECT l.*,
               p.line_of_business as policy_lob,
               c.first_name || ' ' || c.last_name as client_name
        FROM commission_line_items l
        LEFT JOIN policies p ON p.id = l.policy_id
        LEFT JOIN clients c ON c.id = p.client_id
        WHERE l.statement_id = :id
        ORDER BY l.match_status, l.insured_name
    """), {"id": statement_id}).fetchall()

    return {
        "statement": dict(stmt._mapping),
        "lines": [dict(r._mapping) for r in lines],
        "summary": {
            "total_lines": len(lines),
            "matched": sum(1 for r in lines if r.match_status == "matched"),
            "unmatched": sum(1 for r in lines if r.match_status == "unmatched"),
            "discrepancy": sum(1 for r in lines if r.match_status == "discrepancy"),
        }
    }


# ── Reconcile ─────────────────────────────────────────────────────────────────

@router.post("/reconcile/{statement_id}")
def reconcile_statement(statement_id: str, db: Session = Depends(get_db)):
    """
    Match commission line items to policies.
    Algorithm:
    1. Exact policy_number match
    2. Fuzzy insured_name + carrier match
    3. Compare commission_amount vs expected (rate * premium)
    """
    lines = db.execute(text("""
        SELECT * FROM commission_line_items
        WHERE statement_id = :id
    """), {"id": statement_id}).fetchall()

    if not lines:
        raise HTTPException(status_code=404, detail="No lines found for this statement")

    matched = unmatched = discrepancy = 0

    for line in lines:
        match_result = _match_line_to_policy(line, db)

        if match_result["policy_id"]:
            # Compute expected commission
            policy = db.execute(text("""
                SELECT premium_amount FROM policies WHERE id = :id
            """), {"id": match_result["policy_id"]}).fetchone()

            expected = 0.0
            if policy and policy.premium_amount and line.commission_rate:
                expected = float(policy.premium_amount) * float(line.commission_rate) / 100

            actual = float(line.commission_amount or 0)
            variance_pct = abs(actual - expected) / expected * 100 if expected > 0 else 0

            status = "matched" if variance_pct < 5 else "discrepancy"
            if status == "matched":
                matched += 1
            else:
                discrepancy += 1

            db.execute(text("""
                UPDATE commission_line_items
                SET policy_id = :pol_id,
                    expected_commission = :expected,
                    match_status = :status,
                    match_confidence = :conf
                WHERE id = :id
            """), {
                "pol_id": match_result["policy_id"],
                "expected": expected,
                "status": status,
                "conf": match_result["confidence"],
                "id": str(line.id),
            })
        else:
            unmatched += 1
            db.execute(text("""
                UPDATE commission_line_items
                SET match_status = 'unmatched'
                WHERE id = :id
            """), {"id": str(line.id)})

    # Save reconciliation run
    run_id = str(uuid.uuid4())
    total_expected = db.execute(text("""
        SELECT COALESCE(SUM(expected_commission), 0)
        FROM commission_line_items WHERE statement_id = :id
    """), {"id": statement_id}).scalar()

    total_actual = db.execute(text("""
        SELECT COALESCE(SUM(commission_amount), 0)
        FROM commission_line_items WHERE statement_id = :id
    """), {"id": statement_id}).scalar()

    db.execute(text("""
        INSERT INTO commission_reconciliation_runs
            (id, statement_id, total_lines, matched_lines,
             unmatched_lines, discrepancy_lines, total_expected, total_actual)
        VALUES (:id, :stmt_id, :total, :matched, :unmatched, :disc, :expected, :actual)
    """), {
        "id": run_id,
        "stmt_id": statement_id,
        "total": len(lines),
        "matched": matched,
        "unmatched": unmatched,
        "disc": discrepancy,
        "expected": total_expected,
        "actual": total_actual,
    })

    new_status = "reconciled" if discrepancy == 0 and unmatched == 0 else "discrepancy"
    db.execute(text("""
        UPDATE commission_statements SET status = :status WHERE id = :id
    """), {"status": new_status, "id": statement_id})

    db.commit()

    return {
        "run_id": run_id,
        "total_lines": len(lines),
        "matched": matched,
        "unmatched": unmatched,
        "discrepancy": discrepancy,
        "total_expected": float(total_expected),
        "total_actual": float(total_actual),
        "total_variance": float(total_actual) - float(total_expected),
        "status": new_status,
    }


# ── Discrepancies ─────────────────────────────────────────────────────────────

@router.get("/discrepancies")
def get_discrepancies(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT l.*, s.carrier_name, s.statement_date
        FROM commission_line_items l
        JOIN commission_statements s ON s.id = l.statement_id
        WHERE l.match_status IN ('unmatched', 'discrepancy')
        ORDER BY ABS(COALESCE(l.variance, l.commission_amount)) DESC
        LIMIT 100
    """)).fetchall()
    return {
        "discrepancies": [dict(r._mapping) for r in rows],
        "total": len(rows)
    }


# ── Manual Override ────────────────────────────────────────────────────────────

@router.put("/lines/{line_id}")
def override_line(line_id: str, override: LineOverride, db: Session = Depends(get_db)):
    updates = []
    params = {"id": line_id}
    if override.policy_id:
        updates.append("policy_id = :pol_id")
        params["pol_id"] = override.policy_id
    if override.expected_commission is not None:
        updates.append("expected_commission = :expected")
        params["expected"] = override.expected_commission
    if override.match_status:
        updates.append("match_status = :status")
        params["status"] = override.match_status
    if override.notes:
        updates.append("notes = :notes")
        params["notes"] = override.notes

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    db.execute(text(f"""
        UPDATE commission_line_items
        SET {', '.join(updates)}
        WHERE id = :id
    """), params)
    db.commit()
    return {"message": "Line updated successfully"}


# ── Internal: Parse ───────────────────────────────────────────────────────────

def _parse_commission_pdf(file_path: str, filename: str) -> List[dict]:
    """Use HierarchicalDocParser to extract commission table from PDF."""
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from app.parsers.hierarchical_doc_parser import HierarchicalDocParser

        parser = HierarchicalDocParser()
        tree = parser.parse(file_path, filename, "commission_statement")

        items = []
        for table in tree.tables:
            if table.table_type in ("commission", "generic", "financial"):
                for row in table.rows:
                    item = _normalize_commission_row(row)
                    if item:
                        items.append(item)
        return items
    except Exception as e:
        logger.error(f"PDF commission parse failed: {e}")
        return []


def _parse_commission_spreadsheet(file_path: str, suffix: str) -> List[dict]:
    """Parse CSV/Excel commission statements."""
    try:
        if suffix == ".csv":
            import csv
            items = []
            with open(file_path, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    item = _normalize_commission_row(dict(row))
                    if item:
                        items.append(item)
            return items
        else:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True)
            ws = wb.active
            headers = [str(c.value or "").strip() for c in next(ws.iter_rows(max_row=1))]
            items = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                row_dict = {headers[i]: (str(v).strip() if v is not None else "") for i, v in enumerate(row) if i < len(headers)}
                item = _normalize_commission_row(row_dict)
                if item:
                    items.append(item)
            return items
    except Exception as e:
        logger.error(f"Spreadsheet commission parse failed: {e}")
        return []


def _normalize_commission_row(row: dict) -> Optional[dict]:
    """Normalize a raw row dict to commission line item fields."""
    if not row:
        return None

    item = {}
    for k, v in row.items():
        k_lower = k.lower().strip()
        v_str = str(v).strip() if v else ""

        if not v_str or v_str.lower() in ("none", "null", "n/a", ""):
            continue

        if any(x in k_lower for x in ["policy", "pol #", "policy number", "pol no"]):
            item["policy_number"] = v_str
        elif any(x in k_lower for x in ["insured", "name", "client"]):
            item["insured_name"] = v_str
        elif any(x in k_lower for x in ["line", "lob", "business"]):
            item["line_of_business"] = v_str
        elif any(x in k_lower for x in ["written premium", "premium"]):
            item["premium_amount"] = _parse_amount(v_str)
        elif any(x in k_lower for x in ["rate", "comm rate", "commission %"]):
            item["commission_rate"] = _parse_amount(v_str)
        elif any(x in k_lower for x in ["commission", "earned", "comm amount"]):
            item["commission_amount"] = _parse_amount(v_str)

    # Must have at least policy number or commission amount
    if item.get("policy_number") or item.get("commission_amount"):
        return item
    return None


def _match_line_to_policy(line, db) -> dict:
    """Match a commission line to a policy in the DB."""
    # 1. Exact policy number match
    if line.policy_number:
        pol = db.execute(text("""
            SELECT id FROM policies WHERE policy_number = :pn
        """), {"pn": line.policy_number}).fetchone()
        if pol:
            return {"policy_id": str(pol.id), "confidence": 1.0}

    # 2. Fuzzy insured name match
    if line.insured_name:
        pol = db.execute(text("""
            SELECT p.id FROM policies p
            JOIN clients c ON c.id = p.client_id
            WHERE LOWER(c.last_name) = LOWER(:name)
               OR LOWER(c.business_name) = LOWER(:name)
            LIMIT 1
        """), {"name": line.insured_name}).fetchone()
        if pol:
            return {"policy_id": str(pol.id), "confidence": 0.75}

    return {"policy_id": None, "confidence": 0.0}


def _parse_amount(value: str) -> Optional[float]:
    import re
    if not value:
        return None
    try:
        cleaned = re.sub(r'[$,\s%]', '', str(value))
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None