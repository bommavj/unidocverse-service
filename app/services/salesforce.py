"""
Salesforce connector: documents in UniDocVerse → the client's Account in Salesforce.

Flow
  1. Admin saves their Salesforce connected app (consumer key/secret, production |
     sandbox | a My Domain URL) and connects with OAuth 2.0 (web server flow, scopes
     api + refresh_token). Tokens are stored encrypted and refreshed automatically.
  2. prepare(document) builds a draft: which Account (the client named in the document's
     extracted facts), a summary and the key facts, plus checks (the client name must
     appear in the document's own text; not sent already).
  3. Someone approves the draft → send(): find or create the Account, upload the original
     file to it (ContentVersion) and log a completed Task with the key facts. Recorded in
     sf_pushes, the audit trail and a webhook.

Nothing is written to Salesforce without an explicit approval.
"""
import base64
import json
import logging
import secrets
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import text

from app.core.secret_box import decrypt, encrypt

logger = logging.getLogger(__name__)

LOGIN_HOSTS = {"production": "https://login.salesforce.com", "sandbox": "https://test.salesforce.com"}
SCOPE = "api refresh_token"
API_VERSION = "v61.0"

# Where the client's name is found in extracted facts, by document type family
CLIENT_KEYS = ("client_name", "insured_name", "named_insured", "policyholder", "customer_name", "client",
               "beneficiary_name", "employee_name", "patient_name", "buyer", "account_name", "company_name", "vendor")
_FACT_SKIP = {"line_items", "raw", "tables", "text"}


class SFError(Exception):
    pass


