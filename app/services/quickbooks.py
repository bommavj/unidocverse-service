"""
QuickBooks Online connector: vendor invoices in UniDocVerse → Bills in QuickBooks.

Flow
  1. Admin saves their Intuit app (client id/secret, sandbox|production) and connects
     with OAuth 2.0; tokens are stored encrypted and refreshed automatically.
  2. prepare(document) builds a draft Bill from the document's extracted facts and runs
     checks (the total and invoice number must appear in the document's own text).
  3. Someone approves the draft → post(): find or create the vendor, create the Bill,
     attach the original file. Every step is recorded in qb_pushes and announced by webhook.

Nothing is posted without an explicit approval.
"""
import json
import logging
import mimetypes
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from sqlalchemy import text

from app.core.secret_box import decrypt, encrypt

logger = logging.getLogger(__name__)

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
API_BASE = {"sandbox": "https://sandbox-quickbooks.api.intuit.com",
            "production": "https://quickbooks.api.intuit.com"}
SCOPE = "com.intuit.quickbooks.accounting"
MINOR_VERSION = "75"


class QBError(Exception):
    pass


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def ensure_tables(db) -> None:
    db.execute(text("""CREATE TABLE IF NOT EXISTS qb_connections (
        tenant_id TEXT PRIMARY KEY, environment TEXT NOT NULL DEFAULT 'sandbox', client_id TEXT NOT NULL,
        client_secret_enc TEXT NOT NULL, realm_id TEXT, company_name TEXT, access_token_enc TEXT,
        refresh_token_enc TEXT, access_expires_at TIMESTAMPTZ, expense_account_id TEXT, oauth_state TEXT,
        oauth_state_expires TIMESTAMPTZ, connected_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
    db.execute(text("""CREATE TABLE IF NOT EXISTS qb_pushes (
        id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, document_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft', draft JSONB NOT NULL, checks JSONB NOT NULL DEFAULT '[]'::jsonb,
        qb_bill_id TEXT, qb_vendor_id TEXT, error TEXT, prepared_by TEXT, approved_by TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), posted_at TIMESTAMPTZ)"""))
    db.commit()


def redirect_uri() -> str:
    import os
    return os.getenv("QB_REDIRECT_URI", "http://localhost:7979/api/quickbooks/callback")


# ── Connection & tokens ──────────────────────────────────────────────────────

def connection(db, tid: str):
    return db.execute(text("SELECT * FROM qb_connections WHERE tenant_id = :t"), {"t": tid}).mappings().fetchone()


def authorize_url(db, tid: str) -> str:
    conn = connection(db, tid)
    if not conn:
        raise QBError("Save your Intuit app's client ID and secret first")
    state = secrets.token_urlsafe(24)
    db.execute(text("""UPDATE qb_connections SET oauth_state = :s, oauth_state_expires = now() + interval '15 minutes'
        WHERE tenant_id = :t"""), {"s": state, "t": tid})
    db.commit()
    return AUTH_URL + "?" + urlencode({"client_id": conn["client_id"], "response_type": "code", "scope": SCOPE,
                                       "redirect_uri": redirect_uri(), "state": state})


def _token_request(conn, data: Dict[str, str]) -> Dict:
    r = httpx.post(TOKEN_URL, data=data, timeout=20, headers={"Accept": "application/json"},
                   auth=(conn["client_id"], decrypt(conn["client_secret_enc"])))
    if r.status_code != 200:
        raise QBError(f"Intuit token request failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def _store_tokens(db, tid: str, tok: Dict, realm_id: Optional[str] = None) -> None:
    db.execute(text("""UPDATE qb_connections SET access_token_enc = :a, refresh_token_enc = :r,
        access_expires_at = now() + make_interval(secs => :exp), realm_id = coalesce(:realm, realm_id),
        connected_at = coalesce(connected_at, now()), oauth_state = NULL, updated_at = now() WHERE tenant_id = :t"""),
        {"a": encrypt(tok["access_token"]), "r": encrypt(tok["refresh_token"]),
         "exp": int(tok.get("expires_in", 3600)) - 120, "realm": realm_id, "t": tid})
    db.commit()


def complete_oauth(db, state: str, code: str, realm_id: str) -> str:
    """OAuth callback: the state identifies the tenant (the browser has no app session here)."""
    row = db.execute(text("""SELECT tenant_id FROM qb_connections WHERE oauth_state = :s
        AND oauth_state_expires > now()"""), {"s": state}).fetchone()
    if not row:
        raise QBError("This QuickBooks sign-in link expired. Start again from Integrations.")
    tid = row.tenant_id
    conn = connection(db, tid)
    tok = _token_request(conn, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()})
    _store_tokens(db, tid, tok, realm_id)
    try:
        info = api(db, tid, "GET", f"/companyinfo/{realm_id}")
        db.execute(text("UPDATE qb_connections SET company_name = :n WHERE tenant_id = :t"),
                   {"n": info.get("CompanyInfo", {}).get("CompanyName"), "t": tid})
        db.commit()
    except Exception as e:
        logger.warning(f"QuickBooks company lookup failed: {e}")
    return tid


def _access_token(db, tid: str) -> Tuple[str, Any]:
    conn = connection(db, tid)
    if not conn or not conn["refresh_token_enc"] or not conn["realm_id"]:
        raise QBError("QuickBooks isn't connected")
    if conn["access_expires_at"] and conn["access_expires_at"] > datetime.now(timezone.utc):
        return decrypt(conn["access_token_enc"]), conn
    tok = _token_request(conn, {"grant_type": "refresh_token", "refresh_token": decrypt(conn["refresh_token_enc"])})
    _store_tokens(db, tid, tok)
    return tok["access_token"], connection(db, tid)


def api(db, tid: str, method: str, path: str, *, json_body: Optional[Dict] = None,
        params: Optional[Dict] = None, files=None) -> Dict:
    token, conn = _access_token(db, tid)
    url = f"{API_BASE[conn['environment']]}/v3/company/{conn['realm_id']}{path}"
    r = httpx.request(method, url, params={"minorversion": MINOR_VERSION, **(params or {})},
                      json=json_body, files=files, timeout=30,
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    if r.status_code >= 400:
        detail = r.text[:400]
        try:
            errs = r.json().get("Fault", {}).get("Error", [])
            detail = "; ".join(f"{e.get('Message')}: {e.get('Detail')}" for e in errs) or detail
        except Exception:
            pass
        raise QBError(f"QuickBooks {r.status_code}: {detail}")
    return r.json() if r.content else {}


def query(db, tid: str, q: str) -> Dict:
    return api(db, tid, "GET", "/query", params={"query": q}).get("QueryResponse", {})


def disconnect(db, tid: str) -> None:
    conn = connection(db, tid)
    if conn and conn["refresh_token_enc"]:
        try:
            httpx.post(REVOKE_URL, json={"token": decrypt(conn["refresh_token_enc"])}, timeout=15,
                       auth=(conn["client_id"], decrypt(conn["client_secret_enc"])),
                       headers={"Accept": "application/json"})
        except Exception as e:
            logger.warning(f"QuickBooks revoke failed: {e}")
    db.execute(text("""UPDATE qb_connections SET realm_id = NULL, company_name = NULL, access_token_enc = NULL,
        refresh_token_enc = NULL, access_expires_at = NULL, connected_at = NULL, updated_at = now()
        WHERE tenant_id = :t"""), {"t": tid})
    db.commit()


def expense_accounts(db, tid: str) -> List[Dict]:
    res = query(db, tid, "select Id, Name, AccountType from Account where Classification = 'Expense' "
                         "and Active = true maxresults 200")
    return [{"id": a["Id"], "name": a["Name"], "type": a.get("AccountType")} for a in res.get("Account", [])]


# ── Draft → approval → post ──────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    try:
        return round(float(str(v).replace(",", "").replace("$", "")), 2)
    except (TypeError, ValueError):
        return None


def _in_text(value: Any, raw: str) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, (int, float)):
        plain = re.sub(r"(?<=\d),(?=\d)", "", raw)
        return re.search(r"(?<![\d.])" + re.escape(f"{value:.2f}") + r"(?!\d)", plain) is not None
    return str(value).strip().lower() in raw.lower()


def prepare(db, tid: str, document_id: str, user: str) -> Dict:
    doc = db.execute(text("""SELECT id, filename, doc_type, raw_text, extracted_facts FROM documents
        WHERE id = :d AND tenant_id = :t"""), {"d": document_id, "t": tid}).mappings().fetchone()
    if not doc:
        raise QBError("Document not found")
    f = doc["extracted_facts"] or {}
    if not f:
        raise QBError("No extracted fields for this document yet")
    raw = doc["raw_text"] or ""
    total = _num(f.get("total_amount") or f.get("total") or f.get("amount_due"))
    lines = []
    for li in f.get("line_items") or []:
        amt = _num(li.get("total"))
        if amt is not None and li.get("description"):
            lines.append({"description": str(li["description"])[:4000], "amount": amt})
    draft = {"vendor": (f.get("vendor") or "").strip(), "total": total,
             "bill_date": f.get("invoice_date"), "due_date": f.get("due_date"),
             "doc_number": (str(f.get("invoice_number")) if f.get("invoice_number") else None),
             "lines": lines, "filename": doc["filename"]}
    checks = [
        {"check": "Vendor found", "ok": bool(draft["vendor"])},
        {"check": "Total found in the document text", "ok": total is not None and _in_text(total, raw)},
        {"check": "Invoice number found in the document text",
         "ok": bool(draft["doc_number"]) and _in_text(draft["doc_number"], raw)},
        {"check": "Line items add up to the total",
         "ok": bool(lines) and total is not None and abs(sum(l["amount"] for l in lines) - total) < 0.02},
        {"check": "Not already posted",
         "ok": not db.execute(text("SELECT 1 FROM qb_pushes WHERE tenant_id = :t AND document_id = :d "
                                   "AND status = 'posted'"), {"t": tid, "d": document_id}).fetchone()},
    ]
    pid = db.execute(text("""INSERT INTO qb_pushes (tenant_id, document_id, draft, checks, prepared_by)
        VALUES (:t, :d, CAST(:dr AS jsonb), CAST(:c AS jsonb), :u) RETURNING id"""),
        {"t": tid, "d": document_id, "dr": json.dumps(draft), "c": json.dumps(checks), "u": user}).scalar()
    db.commit()
    return {"id": pid, "draft": draft, "checks": checks}


def _vendor_id(db, tid: str, name: str) -> str:
    safe = name.replace("'", "\\'")
    found = query(db, tid, f"select Id, DisplayName from Vendor where DisplayName = '{safe}'").get("Vendor", [])
    if found:
        return found[0]["Id"]
    created = api(db, tid, "POST", "/vendor", json_body={"DisplayName": name[:100]})
    return created["Vendor"]["Id"]


def post(db, tid: str, push_id: int, user: str, edits: Optional[Dict] = None) -> Dict:
    """Approve a draft (optionally with edits) and create the Bill in QuickBooks."""
    from app.services import webhooks
    row = db.execute(text("SELECT * FROM qb_pushes WHERE id = :id AND tenant_id = :t"),
                     {"id": push_id, "t": tid}).mappings().fetchone()
    if not row:
        raise QBError("Draft not found")
    if row["status"] == "posted":
        raise QBError("Already posted")
    draft = {**row["draft"], **{k: v for k, v in (edits or {}).items() if k in
                                ("vendor", "total", "bill_date", "due_date", "doc_number")}}
    if not draft.get("vendor") or draft.get("total") in (None, ""):
        raise QBError("A vendor and a total are required")
    conn = connection(db, tid)
    account = conn["expense_account_id"] if conn else None
    if not account:
        raise QBError("Choose the expense account for bills in Integrations → QuickBooks")
    total = float(draft["total"])
    lines = draft.get("lines") or []
    if edits and "total" in edits or not lines or abs(sum(l["amount"] for l in lines) - total) >= 0.02:
        lines = [{"description": f"Invoice {draft.get('doc_number') or ''} — {draft['filename']}".strip(), "amount": total}]
    try:
        vendor_id = _vendor_id(db, tid, draft["vendor"])
        bill = {"VendorRef": {"value": vendor_id},
                "Line": [{"Amount": l["amount"], "Description": l["description"],
                          "DetailType": "AccountBasedExpenseLineDetail",
                          "AccountBasedExpenseLineDetail": {"AccountRef": {"value": account}}} for l in lines]}
        if draft.get("bill_date"):
            bill["TxnDate"] = draft["bill_date"]
        if draft.get("due_date"):
            bill["DueDate"] = draft["due_date"]
        if draft.get("doc_number"):
            bill["DocNumber"] = str(draft["doc_number"])[:21]
        created = api(db, tid, "POST", "/bill", json_body=bill)["Bill"]
        _attach(db, tid, row["document_id"], created["Id"])
        db.execute(text("""UPDATE qb_pushes SET status='posted', draft=CAST(:dr AS jsonb), qb_bill_id=:b,
            qb_vendor_id=:v, approved_by=:u, posted_at=now(), error=NULL WHERE id=:id"""),
            {"dr": json.dumps(draft), "b": created["Id"], "v": vendor_id, "u": user, "id": push_id})
        db.commit()
        from app.core.audit import record as audit
        audit("quickbooks.bill_posted", actor=user, doc_id=row["document_id"],
              detail=f"Bill {created['Id']} · {draft['vendor']} · {total:.2f}",
              extra={"bill_id": created["Id"], "vendor_id": vendor_id, "draft": draft}, tenant_id=tid)
        webhooks.emit("quickbooks.bill_created", {"document_id": row["document_id"], "bill_id": created["Id"],
                      "vendor": draft["vendor"], "total": total, "due_date": draft.get("due_date")}, tid)
        from app.api.integration_sends_router import notify
        notify(tid, True, "QuickBooks", draft.get("filename"), f"Bill {created['Id']} · {draft['vendor']} · {total:.2f}")
        return {"bill_id": created["Id"], "vendor_id": vendor_id}
    except Exception as e:
        db.rollback()
        db.execute(text("UPDATE qb_pushes SET status='failed', error=:e, approved_by=:u WHERE id=:id"),
                   {"e": str(e)[:1000], "u": user, "id": push_id})
        db.commit()
        from app.core.audit import record as audit
        audit("quickbooks.bill_failed", actor=user, doc_id=row["document_id"], detail=str(e)[:300], tenant_id=tid)
        webhooks.emit("quickbooks.bill_failed", {"document_id": row["document_id"], "error": str(e)[:300]}, tid)
        from app.api.integration_sends_router import notify
        notify(tid, False, "QuickBooks", draft.get("filename"), "", str(e)[:300])
        raise QBError(str(e))


def _attach(db, tid: str, document_id: str, bill_id: str) -> None:
    """Attach the original file to the bill. A failed attachment doesn't undo the bill."""
    doc = db.execute(text("SELECT filename, file_path FROM documents WHERE id = :d"), {"d": document_id}).fetchone()
    if not doc or not doc.file_path or not Path(doc.file_path).exists():
        return
    mime = mimetypes.guess_type(doc.filename)[0] or "application/octet-stream"
    meta = {"AttachableRef": [{"EntityRef": {"type": "Bill", "value": bill_id}}],
            "FileName": doc.filename, "ContentType": mime}
    try:
        with open(doc.file_path, "rb") as fh:
            api(db, tid, "POST", "/upload", files={
                "file_metadata_01": (None, json.dumps(meta), "application/json"),
                "file_content_01": (doc.filename, fh.read(), mime)})
    except Exception as e:
        logger.warning(f"QuickBooks attachment for bill {bill_id} failed: {e}")
