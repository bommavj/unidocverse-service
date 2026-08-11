import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent-reports", tags=["agent-reports"])


@router.get("")
def list_reports(
    agent_type: str = Query(None),
    limit: int = Query(30, le=100),
    db: Session = Depends(get_db)
):
    """Return recent agent-generated reports, optionally filtered by agent_type."""
    try:
        params = {"limit": limit}
        where = "WHERE 1=1"
        if agent_type:
            where += " AND r.agent_type = :agent_type"
            params["agent_type"] = agent_type

        rows = db.execute(text(f"""
            SELECT r.id, r.agent_type, r.report_date, r.title, r.summary,
                   r.body, r.created_at,
                   a.status AS run_status
            FROM agent_reports r
            LEFT JOIN agent_runs a ON a.id = r.run_id
            {where}
            ORDER BY r.created_at DESC
            LIMIT :limit
        """), params).fetchall()

        return {
            "reports": [
                {
                    "id":          str(r[0]),
                    "agent_type":  r[1],
                    "report_date": r[2].isoformat() if r[2] else None,
                    "title":       r[3],
                    "summary":     r[4],
                    "body":        r[5],
                    "created_at":  r[6].isoformat() if r[6] else None,
                    "run_status":  r[7],
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.error(f"❌ agent-reports list failed: {e}")
        return {"reports": []}


@router.get("/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_db)):
    """Return a single report by id."""
    try:
        row = db.execute(text("""
            SELECT r.id, r.agent_type, r.report_date, r.title, r.summary,
                   r.body, r.created_at, a.status AS run_status
            FROM agent_reports r
            LEFT JOIN agent_runs a ON a.id = r.run_id
            WHERE r.id = :id
        """), {"id": report_id}).fetchone()

        if not row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Report not found")

        return {
            "id":          str(row[0]),
            "agent_type":  row[1],
            "report_date": row[2].isoformat() if row[2] else None,
            "title":       row[3],
            "summary":     row[4],
            "body":        row[5],
            "created_at":  row[6].isoformat() if row[6] else None,
            "run_status":  row[7],
        }
    except Exception as e:
        logger.error(f"❌ agent-reports get failed: {e}")
        raise


@router.post("/run-now")
async def run_now(
    agent_type: str = Query(None, description="Specific agent to run, or omit for all enabled"),
    db: Session = Depends(get_db)
):
    """
    Adhoc trigger — runs all enabled domain agents (or one specific agent) immediately
    in the background. Returns immediately; check /api/agent-reports for results.
    """
    from app.services.domain_agents.registry import get_agent_class
    from app.services.scheduler import _run_single_agent

    try:
        if agent_type:
            # Run a specific agent regardless of enabled flag
            row = db.execute(text("""
                SELECT id, agent_type, task_name FROM scheduled_agents
                WHERE agent_type = :agent_type
            """), {"agent_type": agent_type}).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"No scheduled agent for type '{agent_type}'")
            due = [(str(row[0]), row[1], row[2])]
        else:
            # Run all currently enabled agents
            rows = db.execute(text("""
                SELECT id, agent_type, task_name FROM scheduled_agents WHERE enabled = TRUE
            """)).fetchall()
            due = [(str(r[0]), r[1], r[2]) for r in rows]

        if not due:
            return {"message": "No enabled agents to run", "triggered": 0}

        # Fire and forget — don't block the HTTP response
        async def _fire():
            await asyncio.gather(*[
                _run_single_agent(sid, atype, tname)
                for sid, atype, tname in due
            ])

        asyncio.create_task(_fire())

        return {
            "message":   f"Triggered {len(due)} agent(s) — reports will appear shortly",
            "triggered": len(due),
            "agents":    [d[1] for d in due],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ run-now failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
