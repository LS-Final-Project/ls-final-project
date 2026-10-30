"""초안 실측용 근거 스니펫 생성기(KAN-144). 문의마다 cs.document_chunks를 FTS로 찾아 --evidence 파일을 만든다.

출력(--out)은 scripts/cs_draft_run.py --evidence 형식({ticket_id: [EvidenceSnippet dict]})이고, 근거가 없거나
입력 정제에 실패한 문의는 빈 배열이다. 질의는 build_input으로 마스킹한 subject·body에서 단어 토큰(2자 이상,
마스킹 표식 제외, 중복 제거, 앞 40개)을 뽑아 OR로 잇는다. search_fts가 websearch_to_tsquery라 본문을 통째로
넣으면 모든 어휘의 AND가 되어 거의 맞지 않기 때문이다. 검색은 backend.agent.cs_search.fts_searcher_from_dsn과
hybrid.run_hybrid(FTS만)를 그대로 쓰므로 FLT-042 인용 차단 청크는 결과에서 빠진다. 스니펫은 ts_headline(<b> 태그
포함) 대신 청크 본문 앞부분이다.

DB 연결 문자열은 환경변수 DATABASE_URL(cs_app, 로컬 docker)에서만 읽고 출력하지 않는다. 결과에는 질의 원문을
넣지 않는다. 표준 출력(--meta-out이 있으면 그 파일에도)의 meta에는 건수·판본·시각·git sha와 정렬한 (ticket_id, 질의)
전체의 sha256 하나만 낸다. cs_draft_run --evidence가 최상위 키를 ticket_id로 읽으므로 meta는 근거 파일에 넣지 않는다.

제품 우선(KAN-144 11차): --index-manifest(cs_index_docs --manifest-out)가 있으면 문의의 제품 코드(--product-column)가
source_name 경로 조각과 정확히 같거나 파일 이름(확장자 제외)이 '-<제품코드>'로 끝나는 문서를 그 제품 문서로 본다.
필터가 켜져 있으면(기본, --no-product-filter로 끔) 제품 문서로 제한한 검색을 먼저 하고, 모자라면 전역 검색으로
채운다(중복 chunk_id 제외). 제품 문서가 없는 문의는 전역만 쓴다. --document-sha256s를 함께 주면 두 검색 모두 그
범위 안이다. meta의 product_hits·product_top1은 최종 근거 기준이라 필터를 끈 실행과 전후를 비교할 수 있다.

벡터(KAN-99/100): --vector면 cs_search.VectorSearcher(CsVectorSettings, GEMINI_API_KEY)를 FTS와 RRF로 함께 쓴다(정책 예약
단계 제외). 벡터에는 토큰 OR이 아니라 마스킹 원문(subject·body)을 넘긴다. 질의 임베딩은 문의당 1회(실제 API 호출)이고,
임베딩·DB 오류 단계는 FTS만 쓴 채 meta vector.failures로 센다.
"""

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import psycopg

from backend.agent.cs_search import (
    CsVectorSettings,
    fts_searcher_from_dsn,
    vector_searcher_from_settings,
)
from backend.engine.cs.classification import build_input
from backend.engine.cs.drafting import EvidenceSnippet
from backend.engine.cs.graph import policy_slot
from backend.engine.search.hybrid import Searcher, excerpt, scoped_then_global, token_or_query
from backend.engine.search.model import Candidate, SearchFilters
from scripts.cs_candidate_run import SplitBlocked, git_sha, load_targets, write_private
from tests.cs.cs_dev_data import load_dev_tickets

# 검색기에서 FLT-042 차단분만큼 모자라지 않도록 더 받아 앞 limit개를 쓴다.
SEARCH_MARGIN = 10
QuerySource = Literal["subject+body", "subject", "body"]


def query_terms(subject: str | None, body: str, source: QuerySource) -> str:
    """마스킹한 subject·body에서 OR 질의를 만든다.

    Args:
        subject: 마스킹한 제목(없으면 None).
        body: 마스킹한 본문.
        source: 질의에 쓸 필드.

    Returns:
        str: 2자 이상 단어 토큰(마스킹 표식·'or' 제외, casefold 기준 중복 제거, 앞 40개)을 ' or '로 이은
            websearch 질의. 토큰이 없으면 빈 문자열이다.
    """
    return token_or_query(*query_parts(subject, body, source))


