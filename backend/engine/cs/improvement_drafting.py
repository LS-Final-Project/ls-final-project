"""개선요청서 작성 칸·부서 질문 초안의 입력 정제와 원시 출력 검사(KAN-115). 모델 호출은 하지 않는다.

근거 문의·해결 사례·조건 비교 요약(KAN-114 결과)을 받아 요청서 Content 칸과 부서별 질문 초안을 만든다.
초안은 사람이 확정·전달하며 이 모듈은 저장·상태 전이·후보 자동 생성(KAN-113)을 하지 않는다. 조건 판정은
다시 하지 않고 주어진 값을 그대로 넘긴다.

검사하는 것: 근거 참조·부서가 입력 안에 있는지, 고장 요청의 제안 부서·not_claimed 필수, 확정 약속 문구.
원인 단정 금지는 프롬프트 지시로만 두고 코드로 검사하지 않는다(문장 의미 판정이 필요해서다).
시점 제한: 작성 시각(as_of) 이후에 접수·인지된 문의·사례는 입력에서 거부한다(미래 정보 누설 금지).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from backend.engine.cs.classification import _unique_object, build_input
from backend.engine.cs.drafting import _Strict, find_forbidden_commitment
from backend.engine.cs.improvement_reply import ConditionStatus, DocSignal, RequestType
from backend.engine.cs.prompt_registry import get_prompt
from backend.shared.departments import TRANSFER_DEPARTMENTS

IMPROVEMENT_DRAFT_INPUT_VERSION = "cs-improvement-draft-input-v1"
RootCauseType = Literal["SITE_CONDITION", "HARDWARE", "SETTING", "WEAR_PART", "FIRMWARE"]
NonBlank = Annotated[str, Field(pattern=r"\S")]
# 요청서 Content의 Text 최대 길이와 제안 부서 최대 수(backend/api/cs/improvements/schemas.py와 같은 값,
# tests/cs/test_improvement_drafting.py가 일치를 고정한다). 엔진은 API 계층을 import하지 않는다.
TEXT_MAX_LENGTH = 10_000
MAX_PROPOSED_DEPTS = 12


class EvidenceTicket(_Strict):
    """근거 문의 하나. summary는 어댑터가 만든 마스킹 전 요약이다."""

    # 참조는 evidence_refs 대조 키라 가리지 않는다. 외부 ID나 UUID 같은 식별자만 넣는다.
    ticket_ref: NonBlank
    received_at: AwareDatetime
    product_code: NonBlank
    fw_version_reported: str | None
    error_code_reported: str | None
    summary: NonBlank


class EvidenceCase(_Strict):
    """근거 해결 사례 하나(고장 요청만)."""

    case_ref: NonBlank
    known_at: AwareDatetime
    fault_id: str = Field(pattern=r"^FLT-\d{3}$")
    fw_version_at_issue: str | None
    root_cause: NonBlank
    root_cause_type: RootCauseType
    action_taken: NonBlank
    fw_fixed_version: str | None
    resolved_by_dept: NonBlank


class ConditionSummary(_Strict):
    """KAN-114 조건 비교 결과 요약. 엔진은 판정을 다시 하지 않는다."""

    condition_status: ConditionStatus
    summary: str | None


class ImprovementDraftContext(_Strict):
    """초안을 만들 개선요청 맥락. candidate_depts는 호출자가 부서 목록 안에서 고른다."""

    request_type: RequestType
    product_code: NonBlank
    fault_id: str | None = Field(pattern=r"^FLT-\d{3}$")
    doc_signal: DocSignal | None
    tickets: list[EvidenceTicket] = Field(min_length=1)
    cases: list[EvidenceCase]
    condition: ConditionSummary
    candidate_depts: list[str]
    as_of: AwareDatetime


class DraftTicket(_Strict):
    ticket_ref: str
    received_at: str
    product_code: str
    fw_version_reported: str | None
    error_code_reported: str | None
    summary: str


class DraftCase(_Strict):
    case_ref: str
    known_at: str
    fault_id: str
    fw_version_at_issue: str | None
    root_cause: str
    root_cause_type: RootCauseType
    action_taken: str
    fw_fixed_version: str | None
    resolved_by_dept: str


class ImprovementDraftInput(_Strict):
    """모델에 전달할 초안 입력. 자유 서술은 분류와 같은 규칙으로 가렸고 시각은 ISO 문자열이다."""

    request_type: RequestType
    product_code: str
    fault_id: str | None
    doc_signal: DocSignal | None
    condition_status: ConditionStatus
    condition_summary: str | None
    candidate_depts: list[str]
    as_of: str
    tickets: list[DraftTicket]
    cases: list[DraftCase]


@dataclass(frozen=True)
class PreparedImprovementDraftInput:
    """모델용 초안 입력과 처리 판본·주석 제거 건수(EMAIL로 정제하므로 항상 0)."""

    payload: ImprovementDraftInput
    version: str
    removed_annotations: int


def _refs(context: ImprovementDraftContext) -> list[str]:
    """입력 근거 참조 목록.

    Args:
        context: 초안 맥락.

    Returns:
        list[str]: 문의·사례 참조(입력 순서).
    """
    return [t.ticket_ref for t in context.tickets] + [c.case_ref for c in context.cases]


def _invalid_context(context: ImprovementDraftContext) -> bool:
    """유형별 필수 값·제품·고장·시점·참조·부서 규칙을 어기는지 본다.

    Args:
        context: 타입 검사를 통과한 맥락.

    Returns:
        bool: 어기면 True.
    """
    refs = _refs(context)
    depts = context.candidate_depts
    if context.request_type == "FAULT":
        kind_ok = (
            context.fault_id is not None
            and context.doc_signal is None
            and all(case.fault_id == context.fault_id for case in context.cases)
        )
    else:
        kind_ok = context.doc_signal is not None and context.fault_id is None and not context.cases
    return (
        not kind_ok
        or any(t.product_code != context.product_code for t in context.tickets)
        or any(t.received_at > context.as_of for t in context.tickets)
        or any(c.known_at > context.as_of for c in context.cases)
        or len(set(refs)) != len(refs)
        or len(set(depts)) != len(depts)
        or not set(depts) <= TRANSFER_DEPARTMENTS
    )


def build_improvement_draft_input(
    context: ImprovementDraftContext,
    *,
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
) -> PreparedImprovementDraftInput:
    """맥락을 검사하고 자유 서술을 분류와 같은 규칙으로 가린다.

    Args:
        context: 초안 맥락.
        sensitive_values: 어댑터가 식별한 원값과 마스킹 종류.

    Returns:
        PreparedImprovementDraftInput: 정제 입력. 참조·제품·고장 코드 외의 문자열 칸(요약·판본·오류 코드·
            원인·조치·처리 부서·조건 요약)을 build_input(channel=EMAIL, subject=None)으로 가리므로 removed_annotations는 항상 0이다. 규칙 위반은
            CS_INVALID_INPUT ValueError다.
    """
    if not isinstance(context, ImprovementDraftContext) or _invalid_context(context):
        raise ValueError("CS_INVALID_INPUT")

    def mask(text: str) -> str:
        """분류 입력 규칙으로 민감값·이메일·시리얼을 가린다.

        Args:
            text: 비지 않은 자유 서술.

        Returns:
            str: 가린 텍스트.
        """
        record = {"channel": "EMAIL", "body_language": "und", "subject": None, "body": text}
        return build_input(record, sensitive_values=sensitive_values).payload.body

    def optional(text: str | None) -> str | None:
        """비어 있지 않은 선택 칸만 가린다.

        Args:
            text: 선택 자유 서술.

        Returns:
            str | None: 가린 텍스트, 비었으면 원래 값.
        """
        return mask(text) if text and text.strip() else text

    payload = ImprovementDraftInput(
        request_type=context.request_type,
        product_code=context.product_code,
        fault_id=context.fault_id,
        doc_signal=context.doc_signal,
        condition_status=context.condition.condition_status,
        condition_summary=optional(context.condition.summary),
        candidate_depts=list(context.candidate_depts),
        as_of=context.as_of.isoformat(),
        tickets=[
            DraftTicket(
                ticket_ref=t.ticket_ref,
                product_code=t.product_code,
                received_at=t.received_at.isoformat(),
                fw_version_reported=optional(t.fw_version_reported),
                error_code_reported=optional(t.error_code_reported),
                summary=mask(t.summary),
            )
            for t in context.tickets
        ],
        cases=[
            DraftCase(
                case_ref=c.case_ref,
                fault_id=c.fault_id,
                root_cause_type=c.root_cause_type,
                known_at=c.known_at.isoformat(),
                fw_version_at_issue=optional(c.fw_version_at_issue),
                root_cause=mask(c.root_cause),
                action_taken=mask(c.action_taken),
                fw_fixed_version=optional(c.fw_fixed_version),
                resolved_by_dept=mask(c.resolved_by_dept),
            )
            for c in context.cases
        ],
    )
    return PreparedImprovementDraftInput(payload, IMPROVEMENT_DRAFT_INPUT_VERSION, 0)


class Fact(_Strict):
    """근거가 있는 문장. 근거 참조는 1개 이상이고 중복되지 않는다."""

    text: NonBlank
    evidence_refs: list[NonBlank] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_refs(self) -> Self:
        """근거 참조 중복을 거부한다.

        Args:
            self: 타입 검사를 통과한 문장.

        Returns:
            Self: 유효한 문장.
        """
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("CS_DUPLICATE_EVIDENCE")
        return self


class DeptQuestion(_Strict):
    dept_code: str
    question: NonBlank


class ImprovementDraft(_Strict):
    """요청서 작성 칸과 부서별 질문 초안 원시 출력. 부서마다 질문은 하나다."""

    problem_statement: str = Field(pattern=r"^[^\r\n]*\S[^\r\n]*$")
    confirmed_facts: list[Fact]
    inconsistencies: list[Fact]
    missing_info: list[NonBlank]
    not_claimed: list[NonBlank]
    review_task: NonBlank
    proposed_depts: list[str]
    dept_questions: list[DeptQuestion]

    @model_validator(mode="after")
    def validate_departments(self) -> Self:
        """제안 부서·질문 부서의 중복과 질문 부서가 제안 부서 안인지 검사한다.

        Args:
            self: 타입 검사를 통과한 초안.

        Returns:
            Self: 유효한 초안. 입력 후보 부서 안인지는 validate_output이 본다.
        """
        asked = [q.dept_code for q in self.dept_questions]
        if (
            len(set(self.proposed_depts)) != len(self.proposed_depts)
            or len(set(asked)) != len(asked)
            or not set(asked) <= set(self.proposed_depts)
        ):
            raise ValueError("CS_INVALID_DEPARTMENT")
        return self


_SCHEMA = json.dumps(ImprovementDraft.model_json_schema(), ensure_ascii=False, sort_keys=True)
IMPROVEMENT_DRAFT_PROMPT = get_prompt("improvement_draft", "v1") + _SCHEMA


def instruction(prompt_version: str = "v1") -> str:
    """판본 지시문과 출력 스키마를 붙인 고정 지시문을 만든다.

    Args:
        prompt_version: 레지스트리 판본 키.

    Returns:
        str: 공급자에 보낼 지시문(입력 제외). 모르는 판본은 CS_UNKNOWN_PROMPT_VERSION ValueError다.
    """
    return get_prompt("improvement_draft", prompt_version) + _SCHEMA


def render_prompt(payload: ImprovementDraftInput, *, prompt_version: str = "v1") -> str:
    """고정 지시문 뒤에 정제 입력 JSON을 붙인다.

    Args:
        payload: build_improvement_draft_input의 정제 입력.
        prompt_version: 레지스트리 판본 키. 기본 v1은 IMPROVEMENT_DRAFT_PROMPT와 같다.

    Returns:
        str: 공급자에 그대로 보낼 프롬프트.
    """
    return instruction(prompt_version) + "\n\n입력:\n" + payload.model_dump_json()


def validate_output(raw: str, *, context: ImprovementDraftContext) -> ImprovementDraft:
    """원시 JSON을 폴백 없이 검사하고 확정 약속을 거부한다.

    Args:
        raw: 모델 원시 응답.
        context: 입력에 쓴 맥락.

    Returns:
        ImprovementDraft: 유효한 초안. 형식·참조·부서·유형별 필수 위반과 요청서 Content 상한(이어 붙인
            텍스트 칸 TEXT_MAX_LENGTH자, 제안 부서 MAX_PROPOSED_DEPTS개) 초과는 CS_INVALID_OUTPUT, 형식은 맞지만
            어느 텍스트에든 승인·확정 약속이 있으면 CS_FORBIDDEN_COMMITMENT ValueError다.
            원인 단정은 검사하지 않는다.
    """
    refs = set(_refs(context))
    try:
        draft = ImprovementDraft.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
        fault = context.request_type == "FAULT"
        if (
            any(
                not set(f.evidence_refs) <= refs
                for f in (*draft.confirmed_facts, *draft.inconsistencies)
            )
            or not set(draft.proposed_depts) <= set(context.candidate_depts)
            or (fault and (not draft.proposed_depts or not draft.not_claimed))
            or len(draft.proposed_depts) > MAX_PROPOSED_DEPTS
            or any(
                isinstance(value, str) and len(value) > TEXT_MAX_LENGTH
                for value in to_content_fields(draft)["content"].values()
            )
        ):
            raise ValueError("CS_CONTEXT_MISMATCH")
    except (ValueError, RecursionError):
        raise ValueError("CS_INVALID_OUTPUT") from None
    texts = [
        draft.problem_statement,
        draft.review_task,
        *(f.text for f in (*draft.confirmed_facts, *draft.inconsistencies)),
        *draft.missing_info,
        *draft.not_claimed,
        *(q.question for q in draft.dept_questions),
    ]
    if any(find_forbidden_commitment(text) for text in texts):
        raise ValueError("CS_FORBIDDEN_COMMITMENT")
    return draft


def to_content_fields(draft: ImprovementDraft) -> dict:
    """초안을 요청서 Content 칸 이름으로 평탄화한다. 저장하지 않는다.

    Args:
        draft: validate_output을 통과한 초안.

    Returns:
        dict: {"content": Content 칸(목록은 줄바꿈으로 이은 문자열, 빈 목록은 None),
            "evidence_refs": 사실·불일치가 쓴 근거 참조(첫 등장 순, 중복 없음)}. 조건 판정 칸은 맥락 값이므로
            넣지 않으며 호출자가 채우고, evidence_refs는 호출자가 request_evidence에 연결한다.
    """

    def lines(items: list[str]) -> str | None:
        """목록을 줄바꿈 문자열로 잇는다.

        Args:
            items: 문장 목록.

        Returns:
            str | None: 빈 목록이면 None.
        """
        return "\n".join(items) or None

    facts = (*draft.confirmed_facts, *draft.inconsistencies)
    return {
        "content": {
            "problem_statement": draft.problem_statement,
            "confirmed_facts": lines([f.text for f in draft.confirmed_facts]),
            "inconsistencies": lines([f.text for f in draft.inconsistencies]),
            "missing_info": lines(draft.missing_info),
            "not_claimed": lines(draft.not_claimed),
            "review_task": draft.review_task,
            "proposed_depts": list(draft.proposed_depts),
        },
        "evidence_refs": list(dict.fromkeys(ref for f in facts for ref in f.evidence_refs)),
    }
