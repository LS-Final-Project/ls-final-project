"""플랫폼 전용 사서함 캐시 보관 경계. HTTP·키 설정·Graph 호출은 연결하지 않는다."""

import base64
import binascii
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from secrets import token_bytes
from typing import Any
from uuid import UUID, uuid4

import msal  # type: ignore[import-untyped]
import psycopg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from pydantic import SecretStr

from backend.api.platform.auth.callback import MicrosoftSubject
from backend.api.platform.auth.identity import IDENTITY_QUERY, identity
from backend.api.platform.auth.microsoft import (
    MailboxAuthorization,
    MailboxTokenUpdate,
    _mailbox_account,
    _mailbox_error,
)
from backend.shared.database import connection
from backend.shared.settings import Settings

_VALID_SESSION = (
    "EXISTS (SELECT 1 FROM identity.sessions WHERE user_id=%s AND token_hash=%s "
    "AND expires_at>clock_timestamp())"
)


@asynccontextmanager
async def _vault_connection(
    settings: Settings,
) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
    """보관 경계의 DB 장애를 고정된 제공 실패로 변환한다.

    Args:
        settings: 플랫폼 런타임 DB 설정.

    Returns:
        AsyncIterator: 실패 시 롤백되는 보관 트랜잭션.
    """
    try:
        async with connection(settings) as conn:
            yield conn
    except HTTPException as error:
        if error.status_code == 503:
            raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE") from None
        raise


@dataclass(frozen=True, repr=False)
class StoredMailbox:
    """인증·보관 계층 전용 스냅샷. 소비자나 브라우저에 캐시를 반환하지 않는다."""

    mailbox_id: UUID
    owner_user_id: UUID
    subject: MicrosoftSubject
    revision: int
    serialized_cache: SecretStr


class MailboxCipher:
    """단일 키 버전의 AES-256-GCM. 키 등록·자동 생성·환경 변수 로딩은 하지 않는다."""

    def __init__(self, *, key: SecretStr, key_version: str) -> None:
        """서버에서 주입한 base64 키와 비밀이 아닌 버전 식별자를 확인한다.

        Args:
            key: Secret Manager 등에서 주입한 표준 base64 형식의 32바이트 키.
            key_version: 암호문 메타데이터와 결속할 고정 키 버전 이름.

        Returns:
            None: 키 원문을 repr나 오류에 노출하지 않는 암호기를 준비한다.
        """
        try:
            raw = base64.b64decode(key.get_secret_value(), validate=True)
        except (ValueError, binascii.Error):
            raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE") from None
        if len(raw) != 32 or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_version):
            raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE")
        self._aes = AESGCM(raw)
        self.key_version = key_version

    def _aad(self, value: StoredMailbox) -> bytes:
        """암호문을 현재 연결·소유자·사서함·개정과 키 버전에 결속한다.

        Args:
            value: 저장하거나 복호화할 연결 메타데이터.

        Returns:
            bytes: 명확한 순서와 구분을 가진 인증 부가 데이터.
        """
        return json.dumps(
            [
                "team4-mailbox-v1",
                self.key_version,
                str(value.mailbox_id),
                str(value.owner_user_id),
                str(value.subject.tid),
                str(value.subject.oid),
                value.revision,
            ],
            separators=(",", ":"),
        ).encode()

    def encrypt(self, value: StoredMailbox) -> bytes:
        """검증된 캐시를 매번 새로운 96비트 nonce로 인증 암호화한다.

        Args:
            value: 서버가 검증한 연결 메타데이터와 캐시.

        Returns:
            bytes: nonce와 인증 태그가 포함된 암호문. 키는 포함하지 않는다.
        """
        cache = msal.SerializableTokenCache()
        try:
            cache.deserialize(value.serialized_cache.get_secret_value())
            _mailbox_account(cache, value.subject)
        except (ValueError, TypeError, AttributeError, KeyError):
            raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE") from None
        nonce = token_bytes(12)
        return nonce + self._aes.encrypt(
            nonce, value.serialized_cache.get_secret_value().encode(), self._aad(value)
        )

    def decrypt(self, value: StoredMailbox, *, ciphertext: bytes, key_version: str) -> SecretStr:
        """메타데이터와 키 버전이 정확히 맞는 암호문만 복호화한다.

        Args:
            value: 현재 DB에서 권한을 확인한 연결 메타데이터.
            ciphertext: nonce와 태그가 포함된 저장 암호문.
            key_version: 저장된 키 버전. 임의 키나 이전 키로 폴백하지 않는다.

        Returns:
            SecretStr: 인증 태그 검증을 통과한 서버 전용 캐시.
        """
        if key_version != self.key_version or len(ciphertext) <= 28:
            raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE")
        try:
            plaintext = self._aes.decrypt(ciphertext[:12], ciphertext[12:], self._aad(value))
            return SecretStr(plaintext.decode())
        except (InvalidTag, ValueError, UnicodeError):
            raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE") from None


