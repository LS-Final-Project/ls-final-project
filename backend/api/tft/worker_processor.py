"""RFP 처리 함수(KAN-199 완료 조건 4, KAN-188 A07 접착). worker가 `build(settings)`로 받는다.

나영님 엔진 함수를 부르는 접착만 한다. 새 판정 규칙은 없다(contracts/tft/rfp-jobs.md '처리 함수' 절).

- rfp.process 단계: parse(원문 → 조항 트리 저장) → extract(절마다 모델 추출) → search(후보 범위) → compare(자사 비교)
  → supplement(보완 선택지) → candidates(협력사 등급) → draft(아직 없음, counts 0).
- 조항·요구·판본(판단 포함)·조건·비교·선택지·등급은 compare부터 candidates까지 한 트랜잭션에 쓴다. 도중에 멈추면
  아무것도 남지 않아 다시 돌리면 처음부터 한다.
- 재실행: 조항이 저장된 문서 판본(= 끝까지 처리된 판본, 요구 0건 포함)은 다시 처리하지 않는다(모델을 다시 부르지 않음).
- rfp.export·rfp.annotate는 아직 단계가 없어 그 단계 counts 0만 남기고 끝낸다.
"""

import asyncio
import itertools
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.agent.processor_registry import Emit, Processor, TftJob
from backend.agent.tft_worker import TftWorkerSettings
from backend.api.tft.partners import grading
from backend.api.tft.partners.schemas import Condition, PartnerProfile
from backend.api.tft.rfp import comparison, repository, supplement
from backend.api.tft.rfp.jobs import input_version
from backend.api.tft.rfp.storage import read_document
from backend.engine.documents.router import extract
from backend.engine.rfp.clauses import ClauseTree, build_clause_tree
from backend.engine.rfp.requirements import (
    ExtractedRequirement,
    Operator,
    ResponseSchema,
    build_inputs,
    extract_section,
)
from backend.engine.rfp.vocabulary import OTHER
from backend.shared.ai import TRANSIENT_BACKOFF_SECONDS, AISettings, Generation, Usage
from backend.shared.settings import Settings

Provider = Callable[[str], Generation]
ZERO = {"requirements": 0, "proposals": 0}
# 요구를 뽑는 문서 종류. 도면·자료표·메모 PDF는 처리 단계에서 읽지 않는다.
SPEC_KINDS = ("RFP_SPEC", "ADDENDUM")
# 접수 입력 판본(input_version)을 찾을 때 조합을 다 볼 문서 판본 수 상한.
MAX_VERSIONS = 12
ONLY_STEP = {"rfp.export": "export", "rfp.annotate": "annotate"}


class ProcessorSettings(BaseSettings):
    """원문 폴더와 추출 모델 설정. fake는 요구를 뽑지 않는 고정 응답이다(키 없는 로컬 시연·검사).

    추출 값은 공통 AI 기본값(출력 1024토큰)을 쓰지 않는다. 기본은 평가 스크립트(scripts/rfp_requirements_eval.py)와
    같은 모델·출력 상한이고, 시간 제한은 lease 안에 들도록 120초다. 키·공급자·재시도는 공통 AI 설정(GEMINI_*)이다.
    """

    model_config = SettingsConfigDict(env_prefix="TFT_", env_file=".env", extra="ignore")
    document_root: Path | None = None
    extraction_provider: Literal["gemini", "fake"] = "gemini"
    extraction_model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    extraction_max_output_tokens: int = Field(default=16384, ge=1, le=65536)
    extraction_timeout_ms: int = Field(default=120000, ge=1000, le=300000)


def fake_provider(_prompt: str) -> Generation:
    """요구를 뽑지 않는 고정 응답.

    Args:
        _prompt: 쓰지 않는다.

    Returns:
        Generation: 빈 요구 목록.
    """
    return Generation(
        text='{"requirements": []}', usage=Usage(), model_version=None, finish_reasons=[]
    )


