"""메일 원문·첨부 바이트의 보관 위치. DB에는 키·해시·크기만 기록한다."""

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    """보관한 바이트의 위치와 무결성 정보."""

    key: str
    sha256: str
    size: int


class ObjectStore(Protocol):
    """같은 내용은 같은 키에 한 번만 쓰는 저장소."""

    async def put(self, data: bytes) -> StoredObject:
        """바이트를 보관하고 위치를 반환한다.

        Args:
            data: 보관할 원문 또는 첨부 바이트.

        Returns:
            StoredObject: 내용 해시로 정한 키와 크기.
        """
        ...


def content_key(digest: str) -> str:
    """내용 해시로 저장 키를 정한다.

    Args:
        digest: 소문자 16진수 SHA-256.

    Returns:
        str: 해시 앞 두 글자로 나눈 상대 키.
    """
    return f"mail/sha256/{digest[:2]}/{digest}"


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

    async def put(self, data: bytes) -> StoredObject:
        """임시 파일에 쓴 뒤 같은 폴더에서 이름을 바꿔 부분 쓰기를 남기지 않는다.

        Args:
            data: 보관할 바이트.

        Returns:
            StoredObject: 이미 같은 내용이 있으면 다시 쓰지 않은 위치.
        """
        # 해시·파일 쓰기는 이벤트 루프를 막지 않도록 스레드에서 한다.
        return await asyncio.to_thread(self._put, data)

    def _put(self, data: bytes) -> StoredObject:
        """put의 동기 본문.

        Args:
            data: 보관할 바이트.

        Returns:
            StoredObject: 저장 위치.
        """
        digest = hashlib.sha256(data).hexdigest()
        key = content_key(digest)
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
