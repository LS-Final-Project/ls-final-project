"""C/S 분류 후보 비교 실행기(KAN-144). 기본은 dry-run이며 네트워크를 쓰지 않는다.

dry-run은 대상 건수·근사 입력 토큰(문자/4)·입력 토큰 상한(UTF-8 바이트 수)과 단가표 기준 예상 비용·비용 상한을
후보별로 출력한다. 모두 근사이며 실제 과금은 실행 후 usage로 확인한다. 실제 호출은 승인 뒤
--execute로만 한다. 키는 환경변수(GEMINI_API_KEY)에서만 읽는다. 스크립트가 .env를 직접 읽지 않으므로
셸에서 먼저 올린다: set -a; . ./.env; set +a. 레코드는 개발 manifest(load_dev_tickets)로만 받고, test
분할은 최종 1회 사용 원칙에 따라 종료 코드 2로 거부한다. 정답 파일은 eval/cs_gold_locks의 SHA·열과 같아야
한다. 결과에는 원문·raw_response·키·표본 ID를 넣지 않는다(표본 ID는 sha256만).
"""

import argparse
import csv
import hashlib
import io
import json
import math
import os
import random
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from backend.engine.cs.classification import ClassificationInput, build_input
from backend.engine.cs.prompt_registry import prompt_fingerprint
from backend.engine.cs.providers import GenerationKnobs, gemini_classification_provider
from backend.shared.ai import AISettings, Generation, create_client, load_price_table
from eval.cs_candidates import Candidate, run_candidates
from eval.cs_gold_locks import GOLD_LOCKS
from tests.cs.cs_dev_data import load_dev_tickets

Provider = Callable[[ClassificationInput], Generation]
ProviderFactory = Callable[[dict[str, Any], frozenset[str], AISettings], Provider]
Sensitive = Mapping[str, Literal["SERIAL", "PERSONAL"]]
TOKEN_CHARS = 4
# 라벨 파일 이름으로 쓰는 후보 이름. 경로 구분자·상대 경로를 막는다.
LABEL_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")


class SplitBlocked(ValueError):
    """test 분할 행이 있어 실행하지 않는다."""


def load_targets(manifest: Path, limit: int, seed: int) -> dict[str, dict[str, object]]:
    """manifest의 개발 문의에서 대상 표본을 결정적으로 고른다.

    Args:
        manifest: 개발 manifest. load_dev_tickets가 SHA·분할·행 수를 검사한다.
        limit: 대상 상한(1 이상).
        seed: ID 정렬 뒤 셔플 시드.

    Returns:
        dict: 대상 ID → build_input 레코드(channel·body_language·subject·body). test 분할이면
            SplitBlocked다.
    """
    entry = json.loads(manifest.read_text(encoding="utf-8")).get("tickets") or {}
    if "test" in (entry.get("splits") or []):
        raise SplitBlocked
    rows = load_dev_tickets(manifest)
    if any(row["data_split"] == "test" for row in rows):
        raise SplitBlocked
    ids = sorted(row["ticket_id"] for row in rows)
    random.Random(seed).shuffle(ids)
    chosen = set(ids[:limit])
    return {
        row["ticket_id"]: {
            "channel": row.get("channel"),
            "body_language": row.get("body_language"),
            "subject": row.get("inquiry_subject") or None,
            "body": row.get("inquiry_body"),
        }
        for row in rows
        if row["ticket_id"] in chosen
    }


def load_gold(path: Path, metric: str, ids: set[str]) -> dict[str, Any]:
    """잠긴 정답 파일에서 대상 ID의 정답만 읽는다.

    Args:
        path: 정답 CSV(저장소 밖).
        metric: category 또는 urgency.
        ids: 대상 ID 집합.

    Returns:
        dict: 대상 ID → 정답 코드. SHA·열이 잠금과 다르거나 대상 정답이 없으면 ValueError다.
    """
    lock = GOLD_LOCKS[metric]
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != lock.gold_sha256:
        raise ValueError("CS_GOLD_LOCK_MISMATCH")
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    if tuple(reader.fieldnames or ()) != lock.columns:
        raise ValueError("CS_GOLD_COLUMNS")
    key, value = lock.columns
    gold = {row[key]: row[value] for row in reader if row[key] in ids}
    if set(gold) != ids:
        raise ValueError("CS_GOLD_MISSING_TARGETS")
    return gold


