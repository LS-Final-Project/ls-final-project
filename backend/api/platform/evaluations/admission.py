"""합성 실험의 접수·사례·작업을 한 트랜잭션으로 보존한다."""

import hashlib
import json
import re
from typing import Any, get_args
from uuid import UUID, uuid4

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.cs_smoke import matches_profile
from backend.api.platform.evaluations.schemas import (
    EvaluationAdmission,
    EvaluationExperiment,
    EvaluationRunItem,
    EvaluationRunStatus,
    Stage,
)
from backend.api.platform.jobs.admission import EvaluationJobRequest, admit_evaluation_job
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.settings import Settings

CONFIG_FIELDS = (
    "purpose",
    "data_origin",
    "dataset_hash",
    "data_split",
    "gold_version",
    "policy_version",
    "prompt_version",
    "model_id",
    "index_version",
    "input_builder_version",
    "configuration",
    "target_set_hash",
)
# 값이 없거나 빈 dict이면 hash에서 생략해 기존 실험·승인 hash를 그대로 둔다.
OPTIONAL_CONFIG_FIELDS = ("stage", "knobs")
# 마이그레이션 20261029_KAN-90_experiment_stage_knobs.sql의 CHECK 정규식과 같아야 한다(테스트가 비교).
SECRET_KNOB_PATTERN = "api_key|token|secret|password|credential"
KNOB_KEY = re.compile(r"[A-Za-z0-9_.]{1,64}")
MAX_KNOBS = 20
MAX_KNOB_TEXT = 256


def canonical_hash(value: dict[str, Any]) -> str:
    """계약의 키 정렬·공백 없는 UTF-8 JSON으로 SHA-256을 계산한다.

    Args:
        value: 비밀값 없는 요청 또는 동결 구성.

    Returns:
        str: 소문자 64자리 hash.
    """
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def config_payload(experiment: dict[str, Any]) -> dict[str, Any]:
    """config_hash를 계산할 구성 필드를 고른다.

    Args:
        experiment: 실험 행 또는 등록할 구성.

    Returns:
        dict: CONFIG_FIELDS 전부와, 값이 있을 때만 stage·knobs.
    """
    payload = {k: experiment[k] for k in CONFIG_FIELDS}
    payload.update({k: experiment[k] for k in OPTIONAL_CONFIG_FIELDS if experiment.get(k)})
    return payload


def check_stage_knobs(stage: object, knobs: object) -> None:
    """실험 등록 전에 단계와 조절 항목을 검사한다. 등록 경로와 fixture가 쓴다.

    Args:
        stage: 단계(classification·search·draft·rfp) 또는 None.
        knobs: 깊이 1 dict. 키는 1~64자 영숫자·_·., 최대 20개, 값은 문자열(256자 이하)·정수·실수·bool.

    Returns:
        None: 비밀로 보이는 키는 422 EVALUATION_KNOB_SECRET, 그 밖의 위반은 422
            EVALUATION_KNOB_INVALID ApplicationError다.
    """
    if stage is not None and stage not in get_args(Stage):
        raise ApplicationError(422, "EVALUATION_KNOB_INVALID", "Stage is not allowed.")
    if not isinstance(knobs, dict) or len(knobs) > MAX_KNOBS:
        raise ApplicationError(422, "EVALUATION_KNOB_INVALID", "Knobs are invalid.")
    for key, value in knobs.items():
        if isinstance(key, str) and re.search(SECRET_KNOB_PATTERN, key, re.IGNORECASE):
            raise ApplicationError(422, "EVALUATION_KNOB_SECRET", "Secret knobs are not allowed.")
        if (
            not isinstance(key, str)
            or not KNOB_KEY.fullmatch(key)
            or not isinstance(value, str | int | float)
            or (isinstance(value, str) and len(value) > MAX_KNOB_TEXT)
        ):
            raise ApplicationError(422, "EVALUATION_KNOB_INVALID", "Knobs are invalid.")


