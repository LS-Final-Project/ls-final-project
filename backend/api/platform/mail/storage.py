"""메일 원문·첨부 바이트의 보관 위치. DB에는 키·해시·크기만 기록한다.

로컬·검사는 폴더 저장소, 운영은 Cloud Storage 버킷(`GcsObjectStore`)을 쓴다. 운영 버킷의
실행 계정은 객체 생성·읽기만 할 수 있고 삭제·덮어쓰기는 못 한다(`infra/setup_mail_bucket.sh`).
"""

import asyncio
import base64
import hashlib
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote
from uuid import UUID

import httpx

from backend.shared.errors import ApplicationError

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

GCS_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
GCS_OBJECT = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{name}"
GCS_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
# Cloud Storage 버킷 이름 규칙 중 이 프로젝트가 쓰는 형태(소문자·숫자·하이픈·점, 3~63자).
BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]")
StorageToken = Callable[[], Awaitable[str]]


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


def storage_unavailable() -> ApplicationError:
    """원시 응답·버킷·키를 담지 않는 저장 실패.

    Returns:
        ApplicationError: 수집 실행에 `MAIL_STORAGE_UNAVAILABLE`로 기록되는 오류.
    """
    return ApplicationError(503, "MAIL_STORAGE_UNAVAILABLE", "Mail object storage is unavailable.")


class GoogleAccessToken:
    """실행 환경의 Google 자격 증명으로 Google API access token을 준다."""

    def __init__(
        self,
        credentials: "Credentials | None" = None,
        *,
        scope: str = GCS_SCOPE,
        error: Callable[[], ApplicationError] = storage_unavailable,
    ) -> None:
        """자격 증명과 실패 코드를 정한다.

        Args:
            credentials: 검사용 자격 증명. 없으면 실행 환경 기본 자격 증명(Cloud Run 실행 계정).
            scope: 요청할 OAuth 범위. 기본은 Cloud Storage 읽기·쓰기.
            error: 토큰을 받지 못했을 때 낼 오류. 공급자 설명은 넣지 않는다.

        Returns:
            None.
        """
        if credentials is None:
            import google.auth

            credentials, _ = google.auth.default(scopes=[scope])
        self.credentials = credentials
        self.error = error
        self.lock = asyncio.Lock()

    async def __call__(self) -> str:
        """유효한 access token을 돌려준다. 만료됐으면 스레드에서 갱신한다.

        Returns:
            str: 요청 헤더에만 쓰는 토큰.
        """
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request

        async with self.lock:
            if not self.credentials.valid:
                try:
                    await asyncio.to_thread(self.credentials.refresh, Request())
                except GoogleAuthError:
                    raise self.error() from None
            token = self.credentials.token
        if not token:
            raise self.error()
        return str(token)


class GcsObjectStore:
    """Cloud Storage 버킷 저장소. 같은 키에는 한 번만 쓰고 기존 객체는 덮어쓰지 않는다."""

    def __init__(self, client: httpx.AsyncClient, bucket: str, token: StorageToken) -> None:
        """버킷과 인증을 정한다.

        Args:
            client: 제한 시간을 둔 클라이언트. 리디렉션은 따르지 않는다.
            bucket: 메일 객체 전용 버킷 이름(`MAIL_OBJECT_BUCKET`).
            token: Cloud Storage access token을 주는 함수.

        Returns:
            None.
        """
        if not BUCKET_NAME.fullmatch(bucket) or ".." in bucket:
            raise ValueError("Invalid mail object bucket name")
        self.client = client
        self.bucket = bucket
        self.token = token

    async def put(self, data: bytes, *, mailbox_id: UUID) -> StoredObject:
        """객체가 없을 때만 만든다. 이미 있으면 크기·MD5가 같은지 확인하고 그대로 쓴다.

        키에 내용 SHA-256이 들어 있어 같은 키면 같은 내용이어야 한다. 버킷 실행 계정에는
        덮어쓰기 권한이 없으므로 `ifGenerationMatch=0`으로 새 객체만 요청한다.

        Args:
            data: 보관할 바이트.
            mailbox_id: 이 바이트를 받은 사서함 연결 ID.

        Returns:
            StoredObject: 저장 위치. 저장소 장애·내용 불일치는 ApplicationError를 낸다.
        """
        digest = hashlib.sha256(data).hexdigest()
        key = content_key(mailbox_id, digest)
        # Cloud Storage가 받은 바이트의 MD5를 확인해, 전송 중 손상되면 객체를 만들지 않는다.
        md5 = base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
        stored = StoredObject(key=key, sha256=digest, size=len(data))
        response = await self.request(
            "POST",
            GCS_UPLOAD.format(bucket=self.bucket),
            params={"uploadType": "media", "name": key, "ifGenerationMatch": "0"},
            content=data,
            headers={"Content-Type": "application/octet-stream", "x-goog-hash": f"md5={md5}"},
        )
        if response.status_code == 200:
            self.check(response, stored, md5)
            return stored
        # 412는 이미 있는 객체, 403은 덮어쓰기 권한이 없어 거부된 경우일 수 있다. 실제로
        # 같은 객체가 있을 때만 성공으로 보고, 그 밖의 거부는 저장 실패로 기록한다.
        if response.status_code in (403, 412):
            existing = await self.request(
                "GET", GCS_OBJECT.format(bucket=self.bucket, name=quote(key, safe=""))
            )
            if existing.status_code == 200:
                self.check(existing, stored, md5)
                return stored
        raise storage_unavailable()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """인증 헤더를 붙여 Cloud Storage를 한 번 호출한다.

        Args:
            method: HTTP 메서드.
            url: Cloud Storage JSON API 주소.
            **kwargs: httpx 요청 인자.

        Returns:
            httpx.Response: 응답. 연결 실패는 ApplicationError로 바꾼다.
        """
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {await self.token()}"}
        try:
            return await self.client.request(
                method, url, headers=headers, follow_redirects=False, **kwargs
            )
        except httpx.HTTPError:
            raise storage_unavailable() from None

    @staticmethod
    def check(response: httpx.Response, stored: StoredObject, md5: str) -> None:
        """객체 메타데이터가 보관하려던 바이트와 같은지 확인한다.

        Args:
            response: 객체 메타데이터 응답.
            stored: 보관하려던 위치·크기.
            md5: 보관하려던 바이트의 base64 MD5.

        Returns:
            None. 다르면 ApplicationError를 낸다.
        """
        try:
            meta = response.json()
            same = (
                meta.get("name") == stored.key
                and int(meta.get("size", -1)) == stored.size
                and meta.get("md5Hash") == md5
            )
        except (ValueError, TypeError, AttributeError):
            same = False
        if not same:
            raise storage_unavailable()
