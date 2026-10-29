"""검색 근거의 hit@5·top1을 규칙 기대값으로 측정한다(KAN-101 2차). 정답이 아니라 규칙 기대값, 사람 검수 전이다.

cs_evidence_build 결과의 문의별 상위 근거를 document_sha256 → 색인 manifest doc_id → doc_index 문서 구조로 되돌리고,
분류 결과 라벨의 유형과 문의 제품으로 만든 기대 문서 집합(eval/search_expected)과 비교한다. 결과에는 집계와 입력
파일 sha256만 담고 원문·스니펫·사례별 행은 넣지 않는다. 합격선(gates-v1)·reconcile에는 연결하지 않는다.

예:
    uv run --frozen python -m scripts.cs_search_eval --manifest M --ticket-ids L --classification L \\
        --doc-index D --index-manifest I --evidence E --out result.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from eval.search_expected import expected_documents, load_doc_index, load_policy, summarize_hits

K = 5


def document_ids(path: Path) -> dict[str, str | None]:
    """색인 manifest의 적재 문서 sha256 → doc_id(README 등은 None)를 읽는다.

    Args:
        path: cs_index_docs --manifest-out JSON.

    Returns:
        dict: sha256별 doc_id. 형식이 틀리면 CS_SEARCH_EVAL_INDEX_MANIFEST ValueError.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("CS_SEARCH_EVAL_INDEX_MANIFEST")
    return {
        row["sha256"]: row.get("doc_id") or None
        for row in rows
        if isinstance(row.get("sha256"), str) and not row.get("reason")
    }


def evaluate(
    ids: list[str],
    products: dict[str, str],
    labels: dict[str, object],
    evidence: dict[str, object],
    doc_ids: dict[str, str | None],
    doc_index: dict[str, dict[str, str]],
    policy: dict,
) -> dict:
    """문의별 기대 집합과 순위 doc_id를 만들어 집계한다.

    Args:
        ids: 대상 문의 ID.
        products: 문의별 제품 코드.
        labels: 분류 결과 라벨(문의별 {category_code} 또는 null).
        evidence: 문의별 순위 근거 목록({document_sha256}).
        doc_ids: 색인 문서 sha256 → doc_id.
        doc_index: doc_index 문서 구조.
        policy: 규칙 표.

    Returns:
        dict: 규칙 기대값 표시와 summarize_hits 결과. 색인에 없는 근거 문서는 CS_SEARCH_EVAL_UNKNOWN_DOCUMENT.
    """
    ranked: dict[str, list[str | None]] = {}
    expected: dict[str, frozenset[str] | None] = {}
    categories: dict[str, str | None] = {}
    for ticket_id in ids:
        rows = evidence.get(ticket_id) or []
        if not isinstance(rows, list) or any(r.get("document_sha256") not in doc_ids for r in rows):
            raise ValueError("CS_SEARCH_EVAL_UNKNOWN_DOCUMENT")
        ranked[ticket_id] = [doc_ids[r["document_sha256"]] for r in rows]
        label = labels.get(ticket_id)
        category = label.get("category_code") if isinstance(label, dict) else None
        categories[ticket_id] = category if category in policy["categories"] else None
        expected[ticket_id] = (
            None
            if categories[ticket_id] is None
            else expected_documents(
                policy["categories"][category], products.get(ticket_id) or "", doc_index
            )
        )
    return {
        "expected_version": policy["version"],
        "basis": policy["basis"],
        "review": policy["review"],
        "k": K,
        "metrics": summarize_hits(ranked, expected, categories, k=K),
    }


def main(argv: list[str] | None = None) -> int:
    """인수를 읽어 측정하고 결과 JSON을 쓴다.

    Args:
        argv: 명령줄 인수(없으면 sys.argv).

    Returns:
        int: 성공 0, 입력 오류 1(코드만 출력).
    """
    from scripts.cs_evidence_build import load_ticket_ids
    from tests.cs.cs_dev_data import load_dev_tickets

    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "--manifest",
        "--ticket-ids",
        "--classification",
        "--doc-index",
        "--index-manifest",
        "--evidence",
        "--out",
    ):
        parser.add_argument(name, type=Path, required=True)
    parser.add_argument("--product-column", default="product_code")
    args = parser.parse_args(argv)
    try:
        ids = load_ticket_ids(args.ticket_ids)
        wanted = set(ids)
        products = {
            row["ticket_id"]: row[args.product_column]
            for row in load_dev_tickets(args.manifest)
            if row["ticket_id"] in wanted
        }
        if set(products) != wanted:
            raise ValueError("CS_SEARCH_EVAL_TICKETS")
        labels = json.loads(args.classification.read_text(encoding="utf-8"))
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        if not isinstance(labels, dict) or not isinstance(evidence, dict):
            raise ValueError("CS_SEARCH_EVAL_SHAPE")
        result = evaluate(
            ids,
            products,
            labels,
            evidence,
            document_ids(args.index_manifest),
            load_doc_index(args.doc_index),
            load_policy(),
        )
    except (OSError, ValueError, KeyError, AttributeError) as error:
        code = (
            str(error)
            if str(error).isupper() and str(error).startswith(("CS_", "SEARCH_"))
            else type(error).__name__
        )
        print(f"CS_SEARCH_EVAL_FAILED: {code}", file=sys.stderr)
        return 1
    result["inputs_sha256"] = {
        name: hashlib.sha256(getattr(args, name).read_bytes()).hexdigest()
        for name in ("classification", "doc_index", "index_manifest", "evidence")
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
