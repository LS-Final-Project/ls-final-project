"""C/S 답변 초안·추가 정보 요청의 입력 선택과 원시 출력 검사(KAN-106). 모델 호출은 하지 않는다.

입력은 분류와 같은 build_input으로 화이트리스트·마스킹하고, 요청할 정보 코드는 모델이 아니라 여기서
필요 코드 − 제공 코드 − 이미 물은 코드로 계산한다. 출력은 문안 선택지 3개이며 문장마다 근거·조건·미확인을
구분한다. RMA·반품 승인, 방문 확정, 보증·무상 처리 확정 같은 약속은 확정형 문구 패턴으로 거부한다.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.engine.cs.classification import ClassificationOutput, _unique_object, build_input

DRAFT_INPUT_VERSION = "cs-draft-input-v1"
OptionLabel = Literal["CONCISE", "DETAILED", "INFO_FIRST"]
SentenceKind = Literal["EVIDENCE", "CONDITION", "UNCONFIRMED"]

# 승인·확정 약속의 확정형만 잡는다. 조건부 표현(검토 후, 여부, may be eligible)은 통과한다.
# 패턴과 허용 경계는 tests/cs/test_drafting.py의 예문 표로 고정한다.
FORBIDDEN_COMMITMENTS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bRMA\s+(?:is\s+|has\s+been\s+)?(?:approved|authori[sz]ed|issued)\b",
        r"\breturn\s+(?:is\s+|has\s+been\s+)?(?:approved|authori[sz]ed)\b",
        r"\bwarranty\s+(?:claim\s+)?(?:is\s+|has\s+been\s+)?approved\b",
        r"(?<!whether )(?<!if )\b(?:it|this|that|they|the\s+\w+)\s+(?:is|are|will\s+be)\s+"
        r"covered\s+(?:under|by)\s+(?:the\s+|your\s+|our\s+)?warranty\b",
        r"\bit[’']s\s+covered\s+(?:under|by)\s+(?:the\s+|your\s+|our\s+)?warranty\b",
        r"\bwe\s+will\s+(?:send|dispatch)\s+(?:a|an|our)\s+(?:field\s+)?(?:technician|engineer)\b",
        r"\b(?:site\s+visit|visit|technician|engineer)\s+(?:is|has\s+been)\s+"
        r"(?:scheduled|confirmed|booked)\b",
        r"\bwe\s+will\s+(?:repair|replace)\b[^.!?\n]{0,40}"
        r"\b(?:free\s+of\s+charge|at\s+no\s+(?:cost|charge))",
        r"RMA\s*(?:가|를|이)?\s*(?:승인|발급)\s*(?:되었|됐|했|하였|합니다|해\s*드리)",
        r"반품\s*(?:이|을|가)?\s*승인\s*(?:되었|됐|했|하였|합니다|해\s*드리)",
        r"방문\s*(?:하겠습니다|해\s*드리겠습니다)",
        r"방문\s*(?:일정)?\s*(?:이|을)?\s*확정\s*(?:되었|됐|했|하였|합니다)",
        r"보증\s*(?:이|으로)?\s*(?:승인|적용)\s*(?:되었|됐|됩니다|합니다|해\s*드리)",
        r"무상\s*(?:으로)?\s*(?:처리|교체|수리)\s*(?:해\s*드리|하겠습니다|됩니다|되었|됐)",
    )
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class EvidenceSnippet(_Strict):
    """검색 청크에서 고른 근거 한 조각. chunk_id로 출력 문장의 근거를 확인한다."""

    chunk_id: str = Field(min_length=1)
    snippet: str = Field(pattern=r"\S")
    section_path: list[str]
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DraftInput(_Strict):
    """모델에 전달할 초안 입력. 정답·사후 기록은 들어가지 않는다."""

    channel: str
    body_language: str
    subject: str | None
    body: str
    classification: ClassificationOutput
    provided_info: list[str]
    request_info_codes: list[str]
    evidence: list[EvidenceSnippet]


@dataclass(frozen=True)
class PreparedDraftInput:
    """모델용 초안 입력과 처리 판본·PHONE 주석 제거 건수."""

    payload: DraftInput
    version: str
    removed_annotations: int


def build_draft_input(
    record: Mapping[str, object],
    *,
    classification: ClassificationOutput,
    required_info_codes: frozenset[str],
    already_asked_codes: frozenset[str],
    evidence: list[EvidenceSnippet],
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
) -> PreparedDraftInput:
    """분류와 같은 규칙으로 문의를 정제하고 요청할 정보 코드를 계산한다.

    Args:
        record: 어댑터가 정규화한 문의. 네 필드 외의 정답·사후 기록은 선택하지 않는다.
        classification: 검사를 통과한 분류 결과. provided_info를 제공 코드로 쓴다.
        required_info_codes: 이 문의 유형에 필요한 정보 코드(호출자가 코드북에서 고름).
        already_asked_codes: 앞선 답변에서 이미 요청한 정보 코드.
        evidence: 근거 스니펫. chunk_id는 서로 달라야 한다.
        sensitive_values: 어댑터가 식별한 원값과 마스킹 종류.

    Returns:
        PreparedDraftInput: 정제 입력. 요청 코드는 정렬한 required − provided − already_asked다.
            잘못된 입력은 build_input과 같은 안전한 ValueError다.
    """
    prepared = build_input(record, sensitive_values=sensitive_values)
    if (
        not isinstance(classification, ClassificationOutput)
        or not all(isinstance(e, EvidenceSnippet) for e in evidence)
        or len({e.chunk_id for e in evidence}) != len(evidence)
        or not all(
            isinstance(code, str) and code for code in required_info_codes | already_asked_codes
        )
    ):
        raise ValueError("CS_INVALID_INPUT")
    provided = classification.provided_info
    request = sorted(required_info_codes - set(provided) - already_asked_codes)
    payload = DraftInput(
        **prepared.payload.model_dump(),
        classification=classification,
        provided_info=list(provided),
        request_info_codes=request,
        evidence=list(evidence),
    )
    return PreparedDraftInput(payload, DRAFT_INPUT_VERSION, prepared.removed_annotations)


class DraftSentence(_Strict):
    """초안 문장 하나. EVIDENCE는 근거 청크 ID가 1개 이상 있어야 한다."""

    text: str = Field(pattern=r"\S")
    kind: SentenceKind
    evidence_chunk_ids: list[str]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        """근거 문장의 근거 ID 유무와 중복을 검사한다.

        Args:
            self: 타입 검사를 통과한 문장.

        Returns:
            Self: 유효한 문장. 근거 ID가 입력에 있는지는 validate_output이 본다.
        """
        if len(set(self.evidence_chunk_ids)) != len(self.evidence_chunk_ids):
            raise ValueError("CS_DUPLICATE_EVIDENCE")
        if self.kind == "EVIDENCE" and not self.evidence_chunk_ids:
            raise ValueError("CS_EVIDENCE_REQUIRED")
        return self


class InfoRequest(_Strict):
    info_code: str
    question: str = Field(pattern=r"\S")


class DraftOption(_Strict):
    label: OptionLabel
    sentences: list[DraftSentence] = Field(min_length=1)
    info_requests: list[InfoRequest]

    @model_validator(mode="after")
    def validate_requests(self) -> Self:
        """선택지 안의 정보 요청 코드 중복을 거부한다.

        Args:
            self: 타입 검사를 통과한 선택지.

        Returns:
            Self: 유효한 선택지.
        """
        codes = [request.info_code for request in self.info_requests]
        if len(set(codes)) != len(codes):
            raise ValueError("CS_DUPLICATE_INFO")
        return self


class DraftOutput(_Strict):
    """문안 선택지 정확히 3개(CONCISE·DETAILED·INFO_FIRST 각 1개)."""

    options: list[DraftOption] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_labels(self) -> Self:
        """세 라벨이 한 번씩 나오는지 검사한다.

        Args:
            self: 타입 검사를 통과한 출력.

        Returns:
            Self: 유효한 출력.
        """
        if {option.label for option in self.options} != {"CONCISE", "DETAILED", "INFO_FIRST"}:
            raise ValueError("CS_DUPLICATE_OPTION")
        return self


DRAFT_PROMPT = (
    "당신은 전력기기 C/S 담당자의 답변 초안을 쓰는 보조자다. 아래 입력 JSON만 사용한다.\n"
    "- 문안 선택지 3개를 만든다: CONCISE(짧게), DETAILED(자세히), INFO_FIRST(추가 정보 요청 먼저).\n"
    "- 문장마다 kind를 붙인다. EVIDENCE는 evidence의 chunk_id로 뒷받침되는 문장이며 "
    "evidence_chunk_ids에 그 ID를 적는다. CONDITION은 조건에 따라 달라지는 안내, "
    "UNCONFIRMED는 아직 확인되지 않은 내용이다. 근거 없는 사실을 EVIDENCE로 쓰지 않는다.\n"
    "- info_requests에는 request_info_codes에 있는 코드만 쓴다. 고객이 이미 준 정보는 다시 묻지 않는다.\n"
    "- RMA·반품 승인, 현장 방문 확정, 보증 승인·무상 처리 확정을 약속하지 않는다. "
    "승인·적용 여부는 검토 후 안내한다고 쓴다.\n"
    "- 답변 언어는 body_language를 따른다. 마스킹 표식([SERIAL_PROVIDED] 등)은 원값을 추측하지 않는다.\n"
    "- 출력은 다음 JSON 스키마를 따르는 JSON 하나다. 다른 키를 더하지 않는다.\n"
    + json.dumps(DraftOutput.model_json_schema(), ensure_ascii=False, sort_keys=True)
)


def render_prompt(payload: DraftInput) -> str:
    """고정 지시문 뒤에 정제 입력 JSON을 붙인다.

    Args:
        payload: build_draft_input의 정제 입력.

    Returns:
        str: 공급자에 그대로 보낼 프롬프트.
    """
    return DRAFT_PROMPT + "\n\n입력:\n" + payload.model_dump_json()


def find_forbidden_commitment(text: str) -> bool:
    """승인·확정 약속 문구가 있는지 본다.

    Args:
        text: 초안 문장이나 질문.

    Returns:
        bool: FORBIDDEN_COMMITMENTS 중 하나라도 맞으면 True.
    """
    return any(pattern.search(text) for pattern in FORBIDDEN_COMMITMENTS)


def validate_output(
    raw: str, *, evidence_chunk_ids: frozenset[str], request_info_codes: frozenset[str]
) -> DraftOutput:
    """원시 JSON을 폴백 없이 검사하고 금지 약속을 거부한다.

    Args:
        raw: 모델 원시 응답.
        evidence_chunk_ids: 입력에 준 근거 chunk_id 집합.
        request_info_codes: 입력에 준 요청 대상 정보 코드 집합.

    Returns:
        DraftOutput: 유효한 초안. 형식·근거·코드 위반은 CS_INVALID_OUTPUT, 형식은 맞지만 승인·확정
            약속이 있으면 CS_FORBIDDEN_COMMITMENT ValueError다.
    """
    try:
        output = DraftOutput.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
        for option in output.options:
            if any(
                not set(sentence.evidence_chunk_ids) <= evidence_chunk_ids
                for sentence in option.sentences
            ) or any(r.info_code not in request_info_codes for r in option.info_requests):
                raise ValueError("CS_UNKNOWN_REFERENCE")
    except (ValueError, RecursionError):
        raise ValueError("CS_INVALID_OUTPUT") from None
    texts = [
        text
        for option in output.options
        for text in (
            *(sentence.text for sentence in option.sentences),
            *(request.question for request in option.info_requests),
        )
    ]
    if any(find_forbidden_commitment(text) for text in texts):
        raise ValueError("CS_FORBIDDEN_COMMITMENT")
    return output
