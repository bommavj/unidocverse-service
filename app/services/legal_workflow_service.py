import logging
import re
import uuid
from datetime import datetime, date
from typing import Any, Dict, Optional
from sqlalchemy import text
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
from sqlalchemy.orm import Session
from app.core.tenant import get_current_tenant_id, DEFAULT_TENANT_ID
from app.utils.large_document_helper import LargeDocumentScanner

logger = logging.getLogger(__name__)

class LegalWorkflowService:
    """
    Production-grade Legal Workflow Service.
    Parses contracts, NDA, regulatory files, and leases.
    Deduplicates counterparties, extracts clauses, flags deviation, and sets risk scores.
    Enriches global document metadata for standard platform alerts and real-time notifications.
    """

    def __init__(self, db: Session):
        self.db = db

    def process(self, doc: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            doc_id = str(doc.id)
            txt = doc.raw_text or ""
            filename = doc.filename or "Unnamed Contract"
            
            scanner = LargeDocumentScanner(txt)

            # 1. Extract Contract / Agreement Name
            contract_name = scanner.find_regex(r'(?:Agreement|Contract)\s+Title:\s*(.*)')
            if not contract_name:
                m = re.search(r'^([^\n]{10,100})', txt)
                contract_name = m.group(1).strip() if m else filename
            contract_name = self._clean_str(re.sub(r'[*_#`]+', ' ', contract_name or ''))

            # 2. Extract Counterparty
            counterparty = self._extract_counterparty(scanner, txt)

            # 3. Extract Notice Period
            notice_period = scanner.find_regex(r'(\d+\s+(?:days|months)\s+prior\s+notice)') or \
                            scanner.find_regex(r'notice\s+period\s+of\s+(\d+\s+\w+)') or \
                            None
            notice_period = self._clean_str(notice_period)

            # 4. Extract Governing Law & Venue
            gov_law = scanner.find_regex(r'governing\s+law\s+of\s+(?:the\s+state\s+of\s+)?([A-Za-z\s]{2,30})') or \
                      scanner.find_regex(r'governed\s+by\s+(?:the\s+laws\s+of\s+)?([A-Za-z\s]{2,30})') or \
                      None
            gov_law = self._clean_str(gov_law)

            # 5. Extract Expiry Date & Calculate Status
            expiry_date = self._parse_date(
                scanner.find_regex(r'(?:expiry|termination|expiration)\s+date\s*(?:is|of)?:\s*([\d\-\/A-Za-z\s,]{6,25})') or \
                scanner.find_regex(r'terminate\s+on\s+([\d\-\/A-Za-z\s,]{6,25})') or \
                scanner.find_regex(r'expires\s+on\s+([\d\-\/A-Za-z\s,]{6,25})')
            )
            
            status = "active"
            days_until = None
            if expiry_date:
                days_until = (expiry_date - date.today()).days
                if days_until < 0:
                    status = "expired"
                elif days_until <= 90:
                    status = "expiring"

            # 6. Playbook-based compliance validation
            deviations = []
            playbook_notes = []
            risk_rating = "Low"

            # Check for indemnification deviations
            has_indemn = scanner.find_regex(r'(indemnity|indemnify|hold harmless|unlimited liability)')
            if has_indemn:
                lower_indemn = has_indemn.lower()
                if "unlimited" in lower_indemn or "hold harmless" in lower_indemn:
                    deviations.append("Broad Indemnification")
                    playbook_notes.append("WARNING: Playbook deviation detected. Indemnity is unlimited or includes hold harmless provisions, shifting substantial liability risk.")
                    risk_rating = "High"

            # Check NDA/confidentiality duration deviation
            has_conf = scanner.find_regex(r'(confidential|confidentiality|nondisclosure|non-disclosure)')
            duration_match = scanner.find_regex(r'(\d+\s+years?)')
            if has_conf and duration_match:
                try:
                    years = int(re.sub(r'\D', '', duration_match))
                    if years > 5:
                        deviations.append("Long confidentiality duration")
                        playbook_notes.append(f"ADVISORY: Confidentiality obligation of {years} years exceeds standard corporate playbook guidance of 5 years.")
                        if risk_rating != "High":
                            risk_rating = "Medium"
                except:
                    pass

            # Check governing law deviation
            if gov_law and gov_law.lower() not in ["delaware", "new york", "california"]:
                deviations.append(f"Non-standard governing law: {gov_law}")
                playbook_notes.append(f"ADVISORY: Governing law is set to {gov_law} instead of standard jurisdictions (Delaware, New York, or California).")
                if risk_rating == "Low":
                    risk_rating = "Medium"

            # Log dynamic platform notifications for deviations
            if deviations:
                self._create_notification(
                    doc_id=doc_id,
                    doc_type="contract",
                    filename=filename,
                    severity="critical" if risk_rating == "High" else "warning",
                    title=f"Legal Playbook Deviation - {counterparty}",
                    message=f"Risk: {risk_rating}. Violations: {', '.join(deviations)}.",
                    expiry_date=expiry_date.strftime("%Y-%m-%d") if expiry_date else None,
                    days_until=days_until
                )

            # 7. Sync back to general Document Metadata for alerts
            meta = doc.doc_metadata or {}
            if "analyzed_data" not in meta:
                meta["analyzed_data"] = {}
            if "fields" not in meta["analyzed_data"]:
                meta["analyzed_data"]["fields"] = {}

            meta["analyzed_data"]["fields"].update({
                "named_insured": counterparty, 
                "expiration_date": expiry_date.strftime("%Y-%m-%d") if expiry_date else None,
                "days_until_expiry": days_until,
                "notice_period": notice_period,
                "governing_law": gov_law,
                "contract_name": contract_name,
                "risk_rating": risk_rating,
                "deviations": deviations,
                "playbook_notes": playbook_notes
            })
            
            doc.doc_metadata = meta
            self.db.execute(text("UPDATE documents SET doc_metadata = :meta WHERE id = :doc_id"), {
                "meta": json_dumps_compat(meta),
                "doc_id": doc_id
            })

            # Deduplicate contract rows
            existing = self.db.execute(text("""
                SELECT id FROM legal_contracts 
                WHERE document_id = :doc_id OR (counterparty = :cp AND contract_name = :name)
                LIMIT 1
            """), {"doc_id": doc_id, "cp": counterparty, "name": contract_name}).fetchone()

            if existing:
                self.db.execute(text("""
                    UPDATE legal_contracts
                    SET contract_name = :name,
                        counterparty = :cp,
                        notice_period = :notice,
                        expiry_date = :expiry,
                        risk_rating = :risk,
                        status = :status,
                        created_at = NOW()
                    WHERE id = :id
                """), {
                    "id": existing.id,
                    "name": contract_name,
                    "cp": counterparty,
                    "notice": notice_period,
                    "expiry": expiry_date,
                    "risk": risk_rating,
                    "status": status
                })
                contract_id = existing.id
            else:
                contract_id = str(uuid.uuid4())
                self.db.execute(text("""
                    INSERT INTO legal_contracts (id, document_id, contract_name, counterparty, notice_period, expiry_date, risk_rating, status)
                    VALUES (:id, :doc_id, :name, :cp, :notice, :expiry, :risk, :status)
                """), {
                    "id": contract_id,
                    "doc_id": doc_id,
                    "name": contract_name,
                    "cp": counterparty,
                    "notice": notice_period,
                    "expiry": expiry_date,
                    "risk": risk_rating,
                    "status": status
                })

            # Clear old clauses
            self.db.execute(text("DELETE FROM legal_clauses WHERE document_id = :doc_id"), {"doc_id": doc_id})

            # Save parsed clauses
            if has_indemn:
                clause_snippet = scanner.extract_context(r'(indemnity|indemnify|hold harmless|unlimited liability)', 20, 180)
                if clause_snippet:
                    self.db.execute(text("""
                        INSERT INTO legal_clauses (document_id, clause_type, clause_name, clause_text, is_deviation, governing_law)
                        VALUES (:doc_id, 'Indemnification', 'Indemnity & Liability', :text, :dev, :gov)
                    """), {
                        "doc_id": doc_id,
                        "text": self._clean_str(clause_snippet)[:500],
                        "dev": "Broad Indemnification" in deviations,
                        "gov": gov_law
                    })

            if has_conf:
                clause_snippet = scanner.extract_context(r'(confidential|confidentiality|nondisclosure|non-disclosure)', 20, 180)
                if clause_snippet:
                    self.db.execute(text("""
                        INSERT INTO legal_clauses (document_id, clause_type, clause_name, clause_text, is_deviation, governing_law)
                        VALUES (:doc_id, 'Confidentiality', 'Nondisclosure restrictions', :text, :dev, :gov)
                    """), {
                        "doc_id": doc_id,
                        "text": self._clean_str(clause_snippet)[:500],
                        "dev": "Long confidentiality duration" in deviations,
                        "gov": gov_law
                    })

            has_juris = scanner.find_regex(r'(governing law|governed by|jurisdiction|venue|chancery)')
            if has_juris:
                clause_snippet = scanner.extract_context(r'(governing law|governed by|jurisdiction|venue|chancery)', 20, 180)
                if clause_snippet:
                    self.db.execute(text("""
                        INSERT INTO legal_clauses (document_id, clause_type, clause_name, clause_text, is_deviation, governing_law)
                        VALUES (:doc_id, 'Jurisdiction', 'Governing Law and Venue', :text, :dev, :gov)
                    """), {
                        "doc_id": doc_id,
                        "text": self._clean_str(clause_snippet)[:500],
                        "dev": any("governing law" in d.lower() for d in deviations),
                        "gov": gov_law
                    })

            self.db.commit()
            return {
                "action": "legal_contract_processed",
                "contract_id": str(contract_id),
                "counterparty": counterparty,
                "risk_rating": risk_rating,
                "status": status,
                "playbook_notes": playbook_notes
            }

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error in LegalWorkflowService: {e}", exc_info=True)
            return {"error": str(e)}

    def _create_notification(self, doc_id: str, doc_type: str, filename: str, severity: str, title: str, message: str, expiry_date: str = None, days_until: int = None):
        try:
            self.db.execute(text("""
                INSERT INTO notifications (doc_id, doc_type, filename, severity, title, message, expiry_date, days_until, is_read, created_at, updated_at, tenant_id)
                VALUES (:doc_id, :doc_type, :filename, :severity, :title, :message, :expiry, :days, FALSE, NOW(), NOW(), :tenant_id)
                ON CONFLICT (doc_id, severity) DO UPDATE 
                SET title = EXCLUDED.title,
                    message = EXCLUDED.message,
                    expiry_date = EXCLUDED.expiry_date,
                    days_until = EXCLUDED.days_until,
                    updated_at = NOW(),
                    is_read = FALSE
            """), {
                "doc_id": doc_id,
                "doc_type": doc_type,
                "filename": filename,
                "severity": severity,
                "title": title,
                "message": message,
                "expiry": expiry_date,
                "days": days_until,
                "tenant_id": get_current_tenant_id() or DEFAULT_TENANT_ID
            })
        except Exception as err:
            logger.error(f"Failed to create database notification: {err}")

    def _extract_counterparty(self, scanner: LargeDocumentScanner, text_val: str) -> str:
        match_between = scanner.find_regex(r'(?:between|among)\s+([A-Za-z0-9\s,\.\(\)]+?)\s+and\s+([A-Za-z0-9\s,\.\(\)]+?)(?:\,|\s+effective|\s+collectively)')
        if match_between:
            cp = match_between
            m = re.search(r'and\s+([A-Za-z0-9\s,\.\(\)]+)', cp, re.IGNORECASE)
            if m:
                cp = m.group(1).strip()
            if "unidocverse" in cp.lower() or "client" in cp.lower():
                m2 = re.search(r'(.*?)\s+and', cp, re.IGNORECASE)
                if m2:
                    cp = m2.group(1).strip()
            return self._clean_str(cp)[:100]
        
        fallback = scanner.find_regex(r'counterparty:\s*(.*)') or \
                   scanner.find_regex(r'agreement\s+by\s+and\s+between\s+(.*?)\s+and')
        return self._clean_str(fallback) or None

    def _clean_str(self, val: str) -> str:
        if not val:
            return ""
        val = re.sub(r'\s+', ' ', val)
        val = re.sub(r'[^\x00-\x7F]+', '', val)
        return val.strip()

    def _parse_date(self, v) -> Optional[date]:
        if not v:
            return None
        s = self._clean_str(v).strip()
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except:
                continue
        m = re.search(r'([A-Za-z]+)\s+(\d+),\s+(\d{4})', s)
        if m:
            try:
                return datetime.strptime(m.group(0), "%B %d, %Y").date()
            except:
                pass
        return None

def json_dumps_compat(obj) -> str:
    import json
    return json.dumps(obj)
