"""Immutable diagram candidates bound to an exact, already authorized document version.

The caller owns the business transaction and graph-schema validation. This module never
commits, modifies original documents, or promotes candidates to confirmed connections.
"""

import json
import re
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

Row = dict[str, Any]
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def validate_payload(payload: dict[str, Any]) -> None:
    """Reject malformed/oversized manifest envelopes before touching the database."""
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or not isinstance(payload.get("items"), list)
        or len(payload["items"]) > 16
        or any(not isinstance(item, dict) for item in payload["items"])
        or not isinstance(payload.get("warnings"), list)
        or len(payload["warnings"]) > 512
        or any(
            not isinstance(warning, str) or len(warning) > 2000 for warning in payload["warnings"]
        )
    ):
        raise ValueError("TFT_DIAGRAM_MANIFEST_INVALID")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("TFT_DIAGRAM_MANIFEST_INVALID") from None
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("TFT_DIAGRAM_MANIFEST_TOO_LARGE")


async def read_diagrams(conn: AsyncConnection[Row], document_version_id: UUID) -> Row | None:
    """Read one version; project/document access must already have been checked by the caller."""
    cursor = await conn.execute(
        "SELECT document_version_id,source_sha256,payload,created_at "
        "FROM tft.document_diagrams WHERE document_version_id=%s",
        (document_version_id,),
    )
    return await cursor.fetchone()


async def store_diagrams(
    conn: AsyncConnection[Row],
    document_version_id: UUID,
    source_sha256: str,
    payload: dict[str, Any],
) -> bool:
    """Append once in the caller's transaction; identical replay skips, differing replay fails.

    Returns True for a new row and False for an identical existing manifest. A missing
    version or mismatched original SHA is never inserted. No UPDATE/DELETE/commit occurs.
    """
    if not isinstance(document_version_id, UUID) or not isinstance(source_sha256, str):
        raise ValueError("TFT_DIAGRAM_SOURCE_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("TFT_DIAGRAM_SOURCE_INVALID")
    validate_payload(payload)
    cursor = await conn.execute(
        "INSERT INTO tft.document_diagrams (document_version_id,source_sha256,payload) "
        "SELECT id,sha256,%s FROM tft.document_versions WHERE id=%s AND sha256=%s "
        "ON CONFLICT (document_version_id) DO NOTHING RETURNING document_version_id",
        (Jsonb(payload), document_version_id, source_sha256),
    )
    if await cursor.fetchone() is not None:
        return True
    existing = await read_diagrams(conn, document_version_id)
    if existing is None:
        raise ValueError("TFT_DIAGRAM_SOURCE_MISMATCH")
    if existing["source_sha256"] != source_sha256 or existing["payload"] != payload:
        raise ValueError("TFT_DIAGRAM_MANIFEST_CONFLICT")
    return False
