"""원문·수신 범위·행위자를 노출하지 않는 내 알림 응답."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from backend.shared.notifications import Kind


class Notification(BaseModel):
    id: UUID
    kind: Kind
    title: str
    link_path: str
    created_at: datetime
    read_at: datetime | None


class NotificationList(BaseModel):
    items: list[Notification]
    next_offset: int | None


class UnreadCount(BaseModel):
    count: int
