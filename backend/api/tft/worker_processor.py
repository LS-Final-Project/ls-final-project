"""RFP 처리 함수(KAN-199 완료 조건 4, KAN-188 A07 접착). worker가 `build(settings)`로 받는다.

나영님 엔진 함수를 부르는 접착만 한다. 새 판정 규칙은 없다(contracts/tft/rfp-jobs.md '처리 함수' 절).

- rfp.process 단계: parse(원문 → 조항 트리 저장) → extract(절마다 모델 추출) → search(후보 범위) → compare(자사 비교)
  → supplement(보완 선택지) → candidates(협력사 등급) → draft(근거 있는 제안 검토 초안 저장).
- 조항·요구·판본(판단 포함)·조건·비교·선택지·등급은 compare부터 candidates까지 한 트랜잭션에 쓴다. 도중에 멈추면
  아무것도 남지 않아 다시 돌리면 처음부터 한다.
- 재실행: 조항이 저장된 문서 판본은 재추출하지 않는다. 현행 저장 요구는 무모델로 재채점하고 초안으로 잇는다.
  이미 커밋된 같은 job의 초안이 있으면 등급도 중복 저장하지 않는다.
- rfp.export는 저장된 검토본 DOCX를 만들고, 범위 밖인 rfp.annotate는 명시적으로 실패한다.
"""

import asyncio
import hashlib
import itertools
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from decimal import Decimal
from threading import RLock, local
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.agent.processor_registry import Emit, Processor, TftJob
from backend.agent.tft_worker import TftWorkerSettings
from backend.api.tft.partners import grading
from backend.api.tft.partners.loader import PROFILE_FAMILIES, load_document_pools
from backend.api.tft.partners.schemas import Condition, PartnerProfile
from backend.api.tft.rfp import (
    catalog,
    comparison,
    diagram_artifacts,
    diagram_repository,
    draft_exports,
    draft_repository,
    draft_rewrite,
    drafts,
    integration,
    repository,
    supplement,
)
from backend.api.tft.rfp.jobs import input_version
from backend.api.tft.rfp.storage import (
    DocumentStorageSettings,
    DocumentStore,
    document_store,
    read_document,
)
from backend.engine.documents import vision
from backend.engine.documents.router import extract
from backend.engine.rfp import structure_repair
from backend.engine.rfp.clauses import ClauseTree, build_clause_tree, is_korean_section
from backend.engine.rfp.context_graph import ContextProvider, Stage, create_gemini_context_provider
from backend.engine.rfp.context_graph import fake_provider as fake_context_provider
from backend.engine.rfp.context_models import ContextProposal
from backend.engine.rfp.questions import QuestionDraft, generate_question_drafts
from backend.engine.rfp.requirements import (
    ExtractedRequirement,
    ExtractionInput,
    Operator,
    ResponseSchema,
    SectionResult,
    StandardSystem,
    build_inputs,
    extract_section,
)
from backend.engine.rfp.vocabulary import OTHER
from backend.shared.ai import AISettings, Generation, Usage, check_lease
from backend.shared.settings import Settings

Provider = Callable[[str], Generation]
ZERO = {"requirements": 0, "proposals": 0}
# 도면은 별도 연결 후보와 명시된 OCR 문자만 보존한다. 자료표·메모 PDF는 포함하지 않는다.
SPEC_KINDS = ("RFP_SPEC", "ADDENDUM", "DRAWING")
# 접수 입력 판본(input_version)을 찾을 때 조합을 다 볼 문서 판본 수 상한.
MAX_VERSIONS = 12


class ProcessorSettings(BaseSettings):
    """추출 모델 설정. fake는 요구를 뽑지 않는 고정 응답이다(키 없는 로컬 시연·검사).

    추출 값은 공통 AI 기본값(출력 1024토큰)을 쓰지 않는다. 기본은 평가 스크립트(scripts/rfp_requirements_eval.py)와
    같은 모델·출력 상한이고, 시간 제한은 lease 안에 들도록 120초다. 키·공급자·재시도는 공통 AI 설정(GEMINI_*)이다.
    """

    model_config = SettingsConfigDict(env_prefix="TFT_", env_file=".env", extra="ignore")
    extraction_concurrency: int = Field(default=6, ge=1, le=6)
    # 요구 추출(구조 보정 포함) 호출의 thinking 수준. None이면 모델 기본(설정을 보내지 않음).
    # 기본 LOW: 공식 정답셋 재측정(참고값)에서 시간·비용이 절반이고 필수 제약 위반 0이었다(2026-11-06 rfp-timing 보고서).
    extraction_thinking_level: Literal["LOW", "MEDIUM", "HIGH"] | None = "LOW"
    # 맥락 정리(설비 묶기·연결) 호출의 thinking 수준. None이면 모델 기본. LOW는 묶음이 거의 안 돼(D22 묶음 183→904) 쓰지 않는다.
    context_thinking_level: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    extraction_provider: Literal["gemini", "fake"] = "gemini"
    extraction_model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    extraction_max_output_tokens: int = Field(default=16384, ge=1, le=65536)
    extraction_timeout_ms: int = Field(default=120000, ge=1000, le=300000)
    draft_rewrite_provider: Literal["gemini", "fake"] | None = None
    draft_rewrite_model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    draft_rewrite_max_output_tokens: int = Field(default=8192, ge=1, le=65536)
    draft_rewrite_timeout_ms: int = Field(default=120000, ge=1000, le=300000)
    # KAN-224 회신 값 AI 읽기(rfp.reply_read). 공급자는 추출 공급자(fake·gemini)를 따른다.
    reply_read_model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    reply_read_max_output_tokens: int = Field(default=2048, ge=256, le=65536)
    # KAN-188 번호 빈자리만 모델 경계로 나누기(structure_repair). 기본 켬(2026-11-06 측정: 재현율 +약 10건).
    # 추출과 같은 공급자·모델·출력 상한·시간 제한·thinking 수준을 쓴다. 끄면 모델 보정 호출만 빠진다(결정 파서 규칙은 그대로).
    structure_repair_enabled: bool = True


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


def gemini_provider(ai: AISettings, thinking_level: str | None = None) -> Provider:
    """Gemini 구조화 출력 호출(평가 스크립트와 같은 방식).

    Args:
        ai: 추출 호출 설정(extraction_settings).
        thinking_level: thinking 수준(TFT_EXTRACTION_THINKING_LEVEL). None이면 보내지 않는다.

    Returns:
        Provider: 지시문 → Generation.
    """
    from backend.shared.ai import create_client, generate

    client = create_client(ai)
    if thinking_level is None:
        return lambda text: generate(client, ai, text, response_schema=ResponseSchema)
    return lambda text: generate(
        client, ai, text, response_schema=ResponseSchema, thinking_level=thinking_level
    )