def load_codes(path: Path, column: str) -> frozenset[str]:
    """검수된 정보 코드북 CSV에서 코드 열을 읽는다.

    Args:
        path: 코드북 CSV.
        column: 코드가 든 열 이름(추측하지 않고 인자로 받는다).

    Returns:
        frozenset[str]: 비지 않은 코드 집합.
    """
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"), newline=""))
    if column not in (reader.fieldnames or ()):
        raise ValueError("CS_CODEBOOK_COLUMN")
    codes = frozenset(row[column].strip() for row in reader if row[column].strip())
    if not codes:
        raise ValueError("CS_CODEBOOK_EMPTY")
    return codes


KNOB_FIELDS = ("thinking_budget", "thinking_level", "max_output_tokens")


def load_specs(path: Path) -> list[dict[str, Any]]:
    """후보 목록 JSON([{name, model, prompt_version, thinking_budget?, thinking_level?, max_output_tokens?}])을 읽는다.

    Args:
        path: 후보 JSON.

    Returns:
        list[dict[str, Any]]: 이름이 고유한 후보 명세. 생성 조절 항목은 GenerationKnobs 규칙으로 검사한다.
    """
    specs = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(specs, list)
        or not specs
        or any(
            not isinstance(spec, dict)
            or not {"name", "model", "prompt_version"} <= set(spec)
            or not set(spec) <= {"name", "model", "prompt_version", *KNOB_FIELDS}
            or not all(
                isinstance(spec[key], str) and spec[key]
                for key in ("name", "model", "prompt_version")
            )
            for spec in specs
        )
        or len({spec["name"] for spec in specs}) != len(specs)
    ):
        raise ValueError("CS_CANDIDATES_INVALID")
    for spec in specs:
        knobs_of(spec)
    return specs


def knob_fields(spec: Mapping[str, Any]) -> dict[str, int | str]:
    """후보 명세에서 값이 있는 생성 조절 항목만 꺼낸다.

    Args:
        spec: 후보 명세.

    Returns:
        dict: thinking_budget·thinking_level·max_output_tokens 중 있는 것.
    """
    return {key: spec[key] for key in KNOB_FIELDS if spec.get(key) is not None}


def knobs_of(spec: Mapping[str, Any]) -> GenerationKnobs:
    """후보 명세의 생성 조절 항목을 어댑터 인자로 만든다.

    Args:
        spec: 후보 명세.

    Returns:
        GenerationKnobs: 검사한 조절 항목. 위반은 CS_INVALID_GENERATION ValueError다.
    """
    return GenerationKnobs(**knob_fields(spec))  # type: ignore[arg-type]


class RecordingModels:
    """generate_content가 프롬프트 길이만 기록하고 빈 응답을 낸다(네트워크 없음)."""

    def __init__(self) -> None:
        self.lengths: list[int] = []
        self.bytes: list[int] = []

    def generate_content(self, *, model: str, contents: str, config: object) -> object:
        """공급자 어댑터가 만든 프롬프트 길이를 기록한다.

        Args:
            model: 요청 모델.
            contents: 완성 프롬프트.
            config: 생성 설정(쓰지 않는다).

        Returns:
            object: generate가 읽는 최소 응답(사용량·후보 없음).
        """
        self.lengths.append(len(contents))
        self.bytes.append(len(contents.encode("utf-8")))
        return SimpleNamespace(usage_metadata=None, text="", model_version=None, candidates=[])