def ensure_tables(db) -> None:
    db.execute(text("""CREATE TABLE IF NOT EXISTS sf_connections (
        tenant_id TEXT PRIMARY KEY, login_host TEXT NOT NULL DEFAULT 'https://login.salesforce.com',
        client_id TEXT NOT NULL, client_secret_enc TEXT NOT NULL, instance_url TEXT, org_name TEXT,
        user_name TEXT, access_token_enc TEXT, refresh_token_enc TEXT, oauth_state TEXT,
        oauth_state_expires TIMESTAMPTZ, connected_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
    db.execute(text("""CREATE TABLE IF NOT EXISTS sf_pushes (
        id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, document_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft', draft JSONB NOT NULL, checks JSONB NOT NULL DEFAULT '[]'::jsonb,
        sf_account_id TEXT, sf_content_id TEXT, sf_task_id TEXT, error TEXT, prepared_by TEXT, approved_by TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ)"""))
    db.commit()


def redirect_uri() -> str:
    import os
    return os.getenv("SF_REDIRECT_URI", "http://localhost:7979/api/salesforce/callback")


def login_host(env_or_url: str) -> str:
    """production | sandbox | a My Domain URL such as https://acme.my.salesforce.com"""
    v = (env_or_url or "production").strip().rstrip("/")
    if v in LOGIN_HOSTS:
        return LOGIN_HOSTS[v]
    if not v.startswith("https://") or not (v.endswith(".salesforce.com") or v.endswith(".force.com")):
        raise SFError("Use production, sandbox or your https://….my.salesforce.com address")
    return v


# ── Connection & tokens ──────────────────────────────────────────────────────

def connection(db, tid: str):
    return db.execute(text("SELECT * FROM sf_connections WHERE tenant_id = :t"), {"t": tid}).mappings().fetchone()


def authorize_url(db, tid: str) -> str:
    conn = connection(db, tid)
    if not conn:
        raise SFError("Save your connected app's consumer key and secret first")
    state = secrets.token_urlsafe(24)
    db.execute(text("""UPDATE sf_connections SET oauth_state = :s, oauth_state_expires = now() + interval '15 minutes'
        WHERE tenant_id = :t"""), {"s": state, "t": tid})
    db.commit()
    return f"{conn['login_host']}/services/oauth2/authorize?" + urlencode(
        {"client_id": conn["client_id"], "response_type": "code", "scope": SCOPE,
         "redirect_uri": redirect_uri(), "state": state, "prompt": "login consent"})


def _token_request(conn, data: Dict[str, str]) -> Dict:
    r = httpx.post(f"{conn['login_host']}/services/oauth2/token", timeout=20, headers={"Accept": "application/json"},
                   data={**data, "client_id": conn["client_id"], "client_secret": decrypt(conn["client_secret_enc"])})
    if r.status_code != 200:
        raise SFError(f"Salesforce token request failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def complete_oauth(db, state: str, code: str) -> str:
    row = db.execute(text("""SELECT tenant_id FROM sf_connections WHERE oauth_state = :s
        AND oauth_state_expires > now()"""), {"s": state}).fetchone()
    if not row:
        raise SFError("This Salesforce sign-in link expired. Start again from Integrations.")
    tid = row.tenant_id
    tok = _token_request(connection(db, tid), {"grant_type": "authorization_code", "code": code,
                                                "redirect_uri": redirect_uri()})
    if not tok.get("refresh_token"):
        raise SFError("Salesforce didn't return a refresh token — add the 'refresh_token, offline_access' scope "
                      "to the connected app")
    db.execute(text("""UPDATE sf_connections SET access_token_enc = :a, refresh_token_enc = :r, instance_url = :i,
        connected_at = now(), oauth_state = NULL, updated_at = now() WHERE tenant_id = :t"""),
        {"a": encrypt(tok["access_token"]), "r": encrypt(tok["refresh_token"]), "i": tok["instance_url"], "t": tid})
    db.commit()
    try:
        me = httpx.get(tok["id"], headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=15).json()
        org = api(db, tid, "GET", f"/sobjects/Organization/{me.get('organization_id')}", params={"fields": "Name"})
        db.execute(text("UPDATE sf_connections SET org_name = :o, user_name = :u WHERE tenant_id = :t"),
                   {"o": org.get("Name"), "u": me.get("username"), "t": tid})
        db.commit()
    except Exception as e:
        logger.warning(f"Salesforce org lookup failed: {e}")
    return tid


def _refresh(db, tid: str, conn) -> str:
    tok = _token_request(conn, {"grant_type": "refresh_token", "refresh_token": decrypt(conn["refresh_token_enc"])})
    db.execute(text("""UPDATE sf_connections SET access_token_enc = :a, instance_url = coalesce(:i, instance_url),
        updated_at = now() WHERE tenant_id = :t"""), {"a": encrypt(tok["access_token"]), "i": tok.get("instance_url"), "t": tid})
    db.commit()
    return tok["access_token"]


def api(db, tid: str, method: str, path: str, *, json_body: Optional[Dict] = None, params: Optional[Dict] = None) -> Dict:
    """REST call under /services/data/<version>. Salesforce access tokens don't report an expiry,
    so a 401 triggers one refresh and a retry."""
    conn = connection(db, tid)
    if not conn or not conn["refresh_token_enc"] or not conn["instance_url"]:
        raise SFError("Salesforce isn't connected")
    token = decrypt(conn["access_token_enc"]) if conn["access_token_enc"] else _refresh(db, tid, conn)
    for attempt in (1, 2):
        r = httpx.request(method, f"{conn['instance_url']}/services/data/{API_VERSION}{path}", params=params,
                          json=json_body, timeout=60, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if r.status_code == 401 and attempt == 1:
            token = _refresh(db, tid, connection(db, tid))
            continue
        break
    if r.status_code >= 400:
        detail = r.text[:400]
        try:
            detail = "; ".join(f"{e.get('errorCode')}: {e.get('message')}" for e in r.json()) or detail
        except Exception:
            pass
        raise SFError(f"Salesforce {r.status_code}: {detail}")
    return r.json() if r.content else {}


def soql(db, tid: str, q: str) -> list:
    return api(db, tid, "GET", "/query", params={"q": q}).get("records", [])


def disconnect(db, tid: str) -> None:
    conn = connection(db, tid)
    if conn and conn["refresh_token_enc"]:
        try:
            httpx.post(f"{conn['login_host']}/services/oauth2/revoke",
                       data={"token": decrypt(conn["refresh_token_enc"])}, timeout=15)
        except Exception as e:
            logger.warning(f"Salesforce revoke failed: {e}")
    db.execute(text("""UPDATE sf_connections SET instance_url = NULL, org_name = NULL, user_name = NULL,
        access_token_enc = NULL, refresh_token_enc = NULL, connected_at = NULL, updated_at = now()
        WHERE tenant_id = :t"""), {"t": tid})
    db.commit()


# ── Draft → approval → send ──────────────────────────────────────────────────

def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace("'", "\\'")


def _facts_text(f: Dict[str, Any]) -> str:
    lines = []
    for k, v in f.items():
        if k in _FACT_SKIP or v in (None, "", [], {}, "null"):
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, default=str)[:300]
        lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")
    return "\n".join(lines)[:30000]


def prepare(db, tid: str, document_id: str, user: str) -> Dict:
    doc = db.execute(text("""SELECT id, filename, doc_type, summary, raw_text, extracted_facts, created_at FROM documents
        WHERE id::text = :d AND tenant_id::text = :t"""), {"d": document_id, "t": tid}).mappings().fetchone()
    if not doc:
        raise SFError("Document not found")
    f = dict(doc["extracted_facts"] or {})
    if not f:                                   # immigration notices keep their details in the visa tracker
        try:
            from types import SimpleNamespace
            from app.services.immigration_tracker import IMMIGRATION_DOC_TYPES, from_documents
            if doc["doc_type"] in IMMIGRATION_DOC_TYPES:
                rec = (from_documents([SimpleNamespace(**dict(doc))]) or [{}])[0]
                f = {k: v for k, v in rec.items() if k in ("beneficiary_name", "doc_label", "visa_class", "employer_sponsor",
                     "receipt_number", "expiry_date", "valid_from", "days_to_expiry") and v not in (None, "")}
        except Exception as e:
            logger.warning(f"Immigration facts for Salesforce failed: {e}")
    raw = (doc["raw_text"] or "").lower()
    client = next((str(f[k]).strip() for k in CLIENT_KEYS if isinstance(f.get(k), str) and f[k].strip()
                   and f[k].strip().lower() != "null"), "")
    draft = {"account_name": client, "subject": f"{(doc['doc_type'] or 'Document').replace('_', ' ').title()}: {doc['filename']}",
             "summary": (doc["summary"] or "")[:2000], "facts": _facts_text(f), "filename": doc["filename"],
             "doc_type": doc["doc_type"]}
    checks = [
        {"check": "Client named in the document", "ok": bool(client)},
        {"check": "Client name appears in the document text",
         "ok": bool(client) and (client.lower() in raw or ", ".join(reversed(client.lower().rsplit(" ", 1))) in raw)},
        {"check": "Key facts extracted", "ok": bool(draft["facts"])},
        {"check": "Not already sent",
         "ok": not db.execute(text("SELECT 1 FROM sf_pushes WHERE tenant_id = :t AND document_id = :d AND status = 'sent'"),
                              {"t": tid, "d": document_id}).fetchone()},
    ]
    pid = db.execute(text("""INSERT INTO sf_pushes (tenant_id, document_id, draft, checks, prepared_by)
        VALUES (:t, :d, CAST(:dr AS jsonb), CAST(:c AS jsonb), :u) RETURNING id"""),
        {"t": tid, "d": document_id, "dr": json.dumps(draft), "c": json.dumps(checks), "u": user}).scalar()
    db.commit()
    return {"id": pid, "draft": draft, "checks": checks}


def _account_id(db, tid: str, name: str) -> str:
    found = soql(db, tid, f"SELECT Id FROM Account WHERE Name = '{_esc(name)}' ORDER BY CreatedDate LIMIT 1")
    if found:
        return found[0]["Id"]
    return api(db, tid, "POST", "/sobjects/Account", json_body={"Name": name[:255]})["id"]


def send(db, tid: str, push_id: int, user: str, edits: Optional[Dict] = None) -> Dict:
    """Approve a draft (optionally choosing a different Account name) and write it to Salesforce."""
    from app.core.audit import record as audit
    from app.services import webhooks
    row = db.execute(text("SELECT * FROM sf_pushes WHERE id = :id AND tenant_id = :t"),
                     {"id": push_id, "t": tid}).mappings().fetchone()
    if not row:
        raise SFError("Draft not found")
    if row["status"] == "sent":
        raise SFError("Already sent")
    draft = {**row["draft"], **{k: v for k, v in (edits or {}).items() if k in ("account_name", "subject") and v}}
    if not draft.get("account_name"):
        raise SFError("Choose which Salesforce Account this belongs to")
    try:
        account_id = _account_id(db, tid, draft["account_name"])
        content_id = None
        doc = db.execute(text("SELECT file_path FROM documents WHERE id::text = :d"), {"d": row["document_id"]}).fetchone()
        if doc and doc.file_path and Path(doc.file_path).exists():
            data = Path(doc.file_path).read_bytes()
            if len(data) <= 35 * 1024 * 1024:          # REST base64 upload limit is ~37.5 MB
                content_id = api(db, tid, "POST", "/sobjects/ContentVersion", json_body={
                    "Title": draft["filename"][:255], "PathOnClient": draft["filename"][:255],
                    "VersionData": base64.b64encode(data).decode(), "FirstPublishLocationId": account_id})["id"]
        task_id = api(db, tid, "POST", "/sobjects/Task", json_body={
            "WhatId": account_id, "Subject": draft["subject"][:255], "Status": "Completed", "Priority": "Normal",
            "Description": "\n\n".join(x for x in [draft.get("summary"), draft.get("facts"),
                                                   f"Sent from UniDocVerse by {user}."] if x)[:32000]})["id"]
        db.execute(text("""UPDATE sf_pushes SET status='sent', draft=CAST(:dr AS jsonb), sf_account_id=:a,
            sf_content_id=:c, sf_task_id=:k, approved_by=:u, sent_at=now(), error=NULL WHERE id=:id"""),
            {"dr": json.dumps(draft), "a": account_id, "c": content_id, "k": task_id, "u": user, "id": push_id})
        db.commit()
        audit("salesforce.sent", actor=user, doc_id=row["document_id"], detail=f"Account {account_id} · {draft['account_name']}",
              extra={"account_id": account_id, "content_version_id": content_id, "task_id": task_id}, tenant_id=tid)
        webhooks.emit("salesforce.record_created", {"document_id": row["document_id"], "account_id": account_id,
                      "account_name": draft["account_name"], "task_id": task_id}, tid)
        from app.api.integration_sends_router import notify
        notify(tid, True, "Salesforce", draft["filename"], f"Account {draft['account_name']}")
        conn = connection(db, tid)
        return {"account_id": account_id, "content_version_id": content_id, "task_id": task_id,
                "account_url": f"{conn['instance_url']}/lightning/r/Account/{account_id}/view"}
    except Exception as e:
        db.rollback()
        db.execute(text("UPDATE sf_pushes SET status='failed', error=:e, approved_by=:u WHERE id=:id"),
                   {"e": str(e)[:1000], "u": user, "id": push_id})
        db.commit()
        audit("salesforce.failed", actor=user, doc_id=row["document_id"], detail=str(e)[:300], tenant_id=tid)
        webhooks.emit("salesforce.failed", {"document_id": row["document_id"], "error": str(e)[:300]}, tid)
        from app.api.integration_sends_router import notify
        notify(tid, False, "Salesforce", draft.get("filename"), "", str(e)[:300])
        raise SFError(str(e))
