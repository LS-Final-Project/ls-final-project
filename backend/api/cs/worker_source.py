"""C/S 처리 worker의 문의 출처(KAN-107). worker(KAN-194 `backend/agent/cs_worker.py`)가 고정 모듈로 불러 쓴다.

`build(settings)`가 `WorkerParts(ticket_source=...)`를 돌려준다. 규약은 contracts/cs/graph.md "worker 실행" 절.

- `load_ticket`: cs 표에서 그래프 입력(채널·언어·제목·본문, 제품, 고객 등급, 접수 시각, 유형별 필요 정보, 이미 요청한
  정보)을 읽는다. 고객 식별자·정답·사후 기록은 읽지 않는다(cs_worker 권한도 그 열만 준다).
- `record_attempt`: 분류·초안 시도를 `cs.ticket_analyses`·`cs.ticket_drafts`에 작업 ID와 함께 추가한다. 원문과 모델의
  raw 응답은 저장하지 않는다. 업무 상태(`cs.ticket_workflows`)는 건드리지 않는다: 그래프 상태는 체크포인트에만 있다.

worker는 동기 코드이고 cs 저장 함수는 비동기라, 호출마다 `asyncio.run`으로 연결 하나를 열어 같은 저장 함수를 쓴다
(cs_search.fts_searcher_from_dsn과 같은 방식). 실행 중인 이벤트 루프 안에서는 부를 수 없다.
"""

import asyncio
import csv
import io
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from backend.agent.job_runtime import parse_job_id
from backend.agent.ticket_source_registry import REQUIRED_INFO, WorkerParts
from backend.api.cs.analysis import service
from backend.engine.cs.classification_call import ClassificationAttempt
from backend.engine.cs.drafting_call import DraftAttempt
from backend.engine.cs.graph import TicketInputs
from backend.engine.cs.rules import RuleInputError, product_family
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

Row = dict[str, Any]
T = TypeVar("T")

TICKET = (
    "SELECT t.channel, t.body_language, t.subject, t.body, t.product_code, t.received_at, "
    "s.customer_tier FROM cs.tickets t "
    "LEFT JOIN cs.ticket_source_records s ON s.ticket_id = t.id WHERE t.id = %s"
)
# 고객에게 실제로 나간 메시지(플랫폼 ACCEPTED가 업무 상태에 반영된 것)만 "이미 요청함"으로 본다. 승인만 되고
# 보내지지 않은 메시지의 질문은 다시 물어야 한다.
ASKED = (
    "SELECT requested_info_codes FROM cs.ticket_customer_sends "
    "WHERE ticket_id = %s AND recorded_at IS NOT NULL"
)
LATEST_VALID_RUN = (
    "SELECT id FROM cs.ticket_analyses WHERE ticket_id = %s AND job_id = %s AND outcome = 'VALID' "
    "ORDER BY run_seq DESC LIMIT 1"
)


def required_by_family(path: Path = REQUIRED_INFO) -> dict[str, dict[str, frozenset[str]]]:
    """정책표의 제품 계열 → 문의 유형 → 필요 정보 코드.

    Args:
        path: eval/policy/cs_required_info.csv.

    Returns:
        dict: 계열별 유형별 코드 집합. 열이 없으면 ValueError다.
    """
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"), newline=""))
    if not {"family", "category", "required_fields"} <= set(reader.fieldnames or ()):
        raise ValueError("CS_REQUIRED_INFO_COLUMNS")
    table: dict[str, dict[str, frozenset[str]]] = {}
    for row in reader:
        codes = frozenset(
            code.strip() for code in row["required_fields"].split(";") if code.strip()
        )
        table.setdefault(row["family"], {})[row["category"]] = codes
    return table


