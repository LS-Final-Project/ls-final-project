"""C/S 답변 초안 후보 비교 실행기(KAN-144). 기본은 dry-run이며 네트워크를 쓰지 않는다.

분류 실행기(scripts/cs_candidate_run.py)와 같은 모양이다. 대상 표본·코드북·후보 명세·비용 추정·출력 경로
검사·git sha는 그 파일의 함수를 그대로 쓴다. 초안 입력의 분류 결과는 --classification 파일
({ticket_id: ClassificationOutput | null})에서 읽고 null·검사 실패 건은 보류(제외)한다. 필요 정보 코드는
eval/policy/cs_required_info.csv의 (family, category) 행에서 읽고, family는 tickets의 제품 코드 열
(--product-column)로 rules.product_family()가 정한다. 이미 물은 코드는 빈 집합이다.

정답 파일이 없으므로 DraftGold는 '정답'이 아니라 규칙 기반 기대값이다: input·relevant_chunk_ids = 근거 ID 전부,
request_codes = 필요 − 분류 provided_info, answerable = 근거가 있음. 키는 환경변수(GEMINI_API_KEY)에서만 읽는다
(셸에서 set -a; . ./.env; set +a). 결과에는 원문·raw_response·키·표본 ID를 넣지 않는다(표본 ID는 sha256만).
"""

import argparse
import csv
import hashlib
import io
import json
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.engine.cs.classification import validate_output
from backend.engine.cs.drafting import (
    DRAFT_INPUT_VERSION,
    DRAFT_INPUT_VERSION_STRIPPED,
    DraftOutput,
    EvidenceSnippet,
    build_draft_input,
    render_prompt,
)
from backend.engine.cs.drafting_call import DraftAttempt
from backend.engine.cs.prompt_registry import prompt_fingerprint
from backend.engine.cs.providers import gemini_text_provider
from backend.engine.cs.rules import RuleInputError, product_family
from backend.shared.ai import AISettings, Generation, create_client, load_price_table
from eval.cs_draft_candidates import DraftCandidate, DraftCase, run_draft_candidates
from eval.cs_draft_metrics import DraftGold
from scripts.cs_candidate_run import (
    AUDIT_KINDS,
    AuditFiles,
    Prior,
    SplitBlocked,
    audit_meta,
    audit_payloads,
    case_key,
    estimate,
    git_sha,
    knob_fields,
    knobs_of,
    label_path,
    load_codes,
    load_specs,
    load_targets,
    open_progress,
    prepare_output,
    run_fingerprint,
    stop_run,
    write_private,
)
from tests.cs.cs_dev_data import load_dev_tickets

REQUIRED_INFO = Path(__file__).resolve().parents[1] / "eval" / "policy" / "cs_required_info.csv"
EXPECTED_RULE = (
    "규칙 기반 기대값(정답 아님): input·relevant_chunk_ids=근거 ID 전부, "
    "request_codes=필요 정보 − 분류 provided_info, answerable=근거 있음"
)
TextFactory = Callable[[dict[str, Any], AISettings], Callable[[str], Generation]]


def load_required(path: Path = REQUIRED_INFO) -> dict[tuple[str, str], frozenset[str]]:
    """(family, category)별 필요 정보 코드를 읽는다.

    Args:
        path: 필요 정보 정책 CSV(family, category, required_fields 열을 이름으로 읽는다).

    Returns:
        dict: (family, category) → required_fields를 ';'로 나눈 코드 집합.
    """
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"), newline=""))
    if not {"family", "category", "required_fields"} <= set(reader.fieldnames or ()):
        raise ValueError("CS_REQUIRED_INFO_COLUMNS")
    return {
        (row["family"], row["category"]): frozenset(
            code for code in row["required_fields"].split(";") if code
        )
        for row in reader
    }


