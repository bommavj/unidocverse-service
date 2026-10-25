"""
immigration_workflow_service.py
===============================
Post-processing hook for USCIS notices (I-797, I-94) and passports.

Unlike the insurance hook, a new client is never created silently: the person named on the
document (beneficiary / applicant / holder) is matched against existing clients, and when there
is no match an approval request "Create client <name>" is raised. Approving it creates the
client (source 'document_ai') and links every document that was waiting on that request.
"""

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id

logger = logging.getLogger(__name__)

_NAME_FIELDS = ("beneficiary", "applicant", "applicant_name", "holder", "holder_name",
                "full_name", "name", "traveler", "traveler_name", "student_name",
                "exchange_visitor", "card_holder", "naturalized_person")


_NOT_NAMES = {"visa", "passport", "uscis", "notice", "approval", "receipt", "applicant", "beneficiary",
              "petitioner", "name", "united", "states", "of", "america", "department", "homeland", "security"}


def _clean_name(v: str) -> str:
    """A person's name or '' — table cells, dates and form labels are rejected, not guessed around."""
    if "|" in (v or ""):                       # spilled across table cells: partial names are worse than none
        return ""
    v = re.split(r"\n|\s{3,}", v or "")[0]
    v = re.sub(r"\s+", " ", v).strip(" ,.;:")
    if not v or re.search(r"\d", v) or len(v) > 60:
        return ""
    words = [w for w in re.split(r"[ ,]+", v) if w]
    real = [w for w in words if len(w.strip(".")) > 1]
    if len(real) < 2 or any(w.lower() in _NOT_NAMES for w in real) or not all(re.fullmatch(r"[A-Za-z'.\-]+", w) for w in words):
        return ""
    return v


def _person_name(fields: Dict[str, Any]) -> str:
    for k in _NAME_FIELDS:
        v = fields.get(k)
        if isinstance(v, str) and _clean_name(v):
            return _clean_name(v)
    given, surname = fields.get("given_names") or fields.get("given_name"), fields.get("surname")
    if given and surname:
        return _clean_name(f"{surname}, {given}")
    return ""


_MRZ = re.compile(r"P[<A-Z][A-Z<]{3}([A-Z<]+?)<<([A-Z<]+)")
_LABELLED = re.compile(r"Surname[s]?\s*[:\-]?\s*([A-Z][A-Za-z'\- ]+?)\s*(?:\n|\|)\s*Given\s+Names?\s*[:\-]?\s*([A-Z][A-Za-z'\- ]+)",
                       re.I)
_FILENAME = re.compile(r"(?:I-?\d{2,3}[A-Z]?|N-?400|passport|visa|ead|green card|i-20|ds-2019|arrival record|"
                       r"approval notice|receipt notice)[^-–]*[-–]\s*([A-Z][a-z]+(?: [A-Z]\.?)?(?: [A-Z][a-z]+){1,2})\b", re.I)


def _name_from_text(raw: str) -> str:
    """Passport machine-readable zone, then 'Surname / Given names' labels."""
    m = _MRZ.search((raw or "").replace(" ", ""))
    if m:
        last, first = m.group(1).replace("<", " ").strip(), m.group(2).replace("<", " ").strip()
        if first and last:
            return _clean_name(f"{last}, {first}")
    m = _LABELLED.search(raw or "")
    if m and len(m.group(1).split()) <= 3:
        return _clean_name(f"{m.group(1).strip()}, {m.group(2).strip()}")
    return ""


def _name_from_filename(filename: str) -> str:
    m = _FILENAME.search(re.sub(r"\.\w+$", "", filename or ""))
    return _clean_name(m.group(1)) if m else ""


def _split(name: str):
    if "," in name:                                  # "MEHTA, ARJUN"
        last, first = [p.strip() for p in name.split(",", 1)]
    else:
        parts = name.split(" ")
        first, last = " ".join(parts[:-1]) or parts[0], parts[-1] if len(parts) > 1 else ""
    first = re.sub(r"\b[A-Z]\.?(?=\s|$)", "", first.title()).strip() or first.title()   # drop middle initials
    return re.sub(r"\s+", " ", first), last.title()


