"""C/S 분류 후보 비교 실행기(KAN-144). 기본은 dry-run이며 네트워크를 쓰지 않는다.

dry-run은 대상 건수·근사 입력 토큰·단가표 기준 예상 비용 상한을 후보별로 출력한다. 실제 호출은 승인 뒤
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
import random
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
from backend.engine.cs.providers import gemini_classification_provider
from backend.shared.ai import AISettings, Generation, create_client, load_price_table
from eval.cs_candidates import Candidate, run_candidates
from eval.cs_gold_locks import GOLD_LOCKS
from tests.cs.cs_dev_data import load_dev_tickets

Provider = Callable[[ClassificationInput], Generation]
ProviderFactory = Callable[[dict[str, str], frozenset[str], AISettings], Provider]
Sensitive = Mapping[str, Literal["SERIAL", "PERSONAL"]]
TOKEN_CHARS = 4


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


def load_gold(path: Path, metric: str, ids: set[str]) -> dict[str, str]:
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


def load_specs(path: Path) -> list[dict[str, str]]:
    """후보 목록 JSON([{name, model, prompt_version}])을 읽는다.

    Args:
        path: 후보 JSON.

    Returns:
        list[dict[str, str]]: 이름이 고유한 후보 명세.
    """
    specs = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(specs, list)
        or not specs
        or any(
            not isinstance(spec, dict)
            or set(spec) != {"name", "model", "prompt_version"}
            or not all(isinstance(value, str) and value for value in spec.values())
            for spec in specs
        )
        or len({spec["name"] for spec in specs}) != len(specs)
    ):
        raise ValueError("CS_CANDIDATES_INVALID")
    return specs


class _RecordingModels:
    """generate_content가 프롬프트 길이만 기록하고 빈 응답을 낸다(네트워크 없음)."""

    def __init__(self) -> None:
        self.lengths: list[int] = []

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
        return SimpleNamespace(usage_metadata=None, text="", model_version=None, candidates=[])


def dry_run(
    records: Mapping[str, Mapping[str, object]],
    specs: list[dict[str, str]],
    codes: frozenset[str],
    sensitive: Sensitive,
    settings: AISettings,
    prices: Mapping[str, Any],
) -> list[dict[str, object]]:
    """후보마다 실제 어댑터로 프롬프트를 만들어 길이·근사 토큰·예상 비용 상한을 계산한다.

    Args:
        records: 대상 레코드.
        specs: 후보 명세.
        codes: 허용 정보 코드.
        sensitive: 민감값과 마스킹 종류.
        settings: AI 설정(출력 토큰 상한).
        prices: load_price_table 결과.

    Returns:
        list[dict]: 후보별 건수(호출·보류), 프롬프트 문자 수, 근사 입력 토큰(문자 수/4 올림),
            예상 입력 비용·출력 비용 상한(max_output_tokens × 호출 수)·합계. 단가가 없으면 비용은 None이다.
    """
    rows = []
    for spec in specs:
        recorder = _RecordingModels()
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
        chars = sum(recorder.lengths)
        tokens = math.ceil(chars / TOKEN_CHARS)
        calls = len(recorder.lengths)
        price = prices.get(spec["model"])
        input_cost = output_cost = None
        if price is not None:
            input_cost = Decimal(tokens) * price.input_per_million / Decimal(1_000_000)
            output_cost = (
                Decimal(calls * settings.gemini_max_output_tokens)
                * price.output_per_million
                / Decimal(1_000_000)
            )
        rows.append(
            {
                **spec,
                "calls": calls,
                "held": held,
                "prompt_chars": chars,
                "approx_input_tokens": tokens,
                "max_output_tokens_per_call": settings.gemini_max_output_tokens,
                "currency": price.currency if price else None,
                "estimated_input_cost": None if input_cost is None else format(input_cost, "f"),
                "output_cost_upper": None if output_cost is None else format(output_cost, "f"),
                "total_cost_upper": (
                    None if input_cost is None else format(input_cost + output_cost, "f")
                ),
            }
        )
    return rows


def default_factory(settings: AISettings) -> ProviderFactory:
    """실제 Gemini 클라이언트를 한 번 만들고 후보마다 분류 어댑터를 낸다.

    Args:
        settings: 환경변수에서 읽은 AI 설정.

    Returns:
        ProviderFactory: (명세, 코드, 설정) → 공급자.
    """
    client = create_client(settings)

    def factory(spec: dict[str, str], codes: frozenset[str], config: AISettings) -> Provider:
        return gemini_classification_provider(
            client,
            config,
            model=spec["model"],
            allowed_info_codes=codes,
            prompt_version=spec["prompt_version"],
        )

    return factory


def _git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def execute(
    records: Mapping[str, Mapping[str, object]],
    gold: Mapping[str, Mapping[str, str]],
    specs: list[dict[str, str]],
    codes: frozenset[str],
    sensitive: Sensitive,
    settings: AISettings,
    prices: Mapping[str, Any],
    factory: ProviderFactory,
) -> dict[str, object]:
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

    Returns:
        dict: run_candidates 결과와 meta(모델·판본·지문·건수·표본 ID sha256·시각·git sha). 원문·키 없음.
    """
    started = datetime.now(UTC)
    candidates = [
        Candidate(
            name=spec["name"],
            provider=factory(spec, codes, settings),
            model=spec["model"],
            prompt_version=spec["prompt_version"],
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
    )
    return {
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
            "git_sha": _git_sha(),
        },
        "result": result,
    }


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
    args = parser.parse_args(argv)
    if args.limit < 1 or (args.execute and args.out is None):
        print("CS_RUN_INVALID_ARGS: --limit은 1 이상, --execute에는 --out이 필요합니다.")
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
            print("dry-run: 네트워크 호출 없음. 토큰은 문자 수/4의 근사, 출력 비용은 상한이다.")
            print(json.dumps({"targets": len(records), "candidates": rows}, ensure_ascii=False))
            return 0
        output = execute(
            records,
            gold,
            specs,
            codes,
            sensitive,
            settings,
            prices,
            factory or default_factory(settings),
        )
    except SplitBlocked:
        print("CS_RUN_TEST_SPLIT: test 분할은 최종 1회 사용 원칙이라 실행하지 않습니다.")
        return 2
    except (ValueError, OSError, KeyError, TypeError, csv.Error):
        print("CS_RUN_FAILED: manifest·정답 잠금·코드북·후보 파일과 환경변수를 확인하세요.")
        return 1
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"targets": len(records), "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
