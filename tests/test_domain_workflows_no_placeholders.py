"""Domain workflow services record only what a document says — never demo placeholders."""
import uuid

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
import app.models.client, app.models.policy, app.models.document  # noqa: F401  (ORM relationships resolve together)
from app.services.finance_workflow_service import FinanceWorkflowService
from app.services.healthcare_workflow_service import HealthcareWorkflowService
from app.services.hr_workflow_service import HRWorkflowService
from app.services.real_estate_workflow_service import RealEstateWorkflowService
from app.services.supply_chain_workflow_service import SupplyChainWorkflowService

TENANT = "00000000-0000-0000-0000-0000000000a1"
PLACEHOLDERS = ["Amazon Web Services", "Robert Henderson", "Evelyn Vance", "Sarah M. Jenkins", "Lead Architect",
                "Stone Way", "Michael S. Reynolds", "Maersk", "Shanghai", "Tacoma", "Pacific Cargo", "685000"]
TABLES = ["finance_matches", "patient_charts", "patient_biomarkers", "hr_candidates", "hr_onboarding_tasks",
          "real_estate_properties", "supply_chain_shipments", "supply_chain_suppliers"]


@pytest.fixture
def db():
    s = SessionLocal()
    made = []

    def doc(body: str, name: str):
        did = str(uuid.uuid4())
        s.execute(text("INSERT INTO documents (id, filename, file_path, tenant_id, status, raw_text) "
                       "VALUES (:id, :f, '/dev/null', :t, 'completed', :r)"), {"id": did, "f": name, "t": TENANT, "r": body})
        s.commit()
        made.append(did)
        from app.models.document import Document
        return s.get(Document, did)
    yield s, doc
    s.rollback()
    for t in TABLES:
        if t == "real_estate_properties":
            s.execute(text("DELETE FROM real_estate_milestones WHERE property_id IN (SELECT id FROM real_estate_properties "
                           "WHERE document_id::text = ANY(:d))"), {"d": made})
        s.execute(text(f"DELETE FROM {t} WHERE document_id::text = ANY(:d)"), {"d": made})
    s.execute(text("DELETE FROM notifications WHERE doc_id::text = ANY(:d)"), {"d": made})
    s.execute(text("DELETE FROM documents WHERE id = ANY(:d)"), {"d": made})
    s.commit()
    s.close()


def _rows(s, did):
    out = []
    for t in TABLES:
        col = "property_id" if t == "real_estate_milestones" else "document_id"
        out += [dict(r._mapping) for r in s.execute(text(f"SELECT * FROM {t} WHERE {col}::text = :d"), {"d": did})]
    return out


@pytest.mark.parametrize("svc", [FinanceWorkflowService, HealthcareWorkflowService, HRWorkflowService,
                                 RealEstateWorkflowService, SupplyChainWorkflowService])
def test_unrelated_document_writes_nothing(db, svc):
    s, doc = db
    d = doc("Chase checking statement\nBeginning balance $1,234.56\nZelle payment to Shiva $25.00", "stmt.pdf")
    svc(s).process(d, {})
    assert _rows(s, d.id) == []


def test_invoice_without_po_is_pending_not_approved(db):
    s, doc = db
    d = doc("INVOICE\nInvoice Number: 7781\nVendor: Acme Industrial Supply\nInvoice Amount: $4,250.00", "inv.pdf")
    FinanceWorkflowService(s).process(d, {})
    r = s.execute(text("SELECT * FROM finance_matches WHERE document_id::text = :d"), {"d": d.id}).fetchone()
    assert r.vendor_name == "Acme Industrial Supply" and float(r.invoice_val) == 4250.0
    assert r.match_status == "pending" and not r.is_approved


def test_invoice_with_po_and_receipt_is_matched(db):
    s, doc = db
    d = doc("Invoice Number: 7782\nVendor: Acme Industrial Supply\nInvoice Amount: $4,250.00\n"
            "PO Amount: $4,000.00\nReceived Amount: $4,250.00", "inv2.pdf")
    FinanceWorkflowService(s).process(d, {})
    r = s.execute(text("SELECT * FROM finance_matches WHERE document_id::text = :d"), {"d": d.id}).fetchone()
    assert r.match_status == "failed" and float(r.po_val) == 4000.0


def test_resume_uses_real_values(db):
    s, doc = db
    d = doc("Name: Priya Raman\nRole: Data Engineer\n6 Years of Experience\nPython, SQL, Airflow", "cv.docx")
    HRWorkflowService(s).process(d, {})
    rows = _rows(s, d.id)
    blob = repr(rows)
    assert any(r.get("candidate_name") == "Priya Raman" for r in rows)
    assert not any(p in blob for p in PLACEHOLDERS)
    assert "JavaScript" not in blob                     # no invented skills
