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
from typing import Any, Literal, cast

from pydantic import BaseModel

from backend.api.platform.evaluations.smoke_worker import attempt_cost
from backend.engine.cs.classification import (
    INPUT_VERSION,
    INPUT_VERSION_STRIPPED,
    ClassificationInput,
    ClassificationOutput,
    build_input,
)
from backend.engine.cs.classification_call import ClassificationAttempt
from backend.engine.cs.drafting_call import DraftAttempt
from backend.engine.cs.prompt_registry import prompt_fingerprint
from backend.engine.cs.providers import GenerationKnobs, gemini_classification_provider
from backend.engine.cs.verification_call import VerificationAttempt
from backend.shared.ai import AISettings, Generation, Usage, create_client, load_price_table
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
            or not set(spec)
            <= {"name", "model", "prompt_version", "verification_max_output_tokens", *KNOB_FIELDS}
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
        # 검증 상한도 초안 상한과 같은 규칙이다. 없으면 초안 값을 쓴다.
        if "verification_max_output_tokens" in spec:
            knobs_of({**spec, "max_output_tokens": spec["verification_max_output_tokens"]})
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
    strip_phone_annotation: bool = False,
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
        strip_phone_annotation: 분석용 PHONE 표기 제거(기본 False).

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
                payload = build_input(
                    record,
                    sensitive_values=sensitive,
                    strip_phone_annotation=strip_phone_annotation,
                ).payload
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


def label_path(directory: Path, name: str, kind: str = "labels") -> Path:
    """후보의 비공개 산출물(예측 라벨·사례별 감사) 파일 경로를 만든다.

    Args:
        directory: --labels-out 또는 --audit-out 디렉터리.
        name: 후보 이름(영숫자·._- 1~64자, '.'·'..' 불가).
        kind: 파일 종류(labels 또는 audit).

    Returns:
        Path: <directory>/<name>.<kind>.json. 이름이 규칙 밖이면 ValueError다.
    """
    if not LABEL_NAME.fullmatch(name) or name in {".", ".."}:
        raise ValueError("CS_LABEL_NAME")
    return directory / f"{name}.{kind}.json"


AUDIT_KINDS = ("audit", "retry-audit")
AuditFiles = dict[tuple[str, str], bytes]