def rewrite_provider(config: ProcessorSettings) -> Provider:
    """실제 rewrite 작업이 호출할 때만 클라이언트를 만든다. 논리 호출당 운송 재시도는 하지 않는다."""
    from backend.engine.rfp.draft_rewrite import RewriteOutput
    from backend.engine.rfp.draft_rewrite import fake_provider as fake_rewrite

    if (config.draft_rewrite_provider or config.extraction_provider) == "fake":
        return fake_rewrite
    ai = AISettings(
        gemini_model=config.draft_rewrite_model,
        gemini_max_output_tokens=config.draft_rewrite_max_output_tokens,
        gemini_timeout_ms=config.draft_rewrite_timeout_ms,
        gemini_transient_retries=0,
    )
    check_lease(ai, TftWorkerSettings().lease_seconds)
    client = None

    def provide(text: str) -> Generation:
        nonlocal client
        from backend.shared.ai import create_client, generate

        if client is None:
            client = create_client(ai)
        # 글을 다듬는 일이라 깊은 추론이 필요 없다. 기본 thinking은 호출 비용의 대부분이었다(D21 실측).
        return generate(client, ai, text, response_schema=RewriteOutput, thinking_level="LOW")

    return provide


def reply_provider(config: ProcessorSettings) -> Provider:
    """회신 값 읽기 공급자. 실제 작업이 부를 때만 클라이언트를 만든다. fake는 값을 찾지 못하는 고정 응답이다."""
    if config.extraction_provider == "fake":
        return lambda _prompt: Generation(
            text='{"value": null}', usage=Usage(), model_version=None, finish_reasons=[]
        )
    ai = AISettings(
        gemini_model=config.reply_read_model,
        gemini_max_output_tokens=config.reply_read_max_output_tokens,
        gemini_transient_retries=0,
    )
    check_lease(ai, TftWorkerSettings().lease_seconds)
    client = None

    def provide(text: str) -> Generation:
        nonlocal client
        from backend.engine.rfp.reply_values import AiOutput
        from backend.shared.ai import create_client, generate

        if client is None:
            client = create_client(ai)
        # 짧은 회신에서 값 하나를 고르는 일이라 깊은 추론이 필요 없다.
        return generate(client, ai, text, response_schema=AiOutput, thinking_level="LOW")

    return provide


def structure_provider(config: ProcessorSettings) -> Provider | None:
    """구조 보정 공급자. 꺼져 있으면 None이고, 실제로 부를 때만 클라이언트를 만든다."""
    if not config.structure_repair_enabled:
        return None
    if config.extraction_provider == "fake":
        return structure_repair.fake_provider
    ai = extraction_settings(config)
    client = None

    def provide(text: str) -> Generation:
        nonlocal client
        from backend.shared.ai import create_client, generate

        if client is None:
            client = create_client(ai)
        return generate(
            client,
            ai,
            text,
            response_schema=structure_repair.RepairOutput,
            thinking_level=config.extraction_thinking_level,
        )

    return provide


def repair_structure(
    tree: ClauseTree, provider: Provider | None, emit: Emit, document_version_id: str
) -> tuple[ClauseTree, bool]:
    """번호 빈자리만 모델 경계로 나눈다(문서마다 한 번, 추출 전). 모델이 나눈 조항이 있으면 검토가 필요하다.

    호출 앞뒤로 진행을 남겨 lease·소유권을 확인한다(이미지 전사와 같다). 공급자·lease 예외는 그대로 올라간다.
    """
    if provider is None:
        return tree, False

    def tracked(prompt: str) -> Generation:
        emit({"step": "parse", "document_version_id": document_version_id})
        try:
            return provider(prompt)
        finally:
            emit({"step": "parse", "document_version_id": document_version_id})

    result = structure_repair.repair_tree(tree, tracked)
    return result.tree, bool(result.applied)


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
        provider = gemini_provider(ai, config.extraction_thinking_level)
    context_provider: ContextProvider | None = fake_context_provider
    if config.extraction_provider == "gemini":
        # SDK creation is lazy; exports and previously completed jobs do not create a model client.
        context_client: ContextProvider | None = None

        async def organize(stage: Stage, payload: dict[str, Any]) -> ContextProposal:
            nonlocal context_client
            if context_client is None:
                context_client = create_gemini_context_provider(
                    extraction_settings(config), config.context_thinking_level
                )
            return await context_client(stage, payload)

        context_provider = organize
    return make_processor(
        settings,
        provider=provider,
        store=document_store(DocumentStorageSettings()),
        draft_provider=rewrite_provider(config),
        rewrite_model=config.draft_rewrite_model,
        reply_provider=reply_provider(config),
        structure_provider=structure_provider(config),
        extraction_concurrency=config.extraction_concurrency,
        context_provider=context_provider,
        image_provider=(
            vision.gemini_image_provider(extraction_settings(config))
            if config.extraction_provider == "gemini"
            else None
        ),
    )


def make_processor(
    settings: Settings,
    *,
    provider: Provider,
    store: DocumentStore | None,
    products: Sequence[comparison.Product] | None = None,
    today: Callable[[], date] = date.today,
    draft_provider: Provider | None = None,
    rewrite_model: str = "gemini-3.6-flash",
    extraction_concurrency: int = 6,
    image_provider: Callable[[vision.ImageInput], Generation] | None = None,
    context_provider: ContextProvider | None = None,
    reply_provider: Provider | None = None,
    structure_provider: Provider | None = None,
) -> Processor:
    """처리 함수를 만든다(검사는 추출 공급자·원문 폴더를 바꿔 끼운다).

    Args:
        settings: DB 설정.
        provider: 추출 모델 호출.
        store: 원문 저장소. 없으면 원문이 필요한 작업은 실패한다.
        products: 자사 제품(기본은 own_spec_facts 전체).
        today: 인증 유효기한 기준일.
        structure_provider: 번호 빈자리 보정 호출. None이면 부르지 않는다(기본).

    Returns:
        Processor: `processor(job, emit)`.
    """
    # 주입하지 않으면 작업마다 DB에서 읽는다(제품 목록 화면 판본이 다음 작업부터 반영, KAN-246).
    own = list(products) if products is not None else None

    def processor(job: TftJob, emit: Emit) -> dict[str, Any] | None:
        from backend.api.tft.rfp.context_worker import process_context

        if job.kind == "rfp.context":
            if context_provider is None:
                raise ValueError("TFT_CONTEXT_PROVIDER_MISSING")
            return asyncio.run(process_context(settings, job, emit, context_provider))
        if job.kind == "rfp.draft_rewrite":
            if draft_provider is None:
                raise ValueError("TFT_DRAFT_REWRITE_PROVIDER_MISSING")
            return asyncio.run(rewrite_job(settings, job, emit, draft_provider, rewrite_model))
        if job.kind == "rfp.export":
            return asyncio.run(export_job(settings, job, emit, store))
        if job.kind == "rfp.reply_read":
            if reply_provider is None:
                raise ValueError("TFT_REPLY_READ_PROVIDER_MISSING")
            return asyncio.run(reply_job(settings, job, reply_provider))
        if job.kind == "rfp.annotate":
            raise ValueError("TFT_ANNOTATION_UNAVAILABLE")
        result = asyncio.run(
            process(
                settings,
                job,
                emit,
                provider,
                store,
                own,
                today(),
                extraction_concurrency,
                image_provider,
                structure_provider,
            )
        )
        if context_provider is not None:
            organized = asyncio.run(
                process_context(settings, job, emit, context_provider, check_input=False)
            )
            result["needs_review"] = bool(result.get("needs_review") or organized["needs_review"])
        return result

    return processor


