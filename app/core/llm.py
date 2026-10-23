"""
One entry point for model calls: local Ollama, or the organisation's cloud
provider (Anthropic, OpenAI, Gemini, …) when an admin has activated one.

`chat()` takes the same arguments as `ollama.chat()` and returns the same shape
({"message": {"content": ...}}, or an iterator of those when stream=True).

Local is the default. A call goes to the cloud only when its caller passes
cloud=True — today that is only the final answer in Research — so question
analysis, routing, follow-ups, drafting and all upload processing stay on this
machine. That keeps documents local unless they're needed for the answer, and
sends one call per question instead of two or three (lower cost).

Every cloud call passes the organisation's policy first:
  • never-send document types  → answered locally instead
  • ask before sending          → CloudConsentRequired until the user says Send
  • mask identifiers            → SSNs, A-numbers, emails, phones and long account /
                                  receipt numbers become REF_0001 … before sending and
                                  are restored in the answer
and is written to the llm_cloud_calls audit log.
"""
import contextvars
import json
import logging
import re
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

import ollama

logger = logging.getLogger(__name__)

# Model ids change often; Anthropic's current ids are pinned, the rest come from
# each provider's own model list (or the admin types one).
# "compat" providers expose an OpenAI-compatible Chat Completions API at base_url.
PROVIDERS = {
    "anthropic":  {"label": "Anthropic Claude", "kind": "anthropic",
                   "default_model": "claude-opus-5-5",
                   "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]},
    "openai":     {"label": "OpenAI", "kind": "compat", "base_url": None},
    "gemini":     {"label": "Google Gemini", "kind": "compat",
                   "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "xai":        {"label": "xAI Grok", "kind": "compat", "base_url": "https://api.x.ai/v1"},
    "mistral":    {"label": "Mistral AI", "kind": "compat", "base_url": "https://api.mistral.ai/v1"},
    "deepseek":   {"label": "DeepSeek", "kind": "compat", "base_url": "https://api.deepseek.com"},
    "groq":       {"label": "Groq", "kind": "compat", "base_url": "https://api.groq.com/openai/v1"},
    "together":   {"label": "Together AI", "kind": "compat", "base_url": "https://api.together.xyz/v1"},
    "openrouter": {"label": "OpenRouter", "kind": "compat", "base_url": "https://openrouter.ai/api/v1"},
    "perplexity": {"label": "Perplexity", "kind": "compat", "base_url": "https://api.perplexity.ai"},
    "custom":     {"label": "Custom (OpenAI-compatible URL)", "kind": "compat", "base_url": None,
                   "needs_url": True},
}
for _p in PROVIDERS.values():
    _p.setdefault("default_model", "")
    _p.setdefault("models", [])

# USD per million tokens (input, output) — Anthropic list prices; other providers'
# prices vary by account, so their usage is shown in tokens only.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}

DEFAULT_BLOCKED_TYPES = ["passport", "visa_document", "birth_certificate", "uscis_receipt", "immigration_notice",
                         "w2", "form_1040", "medical_record", "government_id", "drivers_license", "ssn_card"]
DEFAULT_POLICY = {"ask_before_send": True, "mask_identifiers": True, "blocked_types": DEFAULT_BLOCKED_TYPES,
                  "cascade_local_first": True,       # answer locally first; use the cloud only if that fails
                  # Upload processing: off unless an admin turns it on for chosen document types
                  "upload_cloud": False, "upload_types": [], "upload_model": None,
                  "monthly_budget_usd": None}

# Per request: the user's choice on the consent prompt ("ok" | "local"), who is asking,
# and notes to show under the answer (e.g. "answered locally because …").
_DECISION: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("cloud_decision", default=None)
_USER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("cloud_user", default=None)
_NOTES: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar("cloud_notes", default=None)
# Where the most recent chat() call actually ran: "local" | "cloud" (with the provider label)
_ROUTE: contextvars.ContextVar[Tuple[str, str]] = contextvars.ContextVar("cloud_route", default=("local", ""))


def decision() -> Optional[str]:
    return _DECISION.get()


def last_route() -> Tuple[str, str]:
    return _ROUTE.get()


def begin_request(decision: Optional[str], user: Optional[str]) -> None:
    _DECISION.set(decision if decision in ("ok", "local") else None)
    _USER.set(user)
    _NOTES.set([])


def request_notes() -> List[str]:
    return list(_NOTES.get() or [])


def _note(msg: str) -> None:
    notes = _NOTES.get()
    if notes is not None and msg not in notes:
        notes.append(msg)


class CloudConsentRequired(Exception):
    """Raised before the first cloud call of a question when the policy asks first."""
    def __init__(self, provider: str, model: str, documents: List[str]):
        super().__init__("consent required")
        self.provider, self.model, self.documents = provider, model, documents
        self.resume: Optional[str] = None      # token for the prepared answer step (see park / unpark)


# Work done before the consent question: the prompt, passages and local answer, so the
# user's choice runs only the final step instead of the whole question again.
_PARKED: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PARK_TTL = 15 * 60


def park(payload: Dict[str, Any]) -> str:
    import secrets
    now = time.time()
    for k in [k for k, (t, _) in _PARKED.items() if now - t > _PARK_TTL]:
        _PARKED.pop(k, None)
    token = secrets.token_urlsafe(16)
    _PARKED[token] = (now, {**payload, "_tenant": str(_current_tenant())})
    return token


def unpark(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The parked step, once, and only for the organisation that parked it."""
    if not token or token not in _PARKED:
        return None
    t, payload = _PARKED.pop(token)
    if time.time() - t > _PARK_TTL or payload.get("_tenant") != str(_current_tenant()):
        return None
    return payload


def _current_tenant():
    from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
    return get_current_tenant_id() or DEFAULT_TENANT_ID


_CACHE: Dict[str, Any] = {}          # (kind, tenant_id) -> (fetched_at, value)
_CACHE_TTL = 30


def invalidate(tenant_id: Optional[str] = None) -> None:
    if tenant_id:
        for k in [k for k in _CACHE if k[1] == tenant_id]:
            _CACHE.pop(k, None)
    else:
        _CACHE.clear()


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _tid(tenant_id: Optional[str] = None) -> str:
    from app.core.tenant import get_current_tenant_id
    return tenant_id or get_current_tenant_id()


def active_provider(tenant_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    """{'provider', 'model', 'api_key', 'base_url'} for the tenant's active cloud provider, else None."""
    tid = _tid(tenant_id)
    hit = _CACHE.get(("provider", tid))
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    cfg = None
    try:
        from sqlalchemy import text
        from app.core.secret_box import decrypt
        db = _db()
        try:
            row = db.execute(text("SELECT provider, model, api_key_enc, base_url FROM tenant_llm_providers "
                                  "WHERE tenant_id = :t AND is_active"), {"t": tid}).fetchone()
        finally:
            db.close()
        if row:
            cfg = {"provider": row.provider, "model": row.model, "api_key": decrypt(row.api_key_enc),
                   "base_url": row.base_url}
    except Exception as e:      # table missing on an old install, key can't be decrypted, …
        logger.warning(f"Cloud provider lookup failed, using local model: {e}")
    _CACHE[("provider", tid)] = (time.time(), cfg)
    return cfg


# ── Model roles ─────────────────────────────────────────────────────────────
# Each step of the pipeline can use its own model. Unset roles keep the defaults below.
ROLES: Dict[str, Dict[str, str]] = {
    "planner":    {"label": "Planner", "help": "Understands the question and plans the search",
                   "default": "Active local model (always local unless you choose otherwise)"},
    "answer":     {"label": "Answer", "help": "Writes the answer from the passages found",
                   "default": "Active local model, or the active cloud model with local-first escalation"},
    "extraction": {"label": "Extraction", "help": "Pulls fields and facts from documents at upload",
                   "default": "Active local model (cloud only if upload processing is turned on)"},
    "vision":     {"label": "Vision", "help": "Reads forms and charts from page images at upload",
                   "default": "gemma4 (local)"},
}


def role_config(role: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    """{'engine': 'local', 'model'} or {'engine': 'cloud', 'provider', 'model'} — None when unset."""
    tid = str(_tid(tenant_id))
    hit = _CACHE.get(("roles", tid))
    if not (hit and time.time() - hit[0] < _CACHE_TTL):
        roles: Dict[str, Dict[str, str]] = {}
        try:
            from sqlalchemy import text
            db = _db()
            try:
                for r in db.execute(text("SELECT role, engine, provider, model FROM tenant_model_roles "
                                         "WHERE tenant_id = :t"), {"t": str(tid)}):
                    roles[r.role] = {"engine": r.engine, "provider": r.provider, "model": r.model}
            finally:
                db.close()
        except Exception:
            roles = {}                                  # table missing on an old install
        _CACHE[("roles", tid)] = (time.time(), roles)
        hit = _CACHE[("roles", tid)]
    return hit[1].get(role)


def provider_cfg(provider: str, model: Optional[str] = None, tenant_id: Optional[str] = None):
    """A configured provider (active or not) with an optional model override, else None."""
    try:
        from sqlalchemy import text
        from app.core.secret_box import decrypt
        db = _db()
        try:
            row = db.execute(text("SELECT provider, model, api_key_enc, base_url FROM tenant_llm_providers "
                                  "WHERE tenant_id = :t AND provider = :p"), {"t": _tid(tenant_id), "p": provider}).fetchone()
        finally:
            db.close()
        if row:
            return {"provider": row.provider, "model": model or row.model, "api_key": decrypt(row.api_key_enc),
                    "base_url": row.base_url}
    except Exception as e:
        logger.warning(f"Provider {provider} unavailable for a model role: {e}")
    return None


def cloud_for(role: Optional[str], tenant_id: Optional[str] = None):
    """The cloud model a role uses, or None when it runs locally. Unset 'answer' keeps today's
    behaviour (the active cloud provider); other unset roles stay local."""
    rc = role_config(role, tenant_id) if role else None
    if rc:
        return provider_cfg(rc["provider"], rc.get("model"), tenant_id) if rc["engine"] == "cloud" else None
    return active_provider(tenant_id) if role in (None, "answer") else None


_LOCAL_MODELS: Dict[str, Any] = {"at": 0.0, "names": set()}


def _installed_local() -> set:
    if time.time() - _LOCAL_MODELS["at"] > 60:
        try:
            _LOCAL_MODELS["names"] = {m.get("model") or m.get("name") for m in ollama.list().get("models", [])}
        except Exception:
            _LOCAL_MODELS["names"] = set()
        _LOCAL_MODELS["at"] = time.time()
    return _LOCAL_MODELS["names"]


def local_model_for(role: Optional[str], default: Optional[str]) -> Optional[str]:
    """The local model a role uses; its default when unset or not installed."""
    rc = role_config(role) if role else None
    if rc and rc["engine"] == "local" and rc.get("model"):
        installed = _installed_local()
        if not installed or rc["model"] in installed:
            return rc["model"]
        logger.warning(f"Model role '{role}': {rc['model']} isn't installed — using {default}")
    return default


def policy(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    tid = _tid(tenant_id)
    hit = _CACHE.get(("policy", tid))
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    pol = dict(DEFAULT_POLICY)
    try:
        from sqlalchemy import text
        db = _db()
        try:
            row = db.execute(text("SELECT ask_before_send, mask_identifiers, blocked_types, upload_cloud, "
                                  "upload_types, upload_model, monthly_budget_usd, cascade_local_first FROM tenant_llm_policy "
                                  "WHERE tenant_id = :t"), {"t": tid}).fetchone()
        finally:
            db.close()
        if row:
            pol = {"ask_before_send": row.ask_before_send, "mask_identifiers": row.mask_identifiers,
                   "blocked_types": list(row.blocked_types or []), "upload_cloud": bool(row.upload_cloud),
                   "upload_types": list(row.upload_types or []), "upload_model": row.upload_model or None,
                   "monthly_budget_usd": float(row.monthly_budget_usd) if row.monthly_budget_usd is not None else None,
                   "cascade_local_first": row.cascade_local_first is not False}
    except Exception:
        pass                    # no policy table yet → safest defaults
    _CACHE[("policy", tid)] = (time.time(), pol)
    return pol


def _doc_types(filenames: List[str]) -> Dict[str, Optional[str]]:
    if not filenames:
        return {}
    from sqlalchemy import text
    db = _db()
    try:
        rows = db.execute(text("SELECT filename, doc_type FROM documents WHERE tenant_id = :t AND filename = ANY(:f)"),
                          {"t": _tid(), "f": list(filenames)}).fetchall()
    finally:
        db.close()
    return {r.filename: r.doc_type for r in rows}


# ── Identifier masking ───────────────────────────────────────────────────────

_MASK_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),                         # email
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                                # SSN
    re.compile(r"\bA-?\d{8,9}\b"),                                       # USCIS A-number
    re.compile(r"\b[A-Z]{3}\d{10}\b"),                                   # USCIS receipt number
    re.compile(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),                 # phone
    re.compile(r"(?<![\d.,$])\b\d{8,}\b(?![.,]\d)"),                       # account / customer / invoice no.
]


def _mask(messages: List[Dict]) -> Tuple[List[Dict], Dict[str, str]]:
    mapping: Dict[str, str] = {}
    reverse: Dict[str, str] = {}

    def sub(m: re.Match) -> str:
        val = m.group(0)
        if val not in reverse:
            ref = f"REF_{len(reverse) + 1:04d}"
            reverse[val], mapping[ref] = ref, val
        return reverse[val]

    out = []
    for msg in messages:
        txt = msg.get("content") or ""
        for pat in _MASK_PATTERNS:
            txt = pat.sub(sub, txt)
        out.append({**msg, "content": txt})
    return out, mapping


def _unmask(text: str, mapping: Dict[str, str]) -> str:
    for ref, val in mapping.items():
        text = text.replace(ref, val)
    return text


# ── Audit ────────────────────────────────────────────────────────────────────

_TABLES_READY = False


def _ensure_tables() -> None:
    """Old installs that haven't run V58 yet: the audit must never silently stop."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    from sqlalchemy import text
    db = _db()
    try:
        db.execute(text("""CREATE TABLE IF NOT EXISTS llm_cloud_calls (
            id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, username TEXT, provider TEXT NOT NULL,
            model TEXT NOT NULL, documents JSONB NOT NULL DEFAULT '[]'::jsonb, chars_sent INTEGER NOT NULL,
            masked_values INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER, output_tokens INTEGER,
            est_cost_usd NUMERIC(12,6), status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
        db.execute(text("""CREATE TABLE IF NOT EXISTS llm_cascade_outcomes (
            id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, username TEXT, question TEXT NOT NULL,
            documents JSONB NOT NULL DEFAULT '[]'::jsonb, local_answer TEXT, local_passed BOOLEAN, local_reason TEXT,
            escalated BOOLEAN NOT NULL DEFAULT FALSE, outcome TEXT NOT NULL, cloud_model TEXT, cloud_answer TEXT,
            cloud_verified BOOLEAN, disagreement JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
        db.commit()
        _TABLES_READY = True
    finally:
        db.close()


def _audit(cfg: Dict, documents: List[str], chars: int, masked: int,
           tokens_in: Optional[int], tokens_out: Optional[int], status: str) -> None:
    try:
        from sqlalchemy import text
        price = PRICES.get(cfg["model"])
        cost = round((tokens_in or 0) / 1e6 * price[0] + (tokens_out or 0) / 1e6 * price[1], 6) \
            if price and tokens_in is not None else None
        _ensure_tables()
        from app.core.audit import record as audit_record
        audit_record("cloud.model_call" if status == "ok" else "cloud.model_call_failed",
                     actor=_USER.get() or "system", detail=f"{cfg['provider']} · {cfg['model']}",
                     extra={"documents": documents, "chars_sent": chars, "masked_values": masked,
                            "input_tokens": tokens_in, "output_tokens": tokens_out, "est_cost_usd": cost})
        db = _db()
        try:
            db.execute(text("""INSERT INTO llm_cloud_calls
                (tenant_id, username, provider, model, documents, chars_sent, masked_values,
                 input_tokens, output_tokens, est_cost_usd, status)
                VALUES (:t, :u, :p, :m, CAST(:d AS jsonb), :c, :mv, :ti, :to, :cost, :s)"""),
                {"t": _tid(), "u": _USER.get(), "p": cfg["provider"], "m": cfg["model"],
                 "d": json.dumps(documents), "c": chars, "mv": masked, "ti": tokens_in, "to": tokens_out,
                 "cost": cost, "s": status})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Cloud call audit failed: {e}")


def month_spend(tenant_id: Optional[str] = None) -> float:
    """Estimated USD spent on cloud calls this calendar month (priced models only)."""
    try:
        from sqlalchemy import text
        _ensure_tables()
        db = _db()
        try:
            v = db.execute(text("SELECT coalesce(sum(est_cost_usd), 0) FROM llm_cloud_calls WHERE tenant_id = :t "
                                "AND created_at >= date_trunc('month', now())"), {"t": _tid(tenant_id)}).scalar()
        finally:
            db.close()
        return float(v or 0)
    except Exception:
        return 0.0


def _over_budget(pol: Dict) -> bool:
    cap = pol.get("monthly_budget_usd")
    return cap is not None and month_spend() >= cap


def log_cascade(question: str, documents: List[str], outcome: str, local_answer: Optional[str] = None,
                local_passed: Optional[bool] = None, local_reason: Optional[str] = None, escalated: bool = False,
                cloud_answer: Optional[str] = None, cloud_verified: Optional[bool] = None,
                disagreement: Optional[List[str]] = None) -> None:
    """Record one cascade decision. Never raises: logging must not break an answer."""
    try:
        from sqlalchemy import text
        _ensure_tables()
        route, label = _ROUTE.get()
        db = _db()
        try:
            db.execute(text("""INSERT INTO llm_cascade_outcomes (tenant_id, username, question, documents,
                    local_answer, local_passed, local_reason, escalated, outcome, cloud_model, cloud_answer,
                    cloud_verified, disagreement)
                VALUES (:t, :u, :q, CAST(:d AS jsonb), :la, :lp, :lr, :e, :o, :cm, :ca, :cv, CAST(:dis AS jsonb))"""),
                {"t": _tid(), "u": _USER.get(), "q": question[:2000], "d": json.dumps(list(documents)),
                 "la": local_answer, "lp": local_passed, "lr": local_reason, "e": escalated, "o": outcome,
                 "cm": label if route == "cloud" else None, "ca": cloud_answer, "cv": cloud_verified,
                 "dis": json.dumps(disagreement) if disagreement else None})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Cascade outcome log failed: {e}")


# ── Entry points ─────────────────────────────────────────────────────────────

def _local(model, messages, options, format, stream, kwargs):
    _ROUTE.set(("local", ""))
    call = dict(model=model, messages=messages, options=options or {}, stream=stream, **kwargs)
    if format:
        call["format"] = format
    return ollama.chat(**call)


def chat(model: Optional[str] = None, messages: Optional[List[Dict]] = None,
         options: Optional[Dict] = None, format: Optional[str] = None,
         stream: bool = False, cloud: bool = False, documents: Optional[List[str]] = None,
         upload: Optional[Tuple[str, str]] = None, role: Optional[str] = None, **kwargs):
    """cloud=True marks a call that may use the cloud provider; `documents` are the
    filenames whose text is in the prompt (checked against the never-send list).
    upload=(filename, doc_type) marks upload processing: it goes to the cloud only when
    the organisation turned that on for this document type (no per-call consent)."""
    if role:
        model = local_model_for(role, model)
        rc = role_config(role)
        if rc and rc["engine"] == "cloud":
            cloud = True                     # the user chose a cloud model for this step
    if upload:
        return _upload_chat(model, messages, options, format, kwargs, *upload)
    cfg = (cloud_for(role) if role else active_provider()) if cloud else None
    if not cfg or _DECISION.get() == "local":
        return _local(model, messages, options, format, stream, kwargs)

    docs = list(dict.fromkeys(documents or []))
    pol = policy()
    if _over_budget(pol):
        _note("Answered by the local model: this month's cloud budget has been reached.")
        return _local(model, messages, options, format, stream, kwargs)
    blocked_types = set(pol["blocked_types"])
    if docs and blocked_types:
        types = _doc_types(docs)
        blocked = [d for d in docs if types.get(d) in blocked_types]
        if blocked:
            _note(f"Answered by the local model: {', '.join(blocked)} is a document type your organisation "
                  f"never sends to cloud providers.")
            return _local(model, messages, options, format, stream, kwargs)
    if pol["ask_before_send"] and _DECISION.get() != "ok":
        raise CloudConsentRequired(cfg["provider"], cfg["model"], docs)

    msgs = list(messages or [])
    if format == "json":
        msgs = msgs + [{"role": "user", "content": "Reply with valid JSON only, no other text."}]
    mapping: Dict[str, str] = {}
    if pol["mask_identifiers"]:
        msgs, mapping = _mask(msgs)
    chars = sum(len(m.get("content") or "") for m in msgs)
    send = _anthropic if PROVIDERS.get(cfg["provider"], {}).get("kind") == "anthropic" else _openai
    logger.info(f"☁️ Cloud call: {cfg['provider']}/{cfg['model']} — {len(docs)} document(s), "
                f"{chars:,} chars, {len(mapping)} values masked")
    try:
        resp = send(cfg, msgs, options or {}, stream)
    except Exception:
        _audit(cfg, docs, chars, len(mapping), None, None, "error")
        raise
    _ROUTE.set(("cloud", f"{PROVIDERS.get(cfg['provider'], {}).get('label', cfg['provider'])} · {cfg['model']}"))
    if stream:
        return _unmask_stream(resp, mapping, lambda: _audit(cfg, docs, chars, len(mapping), None, None, "ok"))
    resp["message"]["content"] = _unmask(resp["message"]["content"], mapping)
    _audit(cfg, docs, chars, len(mapping), resp.get("prompt_eval_count"), resp.get("eval_count"), "ok")
    return resp


def _upload_chat(model, messages, options, format, kwargs, filename: str, doc_type: str):
    """Field extraction during upload. Runs in a background thread: the caller must have
    set the document's own tenant (ProcessingService does) so the right key and policy apply."""
    pol = policy()
    role_cloud = cloud_for("extraction") if (role_config("extraction") or {}).get("engine") == "cloud" else None
    cfg = role_cloud or active_provider()
    # A cloud Extraction role is the user's explicit choice; otherwise the upload policy decides.
    # Never-send document types always stay local.
    allowed = (cfg and (role_cloud or (pol["upload_cloud"] and doc_type in set(pol["upload_types"])))
               and doc_type not in set(pol["blocked_types"]))
    if not allowed:
        return _local(model, messages, options, format, False, kwargs)
    if _over_budget(pol):
        logger.info(f"Upload extraction for {filename} stays local: monthly cloud budget reached")
        return _local(model, messages, options, format, False, kwargs)
    if not role_cloud:
        cfg = {**cfg, "model": pol.get("upload_model") or cfg["model"]}
    msgs = list(messages or [])
    if format == "json":
        msgs = msgs + [{"role": "user", "content": "Reply with valid JSON only, no other text."}]
    mapping: Dict[str, str] = {}
    if pol["mask_identifiers"]:
        msgs, mapping = _mask(msgs)
    chars = sum(len(m.get("content") or "") for m in msgs)
    send = _anthropic if PROVIDERS.get(cfg["provider"], {}).get("kind") == "anthropic" else _openai
    logger.info(f"☁️ Upload extraction: {filename} ({doc_type}) → {cfg['provider']}/{cfg['model']}, "
                f"{chars:,} chars, {len(mapping)} values masked")
    prev_user = _USER.get()
    _USER.set("upload processing")
    try:
        try:
            resp = send(cfg, msgs, options or {}, False)
        except Exception as e:
            _audit(cfg, [filename], chars, len(mapping), None, None, "error")
            logger.warning(f"Cloud upload extraction failed for {filename}, using local model: {e}")
            return _local(model, messages, options, format, False, kwargs)
        resp["message"]["content"] = _unmask(resp["message"]["content"], mapping)
        _audit(cfg, [filename], chars, len(mapping), resp.get("prompt_eval_count"), resp.get("eval_count"), "ok")
        return resp
    finally:
        _USER.set(prev_user)


def _unmask_stream(chunks: Iterator[Dict], mapping: Dict[str, str], on_done) -> Iterator[Dict]:
    """Restore REF_0001… across token boundaries: hold back a short tail that could be
    the start of a placeholder until the next token arrives."""
    buf = ""
    for ch in chunks:
        buf += ch["message"]["content"]
        cut = len(buf)
        tail = buf.rfind("REF_")
        if tail != -1 and len(buf) - tail < 8:
            cut = tail
        else:
            for k in range(1, 4):           # partial "R", "RE", "REF" at the very end
                if "REF_".startswith(buf[-k:]):
                    cut = len(buf) - k
                    break
        if cut:
            yield {"message": {"role": "assistant", "content": _unmask(buf[:cut], mapping)}}
            buf = buf[cut:]
    if buf:
        yield {"message": {"role": "assistant", "content": _unmask(buf, mapping)}}
    on_done()


def generate(model: Optional[str] = None, prompt: str = "", options: Optional[Dict] = None,
             format: Optional[str] = None, **kwargs) -> Dict:
    """ollama.generate() shape: {"response": text}. Always local."""
    call = dict(model=model, prompt=prompt, options=options or {}, **kwargs)
    if format:
        call["format"] = format
    return ollama.generate(**call)


def test_connection(provider: str, api_key: str, model: str, base_url: Optional[str] = None) -> str:
    """One tiny request; returns the reply or raises with the provider's error."""
    cfg = {"provider": provider, "api_key": api_key, "model": model, "base_url": base_url}
    msgs = [{"role": "user", "content": "Reply with the single word: ok"}]
    resp = _anthropic(cfg, msgs, {}, False) if PROVIDERS[provider]["kind"] == "anthropic" \
        else _openai(cfg, msgs, {}, False)
    return resp["message"]["content"].strip()


def list_models(provider: str, api_key: str, base_url: Optional[str] = None) -> List[str]:
    """Model ids this key can use, straight from the provider (newest first where the
    provider orders them). Raises with the provider's error, e.g. for a bad key."""
    if PROVIDERS[provider]["kind"] == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        return [m.id for m in client.models.list()]
    from openai import OpenAI
    url = base_url or PROVIDERS[provider].get("base_url")
    client = OpenAI(api_key=api_key, base_url=url) if url else OpenAI(api_key=api_key)
    ids = [m.id for m in client.models.list()]
    if provider == "openai":
        # The account list includes embeddings, audio, image and moderation models
        skip = ("embedding", "whisper", "tts", "dall-e", "moderation", "davinci", "babbage", "audio", "realtime",
                "transcribe", "image", "search")
        ids = [i for i in ids if not any(k in i for k in skip)]
    if provider == "gemini":
        ids = [i.removeprefix("models/") for i in ids if "embedding" not in i and "aqa" not in i]
    return sorted(set(ids), reverse=True)


# ── Anthropic ────────────────────────────────────────────────────────────────

def _split_system(messages: List[Dict]):
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    rest = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") != "system"]
    return system, rest


def _anthropic(cfg: Dict, messages: List[Dict], opts: Dict, stream: bool):
    import anthropic
    client = anthropic.Anthropic(api_key=cfg["api_key"])
    system, msgs = _split_system(messages)
    # Thinking is always on for current Claude models and counts against max_tokens;
    # the local num_predict (often 50-800) would leave no room for the answer.
    params: Dict[str, Any] = {"model": cfg["model"], "messages": msgs,
                              "max_tokens": 64000 if stream else 16000}
    if system:
        params["system"] = system
    if stream:
        return _anthropic_stream(client, params)
    resp = client.messages.create(**params)
    text = "".join(b.text for b in resp.content if b.type == "text")
    if resp.stop_reason == "refusal":
        text = text or "The cloud model declined to answer this request."
    return {"message": {"role": "assistant", "content": text},
            "prompt_eval_count": resp.usage.input_tokens, "eval_count": resp.usage.output_tokens,
            "done_reason": resp.stop_reason}


def _anthropic_stream(client, params) -> Iterator[Dict]:
    with client.messages.stream(**params) as s:
        for text in s.text_stream:
            yield {"message": {"role": "assistant", "content": text}}


# ── OpenAI and OpenAI-compatible providers ───────────────────────────────────

def _openai(cfg: Dict, messages: List[Dict], opts: Dict, stream: bool):
    from openai import OpenAI
    base_url = cfg.get("base_url") or PROVIDERS.get(cfg["provider"], {}).get("base_url")
    client = OpenAI(api_key=cfg["api_key"], base_url=base_url) if base_url else OpenAI(api_key=cfg["api_key"])
    params: Dict[str, Any] = {"model": cfg["model"], "messages": messages}
    if stream:
        return _openai_stream(client, params)
    resp = client.chat.completions.create(**params)
    choice = resp.choices[0]
    return {"message": {"role": "assistant", "content": choice.message.content or ""},
            "prompt_eval_count": getattr(resp.usage, "prompt_tokens", None),
            "eval_count": getattr(resp.usage, "completion_tokens", None),
            "done_reason": choice.finish_reason}


def _openai_stream(client, params) -> Iterator[Dict]:
    for chunk in client.chat.completions.create(stream=True, **params):
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield {"message": {"role": "assistant", "content": chunk.choices[0].delta.content}}
