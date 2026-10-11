"""The routers registered on 2026-10-09 (claims, matters, quotes, CLM, risk, audit, alerts) are tenant-private over HTTP."""
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth_router import _create_token
from app.core.database import SessionLocal

DEMO = json.loads((Path(__file__).parent / "fixtures" / "demo_user.json").read_text())


@pytest.fixture(scope="module")
def ctx():
    from app.main import app
    c = TestClient(app)
    db = SessionLocal()
    users = {r.username: r for r in db.execute(text("SELECT id, username, role, tenant_id FROM users"))}
    db.close()
    tok = lambda u: {"Authorization": "Bearer " + _create_token(u.id, u.username, u.role, timedelta(minutes=10), u.tenant_id)}
    made = {}
    yield c, tok(users[DEMO["username"]]), tok(users["admin"]), made
    db = SessionLocal()
    if made.get("claim"):
        db.execute(text("DELETE FROM claim_timeline WHERE claim_id=:i"), {"i": made["claim"]})
        db.execute(text("DELETE FROM claims WHERE id=:i"), {"i": made["claim"]})
    if made.get("matter"):
        db.execute(text("DELETE FROM matters WHERE id=:i"), {"i": made["matter"]})
    if made.get("quote"):
        db.execute(text("DELETE FROM quote_activity WHERE quote_id=:i"), {"i": made["quote"]})
        db.execute(text("DELETE FROM quotes WHERE id=:i"), {"i": made["quote"]})
    db.commit(); db.close()


def _ids(resp):
    j = resp.json()
    rows = j if isinstance(j, list) else j.get("quotes") or j.get("items") or j.get("events") or []
    return {r.get("id") for r in rows}


def test_claims(ctx):
    c, demo, admin, made = ctx
    r = c.post("/api/claims", json={"title": "ZZ iso claim"}, headers=demo)
    assert r.status_code == 200
    cid = made["claim"] = r.json()["claim"]["id"]
    assert cid in _ids(c.get("/api/claims", headers=demo))
    assert cid not in _ids(c.get("/api/claims", headers=admin))
    for call in (lambda: c.get(f"/api/claims/{cid}", headers=admin),
                 lambda: c.patch(f"/api/claims/{cid}", json={"adjuster": "x"}, headers=admin),
                 lambda: c.delete(f"/api/claims/{cid}", headers=admin)):
        assert call().status_code == 404
    assert c.get(f"/api/claims/{cid}", headers=demo).status_code == 200


def test_matters(ctx):
    c, demo, admin, made = ctx
    r = c.post("/api/matters", json={"title": "ZZ iso matter"}, headers=demo)
    assert r.status_code == 200
    mid = made["matter"] = r.json()["matter"]["id"]
    assert mid in _ids(c.get("/api/matters", headers=demo)) and mid not in _ids(c.get("/api/matters", headers=admin))
    assert c.get(f"/api/matters/{mid}", headers=admin).status_code == 404
    assert c.delete(f"/api/matters/{mid}", headers=admin).status_code == 404


def test_quotes(ctx):
    c, demo, admin, made = ctx
    r = c.post("/api/quotes", json={"lob": "GL", "state": "CA"}, headers=demo)
    assert r.status_code in (200, 201), r.text
    body = r.json()
    qid = made["quote"] = (body.get("quote") or body)["id"]
    assert qid in _ids(c.get("/api/quotes", headers=demo)) and qid not in _ids(c.get("/api/quotes", headers=admin))
    assert c.get(f"/api/quotes/{qid}", headers=admin).status_code == 404
    assert c.delete(f"/api/quotes/{qid}", headers=admin).status_code == 404


def test_other_tenant_documents_rejected(ctx):
    c, demo, admin, made = ctx
    db = SessionLocal()
    admin_doc = db.execute(text("SELECT id::text FROM documents WHERE tenant_id <> :t LIMIT 1"), {"t": DEMO["tenant_id"]}).scalar()
    db.close()
    assert c.get(f"/api/risk/{admin_doc}", headers=demo).status_code == 404
    assert c.post("/api/clm", json={"doc_id": admin_doc}, headers=demo).status_code == 404


def test_lists_and_alerts_scoped(ctx):
    c, demo, admin, made = ctx
    for path in ("/api/audit", "/api/audit/summary", "/api/clm", "/api/risk", "/api/alerts/all", "/api/alerts/proactive"):
        assert c.get(path, headers=demo).status_code == 200, path
    db = SessionLocal()
    admin_files = {r[0] for r in db.execute(text("SELECT filename FROM documents WHERE tenant_id <> :t"), {"t": DEMO["tenant_id"]})}
    db.close()
    demo_alerts = {a["filename"] for a in c.get("/api/alerts/proactive", headers=demo).json()["alerts"] if a["source"] != "tax"}
    assert not (demo_alerts & admin_files)