async def rewrite_job(
    settings: Settings, job: TftJob, emit: Emit, provider: Provider, model: str
) -> dict[str, Any]:
    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    async with await psycopg.AsyncConnection.connect(
        dsn, row_factory=dict_row, autocommit=True
    ) as conn:
        return await draft_rewrite.process(conn, job, emit, provider, model)


async def reply_job(settings: Settings, job: TftJob, provider: Provider) -> dict[str, Any]:
    from backend.api.tft.rfp import reply_read

    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    async with await psycopg.AsyncConnection.connect(
        dsn, row_factory=dict_row, autocommit=True
    ) as conn:
        return await reply_read.process(conn, job, provider)


async def export_job(
    settings: Settings, job: TftJob, emit: Emit, store: DocumentStore | None
) -> dict[str, Any]:
    """기존 작업 런타임의 소유권 검사를 유지하며 예약된 검토본을 내보낸다."""
    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        return await draft_exports.process(conn, job, emit, store)


def sections_of(tree: ClauseTree) -> list[str]:
    """추출할 절: 번호로 하위 조항을 묶는 조항 중 가장 위의 것(PART 2 아래의 2.1, 2.2 등).

    Args:
        tree: 조항 트리.

    Returns:
        list[str]: 절 번호(트리 순서). 본문이 있는 한글 leaf 절도 포함한다.
    """
    numbers = [c.clause_no for c in tree.clauses]
    groups = {n for n in numbers if any(m.startswith(n + ".") for m in numbers)}
    parents = {c.clause_no: c.parent_no for c in tree.clauses}
    parents_with_children = set(parents.values())
    leaves = {
        c.clause_no
        for c in tree.clauses
        if is_korean_section(c)
        and c.clause_no not in groups
        and c.clause_no not in parents_with_children
        and (c.text.strip() or c.table_rows)
    }
    out = []
    for no in numbers:
        if no not in groups | leaves:
            continue
        parent = parents[no]
        # 한글 장 번호와 독립된 1.1 절은 제8장.7 같은 앞 항목의 번호 묶음에 포함되지 않는다.
        if parent is not None and not no.startswith(parent + "."):
            parent = None
        while parent is not None and parent not in groups:
            parent = parents.get(parent)
        if parent is None:
            out.append(no)
    return out


def document_inputs(tree: ClauseTree) -> tuple[list[ExtractionInput], bool]:
    """모델 호출 전에 문서 입력을 검사한다. 전절 거절은 처리 완료로 저장하지 않는다."""
    inputs: list[ExtractionInput] = []
    skipped = False
    for section in sections_of(tree):
        try:
            [item] = build_inputs(tree, [section])
        except ValueError:
            skipped = True
            continue
        if item.clauses:
            inputs.append(item)
    if not inputs:
        raise ValueError("TFT_DOCUMENT_EXTRACTION_INPUT_INVALID")
    return inputs, skipped


# 질문 초안 종류 → 질문 유형. 승인 절차는 demo_cases(2.1 제조사 목록)와 같이 조건 누락으로 본다.
QUESTION_ISSUES = {
    "PLACEHOLDER": "EMPTY_VALUE",
    "INCONSISTENCY": "CONFLICT",
    "APPROVAL_PROCESS": "CONDITION_MISSING",
}
# 허용 오차 표기: 대칭(±10%, +/-0.5)과 비대칭(+0/-5%, +10%/-5%). 숫자 부분만 읽는다.
SYMMETRIC = re.compile(r"^\s*(?:±|\+\s*/\s*-|\+-)\s*(\d+(?:\.\d+)?)\s*%?\s*$")
ASYMMETRIC = re.compile(r"^\s*\+\s*(\d+(?:\.\d+)?)\s*%?\s*/\s*-\s*(\d+(?:\.\d+)?)\s*%?\s*$")


# 값이 높을수록 만족하는 장비 정격(전압·절연·전류·차단 용량). 요구는 늘 하한이다. "15 kV maximum"(계통 최고전압)처럼
# maximum이 붙어도 장비 정격은 그 이상이어야 한다. 추출이 LTE로 내면 GTE로 바로잡는다(KAN-208, 비교 방향 오독).
MINIMUM_RATINGS = frozenset(
    {
        "rated_max_voltage",
        "rated_operational_voltage",
        "bil",
        "power_frequency_withstand",
        "short_time_withstand_current",
        "rated_current",
        "busbar_rated_current",
        "breaking_capacity_icu",
        "breaking_capacity_ics",
        "rated_short_circuit_breaking_current",
    }
)


def rating_operator(item: ExtractedRequirement) -> Operator:
    """비교 연산자. 하한 정격(MINIMUM_RATINGS)의 LTE·EQ는 GTE로 바로잡는다. 원문·저장 값은 그대로다(KAN-208).

    정격은 높을수록 만족하므로 "정격 전압 11 kV"(계통 11 kV)도 12 kV·25.8 kV 제품이 충족이다. EQ로 비교하면
    값이 다르다고 사양 차이가 났다(40444 P26-0003). 허용 오차가 붙은 EQ는 tolerance_range가 범위로 바꾼다.
    """
    if item.operator in ("LTE", "EQ") and item.attribute_key in MINIMUM_RATINGS:
        return "GTE"
    return item.operator


SectionKey = tuple[str, str | None]


def section_systems(items: Iterable[ExtractedRequirement]) -> dict[SectionKey, StandardSystem]:
    """절(조항 번호 앞 두 자리, 예 2.6)과 장비 계열마다 하나로 정해진 규격 체계. 둘 이상이거나 없으면 넣지 않는다.

    장비 계열까지 키로 써서, 한 절에 개폐반 IEEE 조항과 다른 계열(변압기 등) 정격이 섞여도 체계를 옮기지 않는다
    (리뷰 P3). 계열이 없는 요구(과업·인증 이름표)는 같은 절의 계열 없는 요구끼리만 묶인다.

    IEEE·UL 체계가 상위 조항("Comply with IEEE C37.20.3")에만 적혀 있으면 아래 항목 요구의 standard_system이 비어
    IEC 근거로 충족이 확정됐다(KAN-208). 같은 절의 체계를 이어받아 충족을 조건부로 낮추는 데만 쓴다.
    """
    found: dict[SectionKey, set[StandardSystem]] = {}
    for item in items:
        if item.standard_system != "NONE":
            found.setdefault(_section(item), set()).add(item.standard_system)
    return {key: next(iter(systems)) for key, systems in found.items() if len(systems) == 1}


def _section(item: ExtractedRequirement) -> SectionKey:
    return _article(item.clause_no), item.equipment_family


def _article(clause_no: str) -> str:
    return ".".join(clause_no.split(".")[:2])


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
        # 문장형 요구는 value가 비어도 비교용 표기로 비교한다.
        return rating_operator(item), item.value or item.canonical_value
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


