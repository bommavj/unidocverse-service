-- Values staff confirm or enter for official USCIS forms (e.g. petitioner FEIN, LCA number, wage),
-- keyed by our field keys; the auto-fill uses these before intake answers and case data.
ALTER TABLE imm_cases ADD COLUMN IF NOT EXISTS form_data JSONB DEFAULT '{}';
