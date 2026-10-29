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
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg

from backend.agent.cs_search import fts_searcher_from_dsn
from backend.engine.cs.classification import build_input
from backend.engine.cs.drafting import EvidenceSnippet
from backend.engine.search.hybrid import Searcher, run_hybrid
from backend.engine.search.model import SearchFilters
from scripts.cs_candidate_run import SplitBlocked, git_sha, load_targets, write_private

MAX_TERMS = 40
# 검색기에서 FLT-042 차단분만큼 모자라지 않도록 더 받아 앞 limit개를 쓴다.
SEARCH_MARGIN = 10
MASK_MARKER = re.compile(r"\[[A-Z]+_PROVIDED\]")
WORD = re.compile(r"\w+")
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
    parts = ([subject] if source != "body" else []) + ([body] if source != "subject" else [])
    text = MASK_MARKER.sub(" ", " ".join(part for part in parts if part))
    terms: dict[str, str] = {}
    for token in WORD.findall(text):
        key = token.casefold()
        if len(token) >= 2 and key != "or":
            terms.setdefault(key, token)
    return " or ".join(list(terms.values())[:MAX_TERMS])


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
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """문의마다 FTS로 근거 스니펫을 찾는다.

    Args:
        records: {ticket_id: build_input 레코드}.
        searcher: FTS 검색기(fts_searcher_from_dsn 또는 테스트 대역).
        index_version: 검색할 색인 판본.
        document_sha256s: 찾을 문서 해시(None이면 전체).
        limit: 문의당 스니펫 상한(1~90).
        snippet_chars: 스니펫 최대 글자 수.
        sensitive_values: build_input에 넘길 {원값: SERIAL|PERSONAL}.
        query_from: 질의에 쓸 필드.

    Returns:
        tuple: (evidence {ticket_id: [EvidenceSnippet dict]}, 집계 meta). 입력 정제 실패·토큰 없음은 빈 배열로
            두고 input_failed로 센다. 검색 오류는 그대로 올린다.
    """
    filters = SearchFilters(
        document_sha256s=document_sha256s,
        index_version=index_version,
        limit=min(100, limit + SEARCH_MARGIN),
    )
    evidence: dict[str, list[dict[str, Any]]] = {}
    queries: list[str] = []
    input_failed = dropped = 0
    for ticket_id in sorted(records):
        try:
            payload = build_input(records[ticket_id], sensitive_values=sensitive_values).payload
            query = query_terms(payload.subject, payload.body, query_from)
        except ValueError:
            query = ""
        if not query:
            evidence[ticket_id] = []
            input_failed += 1
            continue
        queries.append(f"{ticket_id}\t{query}")
        result = run_hybrid(query, filters, fts=searcher)
        dropped += len(result.dropped_blocked)
        evidence[ticket_id] = [
            EvidenceSnippet(
                chunk_id=item.candidate.chunk_id,
                snippet=item.candidate.text_excerpt[:snippet_chars],
                section_path=item.candidate.section_path,
                document_sha256=item.candidate.document_sha256,
            ).model_dump()
            for item in result.items
            if item.candidate.text_excerpt and item.candidate.text_excerpt.strip()
        ][:limit]
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


def main(argv: list[str] | None = None, searcher: Callable[[str], Searcher] | None = None) -> int:
    """인자를 읽어 근거 파일(--out)을 쓰고 meta를 표준 출력에 낸다.

    Args:
        argv: 명령행 인자(테스트 주입용).
        searcher: 색인 판본을 받아 검색기를 만드는 함수(테스트 주입용). 없으면 DATABASE_URL을 쓴다.

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
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--meta-out", type=Path, help="meta를 파일로도 남길 경로(표준 출력과 같은 내용)"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.limit_per_ticket <= 100 - SEARCH_MARGIN or args.snippet_chars < 1:
        print(
            "CS_EVIDENCE_INVALID_ARGS: --limit-per-ticket은 1~90, --snippet-chars는 1 이상입니다."
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
        documents = (
            [sha.strip() for sha in args.document_sha256s.split(",") if sha.strip()]
            if args.document_sha256s
            else None
        )
        evidence, meta = build_evidence(
            {ticket_id: records[ticket_id] for ticket_id in ids},
            (searcher or default_searcher)(args.index_version),
            index_version=args.index_version,
            document_sha256s=documents,
            limit=args.limit_per_ticket,
            snippet_chars=args.snippet_chars,
            sensitive_values=sensitive,
            query_from=args.query_from,
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
            "CS_EVIDENCE_FAILED: manifest·대상 ID·민감값 파일과 DATABASE_URL·색인 판본을 확인하세요."
        )
        return 1
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