def audit_payloads(
    attempts: Mapping[str, Mapping[str, ClassificationAttempt | DraftAttempt]],
    retries: Mapping[str, Mapping[str, ClassificationAttempt | DraftAttempt]],
) -> AuditFiles:
    """후보별 사례 감사 파일 내용(최초 시도 audit, 형식 위반 재호출 retry-audit)을 만든다.

    Args:
        attempts: 후보 이름 → {사례 ID: 최초 시도}.
        retries: 후보 이름 → {사례 ID: attempt 2}. 재호출이 없으면 빈 dict다.

    Returns:
        dict: (후보 이름, 종류) → 파일 바이트. 내용은 {사례 ID: audit_entry}(키 정렬)이고 retry-audit은 다시 부른
            사례만 담는다(없으면 {}). 예측·원문·정답은 없다.
    """
    return {
        (name, kind): json.dumps(
            {case_id: audit_entry(attempt) for case_id, attempt in runs.items()},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        for kind, source in zip(AUDIT_KINDS, (attempts, retries), strict=True)
        for name, runs in source.items()
    }


LABEL_KINDS = ("labels", "final-labels")


def audit_meta(
    audits: AuditFiles, kinds: tuple[str, ...] = AUDIT_KINDS
) -> dict[str, dict[str, str]]:
    """결과 meta에 남길 비공개 산출물(감사·라벨) 파일 sha256.

    Args:
        audits: (후보 이름, 종류) → 파일 바이트(audit_payloads·label_payloads 결과).
        kinds: 종류 목록. 기본은 감사 두 종류다.

    Returns:
        dict: <종류>_files(후보 이름 → sha256). 예: audit_files·retry_audit_files, label_files·
            final_label_files('labels'는 기존 키 이름 label_files로 남긴다).
    """
    return {
        f"{kind.removesuffix('s').replace('-', '_')}_files": {
            name: hashlib.sha256(data).hexdigest()
            for (name, file_kind), data in audits.items()
            if file_kind == kind
        }
        for kind in kinds
    }


def label_payloads(
    attempts: Mapping[str, Mapping[str, ClassificationAttempt]],
    retries: Mapping[str, Mapping[str, ClassificationAttempt]],
) -> AuditFiles:
    """후보별 예측 라벨(최초 시도 labels, 최종 시도 final-labels) 파일 내용을 만든다.

    Args:
        attempts: 후보 이름 → {사례 ID: 최초 시도}.
        retries: 후보 이름 → {사례 ID: attempt 2}.

    Returns:
        dict: (후보 이름, 종류) → 파일 바이트. 내용은 {ticket_id: ClassificationOutput dict(VALID만) | null}
            (키 정렬)이다. final-labels는 시도 2가 있는 사례만 그 예측이고 나머지는 최초와 같다.
    """

    def encode(runs: Mapping[str, ClassificationAttempt]) -> bytes:
        return json.dumps(
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

    return {
        (name, kind): encode(runs if kind == "labels" else {**runs, **retries.get(name, {})})
        for name, runs in attempts.items()
        for kind in LABEL_KINDS
    }


def audit_entry(
    attempt: ClassificationAttempt | DraftAttempt | VerificationAttempt,
) -> dict[str, object]:
    """시도 하나의 사례별 감사 항목. evaluation.case_attempts 열과 1:1이며 원문·응답·예측·정답은 없다.

    Args:
        attempt: 첫 분류 시도 또는 첫 초안 시도(cs_draft_run). 두 시도는 쓰는 필드가 같다.

    Returns:
        dict: outcome·error_code·http_status·model(요청)·model_version(응답)·input_version·input_sha256·
            usage(토큰 5종, 미보고 null)·latency_ms·finish_reasons·attempts(공급자 호출 수)·cost_usd(십진
            문자열)·pricing_version. 비용은 워커와 같은 attempt_cost 규칙이며 계산할 수 없으면 둘 다 null이다.
    """
    # attempt_cost는 requested_model·usage만 읽으며 두 시도 타입의 그 필드는 이름·타입이 같다.
    cost, pricing_version = attempt_cost(cast(ClassificationAttempt, attempt))
    usage = attempt.usage
    return {
        "outcome": attempt.outcome,
        "error_code": attempt.error_code,
        "http_status": attempt.http_status,
        "model": attempt.requested_model,
        "model_version": attempt.model_version,
        "input_version": attempt.input_version,
        "input_sha256": attempt.input_sha256,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "thought_tokens": usage.thought_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "total_tokens": usage.total_tokens,
        },
        "latency_ms": attempt.latency_ms,
        "finish_reasons": list(attempt.finish_reasons),
        "attempts": attempt.call_attempts,
        "cost_usd": None if cost is None else format(cost, "f"),
        "pricing_version": pricing_version,
    }


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


Attempt = ClassificationAttempt | DraftAttempt
Prior = dict[str, dict[str, tuple[Any, Any]]]
PROGRESS_VERSION = "cs-run-progress-v1"


def run_fingerprint(fields: Mapping[str, Any]) -> str:
    """재개 일치 판정에 쓰는 실행 지문(정렬 JSON의 sha256).

    Args:
        fields: manifest sha·후보 명세·seed·limit·입력 판본·재호출 횟수·대상 ID hash 등 실행을 정하는 값.

    Returns:
        str: 소문자 64자 hash.
    """
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def case_key(case_id: str) -> str:
    """진행 파일의 사례 키(ID 원값 대신 sha256).

    Args:
        case_id: 사례 ID.

    Returns:
        str: sha256 hex.
    """
    return hashlib.sha256(case_id.encode()).hexdigest()


def progress_labels_path(progress: Path) -> Path:
    """진행 파일 옆의 예측 진행 파일 경로.

    Args:
        progress: <out>.progress.jsonl.

    Returns:
        Path: <out>.progress-labels.jsonl. 이름이 .progress.jsonl로 끝나지 않으면 ValueError다.
    """
    if not progress.name.endswith(".progress.jsonl"):
        raise ValueError("EVAL_RESUME_MISMATCH")
    return progress.with_name(
        progress.name.removesuffix(".progress.jsonl") + ".progress-labels.jsonl"
    )


def _progress_entry(attempt: Attempt) -> dict[str, object]:
    """시도 하나를 진행 줄 항목으로 만든다(감사 항목 + 재구성용 표식, 원문·예측 없음).

    Args:
        attempt: 분류 또는 초안 시도.

    Returns:
        dict: audit_entry에 attempt_number·removed_annotations·call_started·execution_uncertain·has_raw와
            (초안이면) prompt_version·instruction_fingerprint를 더한 값.
    """
    extra = (
        {
            "prompt_version": attempt.prompt_version,
            "instruction_fingerprint": attempt.instruction_fingerprint,
        }
        if isinstance(attempt, DraftAttempt)
        else {}
    )
    return (
        audit_entry(attempt)
        | extra
        | {
            "attempt_number": attempt.attempt_number,
            "removed_annotations": attempt.removed_annotations,
            "call_started": attempt.call_started,
            "execution_uncertain": attempt.execution_uncertain,
            "has_raw": attempt.raw_response is not None,
        }
    )


def _rebuild(entry: Mapping[str, Any], prediction: object, attempt_type: type) -> Any:
    """진행 줄 항목과 예측으로 시도를 되살린다. 원시 응답은 저장하지 않았으므로 있었으면 빈 문자열 표식이다.

    Args:
        entry: _progress_entry 결과.
        prediction: 검사한 예측 모델 또는 None.
        attempt_type: ClassificationAttempt 또는 DraftAttempt.

    Returns:
        시도. 집계에 쓰는 값(outcome·예측·오류·사용량·지연·원시 응답 유무 등)이 원래 시도와 같다.
    """
    usage = entry["usage"]
    extra = (
        {
            "prompt_version": entry["prompt_version"],
            "instruction_fingerprint": entry["instruction_fingerprint"],
        }
        if attempt_type is DraftAttempt
        else {}
    )
    return attempt_type(
        entry["attempt_number"],
        outcome=entry["outcome"],
        input_version=entry["input_version"],
        input_sha256=entry["input_sha256"],
        removed_annotations=entry["removed_annotations"],
        call_started=entry["call_started"],
        execution_uncertain=entry["execution_uncertain"],
        error_code=entry["error_code"],
        http_status=entry["http_status"],
        latency_ms=entry["latency_ms"],
        usage=Usage(**usage),
        raw_response="" if entry["has_raw"] else None,
        prediction=prediction,
        model_version=entry["model_version"],
        finish_reasons=tuple(entry["finish_reasons"]),
        requested_model=entry["model"],
        call_attempts=entry["attempts"],
        **extra,
    )


class Progress:
    """사례가 끝날 때마다 진행 줄을 0600 파일에 덧붙이는 기록기(on_case로 꽂는다).

    진행 파일(<out>.progress.jsonl)은 첫 줄이 실행 지문이고 이후 줄마다 후보·사례 키(sha256)·최초와 선택 시도 2의
    진행 항목이다. 예측은 같은 키로 progress-labels 파일에만 쓴다. 원문·원시 응답·사례 ID 원값은 쓰지 않는다.
    예측을 먼저 쓰고 진행 줄을 나중에 써서, 진행 줄이 있으면 예측도 있다. 줄마다 flush·fsync한다.
    """

    def __init__(self, path: Path, fingerprint: str, *, resume: bool) -> None:
        """파일을 연다. 새 실행이면 기존 파일을 거부하고(O_EXCL) 지문 줄을 쓴다. 그 전에 실패하면 이번에 만든
        파일을 지운다.

        Args:
            path: 진행 파일 경로.
            fingerprint: run_fingerprint 결과.
            resume: 기존 파일에 이어 쓸지(load_progress로 검사한 뒤).
        """
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
        if not resume:
            flags |= os.O_EXCL
        self.completed = 0
        self.files = []
        self.paths = (path, progress_labels_path(path))
        created = []
        try:
            for target in self.paths:
                fd = os.open(target, flags, 0o600)
                if not resume:
                    created.append(target)
                os.fchmod(fd, 0o600)
                self.files.append(os.fdopen(fd, "a", encoding="utf-8"))
            if not resume:
                self._write(
                    0, {"kind": "header", "version": PROGRESS_VERSION, "fingerprint": fingerprint}
                )
        except BaseException:
            # 지문 줄을 쓰기 전에 실패하면 이번에 만든 파일만 지워 다음 새 실행을 막지 않는다.
            self.close()
            for target in created:
                target.unlink(missing_ok=True)
            raise

    def _write(self, index: int, item: Mapping[str, object]) -> None:
        """한 줄을 쓰고 디스크까지 내린다.

        Args:
            index: 0 진행 파일, 1 예측 파일(self.paths 순서).
            item: 줄 내용.
        """
        handle = self.files[index]
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    def record(self, name: str, case_id: str, first: Attempt, retry: Attempt | None) -> None:
        """사례 하나의 예측과 진행 줄을 쓴다.

        Args:
            name: 후보 이름.
            case_id: 사례 ID(키는 sha256으로 쓴다).
            first: 최초 시도.
            retry: 시도 2 또는 None.
        """
        key = {"candidate": name, "case": case_key(case_id)}
        self._write(
            1,
            key
            | {
                "first": first.prediction.model_dump() if first.prediction else None,
                "retry": retry.prediction.model_dump() if retry and retry.prediction else None,
            },
        )
        self._write(
            0,
            key
            | {
                "first": _progress_entry(first),
                "retry": None if retry is None else _progress_entry(retry),
            },
        )
        self.completed += 1

    def close(self) -> None:
        """파일을 닫는다."""
        for handle in self.files:
            handle.close()

    def remove(self) -> None:
        """결과 파일까지 쓴 정상 종료 뒤 진행 파일 둘을 지운다(결과에 모두 담겼고 비공개 산출물을 줄인다)."""
        self.close()
        for target in self.paths:
            target.unlink(missing_ok=True)


def _read_lines(path: Path) -> list[dict[str, Any]]:
    """JSONL을 읽는다. 마지막 줄이 줄바꿈 없이 끊겨 있으면(쓰다 중단) 파일에서 잘라 내고 버린다.

    Args:
        path: 읽을 파일.

    Returns:
        list[dict]: 완전한 줄. 중간 줄이 깨졌으면 EVAL_RESUME_MISMATCH ValueError다.
    """
    data = path.read_bytes()
    complete = data[: data.rfind(b"\n") + 1]
    if len(complete) != len(data):
        os.truncate(path, len(complete))
    try:
        return [json.loads(line) for line in complete.decode("utf-8").splitlines()]
    except ValueError:
        raise ValueError("EVAL_RESUME_MISMATCH") from None


def load_progress(
    path: Path,
    fingerprint: str,
    case_ids: list[str],
    names: list[str],
    attempt_type: type,
    output_model: type[BaseModel],
) -> tuple[Prior, str, int]:
    """진행 파일을 검사해 끝난 사례를 되살린다.

    Args:
        path: --resume 진행 파일.
        fingerprint: 이번 실행의 지문. 파일 첫 줄과 같아야 한다.
        case_ids: 이번 실행의 사례 ID(키를 원값으로 되돌릴 때 쓴다).
        names: 후보 이름.
        attempt_type: ClassificationAttempt 또는 DraftAttempt.
        output_model: 예측 모델(ClassificationOutput 또는 DraftOutput).

    Returns:
        tuple: (prior {후보: {사례: (최초, 시도 2 | None)}}, 읽기 시작 시점의 진행 파일 sha256, 사례 수). 지문·
            형식·후보·사례 키가 맞지 않거나 예측 줄이 없으면 EVAL_RESUME_MISMATCH ValueError다.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        [header, *lines] = _read_lines(path)
        labels = {
            (item["candidate"], item["case"]): item
            for item in _read_lines(progress_labels_path(path))
        }
        if header != {"kind": "header", "version": PROGRESS_VERSION, "fingerprint": fingerprint}:
            raise ValueError
        ids = {case_key(case_id): case_id for case_id in case_ids}
        prior: Prior = {}
        for line in lines:
            name, key = line["candidate"], line["case"]
            if name not in names or key not in ids or ids[key] in prior.get(name, {}):
                raise ValueError
            predicted = labels[name, key]

            def attempt(entry: Mapping[str, Any] | None, value: object) -> Any:
                if entry is None:
                    return None
                output = None if value is None else output_model.model_validate(value)
                return _rebuild(entry, output, attempt_type)

            prior.setdefault(name, {})[ids[key]] = (
                attempt(line["first"], predicted["first"]),
                attempt(line["retry"], predicted["retry"]),
            )
    except (ValueError, KeyError, TypeError):
        raise ValueError("EVAL_RESUME_MISMATCH") from None
    return prior, digest, sum(len(cases) for cases in prior.values())


def open_progress(
    out: Path,
    resume: Path | None,
    fingerprint: str,
    case_ids: list[str],
    names: list[str],
    attempt_type: type,
    output_model: type[BaseModel],
) -> tuple[Progress, Prior | None, dict[str, object]]:
    """진행 기록기를 열고, 재개면 끝난 사례를 되살린다(분류·초안 실행기 공용).

    Args:
        out: --out 경로. 새 실행의 진행 파일은 <out>.progress.jsonl이다.
        resume: --resume 진행 파일 또는 None. 재개 중에도 같은 파일에 이어 쓴다.
        fingerprint: 이번 실행의 지문.
        case_ids: 실행 사례 ID.
        names: 후보 이름.
        attempt_type: 시도 타입.
        output_model: 예측 모델.

    Returns:
        tuple: (기록기, prior 또는 None, meta에 더할 resumed_from·resumed_cases). 새 실행인데 진행 파일이 이미
            있으면 OSError, 재개 불일치는 EVAL_RESUME_MISMATCH ValueError다.
    """
    if resume is None:
        return Progress(Path(f"{out}.progress.jsonl"), fingerprint, resume=False), None, {}
    prior, digest, count = load_progress(
        resume, fingerprint, case_ids, names, attempt_type, output_model
    )
    progress = Progress(resume, fingerprint, resume=True)
    return progress, prior, {"resumed_from": digest, "resumed_cases": count}


def stop_run(progress: Progress | None, code: str, status: int) -> int:
    """중단·실패 시 진행 파일을 닫고 stderr에 코드와 완료 건수만 남긴다(원문·키 없음).

    Args:
        progress: 열린 기록기 또는 None.
        code: EVAL_RUN_INTERRUPTED 또는 EVAL_RUN_FAILED.
        status: 돌려줄 종료 코드.

    Returns:
        int: status.
    """
    completed = 0 if progress is None else progress.completed
    if progress is not None:
        progress.close()
    print(f"{code} completed={completed}", file=sys.stderr)
    return status


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
    strip_phone_annotation: bool = False,
    audit: bool = False,
    invalid_output_retries: int = 1,
    on_case: Callable[..., None] | None = None,
    prior: Prior | None = None,
    resumed: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], AuditFiles, AuditFiles]:
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
        strip_phone_annotation: 분석용 PHONE 표기 제거(기본 False). meta에 값과 입력 판본을 남긴다.
        audit: True면 후보별 사례 감사 파일 내용을 만든다.
        invalid_output_retries: 형식 위반 재호출 횟수(0 또는 1). 재호출은 attempt 2로 따로 남는다.
        on_case: 사례가 끝날 때마다 부를 진행 기록(Progress.record).
        prior: 재개로 되살린 끝난 사례.
        resumed: meta에 더할 resumed_from·resumed_cases.

    Returns:
        tuple: (결과, 라벨, 감사). 결과는 run_candidates 결과와 meta(모델·판본·지문·건수·표본 ID sha256·시각·
            git sha, 라벨·감사를 만들면 그 파일 sha256)이며 원문·키·사례별 예측이 없다. 라벨은 label_payloads
            형식(labels는 최초 시도, final-labels는 최종 시도 예측)이다. 사례 식별자가 들어간 비공개 산출물이라
            raw_response·원문·usage는 넣지 않는다. 감사는 audit_payloads 형식이다.
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
        return_attempts=labels or audit,
        strip_phone_annotation=strip_phone_annotation,
        invalid_output_retries=invalid_output_retries,
        on_case=on_case,
        prior=prior,
    )
    attempts = result.pop("attempts", {})
    retries = result.pop("retries", {})
    files = label_payloads(attempts, retries) if labels else {}
    audits = audit_payloads(attempts, retries) if audit else {}
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
            "strip_phone_annotation": strip_phone_annotation,
            "input_version": INPUT_VERSION_STRIPPED if strip_phone_annotation else INPUT_VERSION,
            "invalid_output_retries": invalid_output_retries,
            **(resumed or {}),
            "targets": len(records),
            "target_ids_sha256": hashlib.sha256("\n".join(sorted(records)).encode()).hexdigest(),
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "git_sha": git_sha(),
            **(audit_meta(files, LABEL_KINDS) if labels else {}),
            **(audit_meta(audits) if audit else {}),
        },
        "result": result,
    }
    return output, files, audits


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
        help="형식 위반(CS_INVALID_OUTPUT) 재호출 횟수. 재호출은 attempt 2로 따로 남는다(기본 1)",
    )
    parser.add_argument(
        "--strip-phone-annotation",
        action="store_true",
        help="분석용. PHONE의 Urgency: <값> 상담원 표기를 지운 v3 입력으로 돌린다(기본은 표기 포함 v4).",
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
        or (args.labels_out is not None and not args.execute)
        or (args.audit_out is not None and not args.execute)
        or (args.resume is not None and not args.execute)
    ):
        print(
            "CS_RUN_INVALID_ARGS: --limit은 1 이상, --execute에는 --out이 필요하고 "
            "--labels-out·--audit-out·--resume은 --execute와 함께만 쓴다."
        )
        return 1
    progress = None
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
            rows = dry_run(
                records,
                specs,
                codes,
                sensitive,
                settings,
                prices,
                strip_phone_annotation=args.strip_phone_annotation,
            )
            print(
                "dry-run: 네트워크 호출 없음. 토큰은 문자/4 근사와 UTF-8 바이트 상한이며 실제 과금은 "
                "실행 후 usage로 확인한다. 형식 위반 재호출(--invalid-output-retries 1)은 위반 사례만큼 "
                "호출을 더하며(최대 2배) 이 상한에 넣지 않았다."
            )
            print(json.dumps({"targets": len(records), "candidates": rows}, ensure_ascii=False))
            return 0
        prepare_output(args.out)
        if args.labels_out is not None:
            for spec in specs:
                for kind in LABEL_KINDS:
                    write_private(label_path(args.labels_out, spec["name"], kind), b"")
        if args.audit_out is not None:
            for spec in specs:
                for kind in AUDIT_KINDS:
                    write_private(label_path(args.audit_out, spec["name"], kind), b"")
        fingerprint = run_fingerprint(
            {
                "kind": "classification",
                "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                "candidates": specs,
                # 판본 이름이 같아도 프롬프트 본문·입력 정제가 바뀌면 다른 실행이다.
                "prompt_fingerprints": [
                    prompt_fingerprint("classification", spec["prompt_version"]) for spec in specs
                ],
                "input_version": INPUT_VERSION_STRIPPED
                if args.strip_phone_annotation
                else INPUT_VERSION,
                "seed": args.seed,
                "limit": args.limit,
                "strip_phone_annotation": args.strip_phone_annotation,
                "invalid_output_retries": args.invalid_output_retries,
                "targets": sorted(case_key(target) for target in records),
                "codes": sorted(codes),
                "sensitive": run_fingerprint(sensitive),
            }
        )
        progress, prior, resumed = open_progress(
            args.out,
            args.resume,
            fingerprint,
            list(records),
            [spec["name"] for spec in specs],
            ClassificationAttempt,
            ClassificationOutput,
        )
        output, labels, audits = execute(
            records,
            gold,
            specs,
            codes,
            sensitive,
            settings,
            prices,
            factory or default_factory(settings),
            labels=args.labels_out is not None,
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
            print("CS_RUN_FAILED: manifest·정답 잠금·코드북·후보 파일과 환경변수를 확인하세요.")
        return stop_run(progress, "EVAL_RUN_FAILED", 1) if progress else 1
    except Exception:
        return stop_run(progress, "EVAL_RUN_FAILED", 1)
    try:
        # 라벨·감사를 먼저 쓰고 결과(그 sha256 포함)를 마지막에 써서, 실패 시 해시가 맞지 않는 결과가 남지 않게
        # 한다.
        for (name, kind), data in labels.items():
            write_private(label_path(args.labels_out, name, kind), data)
        for (name, kind), data in audits.items():
            write_private(label_path(args.audit_out, name, kind), data)
        args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        print(
            "CS_RUN_OUTPUT_FAILED: 실행은 끝났지만 결과 파일을 쓰지 못했습니다. --out 경로를 확인하세요."
        )
        return 1
    progress.remove()
    print(json.dumps({"targets": len(records), "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
