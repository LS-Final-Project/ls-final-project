"""KAN-188: emit guarded SQL for the five confirmed, unprocessed local RFP documents.

This is an operator repair, not an application merge API. It never connects to a database,
reads credentials, runs models or edits files. Default output is a read-only preflight.
Apply output must be reviewed and run only after the existing idle/drain/backup procedure.
"""

import argparse
import json
from uuid import UUID

DOCUMENTS = (
    (
        "D10",
        "f51dab2c-0be6-4b26-b8ad-008dd8bfdcf8",
        "0ab600f9-fadd-4cf3-89f8-a94bef2a30ad",
        "84ed75d8-733f-4920-8032-ef674465eb45",
        "73417adf6749bc76e54d4ec831dfe761b6a016c16c1d580d280154eb7995c6af",
        73506,
    ),
    (
        "D12",
        "8eacbeac-30f7-42d3-9b91-5daf94c1a59c",
        "fb2f12a3-62ba-44e3-a007-5585a59fcad3",
        "569c5f6e-fd1e-4e2d-8af7-ebdcb18083c4",
        "f1797dc3be97d8b6793465a6b3793f51a08d324bdcccc319f23cb9f55057fc16",
        59797,
    ),
    (
        "D14",
        "4b8680ac-19e2-48a6-8507-22c53992d96d",
        "d74ccf38-9782-45fd-890c-7ff92ec0e45c",
        "8d24f27f-92ce-4486-94be-3ffa7fcde177",
        "e73a2eec4c848b68a758636009abfca0054029b0236d97ee0b126e3c25d57945",
        74556,
    ),
    (
        "D15",
        "eef08248-5f03-402f-8537-220145217fa4",
        "20cb22cb-5850-49fe-9c4e-44af0e2b0888",
        "a317b42c-1a07-4a68-a8f3-11da7bca00d5",
        "f7bf68f8b4aa6ce481e1c381d62689b99d029c5ea31381ec0b17b0b25296c1e1",
        74506,
    ),
    (
        "D17",
        "b22a725f-96a0-4239-988c-ed75f58e2437",
        "e3b6050c-a73a-468c-b58c-1574879c1113",
        "ebef5841-1e80-46d7-b967-221733ea3742",
        "7f93aa2ac447689e88c3f606d927e8ec39a735ed35e4a2b801e7ade20eb39be0",
        57010,
    ),
)
DESTINATIONS = {"D10": "D10", "D12": "D10", "D14": "D14", "D15": "D14", "D17": "D14"}
NAMES = {
    "D10": "Project Alpha · D10·D12 [평가·등록조회전용]",
    "D12": "D12 문서 통합 → P26-0010 Project Alpha [이전 등록]",
    "D14": "프로젝트 2300096 · D14·D15·D17 [개발·시연]",
    "D15": "D15 문서 통합 → P26-0014 프로젝트 2300096 [이전 등록]",
    "D17": "D17 문서 통합 → P26-0014 프로젝트 2300096 [이전 등록]",
}
OLD_NAMES = {
    "D10": "KAN159 교육원문 D10 [평가-등록조회전용]",
    "D12": "KAN159 교육원문 D12 [평가-등록조회전용]",
    "D14": "KAN159 교육원문 D14 [개발-교육원문]",
    "D15": "KAN159 교육원문 D15 [교육원문-분할미지정]",
    "D17": "KAN159 교육원문 D17 [개발-교육원문]",
}


def plan() -> list[dict[str, str | int]]:
    """Fixed document identities and checksums; never infer groups from similar names."""
    projects = {row[0]: row[1] for row in DOCUMENTS}
    return [
        dict(
            code=code,
            project_id=pid,
            document_id=did,
            version_id=vid,
            sha256=sha,
            size=size,
            destination=projects[DESTINATIONS[code]],
            name=NAMES[code],
            old_name=OLD_NAMES[code],
            external_id=f"P26-00{code[1:]}",
        )
        for code, pid, did, vid, sha, size in DOCUMENTS
    ]