def extraction_settings(config: ProcessorSettings) -> AISettings:
    """추출 호출 설정: 공통 AI 설정(키·공급자·재시도)에 추출 전용 모델·출력 상한·시간 제한을 덮는다.

    Args:
        config: 처리 함수 설정.

    Returns:
        AISettings: generate에 넘길 설정.
    """
    return AISettings(
        gemini_model=config.extraction_model,
        gemini_max_output_tokens=config.extraction_max_output_tokens,
        gemini_timeout_ms=config.extraction_timeout_ms,
    )


def check_lease(ai: AISettings, lease_seconds: int) -> None:
    """lease가 모델 호출 하나의 최악 시간보다 길어야 한다(호출마다 lease를 연장하므로).

    최악 = 시간 제한 × (일시 오류 재시도 + 1) + 재시도 대기(TRANSIENT_BACKOFF_SECONDS부터 두 배씩).

    Args:
        ai: 추출 호출 설정.
        lease_seconds: worker lease 길이(TFT_WORKER_LEASE_SECONDS).

    Returns:
        None. 짧으면 ValueError(INVALID_WORKER_CONFIGURATION)다.
    """
    retries = ai.gemini_transient_retries
    worst = ai.gemini_timeout_ms / 1000 * (retries + 1) + sum(
        TRANSIENT_BACKOFF_SECONDS * 2**n for n in range(retries)
    )
    if lease_seconds <= worst:
        raise ValueError("INVALID_WORKER_CONFIGURATION")


def gemini_provider(ai: AISettings) -> Provider:
    """Gemini 구조화 출력 호출(평가 스크립트와 같은 방식).

    Args:
        ai: 추출 호출 설정(extraction_settings).

    Returns:
        Provider: 지시문 → Generation.
    """
    from backend.shared.ai import create_client, generate

    client = create_client(ai)
    return lambda text: generate(client, ai, text, response_schema=ResponseSchema)


def build(settings: Settings) -> Processor:
    """worker가 부르는 등록 지점.

    Args:
        settings: worker 설정(DB).

    Returns:
        Processor: 처리 함수.
    """
    config = ProcessorSettings()
    provider: Provider
    if config.extraction_provider == "fake":
        provider = fake_provider
    else:
        ai = extraction_settings(config)
        # 잘못된 조합이면 모델을 부르기 전에 멈춘다(worker는 처리 함수 load 실패로 작업을 FAILED로 남긴다).
        check_lease(ai, TftWorkerSettings().lease_seconds)
        provider = gemini_provider(ai)
    return make_processor(settings, provider=provider, document_root=config.document_root)


def make_processor(
    settings: Settings,
    *,
    provider: Provider,
    document_root: Path | None,
    products: Sequence[comparison.Product] | None = None,
    today: Callable[[], date] = date.today,
) -> Processor:
    """처리 함수를 만든다(검사는 추출 공급자·원문 폴더를 바꿔 끼운다).

    Args:
        settings: DB 설정.
        provider: 추출 모델 호출.
        document_root: 원문 폴더. 없으면 원문이 필요한 작업은 실패한다.
        products: 자사 제품(기본은 own_spec_facts 전체).
        today: 인증 유효기한 기준일.

    Returns:
        Processor: `processor(job, emit)`.
    """
    own = list(products) if products is not None else comparison.load_own_products()

    def processor(job: TftJob, emit: Emit) -> dict[str, Any] | None:
        if job.kind in ONLY_STEP:
            emit({"step": ONLY_STEP[job.kind], "counts": ZERO})
            return {"counts": ZERO, "needs_review": False}
        return asyncio.run(process(settings, job, emit, provider, document_root, own, today()))

    return processor


def sections_of(tree: ClauseTree) -> list[str]:
    """추출할 절: 번호로 하위 조항을 묶는 조항 중 가장 위의 것(PART 2 아래의 2.1, 2.2 등).

    Args:
        tree: 조항 트리.

    Returns:
        list[str]: 절 번호(트리 순서).
    """
    numbers = [c.clause_no for c in tree.clauses]
    groups = {n for n in numbers if any(m.startswith(n + ".") for m in numbers)}
    parents = {c.clause_no: c.parent_no for c in tree.clauses}
    out = []
    for no in numbers:
        if no not in groups:
            continue
        parent = parents[no]
        while parent is not None and parent not in groups:
            parent = parents.get(parent)
        if parent is None:
            out.append(no)
    return out


