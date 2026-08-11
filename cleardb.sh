#!/bin/bash
docker exec unidocverse-postgres psql -U unidocverse -d unidocverse_db -c "
BEGIN;
TRUNCATE TABLE document_qa_history RESTART IDENTITY CASCADE;
TRUNCATE TABLE document_chunks RESTART IDENTITY CASCADE;
TRUNCATE TABLE documents RESTART IDENTITY CASCADE;
COMMIT;
SELECT 'Done — all documents cleared.' AS status;
"