def to_comparison(
    item: ExtractedRequirement,
    systems: dict[SectionKey, StandardSystem] | None = None,
    voltages: Mapping[str, Decimal] | None = None,
    insulations: Mapping[str, comparison.Insulation] | None = None,
) -> comparison.Requirement:
    """추출 요구 → 자사 비교 입력. 매핑 규칙은 이 함수 한 곳에만 둔다.

    COMPLIANCE는 인증 요구(certification_standard=value, 없으면 attribute), 그 밖은 추출의 attribute_key(자사 사양
    어휘, 연결하지 못했으면 OTHER → 비교하지 않음)다. 제품 계열은 equipment_family로 compare_in_family가 고른다
    (KAN-199). 허용 오차가 있는 명목값은 범위로 바꾸고(tolerance_range), 등록(Listed) 요구와 규격 체계도 넘긴다.
    저장하는 요구 판본의 attribute·value는 추출 원문 그대로다.

    Args:
        item: 추출 요구.
        systems: 같은 문서의 절별 규격 체계(section_systems). 요구 체계가 비어 있는 하한 정격(MINIMUM_RATINGS)만
            이어받는다(KAN-208).
        voltages: 같은 문서의 설비별 최고 전압(comparison.equipment_voltages). 전압 등급이 다른 자사 제품을 후보에서
            뺀다(KAN-217).
        insulations: 같은 문서의 설비별 변압기 절연 형식(comparison.equipment_insulations). 건식 요구에 유입 제품을
            쓰지 않는다(KAN-217).

    Returns:
        comparison.Requirement: 비교 입력.
    """
    certification = item.kind == "COMPLIANCE"
    system = item.standard_system
    # 시험 규격에 기대는 물리 정격만 이어받는다. 탭·보호 기능·형식 같은 구성은 규격 체계와 무관하다(시연 B-2 LSIG).
    if system == "NONE" and systems and item.attribute_key in MINIMUM_RATINGS:
        # 같은 계열의 체계가 없으면 같은 절의 계열 없는 상위 조항("Comply with IEEE …")의 체계를 쓴다(리뷰 P3).
        system = systems.get(_section(item)) or systems.get(
            (_article(item.clause_no), None), "NONE"
        )
    operator, value = tolerance_range(item)
    return comparison.Requirement(
        clause_no=item.clause_no,
        item=item.equipment or item.task or item.attribute,
        attribute=None if certification else item.attribute_key,
        attribute_label=item.attribute,
        value=value,
        unit=item.unit,
        operator=operator,
        conditions=[Condition.model_validate(c.model_dump()) for c in item.conditions],
        necessity=item.necessity,
        # 추출은 규격을 value에 둔다(attribute는 'standard compliance' 같은 이름표, 14번 실측).
        certification_standard=(item.value or item.attribute) if certification else None,
        certification_listed=item.certification_listed,
        standard_system=system,
        designated_model=item.designated_model,
        basis_of_design=item.basis_of_design,
        canonical_value=item.canonical_value,
        task=item.task,
        kind=item.kind,
        equipment_voltage_kv=(voltages or {}).get(comparison.equipment_key(item.equipment)),
        equipment_insulation=(insulations or {}).get(comparison.equipment_key(item.equipment)),
    )


def item_insulations(items: Iterable[ExtractedRequirement]) -> dict[str, comparison.Insulation]:
    """한 문서 요구들의 설비별 변압기 절연 형식(to_comparison의 insulations)."""
    return comparison.equipment_insulations((i.equipment, i.attribute_key, i.value) for i in items)


def item_voltages(items: Iterable[ExtractedRequirement]) -> dict[str, Decimal]:
    """한 문서 요구들의 설비별 최고 전압(to_comparison의 voltages)."""
    return comparison.equipment_voltages(
        (i.equipment, i.attribute_key, i.value, i.unit) for i in items
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
        attribute_label=item.attribute,
        value=value,
        unit=item.unit,
        operator=operator,
        conditions=conditions,
        # 절 단위 체계 이어받기(section_systems)는 자사 비교에만 쓴다(KAN-208 승인 범위, 계약 rfp-comparison.md).
        # 협력사 등급은 원래대로 요구 자신의 체계만 본다.
        standard_system=item.standard_system,
        necessity=item.necessity,
        designated_model=item.designated_model,
        clause_no=item.clause_no,
        canonical_value=item.canonical_value,
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


_FOCUS_DROP = re.compile(r"[^0-9A-Za-z가-힣 .,()·/~-]")


def focus(kind: str, text: str, n: int | None = None) -> dict[str, Any]:
    """진행 화면 한 줄의 재료(KAN-232). 허용 문자만 남겨 40자로 자른다(tft_worker.FOCUS_LABEL)."""
    label = " ".join(_FOCUS_DROP.sub(" ", text).split())[:40].strip() or "문서"
    return {"kind": kind, "label": label} | ({} if n is None else {"n": n})


def section_label(item: Any) -> str:
    """절 번호와 제목(예 "제7조 건식 변압기"). 시험의 가짜 입력은 그 값 자체를 쓴다.

    한국어 "제n조 제목" 머리는 제목 칸이 비고 머리 조항 본문이 제목이다(build_inputs가 맨 앞에 넣는다).
    """
    no = getattr(item, "section_no", item)
    title = getattr(item, "section_title", None)
    if not title:
        head = next(iter(getattr(item, "clauses", None) or []), None)
        if head is not None and head.clause_no == no and len(head.text.strip()) <= 30:
            title = head.text.strip()
    return f"{no} {title or ''}"


def lease_keeping(
    provider: Provider, emit: Emit, reading: Callable[[], str | None] = lambda: None
) -> tuple[Provider, list[BaseException]]:
    """Serialize lease events and stop coverage calls after ownership/accounting loss.

    reading: 이 호출이 읽는 절(진행 화면 한 줄, KAN-232). 새 이벤트를 만들지 않고 호출마다 남기는 진행에 싣는다.
    """
    stopped: list[BaseException] = []
    lock = RLock()

    def tracked(prompt: str) -> Generation:
        with lock:
            if stopped:
                raise stopped[0]
        try:
            return provider(prompt)
        except BaseException as error:
            # Ordinary provider errors retain the engine's typed fallback contract.
            if not isinstance(error, Exception):
                with lock:
                    if not stopped:
                        stopped.append(error)
            raise
        finally:
            with lock:
                if not stopped:
                    try:
                        label = reading()
                        emit(
                            {"step": "extract"}
                            | ({} if label is None else {"focus": focus("section", label)})
                        )
                    except BaseException as error:
                        stopped.append(error)
                        raise

    return tracked, stopped


def extract_sections(
    inputs: Sequence[ExtractionInput], provider: Provider, emit: Emit, *, concurrency: int = 6
) -> list[SectionResult]:
    """At most six sections in flight; drain started calls before returning/raising.

    Each section keeps its existing sequential splitting/coverage rules. Only the
    independent sections overlap. Storage order is input order, never completion order.
    """
    if type(concurrency) is not int or not 1 <= concurrency <= 6:
        raise ValueError("INVALID_EXTRACTION_CONCURRENCY")
    lock = RLock()

    def serialized_emit(values: dict[str, Any]) -> None:
        with lock:
            emit(values)

    current = local()
    tracked, stopped = lease_keeping(
        provider, serialized_emit, lambda: getattr(current, "label", None)
    )
    results: dict[int, SectionResult] = {}
    total = 0

    def run(index: int) -> SectionResult:
        with lock:
            if stopped:
                raise stopped[0]
        try:
            current.label = section_label(inputs[index])
            result = extract_section(inputs[index], tracked)
            with lock:
                if stopped:
                    raise stopped[0]
            return result
        except BaseException as error:
            with lock:
                if not stopped:
                    stopped.append(error)
            raise

    # shutdown(wait=True) drains network calls and their cost settlements on failure.
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="rfp-section") as pool:
        next_index = 0
        pending = {}
        while next_index < min(concurrency, len(inputs)):
            pending[pool.submit(run, next_index)] = next_index
            next_index += 1
        try:
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    result = future.result()
                    with lock:
                        if stopped:
                            raise stopped[0]
                        results[index] = result
                        total += len(result.requirements)
                        serialized_emit(
                            {
                                "step": "extract",
                                "counts": counts(total),
                                "focus": focus(
                                    "found", section_label(inputs[index]), len(result.requirements)
                                ),
                            }
                        )
                with lock:
                    if stopped:
                        raise stopped[0]
                    while len(pending) < concurrency and next_index < len(inputs):
                        pending[pool.submit(run, next_index)] = next_index
                        next_index += 1
        except BaseException as error:
            with lock:
                if not stopped:
                    stopped.append(error)
            for future in pending:
                future.cancel()
            raise
    return [results[index] for index in range(len(inputs))]


