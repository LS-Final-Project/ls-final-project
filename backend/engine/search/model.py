"""섹션 청크 표현(KAN-99)과 하이브리드 검색 후보·결과(KAN-100). 색인 저장 형식은 서비스별 마이그레이션이 정한다."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.documents.model import Flag, Position

ChunkKind = Literal["TEXT", "TABLE"]
Source = Literal["FTS", "VECTOR"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChunkMetadata(Strict):
    """권한·제품·판본 메타데이터 자리. 청킹은 값을 추정하지 않고 호출자가 채운다."""

    document_kind: str | None = None
    product_family: str | None = None
    access_scope: str | None = None
    version: str | None = None
    title: str | None = None
    product_code: str | None = None


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


class SearchFilters(Strict):
    """두 검색기에 똑같이 넘기는 조건. 결합 결과에도 입력 그대로 남긴다."""

    document_sha256s: list[str] | None
    index_version: str
    limit: int = Field(default=20, ge=1, le=100)
    # 청크 메타데이터 product_family로 좁힌다(C/S 검색기만 쓰고 TFT 검색기는 무시한다). None이면 전체다.
    product_family: str | None = None
    # 청크 메타데이터 document_kind(doc_index doc_type: POL·FAQ·DS…)로 좁힌다(C/S 검색기만 쓴다). None이면 전체다.
    document_kinds: list[str] | None = None


class Candidate(Strict):
    """검색기 하나가 낸 후보. position은 그 검색기 안의 순위(1부터)다."""

    chunk_id: str
    document_sha256: str
    ordinal: int
    section_path: list[str]
    snippet: str
    text_excerpt: str | None
    kind: ChunkKind
    table_title: str | None
    page_start: int | None
    source: Source
    position: int = Field(ge=1)
    score: float | None


class Fused(Strict):
    """중복 제거·RRF를 거친 후보. candidate는 먼저 나온 검색기(FTS 우선)의 것이다."""

    candidate: Candidate
    rrf_score: float
    sources: list[Source]
    exact_code_match: bool


class HybridResult(Strict):
    query: str
    filters: SearchFilters
    items: list[Fused]
    dropped_blocked: list[str]
    codes_in_query: list[str]