def build_cases(
    records: Mapping[str, Mapping[str, object]],
    products: Mapping[str, str],
    classification: Mapping[str, object],
    evidence: Mapping[str, list[dict[str, Any]]],
    required: Mapping[tuple[str, str], frozenset[str]],
    codes: frozenset[str],
    sensitive: Mapping[str, Any],
) -> tuple[dict[str, DraftCase], dict[str, DraftGold], dict[str, int]]:
    """대상마다 초안 입력과 규칙 기반 기대값을 만든다.

    Args:
        records: 대상 레코드.
        products: 대상 ID → 제품 코드.
        classification: {ticket_id: ClassificationOutput dict | null}.
        evidence: {ticket_id: [EvidenceSnippet dict]}. 없는 대상은 빈 근거다.
        required: (family, category) → 필요 정보 코드.
        codes: 허용 정보 코드(분류 결과 검사에 쓴다).
        sensitive: 민감값과 마스킹 종류.

    Returns:
        tuple: (사례, 기대값, 건수). 건수는 held_classification(null·검사 실패·누락),
            missing_policy(제품군·필요 정보 행 없음), no_evidence(근거 없는 사례)다.
    """
    cases: dict[str, DraftCase] = {}
    gold: dict[str, DraftGold] = {}
    counts = {"held_classification": 0, "missing_policy": 0, "no_evidence": 0}
    for ticket_id, record in records.items():
        value = classification.get(ticket_id)
        try:
            if value is None:
                raise ValueError
            output = validate_output(json.dumps(value), allowed_info_codes=codes)
        except ValueError:
            counts["held_classification"] += 1
            continue
        try:
            needed = required[(product_family(products.get(ticket_id, "")), output.category_code)]
        except (KeyError, RuleInputError):
            counts["missing_policy"] += 1
            continue
        snippets = [EvidenceSnippet.model_validate(item) for item in evidence.get(ticket_id, [])]
        ids = frozenset(s.chunk_id for s in snippets)
        counts["no_evidence"] += not snippets
        cases[ticket_id] = DraftCase(
            record=record,
            classification=output,
            required_info_codes=needed,
            already_asked_codes=frozenset(),
            evidence=snippets,
            sensitive_values=sensitive,
        )
        gold[ticket_id] = DraftGold(
            input_chunk_ids=ids,
            relevant_chunk_ids=ids,
            request_codes=needed - set(output.provided_info),
            answerable=bool(snippets),
        )
    return cases, gold, counts


def dry_run(
    cases: Mapping[str, DraftCase],
    specs: list[dict[str, Any]],
    settings: AISettings,
    prices: Mapping[str, Any],
    strip_phone_annotation: bool = False,
) -> list[dict[str, object]]:
    """후보마다 엔진의 render_prompt로 실제 프롬프트를 만들어 크기·비용을 추정한다.

    Args:
        cases: 초안 사례.
        specs: 후보 명세(name, model, prompt_version).
        settings: AI 설정(출력 토큰 상한).
        prices: 단가표.
        strip_phone_annotation: 분석용 PHONE 표기 제거(기본 False).

    Returns:
        list[dict]: 후보별 보류 건수(초안 입력 실패)와 estimate() 결과. 네트워크를 쓰지 않는다.
    """
    rows = []
    for spec in specs:
        lengths, sizes, held = [], [], 0
        for case in cases.values():
            try:
                payload = build_draft_input(
                    case.record,
                    classification=case.classification,
                    required_info_codes=case.required_info_codes,
                    already_asked_codes=case.already_asked_codes,
                    evidence=case.evidence,
                    sensitive_values=case.sensitive_values,
                    strip_phone_annotation=strip_phone_annotation,
                ).payload
            except ValueError:
                held += 1
                continue
            prompt = render_prompt(payload, prompt_version=spec["prompt_version"])
            lengths.append(len(prompt))
            sizes.append(len(prompt.encode("utf-8")))
        rows.append(
            {
                **spec,
                "held": held,
                **estimate(
                    lengths, sizes, spec["model"], settings, prices, spec.get("max_output_tokens")
                ),
            }
        )
    return rows


