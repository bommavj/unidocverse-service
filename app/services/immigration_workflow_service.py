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


def _person_name(fields: Dict[str, Any]) -> str:
    for k in _NAME_FIELDS:
        v = fields.get(k)
        if isinstance(v, str) and v.strip():
            return re.sub(r"\s+", " ", v).strip()
    given, surname = fields.get("given_names") or fields.get("given_name"), fields.get("surname")
    if given and surname:
        return f"{given} {surname}".strip()
    return ""


def _split(name: str):
    if "," in name:                                  # "MEHTA, ARJUN"
        last, first = [p.strip() for p in name.split(",", 1)]
    else:
        parts = name.split(" ")
        first, last = " ".join(parts[:-1]) or parts[0], parts[-1] if len(parts) > 1 else ""
    return first.title(), last.title()


class ImmigrationWorkflowService:
    def __init__(self, db: Session):
        self.db = db

    def process(self, document: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        doc_id = str(document.id)
        if getattr(document, "client_id", None):
            return {"skipped": True, "reason": "already linked"}
        analysis = state.get("analysis_result") or {}
        name = _person_name(analysis.get("fields") or {})
        if not name:
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
        tenant = get_current_tenant_id() or str(getattr(document, "tenant_id", "") or DEFAULT_TENANT_ID)
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
        row = self.db.execute(text("""
            SELECT id FROM clients
            WHERE status != 'deleted' AND LOWER(first_name) = LOWER(:f) AND LOWER(last_name) = LOWER(:l)
            LIMIT 1"""), {"f": first, "l": last}).fetchone()
        return str(row.id) if row else None

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
    logger.info(f"  created client {client_id[:8]}… '{first} {last}' on approval by {approved_by}")
    return client_id
