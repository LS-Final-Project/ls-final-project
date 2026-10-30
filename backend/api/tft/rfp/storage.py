"""RFP 원문 읽기(KAN-199·KAN-188). local/test는 `TFT_DOCUMENT_ROOT` 아래 `storage_key` 파일을 읽는다.

GCS 읽기는 업로드 경로(6, KAN-188)를 구현할 때 같은 함수 뒤에 붙인다. 읽은 바이트는 판본 행의 sha256·size와
맞아야 한다(다른 파일을 처리하지 않게).
"""

import hashlib
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class DocumentStorageSettings(BaseSettings):
    """원문 폴더. 처리 함수와 같은 `TFT_DOCUMENT_ROOT`다. 없으면 원문을 내려받을 수 없다."""

    model_config = SettingsConfigDict(env_prefix="TFT_", env_file=".env", extra="ignore")
    document_root: Path | None = None


def read_document(root: Path, storage_key: str, sha256: str, size: int) -> bytes:
    """저장 키의 원문을 읽고 해시·크기를 확인한다.

    Args:
        root: 원문 폴더.
        storage_key: 판본 행의 저장 키(폴더 안 상대 경로).
        sha256: 판본 행의 SHA-256.
        size: 판본 행의 바이트 수.

    Returns:
        bytes: 원문. 폴더 밖 경로·없는 파일·해시나 크기가 다르면 ValueError다.
    """
    base = root.resolve()
    path = (base / storage_key).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError("TFT_DOCUMENT_NOT_READABLE")
    data = path.read_bytes()
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("TFT_DOCUMENT_MISMATCH")
    return data
