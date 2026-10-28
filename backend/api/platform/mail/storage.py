"""메일 원문·첨부 바이트의 보관 위치. DB에는 키·해시·크기만 기록한다."""

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class StoredObject:
    """보관한 바이트의 위치와 무결성 정보."""

    key: str
    sha256: str
    size: int


class ObjectStore(Protocol):
    """사서함 안에서 같은 내용은 같은 키에 한 번만 쓰는 저장소."""

    async def put(self, data: bytes, *, mailbox_id: UUID) -> StoredObject:
        """바이트를 보관하고 위치를 반환한다.

        Args:
            data: 보관할 원문 또는 첨부 바이트.
            mailbox_id: 이 바이트를 받은 사서함 연결 ID.

        Returns:
            StoredObject: 사서함과 내용 해시로 정한 키와 크기.
        """
        ...


def content_key(mailbox_id: UUID, digest: str) -> str:
    """사서함과 내용 해시로 저장 키를 정한다.

    사서함마다 키를 나눠, 한 사서함의 객체를 지우거나 권한을 좁혀도 다른 사서함의
    같은 내용 객체에 영향이 없게 한다.

    Args:
        mailbox_id: 사서함 연결 ID.
        digest: 소문자 16진수 SHA-256.

    Returns:
        str: 사서함 폴더 아래 해시 앞 두 글자로 나눈 상대 키.
    """
    return f"mail/{mailbox_id}/sha256/{digest[:2]}/{digest}"


class LocalObjectStore:
    """로컬 개발·검사용 폴더 저장소. 운영은 Cloud Storage 구현으로 바꾼다."""

    def __init__(self, root: Path) -> None:
        """보관 폴더를 지정한다.

        Args:
            root: 저장소 최상위 폴더. 없으면 쓰기 시 만든다.

        Returns:
            None.
        """
        self.root = root

    async def put(self, data: bytes, *, mailbox_id: UUID) -> StoredObject:
        """임시 파일에 쓴 뒤 같은 폴더에서 이름을 바꿔 부분 쓰기를 남기지 않는다.

        Args:
            data: 보관할 바이트.
            mailbox_id: 이 바이트를 받은 사서함 연결 ID.

        Returns:
            StoredObject: 같은 사서함에 같은 내용이 있으면 다시 쓰지 않은 위치.
        """
        # 해시·파일 쓰기는 이벤트 루프를 막지 않도록 스레드에서 한다.
        return await asyncio.to_thread(self._put, data, mailbox_id)

    def _put(self, data: bytes, mailbox_id: UUID) -> StoredObject:
        """put의 동기 본문.

        Args:
            data: 보관할 바이트.
            mailbox_id: 사서함 연결 ID.

        Returns:
            StoredObject: 저장 위치.
        """
        digest = hashlib.sha256(data).hexdigest()
        key = content_key(mailbox_id, digest)
        path = self.root / key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".partial-")
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp, path)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
        return StoredObject(key=key, sha256=digest, size=len(data))
