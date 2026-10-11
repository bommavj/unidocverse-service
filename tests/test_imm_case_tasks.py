"""Immigration case tasks: templates per case type, add/complete/edit/delete, history, tenant scope."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth_router import _create_token
from app.core.database import SessionLocal
from app.services.immigration import suite

DEMO = json.loads((Path(__file__).parent / "fixtures" / "demo_user.json").read_text())


@pytest.fixture(scope="module")
def ctx():
    from app.main import app
    db = SessionLocal()
    users = db.execute(text("SELECT id, username, role, tenant_id FROM users WHERE is_active")).fetchall()
    other = next(u for u in users if str(u.tenant_id) != DEMO["tenant_id"])
    tok = lambda u: {"Authorization": f"Bearer {_create_token(u.id, u.username, u.role, timedelta(minutes=10), u.tenant_id)}"}
    demo = next(u for u in users if u.username == DEMO["username"])
    case = suite.create_case(db, DEMO["tenant_id"], {"beneficiary": "ZZTASK Priya Raman", "case_type": "h1b_extension"})
    yield {"c": TestClient(app), "db": db, "case": case, "me": tok(demo), "other": tok(other)}
    db.execute(text("DELETE FROM imm_cases WHERE id = :c"), {"c": case["id"]}); db.commit(); db.close()


def test_every_case_type_has_a_template():
    tpl = suite.task_templates()
    for ct in suite.rules()["case_types"]:
        assert tpl.get(ct) or tpl.get("_default"), ct
    assert all({"title", "role", "due"} <= set(t) for t in tpl["h1b_extension"])
    assert all(t["role"] in suite.TASK_ROLES for v in tpl.values() for t in v)


def test_apply_template_is_idempotent(ctx):
    c, cid, me = ctx["c"], ctx["case"]["id"], ctx["me"]
    r = c.post(f"/api/immigration-suite/cases/{cid}/tasks/template", headers=me)
    assert r.status_code == 201 and r.json()["added"] == len(suite.task_templates()["h1b_extension"])
    first = r.json()["tasks"][0]
    assert first["due_date"] == (date.today() + timedelta(days=1)).isoformat() and first["days"] == 1
    again = c.post(f"/api/immigration-suite/cases/{cid}/tasks/template", headers=me).json()
    assert again["added"] == 0 and again["skipped"] == r.json()["added"]


def test_add_complete_edit_delete(ctx):
    c, cid, me = ctx["c"], ctx["case"]["id"], ctx["me"]
    t = c.post(f"/api/immigration-suite/cases/{cid}/tasks", json={"title": "Call employer HR", "role": "attorney",
               "assignee": "V. Bomma", "due_date": "2026-11-01"}, headers=me).json()
    assert t["role"] == "attorney" and t["due_date"] == "2026-11-01" and not t["done"]
    done = c.patch(f"/api/immigration-suite/cases/{cid}/tasks/{t['id']}", json={"done": True}, headers=me).json()
    assert done["done"] and done["done_at"]
    moved = c.patch(f"/api/immigration-suite/cases/{cid}/tasks/{t['id']}", json={"due_date": "2026-11-05", "role": "paralegal"}, headers=me).json()
    assert moved["due_date"] == "2026-11-05" and moved["role"] == "paralegal" and moved["done"]
    assert c.delete(f"/api/immigration-suite/cases/{cid}/tasks/{t['id']}", headers=me).status_code == 204
    events = c.get(f"/api/immigration-suite/cases/{cid}", headers=me).json()["events"]
    texts = [e["text"] for e in events]
    assert "Task completed: Call employer HR" in texts and "Task removed: Call employer HR" in texts


def test_validation(ctx):
    c, cid, me = ctx["c"], ctx["case"]["id"], ctx["me"]
    assert c.post(f"/api/immigration-suite/cases/{cid}/tasks", json={"title": "  "}, headers=me).status_code == 400
    assert c.post(f"/api/immigration-suite/cases/{cid}/tasks", json={"title": "x", "role": "boss"}, headers=me).status_code == 400
    assert c.post(f"/api/immigration-suite/cases/{cid}/tasks", json={"title": "x", "due_date": "soon"}, headers=me).status_code == 400
    assert c.patch(f"/api/immigration-suite/cases/{cid}/tasks/nope", json={"done": True}, headers=me).status_code == 404


def test_case_includes_tasks_and_other_tenant_blocked(ctx):
    c, cid = ctx["c"], ctx["case"]["id"]
    case = c.get(f"/api/immigration-suite/cases/{cid}", headers=ctx["me"]).json()
    assert case["tasks"] and case["has_task_template"] is True
    assert c.get(f"/api/immigration-suite/cases/{cid}/tasks", headers=ctx["other"]).status_code == 404
    assert c.post(f"/api/immigration-suite/cases/{cid}/tasks", json={"title": "x"}, headers=ctx["other"]).status_code == 404