# 허용 오차 표기: 대칭(±10%, +/-0.5)과 비대칭(+0/-5%, +10%/-5%). 숫자 부분만 읽는다.
SYMMETRIC = re.compile(r"^\s*(?:±|\+\s*/\s*-|\+-)\s*(\d+(?:\.\d+)?)\s*%?\s*$")
ASYMMETRIC = re.compile(r"^\s*\+\s*(\d+(?:\.\d+)?)\s*%?\s*/\s*-\s*(\d+(?:\.\d+)?)\s*%?\s*$")


def tolerance_range(item: ExtractedRequirement) -> tuple[Operator, str | None]:
    """명목값(EQ)과 원문 허용 오차로 비교 범위를 만든다(KAN-199). 계산은 코드가 하고 원문 값은 그대로 저장한다.

    '%'가 붙은 오차는 명목값에 대한 비율이다(값 단위가 %여도: 5.75% ±10% → 5.175~6.325). '%'가 없으면 같은 단위의
    절대값이다(±0.5 → v-0.5~v+0.5). 경계는 반올림하지 않는다.

    Args:
        item: 추출 요구.

    Returns:
        tuple: (연산자, 값). 허용 오차가 없거나 EQ가 아니면 원래 그대로다. 읽지 못하면 ("RANGE", 원래 값)이라
            비교가 '범위를 숫자로 읽을 수 없어 확인 필요'(조건부)가 된다.
    """
    if item.tolerance is None or item.operator != "EQ" or item.value is None:
        return item.operator, item.value
    nominal = grading._number(item.value)
    symmetric, asymmetric = SYMMETRIC.match(item.tolerance), ASYMMETRIC.match(item.tolerance)
    if nominal is None or not (symmetric or asymmetric):
        return "RANGE", item.value
    if symmetric:
        up = down = symmetric.group(1)
    else:
        assert asymmetric is not None
        up, down = asymmetric.groups()
    scale = abs(nominal) / 100 if "%" in item.tolerance else Decimal(1)
    low, high = nominal - Decimal(down) * scale, nominal + Decimal(up) * scale
    return "RANGE", f"{format(low.normalize(), 'f')}~{format(high.normalize(), 'f')}"


def to_comparison(item: ExtractedRequirement) -> comparison.Requirement:
    """추출 요구 → 자사 비교 입력. 매핑 규칙은 이 함수 한 곳에만 둔다.

    COMPLIANCE는 인증 요구(certification_standard=value, 없으면 attribute), 그 밖은 추출의 attribute_key(자사 사양
    어휘, 연결하지 못했으면 OTHER → 비교하지 않음)다. 제품 계열은 equipment_family로 compare_in_family가 고른다
    (KAN-199). 허용 오차가 있는 명목값은 범위로 바꾸고(tolerance_range), 등록(Listed) 요구와 규격 체계도 넘긴다.
    저장하는 요구 판본의 attribute·value는 추출 원문 그대로다.

    Args:
        item: 추출 요구.

    Returns:
        comparison.Requirement: 비교 입력.
    """
    certification = item.kind == "COMPLIANCE"
    operator, value = tolerance_range(item)
    return comparison.Requirement(
        clause_no=item.clause_no,
        item=item.equipment or item.task or item.attribute,
        attribute=None if certification else item.attribute_key,
        value=value,
        unit=item.unit,
        operator=operator,
        conditions=[Condition.model_validate(c.model_dump()) for c in item.conditions],
        necessity=item.necessity,
        # 추출은 규격을 value에 둔다(attribute는 'standard compliance' 같은 이름표, 14번 실측).
        certification_standard=(item.value or item.attribute) if certification else None,
        certification_listed=item.certification_listed,
        standard_system=item.standard_system,
        designated_model=item.designated_model,
        task=item.task,
        non_equipment=item.kind in ("TASK", "DOCUMENT"),
    )