GUARDS = r"""
DECLARE
    plans jsonb := __PLAN__;
    project_ids uuid[]; document_ids uuid[]; version_ids uuid[];
    item jsonb; ref record; n bigint; config_count bigint;
BEGIN
    SELECT array_agg((x->>'project_id')::uuid), array_agg((x->>'document_id')::uuid),
           array_agg((x->>'version_id')::uuid)
      INTO project_ids, document_ids, version_ids FROM jsonb_array_elements(plans) x;
    -- Project rows differ only in registration identifiers and timestamps.
    SELECT count(DISTINCT (to_jsonb(p) - ARRAY['id','name','company_code','external_id',
                         'known_at','created_at','updated_at','ingested_at']))
      INTO config_count FROM tft.projects p WHERE id=ANY(project_ids);
    IF config_count <> 1 THEN RAISE EXCEPTION 'GROUP_PROJECT_CONFIG_MISMATCH'; END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(plans) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM tft.projects p JOIN tft.documents d ON d.project_id=p.id
              JOIN tft.document_versions v ON v.document_id=d.id
            WHERE p.id=(item->>'project_id')::uuid AND p.name=item->>'old_name'
              AND p.external_id=item->>'external_id' AND p.company_code='KAN159-RFP-'||(item->>'code')
              AND p.version=1 AND p.source='rfp' AND p.source_instance='web'
              AND p.data_origin='demo' AND p.output_status='INTAKE' AND p.review_version IS NULL
              AND p.responsible_user_id IS NULL AND cardinality(p.review_departments)=0
              AND d.id=(item->>'document_id')::uuid AND d.latest_version=1
              AND d.kind='RFP_SPEC' AND d.title LIKE (item->>'code')||' %'
              AND d.base_document_id IS NULL AND d.data_origin='demo'
              AND v.id=(item->>'version_id')::uuid AND v.version=1
              AND v.sha256=item->>'sha256' AND v.size=(item->>'size')::bigint
              AND v.storage_key='rfp/'||(item->>'project_id')||'/'||(item->>'version_id')
              AND v.data_origin='demo'
        ) THEN RAISE EXCEPTION 'GROUP_PIN_MISMATCH: %', item->>'code'; END IF;
    END LOOP;
    IF (SELECT count(*) FROM tft.documents WHERE project_id=ANY(project_ids))<>5
       OR (SELECT count(*) FROM tft.document_versions WHERE document_id=ANY(document_ids))<>5
    THEN RAISE EXCEPTION 'GROUP_EXTRA_DOCUMENT_OR_VERSION'; END IF;
    -- Refuse every project-owned record except immutable original registration history.
    -- Unknown future tables with project_id also fail closed when populated.
    FOR ref IN SELECT table_schema,table_name,column_name FROM information_schema.columns
      WHERE column_name='project_id' AND table_schema NOT IN ('pg_catalog','information_schema')
        AND NOT (table_schema='tft' AND table_name IN ('documents','rfp_commands','rfp_audit_events'))
    LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I WHERE %I=ANY($1)',
                       ref.table_schema,ref.table_name,ref.column_name) INTO n USING project_ids;
        IF n<>0 THEN RAISE EXCEPTION 'GROUP_EXISTING_RECORDS: %.%', ref.table_schema,ref.table_name; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM tft.rfp_commands WHERE project_id=ANY(project_ids)
              AND operation NOT IN ('project.create','document.upload'))
       OR EXISTS(SELECT 1 FROM tft.rfp_audit_events WHERE project_id=ANY(project_ids)
                 AND action NOT IN ('project.create','document.upload'))
    THEN RAISE EXCEPTION 'GROUP_NON_REGISTRATION_HISTORY'; END IF;
    -- A document with parsed clauses, diagram output, annotations or other references is not movable.
    FOR ref IN SELECT table_schema,table_name,column_name FROM information_schema.columns
      WHERE column_name IN ('document_id','document_version_id','base_document_id')
        AND table_schema NOT IN ('pg_catalog','information_schema')
        AND NOT (table_schema='tft' AND table_name='document_versions' AND column_name='document_id')
    LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I WHERE %I=ANY($1)',
                       ref.table_schema,ref.table_name,ref.column_name) INTO n
        USING CASE WHEN ref.column_name='document_version_id' THEN version_ids ELSE document_ids END;
        IF n<>0 THEN RAISE EXCEPTION 'GROUP_DOCUMENT_ALREADY_USED: %.%', ref.table_schema,ref.table_name; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM runtime.jobs WHERE status IN ('QUEUED','RUNNING'))
       OR EXISTS(SELECT 1 FROM runtime.job_control WHERE lease_expires_at>now())
       OR EXISTS(SELECT 1 FROM runtime.jobs WHERE subject_id=ANY(project_ids)
                 OR business_id=ANY(project_ids))
    THEN RAISE EXCEPTION 'GROUP_JOBS_EXIST'; END IF;
END
"""

