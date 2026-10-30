"""RFP 후보 비교 결과 파일 실행기(KAN-191). 문서 manifest의 문서를 처리 함수와 같은 엔진 경로로 돌려 정답과 비교한다.

    uv run --frozen python scripts/rfp_candidate_run.py --manifest eval/manifests/rfp-documents-20261029-v1.json \\
        --docs ../RFP_Cleansed_styled_v6 --documents 14 --gold <저장소 밖 정답.json> --out <저장소 밖 결과.json> \\
        --run-label REMEASURE

- 원문은 manifest의 sha256과 같을 때만 읽는다. 결과·정답은 저장소 밖에만 두고, 이미 있는 결과 파일은 덮어쓰지 않는다.
- 평가 문서(split=evaluation, 10·11·12·18번)는 모델·프롬프트를 고정한 뒤 공식 측정에만 쓴다. `--confirm-evaluation`이
  없으면 모델을 부르기 전에 멈춘다. 첫 실행은 `--run-label FIRST`(공식), 결함을 고친 뒤 재측정은 `REMEASURE`(참고값)다.
- 실제 모델 호출은 비용이 든다. `--provider fake`는 요구를 뽑지 않는 고정 응답으로 경로만 확인한다.
- 결과 파일은 `eval_adopt --task rfp` 입력이다(meta.documents, 후보 행의 게이트 키 9개).
"""

import argparse
import fcntl
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 측정 결과에 영향을 주는 코드 폴더. 저장소 루트의 다른 미추적 파일은 판본 표시에 넣지 않는다.
CODE_DIRS = ("backend", "eval", "scripts")
sys.path.insert(0, str(ROOT))

from backend.api.tft.partners.loader import load_profiles  # noqa: E402
from backend.api.tft.rfp.comparison import load_own_products  # noqa: E402
from backend.api.tft.worker_processor import fake_provider  # noqa: E402
from backend.engine.documents.router import extract  # noqa: E402
from backend.engine.rfp.clauses import build_clause_tree  # noqa: E402
from backend.shared.ai import Generation  # noqa: E402
from eval.rfp_candidates import GoldFile, result_file, run_document, summarize  # noqa: E402


def check_inputs(
    manifest: dict, numbers: list[int], gold: GoldFile, out: Path, *, confirm_evaluation: bool
) -> str | None:
    """모델을 부르기 전에 입력을 검사한다(유료 호출 뒤 실패를 막는다).

    Args:
        manifest: 문서 manifest.
        numbers: 돌릴 문서 번호.
        gold: 정답 파일.
        out: 결과 파일 경로.
        confirm_evaluation: 평가 문서를 공식·참고 측정에 쓴다는 명시.

    Returns:
        str | None: 문제가 있으면 고정 코드, 없으면 None.
    """
    if out.exists():
        return "EVAL_OUTPUT_EXISTS"
    if not out.parent.is_dir():
        return "EVAL_OUTPUT_DIR_MISSING"
    resolved = out.resolve()
    if ROOT == resolved or ROOT in resolved.parents:
        return "EVAL_OUTPUT_INSIDE_REPOSITORY"
    entries = {d["document_number"]: d for d in manifest["documents"]}
    if not numbers or len(set(numbers)) != len(numbers):
        return "EVAL_DOCUMENTS_INVALID"
    if any(n not in entries for n in numbers):
        return "EVAL_DOCUMENT_NOT_IN_MANIFEST"
    # 용도가 다른 문서를 한 점수로 섞지 않는다(평가·개발·회귀 15번은 따로 실행).
    if len({entries[n]["split"] for n in numbers}) != 1:
        return "EVAL_SPLIT_MIXED"
    if any(entries[n]["split"] == "evaluation" for n in numbers) and not confirm_evaluation:
        return "EVAL_EVALUATION_NOT_CONFIRMED"
    golden = {d.document_number for d in gold.documents}
    if any(n not in golden for n in numbers):
        return "EVAL_GOLD_DOCUMENT_MISSING"
    return None


