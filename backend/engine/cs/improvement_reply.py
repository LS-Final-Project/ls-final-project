"""개선요청 부서 회신의 입력 정제·원시 출력 검사·후속 행동 계산(KAN-118). 모델 호출은 하지 않는다.

부서 회신은 주장일 뿐이다. 부서가 완료·해결을 말해도 completion_claimed라는 사실로만 남기고 요청 상태를
바꾸지 않는다(contracts/platform/permissions.md: 부서 회신·조치 등록만으로 COMPLETED가 되지 않으며 완료
판단은 cs_agent). reply_type은 부서가 API로 고른 값이며 이 모듈이 바꾸거나 추론하지 않는다. 회신 본문은
그 자체로 근거가 아니므로 조치·적용 범위·펌웨어 판본 주장은 항상 확인 필요로 둔다. 추가 근거는 연결 후보만
내고 자동으로 연결하지 않는다. 상태 전이·판본·회차 변경은 하지 않는다.

어휘(RequestType·DocSignal·ConditionStatus·ReplyType·NextTask)는 backend/api/cs/improvements/schemas.py와
같은 값이며 tests/cs/test_improvement_reply.py가 일치를 고정한다. 엔진은 API 계층을 import하지 않는다.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from backend.engine.cs.classification import _unique_object, build_input
from backend.engine.cs.drafting import _Strict, find_forbidden_commitment
from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS, get_prompt

IMPROVEMENT_REPLY_INPUT_VERSION = "cs-improvement-reply-input-v1"
RequestType = Literal["FAULT", "DOCUMENT"]
DocSignal = Literal["NOT_FOUND", "VALUE_CONFLICT", "BROKEN_LINK", "REPEATED_SPEC"]
ConditionStatus = Literal["CONFIRMED", "UNKNOWN"]
ReplyType = Literal["ACKNOWLEDGED", "ADOPTED", "INFO_REQUESTED", "REJECTED"]
NextTask = Literal[
    "REVIEW",
    "CONFIRM_RECEIVER",
    "SEND",
    "ACKNOWLEDGE",
    "REPLY",
    "RECORD_ACTION",
    "SUPPLEMENT",
    "REVIEW_REJECTION",
    "PLAN_FOLLOWUP",
    "RECORD_FOLLOWUP",
]
Actor = Literal["CS_OWNER", "DEPARTMENT"]
ClaimKind = Literal["CAUSE", "FIX", "SCOPE", "FW_VERSION", "NO_ISSUE", "OTHER"]
# 회신 본문만으로 확정되지 않아 항상 확인이 필요한 주장. 반려 회신에는 나올 수 없다.
NEEDS_EVIDENCE = frozenset({"FIX", "SCOPE", "FW_VERSION"})
NonBlank = Annotated[str, Field(pattern=r"\S")]
# 요청서의 자유 서술 필드. 회신 본문과 같은 마스킹을 거쳐 모델에 간다.
FREE_TEXT = ("confirmed_facts", "review_task", "missing_info", "not_claimed")


class ImprovementContext(_Strict):
    """회신을 해석할 개선요청 맥락. has_action_record는 plan_followup만 쓰고 모델 입력에 넣지 않는다."""

    request_type: RequestType
    product_code: str
    fault_id: str | None = Field(pattern=r"^FLT-\d{3}$")
    doc_signal: DocSignal | None
    confirmed_facts: str | None
    review_task: str | None
    missing_info: str | None
    not_claimed: str | None
    condition_status: ConditionStatus
    fw_fixed_version: str | None
    dept_code: str
    reply_type: ReplyType
    request_version: int = Field(ge=1)
    has_action_record: bool


class DeptReplyText(_Strict):
    body: str
    body_language: str


class ImprovementReplyInput(_Strict):
    """모델에 전달할 부서 회신 해석 입력. 조치 기록 유무·정답·사후 기록은 들어가지 않는다."""

    request_type: RequestType
    product_code: str
    fault_id: str | None
    doc_signal: DocSignal | None
    confirmed_facts: str | None
    review_task: str | None
    missing_info: str | None
    not_claimed: str | None
    condition_status: ConditionStatus
    fw_fixed_version: str | None
    dept_code: str
    reply_type: ReplyType
    request_version: int
    body_language: str
    body: str


@dataclass(frozen=True)
class PreparedImprovementReplyInput:
    """모델용 부서 회신 입력과 처리 판본·주석 제거 건수(EMAIL로 정제하므로 항상 0)."""

    payload: ImprovementReplyInput
    version: str
    removed_annotations: int


def build_improvement_reply_input(
    context: ImprovementContext,
    reply: DeptReplyText,
    *,
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
) -> PreparedImprovementReplyInput:
    """부서 회신을 분류와 같은 규칙으로 정제하고 요청서 맥락을 붙인다.

    Args:
        context: 개선요청 맥락.
        reply: 부서 회신 본문.
        sensitive_values: 어댑터가 식별한 원값과 마스킹 종류.

    Returns:
        PreparedImprovementReplyInput: 정제 입력. 회신 본문과 요청서 자유 서술(FREE_TEXT)을 같은 규칙으로
            가린다. 회신 본문에는 PHONE 주석이 없어 build_input에
            channel=EMAIL로 넘기므로 removed_annotations는 항상 0이다. 잘못된 입력은 CS_INVALID_INPUT
            ValueError다.
    """
    if not isinstance(context, ImprovementContext) or not isinstance(reply, DeptReplyText):
        raise ValueError("CS_INVALID_INPUT")
    prepared = build_input(
        {
            "channel": "EMAIL",
            "body_language": reply.body_language,
            "subject": None,
            "body": reply.body,
        },
        sensitive_values=sensitive_values,
    )
    fields = context.model_dump(exclude={"has_action_record"})
    # 요청서 자유 서술도 모델에 가므로 subject 자리에 넣어 본문과 같은 규칙으로 가린다.
    for name in FREE_TEXT:
        fields[name] = build_input(
            {
                "channel": "EMAIL",
                "body_language": reply.body_language,
                "subject": fields[name],
                "body": reply.body,
            },
            sensitive_values=sensitive_values,
        ).payload.subject
    payload = ImprovementReplyInput(
        **fields,
        body_language=prepared.payload.body_language,
        body=prepared.payload.body,
    )
    return PreparedImprovementReplyInput(
        payload, IMPROVEMENT_REPLY_INPUT_VERSION, prepared.removed_annotations
    )


class Claim(_Strict):
    """부서가 주장한 내용 하나. FW_VERSION이면 fw_version에 그 판본을 적는다."""

    kind: ClaimKind
    text: NonBlank
    certainty: Literal["STATED", "UNCONFIRMED"]
    needs_confirmation: bool
    confirmation_reason: NonBlank | None
    fw_version: NonBlank | None

    @model_validator(mode="after")
    def validate_confirmation(self) -> Self:
        """확인 필요 강제·확인 이유·판본 값의 관계를 검사한다.

        Args:
            self: 타입 검사를 통과한 주장.

        Returns:
            Self: 유효한 주장.
        """
        if self.kind in NEEDS_EVIDENCE and not self.needs_confirmation:
            raise ValueError("CS_CONFIRMATION_REQUIRED")
        if self.needs_confirmation != (self.confirmation_reason is not None):
            raise ValueError("CS_CONFIRMATION_REASON")
        if (self.kind == "FW_VERSION") != (self.fw_version is not None):
            raise ValueError("CS_FW_VERSION")
        return self


class Question(_Strict):
    topic: NonBlank
    question: NonBlank


class EvidenceRequest(_Strict):
    """추가 근거 연결 후보. 연결은 사람이 한다."""

    kind: Literal["TICKET", "RESOLUTION_CASE", "ACTION_RECORD", "DOCUMENT"]
    description: NonBlank


class ImprovementReplyInterpretation(_Strict):
    """부서 회신 해석 원시 출력. completion_claimed는 부서의 말이며 요청 상태가 아니다."""

    claims: list[Claim]
    completion_claimed: bool
    answers_review_task: Literal["ADDRESSED", "PARTIAL", "NOT_ADDRESSED", "NO_TASK"]
    remaining_questions: list[Question] = Field(max_length=10)
    evidence_requests: list[EvidenceRequest]
    conflicts_with_request: list[NonBlank]


def _same_version(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _version_key(version: str) -> str:
    """판본 비교 키. 앞뒤 공백·끝 마침표를 떼고 casefold하며 숫자 앞 'v'는 뗀다('v2.2.0' = '2.2.0').

    Args:
        version: 판본 문자열.

    Returns:
        str: 비교 키.
    """
    return re.sub(r"^v(?=\d)", "", version.strip().casefold().rstrip("."))


def _mentions_version(text: str, version: str) -> bool:
    """conflicts 문장에 판본이 경계 있게 적혔는지 본다.

    Args:
        text: conflicts_with_request 항목.
        version: 주장한 판본.

    Returns:
        bool: casefold한 문장에서 선택적 'v' 접두와 판본이 앞에 영숫자·점이 없고 뒤에 영숫자나 '점+영숫자'가
            없게 나오면 True다. 한국어 조사('2.1.0에서', 'v2.1이')와 문장 끝 마침표는 맞고, 'v1' 대 'v10'·'1.0'은
            다음 글자가 숫자·점+숫자라 맞지 않는다.
    """
    key = _version_key(version)
    pattern = rf"(?<![a-z0-9.])v?{re.escape(key)}(?![a-z0-9]|\.[a-z0-9])"
    return bool(key) and re.search(pattern, text.casefold()) is not None


def _violates(context: ImprovementContext, output: ImprovementReplyInterpretation) -> bool:
    """요청서 맥락과 어긋나는 해석인지 본다.

    Args:
        context: 개선요청 맥락.
        output: 형식 검사를 통과한 해석.

    Returns:
        bool: 반려의 조치·범위·판본 주장, 질문 없는 추가 정보 요청, 검토 과제 유무와 맞지 않는 답,
            요청서와 다른 판본 주장을 conflicts에 적지 않은 경우 True.
    """
    kinds = {claim.kind for claim in output.claims}
    # 판본은 경계 있게 찾는다('v1'이 'v10'에 맞지 않고 한국어 조사가 붙어도 맞게).
    conflicts = output.conflicts_with_request
    fixed = context.fw_fixed_version
    return (
        (context.reply_type == "REJECTED" and bool(kinds & NEEDS_EVIDENCE))
        or (context.reply_type == "INFO_REQUESTED" and not output.remaining_questions)
        or ((context.review_task is None) != (output.answers_review_task == "NO_TASK"))
        or any(
            fixed is not None
            and claim.fw_version is not None
            and not _same_version(claim.fw_version, fixed)
            and not any(_mentions_version(c, claim.fw_version) for c in conflicts)
            for claim in output.claims
        )
    )


_SCHEMA = json.dumps(
    ImprovementReplyInterpretation.model_json_schema(), ensure_ascii=False, sort_keys=True
)
IMPROVEMENT_REPLY_PROMPT = (
    get_prompt("improvement_reply", DEFAULT_VERSIONS["improvement_reply"]) + _SCHEMA
)


def instruction(prompt_version: str = DEFAULT_VERSIONS["improvement_reply"]) -> str:
    """판본 지시문과 출력 스키마를 붙인 고정 지시문을 만든다.

    Args:
        prompt_version: 레지스트리 판본 키.

    Returns:
        str: 공급자에 보낼 지시문(입력 제외). 모르는 판본은 CS_UNKNOWN_PROMPT_VERSION ValueError다.
    """
    return get_prompt("improvement_reply", prompt_version) + _SCHEMA


def render_prompt(
    payload: ImprovementReplyInput, *, prompt_version: str = DEFAULT_VERSIONS["improvement_reply"]
) -> str:
    """고정 지시문 뒤에 정제 입력 JSON을 붙인다.

    Args:
        payload: build_improvement_reply_input의 정제 입력.
        prompt_version: 레지스트리 판본 키. 기본(DEFAULT_VERSIONS)은 IMPROVEMENT_REPLY_PROMPT와 같다.

    Returns:
        str: 공급자에 그대로 보낼 프롬프트.
    """
    return instruction(prompt_version) + "\n\n입력:\n" + payload.model_dump_json()


def validate_output(raw: str, *, context: ImprovementContext) -> ImprovementReplyInterpretation:
    """원시 JSON을 폴백 없이 검사하고 모델이 쓴 안내 문구의 금지 약속을 거부한다.

    Args:
        raw: 모델 원시 응답.
        context: 입력에 쓴 개선요청 맥락.

    Returns:
        ImprovementReplyInterpretation: 유효한 해석. 형식·확인 필요·맥락 위반은 CS_INVALID_OUTPUT,
            형식은 맞지만 질문·주제·근거 후보 설명에 승인·확정 약속이 있으면 CS_FORBIDDEN_COMMITMENT ValueError다.
    """
    try:
        output = ImprovementReplyInterpretation.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
        if _violates(context, output):
            raise ValueError("CS_CONTEXT_MISMATCH")
    except (ValueError, RecursionError):
        raise ValueError("CS_INVALID_OUTPUT") from None
    # 모델이 쓴 안내 문구만 검사한다. claims·conflicts는 부서 회신을 인용한 기록이라 제외한다.
    texts = [
        *(text for q in output.remaining_questions for text in (q.topic, q.question)),
        *(request.description for request in output.evidence_requests),
    ]
    if any(find_forbidden_commitment(text) for text in texts):
        raise ValueError("CS_FORBIDDEN_COMMITMENT")
    return output


class FollowupPlan(_Strict):
    """한 부서 회신에서 나온 다음 할 일 후보. 요청 상태·판본·회차·완료 여부 필드는 두지 않는다."""

    next_task: NextTask
    actor: Actor
    dept_code: str
    completion_claimed: bool
    confirmations: list[Claim]
    remaining_questions: list[Question]
    evidence_requests: list[EvidenceRequest]
    conflicts_with_request: list[str]


_TASKS: dict[str, tuple[NextTask, Actor]] = {
    "REJECTED": ("REVIEW_REJECTION", "CS_OWNER"),
    "INFO_REQUESTED": ("SUPPLEMENT", "CS_OWNER"),
    "ACKNOWLEDGED": ("REPLY", "DEPARTMENT"),
}


def plan_followup(
    context: ImprovementContext, interpretation: ImprovementReplyInterpretation
) -> FollowupPlan:
    """부서가 고른 reply_type과 조치 기록 유무로 다음 할 일 후보를 고른다(순수 함수).

    Args:
        context: validate_output에 쓴 개선요청 맥락.
        interpretation: validate_output을 통과한 해석.

    Returns:
        FollowupPlan: REJECTED→REVIEW_REJECTION, INFO_REQUESTED→SUPPLEMENT, ADOPTED+조치 없음→
            RECORD_ACTION(부서), ADOPTED+조치 있음→PLAN_FOLLOWUP, ACKNOWLEDGED→REPLY(부서). PLAN_FOLLOWUP은
            이 회신만 본 후보이며, 실제로는 모든 부서가 정리된 뒤 next_owners가 연다. 완료 판단은 cs_agent
            몫이라 완료 여부를 내지 않는다. 맥락과 어긋난 해석은 CS_INVALID_INPUT ValueError다.
    """
    if _violates(context, interpretation):
        raise ValueError("CS_INVALID_INPUT")
    task, actor = _TASKS.get(context.reply_type) or (
        ("PLAN_FOLLOWUP", "CS_OWNER")
        if context.has_action_record
        else ("RECORD_ACTION", "DEPARTMENT")
    )
    return FollowupPlan(
        next_task=task,
        actor=actor,
        dept_code=context.dept_code,
        completion_claimed=interpretation.completion_claimed,
        confirmations=[claim for claim in interpretation.claims if claim.needs_confirmation],
        remaining_questions=list(interpretation.remaining_questions),
        evidence_requests=list(interpretation.evidence_requests),
        conflicts_with_request=list(interpretation.conflicts_with_request),
    )