def dry_run(
    records: Mapping[str, Mapping[str, object]],
    specs: list[dict[str, Any]],
    codes: frozenset[str],
    sensitive: Sensitive,
    settings: AISettings,
    prices: Mapping[str, Any],
) -> list[dict[str, object]]:
    """후보마다 실제 어댑터로 프롬프트를 만들어 길이·근사 토큰·토큰 상한·예상 비용을 계산한다.

    문자/4는 영어 기준 근사라 한국어·JSON 스키마에서는 과소다. 상한은 프롬프트 UTF-8 바이트 수로 잡는다(바이트
    폴백 토크나이저는 바이트보다 많은 토큰을 만들지 않는다). 공급자 count_tokens는 네트워크 호출이라 쓰지 않는다.

    Args:
        records: 대상 레코드.
        specs: 후보 명세.
        codes: 허용 정보 코드.
        sensitive: 민감값과 마스킹 종류.
        settings: AI 설정(출력 토큰 상한).
        prices: load_price_table 결과.

    Returns:
        list[dict]: 후보별 건수(호출·보류), 프롬프트 문자·바이트 수, 근사 입력 토큰(문자/4 올림), 입력 토큰
            상한(바이트 수), 근사 입력 비용, 입력 비용 상한, 출력 비용 상한(max_output_tokens × 호출 수),
            합계 상한(입력 상한 + 출력 상한). 단가가 없으면 비용은 None이다. 모두 근사이며 실제 과금은 실행 후
            usage로 확인한다.
    """
    rows = []
    for spec in specs:
        recorder = RecordingModels()
        provider = gemini_classification_provider(
            SimpleNamespace(models=recorder),  # type: ignore[arg-type]
            settings,
            model=spec["model"],
            allowed_info_codes=codes,
            prompt_version=spec["prompt_version"],
        )
        held = 0
        for record in records.values():
            try:
                payload = build_input(record, sensitive_values=sensitive).payload
            except ValueError:
                held += 1
                continue
            provider(payload)
        rows.append(
            {
                **spec,
                "held": held,
                **estimate(
                    recorder.lengths,
                    recorder.bytes,
                    spec["model"],
                    settings,
                    prices,
                    spec.get("max_output_tokens"),
                ),
            }
        )
    return rows


def estimate(
    lengths: list[int],
    sizes: list[int],
    model: str,
    settings: AISettings,
    prices: Mapping[str, Any],
    max_output_tokens: int | None = None,
) -> dict[str, object]:
    """프롬프트 문자·바이트 수로 근사 토큰·토큰 상한·예상 비용을 계산한다(분류·초안 dry-run 공용).

    문자/4는 영어 기준 근사라 한국어·JSON 스키마에서는 과소다. 상한은 UTF-8 바이트 수로 잡는다(바이트 폴백
    토크나이저는 바이트보다 많은 토큰을 만들지 않는다).

    Args:
        lengths: 호출마다의 프롬프트 문자 수.
        sizes: 호출마다의 프롬프트 UTF-8 바이트 수.
        model: 단가표에서 찾을 모델 ID.
        settings: AI 설정(기본 출력 토큰 상한).
        prices: load_price_table 결과.
        max_output_tokens: 후보의 출력 상한. None이면 settings 값이다. thinking 토큰을 포함한 상한이라 thinking
            예산을 따로 더하지 않는다.

    Returns:
        dict: 호출 수, 문자·바이트 수, 근사 입력 토큰(문자/4 올림), 입력 토큰 상한, 근사 입력 비용, 입력 비용
            상한, 출력 비용 상한(max_output_tokens × 호출 수), 합계 상한, 안내문. 단가가 없으면 비용은 None이다.
    """
    limit = max_output_tokens or settings.gemini_max_output_tokens
    chars = sum(lengths)
    tokens = math.ceil(chars / TOKEN_CHARS)
    upper = sum(sizes)
    calls = len(lengths)
    price = prices.get(model)
    input_cost = input_upper = output_cost = None
    if price is not None:
        input_cost = Decimal(tokens) * price.input_per_million / Decimal(1_000_000)
        input_upper = Decimal(upper) * price.input_per_million / Decimal(1_000_000)
        output_cost = Decimal(calls * limit) * price.output_per_million / Decimal(1_000_000)
    return {
        "calls": calls,
        "prompt_chars": chars,
        "prompt_bytes": upper,
        "approx_input_tokens": tokens,
        "input_tokens_upper": upper,
        "max_output_tokens_per_call": limit,
        "currency": price.currency if price else None,
        "estimated_input_cost": None if input_cost is None else format(input_cost, "f"),
        "input_cost_upper": None if input_upper is None else format(input_upper, "f"),
        "output_cost_upper": None if output_cost is None else format(output_cost, "f"),
        "total_cost_upper": (
            None
            if input_upper is None or output_cost is None
            else format(input_upper + output_cost, "f")
        ),
        "note": (
            "근사값이다. 실제 과금은 실행 후 usage로 확인한다. 출력 상한은 max_output_tokens 기준이며 "
            "thinking 토큰을 포함한다."
        ),
    }


