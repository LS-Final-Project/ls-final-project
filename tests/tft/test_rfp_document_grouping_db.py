"""Pinned local registration repair: preserve identities, refuse any analyzed/owned work."""

import os
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

from scripts.local_business.group_rfp_documents import DOCUMENTS, OLD_NAMES, plan, sql

ACTOR = UUID("00000000-0000-4000-8000-000000000003")


@pytest.fixture
def db():
    dsn = os.getenv("AUTH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("전용 PostgreSQL 검사는 AUTH_TEST_DATABASE_URL이 필요합니다.")
    conn = psycopg.connect(dsn, row_factory=dict_row)
    assert conn.info.dbname == "kan77_auth_test"
    suffix = uuid4().hex[:8]
    schemas = {name: f"group_{name}_{suffix}" for name in ("tft", "runtime", "identity")}

    def rewrite(text):
        for before, after in schemas.items():
            text = text.replace(before + ".", after + ".").replace(
                "'" + before + "'", "'" + after + "'"
            )
        return text

    for name in schemas.values():
        conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(name)))
    conn.execute(
        rewrite("""
        CREATE TABLE identity.users(id uuid PRIMARY KEY);
        CREATE TABLE tft.projects(id uuid PRIMARY KEY,name text,company_code text,external_id text,
          version int DEFAULT 1,source text DEFAULT 'rfp',source_instance text DEFAULT 'web',
          data_origin text DEFAULT 'demo',output_status text DEFAULT 'INTAKE',review_version int,
          responsible_user_id uuid,review_departments text[] DEFAULT '{}',sales_user_id uuid,
          updated_at timestamptz DEFAULT now(),known_at timestamptz DEFAULT now());
        CREATE TABLE tft.documents(id uuid PRIMARY KEY,project_id uuid REFERENCES tft.projects,
          latest_version int DEFAULT 1,kind text DEFAULT 'RFP_SPEC',title text,base_document_id uuid,
          data_origin text DEFAULT 'demo');
        CREATE TABLE tft.document_versions(id uuid PRIMARY KEY,document_id uuid REFERENCES tft.documents,
          version int DEFAULT 1,sha256 text,size bigint,storage_key text,data_origin text DEFAULT 'demo');
        CREATE TABLE tft.rfp_commands(project_id uuid,operation text,result jsonb);
        CREATE TABLE tft.rfp_audit_events(id uuid PRIMARY KEY,project_id uuid,entity text,
          entity_id text,action text,actor_user_id uuid,detail jsonb);
        CREATE TABLE tft.requirements(id uuid,project_id uuid REFERENCES tft.projects);
        CREATE TABLE tft.clauses(id uuid,document_version_id uuid REFERENCES tft.document_versions);
        CREATE TABLE identity.manager_projects(user_id uuid,project_id uuid);
        CREATE TABLE runtime.jobs(id uuid,subject_id uuid,business_id uuid,status text);
        CREATE TABLE runtime.job_control(lease_expires_at timestamptz);
    """)
    )
    conn.execute(rewrite("INSERT INTO identity.users VALUES (%s)"), (ACTOR,))
    for code, pid, did, vid, sha, size in DOCUMENTS:
        conn.execute(
            rewrite(
                "INSERT INTO tft.projects(id,name,company_code,external_id,sales_user_id) VALUES(%s,%s,%s,%s,%s)"
            ),
            (pid, OLD_NAMES[code], "KAN159-RFP-" + code, f"P26-00{code[1:]}", ACTOR),
        )
        conn.execute(
            rewrite("INSERT INTO tft.documents(id,project_id,title) VALUES(%s,%s,%s)"),
            (did, pid, code + " 합성 시험 문서"),
        )
        conn.execute(
            rewrite(
                "INSERT INTO tft.document_versions(id,document_id,sha256,size,storage_key) VALUES(%s,%s,%s,%s,%s)"
            ),
            (vid, did, sha, size, f"rfp/{pid}/{vid}"),
        )
        conn.execute(
            rewrite("INSERT INTO tft.rfp_commands VALUES(%s,'document.upload','{}')"), (pid,)
        )
        conn.execute(
            rewrite(
                "INSERT INTO tft.rfp_audit_events VALUES(%s,%s,'document',%s,'document.upload',%s,'{}')"
            ),
            (uuid4(), pid, did, ACTOR),
        )
    try:
        yield conn, rewrite
    finally:
        conn.rollback()
        conn.close()


def body(rewrite, *, actor=ACTOR):
    # Caller-owned transaction keeps every fixture row isolated and rolled back.
    value = sql(actor=actor).removeprefix("BEGIN;\n").removesuffix("COMMIT;\n")
    return rewrite(value)