class ImmigrationWorkflowService:
    def __init__(self, db: Session):
        self.db = db

    def process(self, document: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        doc_id = str(document.id)
        if getattr(document, "client_id", None):
            return {"skipped": True, "reason": "already linked"}
        analysis = state.get("analysis_result") or {}
        name = _person_name(analysis.get("fields") or {}) or _name_from_text(state.get("raw_text") or "")
        if not name:
            # A filename is only trusted to link to an existing client, never to create one
            guess = _name_from_filename(getattr(document, "filename", ""))
            client_id = guess and self._match(*_split(guess))
            if client_id:
                link_documents(self.db, client_id, [doc_id])
                self.db.commit()
                return {"action": "linked", "client_id": client_id, "route": "client", "by": "filename"}
            return {"skipped": True, "reason": "no person name on document"}
        first, last = _split(name)

        client_id = self._match(first, last)
        if client_id:
            link_documents(self.db, client_id, [doc_id])
            self.db.commit()
            return {"action": "linked", "client_id": client_id, "route": "client"}

        from app.api.approvals_router import ensure_table
        ensure_table(self.db)
        aid = self._pending_request(first, last)
        if aid:
            self._add_doc(aid, doc_id)
            return {"action": "queued_for_approval", "approval_id": aid, "route": "client"}

        from app.api.approvals_router import create_request
        if self._rejected(first, last):
            return {"skipped": True, "reason": "client creation was rejected before"}
        tenant = str(getattr(document, "tenant_id", "") or get_current_tenant_id() or DEFAULT_TENANT_ID)
        aid = create_request(
            self.db, tenant, doc_id,
            title=f"Create client {first} {last}".strip(),
            description=(f"{document.filename or 'A document'} names {first} {last}, who is not a client yet. "
                         "Approve to create the client and link this document."),
            approvers=[], requested_by="document_ai", source="document_ai",
            action={"type": "create_client", "first_name": first, "last_name": last, "doc_ids": [doc_id]},
        )
        return {"action": "approval_requested", "approval_id": aid, "route": "client"}

    def _match(self, first: str, last: str) -> Optional[str]:
        """Same last name and same first given name; middle names and initials are ignored."""
        if not first or not last:
            return None
        rows = self.db.execute(text("""
            SELECT id, first_name FROM clients
            WHERE status != 'deleted' AND LOWER(last_name) = LOWER(:l)"""), {"l": last}).fetchall()
        want = first.lower().split()[0]
        hits = [str(r.id) for r in rows if (r.first_name or "").lower().split()[:1] == [want]]
        return hits[0] if len(hits) == 1 else None          # two possible people → ask, don't guess

    def _rejected(self, first: str, last: str) -> bool:
        return bool(self.db.execute(text("""
            SELECT 1 FROM approval_requests
            WHERE status = 'rejected' AND action->>'type' = 'create_client'
              AND LOWER(action->>'first_name') = LOWER(:f) AND LOWER(action->>'last_name') = LOWER(:l)
            LIMIT 1"""), {"f": first, "l": last}).fetchone())

    def _pending_request(self, first: str, last: str) -> Optional[str]:
        row = self.db.execute(text("""
            SELECT id FROM approval_requests
            WHERE status = 'pending' AND action->>'type' = 'create_client'
              AND LOWER(action->>'first_name') = LOWER(:f) AND LOWER(action->>'last_name') = LOWER(:l)
            LIMIT 1"""), {"f": first, "l": last}).fetchone()
        return str(row.id) if row else None

    def _add_doc(self, aid: str, doc_id: str):
        self.db.execute(text("""
            UPDATE approval_requests
            SET action = jsonb_set(action, '{doc_ids}', (action->'doc_ids') || to_jsonb(CAST(:d AS text)))
            WHERE id = :id AND NOT (action->'doc_ids') ? :d"""), {"id": aid, "d": doc_id})
        self.db.commit()


def link_documents(db: Session, client_id: str, doc_ids: List[str]):
    for d in doc_ids:
        db.execute(text("""
            UPDATE documents
            SET client_id = :cid, client_link_status = 'confirmed', client_link_score = 1.0,
                client_linked_at = NOW(), client_linked_by = 'document_ai'
            WHERE id = :d AND client_id IS NULL"""), {"cid": client_id, "d": d})


def create_client_from_approval(db: Session, tenant_id: str, action: Dict[str, Any], approved_by: str) -> str:
    """Runs when a 'create_client' approval is approved. Reuses a client created meanwhile."""
    first, last = action.get("first_name", ""), action.get("last_name", "")
    client_id = ImmigrationWorkflowService(db)._match(first, last)
    if not client_id:
        client_id = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO clients (id, first_name, last_name, client_type, status, source, created_by, tenant_id)
            VALUES (:id, :f, :l, 'personal', 'active', 'document_ai', :by, :t)"""),
                   {"id": client_id, "f": first, "l": last, "by": approved_by, "t": tenant_id})
    link_documents(db, client_id, action.get("doc_ids") or [])
    db.commit()
    try:
        scan_existing(db)          # documents named only in the filename (e.g. a passport) can link now
    except Exception as e:
        logger.warning(f"  immigration rescan after approval failed: {e}")
    logger.info(f"  created client {client_id[:8]}… '{first} {last}' on approval by {approved_by}")
    return client_id


def scan_existing(db: Session) -> Dict[str, int]:
    """Run the hook over immigration documents that are not linked to a client yet (startup backfill)."""
    from app.services.post_processing_registry import PostProcessingRegistry
    types = [t for t, hooks in PostProcessingRegistry._HOOKS.items() if "immigration" in hooks]
    ids = [r.id for r in db.execute(text(
        "SELECT id FROM documents WHERE client_id IS NULL AND LOWER(doc_type) = ANY(:t) ORDER BY created_at"),
        {"t": types}).fetchall()]
    counts: Dict[str, int] = {}
    for i in ids:
        r = (PostProcessingRegistry.run(str(i), db) or {}).get("immigration") or {}
        k = r.get("action") or ("skipped" if r.get("skipped") else "error")
        counts[k] = counts.get(k, 0) + 1
    return counts
