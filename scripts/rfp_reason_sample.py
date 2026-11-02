"""엔진 사유 표본 추출(KAN-191 ⓐ). `rfp_candidate_run.py --predictions-out` 파일에서 문서별로 같은 수를 뽑아 사람이
맞음/틀림을 적는 표를 만든다.

    uv run --frozen python scripts/rfp_reason_sample.py --predictions <저장소 밖 예측.json> \\
        --documents 10 12 --per-document 10 --seed 20261102 --out <저장소 밖 표.md>

- 시드를 고정해 같은 입력이면 같은 표본이 나온다. 문서별 요구를 (clause_no, attribute) 순으로 정렬한 뒤 뽑는다.
- 정답 파일은 읽지 않는다. 표에는 요구 키·엔진 상태·엔진 사유와 빈 확인 칸(맞음/틀림, 메모)만 있다.
- 출력은 저장소 밖 새 파일로만 쓴다(덮어쓰기 없음). 엔진 사유에는 비교 값이 들어갈 수 있어 1:1로만 전달한다.
"""

import argparse
import json
import random
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ("문서", "조항", "속성", "엔진 상태", "엔진 사유", "맞음/틀림", "메모")


def sample(
    items: Iterable[dict], documents: Sequence[int], per_document: int, seed: int
) -> list[dict]:
    """문서별로 per_document건을 뽑는다. 요구가 그보다 적으면 전부 넣는다.

    Args:
        items: 예측 파일의 items.
        documents: 뽑을 문서 번호.
        per_document: 문서당 표본 수.
        seed: 난수 시드.

    Returns:
        list[dict]: 문서 번호 순, 그 안에서는 뽑힌 순서대로.
    """
    rng = random.Random(seed)
    chosen: list[dict] = []
    for number in documents:
        pool = sorted(
            (i for i in items if i["document_number"] == number),
            key=lambda i: (i["clause_no"], i["attribute"] or ""),
        )
        chosen += rng.sample(pool, min(per_document, len(pool)))
    return chosen


def render(rows: Sequence[dict], meta: dict, seed: int, per_document: int) -> str:
    """표본을 Markdown 표로 만든다. 사람이 '맞음/틀림'·'메모' 칸을 채운다."""
    head = (
        f"# KAN-191 엔진 사유 표본 확인\n\n"
        f"실행 {meta.get('run_label')} started_at {meta.get('started_at')} git_sha {str(meta.get('git_sha', ''))[:12]} "
        f"model {meta.get('model_versions')} / 시드 {seed}, 문서당 {per_document}건, 총 {len(rows)}건.\n"
        "엔진 상태·사유가 원문 기준으로 맞으면 '맞음', 아니면 '틀림'과 짧은 메모를 적어 주세요. 정답 파일 내용은 적지 않습니다.\n\n"
    )
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        cells = (
            str(r["document_number"]),
            r["clause_no"],
            r["attribute"] or "(null)",
            r["status"],
            (r["reason"] or "").replace("|", "\\|").replace("\n", " "),
            "",
            "",
        )
        lines.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--predictions", type=Path, required=True, help="rfp_candidate_run --predictions-out 파일"
    )
    parser.add_argument("--documents", type=int, nargs="+", required=True)
    parser.add_argument("--per-document", type=int, default=10)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True, help="저장소 밖 Markdown 표(새 파일)")
    args = parser.parse_args(argv)
    resolved = args.out.resolve()
    if ROOT == resolved or ROOT in resolved.parents:
        print("EVAL_OUTPUT_INSIDE_REPOSITORY", file=sys.stderr)
        return 1
    if args.out.exists():
        print("EVAL_OUTPUT_EXISTS", file=sys.stderr)
        return 1
    data = json.loads(args.predictions.read_text(encoding="utf-8"))
    rows = sample(data["items"], args.documents, args.per_document, args.seed)
    with args.out.open("x", encoding="utf-8") as handle:
        handle.write(render(rows, data.get("meta", {}), args.seed, args.per_document))
    print(f"표본 {len(rows)}건: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
