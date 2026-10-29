"""KAN-185 요구 추출 로컬 평가. 실제 RFP와 정답은 저장소 밖에 두고 경로로 받는다.

    uv run --frozen python scripts/rfp_requirements_eval.py \\
        --docx <RFP.docx> --gold <정답.jsonl> --sections 2.4 2.5 --out <저장소 밖 폴더>

- 조항 트리를 만들고 평가 절마다 Gemini 구조화 출력을 한 번 부른다(자동 재시도 없음).
- 화면에는 건수·비율·사용량만 낸다. 예측·놓친 정답·남는 예측은 --out 폴더의 JSON에 쓴다(원문 조각 포함).
- 키는 .env.integrations의 GEMINI_API_KEY를 읽고 출력하지 않는다.
- --adoption-out이 있으면 eval_adopt --task rfp 입력 형식으로 요구 추출 게이트 2개(재현율·정밀도)를 쓴다(KAN-143 4차).
  값은 (clause_no, attribute) 키 일치 기준이며 비교 상태는 보지 않는다. report.json의 엄격·관대 점수와 정의가 다르다.
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.engine.documents.router import extract  # noqa: E402
from backend.engine.rfp.clauses import build_clause_tree  # noqa: E402
from backend.engine.rfp.requirements import (  # noqa: E402
    EXTRACTION_VERSION,
    ExtractedRequirement,
    ResponseSchema,
    build_inputs,
    extract_section,
)
from backend.engine.rfp.scoring import GoldRequirement, score  # noqa: E402
from backend.shared.ai import AISettings, create_client, generate  # noqa: E402
from eval.rfp_metrics import summarize_extraction_keys  # noqa: E402

# 채택 기록 meta에 남기는 측정 정의. 게이트 정의(summarize_requirement_extraction)와 다른 점을 함께 적는다.
METRIC_DEFINITION = "(clause_no, attribute) 키 일치, 상태 미비교, 재현율 분모=필수 정답"
VOCABULARY_NOTE = "정답 attribute 어휘 일치 미검증(개발 정답은 저장소 밖)"


def adoption_input(
    gold: Sequence[GoldRequirement],
    predicted: Sequence[ExtractedRequirement],
    *,
    name: str,
    model: str,
    version: str,
    started_at: str,
    git_sha: str,
) -> dict[str, object]:
    """요구 추출 결과를 eval_adopt --task rfp 입력 형식으로 만든다. 원문·값은 넣지 않는다.

    Args:
        gold: 측정 문서의 정답 요구(clause_no·attribute와 재현율 분모용 mandatory만 쓴다).
        predicted: 추출한 요구.
        name: 후보 이름(--candidate-name).
        model: 호출한 모델 ID.
        version: 추출 판본(EXTRACTION_VERSION).
        started_at: 실행 시작 시각(ISO 8601).
        git_sha: 실행한 커밋.

    Returns:
        dict: {"meta": {...}, "result": {"candidates": [{"name", rfp_requirement_recall, rfp_requirement_precision}]}}.
            나머지 게이트 7개는 이 스크립트가 측정하지 않아 넣지 않는다(채택 판정에서 NOT_EVALUABLE).
    """
    case = "document-1"
    keys = {case: [(p.clause_no, p.attribute) for p in predicted]}
    # 정밀도는 예측 전체를 정답 전체와 맞추고, 재현율은 게이트 정의대로 필수(mandatory) 정답만 분모로 둔다.
    summary = summarize_extraction_keys({case: [(g.clause_no, g.attribute) for g in gold]}, keys)[
        "extraction"
    ]
    mandatory = summarize_extraction_keys(
        {case: [(g.clause_no, g.attribute) for g in gold if g.mandatory]}, keys
    )["extraction"]
    return {
        "meta": {
            "documents": 1,
            "candidates": [{"name": name, "model": model, "extraction_version": version}],
            "started_at": started_at,
            "git_sha": git_sha,
            "metric_definition": METRIC_DEFINITION,
            "vocabulary_note": VOCABULARY_NOTE,
            "counts": {
                "recall": mandatory["recall_count"],
                "precision": summary["precision_count"],
            },
        },
        "result": {
            "candidates": [
                {
                    "name": name,
                    "rfp_requirement_recall": mandatory["recall"],
                    "rfp_requirement_precision": summary["precision"],
                }
            ]
        },
    }


def precheck_adoption(gold: Sequence[GoldRequirement], adoption_out: Path | None) -> str | None:
    """--adoption-out을 쓸 수 있는지 모델 호출 전에 본다(유료 호출 뒤 실패를 막는다).

    Args:
        gold: 읽은 정답 요구.
        adoption_out: 채택 기록 입력 파일 경로(없으면 검사하지 않는다).

    Returns:
        str | None: 문제가 있으면 고정 코드(EVAL_OUTPUT_EXISTS·EVAL_OUTPUT_DIR_MISSING·EVAL_GOLD_DUPLICATE_KEY),
            없으면 None.
    """
    if adoption_out is None:
        return None
    if adoption_out.exists():
        return "EVAL_OUTPUT_EXISTS"
    if not adoption_out.parent.is_dir():
        return "EVAL_OUTPUT_DIR_MISSING"
    try:
        summarize_extraction_keys({"document-1": [(g.clause_no, g.attribute) for g in gold]}, {})
    except ValueError:
        return "EVAL_GOLD_DUPLICATE_KEY"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--sections", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.integrations")
    parser.add_argument("--adoption-out", type=Path, help="eval_adopt --task rfp 입력 파일(선택)")
    parser.add_argument("--candidate-name", help="채택 기록 후보 이름(기본 requirements-<모델>)")
    args = parser.parse_args(argv)
    if ROOT in args.out.resolve().parents or args.out.resolve() == ROOT:
        raise SystemExit("STOP: 결과에는 원문 조각이 들어가므로 저장소 밖 폴더에 쓴다")
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    # 정답과 채택 입력 경로는 키·문서·모델을 쓰기 전에 검사한다.
    gold = [
        GoldRequirement.model_validate_json(line)
        for line in args.gold.read_text().splitlines()
        if line
    ]
    problem = precheck_adoption(gold, args.adoption_out)
    if problem:
        print(f"{problem}: --adoption-out을 쓸 수 없어 모델을 부르지 않았다.", file=sys.stderr)
        return 1

    values = {k: v or "" for k, v in dotenv_values(args.env_file).items()}
    settings = AISettings(
        _env_file=None,
        ai_provider="gemini",
        gemini_model=values.get("GEMINI_MODEL") or "gemini-3.6-flash",
        gemini_api_key=SecretStr(values["GEMINI_API_KEY"]),
        gemini_max_output_tokens=16384,
        gemini_timeout_ms=180000,
    )
    client = create_client(settings)

    def provider(text: str):
        return generate(client, settings, text, response_schema=ResponseSchema)

    tree = build_clause_tree(extract(args.docx.read_bytes(), source_name=args.docx.name))
    args.out.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "version": EXTRACTION_VERSION,
        "model": settings.gemini_model,
        "sections": {},
    }
    all_pred, all_gold = [], []
    for item in build_inputs(tree, args.sections):
        result = extract_section(item, provider)
        section_gold = [g for g in gold if g.clause_no.startswith(item.section_no + ".")]
        strict = score(section_gold, result.requirements)
        lenient = score(section_gold, result.requirements, lenient=True)
        all_pred += result.requirements
        all_gold += section_gold
        usage = result.usage
        print(
            f"{item.section_no}: {result.outcome} 채택 {len(result.requirements)} 버림 {len(result.rejected)}"
            f" 값만 비움 {result.value_dropped}"
            f" | 요구 문장 포괄 1차 {result.covered_first}/{result.sentences}"
            f" 보완 후 {result.covered_final}/{result.sentences} (보완 +{result.gap_fill_added})"
            f" | 엄격 재현율 {strict.recall} 정밀도 {strict.precision}"
            f" | 관대 재현율 {lenient.recall} 정밀도 {lenient.precision}"
            f" | 토큰 in {usage.input_tokens} out {usage.output_tokens}",
            flush=True,
        )
        report["sections"][item.section_no] = {  # type: ignore[index]
            "outcome": result.outcome,
            "coverage": {
                "sentences": result.sentences,
                "first": result.covered_first,
                "final": result.covered_final,
                "gap_fill_added": result.gap_fill_added,
            },
            "model_version": result.model_version,
            "usage": {k: v for k, v in asdict(usage).items() if k != "raw"},
            "rejected": [r.model_dump() for r in result.rejected],
            "predicted": [r.model_dump() for r in result.requirements],
            "strict": {
                "recall": strict.recall,
                "precision": strict.precision,
                "missed": [g.model_dump() for g in strict.missed],
                "extra": [p.model_dump() for p in strict.extra],
            },
            "lenient": {
                "recall": lenient.recall,
                "precision": lenient.precision,
                "missed": [g.model_dump() for g in lenient.missed],
                "extra": [p.model_dump() for p in lenient.extra],
            },
        }
    total_s, total_l = score(all_gold, all_pred), score(all_gold, all_pred, lenient=True)
    print(
        f"합계: 엄격 재현율 {total_s.recall} 정밀도 {total_s.precision} | 관대 재현율 {total_l.recall} 정밀도 {total_l.precision}"
    )
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"자세한 결과: {args.out / 'report.json'}")
    if args.adoption_out:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        record = adoption_input(
            all_gold,
            all_pred,
            name=args.candidate_name or f"requirements-{settings.gemini_model}",
            model=settings.gemini_model,
            version=EXTRACTION_VERSION,
            started_at=started_at,
            git_sha=sha,
        )
        args.adoption_out.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        print(f"채택 기록 입력: {args.adoption_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
