-- V59: Clean documents.extracted_facts and index the way facts are queried.
--
-- Model output stored "no value" as text ("null", "N/A", "") and numbers as text
-- ("$1,620.70"), so JSON queries saw strings where they expected null or a number.
-- udv_clean_facts() applies the same rules as facts_extractor.normalize_facts():
--   • fake-null strings → JSON null, at any depth
--   • numeric text → number, only for money keys; identifiers (invoice numbers,
--     SSN last-4, ZIP, account numbers, dates, years) keep their text and leading zeros

CREATE OR REPLACE FUNCTION udv_clean_facts(j jsonb, k text DEFAULT '') RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v   text;
    out jsonb;
    rec record;
BEGIN
    IF j IS NULL THEN
        RETURN NULL;
    END IF;
    CASE jsonb_typeof(j)
    WHEN 'object' THEN
        out := '{}'::jsonb;
        FOR rec IN SELECT key, value FROM jsonb_each(j) LOOP
            out := out || jsonb_build_object(rec.key, udv_clean_facts(rec.value, rec.key));
        END LOOP;
        RETURN out;
    WHEN 'array' THEN
        RETURN coalesce((SELECT jsonb_agg(udv_clean_facts(e, k)) FROM jsonb_array_elements(j) e), '[]'::jsonb);
    WHEN 'string' THEN
        v := btrim(j #>> '{}');
        IF lower(v) IN ('null', 'none', 'n/a', 'na', '', 'unknown', 'not found', 'not available', 'not provided', '-', '—') THEN
            RETURN 'null'::jsonb;
        END IF;
        IF k = 'tax_year' AND v ~ '^\d{4}$' THEN
            RETURN to_jsonb(v::int);
        END IF;
        IF k ~* '(amount|total|balance|wage|tax|pay(?!_?(date|period|frequency|ee|er))|price|value|income|withh|fee|cost|charge|gross|net|proceeds|basis|interest|dividend|compensation|ytd|escrow|principal|deduction|premium|rent|salary|bonus|refund|due|credit|debit|limit)'
           AND k !~* '(number|_id$|^id$|ssn|ein|tin|zip|postal|account|routing|phone|last4|code|policy|receipt|invoice_no|reference|year|date|period|name|address|percent|rate)'
           AND v ~ '^\(?-?\$?\s*-?[0-9,]+(\.[0-9]+)?\)?$' THEN
            RETURN to_jsonb(
                (CASE WHEN v LIKE '(%' OR v LIKE '%-%' THEN -1 ELSE 1 END)
                * regexp_replace(v, '[^0-9.]', '', 'g')::numeric);
        END IF;
        RETURN to_jsonb(v);
    ELSE
        RETURN j;
    END CASE;
END $$;

-- Backfill: only rows that change
UPDATE documents
SET extracted_facts = udv_clean_facts(extracted_facts)
WHERE extracted_facts IS NOT NULL
  AND extracted_facts IS DISTINCT FROM udv_clean_facts(extracted_facts);

-- ── Indexes for how facts are read ────────────────────────────────────────────
-- The existing GIN index (idx_documents_extracted_facts) serves `?` key-exists checks.
-- Typical reads: "this tenant's documents of type X that have facts", then a key.

-- Pay stubs, W-2s, 1099s… for a tenant (payroll_facts, tax reconciliation)
CREATE INDEX IF NOT EXISTS idx_documents_facts_tenant_type
    ON documents (tenant_id, doc_type)
    WHERE extracted_facts IS NOT NULL;

-- Tax-year filters ("2024 W-2s")
CREATE INDEX IF NOT EXISTS idx_documents_facts_tax_year
    ON documents (tenant_id, doc_type, ((extracted_facts->>'tax_year')))
    WHERE extracted_facts ? 'tax_year';

-- Vendor / payee lookups use ILIKE '%name%': a trigram index makes that indexable
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_documents_facts_vendor_trgm
             ON documents USING gin ((extracted_facts->>''vendor'') gin_trgm_ops)
             WHERE extracted_facts ? ''vendor''';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'V59: trigram index skipped (%)', SQLERRM;
END $$;

ANALYZE documents;