def admission_available(settings: Settings, access: EvaluationAccessSettings) -> bool:
    """운영 실행과 구별되는 명시적 로컬 접수 모드 여부를 반환한다.

    Args:
        settings: 실행 환경 설정.
        access: 서버 전용 평가 설정.

    Returns:
        bool: local/test이며 명시적으로 접수를 켠 경우만 True.
    """
    return settings.environment in {"local", "test"} and access.local_admission_enabled


def check_actor(settings: Settings, access: EvaluationAccessSettings, actor: UUID) -> None:
    """저장 함수에서도 환경 잠금과 평가자 지정을 검사한다.

    Args:
        settings: 실행 환경.
        access: 현재 평가 설정.
        actor: 세션 검증을 마친 내부 사용자 UUID.

    Returns:
        None: 허용 조건을 통과하며 위반은 안전한 오류로 거절한다.
    """
    if actor not in access.evaluator_user_ids:
        raise ApplicationError(403, "FORBIDDEN", "Access denied.")
    if not admission_available(settings, access):
        raise ApplicationError(503, "EVALUATION_RUNNER_NOT_CONNECTED", "Execution is unavailable.")


def validate_smoke_experiment(experiment: dict[str, Any], access: EvaluationAccessSettings) -> None:
    """접수와 실행에서 동일한 합성 구성·시험 차단 규칙을 검사한다.

    Args:
        experiment: 서버 DB의 실험 메타데이터.
        access: 현재 서버의 허용 구성 목록.

    Returns:
        None: 허용된 합성 구성만 통과하며 위반은 안전한 오류로 반환한다.
    """
    # 최종 시험은 허용 hash에 있어도 일반 접수에서 항상 거절한다.
    if experiment["data_split"] == "test":
        raise ApplicationError(403, "FINAL_TEST_LOCKED", "Final test is locked.")
    if experiment["config_hash"] not in access.approved_config_hashes:
        raise ApplicationError(403, "EXPERIMENT_NOT_APPROVED", "Experiment is not approved.")
    if (
        experiment["purpose"] != "smoke"
        or experiment["data_origin"] != "synthetic"
        or experiment["model_id"] != "fake"
        or (experiment["configuration"] != {} and not matches_profile(experiment))
        or experiment["dataset_manifest"] != {}
    ):
        raise ApplicationError(403, "LOCAL_SMOKE_ONLY", "Only synthetic smoke is supported.")
    if canonical_hash(config_payload(experiment)) != experiment["config_hash"]:
        raise ApplicationError(409, "EXPERIMENT_HASH_MISMATCH", "Experiment hash differs.")


