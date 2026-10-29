"""KAN-185 요구 추출 로컬 평가. 실제 RFP와 정답은 저장소 밖에 두고 경로로 받는다.

    uv run --frozen python scripts/rfp_requirements_eval.py \\
        --docx <RFP.docx> --gold <정답.jsonl> --sections 2.4 2.5 --out <저장소 밖 폴더>

- 조항 트리를 만들고 평가 절마다 Gemini 구조화 출력을 한 번 부른다(자동 재시도 없음).
- 화면에는 건수·비율·사용량만 낸다. 예측·놓친 정답·남는 예측은 --out 폴더의 JSON에 쓴다(원문 조각 포함).
- 키는 .env.integrations의 GEMINI_API_KEY를 읽고 출력하지 않는다.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.engine.documents.router import extract  # noqa: E402
from backend.engine.rfp.clauses import build_clause_tree  # noqa: E402
from backend.engine.rfp.requirements import (  # noqa: E402
    EXTRACTION_VERSION,
    ResponseSchema,
    build_inputs,
    extract_section,
)
from backend.engine.rfp.scoring import GoldRequirement, score  # noqa: E402
from backend.shared.ai import AISettings, create_client, generate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--sections", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.integrations")
    args = parser.parse_args()
    if ROOT in args.out.resolve().parents or args.out.resolve() == ROOT:
        raise SystemExit("STOP: 결과에는 원문 조각이 들어가므로 저장소 밖 폴더에 쓴다")

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
    gold = [
        GoldRequirement.model_validate_json(line)
        for line in args.gold.read_text().splitlines()
        if line
    ]
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
