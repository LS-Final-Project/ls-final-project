"""고객 회신 초안의 AI 보조 세 가지(KAN-251). 모델 호출은 하지 않고 입력 정제·프롬프트·출력 검사만 둔다.

- 부서 답변 기반 초안(`DEPT_DRAFT`): 문의 원문과 C/S 담당자가 확인 완료(ACCEPTED)한 부서 답변·부서 제안 문안으로
  고객 언어 회신 하나를 쓴다. 회신의 사실은 부서 답변에서만 가져온다.
- 한국어 번역(`TO_KO`): 회신 상자의 초안(AI 선택지, 부서 답변 기반 초안, 담당자가 쓴 글)을 한국어로 옮긴다. 읽기 보조다.
- 한국어 수정 반영(`FROM_KO`): 담당자가 한국어 번역을 고치면, 원래 초안에서 고친 부분만 고객 언어로 바꾼다.

모든 입력은 분류와 같은 연락처·시리얼 가림(mask_contacts_and_serials)을 거친다. 출력 검사(공통):
- JSON 하나 `{"text": "..."}`, 길이 상한(기준 글의 3배, 최소 400자, 최대 8000자).
- 언어: 한국어로 쓸 출력(TO_KO, 고객 언어가 한국어인 회신)에는 한글이 있어야 하고, 고객 언어가 한국어가 아니면
  고객에게 갈 출력(DEPT_DRAFT·FROM_KO)에 한글이 없어야 한다.
- 코드·수는 입력에 있는 것만(요약·번역과 같은 ungrounded), 점으로 이은 수와 가림 표시도 입력에 있는 것만.
- 인용 차단 코드(FLT-042)를 말하면 거부한다.
- 승인·확정 약속(drafting.find_forbidden_commitment): DEPT_DRAFT는 있으면 거부한다(분석 초안과 같다. 부서가 승인했어도
  AI 초안은 확정형으로 쓰지 않고 담당자가 확정한다). FROM_KO는 담당자가 한국어에 직접 쓴 약속이면 사람의 결정이라
  막지 않고 화면이 경고하며(`commitment_warning`), 한국어에 없던 약속이 생기면 거부한다. TO_KO는 원문을 그대로
  옮기는 읽기 보조라 보지 않는다.
정규식은 영어·한국어 문구만 잡는다. 다른 언어 회신의 약속은 담당자가 한국어 번역으로 확인하고, 보내기는 늘 사람의 승인을 거친다.

프롬프트는 이 모듈에 둔다. 요약·번역처럼 채택 전 기능이라 prompt_registry의 평가·채택 게이트에 묶지 않는다.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, ValidationError

from backend.engine.cs.classification import _unique_object, mask_contacts_and_serials
from backend.engine.cs.drafting import _Strict, find_forbidden_commitment
from backend.engine.cs.prompt_registry import CONFIRMATION_RULE
from backend.engine.cs.summary import HANGUL, ungrounded
from backend.engine.cs.translation import DOTTED, PLACEHOLDER, build_translation_input
from backend.engine.search.hybrid import BLOCKED_CODES, mentions_code

Kind = Literal["DEPT_DRAFT", "TO_KO", "FROM_KO"]
INPUT_VERSION = "cs-reply-assist-input-v1"
PROMPT_VERSIONS: dict[Kind, str] = {
    "DEPT_DRAFT": "dept-draft-v1",
    "TO_KO": "draft-ko-v1",
    "FROM_KO": "draft-from-ko-v1",
}
MAX_TEXT_CHARS = 8000
MIN_LIMIT = 400
# 출력 길이 상한 = 기준 글 길이 × 배수. 한국어 → 다른 언어(부서 답변 초안, 한국어 수정 반영)는 글자 수가
# 2~4배로 늘고 인사·맺음말이 붙으므로 넉넉히 둔다(2026-11-09 실제 모델: 한국어 167자 → 스페인어 약 600자).
RATIO: dict[Kind, int] = {"DEPT_DRAFT": 8, "TO_KO": 3, "FROM_KO": 8}
LETTER = re.compile(r"[^\W\d_]")
_FACTS = (
    "- 숫자, 단위, 날짜, 모델명, 시리얼, 오류 코드, 펌웨어 판본은 입력 표기 그대로 쓰고 새로 만들거나 바꾸지 "
    "않는다(예: 2026-10-02, 4.2.1, E-204, 35 kV). [PERSONAL_PROVIDED]·[SERIAL_PROVIDED] 같은 가림 표시는 "
    "그대로 둔다.\n"
)
_OUTPUT = '- 출력은 JSON 하나: {"text": "..."}'
INSTRUCTIONS: dict[Kind, str] = {
    "DEPT_DRAFT": (
        "당신은 전력기기 해외 C/S 담당자가 고객에게 보낼 회신 초안을 쓰는 보조자다. 아래 입력 JSON만 사용한다.\n"
        "- dept_answer는 C/S 담당자가 확인을 마친 전문부서 답변이고, dept_reply는 부서가 제안한 고객 문안(없으면 "
        "null)이다. 회신의 사실은 이 둘에서만 가져온다. subject·body(고객 문의)는 무엇에 답하는지 알기 위해서만 "
        "쓴다.\n"
        "- 부서 답변에 없는 원인·일정·수량·가격·조치·약속을 더하지 않는다. 부서가 불확실하게 쓴 것은 불확실하게 "
        "쓴다.\n"
        "- RMA·반품 승인, 현장 방문 확정, 보증 승인·무상 처리 확정을 약속하지 않는다. 부서 답변에 그런 결정이 "
        "있어도 확정형으로 쓰지 말고 담당자가 따로 안내한다고 쓴다.\n"
        + CONFIRMATION_RULE
        + "- 회신은 body_language(고객 언어)로 쓴다. 부서 답변이 한국어여도 회신은 고객 언어다.\n"
        + _FACTS
        + "- 서명, 담당자 이름, 연락처는 쓰지 않는다.\n"
        + _OUTPUT
    ),
    "TO_KO": (
        "당신은 전력기기 C/S 담당자가 고객에게 보낼 회신 초안을 한국인 담당자가 읽도록 한국어로 옮기는 "
        "번역자다. 아래 입력 JSON의 text만 옮긴다(language는 text의 언어다).\n"
        "- 요약하지 말고 전체를 빠짐없이 자연스러운 한국어로 옮긴다. 문단·줄바꿈(\\n)은 그대로 둔다.\n"
        "- 원문에 없는 내용을 더하거나 빼지 않는다. 약속·조건·불확실한 표현의 강도를 바꾸지 않는다(예: may는 "
        "'…일 수 있습니다').\n" + _FACTS + _OUTPUT
    ),
    "FROM_KO": (
        "당신은 전력기기 C/S 담당자가 한국어로 고친 회신을 고객 언어 초안에 반영하는 번역자다. 아래 입력 JSON만 "
        "사용한다.\n"
        "- original은 고객 언어(language)로 쓴 원래 초안, original_ko는 그 한국어 번역, edited_ko는 담당자가 고친 "
        "한국어다.\n"
        "- edited_ko의 뜻을 language로 옮긴 회신 하나를 쓴다. original_ko와 같은 문장은 original의 해당 문장을 "
        "글자 그대로 둔다. 바뀐 문장과 더한 문장만 새로 옮기고, 지운 문장은 뺀다. 문단·줄바꿈은 edited_ko를 "
        "따른다.\n"
        "- edited_ko에 없는 내용을 더하지 않는다. 약속·조건·불확실한 표현의 강도를 edited_ko 그대로 옮긴다.\n"
        + _FACTS
        + _OUTPUT
    ),
}
ErrorCode = Literal[
    "CS_REPLY_ASSIST_SCHEMA",
    "CS_REPLY_ASSIST_LANGUAGE",
    "CS_REPLY_ASSIST_UNGROUNDED",
    "CS_BLOCKED_EVIDENCE",
    "CS_FORBIDDEN_COMMITMENT",
]


class DeptDraftInput(_Strict):
    channel: str
    body_language: str
    subject: str | None
    body: str
    dept_answer: str = Field(pattern=r"\S")
    dept_reply: str | None


class KoreanInput(_Strict):
    language: str
    text: str = Field(pattern=r"\S")


class KoreanEditInput(_Strict):
    language: str
    original: str = Field(pattern=r"\S")
    original_ko: str = Field(pattern=r"\S")
    edited_ko: str = Field(pattern=r"\S")


Payload = DeptDraftInput | KoreanInput | KoreanEditInput


class TextOutput(_Strict):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS, pattern=r"\S")


class ReplyAssistError(ValueError):
    """모델 출력이 규칙을 어겼다. 원문·출력은 담지 않고 코드만 남긴다."""

    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code)
        self.code: ErrorCode = code


def mask(value: str) -> str:
    """모델에 보낼 글. 분류 입력과 같은 연락처·시리얼 가림과 앞뒤 공백 제거."""
    return mask_contacts_and_serials(value).strip()


def korean_language(language: str) -> bool:
    """고객 언어가 한국어(ko, ko-KR 등)인지."""
    return language.split("-", 1)[0] == "ko"


def mostly_korean(text: str) -> bool:
    """글자의 절반 이상이 한글이면 True(이미 한국어인 초안은 번역하지 않는다)."""
    letters = len(LETTER.findall(text))
    return letters > 0 and 2 * len(HANGUL.findall(text)) >= letters


def build_dept_draft_input(
    ticket: Mapping[str, object], result: Mapping[str, object]
) -> DeptDraftInput:
    """문의(번역과 같은 허용 필드·마스킹)와 확인 완료된 부서 답변으로 모델 입력을 만든다.

    Args:
        ticket: 문의 행.
        result: 부서 답변 행(result·reply_content).

    Returns:
        DeptDraftInput: 모델 입력. 문의 본문이나 부서 답변이 비면 ValueError다.
    """
    payload = build_translation_input(ticket)
    reply = result.get("reply_content")
    return DeptDraftInput(
        **payload.model_dump(),
        dept_answer=mask(str(result["result"])),
        dept_reply=mask(reply) if isinstance(reply, str) and reply.strip() else None,
    )


def input_sha256(kind: Kind, payload: Payload) -> str:
    """같은 입력이면 저장본을 다시 쓰는 열쇠. 글이나 판본이 바뀌면 달라진다."""
    encoded = json.dumps(
        {"v": INPUT_VERSION, "kind": kind, **payload.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def render_prompt(kind: Kind, payload: Payload) -> str:
    return INSTRUCTIONS[kind] + "\n\n입력:\n" + payload.model_dump_json()


def _basis(payload: Payload) -> tuple[str, str, str]:
    """(출력 언어, 길이 기준 글, 코드·수 근거 글)."""
    if isinstance(payload, DeptDraftInput):
        dept = f"{payload.dept_answer}\n{payload.dept_reply or ''}"
        return payload.body_language, dept, f"{dept}\n{payload.subject or ''}\n{payload.body}"
    if isinstance(payload, KoreanInput):
        return "ko", payload.text, payload.text
    return payload.language, payload.edited_ko, f"{payload.original}\n{payload.edited_ko}"


def validate_output(raw: str, *, kind: Kind, payload: Payload) -> str:
    """모델 원시 출력을 검사한다.

    Args:
        raw: 모델 응답 텍스트(JSON).
        kind: 보조 종류.
        payload: 모델에 보낸 입력.

    Returns:
        str: 규칙을 지킨 글(앞뒤 공백만 뗀다). 어기면 ReplyAssistError(코드만)다.
    """
    try:
        text = TextOutput.model_validate(json.loads(raw, object_pairs_hook=_unique_object)).text
    except (ValueError, ValidationError, TypeError, RecursionError):
        raise ReplyAssistError("CS_REPLY_ASSIST_SCHEMA") from None
    text = text.strip()
    language, length_basis, source = _basis(payload)
    if len(text) > min(MAX_TEXT_CHARS, max(MIN_LIMIT, RATIO[kind] * len(length_basis))):
        raise ReplyAssistError("CS_REPLY_ASSIST_SCHEMA")
    if bool(HANGUL.search(text)) != korean_language(language):
        raise ReplyAssistError("CS_REPLY_ASSIST_LANGUAGE")
    if ungrounded(text, source) or any(
        not set(pattern.findall(text)) <= set(pattern.findall(source))
        for pattern in (DOTTED, PLACEHOLDER)
    ):
        raise ReplyAssistError("CS_REPLY_ASSIST_UNGROUNDED")
    if any(mentions_code(text, code) for code in BLOCKED_CODES):
        raise ReplyAssistError("CS_BLOCKED_EVIDENCE")
    if kind != "TO_KO" and find_forbidden_commitment(text):
        # 담당자가 한국어에 직접 쓴 약속은 사람의 결정이다(화면 경고). 그 밖의 약속은 AI가 만든 것이다.
        if not (
            isinstance(payload, KoreanEditInput) and find_forbidden_commitment(payload.edited_ko)
        ):
            raise ReplyAssistError("CS_FORBIDDEN_COMMITMENT")
    return text