async def accept_run(
    settings: Settings,
    access: EvaluationAccessSettings,
    actor: UUID,
    experiment_id: UUID,
    key: str,
    request_id: UUID,
    if_match: str | None = None,
) -> SuccessResponse[EvaluationAdmission]:
    """현재 허용된 합성 실험을 원자적으로 접수하거나 최초 접수 응답을 재생한다.

    Args:
        settings: 플랫폼 DB 연결과 실행 환경.
        access: 지정 평가자와 사전 검수된 구성 hash 허용 목록.
        actor: 현재 운영 세션으로 확인한 사용자.
        experiment_id: 이미 동결 등록된 실험 UUID.
        key: 공통 계약의 멱등 키.
        request_id: 최초 응답에 보존할 요청 UUID.
        if_match: 요청 hash에 포함하는 조건 헤더. 신규 생성의 판본 조건은 아니다.

    Returns:
        SuccessResponse: DB commit이 완료된 최초 접수 응답. 외부 실행은 하지 않는다.
    """
    check_actor(settings, access, actor)
    async with connection(settings) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '2s'")
            await conn.execute("SET LOCAL statement_timeout = '5s'")
            cursor = await conn.execute(
                "SELECT * FROM evaluation.experiments WHERE id=%s AND created_by_user_id=%s",
                (experiment_id, actor),
            )
            experiment = await cursor.fetchone()
            if experiment is None:
                raise ApplicationError(404, "NOT_FOUND", "Experiment is unavailable.")
            validate_smoke_experiment(experiment, access)
            # 검증한 대상 snapshot만 아래 사례 INSERT에 사용한다. 이후 추가된 행은 섞지 않는다.
            cursor = await conn.execute(
                "SELECT target_ref,data_origin,attributes FROM evaluation.experiment_targets "
                "WHERE experiment_id=%s",
                (experiment_id,),
            )
            targets = await cursor.fetchall()
            refs = sorted(row["target_ref"] for row in targets)
            if (
                not targets
                or any(
                    row["data_origin"] != "synthetic" or row["attributes"] != {} for row in targets
                )
                or hashlib.sha256("\n".join(refs).encode()).hexdigest()
                != experiment["target_set_hash"]
            ):
                raise ApplicationError(409, "TARGET_SET_MISMATCH", "Target set differs.")
            job = await admit_evaluation_job(
                conn,
                EvaluationJobRequest(
                    actor_id=actor,
                    run_id=uuid4(),
                    input_version=experiment["config_hash"],
                    idempotency_key=key,
                    request_hash=canonical_hash(
                        {"experiment_id": str(experiment_id), "if_match": if_match}
                    ),
                ),
            )
            if job.created:
                await conn.execute(
                    "INSERT INTO evaluation.runs "
                    "(id,experiment_id,job_id,runner_kind,requested_by_user_id,provider,"
                    "scoring_version,requested_count) VALUES (%s,%s,%s,'test',%s,'fake',"
                    "'smoke-not-scored-v1',%s)",
                    (job.run_id, experiment_id, job.job_id, actor, len(refs)),
                )
                for ref in refs:
                    await conn.execute(
                        "INSERT INTO evaluation.cases(id,run_id,experiment_id,target_ref) "
                        "VALUES (%s,%s,%s,%s)",
                        (uuid4(), job.run_id, experiment_id, ref),
                    )
                await conn.execute(
                    "INSERT INTO evaluation.run_control(run_id) VALUES (%s)",
                    (job.run_id,),
                )
                await conn.execute(
                    "INSERT INTO evaluation.admission_receipts(run_id,response_request_id) "
                    "VALUES (%s,%s)",
                    (job.run_id, request_id),
                )
            cursor = await conn.execute(
                "SELECT response_request_id FROM evaluation.admission_receipts WHERE run_id=%s",
                (job.run_id,),
            )
            receipt = await cursor.fetchone()
            if receipt is None:
                raise ApplicationError(503, "ADMISSION_INCOMPLETE", "Retry with the same key.")
            result: SuccessResponse[EvaluationAdmission] = SuccessResponse(
                data=EvaluationAdmission(
                    run_id=job.run_id,
                    job_id=job.job_id,
                    status_url=f"/api/v1/platform/evaluations/runs/{job.run_id}",
                ),
                meta=ResponseMeta(request_id=receipt["response_request_id"]),
            )
    return result


async def get_run(
    settings: Settings,
    access: EvaluationAccessSettings,
    actor: UUID,
    run_id: UUID,
) -> EvaluationRunStatus:
    """현재 평가 권한을 확인하고 구성 철회 후에도 자기 접수의 제어 상태를 반환한다.

    Args:
        settings: DB와 환경 설정.
        access: 현재 서버 허용 목록.
        actor: 현재 세션 사용자.
        run_id: 조회할 실행 UUID.

    Returns:
        EvaluationRunStatus: 정답·원시 출력·입력 없이 제한된 상태 필드.
    """
    check_actor(settings, access, actor)
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT r.id,r.job_id,r.status,r.requested_count,e.config_hash,e.data_split,r.needs_review,r.failure_code,c.version "
            "FROM evaluation.runs r JOIN evaluation.experiments e ON e.id=r.experiment_id "
            "JOIN evaluation.admission_receipts a ON a.run_id=r.id "
            "JOIN evaluation.run_control c ON c.run_id=r.id "
            "WHERE r.id=%s AND r.requested_by_user_id=%s AND e.created_by_user_id=%s",
            (run_id, actor, actor),
        )
        row = await cursor.fetchone()
        if row is None or row["data_split"] == "test":
            raise ApplicationError(404, "NOT_FOUND", "Run is unavailable.")
        return EvaluationRunStatus(
            run_id=row["id"],
            job_id=row["job_id"],
            status=row["status"],
            requested_count=row["requested_count"],
            version=row["version"],
            needs_review=row["needs_review"],
            failure_code=row["failure_code"],
        )