async def _lock_owner(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    owner: UUID,
    *,
    session_token: str | None = None,
) -> None:
    """저장 트랜잭션 동안 현재 사용자·역할 및 필요한 세션을 보호한다.

    Args:
        conn: 플랫폼 런타임 트랜잭션.
        owner: 인증된 세션 또는 신뢰할 서버 작업에서 확인한 소유자.
        session_token: 연결·해제 시 필요한 현재 세션. 서버 작업은 별도 인증 후 생략한다.

    Returns:
        None: 사용자 행의 배타 잠금과 기존 역할·세션의 공유 잠금을 유지한다.
    """
    await conn.execute("SET LOCAL lock_timeout='3s'")
    await conn.execute("SET LOCAL statement_timeout='5s'")
    user = await conn.execute(
        # FK가 요구하는 KEY SHARE와도 충돌해 새 역할·세션 INSERT를 직렬화한다.
        "SELECT id FROM identity.users WHERE id=%s AND is_active FOR UPDATE",
        (owner,),
    )
    if await user.fetchone() is None:
        raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    roles = await conn.execute(
        "SELECT role FROM identity.user_roles WHERE user_id=%s ORDER BY role FOR SHARE", (owner,)
    )
    if not await roles.fetchall():
        raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    row = await conn.execute(IDENTITY_QUERY + "WHERE u.id=%s GROUP BY u.id", (owner,))
    try:
        identity(await row.fetchone())
    except HTTPException:
        raise _mailbox_error("MAILBOX_ACCESS_DENIED") from None
    if session_token is not None:
        session = await conn.execute(
            "SELECT token_hash FROM identity.sessions "
            "WHERE user_id=%s AND token_hash=%s AND expires_at>clock_timestamp() FOR SHARE",
            (owner, sha256(session_token.encode()).hexdigest()),
        )
        if await session.fetchone() is None:
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")


async def _owned_row(
    conn: psycopg.AsyncConnection[dict[str, Any]], mailbox_id: UUID, owner: UUID
) -> dict[str, Any]:
    """복호화 전에 연결 소유 관계와 현재 상태를 확인한다.

    Args:
        conn: 현재 사용자 잠금을 가진 트랜잭션.
        mailbox_id: 서버 연결 ID.
        owner: 신뢰할 호출 문맥에서 확인한 소유자.

    Returns:
        dict[str, Any]: 수정·해제와 직렬화되는 현재 연결 행.
    """
    cursor = await conn.execute(
        "SELECT * FROM mailbox_auth.connections WHERE id=%s AND owner_user_id=%s FOR UPDATE",
        (mailbox_id, owner),
    )
    row = await cursor.fetchone()
    if row is None:
        raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    return row


def _snapshot(row: dict[str, Any], cache: SecretStr) -> StoredMailbox:
    """현재 연결 행과 보호된 캐시를 내부 스냅샷으로 구성한다.

    Args:
        row: 소유 관계를 확인한 연결 행.
        cache: 복호화했거나 새로 인가받은 캐시.

    Returns:
        StoredMailbox: 외부 응답에 사용하지 않을 내부 결과.
    """
    return StoredMailbox(
        row["id"],
        row["owner_user_id"],
        MicrosoftSubject(row["subject_tid"], row["subject_oid"]),
        row["revision"],
        cache,
    )


