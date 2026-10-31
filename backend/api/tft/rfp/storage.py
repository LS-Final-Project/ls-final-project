"""RFP 원문 저장소(KAN-199·KAN-188). 운영은 Cloud Storage 버킷, local/test는 `TFT_DOCUMENT_ROOT` 폴더다.

읽은 바이트는 판본 행의 sha256·size와 맞아야 한다(다른 파일을 처리하지 않게). 메일 저장소와 같은 방식이지만 서비스
경계를 넘어 import하지 않고 여기에 둔다. 같은 키는 한 번만 쓰고 덮어쓰지 않는다. 실패에는 버킷·키·공급자 원문을
담지 않는다.
"""

import asyncio
import base64
import hashlib
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

GCS_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
GCS_OBJECT = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{name}"
GCS_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]")
StorageToken = Callable[[], Awaitable[str]]


class DocumentUnavailable(ValueError):
    """원문을 읽거나 쓰지 못했다. 경로·공급자 원문은 담지 않는다."""


class DocumentStorageSettings(BaseSettings):
    """원문 위치. 버킷이 있으면 버킷, 없으면 폴더(처리 함수와 같은 `TFT_DOCUMENT_ROOT`)다."""

    model_config = SettingsConfigDict(env_prefix="TFT_", env_file=".env", extra="ignore")
    document_root: Path | None = None
    document_bucket: str | None = None


class DocumentStore(Protocol):
    """원문 바이트 저장소."""

    async def put(self, key: str, data: bytes) -> None:
        """같은 키가 없을 때만 쓴다.

        Args:
            key: 저장 키.
            data: 원문.
        """
        ...

    async def get(self, key: str) -> bytes:
        """저장된 바이트를 읽는다.

        Args:
            key: 저장 키.

        Returns:
            bytes: 원문. 없거나 읽지 못하면 DocumentUnavailable이다.
        """
        ...


class LocalDocumentStore:
    """폴더 저장소(local·검사). 파일 입출력은 이벤트 루프를 막지 않게 스레드에서 한다."""

    def __init__(self, root: Path) -> None:
        """폴더를 정한다.

        Args:
            root: 원문 폴더.
        """
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise DocumentUnavailable("TFT_DOCUMENT_NOT_READABLE")
        return path

    async def put(self, key: str, data: bytes) -> None:
        """같은 키가 없을 때만 쓴다.

        Args:
            key: 폴더 안 상대 경로.
            data: 원문.
        """

        def write() -> None:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as file:
                file.write(data)

        try:
            await asyncio.to_thread(write)
        except OSError:
            raise DocumentUnavailable("TFT_DOCUMENT_NOT_WRITABLE") from None

    async def get(self, key: str) -> bytes:
        """저장된 바이트를 읽는다.

        Args:
            key: 폴더 안 상대 경로.

        Returns:
            bytes: 원문. 폴더 밖 경로·없는 파일이면 DocumentUnavailable이다.
        """

        def read() -> bytes:
            path = self._path(key)
            if not path.is_file():
                raise DocumentUnavailable("TFT_DOCUMENT_NOT_READABLE")
            return path.read_bytes()

        try:
            return await asyncio.to_thread(read)
        except OSError:
            raise DocumentUnavailable("TFT_DOCUMENT_NOT_READABLE") from None


async def default_token() -> str:
    """실행 환경 기본 자격 증명으로 Cloud Storage access token을 받는다.

    Returns:
        str: 요청 헤더에만 쓰는 토큰. 받지 못하면 DocumentUnavailable이다.
    """

    def fetch() -> str:
        import google.auth
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request

        try:
            credentials, _ = google.auth.default(scopes=[GCS_SCOPE])
            credentials.refresh(Request())
        except GoogleAuthError:
            raise DocumentUnavailable("TFT_DOCUMENT_TOKEN") from None
        return str(credentials.token)

    return await asyncio.to_thread(fetch)


class GcsDocumentStore:
    """Cloud Storage 버킷 저장소. 실행 계정은 객체 생성·읽기만 한다(`infra/setup_rfp_bucket.sh`)."""

    def __init__(
        self,
        bucket: str,
        *,
        token: StorageToken = default_token,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """버킷과 인증을 정한다.

        Args:
            bucket: 원문 전용 버킷 이름(`TFT_DOCUMENT_BUCKET`).
            token: access token을 주는 함수.
            transport: 검사용 전송.
        """
        if not BUCKET_NAME.fullmatch(bucket) or ".." in bucket:
            raise ValueError("Invalid document bucket name")
        self.bucket, self.token, self.transport = bucket, token, transport

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {await self.token()}"}
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=False, transport=self.transport
            ) as client:
                return await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise DocumentUnavailable("TFT_DOCUMENT_STORAGE") from None

    async def put(self, key: str, data: bytes) -> None:
        """객체가 없을 때만 만든다(`ifGenerationMatch=0`). 전송 중 손상은 MD5로 막는다.

        Args:
            key: 객체 이름.
            data: 원문.
        """
        md5 = base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
        response = await self._request(
            "POST",
            GCS_UPLOAD.format(bucket=self.bucket),
            params={"uploadType": "media", "name": key, "ifGenerationMatch": "0"},
            content=data,
            headers={"Content-Type": "application/octet-stream", "x-goog-hash": f"md5={md5}"},
        )
        if response.status_code != 200:
            raise DocumentUnavailable("TFT_DOCUMENT_NOT_WRITABLE")

    async def get(self, key: str) -> bytes:
        """객체 바이트를 내려받는다.

        Args:
            key: 객체 이름.

        Returns:
            bytes: 원문. 없거나 실패하면 DocumentUnavailable이다.
        """
        response = await self._request(
            "GET",
            GCS_OBJECT.format(bucket=self.bucket, name=quote(key, safe="")),
            params={"alt": "media"},
        )
        if response.status_code != 200:
            raise DocumentUnavailable("TFT_DOCUMENT_NOT_READABLE")
        return response.content


def document_store(settings: DocumentStorageSettings) -> DocumentStore | None:
    """설정에 맞는 저장소를 고른다.

    Args:
        settings: 원문 위치 설정.

    Returns:
        DocumentStore | None: 버킷 우선, 없으면 폴더, 둘 다 없으면 None.
    """
    if settings.document_bucket:
        return GcsDocumentStore(settings.document_bucket)
    return LocalDocumentStore(settings.document_root) if settings.document_root else None


async def read_document(store: DocumentStore, storage_key: str, sha256: str, size: int) -> bytes:
    """저장 키의 원문을 읽고 해시·크기를 확인한다.

    Args:
        store: 저장소.
        storage_key: 판본 행의 저장 키.
        sha256: 판본 행의 SHA-256.
        size: 판본 행의 바이트 수.

    Returns:
        bytes: 원문. 없거나 해시·크기가 다르면 DocumentUnavailable이다.
    """
    data = await store.get(storage_key)
    digest = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
    if len(data) != size or digest != sha256:
        raise DocumentUnavailable("TFT_DOCUMENT_MISMATCH")
    return data