def to_grading(requirement_id: UUID, item: ExtractedRequirement) -> grading.Requirement | None:
    """추출 요구 → 협력사 등급 입력. 장비·값이 없는 요구는 등급을 매길 수 없어 None이다.

    COMPLIANCE는 자사 비교(to_comparison)와 같은 규칙으로 인증 요구다: attribute='certification', 규격은 value
    우선(없으면 attribute). 그래야 협력사 등급도 certifications 배열로 판정한다(KAN-199). 등록(Listed) 요구는
    grading이 읽는 listing=listed 조건으로, 허용 오차는 자사 비교와 같은 범위로 넘긴다.

    Args:
        requirement_id: 저장한 요구 ID.
        item: 추출 요구.

    Returns:
        grading.Requirement | None: 등급 입력.
    """
    certification = item.kind == "COMPLIANCE"
    operator, value = tolerance_range(item)
    value = (item.value or item.attribute) if certification else value
    conditions = [Condition.model_validate(c.model_dump()) for c in item.conditions]
    if item.certification_listed and not any(
        c.name == "listing" and grading._norm(c.value) == "listed" for c in conditions
    ):
        conditions.append(Condition(name="listing", value="listed"))
    # 속성을 사양 항목에 연결하지 못한 요구(OTHER)는 같은 속성 근거를 찾을 수 없어 등급을 매기지 않는다.
    if (
        item.equipment is None
        or value is None
        or (not certification and item.attribute_key == OTHER)
    ):
        return None
    return grading.Requirement(
        requirement_id=requirement_id,
        equipment=item.equipment,
        task=item.task,
        attribute="certification" if certification else item.attribute_key,
        value=value,
        unit=item.unit,
        operator=operator,
        conditions=conditions,
        standard_system=item.standard_system,
        necessity=item.necessity,
        designated_model=item.designated_model,
        clause_no=item.clause_no,
    )


def pick_documents(versions: Sequence[Mapping[str, Any]], wanted: str) -> list[Mapping[str, Any]]:
    """접수 입력 판본(정렬 ID 해시)과 같은 문서 판본 묶음을 찾는다. 처리 함수는 해시만 받기 때문이다.

    Args:
        versions: 프로젝트 문서 판본 전부.
        wanted: 작업의 input_version.

    Returns:
        list: 그 묶음. 없으면 ValueError(TFT_WORKER_INPUT_NOT_FOUND)다.
    """
    latest = [v for v in versions if v["latest"]]
    if input_version([v["id"] for v in latest]) == wanted:
        return latest
    # ponytail: 판본 MAX_VERSIONS개까지 조합을 다 본다. 넘으면 접수 때 판본 ID를 tft 표에 남기는 쪽으로 바꾼다.
    pool = list(versions)[:MAX_VERSIONS]
    for size in range(1, len(pool) + 1):
        for group in itertools.combinations(pool, size):
            if input_version([v["id"] for v in group]) == wanted:
                return list(group)
    raise ValueError("TFT_WORKER_INPUT_NOT_FOUND")


def lease_keeping(provider: Provider, emit: Emit) -> tuple[Provider, list[BaseException]]:
    """모델 호출이 끝날 때마다 extract 진행을 남겨 lease를 연장하는 공급자.

    절 하나가 여러 번 부를 수 있어(긴 절 나눠 부르기·빠진 문장 보완) 절마다 남기면 lease를 넘을 수 있다. 호출마다
    남기면 사이 간격은 호출 하나(gemini_timeout_ms×(재시도+1)+대기)다. emit이 올린 취소·소유권 상실은 엔진이
    삼키지 않게 담아 두고, 그 뒤 호출은 하지 않는다.

    Args:
        provider: 추출 모델 호출.
        emit: 단계 보고.

    Returns:
        tuple: (감싼 공급자, 멈춤 예외 목록 — 비어 있지 않으면 호출자가 올린다).
    """
    stopped: list[BaseException] = []

    def tracked(prompt: str) -> Generation:
        if stopped:
            raise stopped[0]
        try:
            return provider(prompt)
        finally:
            try:
                emit({"step": "extract"})
            except BaseException as error:
                stopped.append(error)
                raise

    return tracked, stopped


def counts(requirements: int) -> dict[str, int]:
    return {"requirements": requirements, "proposals": 0}