async def create_mailbox(
    settings: Settings,
    *,
    session_token: str,
    authorization: MailboxAuthorization,
    cipher: MailboxCipher,
) -> StoredMailbox:
    """검증된 초기 인계를 현재 세션·권한 재검사와 함께 암호화 저장한다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        session_token: HTTP 경계에서 인증한 현재 세션. 임의 사용자 ID는 받지 않는다.
        authorization: 초기 OAuth 검증을 통과한 서버 전용 인계 결과.
        cipher: 서버 비밀 키로 구성한 암호기.

    Returns:
        StoredMailbox: 같은 소유자·같은 Microsoft 계정의 기존 연결을 재사용했거나 새로 만든
            연결 ID·개정과 서버 전용 캐시. 공개 연결 응답이 아니다.
    """
    if not session_token:
        raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    owner, subject = authorization.owner_user_id, authorization.subject
    async with _vault_connection(settings) as conn:
        # 잠금 순서는 다른 보관 함수와 같이 사용자 행 → 연결 행이다.
        await _lock_owner(conn, owner, session_token=session_token)
        # ponytail: (owner_user_id, subject) 유일 제약이 없어 같은 계정 행이 여럿이면 최근
        # 변경 하나만 재사용한다. 중복 자체를 막으려면 유일 인덱스 마이그레이션을 추가한다.
        cursor = await conn.execute(
            "SELECT id,revision FROM mailbox_auth.connections "
            "WHERE owner_user_id=%s AND subject_tid=%s AND subject_oid=%s "
            "ORDER BY updated_at DESC, id LIMIT 1 FOR UPDATE",
            (owner, subject.tid, subject.oid),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            value = StoredMailbox(
                existing["id"],
                owner,
                subject,
                existing["revision"] + 1,
                authorization.serialized_cache,
            )
            cursor = await conn.execute(
                "UPDATE mailbox_auth.connections SET revision=%s,state='connected',key_version=%s,"
                "cache_ciphertext=%s,reconnect_required_at=NULL,reconnect_reason=NULL,"
                "updated_at=clock_timestamp() "
                "WHERE id=%s AND revision=%s AND " + _VALID_SESSION + " RETURNING id",
                (
                    value.revision,
                    cipher.key_version,
                    cipher.encrypt(value),
                    value.mailbox_id,
                    existing["revision"],
                    owner,
                    sha256(session_token.encode()).hexdigest(),
                ),
            )
            if await cursor.fetchone() is None:
                raise _mailbox_error("MAILBOX_ACCESS_DENIED")
            return value
        value = StoredMailbox(uuid4(), owner, subject, 1, authorization.serialized_cache)
        ciphertext = cipher.encrypt(value)
        cursor = await conn.execute(
            "INSERT INTO mailbox_auth.connections "
            "(id,owner_user_id,subject_tid,subject_oid,revision,state,key_version,cache_ciphertext) "
            "SELECT %s,%s,%s,%s,1,'connected',%s,%s WHERE " + _VALID_SESSION + " RETURNING id",
            (
                value.mailbox_id,
                value.owner_user_id,
                value.subject.tid,
                value.subject.oid,
                cipher.key_version,
                ciphertext,
                value.owner_user_id,
                sha256(session_token.encode()).hexdigest(),
            ),
        )
        if await cursor.fetchone() is None:
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    return value


async def load_mailbox(
    settings: Settings, *, mailbox_id: UUID, owner_user_id: UUID, cipher: MailboxCipher
) -> StoredMailbox:
    """현재 소유자·유효 권한·연결 상태를 확인한 뒤에만 캐시를 복호화한다.

    호출자의 owner_user_id는 인증한 세션/저장된 작업에서 복원해야 한다.
    이 내부 함수는 공개 토큰 getter나 HTTP 요청 인증을 대신하지 않는다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        mailbox_id: 서버에 저장된 연결 ID.
        owner_user_id: 신뢰할 서버 문맥에서 확인한 소유자.
        cipher: 현재 허용한 서버 키 버전의 암호기.

    Returns:
        StoredMailbox: 현재 개정과 복호화된 보관 전용 캐시.
    """
    async with _vault_connection(settings) as conn:
        await _lock_owner(conn, owner_user_id)
        row = await _owned_row(conn, mailbox_id, owner_user_id)
        if row["state"] != "connected":
            raise _mailbox_error("MAILBOX_NOT_CONNECTED")
        # 공급자가 갱신을 거부한 연결은 제품에서 다시 연결할 때까지 MSAL을 부르지 않는다.
        if row["reconnect_required_at"] is not None:
            raise _mailbox_error("MAILBOX_RECONNECT_REQUIRED")
        value = _snapshot(row, SecretStr(""))
        cache = cipher.decrypt(
            value, ciphertext=bytes(row["cache_ciphertext"]), key_version=row["key_version"]
        )
        return replace(value, serialized_cache=cache)


async def save_mailbox_update(
    settings: Settings,
    *,
    snapshot: StoredMailbox,
    update: MailboxTokenUpdate,
    cipher: MailboxCipher,
) -> StoredMailbox:
    """현재 권한과 개정을 재검사해 오래된 갱신이 해제·재연결을 덮어쓰지 못하게 한다.

    외부 MSAL 호출은 트랜잭션 밖에서 완료해야 한다. 충돌을 자동 재시도하지 않는다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        snapshot: 토큰 조회 전에 읽은 현재 연결 스냅샷.
        update: 기존 MSAL 어댑터가 검증한 갱신 결과.
        cipher: 서버에서 주입한 현재 키 버전의 암호기.

    Returns:
        StoredMailbox: 변경 캐시의 새 개정 또는 변경 없는 현재 스냅샷.
    """
    async with _vault_connection(settings) as conn:
        await _lock_owner(conn, snapshot.owner_user_id)
        row = await _owned_row(conn, snapshot.mailbox_id, snapshot.owner_user_id)
        if row["state"] != "connected":
            raise _mailbox_error("MAILBOX_NOT_CONNECTED")
        if row["reconnect_required_at"] is not None:
            raise _mailbox_error("MAILBOX_RECONNECT_REQUIRED")
        if row["revision"] != snapshot.revision or (row["subject_tid"], row["subject_oid"]) != (
            snapshot.subject.tid,
            snapshot.subject.oid,
        ):
            raise _mailbox_error("MAILBOX_VERSION_CONFLICT")
        if not update.cache_changed:
            return snapshot
        value = replace(
            snapshot, revision=snapshot.revision + 1, serialized_cache=update.serialized_cache
        )
        await conn.execute(
            "UPDATE mailbox_auth.connections SET revision=%s,key_version=%s,cache_ciphertext=%s,"
            "updated_at=clock_timestamp() WHERE id=%s AND revision=%s",
            (
                value.revision,
                cipher.key_version,
                cipher.encrypt(value),
                value.mailbox_id,
                snapshot.revision,
            ),
        )
        return value


async def mark_reconnect_required(
    settings: Settings, *, snapshot: StoredMailbox, reason: str
) -> bool:
    """공급자가 갱신을 거부한 연결에 재연결 필요를 표시한다.

    MSAL은 트랜잭션 밖에서 실행되므로 별도의 짧은 트랜잭션으로 기록한다. 토큰을 읽은 뒤
    재연결·해제·갱신으로 개정이 바뀌었으면 표시하지 않아 새 연결을 오래된 거부로 막지 않는다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        snapshot: 토큰 조회 전에 읽은 연결 스냅샷.
        reason: 공급자 오류 이름 또는 token_missing.

    Returns:
        bool: 이번에 표시했으면 True. 이미 표시됐거나 상태·개정이 바뀌었으면 False.
    """
    async with _vault_connection(settings) as conn:
        await _lock_owner(conn, snapshot.owner_user_id)
        row = await _owned_row(conn, snapshot.mailbox_id, snapshot.owner_user_id)
        if (
            row["state"] != "connected"
            or row["revision"] != snapshot.revision
            or row["reconnect_required_at"] is not None
        ):
            return False
        await conn.execute(
            "UPDATE mailbox_auth.connections SET reconnect_required_at=clock_timestamp(),"
            "reconnect_reason=%s WHERE id=%s AND revision=%s",
            (reason, snapshot.mailbox_id, snapshot.revision),
        )
        return True


async def list_mailboxes(settings: Settings, *, owner_user_id: UUID) -> list[dict[str, Any]]:
    """소유자의 연결 상태만 조회한다. 암호문·Microsoft 식별자·키 버전은 읽지 않는다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        owner_user_id: HTTP 경계가 현재 세션으로 확인한 서비스 사용자 ID.

    Returns:
        list[dict[str, Any]]: 최근 변경 순의 mailbox_id·state·revision·updated_at과
        재연결 필요 시각·사유 행.
    """
    async with _vault_connection(settings) as conn:
        # ponytail: 상한·페이지 없이 전부 반환한다. 사용자당 연결은 소수라는 가정이며,
        # 늘어나면 limit과 (updated_at, id) 커서 페이지를 추가한다.
        cursor = await conn.execute(
            "SELECT id AS mailbox_id,state,revision,updated_at,reconnect_required_at,"
            "reconnect_reason FROM mailbox_auth.connections "
            "WHERE owner_user_id=%s ORDER BY updated_at DESC, id",
            (owner_user_id,),
        )
        return await cursor.fetchall()


async def disconnect_mailbox(settings: Settings, *, mailbox_id: UUID, session_token: str) -> None:
    """현재 세션 소유자의 암호문을 제거하고 이후 조회·오래된 갱신을 차단한다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        mailbox_id: 해제할 서버 연결 ID.
        session_token: 신뢰할 HTTP 경계에서 얻은 현재 세션.

    Returns:
        None: 암호문 제거와 개정 증가를 함께 커밋한다. 외부 토큰 즉시 철회는 아니다.
    """
    if not session_token:
        raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    async with _vault_connection(settings) as conn:
        session = await conn.execute(
            "SELECT user_id FROM identity.sessions WHERE token_hash=%s",
            (sha256(session_token.encode()).hexdigest(),),
        )
        found = await session.fetchone()
        if found is None:
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")
        await _lock_owner(conn, found["user_id"], session_token=session_token)
        row = await _owned_row(conn, mailbox_id, found["user_id"])
        if row["state"] == "disconnected":
            return
        cursor = await conn.execute(
            "UPDATE mailbox_auth.connections SET state='disconnected',cache_ciphertext=NULL,"
            "reconnect_required_at=NULL,reconnect_reason=NULL,"
            "revision=revision+1,updated_at=clock_timestamp() WHERE id=%s AND "
            + _VALID_SESSION
            + " RETURNING revision",
            (mailbox_id, found["user_id"], sha256(session_token.encode()).hexdigest()),
        )
        if await cursor.fetchone() is None:
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")


async def reconnect_mailbox(
    settings: Settings,
    *,
    mailbox_id: UUID,
    expected_revision: int,
    session_token: str,
    authorization: MailboxAuthorization,
    cipher: MailboxCipher,
) -> StoredMailbox:
    """같은 Microsoft 계정의 새 OAuth 인계만 현재 세션·개정 재검사 후 기존 연결에 저장한다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        mailbox_id: 소유자가 재연결할 서버 연결 ID.
        expected_revision: 시작 시 서버에서 확인한 연결 개정.
        session_token: 현재 유효한 소유자 세션.
        authorization: 새 인가 교환·서명 검증을 통과한 서버 전용 결과. 계정이 기존 행과
            다르면 저장 없이 `MAILBOX_ACCESS_DENIED`다.
        cipher: 서버 키 버전의 암호기.

    Returns:
        StoredMailbox: 같은 사서함 식별자·새 캐시와 증가한 개정.
    """
    if not session_token:
        raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    async with _vault_connection(settings) as conn:
        await _lock_owner(conn, authorization.owner_user_id, session_token=session_token)
        row = await _owned_row(conn, mailbox_id, authorization.owner_user_id)
        # 다른 Microsoft 계정으로 같은 mailbox_id를 바꾸면 KAN-71 수집 커서가 섞인다.
        if (row["subject_tid"], row["subject_oid"]) != (
            authorization.subject.tid,
            authorization.subject.oid,
        ):
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")
        if row["revision"] != expected_revision:
            raise _mailbox_error("MAILBOX_VERSION_CONFLICT")
        value = StoredMailbox(
            mailbox_id,
            authorization.owner_user_id,
            authorization.subject,
            expected_revision + 1,
            authorization.serialized_cache,
        )
        cursor = await conn.execute(
            "UPDATE mailbox_auth.connections SET revision=%s,state='connected',key_version=%s,"
            "cache_ciphertext=%s,reconnect_required_at=NULL,reconnect_reason=NULL,"
            "updated_at=clock_timestamp() "
            "WHERE id=%s AND revision=%s AND " + _VALID_SESSION + " RETURNING revision",
            (
                value.revision,
                cipher.key_version,
                cipher.encrypt(value),
                mailbox_id,
                expected_revision,
                value.owner_user_id,
                sha256(session_token.encode()).hexdigest(),
            ),
        )
        if await cursor.fetchone() is None:
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")
        return value