def query_parts(subject: str | None, body: str, source: QuerySource) -> list[str]:
    """질의에 쓸 필드 값(빈 값 제외). 토큰 질의와 코드 일치 원문이 같은 필드를 쓴다.

    Args:
        subject: 마스킹한 제목.
        body: 마스킹한 본문.
        source: 질의에 쓸 필드.

    Returns:
        list[str]: 고른 필드 값.
    """
    parts = ([subject] if source != "body" else []) + ([body] if source != "subject" else [])
    return [part for part in parts if part]


def load_ticket_ids(path: Path) -> list[str]:
    """대상 ID를 읽는다. JSON 배열, JSON 객체(라벨 파일의 키), 줄 단위 텍스트를 받는다.

    Args:
        path: ID 파일.

    Returns:
        list[str]: 파일 순서의 ID. 비었거나 문자열이 아니거나 중복이면 ValueError다.
    """
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, list | dict):
        data = [line.strip() for line in text.splitlines() if line.strip()]
    ids = list(data)
    if not ids or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("CS_EVIDENCE_TICKET_IDS")
    return ids


def load_categories(path: Path) -> dict[str, object]:
    """분류 결과 라벨 JSON에서 문의별 category_code를 읽는다.

    Args:
        path: {ticket_id: {category_code, ...} | null} 라벨 파일.

    Returns:
        dict: 문의별 category_code(라벨이 없거나 객체가 아니면 None). 최상위가 객체가 아니면 ValueError다.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("CS_EVIDENCE_CLASSIFICATION")
    return {
        key: value.get("category_code") if isinstance(value, dict) else None
        for key, value in data.items()
    }


def load_policy_documents(path: Path) -> dict[str, str]:
    """색인 manifest에서 적재된 정책 문서의 doc_id(POL-*) → sha256을 읽는다.

    Args:
        path: cs_index_docs --manifest-out JSON(doc_id 열 포함).

    Returns:
        dict[str, str]: reason 없이 적재된 POL-* 문서. 형식이 틀리면 CS_EVIDENCE_INDEX_MANIFEST ValueError다.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("CS_EVIDENCE_INDEX_MANIFEST")
    return {
        row["doc_id"]: row["sha256"]
        for row in rows
        if str(row.get("doc_id") or "").startswith("POL-")
        and isinstance(row.get("sha256"), str)
        and not row.get("reason")
    }


def is_product_document(source_name: str, product_code: str) -> bool:
    """색인 문서가 그 제품의 문서인지 경로 조각으로 판정한다.

    Args:
        source_name: 색인 manifest의 --docs 기준 상대 경로.
        product_code: 문의의 제품 코드.

    Returns:
        bool: 경로 조각 하나가 제품 코드와 같거나 파일 이름(확장자 제외)이 '-<제품코드>'로 끝나면 True. 이웃
            코드(예 MCCB-ABS-103c와 403c)를 섞지 않도록 부분 문자열로는 판정하지 않는다.
    """
    path = PurePosixPath(source_name.replace("\\", "/"))
    return bool(product_code) and (
        product_code in path.parts[:-1]
        or path.stem == product_code
        or path.stem.endswith("-" + product_code)
    )


