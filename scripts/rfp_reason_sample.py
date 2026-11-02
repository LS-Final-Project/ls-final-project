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


def _cell(value: object) -> str:
    """표 셀 글자. 세로줄·개행·역슬래시가 표를 깨지 않게 이스케이프한다(모든 셀 공통)."""
    text = "(null)" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


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
            _cell(r["document_number"]),
            _cell(r["clause_no"]),
            _cell(r["attribute"]),
            _cell(r["status"]),
            _cell(r["reason"] or ""),
            "",
            "",
        )
        lines.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(lines) + "\n"


def check_inputs(
    predictions: Path, documents: Sequence[int], per_document: int, out: Path
) -> str | None:
    """입력을 고정 코드로 검사한다(rfp_candidate_run과 같은 EVAL_* 규칙)."""
    if not predictions.is_file():
        return "EVAL_PREDICTIONS_MISSING"
    if not documents or len(set(documents)) != len(documents):
        return "EVAL_DOCUMENTS_INVALID"
    if per_document < 1:
        return "EVAL_PER_DOCUMENT_INVALID"
    resolved = out.resolve()
    if ROOT == resolved or ROOT in resolved.parents:
        return "EVAL_OUTPUT_INSIDE_REPOSITORY"
    if out.exists():
        return "EVAL_OUTPUT_EXISTS"
    if not out.parent.is_dir():
        return "EVAL_OUTPUT_DIR_MISSING"
    return None


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
    problem = check_inputs(args.predictions, args.documents, args.per_document, args.out)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    data = json.loads(args.predictions.read_text(encoding="utf-8"))
    present = {i["document_number"] for i in data.get("items", [])}
    missing = [n for n in args.documents if n not in present]
    if missing:
        # 번호 오타나 예측 누락으로 문서 하나가 조용히 빠진 표가 나오지 않게 한다(리뷰 P3).
        print(f"EVAL_PREDICTIONS_DOCUMENT_EMPTY {missing}", file=sys.stderr)
        return 1
    rows = sample(data["items"], args.documents, args.per_document, args.seed)
    with args.out.open("x", encoding="utf-8") as handle:
        handle.write(render(rows, data.get("meta", {}), args.seed, args.per_document))
    print(f"표본 {len(rows)}건: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
