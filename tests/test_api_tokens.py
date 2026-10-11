"""API tokens: create once, act as the owner inside their tenant, honour scope, revoke cleanly."""
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
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def session(client):
    tok = client.post("/api/auth/login", json={"username": DEMO["username"], "password": DEMO["password"]}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def made(client, session):
    out = {}
    for scope in ("read", "write"):
        r = client.post("/api/auth/tokens", json={"name": f"ZZTEST {scope}", "scope": scope, "expires_days": 1}, headers=session)
        assert r.status_code == 200, r.text
        out[scope] = r.json()
    yield out
    db = SessionLocal()
    db.execute(text("DELETE FROM api_tokens WHERE name LIKE 'ZZTEST%'"))
    db.commit(); db.close()


def _b(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_token_shown_once_and_hashed(client, session, made):
    raw = made["read"]["token"]
    assert raw.startswith("udv_") and made["read"]["prefix"] in raw
    listed = client.get("/api/auth/tokens", headers=session).json()["tokens"]
    mine = next(t for t in listed if t["id"] == made["read"]["id"])
    assert "token" not in mine and mine["status"] == "active"
    db = SessionLocal()
    assert db.execute(text("SELECT count(*) FROM api_tokens WHERE token_hash = :r"), {"r": raw}).scalar() == 0
    db.close()


def test_token_acts_as_owner_in_own_tenant(client, made):
    r = client.get("/api/auth/me", headers=_b(made["read"]["token"]))
    assert r.status_code == 200
    assert r.json().get("username") == DEMO["username"]
    cases = client.get("/api/immigration-suite/cases", headers=_b(made["read"]["token"]))
    assert cases.status_code == 200


def test_read_token_cannot_write(client, made):
    r = client.post("/api/immigration-suite/cases", json={"beneficiary": "ZZTEST", "case_type": "i90"}, headers=_b(made["read"]["token"]))
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"]


def test_write_token_can_write(client, made):
    r = client.post("/api/immigration-suite/cases", json={"beneficiary": "ZZTEST token case", "case_type": "i90"}, headers=_b(made["write"]["token"]))
    assert r.status_code in (200, 201), r.text
    db = SessionLocal()
    row = db.execute(text("SELECT tenant_id FROM imm_cases WHERE beneficiary = 'ZZTEST token case'")).fetchone()
    assert str(row.tenant_id) == DEMO["tenant_id"]
    db.execute(text("DELETE FROM imm_cases WHERE beneficiary = 'ZZTEST token case'")); db.commit(); db.close()


def test_token_cannot_manage_tokens(client, made):
    assert client.get("/api/auth/tokens", headers=_b(made["write"]["token"])).status_code == 403
    assert client.post("/api/auth/tokens", json={"name": "ZZTEST nested"}, headers=_b(made["write"]["token"])).status_code == 403


def test_other_tenant_cannot_revoke(client, made):
    db = SessionLocal()
    a = db.execute(text("SELECT id, username, role, tenant_id FROM users WHERE tenant_id <> :t AND role = 'admin' LIMIT 1"),
                   {"t": DEMO["tenant_id"]}).fetchone()
    db.close()
    if not a:
        pytest.skip("no admin in another tenant")
    other = _b(_create_token(a.id, a.username, a.role, timedelta(minutes=5), a.tenant_id))
    assert client.post(f"/api/auth/tokens/{made['read']['id']}/revoke", headers=other).status_code == 404
    assert all(t["id"] != made["read"]["id"] for t in client.get("/api/auth/tokens", headers=other).json()["tokens"])


def test_revoked_and_bogus_tokens_rejected(client, session, made):
    assert client.post(f"/api/auth/tokens/{made['write']['id']}/revoke", headers=session).status_code == 200
    assert client.get("/api/auth/me", headers=_b(made["write"]["token"])).status_code == 401
    assert client.get("/api/auth/me", headers=_b("udv_deadbeef_not-a-real-token")).status_code == 401
