"""
Guardrails API — view/update config, query audit log.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from app.core.guardrails import (
    _load_config, save_config, read_audit_log,
    purge_old_audit_logs, _default_config,
)

router = APIRouter(prefix="/api/guardrails", tags=["guardrails"])


# ── Config endpoints ──────────────────────────────────────────────────────────

@router.get("/config")
def get_config():
    return _load_config()


@router.post("/config")
def update_config(body: Dict[str, Any]):
    existing = _load_config()
    # Deep merge so callers can patch a single layer without wiping others
    def _merge(base: dict, overlay: dict) -> dict:
        for k, v in overlay.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                _merge(base[k], v)
            else:
                base[k] = v
        return base
    merged = _merge(existing, body)
    save_config(merged)
    return {"ok": True, "config": merged}


@router.post("/config/reset")
def reset_config():
    cfg = _default_config()
    save_config(cfg)
    return {"ok": True, "config": cfg}


# ── Audit log endpoints ───────────────────────────────────────────────────────

@router.get("/audit")
def get_audit_log(
    session_id: Optional[str] = None,
    event_prefix: Optional[str] = None,
    since_hours: int = 24,
    limit: int = 200,
):
    return {
        "events": read_audit_log(
            session_id=session_id,
            event_prefix=event_prefix,
            since_hours=since_hours,
            limit=limit,
        )
    }


@router.post("/audit/purge")
def purge_audit():
    purge_old_audit_logs()
    return {"ok": True}


# ── Runtime test endpoints (dev / smoke-test) ─────────────────────────────────

class TestInputBody(BaseModel):
    text: str
    session_id: str = "test"


@router.post("/test/input")
def test_input_guardrail(body: TestInputBody):
    from app.core.guardrails import check_input, GuardrailViolation
    try:
        check_input(body.text, body.session_id)
        return {"passed": True}
    except GuardrailViolation as e:
        return {"passed": False, "layer": e.layer, "rule": e.rule, "message": e.message}


@router.post("/test/output")
def test_output_guardrail(body: TestInputBody):
    from app.core.guardrails import sanitize_output, GuardrailViolation
    try:
        result = sanitize_output(body.text, body.session_id)
        return {"passed": True, "output": result}
    except GuardrailViolation as e:
        return {"passed": False, "layer": e.layer, "rule": e.rule, "message": e.message}
