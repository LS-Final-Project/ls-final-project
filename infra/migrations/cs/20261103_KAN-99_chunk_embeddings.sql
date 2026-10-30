-- KAN-99: embeddings of the C/S document chunks for vector search (model fixed by KAN-144:
-- gemini-embedding-2, 768 dimensions). Needs 20261029_KAN-99_search_chunks.sql and the pgvector
-- extension in the public schema (the admin installs it; this file does not create it).
-- A separate table keyed by (chunk row, embedding_version) so a model or dimension change adds
-- new rows instead of mixing vectors, and re-indexing a document (delete and insert of its chunk
-- rows) drops its embeddings through the foreign key.
-- 20261101_KAN-107_worker_source.sql revokes every cs table from cs_worker before granting its
-- list, so re-apply this file after re-applying that one. Re-runnable.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.chunk_embeddings (
    chunk_row_id uuid NOT NULL REFERENCES cs.document_chunks(id) ON DELETE CASCADE,
    -- model:dimensions:task_type, e.g. gemini-embedding-2:768:RETRIEVAL_DOCUMENT.
    embedding_version text NOT NULL
        CHECK (embedding_version ~ '^[A-Za-z0-9._-]{1,64}:768:[A-Z_]{1,32}$'),
    embedding public.vector(768) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_row_id, embedding_version)
);
CREATE INDEX IF NOT EXISTS chunk_embeddings_cosine
    ON cs.chunk_embeddings USING hnsw (embedding public.vector_cosine_ops);

-- Embeddings are replaced with their chunks, never edited.
GRANT SELECT, INSERT, DELETE ON cs.chunk_embeddings TO cs_app;
REVOKE UPDATE, TRUNCATE ON cs.chunk_embeddings FROM cs_app;
-- The processing worker searches but never writes.
GRANT SELECT ON cs.chunk_embeddings TO cs_worker;
COMMIT;
