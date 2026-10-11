"""Tenant isolation through real HTTP: login → JWT → TenantMiddleware → endpoints."""
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
    return TestClient(app)          # no `with`: skips model preloading at startup


@pytest.fixture(scope="module")
def users():
    db = SessionLocal()
    rows = {r.username: r for r in db.execute(text("SELECT id, username, role, tenant_id FROM users"))}
    db.close()
    return rows


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_login_token_carries_tenant(client):
    r = client.post("/api/auth/login", json={"username": DEMO["username"], "password": DEMO["password"]})
    assert r.status_code == 200
    from jose import jwt
    claims = jwt.get_unverified_claims(r.json()["access_token"])
    assert claims["tenant_id"] == DEMO["tenant_id"]


def test_users_only_see_their_tenant(client, users):
    demo = client.post("/api/auth/login", json={"username": DEMO["username"], "password": DEMO["password"]}).json()["access_token"]
    a = users["admin"]
    admin = _create_token(a.id, a.username, a.role, timedelta(minutes=5), a.tenant_id)
    db = SessionLocal()
    mine = {r[0] for r in db.execute(text("SELECT id FROM imm_cases WHERE tenant_id = :t"), {"t": str(a.tenant_id)})}
    db.close()
    demo_cases = {c["id"] for c in client.get("/api/immigration-suite/cases", headers=_auth(demo)).json()}
    admin_cases = {c["id"] for c in client.get("/api/immigration-suite/cases", headers=_auth(admin)).json()}
    assert not (demo_cases & mine)
    assert admin_cases == mine
    # a client-supplied tenant header must not switch tenants
    spoof = client.get("/api/immigration-suite/cases", headers={**_auth(demo), "X-Tenant-ID": str(a.tenant_id)}).json()
    assert not ({c["id"] for c in spoof} & mine)


def test_old_token_without_tenant_uses_users_own_tenant(client, users):
    d = users[DEMO["username"]]
    old = _create_token(d.id, d.username, d.role, timedelta(minutes=5))          # no tenant claim
    db = SessionLocal()
    others = {r[0] for r in db.execute(text("SELECT id FROM imm_cases WHERE tenant_id <> :t"), {"t": DEMO["tenant_id"]})}
    db.close()
    got = {c["id"] for c in client.get("/api/immigration-suite/cases", headers=_auth(old)).json()}
    assert not (got & others)
