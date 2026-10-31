-- V74: Immigration document graph (docs/design/immigration-document-graph.md).
-- Nodes are documents (and facts derived from them, such as status periods), edges are typed
-- relationships between nodes, checks are the mismatches found between them. Everything the
-- system proposes is editable by the lawyer; every decision is kept with who, when and why.

CREATE TABLE IF NOT EXISTS imm_nodes (
    id           TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    person_key   TEXT,                               -- normalised "last|first|dob" or client id
    client_id    TEXT,
    node_type    TEXT        NOT NULL,               -- passport, i94, i797_approval, lca, i140, status_period …
    document_id  TEXT,                               -- NULL for derived nodes
    label        TEXT,
    facts        JSONB       NOT NULL DEFAULT '{}'::jsonb,   -- {field: {value, extracted, set_by, set_at, reason}}
    valid_from   DATE,
    valid_to     DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_imm_nodes_doc ON imm_nodes (tenant_id, document_id) WHERE document_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_imm_nodes_person ON imm_nodes (tenant_id, person_key);
CREATE INDEX IF NOT EXISTS idx_imm_nodes_client ON imm_nodes (tenant_id, client_id);

CREATE TABLE IF NOT EXISTS imm_edges (
    id           TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    from_node    TEXT        NOT NULL REFERENCES imm_nodes(id) ON DELETE CASCADE,
    to_node      TEXT        NOT NULL REFERENCES imm_nodes(id) ON DELETE CASCADE,
    edge_type    TEXT        NOT NULL,               -- identifies, requires, decides, grants, derives_from, supersedes, same_as, enables
    state        TEXT        NOT NULL DEFAULT 'proposed',   -- proposed | confirmed | rejected | overridden
    confidence   REAL,
    basis        TEXT,                               -- why it was proposed ("same receipt number IOE…")
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ,
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_imm_edges ON imm_edges (tenant_id, from_node, to_node, edge_type);

CREATE TABLE IF NOT EXISTS imm_checks (
    id           TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    person_key   TEXT,
    check_key    TEXT        NOT NULL,               -- stable key: rule + nodes, so reruns update, not duplicate
    rule         TEXT        NOT NULL,
    severity     TEXT        NOT NULL,               -- blocker | warning | info
    message      TEXT        NOT NULL,
    node_ids     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    details      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    state        TEXT        NOT NULL DEFAULT 'open',       -- open | resolved | waived
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ,
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_imm_checks ON imm_checks (tenant_id, check_key);
CREATE INDEX IF NOT EXISTS idx_imm_checks_person ON imm_checks (tenant_id, person_key, state);

-- Every lawyer action on the graph (confirm, reject, re-link, correct, waive)
CREATE TABLE IF NOT EXISTS imm_graph_log (
    id           BIGSERIAL   PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    person_key   TEXT,
    action       TEXT        NOT NULL,
    target       TEXT        NOT NULL,               -- node / edge / check id
    before       JSONB,
    after        JSONB,
    reason       TEXT,
    actor        TEXT        NOT NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_imm_graph_log ON imm_graph_log (tenant_id, person_key, at DESC);

SELECT udv_apply_tenant_policy(t, 'udv_tenant_ok(tenant_id::text)')
FROM unnest(ARRAY['imm_nodes', 'imm_edges', 'imm_checks', 'imm_graph_log']) AS t;