def default_factory(settings: AISettings) -> TextFactory:
    """실제 Gemini 클라이언트를 한 번 만들고 후보마다 텍스트 어댑터를 낸다.

    Args:
        settings: 환경변수에서 읽은 AI 설정.

    Returns:
        TextFactory: (명세, 설정) → DraftOutput 스키마로 부르는 공급자.
    """
    client = create_client(settings)

    def factory(spec: dict[str, Any], config: AISettings) -> Callable[[str], Generation]:
        return gemini_text_provider(
            client,
            config,
            model=spec["model"],
            response_schema=DraftOutput,
            generation=knobs_of(spec),
        )

    return factory


def execute(
    cases: Mapping[str, DraftCase],
    gold: Mapping[str, DraftGold],
    counts: Mapping[str, int],
    specs: list[dict[str, Any]],
    settings: AISettings,
    prices: Mapping[str, Any],
    factory: TextFactory,
    subset: frozenset[str] | None,
    strip_phone_annotation: bool = False,
    audit: bool = False,
    invalid_output_retries: int = 1,
    on_case: Callable[..., None] | None = None,
    prior: Prior | None = None,
    resumed: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], AuditFiles]:
    """하네스로 실제 비교를 돌리고 실행 메타를 붙인다.

    Args:
        cases: 초안 사례.
        gold: 규칙 기반 기대값.
        counts: build_cases의 제외·근거 건수.
        specs: 후보 명세.
        settings: AI 설정.
        prices: 단가표.
        factory: 후보별 공급자를 만드는 함수(테스트는 fake로 교체).
        subset: 재실행할 사례 ID(없으면 전체).
        strip_phone_annotation: 분석용 PHONE 표기 제거(기본 False). meta에 값과 입력 판본을 남긴다.
        audit: True면 후보별 사례 감사 파일 내용을 만든다.
        invalid_output_retries: 형식 위반 재호출 횟수(0 또는 1). 재호출은 attempt 2로 따로 남는다.
        on_case: 사례가 끝날 때마다 부를 진행 기록(Progress.record).
        prior: 재개로 되살린 끝난 사례.
        resumed: meta에 더할 resumed_from·resumed_cases.

    Returns:
        tuple: (결과, 감사). 결과는 run_draft_candidates 결과와 meta(후보·판본 지문·건수·기대값 규칙·표본 ID
            sha256·시각·git sha, 감사를 만들면 그 파일 sha256)다. 감사는 audit_payloads 형식(실행한 사례만)이며
            초안·근거·요청 코드·원시 응답이 없다.
    """
    started = datetime.now(UTC)
    candidates = [
        DraftCandidate(
            name=spec["name"],
            provider=factory(spec, settings),
            model=spec["model"],
            prompt_version=spec["prompt_version"],
            knobs=knob_fields(spec),
        )
        for spec in specs
    ]
    result = run_draft_candidates(
        cases,
        gold=gold,
        candidates=candidates,
        price_table=dict(prices),
        subset=subset,
        strip_phone_annotation=strip_phone_annotation,
        return_attempts=audit,
        invalid_output_retries=invalid_output_retries,
        on_case=on_case,
        prior=prior,
    )
    attempts, retries = result.pop("attempts", {}), result.pop("retries", {})
    audits = audit_payloads(attempts, retries) if audit else {}
    output: dict[str, object] = {
        "meta": {
            "candidates": [
                {**spec, "prompt_fingerprint": prompt_fingerprint("draft", spec["prompt_version"])}
                for spec in specs
            ],
            "strip_phone_annotation": strip_phone_annotation,
            "input_version": DRAFT_INPUT_VERSION_STRIPPED
            if strip_phone_annotation
            else DRAFT_INPUT_VERSION,
            "invalid_output_retries": invalid_output_retries,
            **(resumed or {}),
            "cases": len(cases),
            **counts,
            "expected_values": EXPECTED_RULE,
            "case_ids_sha256": hashlib.sha256("\n".join(sorted(cases)).encode()).hexdigest(),
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "git_sha": git_sha(),
            **(audit_meta(audits) if audit else {}),
        },
        "result": result,
    }
    return output, audits


