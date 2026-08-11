"""
Quote Pipeline — Lead → Quote → Bind → Policy.
Multi-carrier comparison with jurisdiction-aware coverage validation.
"""
import uuid, json
from typing import Optional
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID

router = APIRouter(prefix="/api/quotes", tags=["quotes"])

STAGES = ["draft", "sent", "comparing", "bound", "lost", "expired"]

LOSS_REASONS = [
    "price_too_high", "went_with_competitor", "client_unresponsive",
    "coverage_not_available", "underwriting_declined", "client_already_covered",
    "other"
]

class QuoteCreate(BaseModel):
    client_id: Optional[str] = None
    client_name: Optional[str] = None   # for walk-ins without a client record
    lob: str = "auto"
    state: str
    country: str = "US"
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    risk_data: Optional[dict] = None     # vehicle/property/etc details
    requested_limits: Optional[dict] = None
    notes: Optional[str] = None
    assigned_agent: Optional[str] = None

class QuoteUpdate(BaseModel):
    stage: Optional[str] = None
    notes: Optional[str] = None
    assigned_agent: Optional[str] = None
    lob: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    requested_limits: Optional[dict] = None
    risk_data: Optional[dict] = None

class CarrierQuote(BaseModel):
    carrier_name: str
    carrier_code: Optional[str] = None
    annual_premium: Optional[float] = None
    monthly_premium: Optional[float] = None
    coverage_details: Optional[dict] = None   # actual limits offered
    deductibles: Optional[dict] = None
    notes: Optional[str] = None

class CarrierUpdate(BaseModel):
    annual_premium: Optional[float] = None
    monthly_premium: Optional[float] = None
    coverage_details: Optional[dict] = None
    deductibles: Optional[dict] = None
    status: Optional[str] = None   # pending|received|selected|declined
    notes: Optional[str] = None

class BindRequest(BaseModel):
    carrier_quote_id: str
    policy_number: Optional[str] = None
    notes: Optional[str] = None

class LossRequest(BaseModel):
    reason: str
    notes: Optional[str] = None