def load_product_documents(path: Path) -> dict[str, str]:
    """색인 manifest에서 적재된 문서의 source_name → sha256을 읽는다.

    Args:
        path: cs_index_docs --manifest-out JSON.

    Returns:
        dict[str, str]: sha256이 있고 reason이 없는 문서만. 형식이 틀리면 CS_EVIDENCE_INDEX_MANIFEST ValueError다.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) and isinstance(row.get("source_name"), str) for row in rows
    ):
        raise ValueError("CS_EVIDENCE_INDEX_MANIFEST")
    return {
        row["source_name"]: row["sha256"]
        for row in rows
        if isinstance(row.get("sha256"), str) and row["sha256"] and not row.get("reason")
    }


def by_text(vector: Searcher, text: str, _query: str, filters: SearchFilters) -> list[Candidate]:
    """벡터 검색기에 FTS용 토큰 OR 대신 마스킹 원문을 넘긴다(partial로 원문을 묶어 Searcher로 쓴다).

    Args:
        vector: 벡터 검색기.
        text: 마스킹 원문.
        _query: scoped_then_global이 넘기는 토큰 OR 질의(쓰지 않음).
        filters: 단계 조건.

    Returns:
        list[Candidate]: 벡터 후보.
    """
    return vector(text, filters)


def build_evidence(
    records: Mapping[str, Mapping[str, object]],
    searcher: Searcher,
    *,
    index_version: str,
    document_sha256s: list[str] | None,
    limit: int,
    snippet_chars: int,
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
    query_from: QuerySource,
    product_documents: Mapping[str, frozenset[str]] | None = None,
    product_filter: bool = True,
    categories: Mapping[str, object] | None = None,
    policy_documents: Mapping[str, str] | None = None,
    vector: Searcher | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """문의마다 FTS로 근거 스니펫을 찾는다. 제품 문서가 주어지면 그 문서를 먼저 찾고 전역으로 채운다.

    Args:
        records: {ticket_id: build_input 레코드}.
        searcher: FTS 검색기(fts_searcher_from_dsn 또는 테스트 대역).
        index_version: 검색할 색인 판본.
        document_sha256s: 찾을 문서 해시(None이면 전체).
        limit: 문의당 스니펫 상한(1~90).
        snippet_chars: 스니펫 최대 글자 수.
        sensitive_values: build_input에 넘길 {원값: SERIAL|PERSONAL}.
        query_from: 질의에 쓸 필드.
        product_documents: {ticket_id: 그 문의 제품 문서의 sha256 집합}. None이면 제품 통계도 내지 않는다.
        product_filter: True면 제품 문서 제한 검색을 먼저 하고 모자라면 전역으로 채운다. False면 전역만 쓰고
            통계만 낸다(전후 비교용).
        categories: {ticket_id: 분류 결과 category_code}. policy_documents와 함께 있으면 graph.policy_slot과 같은
            규칙으로 그 유형의 정책 문서 1자리를 예약한다(KAN-194 10차). None이면 예약하지 않는다.
        policy_documents: 정책 문서 doc_id → sha256(load_policy_documents).
        vector: 벡터 검색기(선택). 마스킹 원문을 질의로 받는다. 있으면 meta에 vector(embedding_version·failures)를 더한다.

    Returns:
        tuple: (evidence {ticket_id: [EvidenceSnippet dict]}, 집계 meta). 입력 정제 실패·토큰 없음은 빈 배열로
            두고 input_failed로 센다. product_documents가 있으면 meta에 product_filter·tickets_with_product_docs·
            product_hits(최종 근거에 제품 문서가 하나라도 든 문의 수)·product_top1(1순위가 제품 문서인 문의 수)을
            더한다. 검색 오류는 그대로 올린다.
    """
    scope = None if document_sha256s is None else frozenset(document_sha256s)
    filters = SearchFilters(
        document_sha256s=document_sha256s,
        index_version=index_version,
        limit=min(100, limit + SEARCH_MARGIN),
    )

    evidence: dict[str, list[dict[str, Any]]] = {}
    queries: list[str] = []
    input_failed = dropped = with_docs = hits = top1 = reserved_tickets = 0
    for ticket_id in sorted(records):
        docs = (product_documents or {}).get(ticket_id, frozenset())
        if scope is not None:
            docs = docs & scope
        with_docs += bool(docs)
        try:
            payload = build_input(records[ticket_id], sensitive_values=sensitive_values).payload
            query = query_terms(payload.subject, payload.body, query_from)
            text = "\n".join(query_parts(payload.subject, payload.body, query_from))
        except ValueError:
            query = text = ""
        if not query:
            evidence[ticket_id] = []
            input_failed += 1
            continue
        queries.append(f"{ticket_id}\t{query}")
        scoped = (
            filters.model_copy(update={"document_sha256s": sorted(docs)})
            if product_filter and docs
            else None
        )
        reserved = policy_slot(filters, (categories or {}).get(ticket_id), policy_documents)
        reserved_tickets += reserved is not None
        items, blocked = scoped_then_global(
            query,
            filters,
            scoped,
            fts=searcher,
            vector=partial(by_text, vector, text) if vector else None,
            limit=limit,
            code_text=text,
            reserved=reserved,
        )
        rows = [
            EvidenceSnippet(
                chunk_id=item.candidate.chunk_id,
                # scoped_then_global이 본문 있는 후보만 남기므로 빈 값은 오지 않는다.
                snippet=excerpt(item.candidate, snippet_chars) or "",
                section_path=item.candidate.section_path,
                document_sha256=item.candidate.document_sha256,
            ).model_dump()
            for item in items
        ]
        dropped += len(blocked)
        evidence[ticket_id] = rows
        hits += any(row["document_sha256"] in docs for row in rows)
        top1 += bool(rows) and rows[0]["document_sha256"] in docs
    meta = {
        "tickets": len(evidence),
        "with_evidence": sum(bool(rows) for rows in evidence.values()),
        "empty": sum(not rows for rows in evidence.values()),
        "input_failed": input_failed,
        "dropped_blocked": dropped,
        "index_version": index_version,
        "document_sha256s": document_sha256s,
        "limit_per_ticket": limit,
        "snippet_chars": snippet_chars,
        "query_from": query_from,
        "queries_sha256": hashlib.sha256("\n".join(queries).encode()).hexdigest(),
    }
    if categories is not None:
        meta |= {"policy_slot": True, "reserved_tickets": reserved_tickets}
    if vector is not None:
        meta["vector"] = {
            "embedding_version": getattr(vector, "embedding_version", None),
            "failures": getattr(vector, "failures", None),
        }
    if product_documents is not None:
        meta |= {
            "product_filter": product_filter,
            "tickets_with_product_docs": with_docs,
            "product_hits": hits,
            "product_top1": top1,
        }
    return evidence, meta


def default_searcher(index_version: str) -> Searcher:
    """DATABASE_URL의 cs_app DSN으로 FTS 검색기를 만든다.

    Args:
        index_version: 검색할 색인 판본.

    Returns:
        Searcher: fts_searcher_from_dsn 검색기. 환경변수가 없으면 ValueError다.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("CS_EVIDENCE_NO_DATABASE_URL")
    return fts_searcher_from_dsn(dsn, index_version)


