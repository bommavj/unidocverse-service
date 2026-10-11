"""
Tenant scoping for the domain tables that have no tenant_id column.

These tables (legal_contracts, patient_charts, finance_matches, …) are filled
from processed documents and carry document_id, so a row belongs to the tenant
that owns its document. `scope_sql` swaps each such table in a query for a
subquery that keeps only the current tenant's rows, under the same name or
alias — the rest of the query is unchanged:

    FROM legal_contracts lc   →   FROM (SELECT x.* FROM legal_contracts x JOIN documents d
                                        ON d.id::text = x.document_id::text
                                        WHERE d.tenant_id = :tenant_id) lc

Fails closed: a row with no document_id is not shown to anyone. Bind
`tenant_params()` (adds :tenant_id) when executing.
"""
import re
from typing import Dict, Optional

from app.core.tenant import DEFAULT_TENANT_ID, get_current_tenant_id

_BY_DOCUMENT = ("legal_clauses", "legal_contracts", "patient_biomarkers", "patient_charts", "real_estate_properties",
                "hr_candidates", "hr_onboarding_tasks", "supply_chain_shipments", "supply_chain_suppliers",
                "finance_matches", "finance_anomalies")

_SUBQUERY = {t: (f"SELECT x.* FROM {t} x JOIN documents d ON d.id::text = x.document_id::text "
                 f"WHERE d.tenant_id = :tenant_id") for t in _BY_DOCUMENT}
# Milestones belong to a property, which belongs to a document
_SUBQUERY["real_estate_milestones"] = (
    "SELECT x.* FROM real_estate_milestones x JOIN real_estate_properties p ON p.id = x.property_id "
    "JOIN documents d ON d.id::text = p.document_id::text WHERE d.tenant_id = :tenant_id")

# Insurance automation: log entries and notifications belong to the quote / policy they're about
_SUBQUERY["insurance_automation_log"] = (
    "SELECT x.* FROM insurance_automation_log x WHERE "
    "(x.entity_type IN ('quote', 'carrier_quote') AND EXISTS (SELECT 1 FROM quotes q WHERE q.id::text = x.entity_id "
    "AND q.tenant_id = :tenant_id)) OR (x.entity_type = 'policy' AND EXISTS (SELECT 1 FROM policies p "
    "WHERE p.id::text = x.entity_id AND p.tenant_id = :tenant_id))")
_SUBQUERY["insurance_notifications"] = (
    "SELECT x.* FROM insurance_notifications x WHERE "
    "EXISTS (SELECT 1 FROM quotes q WHERE q.id::text = x.quote_id::text AND q.tenant_id = :tenant_id) OR "
    "EXISTS (SELECT 1 FROM policies p WHERE p.id::text = x.policy_id::text AND p.tenant_id = :tenant_id)")

# Tables that carry tenant_id themselves
for _t in ("quotes",):
    _SUBQUERY[_t] = f"SELECT x.* FROM {_t} x WHERE x.tenant_id::text = :tenant_id"

_KEYWORDS = {"where", "order", "group", "join", "left", "right", "inner", "outer", "full", "cross", "on", "limit",
             "having", "union", "as", "using", "natural", "offset"}
_TABLE_RE = re.compile(r"\b(FROM|JOIN)\s+(" + "|".join(sorted(_SUBQUERY, key=len, reverse=True)) +
                       r")\b(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?", re.I)


def current_tenant() -> str:
    return get_current_tenant_id() or DEFAULT_TENANT_ID


def scope_sql(sql: str) -> str:
    def swap(m: re.Match) -> str:
        kw, table, alias = m.group(1), m.group(2).lower(), m.group(3)
        trailing = ""
        if alias and alias.lower() in _KEYWORDS:
            trailing, alias = f" {alias}", None
        return f"{kw} ({_SUBQUERY[table]}) {alias or table}{trailing}"
    return _TABLE_RE.sub(swap, sql)


def tenant_params(params: Optional[Dict] = None) -> Dict:
    return {**(params or {}), "tenant_id": current_tenant()}
