"""
Two fresh organisations, one document / client / policy each: over HTTP, each organisation's token
sees only its own rows on the main list endpoints. Their rows are removed afterwards; the tenants
rows stay, deactivated, because audit_log is append-only and still references them.
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth_router import _create_token
from app.core.database import SessionLocal

# List endpoints that return organisation data and take no required parameters
ENDPOINTS = [
    "/api/documents", "/api/documents/cross-link", "/api/research/docs", "/api/research/history",
    "/api/brief/today", "/api/notifications", "/api/ams/clients", "/api/ams/policies",
    "/api/ams/policies/pipeline/renewals", "/api/ams/renewal/pipeline", "/api/ams/renewal/upcoming",
    "/api/ams/renewal/calendar", "/api/ams/calendar/events", "/api/insights/portfolio",
    "/api/insights/alerts", "/api/analytics/transactions",
    "/api/agents/runs", "/api/approvals", "/api/claims", "/api/matters", "/api/quotes",
]


@pytest.fixture(scope="module")
def orgs():
    from app.main import app
    db = SessionLocal()
    made = {}
    try:
        for name in ("a", "b"):
            tid, mark = str(uuid.uuid4()), f"ISO{uuid.uuid4().hex[:8].upper()}"
            db.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:t, :n, :s)"),
                       {"t": tid, "n": f"Isolation {mark}", "s": f"iso-{mark.lower()}"})
            uid = db.execute(text("""INSERT INTO users (tenant_id, username, email, full_name, password_hash, role, is_active)
                VALUES (:t, :u, :e, :n, 'x', 'admin', TRUE) RETURNING id"""),
                {"t": tid, "u": f"iso_{mark.lower()}", "e": f"{mark.lower()}@example.test", "n": mark}).scalar()
            cid = str(uuid.uuid4())
            db.execute(text("""INSERT INTO clients (id, tenant_id, client_number, client_type, first_name, last_name, status)
                VALUES (:c, :t, :num, 'personal', 'Client', :m, 'active')"""), {"c": cid, "t": tid, "num": mark, "m": mark})
            db.execute(text("""INSERT INTO policies (tenant_id, client_id, policy_number, line_of_business, carrier_name,
                    effective_date, expiry_date, status, renewal_status, premium_amount, premium_freq)
                VALUES (:t, :c, :m, 'auto', :m, :eff, :exp, 'active', 'not_started', 1000, 'annual')"""),
                {"t": tid, "c": cid, "m": mark, "eff": date.today() - timedelta(days=300), "exp": date.today() + timedelta(days=20)})
            db.execute(text("""INSERT INTO documents (id, tenant_id, filename, file_path, status, doc_type, summary, client_id)
                VALUES (:d, :t, :f, '/dev/null', 'completed', 'insurance_policy', :m, :c)"""),
                {"d": str(uuid.uuid4()), "t": tid, "f": f"{mark}.pdf", "m": mark, "c": cid})
            token = _create_token(uid, f"iso_{mark.lower()}", "admin", timedelta(minutes=10), tid)
            made[name] = {"tenant": tid, "mark": mark, "headers": {"Authorization": f"Bearer {token}"}}
        db.commit()
        yield TestClient(app), made
    finally:
        db.rollback()
        tables = [r[0] for r in db.execute(text("""SELECT c.relname FROM pg_class c
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
            WHERE c.relkind = 'r' AND NOT c.relispartition AND c.relname NOT IN ('tenants', 'users')"""))]
        for o in made.values():
            # documents before clients (documents.client_id), users last (rows may reference them)
            for t in sorted(tables, key=lambda t: t != "documents") + ["users"]:
                db.execute(text(f"DELETE FROM {t} WHERE tenant_id::text = :t"), {"t": o["tenant"]})
            db.execute(text("UPDATE tenants SET is_active = FALSE, name = 'Isolation test (removed)' WHERE id = :t"),
                       {"t": o["tenant"]})
        db.commit()
        db.close()


@pytest.mark.parametrize("me,other", [("a", "b"), ("b", "a")])
def test_lists_show_only_own_organisation(orgs, me, other):
    client, made = orgs
    leaks = []
    for path in ENDPOINTS:
        r = client.get(path, headers=made[me]["headers"])
        if made[other]["mark"] in r.text:
            leaks.append(path)
    assert not leaks, f"{me} sees {other}'s rows on {leaks}"


@pytest.mark.parametrize("me", ["a", "b"])
def test_own_rows_still_visible(orgs, me):
    client, made = orgs
    mark = made[me]["mark"]
    assert mark in client.get("/api/documents", headers=made[me]["headers"]).text
    assert mark in client.get("/api/ams/clients", headers=made[me]["headers"]).text
    assert mark in client.get("/api/ams/policies", headers=made[me]["headers"]).text
