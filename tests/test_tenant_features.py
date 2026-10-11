"""Module switches are per organisation, admin-only to change, and default from the legacy global row."""
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth_router import _create_token
from app.api.features_router import FLAGS, flags_for
from app.core.database import SessionLocal

DEMO = json.loads((Path(__file__).parent / "fixtures" / "demo_user.json").read_text())


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def ctx():
    db = SessionLocal()
    users = db.execute(text("SELECT id, username, role, tenant_id FROM users WHERE is_active")).fetchall()
    other_admin = next(u for u in users if u.role == "admin" and str(u.tenant_id) != DEMO["tenant_id"])
    demo = next(u for u in users if u.username == DEMO["username"])
    tenants = [DEMO["tenant_id"], str(other_admin.tenant_id)]
    saved = {t: db.execute(text("SELECT flags FROM tenant_features WHERE tenant_id = :t"), {"t": t}).scalar() for t in tenants}
    db.execute(text("DELETE FROM tenant_features WHERE tenant_id = ANY(:t)"), {"t": tenants}); db.commit()
    tok = lambda u: {"Authorization": f"Bearer {_create_token(u.id, u.username, u.role, timedelta(minutes=10), u.tenant_id)}"}
    yield {"db": db, "demo": demo, "other": other_admin, "tok": tok}
    db.execute(text("DELETE FROM tenant_features WHERE tenant_id = ANY(:t)"), {"t": tenants})
    for t, f in saved.items():
        if f is not None:
            db.execute(text("INSERT INTO tenant_features (tenant_id, flags) VALUES (:t, CAST(:f AS jsonb))"), {"t": t, "f": json.dumps(f)})
    db.commit(); db.close()


def test_defaults_come_from_legacy_row(ctx):
    f = flags_for(ctx["db"], DEMO["tenant_id"])
    assert set(f) == set(FLAGS)
    assert f["immigration_mode"] == f["hr_mode"]            # immigration used to follow the HR switch


def test_admin_change_is_scoped_to_own_tenant(client, ctx):
    other = ctx["tok"](ctx["other"])
    before = client.get("/api/features", headers=ctx["tok"](ctx["demo"])).json()["flags"]
    r = client.patch("/api/features", json={"supply_chain_mode": not before["supply_chain_mode"]}, headers=other)
    assert r.status_code == 200
    assert r.json()["flags"]["supply_chain_mode"] == (not before["supply_chain_mode"])
    after_demo = client.get("/api/features", headers=ctx["tok"](ctx["demo"])).json()["flags"]
    assert after_demo == before                              # the other organisation's change didn't touch this one


def test_partial_update_keeps_other_flags(client, ctx):
    other = ctx["tok"](ctx["other"])
    start = client.get("/api/features", headers=other).json()["flags"]
    client.patch("/api/features", json={"legal_mode": not start["legal_mode"]}, headers=other)
    now = client.get("/api/features", headers=other).json()["flags"]
    assert now["legal_mode"] == (not start["legal_mode"])
    assert {k: v for k, v in now.items() if k != "legal_mode"} == {k: v for k, v in start.items() if k != "legal_mode"}


def test_non_admin_cannot_change(client, ctx):
    u = ctx["db"].execute(text("SELECT id, username, role, tenant_id FROM users WHERE is_active AND role <> 'admin' LIMIT 1")).fetchone()
    if not u:
        pytest.skip("no non-admin user")
    r = client.patch("/api/features", json={"finance_mode": True}, headers=ctx["tok"](u))
    assert r.status_code == 403


def test_scheduled_agents_stay_on_while_any_tenant_uses_module(client, ctx):
    db = ctx["db"]
    other = ctx["tok"](ctx["other"])
    db.execute(text("INSERT INTO tenant_features (tenant_id, flags) VALUES ('zz-test-tenant', '{\"compliance_mode\": true}') ON CONFLICT (tenant_id) DO UPDATE SET flags = EXCLUDED.flags"))
    db.commit()
    client.patch("/api/features", json={"compliance_mode": False}, headers=other)
    rows = db.execute(text("SELECT enabled FROM scheduled_agents WHERE flag_name = 'compliance_mode'")).fetchall()
    db.execute(text("DELETE FROM tenant_features WHERE tenant_id = 'zz-test-tenant'")); db.commit()
    assert all(r.enabled for r in rows)
