"""업무 트랜잭션에 수신 범위만 기록한다(KAN-201). identity·외부 서비스·commit은 다루지 않는다."""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal[
    "cs.review_wait",
    "cs.safety",
    "cs.reply",
    "mail.unknown",
    "cs.transfer",
    "rfp.done",
    "rfp.failed",
]
# 제목·경로를 호출자 문자열에서 받지 않는다. 원문·고객명·오류 코드는 알림에 없다.
EVENTS: dict[str, tuple[str, str, str]] = {
    "cs.review_wait": ("cs", "C/S 답변 확인 대기", "/cs/tickets/"),
    "cs.safety": ("cs", "안전 문의 확인 필요", "/cs/tickets/"),
    "cs.reply": ("cs", "고객 회신 재검토", "/cs/tickets/"),
    "mail.unknown": ("platform", "메일 발송 결과 확인 필요", "/cs/tickets/"),
    "cs.transfer": ("cs", "이관 문의 수신", "/cs/tickets/"),
    "rfp.done": ("tft", "RFP 처리 완료", "/tft/projects/"),
    "rfp.failed": ("tft", "RFP 처리 실패", "/tft/projects/"),
}
INSERT_INTENT = (
    "INSERT INTO runtime.notification_intents "
    "(id,owner,kind,title,link_path,audience,actor_id,dedupe_key) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING"
)


class Audience(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user: UUID | None = None
    role: Literal["cs_agent"] | None = None
    dept: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    manager_dept: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")

    @model_validator(mode="after")
    def one_scope(self) -> Self:
        if len(self.model_fields_set) != 1 or len(self.model_dump(exclude_none=True)) != 1:
            raise ValueError("NOTIFICATION_INVALID_AUDIENCE")
        return self


def intent_values(
    *,
    kind: Kind,
    subject_id: UUID,
    audience: Sequence[Mapping[str, Any]],
    actor_id: UUID | None,
    dedupe_key: str,
    needs_review: bool = False,
) -> tuple[Any, ...]:
    """동기·비동기 쓰기가 공유하는 고정 문구·경로·범위 검증."""
    if (
        kind not in EVENTS
        or not isinstance(subject_id, UUID)
        or (actor_id is not None and not isinstance(actor_id, UUID))
        or not isinstance(dedupe_key, str)
        or not 1 <= len(dedupe_key) <= 200
        or not dedupe_key.startswith(kind + ":")
        or type(needs_review) is not bool
        or (needs_review and kind != "rfp.done")
        or not 1 <= len(audience) <= 64
    ):
        raise ValueError("NOTIFICATION_INVALID_INPUT")
    scopes = [
        Audience.model_validate(value).model_dump(mode="json", exclude_none=True)
        for value in audience
    ]
    scopes = [
        json.loads(value) for value in dict.fromkeys(json.dumps(s, sort_keys=True) for s in scopes)
    ]
    owner, title, prefix = EVENTS[kind]
    if needs_review:
        title = "RFP 처리 완료, 검토 필요"
    return (
        uuid4(),
        owner,
        kind,
        title,
        prefix + str(subject_id),
        Jsonb(scopes),
        actor_id,
        dedupe_key,
    )


def enqueue_notification_sync(conn: psycopg.Connection[Any], **values: Any) -> bool:
    """제공된 동기 트랜잭션에 INSERT만 한다. 조회·RETURNING·commit하지 않는다."""
    return conn.execute(INSERT_INTENT, intent_values(**values)).rowcount == 1


async def enqueue_notification(conn: psycopg.AsyncConnection[Any], **values: Any) -> bool:
    """제공된 비동기 트랜잭션에 INSERT만 한다. 조회·RETURNING·commit하지 않는다."""
    return (await conn.execute(INSERT_INTENT, intent_values(**values))).rowcount == 1