def counts(requirements: int) -> dict[str, int]:
    return {"requirements": requirements, "proposals": 0}


# 협력사 등급 대상. 충족·조건부는 보완 대상이 아니다.
GRADED_STATUSES = frozenset({"SPEC_GAP", "INSUFFICIENT_EVIDENCE"})
# ponytail: 문서에 데이터셋 번호 칸이 없어 제목에서 읽는다. 풀은 시연·평가 전용 가상 후보라 칸을 만들 값이 아니다.
# 제목에 RFP-번호가 없으면 풀을 못 쓰고 전체 후보가 된다. 실제 후보 풀을 운영하게 되면 문서에 칸을 둔다.
DOCUMENT_NUMBER = re.compile(r"\bRFP-(\d+)\b")


def integration_of(
    items: Iterable[ExtractedRequirement], clause_texts: Mapping[str, str]
) -> integration.IntegrationResponsibility:
    """추출 요구로 통합 책임 필요 여부를 정한다.

    Args:
        items: 이번 처리의 추출 요구.
        clause_texts: 조항 번호별 현행 원문(원문 문형 규칙에 쓴다).

    Returns:
        integration.IntegrationResponsibility: 필요 여부와 근거 조항.
    """
    return integration.integration_responsibility(
        [
            integration.Requirement(
                clause_no=r.clause_no,
                task=r.task,
                assembly_clause=r.assembly_clause,
                clause_text=clause_texts.get(r.clause_no),
            )
            for r in items
        ]
    )


def candidate_pool(titles: Iterable[str]) -> frozenset[str] | None:
    """처리 문서들의 후보 풀. 문서 제목의 데이터셋 번호(RFP-14)로 찾는다.

    Args:
        titles: 문서 제목들.

    Returns:
        frozenset[str] | None: 풀의 합집합. 번호나 풀을 모르는 문서가 있으면 None(후보를 줄이지 않는다).
    """
    pools = load_document_pools()
    picked: set[str] = set()
    known = False
    for title in titles:
        found = DOCUMENT_NUMBER.search(title)
        pool = pools.get(found.group(1)) if found else None
        if pool is None:
            return None
        picked.update(pool)
        known = True
    return frozenset(picked) if known else None


def outside_pool(profile: PartnerProfile, pool: frozenset[str] | None) -> bool:
    """후보가 문서 풀 밖인가. 화면에서 새로 등록해 어느 풀에도 없는 협력사(KAN-246)만 모든 문서에서 채점한다.

    시드 프로필과, 풀에 있는 시드 협력사를 화면에서 고친 판본은 원래 풀을 따른다.
    """
    if pool is None or profile.candidate_code in pool:
        return False
    if profile.profile_source != "registered":
        return True
    return any(profile.candidate_code in codes for codes in load_document_pools().values())


def grading_groups(
    targets: Sequence[tuple[grading.Requirement, str | None]],
    profiles: Sequence[PartnerProfile],
    pool: frozenset[str] | None,
) -> list[tuple[PartnerProfile, list[grading.Requirement]]]:
    """후보별로 채점할 요구를 고른다. 풀 안의 후보를 자기 계열 요구로만 채점한다.

    한 문서에 여러 계열이 있으면 다른 계열 요구가 후보를 EXCLUDED로 만든다(14번 B-1·B-2). 계열을 모르는 요구는 어느
    후보도 채점하지 않는다.

    Args:
        targets: (요구, 추출 equipment_family).
        profiles: 협력사 프로필.
        pool: 후보 풀. None이면 줄이지 않는다.

    Returns:
        list: (후보, 그 후보의 요구). 채점할 요구가 없는 후보는 뺀다.
    """
    groups = []
    for profile in profiles:
        if outside_pool(profile, pool):
            continue
        families = PROFILE_FAMILIES.get(profile.product_family, frozenset())
        mine = [g for g, wanted in targets if wanted in families]
        if mine:
            groups.append((profile, mine))
    return groups


def stored_extraction(row: Mapping[str, Any]) -> ExtractedRequirement | None:
    """보존된 추출값만 복원한다. 과거 미보존 kind/Listed는 추론하거나 false로 만들지 않는다."""
    if row.get("kind") is None or row.get("certification_listed") is None:
        return None
    names = (
        "quote",
        "kind",
        "equipment",
        "task",
        "attribute",
        "attribute_key",
        "equipment_family",
        "value",
        "unit",
        "operator",
        "conditions",
        "standard_system",
        "necessity",
        "designated_model",
        "designated_manufacturer",
        "assembly_clause",
        "tolerance",
        "needs_review",
        "certification_listed",
        "canonical_value",
    )
    return ExtractedRequirement.model_validate(
        {
            **{name: row[name] for name in names if name in row},
            "clause_no": row.get("clause_no") or "",
        }
    )