async def process(
    settings: Settings,
    job: TftJob,
    emit: Emit,
    provider: Provider,
    document_root: Path | None,
    products: Sequence[comparison.Product],
    today: date,
) -> dict[str, Any]:
    """rfp.process 한 번(단계 7개).

    Args:
        settings: DB 설정.
        job: 작업.
        emit: 단계 보고(소유권·취소 검사).
        provider: 추출 모델 호출.
        document_root: 원문 폴더.
        products: 자사 제품.
        today: 인증 유효기한 기준일.

    Returns:
        dict: 종료 요약(counts·needs_review).
    """
    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    needs_review = False
    async with await psycopg.AsyncConnection.connect(
        dsn, row_factory=dict_row, autocommit=True
    ) as conn:
        documents = [
            d
            for d in pick_documents(
                await repository.all_document_versions(conn, job.project_id), job.input_version
            )
            if d["kind"] in SPEC_KINDS
        ]
        # parse: 아직 처리하지 않은 판본만 원문을 읽는다. 조항은 여기서 쓰지 않고 compare 트랜잭션에서 요구와 함께
        # 쓴다. 그래서 조항이 있으면 그 판본은 끝까지 처리된 것이다(요구 0건 문서 포함).
        parsed: list[tuple[Mapping[str, Any], ClauseTree, dict[str, Any]]] = []
        for document in documents:
            if not await repository.document_processed(conn, document["id"]):
                if document_root is None:
                    raise ValueError("TFT_DOCUMENT_ROOT_MISSING")
                data = read_document(
                    document_root, document["storage_key"], document["sha256"], document["size"]
                )
                tree = build_clause_tree(extract(data, mime=document["mime"]))
                # 조항 번호 → 조항 행. compare 트랜잭션에서 채운다(추출 요구가 같은 dict를 가리킨다).
                parsed.append((document, tree, {}))
            emit({"step": "parse", "document_version_id": str(document["id"])})

        # extract: 절마다 모델을 부른다. 너무 긴 절·실패한 절은 건너뛰고 검토가 필요하다고 표시한다.
        # 절마다 진행을 남겨 lease를 연장한다(절이 많으면 모델 호출 중 lease가 끝나 다른 worker가 다시 부르지 않게).
        extracted: list[tuple[Mapping[str, Any], dict[str, Any], ExtractedRequirement]] = []
        called = False
        tracked, stopped = lease_keeping(provider, emit)
        for document, tree, clauses in parsed:
            for section in sections_of(tree):
                try:
                    [section_input] = build_inputs(tree, [section])
                except ValueError:
                    needs_review = True
                    continue
                result = await asyncio.to_thread(extract_section, section_input, tracked)
                if stopped:
                    # extract_section은 공급자 예외를 결과 코드로 바꾸므로 취소·소유권 상실을 여기서 다시 올린다.
                    raise stopped[0]
                # 요구별 검토 표시(허용 오차 불일치·목록 밖 키, KAN-185)도 작업 요약에 모은다.
                needs_review |= result.outcome != "VALID" or any(
                    r.needs_review for r in result.requirements
                )
                extracted += [(document, clauses, r) for r in result.requirements]
                emit({"step": "extract", "counts": counts(len(extracted))})
                called = True
        total = len(extracted)
        if not called:
            emit({"step": "extract", "counts": counts(total)})

        # search: 문서 검색기 연결 전에는 요구마다 제품 마스터의 같은 계열 후보가 범위다(compare_in_family, trace에 남음).
        emit({"step": "search", "counts": counts(total)})

        # 요구 장비 계열의 자사 후보 안에서만 비교한다(KAN-199). 계열을 모르면 비교하지 않는다.
        outcomes = [
            comparison.compare_in_family(to_comparison(r), r.equipment_family, products)
            for _, _, r in extracted
        ]
        profiles = [
            PartnerProfile.model_validate({**row, "known_at": row["known_at"].date()})
            for row in await repository.partner_profiles(conn)
        ]
        async with conn.transaction():
            for document, tree, clauses in parsed:
                clauses.update(await repository.store_clauses(conn, document, tree.clauses))
            ids = await store_requirements(conn, job.project_id, extracted, outcomes)
            emit({"step": "compare", "counts": counts(total)})
            for requirement_id, (document, _, item), outcome in zip(ids, extracted, outcomes):
                options = supplement.options_for(
                    outcome, "UNSTATED", designated=item.designated_model is not None
                )
                for option in options.options:
                    await repository.insert_option(
                        conn,
                        requirement_id,
                        option.model_dump(),
                        deficiency_cause=options.deficiency_cause,
                        customer_permission=options.customer_permission,
                        data_origin=document["data_origin"],
                    )
            emit({"step": "supplement", "counts": counts(total)})
            gaps = [
                (to_grading(requirement_id, item), document["data_origin"])
                for requirement_id, (document, _, item), outcome in zip(ids, extracted, outcomes)
                if outcome.status == "SPEC_GAP"
            ]
            targets = [(g, origin) for g, origin in gaps if g is not None]
            if not profiles:
                # 프로필 시드 전(KAN-184): 등급을 매기지 않고 검토가 필요하다고 남긴다.
                needs_review = True
            elif targets:
                origin_of = {g.requirement_id: origin for g, origin in targets}
                context = grading.RfpContext(
                    manufacturer_list=None,
                    off_list_permission="UNSTATED",
                    supply_region=None,
                    requires_integrator=False,
                )
                for profile in profiles:
                    _, grades = grading.grade_candidate(
                        profile, [g for g, _ in targets], context, today
                    )
                    for grade in grades:
                        await repository.insert_grade(
                            conn,
                            project_id=job.project_id,
                            candidate_code=profile.candidate_code,
                            profile_version=profile.profile_version,
                            grade=grade.model_dump(mode="json"),
                            data_origin=origin_of[grade.requirement_id],
                        )
        emit({"step": "candidates", "counts": counts(total)})
        # 초안 생성(A07)이 오기 전에는 건너뛴다(실패 아님).
        emit({"step": "draft", "counts": counts(total)})
    return {"counts": counts(total), "needs_review": needs_review}


