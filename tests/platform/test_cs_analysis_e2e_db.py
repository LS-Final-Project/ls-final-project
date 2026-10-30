"""KAN-107 C/S AI 분석 종단: C/S 접수 → 플랫폼 작업 → worker → 결과 조회 → 고객 발송 접수 → 그래프 재개.

전용 PostgreSQL 하나에 platform·cs 마이그레이션을 두고, 실제 권한(cs_app·platform_app·cs_worker 로그인)과 실제
체크포인터(checkpoint_cs)로 돈다. 모델·검색·메일 제공자는 가짜이고 행은 합성이다. 두 서비스 사이 HTTP는 같은
프로세스의 ASGI 전송, ID 토큰은 합성 검증기로 대신한다(운영 ID 토큰·Cloud Run Jobs 전송은 KAN-159 배포 묶음).
"""

from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from pydantic import SecretStr

from backend.agent.cs_worker import execute_job
from backend.api.cs.analysis.jobs import CsAnalysisSettings, configure_analysis_jobs
from backend.api.cs.main import create_app as create_cs_app
from backend.api.cs.workflow.mail import CustomerMailSettings, configure_customer_mail
from backend.api.platform.jobs.cs import configure_cs_jobs
from backend.api.platform.main import create_app as create_platform_app
from backend.engine.cs.graph import HUMAN_REVIEW, build_graph, thread_config
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings
from tests.cs.graph_fakes import FILTERS, classification_provider, draft_provider, fts_searcher
from tests.cs.test_customer_mail_db import PLATFORM, FakePlatform, last_send, send
from tests.cs.test_workflow_db import act, ok
from tests.platform.test_cs_jobs_db import FakeCaller, enabled
from tests.platform.test_cs_worker_db import WORKER
from tests.platform.test_cs_worker_db import worker_settings as worker_settings

CS_MIGRATIONS = sorted(Path("infra/migrations/cs").glob("*.sql"))


@pytest.fixture
def cs_tables(jobs_database):
    """같은 DB에 C/S 마이그레이션 전부(재실행 가능)를 적용한다. cs 검사 conftest와 같은 준비다."""
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        for role in ("cs_migrator", "cs_app"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(role)))
        conn.execute("ALTER ROLE cs_app LOGIN PASSWORD 'test-only'")
        conn.execute("CREATE SCHEMA IF NOT EXISTS cs AUTHORIZATION cs_migrator")
        conn.execute("GRANT USAGE ON SCHEMA cs TO cs_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE cs_migrator IN SCHEMA cs "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO cs_app"
        )
        for migration in CS_MIGRATIONS:
            conn.execute(
                "\n".join(x for x in migration.read_text().splitlines() if not x.startswith("\\"))
            )
    return make_conninfo(jobs_database, user="cs_app", password="test-only")


@pytest.fixture
def ticket(jobs_database, cs_tables):
    ticket_id = uuid4()
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
            "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
            "product_code, subject, body) VALUES (%s, 'test', 'kan107e2e', %s, 'synthetic', "
            "'PROVIDED', now(), now(), 'EMAIL', 'en', 'CUST-0374', 'TRF-OIL-1000', 'Oil leak', "
            "'Oil leakage at gasket.')",
            (ticket_id, f"T-{ticket_id}"),
        )
        conn.execute(
            "INSERT INTO cs.ticket_source_records (ticket_id, data_origin, customer_tier) "
            "VALUES (%s, 'synthetic', 'GOLD')",
            (ticket_id,),
        )
    return ticket_id


@pytest.fixture
def agent_jobs(jobs_database, registered_user):
    """이 검사 사용자의 작업 행을 사용자보다 먼저 지운다(작업이 사용자를 참조한다)."""
    yield registered_user
    with psycopg.connect(jobs_database) as conn:
        mine = "SELECT id FROM runtime.jobs WHERE actor_id=%s"
        for table in ("job_control", "dispatch_attempts", "dispatch_outbox", "job_events"):
            conn.execute(
                f"DELETE FROM runtime.{table} WHERE job_id IN ({mine})", (registered_user[1],)
            )
        conn.execute("DELETE FROM runtime.jobs WHERE actor_id=%s", (registered_user[1],))


def fake_build(_settings, parts, saver):
    """실제 문의 출처(worker_source)·정책 입력·체크포인터에 가짜 모델·검색만 꽂는다."""
    return build_graph(
        classification_provider=classification_provider,
        draft_provider=draft_provider,
        fts_searcher=fts_searcher,
        search_filters=FILTERS,
        allowed_info_codes=parts.allowed_info_codes,
        load_ticket=parts.ticket_source.load_ticket,
        record_attempt=parts.ticket_source.record_attempt,
        checkpointer=saver,
    )


