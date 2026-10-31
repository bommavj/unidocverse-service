-- V75: Family links between people in the immigration document graph.
-- A dependent's status (H-4, L-2, …) lasts only as long as the principal's, so linked family members
-- are checked together. Links are proposed from documents (marriage certificate, I-130, I-129F,
-- matching dependent/principal status) and confirmed, corrected or removed by the lawyer.

CREATE TABLE IF NOT EXISTS imm_family (
    id           TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    person_a     TEXT        NOT NULL,
    person_b     TEXT        NOT NULL,
    relation     TEXT        NOT NULL,               -- spouse | child | parent | fiance | relative
    state        TEXT        NOT NULL DEFAULT 'proposed',   -- proposed | confirmed | rejected
    basis        TEXT,
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ,
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_imm_family ON imm_family (tenant_id, LEAST(person_a, person_b), GREATEST(person_a, person_b));
CREATE INDEX IF NOT EXISTS idx_imm_family_a ON imm_family (tenant_id, person_a);
CREATE INDEX IF NOT EXISTS idx_imm_family_b ON imm_family (tenant_id, person_b);

SELECT udv_apply_tenant_policy('imm_family', 'udv_tenant_ok(tenant_id::text)');
