"""
Domain dashboard and export endpoints return only the current tenant's rows.
Runs inside one transaction that is rolled back — nothing is left in the database.
"""
import csv
import io
import uuid

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.tenant import set_current_tenant_id
from app.core.tenant_scope import scope_sql

T1 = "00000000-0000-0000-0000-000000000001"
T2 = "00000000-0000-0000-0000-0000000000a1"

# table → name column the endpoints show
TABLES = {"legal_contracts": "contract_name", "legal_clauses": "clause_name", "patient_charts": "patient_name",
          "patient_biomarkers": "biomarker_name", "real_estate_properties": "address", "hr_candidates": "candidate_name",
          "supply_chain_shipments": "bol_number", "finance_matches": "vendor_name"}


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    marks = {}
    try:
        for tenant in (T1, T2):
            doc = str(uuid.uuid4())
            s.execute(text("INSERT INTO documents (id, filename, file_path, tenant_id, status) "
                           "VALUES (:id, :f, '/dev/null', :t, 'completed')"), {"id": doc, "f": f"iso-{tenant[-2:]}.pdf", "t": tenant})
            for table, col in TABLES.items():
                mark = f"ISO-{tenant[-2:]}-{table}"
                extra = {"finance_matches": ", invoice_val, po_val, receipt_val, match_status", "patient_biomarkers": ", is_abnormal"}.get(table, "")
                vals = {"finance_matches": ", 1, 1, 1, 'failed'", "patient_biomarkers": ", TRUE"}.get(table, "")
                s.execute(text(f"INSERT INTO {table} (document_id, {col}{extra}) VALUES (:d, :m{vals})"), {"d": doc, "m": mark})
            marks[tenant] = tenant[-2:]
        # an orphan row with no document must be visible to nobody
        s.execute(text("INSERT INTO hr_candidates (candidate_name) VALUES ('ISO-ORPHAN')"))
        yield s
    finally:
        s.rollback()
        s.close()
        set_current_tenant_id(T1)


def _endpoints():
    from app.api import domains_router as d
    return [d.get_legal_clauses if hasattr(d, "get_legal_clauses") else None,
            *[getattr(d, n) for n in dir(d) if n.startswith("get_") and callable(getattr(d, n))]]


@pytest.mark.parametrize("tenant,other", [(T1, T2), (T2, T1)])
def test_domain_endpoints(db, tenant, other):
    from app.api import domains_router as d
    set_current_tenant_id(tenant)
    seen = ""
    for name in dir(d):
        fn = getattr(d, name)
        if name.startswith("get_") and callable(fn) and name not in ("get_db", "get_current_tenant_id", "get_finance_documents"):
            try:
                seen += repr(fn(db=db))
            except TypeError:
                continue
    assert f"ISO-{tenant[-2:]}" in seen
    assert f"ISO-{other[-2:]}" not in seen and "ISO-ORPHAN" not in seen


def _read(resp) -> str:
    import asyncio
    it = resp.body_iterator
    if hasattr(it, "__aiter__"):
        async def collect():
            return b"".join([c if isinstance(c, bytes) else c.encode() async for c in it])
        data = asyncio.run(collect())
    else:
        data = b"".join(c if isinstance(c, bytes) else c.encode() for c in it)
    return data.decode("utf-8", "replace")


@pytest.mark.parametrize("tenant,other", [(T1, T2), (T2, T1)])
def test_exports(db, tenant, other):
    from app.api import export_router as e
    set_current_tenant_id(tenant)
    body = ""
    for fn in (e.export_legal, e.export_healthcare, e.export_finance, e.export_real_estate, e.export_hr,
               e.export_supply_chain):
        body += _read(fn(fmt="csv", db=db))
    body += _read(e.export_documents(fmt="csv", doc_type=None, folder=None, db=db))
    assert f"ISO-{tenant[-2:]}" in body or f"iso-{tenant[-2:]}" in body
    assert f"ISO-{other[-2:]}" not in body and f"iso-{other[-2:]}" not in body and "ISO-ORPHAN" not in body


def test_scope_sql_keeps_aliases():
    q = scope_sql("SELECT lc.x FROM legal_contracts lc LEFT JOIN documents d ON d.id = lc.document_id ORDER BY lc.created_at")
    assert ") lc LEFT JOIN documents d" in q and ":tenant_id" in q
    q = scope_sql("SELECT * FROM hr_candidates ORDER BY created_at")
    assert ") hr_candidates ORDER BY" in q
