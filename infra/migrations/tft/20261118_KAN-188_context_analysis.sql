-- Immutable grouping/classification candidates; never updates original requirements or decisions.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
CREATE TABLE IF NOT EXISTS tft.context_versions (
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    version integer NOT NULL CHECK (version > 0),
    source_job_id uuid UNIQUE,
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    source_snapshot jsonb NOT NULL CHECK (jsonb_typeof(source_snapshot)='object' AND octet_length(source_snapshot::text)<=33554432),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object' AND coalesce(payload->>'schema_version'='rfp-context-v1',false) AND octet_length(payload::text)<=16777216),
    provenance text NOT NULL CHECK (provenance IN ('ai_draft','human_input')),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    reason text CHECK (length(reason) BETWEEN 1 AND 2000),
    request_key text CHECK (length(request_key) BETWEEN 1 AND 200),
    request_hash text CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (project_id,version),
    UNIQUE (project_id,created_by,request_key),
    CHECK ((request_key IS NULL) = (request_hash IS NULL)),
    CHECK ((provenance='ai_draft' AND source_job_id IS NOT NULL AND request_key IS NULL)
        OR (provenance='human_input' AND source_job_id IS NULL AND request_key IS NOT NULL AND reason IS NOT NULL))
);
-- Reapply the same strict constraint when an earlier disposable fixture used this table.
ALTER TABLE tft.context_versions DROP CONSTRAINT IF EXISTS context_versions_payload_check;
ALTER TABLE tft.context_versions ADD CONSTRAINT context_versions_payload_check CHECK (jsonb_typeof(payload)='object' AND coalesce(payload->>'schema_version'='rfp-context-v1',false) AND octet_length(payload::text)<=16777216);
CREATE TABLE IF NOT EXISTS tft.review_context_snapshots (
    project_id uuid NOT NULL,
    review_version integer NOT NULL,
    context_version integer,
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=50331648),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id,review_version),
    FOREIGN KEY (project_id,review_version) REFERENCES tft.review_versions(project_id,review_version),
    FOREIGN KEY (project_id,context_version) REFERENCES tft.context_versions(project_id,version)
);
REVOKE ALL ON tft.context_versions,tft.review_context_snapshots FROM PUBLIC,tft_app,tft_worker;
GRANT SELECT,INSERT ON tft.context_versions TO tft_app,tft_worker;
GRANT SELECT,INSERT ON tft.review_context_snapshots TO tft_app;
GRANT SELECT ON tft.review_context_snapshots TO tft_worker;
COMMIT;
