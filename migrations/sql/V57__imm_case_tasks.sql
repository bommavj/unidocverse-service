-- Per-case task lists for immigration cases (Tasks tab on the case page)
CREATE TABLE IF NOT EXISTS imm_case_tasks (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL REFERENCES imm_cases(id) ON DELETE CASCADE,
    tenant_id     UUID NOT NULL,
    title         TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'paralegal' CHECK (role IN ('attorney', 'paralegal', 'client', 'petitioner', 'other')),
    assignee      TEXT,
    due_date      DATE,
    done          BOOLEAN NOT NULL DEFAULT FALSE,
    done_at       TIMESTAMPTZ,
    done_by       TEXT,
    position      INTEGER NOT NULL DEFAULT 0,
    from_template TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_imm_case_tasks_case ON imm_case_tasks (case_id, position);
CREATE INDEX IF NOT EXISTS idx_imm_case_tasks_open ON imm_case_tasks (tenant_id, done, due_date);
