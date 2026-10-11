"""Insurance automation rules are per tenant: list/edit/delete/run and evaluation stay inside one tenant."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api import insurance_automation_router as r
from app.core.database import SessionLocal
from app.core.tenant import set_current_tenant_id

T1 = "00000000-0000-0000-0000-000000000001"
T2 = "00000000-0000-0000-0000-0000000000a1"
TAG = f"ISO-{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    r.ensure_tables(s)
    yield s
    # The router commits, so clean up explicitly
    s.rollback()
    q = [x[0] for x in s.execute(text("SELECT id FROM quotes WHERE notes LIKE :t"), {"t": f"%{TAG}%"})]
    rules = [x[0] for x in s.execute(text("SELECT id FROM insurance_automation_rules WHERE name LIKE :t"), {"t": f"{TAG}%"})]
    if q:
        s.execute(text("DELETE FROM insurance_notifications WHERE quote_id = ANY(:q)"), {"q": q})
        s.execute(text("DELETE FROM insurance_automation_log WHERE entity_id = ANY(:q)"), {"q": q})
        s.execute(text("DELETE FROM quotes WHERE id = ANY(:q)"), {"q": q})
    if rules:
        s.execute(text("DELETE FROM insurance_automation_log WHERE rule_id = ANY(:r)"), {"r": rules})
        s.execute(text("DELETE FROM insurance_automation_rules WHERE id = ANY(:r)"), {"r": rules})
    s.commit()
    s.close()
    set_current_tenant_id(T1)


def _rule(db, tenant, name, trigger="quote_comparing"):
    set_current_tenant_id(tenant)
    return r.create_rule({"name": f"{TAG} {name}", "trigger_type": trigger, "action_type": "notify_agent",
                          "action_config": {"message": "{quote_number}"}}, db=db)


def test_rules_are_private(db):
    a = _rule(db, T1, "A")
    assert str(a["tenant_id"]) == T1
    set_current_tenant_id(T2)
    assert a["id"] not in {x["id"] for x in r.list_rules(db=db)}
    for call in (lambda: r.update_rule(a["id"], {"enabled": False}, db=db),
                 lambda: r.delete_rule(a["id"], db=db),
                 lambda: r.run_single_rule(a["id"], db=db)):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 404
    set_current_tenant_id(T1)
    assert a["id"] in {x["id"] for x in r.list_rules(db=db)}           # untouched


def test_update_whitelist(db):
    a = _rule(db, T1, "B")
    for bad in ({"foo": 1}, {"enabled=TRUE, name": "x"}):
        with pytest.raises(HTTPException) as e:
            r.update_rule(a["id"], bad, db=db)
        assert e.value.status_code == 400
    r.update_rule(a["id"], {"enabled": False, "tenant_id": T2}, db=db)       # tenant_id is ignored
    row = db.execute(text("SELECT enabled, tenant_id FROM insurance_automation_rules WHERE id=:id"), {"id": a["id"]}).fetchone()
    assert row.enabled is False and str(row.tenant_id) == T1


def test_evaluation_stays_in_tenant(db):
    mine, theirs = str(uuid.uuid4()), str(uuid.uuid4())
    for qid, tenant in ((mine, T1), (theirs, T2)):
        db.execute(text("INSERT INTO quotes (id, quote_number, lob, state, stage, tenant_id, notes) "
                        "VALUES (:id, :n, 'GL', 'CA', 'comparing', :t, :tag)"),
                   {"id": qid, "n": f"QT-{qid[:6]}", "t": tenant, "tag": TAG})
    db.commit()
    rule = _rule(db, T1, "C")
    set_current_tenant_id(T1)
    r.run_single_rule(rule["id"], db=db)
    touched = {x[0] for x in db.execute(text("SELECT entity_id FROM insurance_automation_log WHERE rule_id=:r"),
                                        {"r": rule["id"]})}
    assert mine in touched and theirs not in touched


def test_seed_templates_per_tenant(db):
    set_current_tenant_id(T2)
    before = {x["name"] for x in r.list_rules(db=db)}
    r.seed_templates(db=db)
    after = r.list_rules(db=db)
    assert all(str(x["tenant_id"]) == T2 for x in after)
    assert {t["name"] for t in r.TEMPLATES} <= {x["name"] for x in after}
    # clean up only what this test seeded for T2
    new = [x["id"] for x in after if x["name"] not in before]
    if new:
        db.execute(text("DELETE FROM insurance_automation_rules WHERE id = ANY(:i)"), {"i": new}); db.commit()