def legacy_grade(
    row: Mapping[str, Any],
    profile: PartnerProfile,
    context: grading.RfpContext,
    previous: Mapping[str, Any] | None,
) -> grading.RequirementGrade:
    """과거 추출 의미가 없으면 사실 확인을 요구한다. 기존 제외/참고 단계는 올리지 않는다."""
    checks = grading._constraints(profile, context)
    check = grading.MandatoryCheck(unknown=["extraction_semantics"])
    for name, verdict, _ in checks:
        getattr(check, verdict).append(name)
    exclusion = [reason for _, verdict, reason in checks if verdict == "failed"]
    if previous and previous["grade"] == "EXCLUDED":
        exclusion += previous["exclusion_reasons"] or [
            "과거 제외 판정을 유지하며 추출 의미를 확인해야 합니다."
        ]
    value: grading.Grade = "EXCLUDED" if exclusion else "REVIEW"
    if previous and previous["grade"] == "REFERENCE_ONLY" and not exclusion:
        value = "REFERENCE_ONLY"
    return grading.RequirementGrade(
        requirement_id=row["requirement_id"],
        grade=value,
        reasons=["과거 추출 의미(kind·Listed·정규값)가 미보존되어 사람의 확인이 필요합니다."],
        exclusion_reasons=list(dict.fromkeys(exclusion)),
        mandatory_check=check,
    )


async def recompare_stored(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    project_id: UUID,
    skip: Iterable[UUID] = (),
) -> int:
    """다시 채점: 저장된 요구 전부를 자사 사양과 다시 비교한다(모델 없음, 요구별 다시 비교와 같은 함수).

    사람이 판단을 정하지 않은 요구(AI·시스템 판본, 결정 기록 없음)는 판단이 바뀌면 새 판본(system_record)과 그 판본의
    보완 선택지를 쓴다. 사람이 정한 요구는 판단을 두고 비교 결과·자사 보증값만 갱신한다(바꾸려면 요구별 다시 비교의
    변경 제안으로). 협력사 재채점·초안은 이어지는 regrade_stored·drafts.generate가 새 판본으로 한다.

    Returns:
        int: 판단을 새 판본으로 바꾼 요구 수.
    """
    from pydantic import ValidationError

    from backend.api.tft.rfp import workflow

    skipped = set(skip)
    human = await repository.human_owned_requirements(conn, project_id)
    products = await catalog.own_products(conn)
    changed = 0
    for row in await repository.stored_grading_requirements(conn, project_id):
        if row["requirement_id"] in skipped:
            continue
        try:
            item = stored_extraction(row)
        except ValidationError:
            continue
        if item is None:
            continue
        outcome = await workflow.stored_outcome(conn, project_id, row, item, products)
        target = row
        judged = (row["status"], row["reason"], list(row["unresolved"]))
        if row["requirement_id"] not in human and judged != (
            outcome.status,
            outcome.reason,
            list(outcome.unresolved),
        ):
            version = await repository.insert_system_version(
                conn,
                row,
                status=outcome.status,
                reason=outcome.reason,
                unresolved=outcome.unresolved,
            )
            target = {**row, "version": version}
            options = supplement.options_for(
                outcome, "UNSTATED", designated=item.designated_model is not None
            )
            for option in options.options:
                await repository.insert_option(
                    conn,
                    row["requirement_id"],
                    option.model_dump(),
                    deficiency_cause=options.deficiency_cause,
                    customer_permission=options.customer_permission,
                    data_origin=row["data_origin"],
                    requirement_version=version,
                )
            changed += 1
        await workflow.store_outcome(conn, target, item, outcome)
    return changed


async def regrade_stored(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    project_id: UUID,
    reference_date: date,
    pool: frozenset[str] | None,
) -> bool:
    """호출자가 프로젝트 잠금을 보유한 업무 TX에서 재채점한다. 원문/provider/별도 commit 없음."""
    rows = await repository.stored_grading_requirements(conn, project_id)
    inputs = await repository.project_constraints(conn, project_id)
    titles = [r.get("document_title") for r in rows]
    if titles and all(title is not None for title in titles):
        pool = candidate_pool(title for title in titles if title is not None)
    context = grading.RfpContext(
        manufacturer_list=inputs["customer_manufacturer_list"],
        off_list_permission=inputs["off_list_permission"],
        supply_region=inputs["supply_region"],
        requires_integrator=integration.integration_responsibility(
            [
                integration.Requirement(
                    clause_no=r.get("clause_no") or "",
                    task=r["task"],
                    assembly_clause=r["assembly_clause"],
                    clause_text=r.get("clause_text"),
                )
                for r in rows
            ]
        ).required,
    )
    profiles = [
        PartnerProfile.model_validate({**row, "known_at": row["known_at"].date()})
        for row in await repository.partner_profiles(conn)
    ]
    previous = {
        (g["candidate_code"], g["requirement_id"]): g
        for g in await repository.prior_grades_for_regrading(conn, project_id)
    }
    targets, legacy = [], []
    by_id = {r["requirement_id"]: r for r in rows}
    for row in rows:
        if row["status"] not in GRADED_STATUSES or row["designated_model"] is not None:
            continue
        item = stored_extraction(row)
        if item is None:
            legacy.append(row)
        elif (requirement := to_grading(row["requirement_id"], item)) is not None:
            targets.append((requirement, row["equipment_family"]))
    for profile, requirements in grading_groups(targets, profiles, pool):
        _, grades = grading.grade_candidate(profile, requirements, context, reference_date)
        for grade in grades:
            row = by_id[grade.requirement_id]
            if is_ocr(row.get("clause_no") or ""):
                grade = grade.model_copy(
                    update={
                        "grade": "REVIEW" if grade.grade == "MANDATORY_CONFIRMED" else grade.grade,
                        "reasons": [*grade.reasons, "이미지 전사 원문 확인 필요"],
                        "mandatory_check": grade.mandatory_check.model_copy(
                            update={
                                "unknown": [*grade.mandatory_check.unknown, "ocr_source_review"]
                            }
                        ),
                    }
                )
            await repository.insert_grade(
                conn,
                project_id=project_id,
                candidate_code=profile.candidate_code,
                profile_version=profile.profile_version,
                requirement_version=row["version"],
                grade=grade.model_dump(mode="json"),
                data_origin=row["data_origin"],
            )
    for profile in profiles:
        if outside_pool(profile, pool):
            continue
        for row in legacy:
            if row["equipment_family"] not in PROFILE_FAMILIES.get(
                profile.product_family, frozenset()
            ):
                continue
            grade = legacy_grade(
                row, profile, context, previous.get((profile.candidate_code, row["requirement_id"]))
            )
            await repository.insert_grade(
                conn,
                project_id=project_id,
                candidate_code=profile.candidate_code,
                profile_version=profile.profile_version,
                requirement_version=row["version"],
                grade=grade.model_dump(mode="json"),
                data_origin=row["data_origin"],
            )
    return not profiles or bool(legacy)


def is_ocr(clause_no: str) -> bool:
    return clause_no.startswith(("OCR-DOCX-", "OCR-PDF-P"))