# ponytail: 커서 없이 최근 50개만 준다. 평가자 한 명의 실험·실행이 더 많아지면 커서를 더한다.
LIST_LIMIT = 50


async def list_experiments(
    settings: Settings, access: EvaluationAccessSettings, actor: UUID, stage: Stage | None = None
) -> list[EvaluationExperiment]:
    """본인 실험을 최근순으로 읽고 접수 가능 여부를 접수와 같은 규칙으로 계산한다.

    Args:
        settings: DB와 환경 설정.
        access: 현재 서버 허용 목록.
        actor: 현재 세션 사용자(지정 평가자 검사는 라우터가 한다).
        stage: 주어지면 그 단계의 실험만.

    Returns:
        list[EvaluationExperiment]: 최대 50개. 구성 원문·manifest·hash·대상 ID는 없다.
    """
    available = admission_available(settings, access)
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT e.*, (SELECT count(*) FROM evaluation.experiment_targets t "
            "WHERE t.experiment_id=e.id) AS target_count FROM evaluation.experiments e "
            "WHERE e.created_by_user_id=%s AND (%s::text IS NULL OR e.stage=%s) "
            "ORDER BY e.created_at DESC, e.id LIMIT %s",
            (actor, stage, stage, LIST_LIMIT),
        )
        rows = await cursor.fetchall()
    items = []
    for row in rows:
        reason = None if available else "ADMISSION_UNAVAILABLE"
        if reason is None:
            try:
                validate_smoke_experiment(row, access)
            except ApplicationError as error:
                reason = error.code
        items.append(
            EvaluationExperiment(
                experiment_id=row["id"],
                name=row["name"],
                purpose=row["purpose"],
                data_split=row["data_split"],
                data_origin=row["data_origin"],
                model_id=row["model_id"],
                stage=row["stage"],
                knobs=row["knobs"],
                target_count=row["target_count"],
                created_at=row["created_at"],
                admissible=reason is None,
                blocked_reason=reason,
            )
        )
    return items


async def list_runs(settings: Settings, actor: UUID) -> list[EvaluationRunItem]:
    """본인 접수 실행을 최근순으로 읽는다. GET runs/{id}로 볼 수 있는 실행만 나온다.

    Args:
        settings: DB 설정.
        actor: 현재 세션 사용자.

    Returns:
        list[EvaluationRunItem]: 최대 50개. 최종 시험 실행은 상태 조회처럼 숨긴다.
    """
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT r.id,r.job_id,r.experiment_id,e.stage,r.status,r.requested_count,"
            "r.needs_review,r.failure_code,r.created_at FROM evaluation.runs r "
            "JOIN evaluation.experiments e ON e.id=r.experiment_id "
            "JOIN evaluation.admission_receipts a ON a.run_id=r.id "
            "JOIN evaluation.run_control c ON c.run_id=r.id "
            "WHERE r.requested_by_user_id=%s AND e.created_by_user_id=%s "
            "AND e.data_split IS DISTINCT FROM 'test' "
            "ORDER BY r.created_at DESC, r.id LIMIT %s",
            (actor, actor, LIST_LIMIT),
        )
        rows = await cursor.fetchall()
    return [
        EvaluationRunItem(
            run_id=row["id"],
            job_id=row["job_id"],
            experiment_id=row["experiment_id"],
            stage=row["stage"],
            status=row["status"],
            requested_count=row["requested_count"],
            needs_review=row["needs_review"],
            failure_code=row["failure_code"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
