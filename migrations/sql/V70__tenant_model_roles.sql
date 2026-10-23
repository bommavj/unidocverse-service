-- V70: Model roles — each step (planner, answer, extraction, vision) can use its own model.
-- engine = 'local' (an Ollama model) or 'cloud' (a configured provider in tenant_llm_providers).
-- No row = the step keeps its default. Read by app/core/llm.py (role_config).
CREATE TABLE IF NOT EXISTS tenant_model_roles (
    tenant_id  TEXT        NOT NULL,
    role       TEXT        NOT NULL,                 -- planner | answer | extraction | vision
    engine     TEXT        NOT NULL,                 -- local | cloud
    provider   TEXT,                                 -- cloud only: key in llm.PROVIDERS
    model      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT,
    PRIMARY KEY (tenant_id, role)
);
