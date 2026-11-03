-- KAN-188: immutable, review-only single-line diagram candidates per original document version.
-- Apply after T14 worker roles. JSON is validated by the engine/API; this table also bounds the envelope.
-- Original document bytes and business decisions are not changed. Re-applying preserves all rows.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
CREATE TABLE IF NOT EXISTS tft.document_diagrams (
    document_version_id uuid PRIMARY KEY REFERENCES tft.document_versions(id),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK (
        jsonb_typeof(payload) = 'object'
        AND payload->'schema_version' = '1'::jsonb
        AND CASE WHEN jsonb_typeof(payload->'items') = 'array'
            THEN jsonb_array_length(payload->'items') <= 16 ELSE false END
        AND CASE WHEN jsonb_typeof(payload->'warnings') = 'array'
            THEN jsonb_array_length(payload->'warnings') <= 512 ELSE false END
        AND payload ?& ARRAY['schema_version','items','warnings']
        AND octet_length(payload::text) <= 4194304
    ),
    created_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON tft.document_diagrams FROM PUBLIC, tft_app, tft_worker;
GRANT SELECT ON tft.document_diagrams TO tft_app;
GRANT SELECT, INSERT ON tft.document_diagrams TO tft_worker;
COMMIT;