def prepare_output(path: Path) -> None:
    """유료 호출 전에 결과 파일을 만들 수 있는지 확인한다(빈 파일 생성 시도).

    Args:
        path: 결과 JSON 경로.

    Returns:
        None: 만들 수 없으면 OSError다.
    """
    with path.open("a", encoding="utf-8"):
        pass


def default_factory(settings: AISettings) -> ProviderFactory:
    """실제 Gemini 클라이언트를 한 번 만들고 후보마다 분류 어댑터를 낸다.

    Args:
        settings: 환경변수에서 읽은 AI 설정.

    Returns:
        ProviderFactory: (명세, 코드, 설정) → 공급자.
    """
    client = create_client(settings)

    def factory(spec: dict[str, Any], codes: frozenset[str], config: AISettings) -> Provider:
        return gemini_classification_provider(
            client,
            config,
            model=spec["model"],
            allowed_info_codes=codes,
            prompt_version=spec["prompt_version"],
            generation=knobs_of(spec),
        )

    return factory


def label_path(directory: Path, name: str) -> Path:
    """후보의 예측 라벨 파일 경로를 만든다.

    Args:
        directory: --labels-out 디렉터리.
        name: 후보 이름(영숫자·._- 1~64자, '.'·'..' 불가).

    Returns:
        Path: <directory>/<name>.labels.json. 이름이 규칙 밖이면 ValueError다.
    """
    if not LABEL_NAME.fullmatch(name) or name in {".", ".."}:
        raise ValueError("CS_LABEL_NAME")
    return directory / f"{name}.labels.json"


def write_private(path: Path, data: bytes) -> None:
    """파일을 소유자만 읽고 쓰는 0600 권한으로 쓴다(빈 바이트면 만들기만 한다).

    Args:
        path: 쓸 경로.
        data: 내용.

    Returns:
        None: 쓰지 못하거나 경로가 심볼릭 링크면 OSError다.
    """
    # 심볼릭 링크를 따라가지 않는다(비공개 라벨이 링크 대상 위치로 새거나 다른 파일을 덮어쓰지 않게).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
    finally:
        os.close(fd)