def image_comparison_review(
    item: ExtractedRequirement, result: comparison.Outcome
) -> comparison.Outcome:
    """OCR quotes match a transcription, not verified source glyphs; never auto-confirm."""
    if not is_ocr(item.clause_no):
        return result
    note = "이미지 전사 원문 확인 필요"
    return result.model_copy(
        update={
            "status": "CONDITIONAL" if result.status == "EVIDENCE_FOUND" else result.status,
            "reason": f"{note}. {result.reason}",
            "unresolved": [*result.unresolved, note],
            "next_owner": "TECH",
            "candidates": [
                candidate.model_copy(
                    update={
                        "status": "CONDITIONAL"
                        if candidate.status == "EVIDENCE_FOUND"
                        else candidate.status,
                        "unresolved": [*candidate.unresolved, note],
                        "comparable": False
                        if candidate.status == "EVIDENCE_FOUND"
                        else candidate.comparable,
                        "result": "NOT_COMPARABLE"
                        if candidate.status == "EVIDENCE_FOUND"
                        else candidate.result,
                    }
                )
                for candidate in result.candidates
            ],
        }
    )


def parse_with_images(
    data: bytes,
    mime: str,
    image_provider: Callable[[vision.ImageInput], Generation] | None,
    emit: Emit,
    document_version_id: str,
) -> tuple[ClauseTree, bool]:
    document = extract(data, mime=mime)
    tree = build_clause_tree(document)
    plan = vision.image_inputs(data, document)
    return apply_image_plan(tree, plan, image_provider, emit, document_version_id)


def apply_image_plan(
    tree: ClauseTree,
    plan: vision.VisionPlan,
    image_provider: Callable[[vision.ImageInput], Generation] | None,
    emit: Emit,
    document_version_id: str,
) -> tuple[ClauseTree, bool]:
    result = transcribe_image_plan(plan, image_provider, emit, document_version_id)
    return vision.append_ocr_clauses(tree, result), bool(result.items or result.skipped)


def transcribe_image_plan(
    plan: vision.VisionPlan,
    image_provider: Callable[[vision.ImageInput], Generation] | None,
    emit: Emit,
    document_version_id: str,
) -> vision.VisionResult:
    """Retain the observations for source preservation without changing the legacy parse tuple."""
    if not plan.inputs and not plan.skipped:
        return vision.VisionResult()
    if image_provider is None:
        result = vision.VisionResult(
            skipped=[
                *plan.skipped,
                *[
                    vision.VisionIssue(item.source_id, "OCR_PROVIDER_DISABLED")
                    for item in plan.inputs
                ],
            ]
        )
    else:

        def tracked(item: vision.ImageInput) -> Generation:
            # Check ownership before each image and after a possibly long paid call.
            emit(
                {
                    "step": "parse",
                    "document_version_id": document_version_id,
                    "focus": focus("ocr", item.location),
                }
            )
            try:
                return image_provider(item)
            finally:
                emit({"step": "parse", "document_version_id": document_version_id})

        result = vision.transcribe(plan, tracked)
    return result


def drawing_inputs(
    kind: str, tree: ClauseTree, observations: vision.VisionResult
) -> tuple[list[ExtractionInput], bool]:
    """Only a validated graph-only DRAWING may have zero textual requirement inputs.

    Existing malformed/native sections and all specification documents still use the
    normal extraction-input gate; topology is never turned into text requirements.
    """
    if (
        kind == "DRAWING"
        and not sections_of(tree)
        and any(
            row.diagram is not None
            and row.diagram.diagram_type == "SINGLE_LINE"
            and bool(row.diagram.nodes)
            for row in observations.items
        )
    ):
        return [], True
    return document_inputs(tree)


async def process(
    settings: Settings,
    job: TftJob,
    emit: Emit,
    provider: Provider,
    store: DocumentStore | None,
    products: Sequence[comparison.Product] | None,
    today: date,
    extraction_concurrency: int = 6,
    image_provider: Callable[[vision.ImageInput], Generation] | None = None,
    structure_provider: Provider | None = None,
) -> dict[str, Any]:
    """rfp.process 한 번(단계 7개).

    Args:
        settings: DB 설정.
        job: 작업.
        emit: 단계 보고(소유권·취소 검사).
        provider: 추출 모델 호출.
        store: 원문 저장소.
        products: 자사 제품. None이면 DB(catalog.own_products)에서 읽는다.
        today: 인증 유효기한 기준일.

    Returns:
        dict: 종료 요약(counts·needs_review).
    """
    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    needs_review = False
    async with await psycopg.AsyncConnection.connect(
        dsn, row_factory=dict_row, autocommit=True
    ) as conn:
        if products is None:
            products = await catalog.own_products(conn)
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
        planned: list[tuple[Mapping[str, Any], ClauseTree, vision.VisionPlan]] = []
        image_manifests: dict[UUID, dict[str, Any]] = {}
        for document in documents:
            processed = await repository.document_processed(conn, document["id"])
            previous = await diagram_repository.read_diagrams(conn, document["id"])
            if previous is not None:
                if previous["source_sha256"] != document["sha256"]:
                    raise ValueError("TFT_DIAGRAM_SOURCE_MISMATCH")
                needs_review |= bool(
                    previous["payload"]["items"] or previous["payload"]["warnings"]
                )
                processed = True
            if not processed:
                if store is None:
                    raise ValueError("TFT_DOCUMENT_ROOT_MISSING")
                data = await read_document(
                    store, document["storage_key"], document["sha256"], document["size"]
                )
                native = await asyncio.to_thread(extract, data, mime=document["mime"])
                tree = build_clause_tree(native)
                plan = await asyncio.to_thread(vision.image_inputs, data, native)
                planned.append((document, tree, plan))
            emit({"step": "parse", "document_version_id": str(document["id"])})

        # Native inputs across all documents are checked before even the first OCR call.
        # An image-only document may obtain its first usable section from transcription.
        for _, tree, plan in planned:
            if sections_of(tree) or not plan.inputs:
                _, skipped = document_inputs(tree)
                needs_review |= skipped

        prepared = []
        for document, tree, plan in planned:
            tree, repaired = await asyncio.to_thread(
                repair_structure, tree, structure_provider, emit, str(document["id"])
            )
            needs_review |= repaired
            observations = await asyncio.to_thread(
                transcribe_image_plan, plan, image_provider, emit, str(document["id"])
            )
            tree = vision.append_ocr_clauses(tree, observations)
            needs_review |= bool(observations.items or observations.skipped)
            clauses: dict[str, Any] = {}
            parsed.append((document, tree, clauses))
            inputs, skipped = drawing_inputs(document["kind"], tree, observations)
            needs_review |= skipped
            if store is None:  # Already checked before reading each planned document.
                raise ValueError("TFT_DOCUMENT_ROOT_MISSING")
            image_manifests[document["id"]] = await diagram_artifacts.preserve_images(
                store, document["id"], plan, observations
            )
            prepared.append((document, clauses, inputs))

        # extract: 절마다 모델을 부른다. 일부 긴 절·실패한 절은 검토가 필요하다고 표시한다.
        # 절마다 진행을 남겨 lease를 연장한다(절이 많으면 모델 호출 중 lease가 끝나 다른 worker가 다시 부르지 않게).
        extracted: list[tuple[Mapping[str, Any], dict[str, Any], ExtractedRequirement]] = []
        sections = [
            (document, clauses, item) for document, clauses, inputs in prepared for item in inputs
        ]
        results = await asyncio.to_thread(
            extract_sections,
            [item for _, _, item in sections],
            provider,
            emit,
            concurrency=extraction_concurrency,
        )
        for (document, clauses, _), result in zip(sections, results, strict=True):
            needs_review |= result.outcome != "VALID" or any(
                r.needs_review for r in result.requirements
            )
            extracted += [
                (
                    document,
                    clauses,
                    r.model_copy(update={"needs_review": True}) if is_ocr(r.clause_no) else r,
                )
                for r in result.requirements
            ]
        total = len(extracted)
        if not sections:
            emit({"step": "extract", "counts": counts(total)})

        # search: 문서 검색기 연결 전에는 요구마다 제품 마스터의 같은 계열 후보가 범위다(compare_in_family, trace에 남음).
        emit(
            {
                "step": "search",
                "counts": counts(total),
                "focus": focus("compare", "자사 제품", total),
            }
        )

        # 요구 장비 계열의 자사 후보 안에서만 비교한다(KAN-199). 계열을 모르면 비교하지 않는다.
        # 절별 규격 체계는 문서마다 따로 본다(다른 문서의 같은 절 번호와 섞지 않는다).
        systems_by_document = {
            id(document): section_systems(r for d, _, r in extracted if d is document)
            for document, _, _ in extracted
        }
        voltages_by_document = {
            id(document): item_voltages(r for d, _, r in extracted if d is document)
            for document, _, _ in extracted
        }
        insulations_by_document = {
            id(document): item_insulations(r for d, _, r in extracted if d is document)
            for document, _, _ in extracted
        }
        outcomes = [
            image_comparison_review(
                r,
                comparison.compare_in_family(
                    to_comparison(
                        r,
                        systems_by_document[id(document)],
                        voltages_by_document[id(document)],
                        insulations_by_document[id(document)],
                    ),
                    r.equipment_family,
                    products,
                ),
            )
            for document, _, r in extracted
        ]
        # 잠금 SELECT 자체도 snapshot을 만든다. autocommit 문장에서 기다린 뒤 RR을 시작해야
        # 선행 작업이 커밋한 등급을 첫 데이터 snapshot에 포함한다. 업무 TX 종료까지 같은 키를 보유한다.
        await conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s,187))", (str(job.project_id),)
        )
        try:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                for document, tree, clauses in parsed:
                    clauses.update(await repository.store_clauses(conn, document, tree.clauses))
                    await diagram_repository.store_diagrams(
                        conn, document["id"], document["sha256"], image_manifests[document["id"]]
                    )
                ids = await store_requirements(conn, job.project_id, extracted, outcomes)
                await store_question_drafts(conn, job, parsed, extracted, ids)
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
                # 이미 처리한 문서도 모델 없이 현행 저장 요구를 재채점한다.
                if await draft_repository.generated(conn, job.project_id, job.job_id) is None:
                    await recompare_stored(conn, job.project_id, ids)
                    needs_review |= await regrade_stored(
                        conn,
                        job.project_id,
                        today,
                        candidate_pool(d["title"] for d in documents),
                    )
                emit({"step": "candidates", "counts": counts(total)})
                proposal = await drafts.generate(conn, job.project_id, job.actor_id, job.job_id)
                needs_review |= any(
                    row.evidence_missing or row.needs_rereview
                    for part in proposal.sections
                    for row in part.sentences
                )
                emit({"step": "draft", "counts": counts(total)})
        finally:
            # commit/rollback 뒤 해제한다. 취소나 unlock 실패 시에도 바깥 연결 종료가 잠금을 해제한다.
            await conn.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s,187))", (str(job.project_id),)
            )
    return {"counts": counts(total), "needs_review": needs_review}


