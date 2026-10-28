"""섹션 청크 표현(KAN-99). 색인 저장 형식은 search 스키마가 정해진 뒤 따로 정한다."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.engine.documents.model import Flag, Position

ChunkKind = Literal["TEXT", "TABLE"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChunkMetadata(Strict):
    """권한·제품·판본 메타데이터 자리. 청킹은 값을 추정하지 않고 호출자가 채운다."""

    document_kind: str | None = None
    product_family: str | None = None
    access_scope: str | None = None
    version: str | None = None


class Chunk(Strict):
    """검색 단위 하나. text는 현행 텍스트와 각주만 담고 삭제 값은 담지 않는다."""

    chunk_id: str
    document_sha256: str
    ordinal: int
    index_version: str
    section_path: list[str]
    block_ids: list[str]
    source_position: Position
    text: str
    char_count: int
    page_start: int | None
    page_end: int | None
    kind: ChunkKind
    table_title: str | None
    has_deleted_values: bool
    flags: list[Flag]
    metadata: ChunkMetadata = ChunkMetadata()


class ChunkingResult(Strict):
    chunks: list[Chunk]
    skipped_block_ids: list[str]
    index_version: str