MUTATE = r"""
DECLARE
    plans jsonb := __PLAN__;
    item jsonb; before_project jsonb; actor uuid := '__ACTOR__';
BEGIN
    IF NOT EXISTS(SELECT 1 FROM identity.users WHERE id=actor)
       OR EXISTS(SELECT 1 FROM tft.projects WHERE id IN
                    (SELECT (x->>'project_id')::uuid FROM jsonb_array_elements(plans) x)
                 AND sales_user_id IS DISTINCT FROM actor)
    THEN RAISE EXCEPTION 'GROUP_ACTOR_MISMATCH'; END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(plans) LOOP
        SELECT to_jsonb(p) INTO before_project FROM tft.projects p WHERE id=(item->>'project_id')::uuid;
        UPDATE tft.documents SET project_id=(item->>'destination')::uuid
          WHERE id=(item->>'document_id')::uuid AND project_id=(item->>'project_id')::uuid;
        UPDATE tft.projects SET name=item->>'name',version=version+1,updated_at=now()
          WHERE id=(item->>'project_id')::uuid;
        INSERT INTO tft.rfp_audit_events(id,project_id,entity,entity_id,action,actor_user_id,detail)
        VALUES(gen_random_uuid(),(item->>'project_id')::uuid,'project',item->>'project_id',
               'project.documents_grouped',actor,
               jsonb_build_object('ticket','KAN-188','before_project',before_project,
                                  'mapping',item,'models_called',false,'original_files_changed',false));
    END LOOP;
END
"""


def sql(*, actor: UUID | None = None) -> str:
    """Read-only check by default; explicit actor emits one atomic write transaction."""
    literal = "'" + json.dumps(plan(), ensure_ascii=False).replace("'", "''") + "'::jsonb"
    guard = "DO $guard$\n" + GUARDS.replace("__PLAN__", literal) + "$guard$;\n"
    start = "BEGIN;\nSET LOCAL lock_timeout='5s';\nSET LOCAL statement_timeout='30s';\n"
    if actor is None:
        return start + "SET TRANSACTION READ ONLY;\n" + guard + "COMMIT;\n"
    # Lock the whole relevant namespace while running all guards again. The operator must
    # already have drained HTTP entrypoints/driver; locks defend against a late database writer.
    locks = """DO $locks$ DECLARE r record; BEGIN
      FOR r IN SELECT schemaname,tablename FROM pg_tables
        WHERE schemaname IN ('tft','runtime','identity') ORDER BY schemaname,tablename
      LOOP EXECUTE format('LOCK TABLE %I.%I IN SHARE ROW EXCLUSIVE MODE',r.schemaname,r.tablename); END LOOP;
    END $locks$;\n"""
    mutate = (
        "DO $mutate$\n"
        + MUTATE.replace("__PLAN__", literal).replace("__ACTOR__", str(actor))
        + "$mutate$;\n"
    )
    return start + locks + guard + mutate + "COMMIT;\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-sql-for-actor", type=UUID, help="Emit write SQL, never execute it."
    )
    args = parser.parse_args()
    print(sql(actor=args.write_sql_for_actor), end="")


if __name__ == "__main__":
    main()