def rows(conn, rewrite, table):
    return conn.execute(rewrite(f"SELECT * FROM {table} ORDER BY 1")).fetchall()


def test_moves_only_document_ownership_and_preserves_original_history(db):
    conn, rewrite = db
    before_versions = rows(conn, rewrite, "tft.document_versions")
    before_documents = rows(conn, rewrite, "tft.documents")
    before_audit = rows(conn, rewrite, "tft.rfp_audit_events")
    before_commands = rows(conn, rewrite, "tft.rfp_commands")
    conn.execute(body(rewrite))
    assert rows(conn, rewrite, "tft.document_versions") == before_versions
    assert rows(conn, rewrite, "tft.rfp_commands") == before_commands
    mapping = {UUID(str(item["document_id"])): UUID(str(item["destination"])) for item in plan()}
    assert rows(conn, rewrite, "tft.documents") == [
        dict(row, project_id=mapping[row["id"]]) for row in before_documents
    ]
    audit = rows(conn, rewrite, "tft.rfp_audit_events")
    assert len(audit) == 10 and all(row in audit for row in before_audit)
    projects = rows(conn, rewrite, "tft.projects")
    assert len(projects) == 5 and all(row["version"] == 2 for row in projects)
    grouped = conn.execute(
        rewrite("SELECT project_id,count(*) AS n FROM tft.documents GROUP BY project_id ORDER BY n")
    ).fetchall()
    assert [row["n"] for row in grouped] == [2, 3]
    with pytest.raises(psycopg.errors.RaiseException, match="GROUP_PIN_MISMATCH"):
        conn.execute(body(rewrite))


@pytest.mark.parametrize(
    "change,reason",
    [
        (
            "INSERT INTO tft.requirements VALUES(gen_random_uuid(),'{project}')",
            "GROUP_EXISTING_RECORDS",
        ),
        (
            "INSERT INTO tft.clauses VALUES(gen_random_uuid(),'{version}')",
            "GROUP_DOCUMENT_ALREADY_USED",
        ),
        (
            "INSERT INTO identity.manager_projects VALUES(gen_random_uuid(),'{project}')",
            "GROUP_EXISTING_RECORDS",
        ),
        (
            "INSERT INTO runtime.jobs VALUES(gen_random_uuid(),'{project}','{project}','SUCCEEDED')",
            "GROUP_JOBS_EXIST",
        ),
        (
            "INSERT INTO runtime.jobs VALUES(gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),'QUEUED')",
            "GROUP_JOBS_EXIST",
        ),
        ("INSERT INTO runtime.job_control VALUES(now()+interval '1 hour')", "GROUP_JOBS_EXIST"),
        (
            "UPDATE tft.document_versions SET sha256=repeat('0',64) WHERE id='{version}'",
            "GROUP_PIN_MISMATCH",
        ),
        (
            "UPDATE tft.projects SET review_departments=ARRAY['TS_TRF'] WHERE id='{project}'",
            "GROUP_PROJECT_CONFIG_MISMATCH",
        ),
        (
            "INSERT INTO tft.rfp_commands VALUES('{project}','rfp.process','{}')",
            "GROUP_NON_REGISTRATION_HISTORY",
        ),
    ],
)
def test_refuses_any_work_or_changed_identity_before_mutation(db, change, reason):
    conn, rewrite = db
    _, pid, _, vid, _, _ = DOCUMENTS[0]
    conn.execute(rewrite(change.replace("{project}", pid).replace("{version}", vid)))
    before = rows(conn, rewrite, "tft.documents")
    with pytest.raises(psycopg.errors.RaiseException, match=reason), conn.transaction():
        conn.execute(body(rewrite))
    assert rows(conn, rewrite, "tft.documents") == before


def test_unknown_future_project_record_blocks_grouping(db):
    conn, rewrite = db
    conn.execute(rewrite("CREATE TABLE tft.future_reviews(project_id uuid)"))
    conn.execute(rewrite("INSERT INTO tft.future_reviews VALUES(%s)"), (DOCUMENTS[0][1],))
    with pytest.raises(psycopg.errors.RaiseException, match="GROUP_EXISTING_RECORDS"):
        conn.execute(body(rewrite))


def test_sql_default_is_read_only_and_never_contains_updates():
    emitted = sql()
    assert "SET TRANSACTION READ ONLY" in emitted
    assert "UPDATE tft." not in emitted and "INSERT INTO" not in emitted
    assert len(plan()) == 5
