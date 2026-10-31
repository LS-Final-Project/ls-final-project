-- KAN-99: chunking-v3 search context. Document title, product, kind and section path go
-- into the full-text index only; snippets and model input keep using text. Rows of older
-- index versions keep an empty context, so their search results do not change.
-- A generated column cannot be altered, so tsv and its index are rebuilt. Safe to re-run.
-- Needs C10. Takes an exclusive lock on cs.document_chunks while the table is rewritten.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

ALTER TABLE cs.document_chunks ADD COLUMN IF NOT EXISTS search_context text NOT NULL DEFAULT '';
DROP INDEX IF EXISTS cs.document_chunks_tsv;
ALTER TABLE cs.document_chunks DROP COLUMN IF EXISTS tsv;
ALTER TABLE cs.document_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
    to_tsvector('english', coalesce(table_title, '') || ' ' || text || ' ' || search_context)
) STORED;
CREATE INDEX IF NOT EXISTS document_chunks_tsv ON cs.document_chunks USING gin (tsv);
COMMIT;