def claim_official(
    ledger: Path, manifest_version: str, numbers: list[int], started_at: str, sha: str
) -> str | None:
    """평가 문서 공식(FIRST) 실행 자리를 모델 호출 전에 차지한다. 같은 manifest·문서로 기록이 있으면 거부한다.

    기록은 저장소 밖 JSON Lines 파일이고 줄마다 {"event": STARTED|COMPLETED, ...}다. 확인과 추가를 파일 잠금 안에서
    한 번에 해 두 실행이 동시에 차지하지 못한다. 도중에 실패한 첫 실행도 STARTED로 남으므로, 그 뒤 실행은
    REMEASURE(참고값)다(기획서 3.2: 첫 실행이 공식 점수).

    Args:
        ledger: 공식 실행 기록 파일.
        manifest_version: 문서 manifest 판본.
        numbers: 실행할 문서 번호.
        started_at: 실행 시작 시각.
        sha: 실행한 코드 판본.

    Returns:
        str | None: 이미 기록이 있으면 EVAL_OFFICIAL_ALREADY_RECORDED, 읽을 수 없는 줄이 있으면
            EVAL_OFFICIAL_LEDGER_CORRUPT, 차지했으면 None. 빈 줄은 건너뛴다.
    """
    with ledger.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        for line in handle.read().splitlines():
            if not line.strip():
                continue
            # 공식 판정의 기준 파일이라, 손으로 고치다 깨진 줄은 건너뛰지 않고 멈춘다(#322 리뷰 P3).
            try:
                entry = json.loads(line)
                recorded = entry["manifest_version"], set(entry["document_numbers"])
            except (ValueError, KeyError, TypeError):
                return "EVAL_OFFICIAL_LEDGER_CORRUPT"
            if recorded[0] == manifest_version and recorded[1] & set(numbers):
                return "EVAL_OFFICIAL_ALREADY_RECORDED"
        record = {
            "event": "STARTED",
            "manifest_version": manifest_version,
            "document_numbers": numbers,
            "started_at": started_at,
            "git_sha": sha,
        }
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return None


def complete_official(ledger: Path, claim: dict, result_sha256: str) -> None:
    """공식 실행 결과 파일의 sha256을 기록에 더한다(Jira에 같은 해시를 남겨 공식 결과를 가리킨다)."""
    with ledger.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        record = {**claim, "event": "COMPLETED", "result_sha256": result_sha256}
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_new(out: Path, text: str) -> Path:
    """결과를 새 파일로만 쓴다. 실행 중에 같은 경로가 생겼으면 덮어쓰지 않고 옆 파일에 남긴다(유료 호출 결과 보존).

    Args:
        out: 결과 파일 경로.
        text: 결과 JSON.

    Returns:
        Path: 실제로 쓴 경로. out이 아니면 경로 충돌이 있었던 것이다.
    """
    try:
        with out.open("x", encoding="utf-8") as handle:
            handle.write(text)
        return out
    except FileExistsError:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        other = out.with_name(f"{out.stem}.{stamp}{out.suffix}")
        with other.open("x", encoding="utf-8") as handle:
            handle.write(text)
        return other