class DbTicketSource:
    """cs 표를 읽고 쓰는 `TicketSource`(backend/agent/cs_runner.py). 작업 하나에 하나를 만든다."""

    def __init__(
        self,
        dsn: str,
        job_id: UUID | None,
        required: Mapping[str, Mapping[str, frozenset[str]]] | None = None,
    ) -> None:
        """연결 정보와 작업 ID를 받는다.

        Args:
            dsn: cs_worker 로그인 DSN.
            job_id: 이 프로세스가 맡은 작업. 저장 행에 남고 초안을 같은 작업의 분류 실행에 잇는다.
            required: 계열별 필요 정보 지도. None이면 정책표를 읽는다.
        """
        self.dsn = dsn
        self.job_id = job_id
        self.required = required if required is not None else required_by_family()

    def _run(self, work: Callable[[psycopg.AsyncConnection[Row]], Awaitable[T]]) -> T:
        """연결 하나를 열어 트랜잭션 하나로 실행한다."""

        async def run() -> T:
            async with await psycopg.AsyncConnection.connect(
                self.dsn, row_factory=dict_row
            ) as conn:
                result = await work(conn)
                await conn.commit()
                return result

        return asyncio.run(run())

    def load_ticket(self, ticket_id: str) -> TicketInputs:
        """그래프 입력을 읽는다. 상태에 넣지 않고 노드 안에서만 쓴다.

        Args:
            ticket_id: 문의 ID.

        Returns:
            TicketInputs: 문의 입력. 없는 문의면 ApplicationError CS_TICKET_NOT_FOUND다.
        """

        async def work(conn: psycopg.AsyncConnection[Row]) -> TicketInputs:
            ticket = await (await conn.execute(TICKET, (ticket_id,))).fetchone()
            if ticket is None:
                raise service.ticket_not_found()
            asked = await (await conn.execute(ASKED, (ticket_id,))).fetchall()
            try:
                family: str | None = product_family(ticket["product_code"])
            except RuleInputError:
                family = None
            received = ticket["received_at"]
            return TicketInputs(
                record={
                    "channel": ticket["channel"],
                    "body_language": ticket["body_language"],
                    "subject": ticket["subject"],
                    "body": ticket["body"],
                },
                # 제공 자료는 적재 때 이미 마스킹돼 있고 민감값을 따로 둔 표가 없다(2026-10-30 결정). 적재
                # 어댑터(KAN-92)가 민감값을 주면 여기서 읽는다. 이메일·라벨 붙은 시리얼은 build_input이 가린다.
                sensitive_values={},
                product_code=ticket["product_code"],
                # 직접 등록한 문의는 고객 등급이 없다. 빈 값이면 규칙 노드가 CS_RULE_INPUT으로 남기고 계속한다.
                customer_tier=ticket["customer_tier"] or "",
                received_at=received if received.tzinfo else received.replace(tzinfo=UTC),
                # 유형을 미리 알 필요가 없도록 계열의 유형별 지도를 주고, 초안 노드가 분류 결과로 행을 고른다.
                required_info_codes=frozenset(),
                already_asked_codes=frozenset(
                    code for row in asked for code in row["requested_info_codes"]
                ),
                required_info_by_category=dict(self.required.get(family or "", {})),
            )

        return self._run(work)

    def record_attempt(
        self, kind: str, ticket_id: str, attempt: ClassificationAttempt | DraftAttempt
    ) -> str:
        """시도 하나를 추가하고 저장한 행 ID를 돌려준다.

        Args:
            kind: "classification" 또는 "draft".
            ticket_id: 문의 ID.
            attempt: 시도 기록. raw_response는 저장하지 않는다.

        Returns:
            str: ticket_analyses 또는 ticket_drafts 행 ID.
        """
        valid = attempt.outcome == "VALID" and attempt.prediction is not None
        values: Row = {
            "job_id": self.job_id,
            "outcome": attempt.outcome,
            "input_version": attempt.input_version,
            "input_sha256": attempt.input_sha256,
            "model_version": attempt.model_version,
            "prompt_version": getattr(attempt, "prompt_version", None),
            "error_code": attempt.error_code,
        }
        ticket = UUID(ticket_id)

        async def work(conn: psycopg.AsyncConnection[Row]) -> str:
            # 문의 행을 잠그지 않는다(cs_worker에 문의 UPDATE 권한을 주지 않으려고). 플랫폼이 문의마다 진행 중
            # 작업 하나만 받고, 순번이 겹치면 UNIQUE(ticket_id, run_seq)가 막아 작업이 실패로 끝난다.
            if kind == "classification":
                prediction = attempt.prediction.model_dump(mode="json") if valid else None  # type: ignore[union-attr]
                row = await service.record_analysis(
                    conn, ticket, {**values, "classification": prediction}, lock=False
                )
            elif kind == "draft":
                run = None
                if self.job_id is not None:
                    run = await (
                        await conn.execute(LATEST_VALID_RUN, (ticket, self.job_id))
                    ).fetchone()
                prediction = attempt.prediction.model_dump(mode="json") if valid else None  # type: ignore[union-attr]
                row = await service.record_draft(
                    conn,
                    ticket,
                    {**values, "draft": prediction, "analysis_id": run["id"] if run else None},
                    lock=False,
                )
            else:
                raise ValueError("CS_ATTEMPT_KIND")
            return str(row["id"])

        return self._run(work)


def build(settings: Settings) -> WorkerParts:
    """worker가 부르는 입구. 작업 ID는 worker와 같은 `CS_JOB_ID`에서 읽는다.

    Args:
        settings: worker 설정(cs_worker 로그인 DATABASE_URL).

    Returns:
        WorkerParts: 문의 출처. 허용 정보 코드·검색 조건은 비워 두어 정책 기본값을 쓴다.
    """
    if settings.database_url is None:
        raise ApplicationError(503, "CS_WORKER_DATABASE_MISSING", "DATABASE_URL is required.")
    return WorkerParts(
        ticket_source=DbTicketSource(
            settings.database_url.get_secret_value(), parse_job_id(os.getenv("CS_JOB_ID"))
        )
    )
