"""
Agent Router — start, list, detail, resume agent runs.
"""
import uuid
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
from app.services.react_agent import run_agent, resume_agent

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class RunCreate(BaseModel):
    sector: str
    task_name: str
    instruction: Optional[str] = None
    approval_level: str = "external"
    trigger_type: str = "manual"
    trigger_payload: Optional[dict] = None

class ResumeRequest(BaseModel):
    decision: str
    comment: Optional[str] = None


def _row(r):
    return dict(r._mapping) if r else None


def _t() -> str:
    return str(get_current_tenant_id() or DEFAULT_TENANT_ID)


def _scope(db: Session) -> dict:
    """This organisation's runs in the domains it has switched on."""
    from app.api.features_router import enabled_domains
    return {"t": _t(), "domains": enabled_domains(db, _t())}


_SCOPE_SQL = "tenant_id::text = :t AND sector = ANY(:domains)"


def _own_run(db: Session, run_id: str):
    row = db.execute(text(f"SELECT * FROM agent_runs WHERE id::text = :id AND {_SCOPE_SQL}"),
                     {"id": run_id, **_scope(db)}).fetchone()
    if not row:
        raise HTTPException(404, "Run not found")
    return row


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/runs")
def start_run(body: RunCreate, bg: BackgroundTasks, db: Session = Depends(get_db)):
    import json
    if body.sector not in _scope(db)["domains"]:
        raise HTTPException(400, f"The {body.sector.replace('_', ' ')} module is turned off for your organisation")
    run_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO agent_runs
            (id, sector, task_name, instruction, approval_level, trigger_type, trigger_payload, status, tenant_id)
        VALUES
            (:id, :sector, :task, :instr, :al, :tt, CAST(:tp AS jsonb), 'idle', :tenant_id)
    """), {
        "id": run_id, "sector": body.sector, "task": body.task_name,
        "instr": body.instruction, "al": body.approval_level,
        "tt": body.trigger_type,
        "tp": json.dumps(body.trigger_payload or {}),
        "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID,
    })
    db.commit()
    bg.add_task(run_agent, run_id)
    return {"run_id": run_id, "status": "started"}


@router.get("/runs")
def list_runs(
    sector: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    q = f"SELECT * FROM agent_runs WHERE {_SCOPE_SQL}"
    params: dict = _scope(db)
    if sector:
        q += " AND sector = :sector"; params["sector"] = sector
    if status:
        q += " AND status = :status"; params["status"] = status
    q += " ORDER BY started_at DESC LIMIT :limit"; params["limit"] = limit
    rows = db.execute(text(q), params).fetchall()
    return [_row(r) for r in rows]


@router.get("/runs/stats")
def run_stats(db: Session = Depends(get_db)):
    rows = db.execute(text(
        f"SELECT status, COUNT(*) as cnt FROM agent_runs WHERE {_SCOPE_SQL} GROUP BY status"
    ), _scope(db)).fetchall()
    return {r._mapping["status"]: r._mapping["cnt"] for r in rows}


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = _row(_own_run(db, run_id))
    tool_calls = db.execute(text(
        "SELECT * FROM agent_tool_calls WHERE run_id = :id ORDER BY called_at ASC"
    ), {"id": run_id}).fetchall()
    run["tool_calls"] = [_row(t) for t in tool_calls]
    return run


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str, body: ResumeRequest, bg: BackgroundTasks, db: Session = Depends(get_db)):
    row = _own_run(db, run_id)
    if row._mapping["status"] != "waiting_approval":
        raise HTTPException(400, f"Run is not paused (status={row._mapping['status']})")
    bg.add_task(resume_agent, run_id, body.decision, body.comment or "")
    return {"ok": True, "decision": body.decision}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)):
    _own_run(db, run_id)
    db.execute(text("""
        UPDATE agent_runs SET status='failed', error_message='Cancelled by user', ended_at=NOW()
        WHERE id = :id AND status NOT IN ('completed','failed','rejected')
    """), {"id": run_id})
    db.commit()
    return {"ok": True}


@router.get("/tools")
def list_tools():
    from app.services.agent_tools import TOOL_REGISTRY
    return {"tools": list(TOOL_REGISTRY.keys())}
