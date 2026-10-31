"""실행의 사례별 시도 조회(KAN-139 4차). summary와 같은 접근 검사를 하고 원문·정답·원시 응답은 내지 않는다.

사례 ID(target_ref)는 sha256 앞 12자로만 낸다. 시도의 parsed_output은 분류 단계(classify·smoke)에서만 코드 다섯
개(category_code·secondary_category·urgency_flag·return_subtype·provided_info)로 투영하고, 문장이 들어 있을 수 있는
다른 단계는 null이다. 정답(gold_labels)·최종 출력·raw_output·입력 hash·사용량 원본은 읽지 않는다. 잠금 채점은 실행
단위라 사례별 정답·채점 결과는 내지 않는다.
"""

import hashlib
import re
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import check_actor
from backend.api.platform.evaluations.summary import open_run
from backend.shared.settings import Settings

CODE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
CODE_FIELDS = ("category_code", "secondary_category", "urgency_flag", "return_subtype")
PARSED_STEPS = frozenset({"classify", "smoke"})
MAX_LIMIT = 100


class ParsedCodes(BaseModel):
    """분류 출력의 코드 값만. 코드 형식이 아닌 값은 null(provided_info는 뺀다)이다."""

    model_config = ConfigDict(extra="forbid")
    category_code: str | None
    secondary_category: str | None
    urgency_flag: str | None
    return_subtype: str | None
    provided_info: list[str]


class CaseAttemptView(BaseModel):
    """시도 하나의 결과 메타데이터. 원문·원시 응답·사용량 원본은 없다."""

    model_config = ConfigDict(extra="forbid")
    step: str
    attempt: Annotated[int, Field(ge=1)]
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"]
    error_code: str | None
    http_status: int | None
    finish_reasons: list[str]
    call_attempts: int | None
    model_version: str | None
    latency_ms: int | None
    parsed_output: ParsedCodes | None


class CaseView(BaseModel):
    """사례 하나. 사례 ID는 sha256 앞 12자다."""

    model_config = ConfigDict(extra="forbid")
    case_id: UUID
    target_ref_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{12}$")]
    status: Literal["PENDING", "VALID", "INVALID_OUTPUT", "HELD", "FAILED", "EXCLUDED"]
    attempts: list[CaseAttemptView]


class EvaluationRunCases(BaseModel):
    """사례 한 페이지와 다음 커서(다음이 없으면 null)."""

    model_config = ConfigDict(extra="forbid")
    items: list[CaseView]
    next_cursor: UUID | None


def _codes(step: str, value: object) -> ParsedCodes | None:
    """분류 단계의 parsed_output에서 코드 값만 고른다.

    Args:
        step: 시도 단계.
        value: 저장된 parsed_output.

    Returns:
        ParsedCodes | None: 분류 단계가 아니거나 값이 객체가 아니면 None.
    """
    if step not in PARSED_STEPS or not isinstance(value, dict):
        return None

    def code(item: object) -> str | None:
        return item if isinstance(item, str) and CODE.fullmatch(item) else None

    info = value.get("provided_info")
    return ParsedCodes(
        **{key: code(value.get(key)) for key in CODE_FIELDS},
        provided_info=[c for c in (info if isinstance(info, list) else []) if code(c)],
    )


def list_run_cases(
    settings: Settings,
    access: EvaluationAccessSettings,
    actor: UUID,
    run_id: UUID,
    *,
    cursor: UUID | None,
    limit: int,
) -> EvaluationRunCases:
    """summary와 같은 접근 검사 뒤 사례를 case_id 순으로 한 페이지 읽는다.

    Args:
        settings: local/test 환경과 플랫폼 DB 연결.
        access: 현재 평가자·구성 승인 목록.
        actor: 현재 세션 사용자.
        run_id: 조회할 실행.
        cursor: 앞 페이지의 마지막 case_id(없으면 처음부터).
        limit: 페이지 크기(1~MAX_LIMIT, 라우터가 검사한다).

    Returns:
        EvaluationRunCases: 사례와 시도(step·attempt 순), 다음 커서. 접근 위반은 ApplicationError, DB 실패는 503이다.
    """
    check_actor(settings, access, actor)
    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout='5s'")
            with conn.cursor(row_factory=dict_row) as rows:
                open_run(rows, access, actor, run_id)
                cases = rows.execute(
                    "SELECT id,target_ref,status FROM evaluation.cases WHERE run_id=%s "
                    "AND (%s::uuid IS NULL OR id>%s) ORDER BY id LIMIT %s",
                    (run_id, cursor, cursor, limit + 1),
                ).fetchall()
                page = cases[:limit]
                attempts = rows.execute(
                    "SELECT case_id,step,attempt,outcome,error_code,http_status,finish_reasons,"
                    "call_attempts,model_version,latency_ms,parsed_output "
                    "FROM evaluation.case_attempts WHERE case_id=ANY(%s) ORDER BY case_id,step,attempt",
                    ([case["id"] for case in page],),
                ).fetchall()
    except psycopg.Error:
        raise HTTPException(503, "Evaluation cases are unavailable") from None
    by_case: dict[UUID, list[CaseAttemptView]] = {case["id"]: [] for case in page}
    for row in attempts:
        by_case[row["case_id"]].append(
            CaseAttemptView(
                **{
                    key: row[key]
                    for key in (
                        "step",
                        "attempt",
                        "outcome",
                        "error_code",
                        "http_status",
                        "call_attempts",
                        "model_version",
                        "latency_ms",
                    )
                },
                finish_reasons=list(row["finish_reasons"] or []),
                parsed_output=_codes(row["step"], row["parsed_output"]),
            )
        )
    return EvaluationRunCases(
        items=[
            CaseView(
                case_id=case["id"],
                target_ref_sha=hashlib.sha256(case["target_ref"].encode()).hexdigest()[:12],
                status=case["status"],
                attempts=by_case[case["id"]],
            )
            for case in page
        ],
        next_cursor=page[-1]["id"] if len(cases) > limit else None,
    )