def default_vector_searcher(index_version: str) -> Searcher:
    """DATABASE_URL(cs_app)과 GEMINI_API_KEY로 벡터 검색기를 만든다(CsVectorSettings, 활성 여부는 --vector로 정함).

    Args:
        index_version: 검색할 청크 색인 판본.

    Returns:
        Searcher: VectorSearcher. DSN·키가 없으면 ValueError다.
    """
    from backend.shared.ai import AISettings, create_client

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("CS_EVIDENCE_NO_DATABASE_URL")
    return vector_searcher_from_settings(
        dsn, index_version, CsVectorSettings(), create_client(AISettings(), embedding=True)
    )


def main(
    argv: list[str] | None = None,
    searcher: Callable[[str], Searcher] | None = None,
    vector: Callable[[str], Searcher] | None = None,
) -> int:
    """인자를 읽어 근거 파일(--out)을 쓰고 meta를 표준 출력에 낸다.

    Args:
        argv: 명령행 인자(테스트 주입용).
        searcher: 색인 판본을 받아 검색기를 만드는 함수(테스트 주입용). 없으면 DATABASE_URL을 쓴다.
        vector: --vector일 때 색인 판본을 받아 벡터 검색기를 만드는 함수(테스트 주입용). 없으면 Gemini를 부른다.

    Returns:
        int: 0 성공, 2 test 분할 거부, 1 입력·DB·출력 오류. 오류 원문·연결 문자열은 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--ticket-ids", type=Path, required=True, help="줄 단위·JSON 배열·라벨 JSON"
    )
    parser.add_argument(
        "--query-from", choices=["subject+body", "subject", "body"], default="subject+body"
    )
    parser.add_argument("--index-version", required=True)
    parser.add_argument("--document-sha256s", help="쉼표로 구분한 문서 해시(없으면 전체)")
    parser.add_argument("--limit-per-ticket", type=int, default=5)
    parser.add_argument("--snippet-chars", type=int, default=800)
    parser.add_argument("--sensitive-values", type=Path)
    parser.add_argument(
        "--index-manifest", type=Path, help="cs_index_docs --manifest-out JSON(제품 우선 검색·통계)"
    )
    parser.add_argument("--product-column", default="product_code")
    parser.add_argument(
        "--no-product-filter",
        action="store_true",
        help="제품 우선 검색을 끄고 통계만 낸다(--index-manifest와 함께, 전후 비교용)",
    )
    parser.add_argument(
        "--classification",
        type=Path,
        help="분류 결과 라벨 JSON({ticket_id: {category_code}}). --index-manifest의 정책 문서로 1자리를 예약한다",
    )
    parser.add_argument(
        "--vector",
        action="store_true",
        help="벡터 검색기도 함께 쓴다(KAN-99/100, 로컬 DB·GEMINI_API_KEY, 문의당 임베딩 1회)",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--meta-out", type=Path, help="meta를 파일로도 남길 경로(표준 출력과 같은 내용)"
    )
    args = parser.parse_args(argv)
    if (
        not 1 <= args.limit_per_ticket <= 100 - SEARCH_MARGIN
        or args.snippet_chars < 1
        or (args.no_product_filter and args.index_manifest is None)
        or (args.classification is not None and args.index_manifest is None)
    ):
        print(
            "CS_EVIDENCE_INVALID_ARGS: --limit-per-ticket은 1~90, --snippet-chars는 1 이상이고 "
            "--no-product-filter·--classification은 --index-manifest와 함께만 씁니다."
        )
        return 1
    started = datetime.now(UTC)
    try:
        ids = load_ticket_ids(args.ticket_ids)
        # manifest에 없는 ID는 아래 records[ticket_id]의 KeyError로 거부된다.
        records = load_targets(args.manifest, sys.maxsize, 0)
        sensitive = (
            json.loads(args.sensitive_values.read_text(encoding="utf-8"))
            if args.sensitive_values
            else {}
        )
        if not isinstance(sensitive, dict):
            raise ValueError("CS_EVIDENCE_SENSITIVE_SHAPE")
        documents = None
        if args.document_sha256s is not None:
            documents = [sha.strip() for sha in args.document_sha256s.split(",")]
            # 빈 항목(',' 만·'a,,b'·빈 문자열)은 결과 0건이나 필터 없음으로 떨어지지 않게 입력 오류로 거부한다.
            if not all(documents):
                raise ValueError("CS_EVIDENCE_DOCUMENT_SHA256S")
        product_documents = None
        if args.index_manifest is not None:
            indexed = load_product_documents(args.index_manifest)
            products = {
                row["ticket_id"]: row[args.product_column]
                for row in load_dev_tickets(args.manifest)
                if row["ticket_id"] in set(ids)
            }
            product_documents = {
                ticket_id: frozenset(
                    sha
                    for name, sha in indexed.items()
                    if is_product_document(name, products.get(ticket_id) or "")
                )
                for ticket_id in ids
            }
        evidence, meta = build_evidence(
            {ticket_id: records[ticket_id] for ticket_id in ids},
            (searcher or default_searcher)(args.index_version),
            index_version=args.index_version,
            document_sha256s=documents,
            limit=args.limit_per_ticket,
            snippet_chars=args.snippet_chars,
            sensitive_values=sensitive,
            query_from=args.query_from,
            product_documents=product_documents,
            product_filter=not args.no_product_filter,
            categories=load_categories(args.classification) if args.classification else None,
            policy_documents=(
                load_policy_documents(args.index_manifest) if args.classification else None
            ),
            vector=(vector or default_vector_searcher)(args.index_version) if args.vector else None,
        )
        write_private(args.out, json.dumps(evidence, ensure_ascii=False, indent=2).encode())
        meta |= {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "git_sha": git_sha(),
        }
        if args.meta_out:
            write_private(args.meta_out, json.dumps(meta, ensure_ascii=False, indent=2).encode())
    except SplitBlocked:
        print("CS_RUN_TEST_SPLIT: test 분할은 최종 1회 사용 원칙이라 실행하지 않습니다.")
        return 2
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, psycopg.Error):
        print(
            "CS_EVIDENCE_FAILED: manifest·대상 ID·민감값 파일과 DATABASE_URL·색인 판본"
            "(--vector면 GEMINI_API_KEY)을 확인하세요."
        )
        return 1
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
