"""RFP 처리 함수(KAN-199 완료 조건 4, KAN-188 A07 접착). worker가 `build(settings)`로 받는다.

나영님 엔진 함수를 부르는 접착만 한다. 새 판정 규칙은 없다(contracts/tft/rfp-jobs.md '처리 함수' 절).

- rfp.process 단계: parse(원문 → 조항 트리 저장) → extract(절마다 모델 추출) → search(후보 범위) → compare(자사 비교)
  → supplement(보완 선택지) → candidates(협력사 등급) → draft(아직 없음, counts 0).
- 요구·판본(판단 포함)·조건·비교·선택지·등급은 compare부터 candidates까지 한 트랜잭션에 쓴다. 도중에 멈추면 아무것도
  남지 않아 다시 돌리면 처음부터 한다. 조항 트리는 parse 때 판본마다 한 번 쓴다.
- 재실행: 이미 요구 판본을 만든 문서 판본은 다시 처리하지 않는다(모델을 다시 부르지 않음).
- rfp.export·rfp.annotate는 아직 단계가 없어 그 단계 counts 0만 남기고 끝낸다.
"""

import asyncio
import itertools
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.agent.processor_registry import Emit, Processor, TftJob
from backend.api.tft.partners import grading
from backend.api.tft.partners.schemas import Condition, PartnerProfile
from backend.api.tft.rfp import comparison, repository, supplement
from backend.api.tft.rfp.jobs import input_version
from backend.api.tft.rfp.storage import read_document
from backend.engine.documents.router import extract
from backend.engine.rfp.clauses import ClauseTree, build_clause_tree
from backend.engine.rfp.requirements import (
    ExtractedRequirement,
    ResponseSchema,
    build_inputs,
    extract_section,
)
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings

Provider = Callable[[str], Generation]
ZERO = {"requirements": 0, "proposals": 0}
# 요구를 뽑는 문서 종류. 도면·자료표·메모 PDF는 처리 단계에서 읽지 않는다.
SPEC_KINDS = ("RFP_SPEC", "ADDENDUM")
# 접수 입력 판본(input_version)을 찾을 때 조합을 다 볼 문서 판본 수 상한.
MAX_VERSIONS = 12
ONLY_STEP = {"rfp.export": "export", "rfp.annotate": "annotate"}


class ProcessorSettings(BaseSettings):
    """원문 폴더와 추출 모델 선택. fake는 요구를 뽑지 않는 고정 응답이다(키 없는 로컬 시연·검사)."""

    model_config = SettingsConfigDict(env_prefix="TFT_", env_file=".env", extra="ignore")
    document_root: Path | None = None
    extraction_provider: Literal["gemini", "fake"] = "gemini"


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


def gemini_provider() -> Provider:
    """Gemini 구조화 출력 호출(평가 스크립트와 같은 방식, 설정은 환경의 AI_*·GEMINI_*).

    Returns:
        Provider: 지시문 → Generation.
    """
    from backend.shared.ai import AISettings, create_client, generate

    settings = AISettings()
    client = create_client(settings)
    return lambda text: generate(client, settings, text, response_schema=ResponseSchema)


def build(settings: Settings) -> Processor:
    """worker가 부르는 등록 지점.

    Args:
        settings: worker 설정(DB).

    Returns:
        Processor: 처리 함수.
    """
    config = ProcessorSettings()
    provider = fake_provider if config.extraction_provider == "fake" else gemini_provider()
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


def to_comparison(item: ExtractedRequirement) -> comparison.Requirement:
    """추출 요구 → 자사 비교 입력. 매핑 규칙은 이 함수 한 곳에만 둔다.

    기본값(2026-10-30, KAN-186 확인 요청 중): COMPLIANCE는 인증 요구(certification_standard=attribute), 그 밖은
    attribute 그대로, 제품 계열은 정하지 않는다(search 단계가 자사 제품 전체와 비교).

    Args:
        item: 추출 요구.

    Returns:
        comparison.Requirement: 비교 입력.
    """
    certification = item.kind == "COMPLIANCE"
    return comparison.Requirement(
        clause_no=item.clause_no,
        item=item.equipment or item.task or item.attribute,
        attribute=None if certification else item.attribute,
        value=item.value,
        unit=item.unit,
        operator=item.operator,
        conditions=[Condition.model_validate(c.model_dump()) for c in item.conditions],
        necessity=item.necessity,
        certification_standard=item.attribute if certification else None,
        designated_model=item.designated_model,
        task=item.task,
    )


def to_grading(requirement_id: UUID, item: ExtractedRequirement) -> grading.Requirement | None:
    """추출 요구 → 협력사 등급 입력. 장비·값이 없는 요구는 등급을 매길 수 없어 None이다.

    Args:
        requirement_id: 저장한 요구 ID.
        item: 추출 요구.

    Returns:
        grading.Requirement | None: 등급 입력.
    """
    if item.equipment is None or item.value is None:
        return None
    return grading.Requirement(
        requirement_id=requirement_id,
        equipment=item.equipment,
        task=item.task,
        attribute=item.attribute,
        value=item.value,
        unit=item.unit,
        operator=item.operator,
        conditions=[Condition.model_validate(c.model_dump()) for c in item.conditions],
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
        # parse: 아직 처리하지 않은 판본만 원문을 읽는다.
        parsed: list[tuple[Mapping[str, Any], ClauseTree, dict[str, Any]]] = []
        for document in documents:
            if not await repository.document_processed(conn, document["id"]):
                if document_root is None:
                    raise ValueError("TFT_DOCUMENT_ROOT_MISSING")
                data = read_document(
                    document_root, document["storage_key"], document["sha256"], document["size"]
                )
                tree = build_clause_tree(extract(data, mime=document["mime"]))
                async with conn.transaction():
                    clauses = await repository.store_clauses(conn, document, tree.clauses)
                parsed.append((document, tree, clauses))
            emit({"step": "parse", "document_version_id": str(document["id"])})

        # extract: 절마다 모델을 부른다. 너무 긴 절·실패한 절은 건너뛰고 검토가 필요하다고 표시한다.
        extracted: list[tuple[Mapping[str, Any], dict[str, Any], ExtractedRequirement]] = []
        for document, tree, clauses in parsed:
            for section in sections_of(tree):
                try:
                    [section_input] = build_inputs(tree, [section])
                except ValueError:
                    needs_review = True
                    continue
                result = await asyncio.to_thread(extract_section, section_input, provider)
                needs_review |= result.outcome != "VALID"
                extracted += [(document, clauses, r) for r in result.requirements]
        total = len(extracted)
        emit({"step": "extract", "counts": counts(total)})

        # search: 검색기 연결 전에는 자사 제품 전체가 범위다(범위는 trace에 남는다).
        trace = comparison.SearchTrace(
            filters=["own_products=all"], documents_searched=0, search_failed=False
        )
        emit({"step": "search", "counts": counts(total)})

        outcomes = [comparison.compare(to_comparison(r), products, trace) for _, _, r in extracted]
        profiles = [
            PartnerProfile.model_validate({**row, "known_at": row["known_at"].date()})
            for row in await repository.partner_profiles(conn)
        ]
        async with conn.transaction():
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
                matched_attribute=item.attribute,
                search_trace=outcome.search_trace.model_dump(),
                data_origin=document["data_origin"],
            )
        ids.append(requirement_id)
    return ids