def ensure_tables(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS quotes (
            id TEXT PRIMARY KEY,
            quote_number TEXT UNIQUE,
            client_id TEXT,
            client_name TEXT,
            lob TEXT NOT NULL,
            state TEXT NOT NULL,
            country TEXT DEFAULT 'US',
            stage TEXT DEFAULT 'draft',
            effective_date TEXT,
            expiry_date TEXT,
            risk_data JSONB DEFAULT '{}',
            requested_limits JSONB DEFAULT '{}',
            jurisdiction_minimums JSONB DEFAULT '{}',
            jurisdiction_required JSONB DEFAULT '[]',
            notes TEXT,
            assigned_agent TEXT,
            loss_reason TEXT,
            loss_notes TEXT,
            bound_policy_id TEXT,
            bound_carrier TEXT,
            bound_premium NUMERIC,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS quote_carriers (
            id TEXT PRIMARY KEY,
            quote_id TEXT NOT NULL,
            carrier_name TEXT NOT NULL,
            carrier_code TEXT,
            annual_premium NUMERIC,
            monthly_premium NUMERIC,
            coverage_details JSONB DEFAULT '{}',
            deductibles JSONB DEFAULT '{}',
            status TEXT DEFAULT 'pending',
            notes TEXT,
            added_at TIMESTAMP DEFAULT NOW(),
            received_at TIMESTAMP,
            UNIQUE(quote_id, carrier_name)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS quote_activity (
            id TEXT PRIMARY KEY,
            quote_id TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            actor TEXT DEFAULT 'agent',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()

def r2d(row): return dict(row._mapping) if row else None

def log_activity(quote_id: str, action: str, detail: str, db: Session, actor: str = "agent"):
    db.execute(text("""
        INSERT INTO quote_activity (id, quote_id, action, detail, actor)
        VALUES (:id, :qid, :action, :detail, :actor)
    """), {"id": str(uuid.uuid4()), "qid": quote_id,
           "action": action, "detail": detail, "actor": actor})

def _enrich(q: dict, db: Session) -> dict:
    carriers = db.execute(text(
        "SELECT * FROM quote_carriers WHERE quote_id=:id ORDER BY annual_premium ASC NULLS LAST"
    ), {"id": q["id"]}).fetchall()
    q["carriers"] = [r2d(c) for c in carriers]
    q["carrier_count"] = len(q["carriers"])
    received = [c for c in q["carriers"] if r2d(c)["annual_premium"]]
    q["best_premium"] = min((r2d(c)["annual_premium"] for c in received), default=None)
    return q

def _load_jurisdiction(state: str, country: str, lob: str, db: Session) -> dict:
    """Pull min limits + required coverages from jurisdiction engine."""
    j = db.execute(text("""
        SELECT jp.min_limits, jp.required_coverages
        FROM jurisdictions j
        JOIN jurisdiction_products jp ON jp.jurisdiction_id = j.id
        WHERE j.country_code=:cc AND j.state_code=:sc AND jp.lob=:lob
        LIMIT 1
    """), {"cc": country, "sc": state, "lob": lob}).fetchone()
    if j:
        d = r2d(j)
        return {"minimums": d["min_limits"] or {}, "required": d["required_coverages"] or []}
    return {"minimums": {}, "required": []}

# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/stages")
def get_stages(): return {"stages": STAGES, "loss_reasons": LOSS_REASONS}

@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    ensure_tables(db)
    by_stage = db.execute(text("""
        SELECT stage, COUNT(*) as cnt,
               AVG(bound_premium) FILTER (WHERE stage='bound') as avg_bound_premium
        FROM quotes GROUP BY stage
    """)).fetchall()
    by_lob = db.execute(text("SELECT lob, COUNT(*) as cnt FROM quotes GROUP BY lob ORDER BY cnt DESC")).fetchall()
    win_rate = db.execute(text("""
        SELECT
          COUNT(*) FILTER (WHERE stage='bound') as won,
          COUNT(*) FILTER (WHERE stage='lost')  as lost,
          COUNT(*) as total
        FROM quotes WHERE stage IN ('bound','lost')
    """)).fetchone()
    return {
        "by_stage": [r2d(r) for r in by_stage],
        "by_lob":   [r2d(r) for r in by_lob],
        "win_rate": r2d(win_rate),
    }

@router.get("")
def list_quotes(stage: Optional[str] = None, lob: Optional[str] = None,
                state: Optional[str] = None, limit: int = 100,
                db: Session = Depends(get_db)):
    ensure_tables(db)
    q = "SELECT * FROM quotes WHERE 1=1"
    p: dict = {}
    if stage: q += " AND stage=:stage"; p["stage"] = stage
    if lob:   q += " AND lob=:lob";     p["lob"]   = lob
    if state: q += " AND state=:state"; p["state"] = state
    q += " ORDER BY updated_at DESC LIMIT :limit"; p["limit"] = limit
    rows = db.execute(text(q), p).fetchall()
    quotes = [r2d(r) for r in rows]
    for qt in quotes:
        cc = db.execute(text("SELECT COUNT(*) FROM quote_carriers WHERE quote_id=:id"),
                        {"id": qt["id"]}).scalar()
        qt["carrier_count"] = cc
        best = db.execute(text("""
            SELECT MIN(annual_premium) FROM quote_carriers
            WHERE quote_id=:id AND annual_premium IS NOT NULL
        """), {"id": qt["id"]}).scalar()
        qt["best_premium"] = best
    return quotes

@router.post("")
def create_quote(body: QuoteCreate, db: Session = Depends(get_db)):
    ensure_tables(db)
    qid  = str(uuid.uuid4())
    qnum = f"QT-{qid[:8].upper()}"
    exp  = body.expiry_date or str(date.today() + timedelta(days=30))

    # Load jurisdiction minimums automatically
    jur = _load_jurisdiction(body.state, body.country, body.lob, db)

    db.execute(text("""
        INSERT INTO quotes (id, quote_number, client_id, client_name, lob, state, country,
                            effective_date, expiry_date, risk_data, requested_limits,
                            jurisdiction_minimums, jurisdiction_required, notes, assigned_agent, tenant_id)
        VALUES (:id,:qnum,:cid,:cname,:lob,:state,:country,
                :eff,:exp,CAST(:rd AS jsonb),CAST(:rl AS jsonb),CAST(:jm AS jsonb),CAST(:jr AS jsonb),:notes,:agent,:tenant_id)
    """), {
        "id": qid, "qnum": qnum, "cid": body.client_id, "cname": body.client_name,
        "lob": body.lob, "state": body.state, "country": body.country,
        "eff": body.effective_date, "exp": exp,
        "rd":  json.dumps(body.risk_data or {}),
        "rl":  json.dumps(body.requested_limits or {}),
        "jm":  json.dumps(jur["minimums"]),
        "jr":  json.dumps(jur["required"]),
        "notes": body.notes, "agent": body.assigned_agent,
        "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID,
    })
    log_activity(qid, "created", f"Quote {qnum} created for {body.lob.upper()} in {body.state}", db)
    db.commit()
    row = db.execute(text("SELECT * FROM quotes WHERE id=:id"), {"id": qid}).fetchone()
    return _enrich(r2d(row), db)

@router.get("/{qid}")
def get_quote(qid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    row = db.execute(text("SELECT * FROM quotes WHERE id=:id"), {"id": qid}).fetchone()
    if not row: raise HTTPException(404, "Not found")
    q = _enrich(r2d(row), db)
    q["activity"] = [r2d(r) for r in db.execute(text(
        "SELECT * FROM quote_activity WHERE quote_id=:id ORDER BY created_at DESC"
    ), {"id": qid}).fetchall()]
    return q

@router.patch("/{qid}")
def update_quote(qid: str, body: QuoteUpdate, db: Session = Depends(get_db)):
    ensure_tables(db)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates: return {"ok": True}
    set_parts = []
    for k, v in updates.items():
        if k in ("risk_data", "requested_limits"):
            set_parts.append(f"{k}=CAST(:{k} AS jsonb)")
            updates[k] = json.dumps(v)
        else:
            set_parts.append(f"{k}=:{k}")
    updates["id"] = qid
    db.execute(text(f"UPDATE quotes SET {', '.join(set_parts)}, updated_at=NOW() WHERE id=:id"), updates)
    if "stage" in updates:
        log_activity(qid, "stage_change", f"Stage → {body.stage}", db)
    db.commit()
    return {"ok": True}

@router.post("/{qid}/carriers")
def add_carrier(qid: str, body: CarrierQuote, db: Session = Depends(get_db)):
    ensure_tables(db)
    cid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO quote_carriers (id, quote_id, carrier_name, carrier_code,
                                   annual_premium, monthly_premium, coverage_details, deductibles, notes)
        VALUES (:id,:qid,:cn,:cc,:ap,:mp,CAST(:cd AS jsonb),CAST(:ded AS jsonb),:notes)
        ON CONFLICT (quote_id, carrier_name) DO UPDATE
          SET annual_premium=:ap, monthly_premium=:mp,
              coverage_details=CAST(:cd AS jsonb), deductibles=CAST(:ded AS jsonb),
              notes=:notes, received_at=NOW(),
              status=CASE WHEN :ap IS NOT NULL THEN 'received' ELSE quote_carriers.status END
    """), {
        "id": cid, "qid": qid, "cn": body.carrier_name, "cc": body.carrier_code,
        "ap": body.annual_premium, "mp": body.monthly_premium,
        "cd": json.dumps(body.coverage_details or {}),
        "ded": json.dumps(body.deductibles or {}),
        "notes": body.notes,
    })
    db.execute(text("UPDATE quotes SET stage='sent', updated_at=NOW() WHERE id=:id AND stage='draft'"), {"id": qid})
    log_activity(qid, "carrier_added", f"Carrier {body.carrier_name} added" + (f" @ ${body.annual_premium:,.0f}/yr" if body.annual_premium else ""), db)
    db.commit()
    return {"ok": True}

@router.patch("/{qid}/carriers/{cid}")
def update_carrier(qid: str, cid: str, body: CarrierUpdate, db: Session = Depends(get_db)):
    ensure_tables(db)
    updates: dict = {}
    for k, v in body.dict().items():
        if v is None: continue
        if k in ("coverage_details", "deductibles"):
            updates[k] = json.dumps(v)
        else:
            updates[k] = v
    if not updates: return {"ok": True}

    set_parts = [f"{k}=CAST(:{k} AS jsonb)" if k in ("coverage_details","deductibles") else f"{k}=:{k}"
                 for k in updates]
    if "annual_premium" in updates or "status" in updates:
        set_parts.append("received_at=NOW()")
    updates["id"] = cid; updates["qid"] = qid
    db.execute(text(f"UPDATE quote_carriers SET {', '.join(set_parts)} WHERE id=:id AND quote_id=:qid"), updates)

    # If any carrier has a premium → move to comparing
    has_premium = db.execute(text(
        "SELECT COUNT(*) FROM quote_carriers WHERE quote_id=:qid AND annual_premium IS NOT NULL"
    ), {"qid": qid}).scalar()
    if has_premium and (has_premium or 0) > 0:
        db.execute(text(
            "UPDATE quotes SET stage='comparing', updated_at=NOW() WHERE id=:id AND stage IN ('draft','sent')"
        ), {"id": qid})
    log_activity(qid, "carrier_updated", f"Carrier quote updated", db)
    db.commit()
    return {"ok": True}

@router.post("/{qid}/bind")
def bind_quote(qid: str, body: BindRequest, db: Session = Depends(get_db)):
    """Bind: mark winner, create a policy record, move stage to bound."""
    ensure_tables(db)
    cq = db.execute(text("SELECT * FROM quote_carriers WHERE id=:id AND quote_id=:qid"),
                    {"id": body.carrier_quote_id, "qid": qid}).fetchone()
    if not cq: raise HTTPException(404, "Carrier quote not found")
    c = r2d(cq)

    q = r2d(db.execute(text("SELECT * FROM quotes WHERE id=:id"), {"id": qid}).fetchone())
    if not q: raise HTTPException(404, "Quote not found")

    # Create policy record
    pid  = str(uuid.uuid4())
    pnum = body.policy_number or f"POL-{pid[:8].upper()}"
    db.execute(text("""
        INSERT INTO policies (id, policy_number, client_id, line_of_business, carrier_name,
                              carrier_code, premium_amount, premium_freq, effective_date, expiry_date,
                              status, coverage_summary, notes, tenant_id)
        VALUES (:id,:pnum,:cid,:lob,:cn,:cc,:prem,'annual',:eff,:exp,'bound',CAST(:cov AS jsonb),:notes,:tenant_id)
    """), {
        "id": pid, "pnum": pnum, "cid": q.get("client_id"), "lob": q["lob"],
        "cn": c["carrier_name"], "cc": c.get("carrier_code"),
        "prem": c["annual_premium"], "eff": q.get("effective_date"),
        "exp":  q.get("expiry_date"),
        "cov":  json.dumps(c.get("coverage_details") or {}),
        "notes": body.notes,
        "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID,
    })

    # Mark carrier as selected
    db.execute(text("UPDATE quote_carriers SET status='selected' WHERE id=:id"), {"id": body.carrier_quote_id})
    db.execute(text("UPDATE quote_carriers SET status='declined' WHERE quote_id=:qid AND id!=:id"),
               {"qid": qid, "id": body.carrier_quote_id})

    # Close the quote
    db.execute(text("""
        UPDATE quotes SET stage='bound', bound_policy_id=:pid,
          bound_carrier=:cn, bound_premium=:prem, updated_at=NOW()
        WHERE id=:id
    """), {"pid": pid, "cn": c["carrier_name"], "prem": c["annual_premium"], "id": qid})

    log_activity(qid, "bound",
                 f"Bound with {c['carrier_name']} @ ${c['annual_premium']:,.0f}/yr → Policy {pnum}", db)
    db.commit()
    return {"ok": True, "policy_id": pid, "policy_number": pnum}

@router.post("/{qid}/lose")
def lose_quote(qid: str, body: LossRequest, db: Session = Depends(get_db)):
    ensure_tables(db)
    db.execute(text("""
        UPDATE quotes SET stage='lost', loss_reason=:reason, loss_notes=:notes, updated_at=NOW()
        WHERE id=:id
    """), {"reason": body.reason, "notes": body.notes, "id": qid})
    log_activity(qid, "lost", f"Lost — {body.reason}. {body.notes or ''}", db)
    db.commit()
    return {"ok": True}

@router.delete("/{qid}")
def delete_quote(qid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    for t in ["quote_activity", "quote_carriers", "quotes"]:
        col = "quote_id" if t != "quotes" else "id"
        db.execute(text(f"DELETE FROM {t} WHERE {col}=:id"), {"id": qid})
    db.commit()
    return {"ok": True}
