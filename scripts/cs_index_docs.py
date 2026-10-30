"""C/S 문서 색인 적재(KAN-99 3차). 문서 디렉터리를 추출 → 청킹 → cs.document_chunks 적재까지 한 명령으로 한다.

로컬 docker DB 전용이다. DATABASE_URL(cs_app)만 쓰고 공용 DB에는 적재하지 않는다. DSN 호스트가 localhost·
127.0.0.1·::1·db(compose 서비스 이름)·유닉스 소켓이 아니면 --allow-remote 없이는 거부한다. 기본은 --dry-run
(추출·청킹만 하고 문서별 블록·청크 수·실패 사유를 출력)이며 --execute일 때만 적재한다.
실행: uv run --frozen python -m scripts.cs_index_docs --docs <디렉터리> --data-origin demo [--doc-index <csv>].

--docs 아래를 재귀로 훑어 .md·.docx·.pdf만(숨김 파일·폴더 제외) 읽는다. 입력 디렉터리는 수정하지 않는다. 실패 문서는
건너뛰고 사유 코드만 남긴다(원문·예외 메시지 미출력). 같은 sha256은 store_chunks 규칙대로 지우고 다시 넣는다.
차단 코드(hybrid.BLOCKED_CODES, 예 FLT-042)를 말하는 청크도 적재하며(차단은 검색 결합 경계에서 한다)
manifest에 blocked_code_chunks 수를 낸다. --manifest-out에는 문서별 source_name·sha256·size·블록·청크 수·적재
시각·사유만 쓰고 본문은 쓰지 않는다.

--doc-index(문서 묶음의 doc_index.csv)가 있으면 path 열로 문서를 찾아 ChunkMetadata를 채운다. doc_type은
document_kind, family는 product_family, revision은 version이다(product_code는 맞는 필드가 없어 쓰지 않는다).
문서의 --docs 기준 상대 경로가 path의 2단계 이상 꼬리와 같으면(예 path docs/A/x.md ↔ A/x.md) 그 행을 쓴다.
파일 이름만 같은 경우와 꼬리가 여러 행에 걸리는 경우는 쓰지 않는다. 목록에 없는 문서는 --document-kind 전역값을 쓴다.
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from backend.api.cs.search.repository import missing_embeddings, store_chunks, store_embeddings
from backend.engine.documents.model import ExtractedDocument
from backend.engine.documents.router import extract
from backend.engine.search.chunking import INDEX_VERSION, chunk_document
from backend.engine.search.hybrid import BLOCKED_CODES, mentions_code
from backend.engine.search.model import ChunkingResult, ChunkMetadata

SUFFIXES = (".md", ".docx", ".pdf")
DOC_INDEX_COLUMNS = {"doc_id", "doc_type", "family", "revision", "path"}
LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1", "db"})
# 운영자 Cloud SQL Proxy 관례 포트. 로컬 주소여도 공용 DB로 이어지므로 원격으로 본다.
PROXY_PORTS = frozenset({"55434"})
Store = Callable[[str, ChunkingResult, ChunkMetadata, str, datetime], int]
# 청크 문자열 하나를 임베딩 값으로 바꾸는 함수(--embed). 기본은 ai.embed(RETRIEVAL_DOCUMENT, 768차원)다.
Embedder = Callable[[str], list[float]]
EMBED_BATCH = 64


class EmbedSetupFailed(Exception):
    """임베딩 클라이언트를 만들지 못했다(키·설정). 원문·키 없이 CS_INDEX_EMBED_FAILED로만 알린다."""


def find_documents(root: Path) -> list[Path]:
    """색인할 문서 경로를 정렬해 고른다.

    Args:
        root: --docs 디렉터리.

    Returns:
        list[Path]: .md·.docx·.pdf 파일(대소문자 무시). 이름이 '.'로 시작하는 파일·폴더 아래는 뺀다.
    """
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in SUFFIXES
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )


def load_doc_index(csv_path: Path) -> dict[tuple[str, ...], dict[str, str | None]]:
    """doc_index.csv를 경로 꼬리 → 메타데이터로 읽는다.

    Args:
        csv_path: doc_index.csv 경로.

    Returns:
        dict: path 열의 2단계 이상 꼬리(예 docs/A/x.md → (docs, A, x.md)·(A, x.md)) → {doc_id, document_kind,
            product_family, version}. 두 행 이상이 같은 꼬리를 가지면 그 꼬리는 뺀다(모호). 빈 칸은 None이다.
            필수 열(DOC_INDEX_COLUMNS)이 없거나 path가 비거나 겹치면 CS_INDEX_DOC_INDEX_INVALID ValueError다.
    """
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not DOC_INDEX_COLUMNS <= set(reader.fieldnames or ()):
            raise ValueError("CS_INDEX_DOC_INDEX_INVALID")
        rows = list(reader)
    paths = [PurePosixPath((row.get("path") or "").strip().replace("\\", "/")) for row in rows]
    if not all(path.parts for path in paths) or len(set(paths)) != len(paths):
        raise ValueError("CS_INDEX_DOC_INDEX_INVALID")
    index: dict[tuple[str, ...], dict[str, str | None]] = {}
    ambiguous: set[tuple[str, ...]] = set()
    for row, path in zip(rows, paths, strict=True):
        value = {
            key: (row.get(column) or "").strip() or None
            for key, column in (
                ("doc_id", "doc_id"),
                ("document_kind", "doc_type"),
                ("product_family", "family"),
                ("version", "revision"),
            )
        }
        # 파일 이름만 같은 다른 문서가 걸리지 않게 2단계 이상 꼬리만 쓴다(path가 한 단계면 그대로).
        for start in range(max(len(path.parts) - 1, 1)):
            tail = path.parts[start:]
            if tail in index:
                ambiguous.add(tail)
            index[tail] = value
    return {tail: value for tail, value in index.items() if tail not in ambiguous}


def is_local(dsn: str) -> bool:
    """DSN이 로컬 DB를 가리키는지 본다.

    Args:
        dsn: 연결 문자열.

    Returns:
        bool: 모든 호스트가 LOCAL_HOSTS이거나 유닉스 소켓(/로 시작)이고 hostaddr가 비었거나 루프백이면 True.
            DSN에 host·hostaddr가 없으면 libpq처럼 PGHOST·PGHOSTADDR를 본다. service(PGSERVICE)는 주소를 알 수
            없어 False, 포트(없으면 PGPORT)가 PROXY_PORTS면 False, 해석할 수 없어도 False다.
    """
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        return False
    if params.get("service") or os.environ.get("PGSERVICE"):
        return False
    hosts = str(params.get("host") or os.environ.get("PGHOST") or "").split(",")
    addrs = str(params.get("hostaddr") or os.environ.get("PGHOSTADDR") or "").split(",")
    ports = str(params.get("port") or os.environ.get("PGPORT") or "").split(",")
    if any(port.strip() in PROXY_PORTS for port in ports):
        return False
    return all(host in LOCAL_HOSTS or host.startswith("/") for host in hosts) and all(
        addr in ("", "127.0.0.1", "::1") for addr in addrs
    )


def store_with_dsn(dsn: str) -> Store:
    """cs_app DSN으로 store_chunks를 부르는 동기 함수를 만든다.

    Args:
        dsn: cs_app 연결 문자열.

    Returns:
        Store: (sha256 확인용 결과, 메타데이터, data_origin, known_at)으로 한 문서를 적재하고 행 수를 돌려준다.
    """

    def store(
        _dsn: str, result: ChunkingResult, metadata: ChunkMetadata, origin: str, known_at: datetime
    ) -> int:
        """한 문서의 청크를 적재한다.

        Args:
            _dsn: 쓰지 않는다(시그니처를 주입 함수와 맞춘다).
            result: 청킹 결과.
            metadata: 청크 메타데이터.
            origin: data_origin.
            known_at: 알게 된 시각.

        Returns:
            int: 저장 행 수.
        """

        async def run() -> int:
            """연결 하나로 적재한다.

            Returns:
                int: 저장 행 수.
            """
            async with await psycopg.AsyncConnection.connect(
                dsn, row_factory=dict_row, autocommit=True
            ) as conn:
                return await store_chunks(
                    conn,
                    result,
                    document_sha256=result.chunks[0].document_sha256,
                    metadata=metadata,
                    data_origin=origin,
                    known_at=known_at,
                )

        return asyncio.run(run())

    return store


def default_embedder(model: str, dimensions: int, task_type: str) -> Embedder:
    """Gemini 임베딩 호출 함수를 만든다(키는 환경변수 GEMINI_API_KEY에서만).

    Args:
        model: 임베딩 모델 ID.
        dimensions: 출력 차원.
        task_type: 문서 쪽 task_type.

    Returns:
        Embedder: 문자열 하나당 API 1회.
    """
    from backend.shared.ai import AISettings, create_client, embed

    client = create_client(AISettings(), embedding=True)
    return lambda text: (
        embed(client, model, text, dimensions=dimensions, task_type=task_type).values
    )


def embed_chunks(
    dsn: str,
    *,
    index_version: str,
    embedding_version: str,
    model: str,
    embedder: Callable[[], Embedder],
) -> dict[str, Any]:
    """그 판본의 임베딩이 없는 청크만 임베딩해 넣는다(KAN-99, C20). 첫 공급자 오류에서 멈추고 다음 실행이 잇는다.

    Args:
        dsn: cs_app 로컬 DSN.
        index_version: 청크 색인 판본.
        embedding_version: 모델:차원:task_type.
        model: 단가 조회용 모델 ID.
        embedder: 청크 문자열 → 임베딩 값 함수를 만드는 함수. 대상 청크가 있을 때만 한 번 부른다(지연 생성).

    Returns:
        dict: embedding_version·missing(시작 시 대상 수)·embedded(넣은 수)·calls·chars·reason(멈춘 사유 코드,
            없으면 None)·approx_input_cost(문자/4 × ai_prices 단가, 단가가 없으면 None). 원문·벡터는 넣지 않는다.
            클라이언트를 만들지 못하면 EmbedSetupFailed다.
    """
    from backend.shared.ai import load_price_table

    async def connect() -> psycopg.AsyncConnection[dict[str, Any]]:
        return await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row, autocommit=True)

    async def load() -> list[dict[str, Any]]:
        async with await connect() as conn:
            return await missing_embeddings(
                conn, index_version=index_version, embedding_version=embedding_version
            )

    async def save(batch: list[tuple[Any, list[float]]]) -> int:
        async with await connect() as conn:
            return await store_embeddings(conn, batch, embedding_version=embedding_version)

    rows = asyncio.run(load())
    summary: dict[str, Any] = {
        "embedding_version": embedding_version,
        "missing": len(rows),
        "embedded": 0,
        "calls": 0,
        "chars": 0,
        "reason": None,
    }
    batch: list[tuple[Any, list[float]]] = []
    call: Embedder | None = None
    if rows:
        try:
            call = embedder()
        except Exception:
            raise EmbedSetupFailed from None
    for row in rows:
        assert call is not None
        try:
            values = call(row["text"])
        except Exception:
            # 공급자 원문은 남기지 않는다. 이미 넣은 행은 유지되고 다음 실행이 남은 청크부터 잇는다.
            summary["reason"] = "CS_EMBED_PROVIDER_ERROR"
            break
        summary["calls"] += 1
        summary["chars"] += len(row["text"])
        batch.append((row["id"], values))
        if len(batch) == EMBED_BATCH:
            summary["embedded"] += asyncio.run(save(batch))
            batch = []
    if batch:
        summary["embedded"] += asyncio.run(save(batch))
    price = load_price_table().get(model)
    summary["approx_input_cost"] = (
        None
        if price is None
        else str(Decimal(summary["chars"]) / 4 * price.input_per_million / Decimal(1_000_000))
    )
    return summary


def index_documents(
    root: Path,
    *,
    execute: bool,
    dsn: str | None,
    data_origin: str,
    document_kind: str | None,
    doc_index: dict[tuple[str, ...], dict[str, str | None]] | None = None,
    extractor: Callable[[Path], ExtractedDocument] = extract,
    chunker: Callable[[ExtractedDocument], ChunkingResult] = chunk_document,
    store: Store | None = None,
) -> list[dict[str, Any]]:
    """문서마다 추출·청킹하고 --execute면 적재한다. 실패는 사유 코드만 남기고 건너뛴다.

    Args:
        root: 문서 디렉터리.
        execute: True면 적재한다.
        dsn: 적재에 쓸 DSN(execute일 때만).
        data_origin: synthetic·demo·real.
        document_kind: 청크 메타데이터의 문서 종류(선택). doc_index에 없는 문서에 쓴다.
        doc_index: load_doc_index 결과(선택).
        extractor: 추출 함수(검사에서 바꾼다).
        chunker: 청킹 함수.
        store: 적재 함수. None이면 store_with_dsn(dsn)이다.

    Returns:
        list[dict]: 문서별 source_name(상대 경로)·doc_id(doc_index에 있을 때)·sha256·size·blocks·chunks·blocked_code_chunks·stored·stored_at·
            reason. 본문·예외 메시지는 없다.
    """
    saver = store or (store_with_dsn(dsn) if execute and dsn else None)
    rows = []
    for path in find_documents(root):
        entry = (doc_index or {}).get(path.relative_to(root).parts)
        row: dict[str, Any] = {
            "source_name": str(path.relative_to(root)),
            "doc_id": entry["doc_id"] if entry else None,
            "sha256": None,
            "size": None,
            "blocks": None,
            "chunks": None,
            "blocked_code_chunks": None,
            "stored": None,
            "stored_at": None,
            "reason": None,
        }
        rows.append(row)
        try:
            document = extractor(path)
        except ValueError:
            row["reason"] = "CS_INDEX_EXTRACT_REJECTED"
            continue
        except Exception:
            row["reason"] = "CS_INDEX_EXTRACT_FAILED"
            continue
        row.update(sha256=document.sha256, size=document.size, blocks=len(document.blocks))
        try:
            result = chunker(document)
        except Exception:
            row["reason"] = "CS_INDEX_CHUNK_FAILED"
            continue
        row["chunks"] = len(result.chunks)
        row["blocked_code_chunks"] = sum(
            any(mentions_code(chunk.text, code) for code in BLOCKED_CODES)
            for chunk in result.chunks
        )
        if not result.chunks:
            row["reason"] = "CS_INDEX_NO_CHUNKS"
            continue
        if not execute or saver is None:
            continue
        known_at = datetime.now(UTC)
        try:
            row["stored"] = saver(
                dsn or "",
                result,
                ChunkMetadata(
                    document_kind=entry["document_kind"],
                    product_family=entry["product_family"],
                    version=entry["version"],
                )
                if entry
                else ChunkMetadata(document_kind=document_kind),
                data_origin,
                known_at,
            )
        except Exception:
            row["reason"] = "CS_INDEX_STORE_FAILED"
            continue
        row["stored_at"] = known_at.isoformat()
    return rows


def main(
    argv: list[str] | None = None, store: Store | None = None, embedder: Embedder | None = None
) -> int:
    """인자를 읽어 dry-run 표를 출력하거나 --execute로 적재하고 manifest를 쓴다.

    --embed는 적재 뒤(또는 --execute 없이 단독으로) 임베딩이 없는 청크만 임베딩한다. 단독이면 문서를 다시
    적재하지 않는다(재적재는 청크 행을 바꿔 임베딩이 CASCADE로 지워진다).

    Args:
        argv: 명령행 인자. None이면 sys.argv.
        store: 적재 함수(검사에서 바꾼다).
        embedder: 임베딩 함수(검사에서 바꾼다). None이면 Gemini를 부른다.

    Returns:
        int: 성공 0(문서별 실패는 manifest의 reason), 인자·DB 경계 위반 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=Path, required=True, help="문서 디렉터리(재귀, 읽기 전용)")
    parser.add_argument("--index-version", default=INDEX_VERSION)
    parser.add_argument("--data-origin", choices=("synthetic", "demo", "real"), required=True)
    parser.add_argument("--document-kind", help="doc_index에 없는 문서의 document_kind")
    parser.add_argument("--doc-index", type=Path, help="문서 묶음의 doc_index.csv(선택)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="기본값. 추출·청킹만")
    mode.add_argument("--execute", action="store_true", help="로컬 DB(DATABASE_URL)에 적재")
    parser.add_argument(
        "--allow-remote", action="store_true", help="로컬이 아닌 DSN 허용(공용 DB 금지)"
    )
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument(
        "--embed", action="store_true", help="임베딩이 없는 청크만 임베딩(로컬 DB, 실제 호출)"
    )
    parser.add_argument("--embed-model", default="gemini-embedding-2")
    parser.add_argument("--embed-dimension", type=int, default=768, choices=(768,))
    parser.add_argument("--embed-task-type", default="RETRIEVAL_DOCUMENT")
    args = parser.parse_args(argv)
    if args.embed and args.dry_run:
        print("CS_INDEX_EMBED_NEEDS_DB: --embed는 --dry-run과 함께 쓰지 않는다.", file=sys.stderr)
        return 1
    if not args.docs.is_dir():
        print("CS_INDEX_DOCS_MISSING: --docs는 디렉터리여야 한다.", file=sys.stderr)
        return 1
    if args.index_version != INDEX_VERSION:
        # 청킹 판본은 chunking.INDEX_VERSION으로 정해지므로 다른 값으로는 적재하지 않는다.
        print(
            "CS_INDEX_VERSION_MISMATCH: --index-version은 청킹 판본과 같아야 한다.", file=sys.stderr
        )
        return 1
    doc_index = None
    if args.doc_index:
        try:
            doc_index = load_doc_index(args.doc_index)
        except (OSError, UnicodeDecodeError, csv.Error, ValueError):
            print("CS_INDEX_DOC_INDEX_INVALID: --doc-index를 읽을 수 없다.", file=sys.stderr)
            return 1
    dsn = os.environ.get("DATABASE_URL")
    if (args.execute and store is None) or args.embed:
        if not dsn:
            print(
                "CS_INDEX_NEEDS_DB: --execute·--embed는 DATABASE_URL(cs_app, 로컬 DB)이 필요하다.",
                file=sys.stderr,
            )
            return 1
        if not is_local(dsn) and not args.allow_remote:
            print(
                "CS_INDEX_REMOTE_DB: 로컬 DB가 아니다. 공용 DB에는 적재하지 않는다.",
                file=sys.stderr,
            )
            return 1
    # --embed 단독이면 문서를 다시 적재하지 않는다.
    rows = (
        []
        if args.embed and not args.execute
        else index_documents(
            args.docs,
            execute=args.execute,
            dsn=dsn,
            data_origin=args.data_origin,
            document_kind=args.document_kind,
            doc_index=doc_index,
            store=store,
        )
    )
    manifest: dict[str, Any] = {
        "mode": "execute" if args.execute else "embed" if args.embed else "dry-run",
        "index_version": INDEX_VERSION,
        "documents": rows,
    }
    if args.embed:
        assert dsn is not None
        version = f"{args.embed_model}:{args.embed_dimension}:{args.embed_task_type}"
        try:
            manifest["embedding"] = embed_chunks(
                dsn,
                index_version=INDEX_VERSION,
                embedding_version=version,
                model=args.embed_model,
                embedder=(lambda: embedder)
                if embedder
                else lambda: default_embedder(
                    args.embed_model, args.embed_dimension, args.embed_task_type
                ),
            )
        except (psycopg.Error, ValueError, EmbedSetupFailed):
            print("CS_INDEX_EMBED_FAILED: DB·임베딩 키·설정을 확인한다.", file=sys.stderr)
            return 1
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.manifest_out:
        args.manifest_out.write_text(text + "\n", encoding="utf-8")
    for row in rows:
        print(
            f"{row['source_name']}\tblocks={row['blocks']}\tchunks={row['chunks']}"
            f"\tblocked={row['blocked_code_chunks']}\tstored={row['stored']}\treason={row['reason']}"
        )
    if "embedding" in manifest:
        print(json.dumps(manifest["embedding"], ensure_ascii=False))
    return 1 if manifest.get("embedding", {}).get("reason") else 0


if __name__ == "__main__":
    sys.exit(main())