def main(argv: list[str] | None = None, factory: TextFactory | None = None) -> int:
    """인자를 읽어 dry-run 표를 출력하거나 --execute로 결과 JSON을 쓴다.

    Args:
        argv: 명령행 인자(테스트 주입용).
        factory: 실제 공급자 대신 쓸 공급자 함수(테스트 주입용).

    Returns:
        int: 0 성공, 2 test 분할 거부, 1 입력·출력 오류. 오류 원문·키는 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--product-column", required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--allowed-info-codes", type=Path, required=True)
    parser.add_argument("--info-code-column", required=True)
    parser.add_argument("--sensitive-values", type=Path)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--ticket-ids", help="쉼표로 구분한 재실행 대상 ID(--execute에서만)")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--audit-out",
        type=Path,
        help="후보별 사례 감사(<name>.audit.json·<name>.retry-audit.json, 0600) 디렉터리. 사례 식별자가 "
        "포함된 비공개 산출물",
    )
    parser.add_argument(
        "--invalid-output-retries",
        type=int,
        choices=[0, 1],
        default=1,
        help="형식 위반(CS_INVALID_OUTPUT) 재호출 횟수. 금지 약속·차단 근거는 재호출하지 않는다(기본 1)",
    )
    parser.add_argument(
        "--strip-phone-annotation",
        action="store_true",
        help="분석용. PHONE의 Urgency: <값> 상담원 표기를 지운 v1 초안 입력으로 돌린다(기본은 표기 포함 v2).",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        help="중단된 실행의 <out>.progress.jsonl. 같은 실행 지문일 때만 끝난 사례를 건너뛰고 이어 쓴다",
    )
    args = parser.parse_args(argv)
    if (
        args.limit < 1
        or (args.execute and args.out is None)
        or (args.audit_out is not None and not args.execute)
        or (args.resume is not None and not args.execute)
    ):
        print(
            "CS_RUN_INVALID_ARGS: --limit은 1 이상, --execute에는 --out이 필요하고 "
            "--audit-out·--resume은 --execute와 함께만 쓴다."
        )
        return 1
    progress = None
    try:
        records = load_targets(args.manifest, args.limit, args.seed)
        products = {
            row["ticket_id"]: row[args.product_column]
            for row in load_dev_tickets(args.manifest)
            if row["ticket_id"] in records
        }
        classification = json.loads(args.classification.read_text(encoding="utf-8"))
        evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else {}
        sensitive = (
            json.loads(args.sensitive_values.read_text(encoding="utf-8"))
            if args.sensitive_values
            else {}
        )
        if not isinstance(classification, dict) or not isinstance(evidence, dict):
            raise ValueError("CS_RUN_INPUT_SHAPE")
        codes = load_codes(args.allowed_info_codes, args.info_code_column)
        cases, gold, counts = build_cases(
            records, products, classification, evidence, load_required(), codes, sensitive
        )
        if not cases:
            raise ValueError("CS_RUN_NO_CASES")
        specs = load_specs(args.candidates)
        subset = (
            frozenset(i.strip() for i in args.ticket_ids.split(",") if i.strip())
            if args.ticket_ids
            else None
        )
        if subset is not None and (not subset or not subset <= set(cases)):
            raise ValueError("CS_RUN_SUBSET")
        settings = AISettings()
        prices = load_price_table()
        if not args.execute:
            scoped = {k: v for k, v in cases.items() if subset is None or k in subset}
            rows = dry_run(
                scoped,
                specs,
                settings,
                prices,
                strip_phone_annotation=args.strip_phone_annotation,
            )
            print(
                "dry-run: 네트워크 호출 없음. 토큰은 문자/4 근사와 UTF-8 바이트 상한이며 실제 과금은 "
                "실행 후 usage로 확인한다. 근거 없는 사례는 모든 문장이 CONDITION·UNCONFIRMED여야 한다."
            )
            print(
                json.dumps({"cases": len(cases), **counts, "candidates": rows}, ensure_ascii=False)
            )
            return 0
        # 유료 호출 전에 모든 후보 판본이 레지스트리에 있는지 확인한다(뒤 후보에서 멈춰 결과를 잃지 않게).
        for spec in specs:
            prompt_fingerprint("draft", spec["prompt_version"])
        prepare_output(args.out)
        if args.audit_out is not None:
            for spec in specs:
                for kind in AUDIT_KINDS:
                    write_private(label_path(args.audit_out, spec["name"], kind), b"")
        selected = [case_id for case_id in cases if subset is None or case_id in subset]
        fingerprint = run_fingerprint(
            {
                "kind": "draft",
                "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                # 판본 이름이 같아도 프롬프트·지침 본문이나 입력 정제가 바뀌면 다른 실행이다.
                "prompt_fingerprints": [
                    prompt_fingerprint("draft", spec["prompt_version"]) for spec in specs
                ],
                "input_version": DRAFT_INPUT_VERSION_STRIPPED
                if args.strip_phone_annotation
                else DRAFT_INPUT_VERSION,
                "product_column": args.product_column,
                "classification_sha256": hashlib.sha256(
                    args.classification.read_bytes()
                ).hexdigest(),
                "evidence_sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest()
                if args.evidence
                else None,
                "candidates": specs,
                "seed": args.seed,
                "limit": args.limit,
                "strip_phone_annotation": args.strip_phone_annotation,
                "invalid_output_retries": args.invalid_output_retries,
                "cases": sorted(case_key(case_id) for case_id in selected),
                "codes": sorted(codes),
                "sensitive": run_fingerprint(sensitive),
            }
        )
        progress, prior, resumed = open_progress(
            args.out,
            args.resume,
            fingerprint,
            selected,
            [spec["name"] for spec in specs],
            DraftAttempt,
            DraftOutput,
        )
        output, audits = execute(
            cases,
            gold,
            counts,
            specs,
            settings,
            prices,
            factory or default_factory(settings),
            subset,
            strip_phone_annotation=args.strip_phone_annotation,
            audit=args.audit_out is not None,
            invalid_output_retries=args.invalid_output_retries,
            on_case=progress.record,
            prior=prior,
            resumed=resumed,
        )
        progress.close()
    except SplitBlocked:
        print("CS_RUN_TEST_SPLIT: test 분할은 최종 1회 사용 원칙이라 실행하지 않습니다.")
        return 2
    except KeyboardInterrupt:
        return stop_run(progress, "EVAL_RUN_INTERRUPTED", 130)
    except (ValueError, OSError, KeyError, TypeError, csv.Error) as error:
        if str(error) == "EVAL_RESUME_MISMATCH":
            print(
                "EVAL_RESUME_MISMATCH: 진행 파일이 이번 실행(manifest·후보·seed·limit·판본)과 다릅니다."
            )
        else:
            print(
                "CS_RUN_FAILED: manifest·제품 열·분류·근거·코드북·후보 파일과 환경변수를 확인하세요."
            )
        return stop_run(progress, "EVAL_RUN_FAILED", 1) if progress else 1
    except Exception:
        return stop_run(progress, "EVAL_RUN_FAILED", 1)
    try:
        # 감사를 먼저 쓰고 결과(감사 sha256 포함)를 마지막에 써서, 실패 시 해시가 맞지 않는 결과가 남지 않게 한다.
        for (name, kind), data in audits.items():
            write_private(label_path(args.audit_out, name, kind), data)
        args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        print(
            "CS_RUN_OUTPUT_FAILED: 실행은 끝났지만 결과 파일을 쓰지 못했습니다. --out 경로를 확인하세요."
        )
        return 1
    progress.remove()
    print(json.dumps({"cases": len(cases), "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
