-- KAN-99: C/S document chunks and the English full-text index (search tables are owned
-- per service; the chunking code is shared in backend/engine/search). Needs only the
-- cs schema from 001. No embedding column until the model is fixed (KAN-144).
-- Re-indexing deletes the rows of one (document_sha256, index_version) and inserts them
-- again, so cs_app gets SELECT, INSERT and DELETE but never UPDATE.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.document_chunks (
    id uuid PRIMARY KEY,
    document_sha256 text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
    index_version text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    chunk_id text NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('TEXT', 'TABLE')),
    section_path text[] NOT NULL,
    text text NOT NULL,
    char_count integer NOT NULL CHECK (char_count = char_length(text)),
    page_start integer,
    page_end integer,
    table_title text,
    has_deleted_values boolean NOT NULL,
    flags text[] NOT NULL,
    block_ids text[] NOT NULL,
    source_position jsonb NOT NULL,
    -- ChunkMetadata. The chunker never guesses these; the caller fills them.
    document_kind text,
    product_family text,
    access_scope text,
    meta_version text,
    -- Source document version link. No FK until a C/S document table exists.
    document_version_id uuid,
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL DEFAULT 'DERIVED'
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(table_title, '') || ' ' || text)
    ) STORED,
    UNIQUE (document_sha256, index_version, ordinal)
);
CREATE INDEX IF NOT EXISTS document_chunks_tsv ON cs.document_chunks USING gin (tsv);

-- 001 default privileges also grant UPDATE; chunks are replaced, never edited.
GRANT SELECT, INSERT, DELETE ON cs.document_chunks TO cs_app;
REVOKE UPDATE, TRUNCATE ON cs.document_chunks FROM cs_app;
COMMIT;