def read_verified(directory: Path, entry: dict) -> bytes:
    """manifest 해시와 같은 원문만 돌려준다. 다르면 EVAL_DOCUMENT_HASH_MISMATCH ValueError다."""
    data = (directory / entry["filename"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("EVAL_DOCUMENT_HASH_MISMATCH")
    return data


def git_sha() -> str:
    """실행한 커밋. 코드 폴더에 커밋 안 한 변경·새 파일이 있으면 '+dirty'를 붙인다(공식 점수의 코드 판본 기록)."""
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", *CODE_DIRS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return sha + ("+dirty" if status else "")


def gemini(env_file: Path) -> tuple[str, Callable[[str], Generation]]:
    """평가 스크립트(rfp_requirements_eval)와 같은 설정의 Gemini 구조화 출력 호출."""
    from dotenv import dotenv_values
    from pydantic import SecretStr

    from backend.engine.rfp.requirements import ResponseSchema
    from backend.shared.ai import AISettings, create_client, generate

    values = {k: v or "" for k, v in dotenv_values(env_file).items()}
    settings = AISettings(
        _env_file=None,
        ai_provider="gemini",
        gemini_model=values.get("GEMINI_MODEL") or "gemini-3.6-flash",
        gemini_api_key=SecretStr(values["GEMINI_API_KEY"]),
        gemini_max_output_tokens=16384,
        gemini_timeout_ms=180000,
    )
    client = create_client(settings)
    return settings.gemini_model, lambda text: generate(
        client, settings, text, response_schema=ResponseSchema
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--docs", type=Path, required=True, help="manifest 파일명이 있는 원문 폴더")
    parser.add_argument("--documents", type=int, nargs="+", required=True)
    parser.add_argument("--gold", type=Path, required=True, help="저장소 밖 정답 JSON(GoldFile)")
    parser.add_argument("--out", type=Path, required=True, help="저장소 밖 결과 JSON")
    parser.add_argument("--run-label", choices=["FIRST", "REMEASURE"], required=True)
    parser.add_argument("--confirm-evaluation", action="store_true")
    parser.add_argument(
        "--official-ledger",
        type=Path,
        help="저장소 밖 공식 실행 기록(JSON Lines). 평가 문서 FIRST 실행에 필수",
    )
    parser.add_argument("--provider", choices=["gemini", "fake"], default="gemini")
    parser.add_argument("--candidate-name", help="결과 후보 이름(기본 rfp-<모델>)")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.integrations")
    parser.add_argument("--today", type=date.fromisoformat, help="인증 유효기한 기준일(기본 오늘)")
    args = parser.parse_args(argv)

    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    manifest = json.loads(args.manifest.read_text())
    gold = GoldFile.model_validate_json(args.gold.read_text())
    problem = check_inputs(
        manifest, args.documents, gold, args.out, confirm_evaluation=args.confirm_evaluation
    )
    entries = {d["document_number"]: d for d in manifest["documents"]}
    official = (
        not problem
        and args.run_label == "FIRST"
        and entries[args.documents[0]]["split"] == "evaluation"
    )
    if official:
        ledger = args.official_ledger
        if ledger is None:
            problem = "EVAL_OFFICIAL_LEDGER_REQUIRED"
        elif ROOT == ledger.resolve() or ROOT in ledger.resolve().parents:
            problem = "EVAL_OFFICIAL_LEDGER_INSIDE_REPOSITORY"
        elif not ledger.parent.is_dir():
            problem = "EVAL_OFFICIAL_LEDGER_DIR_MISSING"
    if problem:
        print(f"{problem}: 모델을 부르지 않았다.", file=sys.stderr)
        return 1
    # 원문 해시를 모델 호출 전에 모두 확인한다.
    trees = {
        n: build_clause_tree(
            extract(read_verified(args.docs, entries[n]), source_name=entries[n]["filename"])
        )
        for n in args.documents
    }
    sha = git_sha()
    # 모델 설정·자사 제품·협력사 프로필을 먼저 다 읽는다. 여기서 실패하면 공식 자리를 쓰지 않는다(#322 리뷰 P2).
    provider: Callable[[str], Generation]
    if args.provider == "fake":
        model, provider = "fake", fake_provider
    else:
        model, provider = gemini(args.env_file)
    products, profiles = load_own_products(), load_profiles()
    by_number = {d.document_number: d for d in gold.documents}
    today = args.today or date.today()
    claim: dict | None = None
    if official:
        # 준비가 끝난 뒤, 첫 모델 호출 직전에 공식 자리를 차지한다.
        refused = claim_official(
            args.official_ledger, manifest["manifest_version"], args.documents, started_at, sha
        )
        if refused:
            hint = {
                "EVAL_OFFICIAL_ALREADY_RECORDED": "이 문서의 공식 실행이 이미 있다. "
                "--run-label REMEASURE(참고값)로 돌린다.",
                "EVAL_OFFICIAL_LEDGER_CORRUPT": "공식 실행 기록에 읽을 수 없는 줄이 있다. 사람이 확인한다.",
            }[refused]
            print(f"{refused}: {hint} 모델을 부르지 않았다.", file=sys.stderr)
            return 1
        claim = {
            "manifest_version": manifest["manifest_version"],
            "document_numbers": args.documents,
            "started_at": started_at,
            "git_sha": sha,
        }
    runs = []
    for n in args.documents:
        run = run_document(trees[n], by_number[n], provider, products, profiles, today)
        print(f"D{n}: 절 {run.section_outcomes} 요구 {len(run.predicted)}", flush=True)
        runs.append(run)
    summary = summarize(gold, runs, args.run_label)
    data = result_file(
        summary,
        runs,
        name=args.candidate_name or f"rfp-{model}",
        model=model,
        run_label=args.run_label,
        split=entries[args.documents[0]]["split"],
        manifest_version=manifest["manifest_version"],
        gold_version=gold.gold_version,
        started_at=started_at,
        git_sha=sha,
    )
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    written = write_new(args.out, text)
    if claim is not None:
        complete_official(
            args.official_ledger, claim, hashlib.sha256(text.encode("utf-8")).hexdigest()
        )
    meta = data["meta"]
    print(
        f"{args.run_label}({'공식' if meta['official'] else '참고값'}) 문서 {meta['documents']}건 | "
        + " | ".join(f"{k} {v}" for k, v in meta["counts"].items())
    )
    print(f"결과: {written}")
    if written != args.out:
        print(
            f"EVAL_OUTPUT_EXISTS: 실행 중 {args.out}가 생겨 덮어쓰지 않고 위 경로에 남겼다.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
