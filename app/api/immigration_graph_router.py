"""
Immigration document graph API — how a client's documents relate, where they disagree, and the
lawyer's corrections (docs/design/immigration-document-graph.md).
"""

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.auth_router import get_current_user
from app.core.audit import record as audit
from app.core.database import get_db
from app.core.tenant_scope import current_tenant
from app.services.immigration import graph

router = APIRouter(prefix="/api/immigration/graph", tags=["immigration-graph"])


def _t() -> str:
    return str(current_tenant())


def _actor(user: dict) -> str:
    return (user or {}).get("username") or "unknown"


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except LookupError as e:
        raise HTTPException(404, f"{str(e).capitalize() or 'Record'} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/people")
def people(db: Session = Depends(get_db)):
    """Everyone with immigration documents, worst problems first."""
    return {"people": graph.people(db, _t())}


@router.get("/person")
def person(key: Optional[str] = None, client_id: Optional[str] = None, db: Session = Depends(get_db)):
    pk = f"client:{client_id}" if client_id else key
    if not pk:
        raise HTTPException(400, "Give key or client_id")
    return graph.person_graph(db, _t(), pk)


@router.post("/rebuild")
def rebuild(request: Request, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    out = graph.rebuild(db, _t())
    audit("immigration.graph_rebuilt", actor=_actor(user), extra=out, tenant_id=_t())
    return out


@router.post("/nodes/{node_id}/facts")
def correct_fact(node_id: str, request: Request, user: dict = Depends(get_current_user), body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(graph.correct_fact, db, _t(), node_id, body.get("field"), body.get("value"),
                  body.get("reason"), _actor(user))


@router.post("/edges/{edge_id}")
def decide_edge(edge_id: str, request: Request, user: dict = Depends(get_current_user), body: dict = Body(...), db: Session = Depends(get_db)):
    return _guard(graph.decide_edge, db, _t(), edge_id, body.get("decision"), body.get("reason"),
                  _actor(user), body.get("to_node"))


@router.post("/checks/{check_id}")
def decide_check(check_id: str, request: Request, user: dict = Depends(get_current_user), body: dict = Body(...), db: Session = Depends(get_db)):
    if body.get("decision") == "use_value":
        return _guard(graph.resolve_identity, db, _t(), check_id, body.get("value"), body.get("reason"), _actor(user))
    return _guard(graph.decide_check, db, _t(), check_id, body.get("decision"), body.get("reason"), _actor(user))