def test_an_analysis_runs_end_to_end_and_ends_after_the_send(
    monkeypatch, settings, worker_settings, jobs_database, cs_tables, agent_jobs, ticket
):
    agent = Principal(user_id=agent_jobs[1], roles={"cs_agent"})
    platform_app = create_platform_app(settings=settings)
    configure_cs_jobs(platform_app, enabled(), caller=FakeCaller())
    cs_settings = Settings(_env_file=None, environment="test", database_url=SecretStr(cs_tables))
    cs_app = create_cs_app(cs_settings)
    mail = FakePlatform()
    configure_customer_mail(
        cs_app,
        CustomerMailSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "cs-token",
        transport=httpx.MockTransport(mail.handler),
    )
    configure_analysis_jobs(
        cs_app,
        CsAnalysisSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "good-caller",
        transport=httpx.ASGITransport(app=platform_app),
    )
    with TestClient(cs_app, headers=principal_header(agent)) as client:
        # 1. The agent asks; the platform stores a queued job for this ticket.
        response = client.post(
            f"/api/v1/cs/tickets/{ticket}/analysis",
            headers={"Idempotency-Key": "e2e-analysis-0001"},
        )
        assert response.status_code == 202, response.text
        job = UUID(response.json()["data"]["job_id"])
        with psycopg.connect(jobs_database) as conn:
            assert conn.execute(
                "SELECT owner, kind, subject_id, status FROM runtime.jobs WHERE id=%s", (job,)
            ).fetchone() == ("cs", "cs.analyze", ticket, "QUEUED")

        # 2. The worker (cs_worker login, real ticket source and checkpointer) runs it.
        monkeypatch.setenv("CS_JOB_ID", str(job))
        assert execute_job(worker_settings, WORKER, job, build=fake_build) == "CS_JOB_SUCCEEDED"
        with psycopg.connect(jobs_database) as conn:
            assert conn.execute(
                "SELECT status FROM runtime.jobs WHERE id=%s", (job,)
            ).fetchone() == ("SUCCEEDED",)
        # Running the same job again does not call the model again (stopped before review).
        assert execute_job(worker_settings, WORKER, job, build=fake_build) == "NOT_CLAIMED"

        # 3. The results are in the C/S analysis view, as AI runs of this job.
        analysis = ok(client.get(f"/api/v1/cs/tickets/{ticket}/analysis"))
        assert [r["outcome"] for r in analysis["runs"]] == ["VALID"]
        assert analysis["fields"]["category_code"]["effective_value"] == "WARRANTY"
        [draft] = analysis["drafts"]
        assert draft["outcome"] == "VALID"
        # The workflow is untouched by the graph: the ticket is still at intake.
        assert ok(client.get(f"/api/v1/cs/tickets/{ticket}/workflow"))["state"] == "RECEIVED"

        # 4. The agent reviews and sends the information request; once accepted the graph ends.
        ok(act(client, ticket, "start-review", version=0, user=agent))
        sent = last_send(
            ok(
                send(
                    client,
                    ticket,
                    1,
                    kind="INFO_REQUEST",
                    draft_id=draft["id"],
                    draft_option="INFO_FIRST",
                )
            )
        )
        assert sent["requested_info_codes"]
        mail.settle("ACCEPTED")
        state = ok(act(client, ticket, f"sends/{sent['id']}/refresh", user=agent))["state"]
        assert state == "WAITING_CUSTOMER"

    with psycopg.connect(
        make_conninfo(cs_tables, options="-c search_path=checkpoint_cs"), autocommit=True
    ) as conn:
        from langgraph.checkpoint.postgres import PostgresSaver

        graph = fake_build(None, _parts(), PostgresSaver(conn))
        final = graph.get_state(thread_config(str(job)))
    assert final.next == () and final.values["stage"] == "HUMAN_REVIEW"
    assert HUMAN_REVIEW not in (final.next or ())


def _parts():
    """체크포인트를 읽기만 하는 그래프용 빈 입력."""

    class Unused:
        def load_ticket(self, _ticket_id):
            raise AssertionError("not called")

        def record_attempt(self, *_args):
            raise AssertionError("not called")

    class Parts:
        ticket_source = Unused()
        allowed_info_codes = frozenset({"none"})

    return Parts()
