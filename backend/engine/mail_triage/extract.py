"""원문 MIME에서 분류 입력(제목·본문 텍스트·메타)을 만든다. 네트워크·DB를 쓰지 않는다."""

import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.engine.cs.classification import mask_contacts_and_serials

INPUT_VERSION = "mail-triage-input-v1"
BODY_LIMIT = 8_000
SUBJECT_LIMIT = 500
# 회신 아래의 이전 메일은 분류 근거로 쓰지 않는다. 흔한 구분선만 자르는 단순 규칙이다.
# ponytail: 클라이언트별 인용 형식 전부를 다루지 않는다. 오분류가 보이면 규칙을 늘린다.
_QUOTE_START = re.compile(
    r"^(?:-{2,}\s*(?:Original Message|원본 메시지)\s*-{2,}|On .+ wrote:|From:\s.+|보낸 사람:.*)$",
    re.MULTILINE,
)


class AttachmentSummary(BaseModel):
    """첨부의 종류만. 파일 이름·내용은 개인정보일 수 있어 입력에 넣지 않는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["file", "item", "reference"]
    content_type: str | None
    is_inline: bool


class TriageInput(BaseModel):
    """모델에 넘길 마스킹된 입력. 원문·주소 원값을 담지 않는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    folder: Literal["inbox", "sentitems"]
    subject: str
    body: str
    is_reply: bool
    attachments: tuple[AttachmentSummary, ...]


class _Text(HTMLParser):
    """HTML 본문의 보이는 텍스트만 모은다(script·style 제외)."""

    def __init__(self) -> None:
        """빈 텍스트 버퍼로 시작한다.

        Returns:
            None: 파서를 준비한다.
        """
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """줄바꿈 태그는 개행으로, script·style은 건너뛴다.

        Args:
            tag: 태그 이름.
            attrs: 속성(쓰지 않음).

        Returns:
            None: 버퍼를 갱신한다.
        """
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in {"br", "p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """script·style을 닫는다.

        Args:
            tag: 태그 이름.

        Returns:
            None: 건너뛰기 깊이를 줄인다.
        """
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        """보이는 텍스트를 모은다.

        Args:
            data: 태그 사이 텍스트.

        Returns:
            None: 버퍼에 더한다.
        """
        if not self._skip:
            self.parts.append(data)


def _body_text(message: EmailMessage) -> str:
    """text/plain을 우선하고 없으면 text/html의 보이는 텍스트를 쓴다.

    Args:
        message: 파싱한 메일.

    Returns:
        str: 본문 텍스트. 없으면 빈 문자열.
    """
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if not isinstance(content, str):
        return ""
    if part.get_content_subtype() == "html":
        parser = _Text()
        parser.feed(content)
        content = "".join(parser.parts)
    return content


def _clean(value: str) -> str:
    """인용 이력과 '>' 인용 줄을 자르고 공백을 정리한다. 길이 제한은 마스킹 뒤에 한다.

    Args:
        value: 원본 텍스트.

    Returns:
        str: 정리한 텍스트.
    """
    quoted = _QUOTE_START.search(value)
    if quoted:
        value = value[: quoted.start()]
    lines = [line.rstrip() for line in value.splitlines() if not line.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def build_input(
    raw: bytes,
    *,
    folder: Literal["inbox", "sentitems"],
    attachments: tuple[AttachmentSummary, ...],
) -> TriageInput:
    """원문 MIME 한 통을 마스킹된 분류 입력으로 바꾼다.

    Args:
        raw: 객체 저장소에서 읽은 원문 MIME 바이트.
        folder: 수집 폴더.
        attachments: 수집 표의 첨부 종류 요약(이름 없음).

    Returns:
        TriageInput: 마스킹한 제목·본문, 회신 여부, 첨부 요약. 파싱할 수 없으면
        ValueError("MAIL_TRIAGE_INVALID_INPUT").
    """
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if not isinstance(message, EmailMessage):
            raise ValueError
        subject = str(message.get("Subject", "") or "")
        body = _body_text(message)
        is_reply = bool(message.get("In-Reply-To") or message.get("References"))
    except (ValueError, LookupError, TypeError):
        raise ValueError("MAIL_TRIAGE_INVALID_INPUT") from None
    return TriageInput(
        folder=folder,
        # 먼저 가리고 자른다. 자른 뒤 가리면 경계에 걸친 주소·시리얼 조각이 남는다.
        subject=mask_contacts_and_serials(_clean(subject))[:SUBJECT_LIMIT],
        body=mask_contacts_and_serials(_clean(body))[:BODY_LIMIT],
        is_reply=is_reply,
        attachments=attachments,
    )
