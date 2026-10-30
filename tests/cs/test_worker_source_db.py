"""KAN-107 worker 문의 출처(backend/api/cs/worker_source.py)를 전용 PostgreSQL·cs_worker 로그인으로 확인한다.

모델은 가짜 공급자이고 행은 합성이다. 실제 체크포인터(PostgresSaver, checkpoint_cs)로 그래프를 돌려 cs_worker
권한만으로 문의를 읽고, 결과를 저장하고, 사람 확인 직전에 멈추는지 본다. 작업 인수·lease는 KAN-194 검사
(tests/platform/test_cs_worker_db.py)가 맡는다.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr

from backend.agent.ticket_source_registry import policy_info_codes
from backend.api.cs import worker_source
from backend.engine.cs.classification_call import ClassificationAttempt
from backend.engine.cs.graph import HUMAN_REVIEW, build_graph, initial_state, thread_config
from backend.shared.ai import Usage
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from tests.cs.graph_fakes import FILTERS, classification_provider, draft_provider, fts_searcher

RECEIVED = datetime(2026, 10, 29, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def worker_dsn(cs_database):
    """cs_worker만 상속하는 검사 로그인."""
    with psycopg.connect(cs_database, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='test_cs_source'").fetchone():
            conn.execute("CREATE ROLE test_cs_source LOGIN PASSWORD 'test-only'")
        conn.execute("GRANT cs_worker TO test_cs_source")
    return make_conninfo(cs_database, user="test_cs_source", password="test-only")


@pytest.fixture
def ticket(admin):
    ticket_id = uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, subject, body) VALUES (%s, 'test', 'kan107w', %s, 'synthetic', 'PROVIDED', "
        "%s, %s, 'EMAIL', 'en', 'CUST-0374', 'TRF-OIL-1000', 'Oil leak', 'Oil leakage at gasket.')",
        (ticket_id, f"T-{ticket_id}", RECEIVED, RECEIVED),
    )
    admin.execute(
        "INSERT INTO cs.ticket_source_records (ticket_id, data_origin, customer_tier) "
        "VALUES (%s, 'synthetic', 'GOLD')",
        (ticket_id,),
    )
    return ticket_id


def add_send(admin, ticket_id, version, codes, sent):
    """고객 메시지 하나. sent=False면 승인만 되고 나가지 않은 메시지다."""
    admin.execute(
        "INSERT INTO cs.ticket_customer_sends (id, ticket_id, kind, workflow_version, mailbox_id, "
        "subject, body_type, body, to_addresses, payload_sha256, approved_by, approved_at, "
        "requested_info_codes, recorded_at) VALUES (%s, %s, 'INFO_REQUEST', %s, %s, 'Re', 'text', "
        "'Please send', '[]', %s, %s, now(), %s, %s)",
        (
            uuid4(),
            ticket_id,
            version,
            uuid4(),
            "0" * 64,
            uuid4(),
            psycopg.types.json.Jsonb(list(codes)),
            datetime.now(UTC) if sent else None,
        ),
    )


def test_the_ticket_is_read_with_the_policy_map_and_only_sent_questions(worker_dsn, admin, ticket):
    add_send(admin, ticket, 1, ["serial_no"], sent=True)
    # Approved but never sent: the customer was not asked yet.
    add_send(admin, ticket, 2, ["photos"], sent=False)
    inputs = worker_source.DbTicketSource(worker_dsn, uuid4()).load_ticket(str(ticket))
    assert inputs.record == {
        "channel": "EMAIL",
        "body_language": "en",
        "subject": "Oil leak",
        "body": "Oil leakage at gasket.",
    }
    assert (inputs.product_code, inputs.customer_tier, inputs.received_at) == (
        "TRF-OIL-1000",
        "GOLD",
        RECEIVED,
    )
    assert inputs.already_asked_codes == frozenset({"serial_no"})
    assert inputs.sensitive_values == {}
    # The transformer rows of the policy table, per category; no answer label is needed.
    expected = worker_source.required_by_family()["TRANSFORMER"]
    assert inputs.required_info_by_category == expected and "WARRANTY" in expected
    with pytest.raises(ApplicationError) as missing:
        worker_source.DbTicketSource(worker_dsn, None).load_ticket(str(uuid4()))
    assert missing.value.code == "CS_TICKET_NOT_FOUND"


def test_a_ticket_without_a_source_record_has_no_tier(worker_dsn, admin, ticket):
    admin.execute("DELETE FROM cs.ticket_source_records WHERE ticket_id=%s", (ticket,))
    inputs = worker_source.DbTicketSource(worker_dsn, None).load_ticket(str(ticket))
    assert inputs.customer_tier == ""


def test_the_graph_runs_on_the_worker_login_and_stops_before_human_review(
    worker_dsn, admin, ticket
):
    add_send(admin, ticket, 1, ["serial_no"], sent=True)
    job = uuid4()
    source = worker_source.DbTicketSource(worker_dsn, job)
    with PostgresSaver.from_conn_string(
        worker_dsn + " options='-c search_path=checkpoint_cs'"
    ) as saver:
        graph = build_graph(
            classification_provider=classification_provider,
            draft_provider=draft_provider,
            fts_searcher=fts_searcher,
            search_filters=FILTERS,
            allowed_info_codes=policy_info_codes(),
            load_ticket=source.load_ticket,
            record_attempt=source.record_attempt,
            checkpointer=saver,
        )
        config = thread_config(str(job))
        graph.invoke(initial_state(str(ticket)), config)
        state = graph.get_state(config)
    assert state.next == (HUMAN_REVIEW,)
    runs = admin.execute(
        "SELECT id, job_id, outcome, classification->>'category_code', provenance_kind "
        "FROM cs.ticket_analyses WHERE ticket_id=%s",
        (ticket,),
    ).fetchall()
    assert [(r[1], r[2], r[3], r[4]) for r in runs] == [(job, "VALID", "WARRANTY", "ai_draft")]
    draft = admin.execute(
        "SELECT analysis_id, job_id, outcome, draft FROM cs.ticket_drafts WHERE ticket_id=%s",
        (ticket,),
    ).fetchone()
    assert (draft[0], draft[1], draft[2]) == (runs[0][0], job, "VALID")
    asked = {
        r["info_code"]
        for o in draft[3]["options"]
        if o["label"] == "INFO_FIRST"
        for r in o["info_requests"]
    }
    # The question already sent is not asked again; the rest of the warranty row is.
    assert "serial_no" not in asked and asked
    assert asked <= worker_source.required_by_family()["TRANSFORMER"]["WARRANTY"]
    # Graph state lives in the checkpoint only; the workflow is untouched.
    assert (
        admin.execute(
            "SELECT count(*) FROM checkpoint_cs.checkpoints WHERE thread_id=%s", (str(job),)
        ).fetchone()[0]
        > 0
    )
    assert admin.execute(
        "SELECT count(*) FROM cs.ticket_workflows WHERE ticket_id=%s", (ticket,)
    ).fetchone() == (0,)
    # Nothing from the raw model answer or the body is stored with the run.
    stored = admin.execute(
        "SELECT row_to_json(a)::text FROM cs.ticket_analyses a WHERE ticket_id=%s", (ticket,)
    ).fetchone()[0]
    assert "Oil leakage" not in stored and "       " not in stored
    # Usage per attempt (KAN-107). The fake model has no price, so cost stays null.
    usage = (
        "SELECT input_tokens, output_tokens, latency_ms IS NOT NULL, cost_usd, pricing_version "
        "FROM cs.{} WHERE ticket_id=%s"
    )
    assert admin.execute(usage.format("ticket_analyses"), (ticket,)).fetchone() == (
        10,
        2,
        True,
        None,
        None,
    )
    assert admin.execute(usage.format("ticket_drafts"), (ticket,)).fetchone() == (
        20,
        5,
        True,
        None,
        None,
    )
    assert admin.execute(
        "SELECT prompt_version FROM cs.ticket_drafts WHERE ticket_id=%s", (ticket,)
    ).fetchone() == ("v1",)


def test_a_priced_attempt_stores_its_cost_and_price_version(worker_dsn, ticket):
    attempt = ClassificationAttempt(
        attempt_number=1,
        outcome="TRANSPORT_FAILURE",
        latency_ms=12.6,
        usage=Usage(input_tokens=1_000_000, output_tokens=0, total_tokens=1_000_000),
        requested_model="gemini-3.6-flash",
        prompt_version="v3",
        error_code="CS_TRANSPORT",
    )
    source = worker_source.DbTicketSource(worker_dsn, None)
    run_id = source.record_attempt("classification", str(ticket), attempt)
    with psycopg.connect(worker_dsn) as conn:
        row = conn.execute(
            "SELECT prompt_version, latency_ms, cost_usd, pricing_version "
            "FROM cs.ticket_analyses WHERE id=%s",
            (run_id,),
        ).fetchone()
    assert row == ("v3", 13, Decimal("0.750000"), "gemini-3.6-flash@2026-10-28")


@pytest.mark.parametrize("table", ["ticket_analyses", "ticket_drafts"])
def test_cost_without_a_price_version_is_refused(admin, ticket, table):
    seq = "run_seq" if table == "ticket_analyses" else "seq"
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        admin.execute(
            f"INSERT INTO cs.{table} (id, ticket_id, {seq}, outcome, input_version, cost_usd) "
            "VALUES (%s, %s, 99, 'HELD', 'x', 0.1)",
            (uuid4(), ticket),
        )
    assert refused.value.diag.constraint_name == f"{table}_cost_needs_pricing"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT customer_external_id FROM cs.tickets LIMIT 1",
        "SELECT category_code FROM cs.ticket_source_records LIMIT 1",
        "SELECT body FROM cs.ticket_customer_sends LIMIT 1",
        "SELECT * FROM cs.ticket_classification_corrections LIMIT 1",
        "SELECT * FROM cs.ticket_workflows LIMIT 1",
        "UPDATE cs.ticket_analyses SET error_code = 'x'",
        "DELETE FROM cs.ticket_drafts",
        "UPDATE cs.tickets SET body = 'x'",
        "SELECT 1 FROM cs.tickets FOR UPDATE",
        "DELETE FROM checkpoint_cs.checkpoints",
    ],
)
def test_the_worker_login_reads_and_writes_only_what_the_graph_needs(worker_dsn, sql):
    with psycopg.connect(worker_dsn, row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql)


def test_build_reads_the_job_id_of_the_worker(monkeypatch, worker_dsn):
    job = uuid4()
    monkeypatch.setenv("CS_JOB_ID", str(job))
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(worker_dsn))
    parts = worker_source.build(settings)
    assert isinstance(parts.ticket_source, worker_source.DbTicketSource)
    assert parts.ticket_source.job_id == job
    assert parts.allowed_info_codes is None and parts.search_filters is None
