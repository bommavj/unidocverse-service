"""
Cloud model providers (Settings → Integrations → Cloud models).

Admins store an API key per organisation for any listed provider (Anthropic,
OpenAI, Gemini, xAI, Mistral, DeepSeek, … or a custom OpenAI-compatible URL), test it, and make
one provider active. While a provider is active it answers questions and chat;
the local BYOM model stays selected but unused. Keys are encrypted at rest and
never returned in full.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_router import require_admin
from app.core import llm
from app.core.database import get_db
from app.core.secret_box import decrypt, encrypt, mask
from app.core.tenant import get_current_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm-providers", tags=["llm-providers"])


class ProviderSave(BaseModel):
    api_key: Optional[str] = None      # omit to keep the stored key and only change the model
    model: str
    base_url: Optional[str] = None     # custom provider only


class ProviderTest(BaseModel):
    api_key: Optional[str] = None      # omit to test the stored key
    model: Optional[str] = None
    base_url: Optional[str] = None


class PolicyUpdate(BaseModel):
    ask_before_send: bool
    mask_identifiers: bool
    blocked_types: List[str]
    upload_cloud: bool = False
    upload_types: List[str] = []
    upload_model: Optional[str] = None
    monthly_budget_usd: Optional[float] = None
    cascade_local_first: bool = True


class Activate(BaseModel):
    provider: Optional[str] = None     # None → back to the local model


def _ensure(db: Session) -> None:
    db.execute(text("""CREATE TABLE IF NOT EXISTS tenant_llm_providers (
        tenant_id TEXT NOT NULL, provider TEXT NOT NULL, api_key_enc TEXT NOT NULL, model TEXT NOT NULL, base_url TEXT,
        is_active BOOLEAN NOT NULL DEFAULT FALSE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_by TEXT, PRIMARY KEY (tenant_id, provider))"""))
    db.execute(text("ALTER TABLE tenant_llm_providers ADD COLUMN IF NOT EXISTS base_url TEXT"))
    db.execute(text("""CREATE TABLE IF NOT EXISTS tenant_llm_policy (
        tenant_id TEXT PRIMARY KEY, ask_before_send BOOLEAN NOT NULL DEFAULT TRUE,
        mask_identifiers BOOLEAN NOT NULL DEFAULT TRUE, blocked_types TEXT[] NOT NULL DEFAULT '{}',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by TEXT)"""))
    for col in ("upload_cloud BOOLEAN NOT NULL DEFAULT FALSE", "upload_types TEXT[] NOT NULL DEFAULT '{}'",
                "upload_model TEXT", "monthly_budget_usd NUMERIC(10,2)",
                "cascade_local_first BOOLEAN NOT NULL DEFAULT TRUE"):
        db.execute(text(f"ALTER TABLE tenant_llm_policy ADD COLUMN IF NOT EXISTS {col}"))
    db.execute(text("""CREATE TABLE IF NOT EXISTS llm_cloud_calls (
        id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, username TEXT, provider TEXT NOT NULL, model TEXT NOT NULL,
        documents JSONB NOT NULL DEFAULT '[]'::jsonb, chars_sent INTEGER NOT NULL, masked_values INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER, output_tokens INTEGER, est_cost_usd NUMERIC(12,6), status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
    db.commit()


def _check(provider: str) -> None:
    if provider not in llm.PROVIDERS:
        raise HTTPException(404, f"Unknown provider '{provider}'")


def _stored(db: Session, tid: str, provider: str):
    return db.execute(text("SELECT api_key_enc, model, base_url, is_active FROM tenant_llm_providers "
                           "WHERE tenant_id = :t AND provider = :p"), {"t": tid, "p": provider}).fetchone()


@router.get("")
def list_providers(user=Depends(require_admin), db: Session = Depends(get_db)):
    _ensure(db)
    tid = get_current_tenant_id()
    rows = {r.provider: r for r in db.execute(text(
        "SELECT provider, api_key_enc, model, base_url, is_active, updated_at FROM tenant_llm_providers WHERE tenant_id = :t"),
        {"t": tid})}
    out = []
    for key, meta in llm.PROVIDERS.items():
        r = rows.get(key)
        try:
            masked = mask(decrypt(r.api_key_enc)) if r else None
        except ValueError:
            masked = "re-enter key"
        out.append({"provider": key, "label": meta["label"], "models": meta["models"],
                    "default_model": meta["default_model"], "configured": bool(r),
                    "needs_url": bool(meta.get("needs_url")), "base_url": r.base_url if r else None,
                    "key_hint": masked, "model": r.model if r else meta["default_model"],
                    "active": bool(r and r.is_active),
                    "updated_at": r.updated_at.isoformat() if r else None})
    return {"providers": out, "active": next((p["provider"] for p in out if p["active"]), None)}


class RoleIn(BaseModel):
    engine: str                      # local | cloud
    model: str
    provider: Optional[str] = None   # cloud only


def _ensure_roles_table(db: Session) -> None:
    db.execute(text("""CREATE TABLE IF NOT EXISTS tenant_model_roles (
        tenant_id TEXT NOT NULL, role TEXT NOT NULL, engine TEXT NOT NULL, provider TEXT, model TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by TEXT, PRIMARY KEY (tenant_id, role))"""))
    db.commit()


@router.get("/model-roles")
def model_roles(db: Session = Depends(get_db)):
    """Each role, what it uses now, and the models it can use: installed Ollama models and
    configured cloud providers. Vision is local only (page images can't be masked)."""
    _ensure_roles_table(db)
    tid = str(get_current_tenant_id())
    local = engine_status()["ollama"]["models"]
    clouds = [{"provider": r.provider, "label": llm.PROVIDERS.get(r.provider, {}).get("label", r.provider),
               "model": r.model} for r in db.execute(text(
                   "SELECT provider, model FROM tenant_llm_providers WHERE tenant_id = :t ORDER BY provider"), {"t": tid})]
    out = []
    for key, meta in llm.ROLES.items():
        rc = llm.role_config(key)
        out.append({"role": key, **meta, "current": rc, "local_only": key == "vision"})
    return {"roles": out, "local_models": local, "cloud_providers": clouds}


@router.put("/model-roles/{role}")
def set_model_role(role: str, body: RoleIn, user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    if role not in llm.ROLES:
        raise HTTPException(404, "Unknown role")
    if body.engine not in ("local", "cloud"):
        raise HTTPException(400, "engine must be local or cloud")
    if body.engine == "cloud" and role == "vision":
        raise HTTPException(400, "Vision reads page images, which can't be masked — it runs locally only")
    if body.engine == "cloud":
        if not body.provider or not db.execute(text("SELECT 1 FROM tenant_llm_providers WHERE tenant_id = :t "
                                                    "AND provider = :p"), {"t": str(get_current_tenant_id()), "p": body.provider}).fetchone():
            raise HTTPException(400, "Add that cloud provider in Cloud models first")
    _ensure_roles_table(db)
    db.execute(text("""INSERT INTO tenant_model_roles (tenant_id, role, engine, provider, model, updated_by)
        VALUES (:t, :r, :e, :p, :m, :u) ON CONFLICT (tenant_id, role) DO UPDATE SET engine = EXCLUDED.engine,
        provider = EXCLUDED.provider, model = EXCLUDED.model, updated_at = now(), updated_by = EXCLUDED.updated_by"""),
        {"t": str(get_current_tenant_id()), "r": role, "e": body.engine,
         "p": body.provider if body.engine == "cloud" else None, "m": body.model.strip(), "u": user.get("username")})
    db.commit()
    llm.invalidate(str(get_current_tenant_id()))
    from app.core.audit import record as audit
    audit("model_role.set", actor=user.get("username"), detail=f"{role} → {body.engine}:{body.provider or ''}:{body.model}")
    return {"success": True}


@router.delete("/model-roles/{role}")
def reset_model_role(role: str, user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    _ensure_roles_table(db)
    db.execute(text("DELETE FROM tenant_model_roles WHERE tenant_id = :t AND role = :r"),
               {"t": str(get_current_tenant_id()), "r": role})
    db.commit()
    llm.invalidate(str(get_current_tenant_id()))
    from app.core.audit import record as audit
    audit("model_role.reset", actor=user.get("username"), detail=role)
    return {"success": True}


@router.get("/engine-status")
def engine_status():
    """Can this install answer questions? Local Ollama (and its models) and/or an active cloud
    model. The Lite edition ships no model, so the dashboard asks the user to choose when neither
    is available. Any signed-in user may call this; nothing secret is returned."""
    import httpx
    from app.core.config import settings
    base = (getattr(settings, "OLLAMA_HOST", None) or "http://127.0.0.1:11434").rstrip("/")
    models: List[str] = []
    reachable = False
    try:
        r = httpx.get(f"{base}/api/tags", timeout=3)
        reachable = r.status_code == 200
        models = [m.get("name") for m in r.json().get("models", []) if m.get("name")]
    except Exception:
        pass
    cfg = llm.active_provider()
    chat_models = [m for m in models if not any(k in m for k in ("embed", "minilm", "bge"))]
    from app.services.vlm_forms import VLM_MODEL
    vision = llm.local_model_for("vision", VLM_MODEL)
    return {
        "ollama": {"reachable": reachable, "models": chat_models},
        "cloud": {"provider": cfg["provider"], "model": cfg["model"]} if cfg else None,
        "ready": bool(chat_models) or bool(cfg),
        # Forms and charts are read accurately only when the vision model is installed
        "vision": {"model": vision, "installed": vision in models, "pull": f"ollama pull {vision.split(':')[0]}"},
    }


@router.get("/policy")
def get_policy(user=Depends(require_admin), db: Session = Depends(get_db)):
    _ensure(db)
    types = [r[0] for r in db.execute(text(
        "SELECT DISTINCT doc_type FROM documents WHERE tenant_id = :t AND doc_type IS NOT NULL ORDER BY 1"),
        {"t": get_current_tenant_id()})]
    return {**llm.policy(), "known_types": sorted(set(types) | set(llm.DEFAULT_BLOCKED_TYPES)),
            "month_spend_usd": round(llm.month_spend(), 4)}


@router.put("/policy")
def put_policy(body: PolicyUpdate, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ensure(db)
    tid = get_current_tenant_id()
    if body.monthly_budget_usd is not None and body.monthly_budget_usd < 0:
        raise HTTPException(400, "Budget can't be negative")
    blocked = sorted(set(body.blocked_types))
    upload_types = sorted(set(body.upload_types) - set(blocked))     # never-send always wins
    db.execute(text("""INSERT INTO tenant_llm_policy (tenant_id, ask_before_send, mask_identifiers, blocked_types,
            upload_cloud, upload_types, upload_model, monthly_budget_usd, cascade_local_first, updated_by)
        VALUES (:t, :a, :m, :b, :uc, :ut, :um, :mb, :cl, :u)
        ON CONFLICT (tenant_id) DO UPDATE SET ask_before_send = EXCLUDED.ask_before_send,
            mask_identifiers = EXCLUDED.mask_identifiers, blocked_types = EXCLUDED.blocked_types,
            upload_cloud = EXCLUDED.upload_cloud, upload_types = EXCLUDED.upload_types,
            upload_model = EXCLUDED.upload_model, monthly_budget_usd = EXCLUDED.monthly_budget_usd,
            cascade_local_first = EXCLUDED.cascade_local_first,
            updated_at = now(), updated_by = EXCLUDED.updated_by"""),
        {"t": tid, "a": body.ask_before_send, "m": body.mask_identifiers, "b": blocked,
         "uc": body.upload_cloud, "ut": upload_types, "um": (body.upload_model or "").strip() or None,
         "mb": body.monthly_budget_usd, "cl": body.cascade_local_first, "u": user.get("username")})
    db.commit()
    llm.invalidate(tid)
    return {"success": True}


@router.get("/usage")
def usage(days: int = 30, user=Depends(require_admin), db: Session = Depends(get_db)):
    """Cloud calls in the last `days`: totals and the most recent calls (audit trail)."""
    _ensure(db)
    tid = get_current_tenant_id()
    p = {"t": tid, "d": max(1, min(days, 365))}
    tot = db.execute(text("""SELECT count(*) calls, coalesce(sum(chars_sent),0) chars,
        coalesce(sum(input_tokens),0) tin, coalesce(sum(output_tokens),0) tout, sum(est_cost_usd) cost
        FROM llm_cloud_calls WHERE tenant_id = :t AND created_at > now() - make_interval(days => :d)"""), p).fetchone()
    rows = db.execute(text("""SELECT created_at, username, provider, model, documents, chars_sent, masked_values,
        input_tokens, output_tokens, est_cost_usd, status FROM llm_cloud_calls
        WHERE tenant_id = :t AND created_at > now() - make_interval(days => :d)
        ORDER BY created_at DESC LIMIT 100"""), p).fetchall()
    return {"days": p["d"],
            "totals": {"calls": tot.calls, "chars_sent": tot.chars, "input_tokens": tot.tin,
                       "output_tokens": tot.tout, "est_cost_usd": float(tot.cost) if tot.cost is not None else None},
            "calls": [{"at": r.created_at.isoformat(), "user": r.username, "provider": r.provider, "model": r.model,
                       "documents": r.documents, "chars_sent": r.chars_sent, "masked_values": r.masked_values,
                       "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                       "est_cost_usd": float(r.est_cost_usd) if r.est_cost_usd is not None else None,
                       "status": r.status} for r in rows]}


@router.get("/cascade-outcomes")
def cascade_outcomes(days: int = 90, user=Depends(require_admin), db: Session = Depends(get_db)):
    """Cascade decisions as JSON Lines — labelled data for evaluating or training the escalation step."""
    from fastapi.responses import PlainTextResponse
    import json as _json
    llm._ensure_tables()
    rows = db.execute(text("""SELECT created_at, question, documents, local_answer, local_passed, local_reason,
            escalated, outcome, cloud_model, cloud_answer, cloud_verified, disagreement
        FROM llm_cascade_outcomes WHERE tenant_id = :t AND created_at > now() - make_interval(days => :d)
        ORDER BY created_at"""), {"t": get_current_tenant_id(), "d": max(1, min(days, 3650))}).mappings().fetchall()
    body = "\n".join(_json.dumps({**dict(r), "created_at": r["created_at"].isoformat()}, default=str) for r in rows)
    return PlainTextResponse(body + ("\n" if body else ""), media_type="application/x-ndjson",
                             headers={"Content-Disposition": "attachment; filename=cascade-outcomes.jsonl"})


@router.put("/{provider}")
def save_provider(provider: str, body: ProviderSave, user=Depends(require_admin), db: Session = Depends(get_db)):
    _check(provider)
    _ensure(db)
    tid = get_current_tenant_id()
    model = body.model.strip()
    if not model:
        raise HTTPException(400, "Enter the model id")
    base_url = (body.base_url or "").strip() or None
    if llm.PROVIDERS[provider].get("needs_url") and not base_url:
        raise HTTPException(400, "Enter the API base URL")
    if base_url and not base_url.startswith("https://") and not base_url.startswith("http://localhost"):
        raise HTTPException(400, "Base URL must use https://")
    existing = _stored(db, tid, provider)
    if body.api_key:
        key_enc = encrypt(body.api_key.strip())
    elif existing:
        key_enc = existing.api_key_enc
    else:
        raise HTTPException(400, "Enter an API key")
    db.execute(text("""INSERT INTO tenant_llm_providers (tenant_id, provider, api_key_enc, model, base_url, updated_by)
        VALUES (:t, :p, :k, :m, :b, :u)
        ON CONFLICT (tenant_id, provider) DO UPDATE
        SET api_key_enc = EXCLUDED.api_key_enc, model = EXCLUDED.model, base_url = EXCLUDED.base_url,
            updated_at = now(), updated_by = EXCLUDED.updated_by"""),
        {"t": tid, "p": provider, "k": key_enc, "m": model, "b": base_url, "u": user.get("username")})
    db.commit()
    llm.invalidate(tid)
    logger.info(f"Cloud provider {provider} saved for tenant {tid} by {user.get('username')}")
    return {"success": True}


@router.post("/{provider}/test")
def test_provider(provider: str, body: ProviderTest, user=Depends(require_admin), db: Session = Depends(get_db)):
    _check(provider)
    _ensure(db)
    existing = _stored(db, get_current_tenant_id(), provider)
    try:
        key = body.api_key or (decrypt(existing.api_key_enc) if existing else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    model = (body.model or (existing.model if existing else "")).strip()
    base_url = (body.base_url or (existing.base_url if existing else "") or "").strip() or None
    if not key or not model:
        raise HTTPException(400, "Enter an API key and a model first")
    try:
        reply = llm.test_connection(provider, key, model, base_url)
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"[:400]}
    return {"success": True, "reply": reply[:80]}


@router.post("/{provider}/models")
def provider_models(provider: str, body: ProviderTest, user=Depends(require_admin), db: Session = Depends(get_db)):
    """Models available to the entered (or stored) key, for the model picker."""
    _check(provider)
    _ensure(db)
    existing = _stored(db, get_current_tenant_id(), provider)
    try:
        key = body.api_key or (decrypt(existing.api_key_enc) if existing else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not key:
        return {"models": [], "error": None}
    base_url = (body.base_url or (existing.base_url if existing else "") or "").strip() or None
    try:
        return {"models": llm.list_models(provider, key, base_url)[:200], "error": None}
    except Exception as e:
        # Some providers don't offer a model list; the admin can still type the id
        return {"models": [], "error": f"{type(e).__name__}: {e}"[:300]}


@router.post("/activate")
def activate(body: Activate, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ensure(db)
    tid = get_current_tenant_id()
    if body.provider:
        _check(body.provider)
        if not _stored(db, tid, body.provider):
            raise HTTPException(400, "Save an API key for this provider first")
    db.execute(text("UPDATE tenant_llm_providers SET is_active = FALSE WHERE tenant_id = :t"), {"t": tid})
    if body.provider:
        db.execute(text("UPDATE tenant_llm_providers SET is_active = TRUE, updated_at = now() "
                        "WHERE tenant_id = :t AND provider = :p"), {"t": tid, "p": body.provider})
    db.commit()
    llm.invalidate(tid)
    logger.info(f"Tenant {tid}: answers now use {body.provider or 'the local model'} ({user.get('username')})")
    return {"success": True, "active": body.provider}


@router.delete("/{provider}")
def remove_provider(provider: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    _check(provider)
    _ensure(db)
    tid = get_current_tenant_id()
    db.execute(text("DELETE FROM tenant_llm_providers WHERE tenant_id = :t AND provider = :p"), {"t": tid, "p": provider})
    db.commit()
    llm.invalidate(tid)
    return {"success": True}