def git_sha() -> str | None:
    """실행한 코드의 git 커밋을 읽는다.

    Returns:
        str | None: HEAD SHA. git이 없거나 저장소 밖이면 None이다.
    """
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def execute(
    records: Mapping[str, Mapping[str, object]],
    gold: Mapping[str, Mapping[str, str]],
    specs: list[dict[str, Any]],
    codes: frozenset[str],
    sensitive: Sensitive,
    settings: AISettings,
    prices: Mapping[str, Any],
    factory: ProviderFactory,
    labels: bool = False,
) -> tuple[dict[str, object], dict[str, bytes]]:
    """하네스로 실제 비교를 돌리고 결과에 실행 메타를 붙인다.

    Args:
        records: 대상 레코드.
        gold: {"category": …, "urgency": …} 대상 정답.
        specs: 후보 명세.
        codes: 허용 정보 코드.
        sensitive: 민감값.
        settings: AI 설정.
        prices: 단가표.
        factory: 후보별 공급자를 만드는 함수(테스트는 fake로 교체).
        labels: True면 후보별 예측 라벨 파일 내용을 만든다.

    Returns:
        tuple: (결과, 라벨). 결과는 run_candidates 결과와 meta(모델·판본·지문·건수·표본 ID sha256·시각·git sha,
            라벨을 만들면 라벨 파일 sha256)이며 원문·키·사례별 예측이 없다. 라벨은 후보 이름 → 파일 바이트이고
            내용은 {ticket_id: ClassificationOutput dict(VALID만) | null}(키 정렬)이다. 사례 식별자가 들어간
            비공개 산출물이라 raw_response·원문·usage는 넣지 않는다.
    """
    started = datetime.now(UTC)
    candidates = [
        Candidate(
            name=spec["name"],
            provider=factory(spec, codes, settings),
            model=spec["model"],
            prompt_version=spec["prompt_version"],
            knobs=knob_fields(spec),
        )
        for spec in specs
    ]
    result = run_candidates(
        records,
        gold=gold,
        candidates=candidates,
        sensitive_values=sensitive,
        allowed_info_codes=codes,
        price_table=dict(prices),
        return_attempts=labels,
    )
    attempts = result.pop("attempts", {})
    files = {
        name: json.dumps(
            {
                ticket_id: attempt.prediction.model_dump()
                if attempt.outcome == "VALID" and attempt.prediction is not None
                else None
                for ticket_id, attempt in runs.items()
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        for name, runs in attempts.items()
    }
    output: dict[str, object] = {
        "meta": {
            "candidates": [
                {
                    **spec,
                    "prompt_fingerprint": prompt_fingerprint(
                        "classification", spec["prompt_version"]
                    ),
                }
                for spec in specs
            ],
            "targets": len(records),
            "target_ids_sha256": hashlib.sha256("\n".join(sorted(records)).encode()).hexdigest(),
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "git_sha": git_sha(),
            **(
                {
                    "label_files": {
                        name: hashlib.sha256(data).hexdigest() for name, data in files.items()
                    }
                }
                if labels
                else {}
            ),
        },
        "result": result,
    }
    return output, files


def main(argv: list[str] | None = None, factory: ProviderFactory | None = None) -> int:
    """인자를 읽어 dry-run 표를 출력하거나 --execute로 결과 JSON을 쓴다.

    Args:
        argv: 명령행 인자(테스트 주입용).
        factory: 실제 공급자 대신 쓸 공급자 함수(테스트 주입용).

    Returns:
        int: 0 성공, 2 test 분할 거부, 1 입력 오류. 오류 원문·키는 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gold-category", type=Path, required=True)
    parser.add_argument("--gold-urgency", type=Path, required=True)
    parser.add_argument("--allowed-info-codes", type=Path, required=True)
    parser.add_argument("--info-code-column", required=True)
    parser.add_argument("--sensitive-values", type=Path)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--labels-out",
        type=Path,
        help="후보별 예측 라벨(<name>.labels.json, 0600) 디렉터리. 사례 식별자가 포함된 비공개 산출물",
    )
    args = parser.parse_args(argv)
    if (
        args.limit < 1
        or (args.execute and args.out is None)
        or (args.labels_out is not None and not args.execute)
    ):
        print(
            "CS_RUN_INVALID_ARGS: --limit은 1 이상, --execute에는 --out이 필요하고 "
            "--labels-out은 --execute와 함께만 쓴다."
        )
        return 1
    try:
        records = load_targets(args.manifest, args.limit, args.seed)
        ids = set(records)
        gold = {
            "category": load_gold(args.gold_category, "category", ids),
            "urgency": load_gold(args.gold_urgency, "urgency", ids),
        }
        codes = load_codes(args.allowed_info_codes, args.info_code_column)
        specs = load_specs(args.candidates)
        sensitive = (
            json.loads(args.sensitive_values.read_text(encoding="utf-8"))
            if args.sensitive_values
            else {}
        )
        settings = AISettings()
        prices = load_price_table()
        if not args.execute:
            rows = dry_run(records, specs, codes, sensitive, settings, prices)
            print(
                "dry-run: 네트워크 호출 없음. 토큰은 문자/4 근사와 UTF-8 바이트 상한이며 실제 과금은 "
                "실행 후 usage로 확인한다."
            )
            print(json.dumps({"targets": len(records), "candidates": rows}, ensure_ascii=False))
            return 0
        prepare_output(args.out)
        if args.labels_out is not None:
            for spec in specs:
                write_private(label_path(args.labels_out, spec["name"]), b"")
        output, labels = execute(
            records,
            gold,
            specs,
            codes,
            sensitive,
            settings,
            prices,
            factory or default_factory(settings),
            labels=args.labels_out is not None,
        )
    except SplitBlocked:
        print("CS_RUN_TEST_SPLIT: test 분할은 최종 1회 사용 원칙이라 실행하지 않습니다.")
        return 2
    except (ValueError, OSError, KeyError, TypeError, csv.Error):
        print("CS_RUN_FAILED: manifest·정답 잠금·코드북·후보 파일과 환경변수를 확인하세요.")
        return 1
    try:
        # 라벨을 먼저 쓰고 결과(라벨 sha256 포함)를 마지막에 써서, 실패 시 해시가 맞지 않는 결과가 남지 않게 한다.
        for name, data in labels.items():
            write_private(label_path(args.labels_out, name), data)
        args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        print(
            "CS_RUN_OUTPUT_FAILED: 실행은 끝났지만 결과 파일을 쓰지 못했습니다. --out 경로를 확인하세요."
        )
        return 1
    print(json.dumps({"targets": len(records), "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