async def store_requirements(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    project_id: UUID,
    extracted: Sequence[tuple[Mapping[str, Any], dict[str, Any], ExtractedRequirement]],
    outcomes: Sequence[comparison.Outcome],
) -> list[UUID]:
    """요구·판본 1(판단 포함)·조건·자사 비교를 쓴다.

    Args:
        conn: 진행 중 트랜잭션.
        project_id: 프로젝트 ID.
        extracted: (문서 판본, 조항 번호 → 조항 행, 추출 요구).
        outcomes: 요구마다 비교 결과.

    Returns:
        list[UUID]: 요구 ID(입력 순서).
    """
    ids = []
    seen: dict[tuple[UUID, str], int] = {}
    for (document, clauses, item), outcome in zip(extracted, outcomes, strict=True):
        key = (document["id"], item.clause_no)
        seen[key] = seen.get(key, 0) + 1
        # 문서 판본·조항·순번으로 정한다(같은 프로젝트에서 유일).
        code = f"{document['id'].hex[:8]}:{item.clause_no}:{seen[key]}"
        spec = item.model_dump(
            include={
                "quote",
                "equipment",
                "task",
                "attribute",
                "value",
                "unit",
                "operator",
                "standard_system",
                "necessity",
                "designated_model",
                "designated_manufacturer",
                "assembly_clause",
                "conditions",
            }
        )
        requirement_id = await repository.insert_requirement(
            conn,
            project_id=project_id,
            code=code,
            document_version=document,
            clause=clauses.get(item.clause_no),
            spec=spec,
            judgement={
                "status": outcome.status,
                "reason": outcome.reason,
                "unresolved": outcome.unresolved,
                "next_owner": outcome.next_owner,
            },
        )
        for candidate in outcome.candidates:
            await repository.insert_comparison(
                conn,
                requirement_id,
                candidate.model_dump(mode="json"),
                # 인증 요구는 속성이 아니라 규격으로 비교한다(to_comparison과 같은 기준).
                matched_attribute=None if item.kind == "COMPLIANCE" else item.attribute_key,
                search_trace=outcome.search_trace.model_dump(),
                data_origin=document["data_origin"],
            )
        ids.append(requirement_id)
    return ids
