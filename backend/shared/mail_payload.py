"""승인 메일 내용의 정규화 해시. 업무 서비스(C/S)와 플랫폼이 같은 규칙으로 계산한다(KAN-74·110).

업무는 담당자가 승인한 내용으로 이 값을 계산해 승인 기록에 묶고, 플랫폼은 받은 내용으로 다시
계산해 같을 때만 발송 의도를 만든다. 저장 키는 넣지 않는다.
"""

import hashlib
import json
from typing import Any


def payload_sha256(
    *,
    subject: str,
    body_type: str,
    body: str,
    to: list[str] | tuple[str, ...],
    cc: list[str] | tuple[str, ...],
    attachments: list[dict[str, Any]],
) -> str:
    """승인 대상 내용의 정규화 해시.

    Args:
        subject: 제목.
        body_type: `text` 또는 `html`.
        body: 본문.
        to: 받는 사람 주소(입력 순서 유지).
        cc: 참조 주소.
        attachments: 첨부(이름·형식·SHA-256·크기). 저장 키는 넣지 않는다.

    Returns:
        str: 소문자 16진수 SHA-256.
    """
    canonical = {
        "subject": subject,
        "body_type": body_type,
        "body": body,
        "to": list(to),
        "cc": list(cc),
        "attachments": [
            {k: a[k] for k in ("name", "content_type", "sha256", "size")} for a in attachments
        ],
    }
    text = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()