def draft_key(draft: QuestionDraft) -> str:
    """판본과 무관하게 같은 질문을 가리키는 키. 조항 ID는 판본마다 달라 쓰지 않는다.

    Args:
        draft: 질문 초안.

    Returns:
        str: 종류와 정렬한 (조항 번호, 원문) 목록의 sha256.
    """
    sources = sorted((s.clause_no, s.quote) for s in draft.sources)
    return hashlib.sha256(json.dumps([draft.kind, sources]).encode()).hexdigest()


def draft_requirement(
    draft: QuestionDraft, stored: Sequence[tuple[UUID, ExtractedRequirement]]
) -> UUID | None:
    """초안이 가리키는 요구. 원문으로 하나로 정해질 때만 연결한다.

    같은 조항 번호에서 요구 인용과 출처 원문이 같거나 서로 포함되는 요구만 본다. 조항의 첫 요구로 넘겨짚으면 다른
    요구의 고객 질문 여부(has_customer_question)가 참이 되어 승인 질문 게이트가 잘못 풀린다.

    Args:
        draft: 질문 초안.
        stored: 같은 문서의 (요구 ID, 추출 요구).

    Returns:
        UUID | None: 하나로 정해진 요구. 없거나 둘 이상으로 갈리면 None(조항만 있는 질문)이다.
    """
    matched = {
        requirement_id
        for source in draft.sources
        for requirement_id, item in stored
        if item.clause_no == source.clause_no
        and (item.quote in source.quote or source.quote in item.quote)
    }
    return matched.pop() if len(matched) == 1 else None


async def store_question_drafts(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    job: TftJob,
    parsed: Sequence[tuple[Mapping[str, Any], ClauseTree, dict[str, Any]]],
    extracted: Sequence[tuple[Mapping[str, Any], dict[str, Any], ExtractedRequirement]],
    ids: Sequence[UUID],
) -> None:
    """문서마다 고객 질문 초안을 DRAFT로 저장한다. 처리된 판본은 parsed에 없어 다시 만들지 않는다.

    Args:
        conn: 진행 중 트랜잭션(조항·요구와 함께 커밋한다).
        job: 작업(요청자).
        parsed: 이번에 처리한 문서.
        extracted: 추출 요구.
        ids: extracted와 같은 순서의 요구 ID.
    """
    for document, tree, clauses in parsed:
        stored = [
            (requirement_id, item)
            for requirement_id, (origin, _, item) in zip(ids, extracted, strict=True)
            if origin is document
        ]
        for draft in generate_question_drafts(tree, [item for _, item in stored]):
            numbers = [s.clause_no for s in draft.sources]
            await repository.insert_question_draft(
                conn,
                project_id=job.project_id,
                requirement_id=draft_requirement(draft, stored),
                clause=clauses.get(numbers[0]),
                issue_type=QUESTION_ISSUES[draft.kind],
                question_text=draft.question,
                internal_assumption={
                    "value": draft.pre_reply_note,
                    "basis_standard": None,
                    "basis_clause": ", ".join(dict.fromkeys(numbers)),
                    "approver_user_id": None,
                    "shown_in_draft": False,
                },
                draft_key=draft_key(draft),
                data_origin=document["data_origin"],
                actor=job.actor_id,
            )


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
                "tolerance",
                "needs_review",
                "attribute_key",
                "equipment_family",
                "conditions",
                "kind",
                "certification_listed",
                "canonical_value",
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
