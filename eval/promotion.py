"""불합격 설정의 승격 차단(KAN-147). 운영에 나가는 모델·프롬프트 설정이 사람이 채택한(ADOPTED) 기록과 같은지 본다.

설정값은 복사본을 두지 않고 코드와 배포 스크립트에서 직접 읽는다(`deployed_config`). 그래서 모델·프롬프트 판본·출력
상한을 바꾸면 채택 기록과 어긋나 이 검사가 실패한다. 설정을 바꾸려면 실측(실제 API 평가) 결과로 새 채택 기록을
만들어 ADOPTED로 승인한 뒤 함께 커밋해야 한다(완료 조건 "설정 변경 시 실 API 평가"). 실측은 CI가 하지 않는다
(키·비용·저장소 밖 개발 데이터). CI는 그 결과인 채택 기록이 있는지만 본다.

- 문제 종류: MISMATCH(설정값 ≠ 채택값), NO_ADOPTION(그 단계의 ADOPTED 기록이 없음), BLOCKER_UNWAIVED(ADOPTED
  기록에 예외 승인 없는 blocker 불합격), DEPLOY_DIFFERS(배포 스크립트가 코드 기본 모델을 다른 값으로 덮어씀).
- 이미 알고 있는 어긋남은 `eval/policy/promotion-exceptions.json`에 사유·티켓과 함께 적어 둔다. 목록에 없는 문제와,
  더는 생기지 않는 예외(해소됐는데 남은 줄)는 둘 다 실패다. 예외는 숨기지 않고 결과에 그대로 보인다.
"""

import hashlib
import inspect
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.adoption import AdoptionRecord, blocker_waiver_errors

ROOT = Path(__file__).resolve().parents[1]
ADOPTIONS = ROOT / "eval" / "policy" / "adoptions"
EXCEPTIONS = ROOT / "eval" / "policy" / "promotion-exceptions.json"
DEPLOY_SCRIPT = ROOT / "infra" / "deploy_private.py"
# 운영 단계 → 채택 기록 과제. RFP 요구 추출은 채택 도구(eval_adopt --task rfp)의 과제 이름을 쓴다.
STAGE_TASKS = {
    "classification": "classification",
    "draft": "draft",
    "embedding": "embedding",
    "rfp_extraction": "rfp",
}
ISSUE_KINDS = ("MISMATCH", "NO_ADOPTION", "BLOCKER_UNWAIVED", "DEPLOY_DIFFERS")


@dataclass(frozen=True)
class Issue:
    stage: str
    kind: str
    field: str | None = None
    deployed: Any = None
    adopted: Any = None

    def key(self) -> tuple[str, str, str | None, str, str]:
        # 불일치는 값까지 맞아야 같은 문제다. 알려진 불일치 위에서 설정을 또 바꾸면 새 문제가 된다(#326 리뷰 P2).
        return (
            self.stage,
            self.kind,
            self.field,
            json.dumps(self.deployed, sort_keys=True),
            json.dumps(self.adopted, sort_keys=True),
        )


def exception_key(entry: Mapping[str, Any]) -> tuple[str, str, str | None, str, str]:
    """예외 줄의 비교 키(Issue.key와 같은 모양)."""
    return (
        entry["stage"],
        entry["kind"],
        entry.get("field"),
        json.dumps(entry.get("deployed"), sort_keys=True),
        json.dumps(entry.get("adopted"), sort_keys=True),
    )


def deploy_model(script: Path = DEPLOY_SCRIPT) -> str:
    """배포 스크립트가 C/S·TFT 서비스에 넣는 GEMINI_MODEL. 한 곳에만 있어야 한다.

    Raises:
        ValueError: 0개이거나 여러 개면 PROMOTION_DEPLOY_MODEL_UNREADABLE(배포 형식이 바뀌면 이 검사도 고친다).
    """
    found = re.findall(r"GEMINI_MODEL=([A-Za-z0-9._-]+)", script.read_text(encoding="utf-8"))
    if len(found) != 1:
        raise ValueError("PROMOTION_DEPLOY_MODEL_UNREADABLE")
    return found[0]


def draft_prompt_version() -> str:
    """C/S 초안 노드가 실제로 쓰는 프롬프트 판본.

    그래프의 초안 노드(backend/engine/cs/graph.py)는 run_draft_attempt에 prompt_version을 넘기지 않으므로 그 함수의
    기본값이 실행 판본이다. prompt_registry.DEFAULT_VERSIONS["draft"]는 실행 경로에서 쓰이지 않는다(Fable 2차 리뷰).
    그래프가 판본을 넘기게 바뀌면 tests/eval/test_promotion.py가 실패해 이 함수도 고치게 된다.
    """
    from backend.engine.cs.drafting_call import run_draft_attempt

    return str(inspect.signature(run_draft_attempt).parameters["prompt_version"].default)


def deployed_config() -> dict[str, dict[str, Any]]:
    """업무 실행 경로가 쓰는 단계별 설정을 코드 기본값에서 읽는다(환경 변수·.env는 보지 않는다).

    - C/S 분류 전용 모델·출력 상한: CsWorkerSettings(없으면 공통 모델). 공통 출력 상한은 분류가 쓰지 않는다.
    - C/S 분류·초안: C/S worker(backend/agent/cs_worker.py)가 공통 AI 설정의 gemini_model 하나를 두 단계에 함께 넘긴다.
    - RFP 요구 추출: 처리 함수(backend/api/tft/worker_processor.py)의 추출 전용 설정 extraction_model(TFT_EXTRACTION_MODEL)이다.
      공통 GEMINI_MODEL과 별개다.
    - 임베딩: C/S 벡터 검색 설정의 모델·차원과 문서 쪽 적재 판본의 task_type.
    배포가 기본값을 덮어쓰는지는 deploy_drift가 따로 본다.

    Returns:
        dict: 단계 → 필드 → 값. 채택 기록의 adopted 키와 같은 이름을 쓴다.
    """
    from backend.agent.cs_runner import DRAFT_DEFAULTS
    from backend.agent.cs_search import CsVectorSettings
    from backend.agent.cs_worker import CsWorkerSettings
    from backend.api.tft.worker_processor import ProcessorSettings
    from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS, prompt_fingerprint
    from backend.engine.rfp.requirements import EXTRACTION_VERSION
    from backend.engine.rfp.requirements import PROMPT as EXTRACTION_PROMPT
    from backend.shared.ai import AISettings

    # model_construct는 환경 변수를 읽지 않고 코드 기본값만 채운다(실행한 PC의 설정이 결과를 바꾸지 않게).
    ai = AISettings.model_construct()
    worker = CsWorkerSettings.model_construct()
    model = ai.gemini_model
    draft_version = draft_prompt_version()
    vector = CsVectorSettings.model_construct()
    extraction = ProcessorSettings.model_construct()
    task_type = vector.embedding_version.rsplit(":", 1)[-1]
    return {
        "classification": {
            "model": worker.classification_model or model,
            "prompt_version": DEFAULT_VERSIONS["classification"],
            "prompt_fingerprint": prompt_fingerprint(
                "classification", DEFAULT_VERSIONS["classification"]
            ),
            "max_output_tokens": worker.classification_max_output_tokens,
        },
        "draft": {
            "model": model,
            "prompt_version": draft_version,
            "prompt_fingerprint": prompt_fingerprint("draft", draft_version),
            "max_output_tokens": DRAFT_DEFAULTS["max_output_tokens"],
            "evidence_limit": DRAFT_DEFAULTS["evidence_limit"],
        },
        "embedding": {
            "model": vector.embedding_model,
            "dimension": vector.embedding_dimension,
            "task_type": task_type,
        },
        "rfp_extraction": {
            "model": extraction.extraction_model,
            "extraction_version": EXTRACTION_VERSION,
            # 판본 이름을 그대로 두고 지시문 본문만 고쳐도 잡히게 본문 지문을 같이 본다.
            "prompt_fingerprint": hashlib.sha256(EXTRACTION_PROMPT.encode()).hexdigest()[:16],
            "max_output_tokens": extraction.extraction_max_output_tokens,
        },
    }


def deploy_setting(name: str, script: Path = DEPLOY_SCRIPT) -> str | None:
    """배포 스크립트가 넣는 환경 변수 값. 없으면 None, 여러 번 나오면 PROMOTION_DEPLOY_SETTING_UNREADABLE."""
    found = re.findall(rf"\b{name}=([A-Za-z0-9._-]+)", script.read_text(encoding="utf-8"))
    if len(found) > 1:
        raise ValueError("PROMOTION_DEPLOY_SETTING_UNREADABLE")
    return found[0] if found else None


def deploy_drift(
    config: Mapping[str, Mapping[str, Any]], script: Path = DEPLOY_SCRIPT
) -> list[Issue]:
    """배포 스크립트가 코드 기본값을 다른 값으로 덮어쓰는지 본다(배포가 검사한 값과 다른 설정으로 돌지 않게).

    - GEMINI_MODEL(반드시 있어야 함): C/S 분류·초안 모델.
    - CS_WORKER_CLASSIFICATION_MODEL·CS_WORKER_CLASSIFICATION_MAX_OUTPUT_TOKENS: C/S 분류 전용 값.
      공통 GEMINI_MAX_OUTPUT_TOKENS는 분류·초안이 쓰지 않아 보지 않는다.
    - TFT_EXTRACTION_MODEL·TFT_EXTRACTION_MAX_OUTPUT_TOKENS: RFP 요구 추출. 배포가 넣지 않으면 코드 기본값이다.

    Returns:
        list[Issue]: 다른 값마다 DEPLOY_DIFFERS(deployed=배포값, adopted=코드 기본값).
    """
    watched = [
        ("GEMINI_MODEL", ("classification", "draft"), "model"),
        ("CS_WORKER_CLASSIFICATION_MODEL", ("classification",), "model"),
        ("CS_WORKER_CLASSIFICATION_MAX_OUTPUT_TOKENS", ("classification",), "max_output_tokens"),
        ("TFT_EXTRACTION_MODEL", ("rfp_extraction",), "model"),
        ("TFT_EXTRACTION_MAX_OUTPUT_TOKENS", ("rfp_extraction",), "max_output_tokens"),
    ]
    deploy_model(script)  # GEMINI_MODEL은 정확히 한 번 있어야 한다(형식이 바뀌면 여기서 멈춘다).
    issues: list[Issue] = []
    for name, stages, field_name in watched:
        raw = deploy_setting(name, script)
        if raw is None:
            continue
        for stage in stages:
            code_value = config[stage][field_name]
            value: Any = int(raw) if isinstance(code_value, int) and raw.isdigit() else raw
            if value != code_value:
                issues.append(Issue(stage, "DEPLOY_DIFFERS", field_name, value, code_value))
    return issues


def load_adoptions(directory: Path = ADOPTIONS) -> list[AdoptionRecord]:
    """채택 기록 전부. AdoptionRecord가 예외 승인 형식과 ADOPTED의 blocker 불합격 승인을 다시 검사한다.

    decided_on은 YYYY-MM-DD여야 한다. 최신 기록을 문자열 순서로 고르므로 '2026-1-5'처럼 적으면 옛 기록이 최신으로
    뽑힐 수 있다(PROMOTION_ADOPTION_DATE).
    """
    records = [
        AdoptionRecord(**json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]
    if any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.decided_on) for r in records):
        raise ValueError("PROMOTION_ADOPTION_DATE")
    return records


def find_issues(
    config: Mapping[str, Mapping[str, Any]], records: Sequence[AdoptionRecord]
) -> list[Issue]:
    """단계마다 설정이 가장 최근 ADOPTED 기록(decided_on, YYYY-MM-DD)과 맞는지 본다.

    새 기록이 옛 기록을 대신하므로, 옛 채택값으로 되돌리면 불일치다(#326 리뷰 P3). 같은 날짜면 파일 이름 순서의
    뒤 기록이다. 채택 기록에 없는 설정 필드는 비교하지 않는다(채택 때 고정한 값만 본다).

    Args:
        config: deployed_config 결과.
        records: 채택 기록.

    Returns:
        list[Issue]: 문제 목록(단계 순서).
    """
    issues: list[Issue] = []
    for record in records:
        if record.status == "ADOPTED":
            issues += [
                Issue(stage, "BLOCKER_UNWAIVED", metric)
                for stage, task in STAGE_TASKS.items()
                if task == record.task
                for metric in blocker_waiver_errors(record)
            ]
    for stage, task in STAGE_TASKS.items():
        adopted = sorted(
            (r for r in records if r.task == task and r.status == "ADOPTED"),
            key=lambda r: r.decided_on,
        )
        if not adopted:
            # 채택 기록이 없는 단계도 지금 설정값을 함께 남긴다. 예외 줄이 이 값을 그대로 적어야 통과하므로, 기록
            # 없이 설정을 또 바꾸면 막힌다.
            issues.append(Issue(stage, "NO_ADOPTION", deployed=dict(config[stage])))
            continue
        current, chosen = config[stage], adopted[-1].adopted
        # 양쪽의 모든 항목을 본다. 채택 기록에 없는 실행 설정(예: 출력 상한)이나 실행 경로에 없는 채택 항목도
        # 불일치다. 채택 때 고정하지 않은 설정을 바꿔도 통과하던 빈칸을 막는다(Fable 2차 리뷰).
        issues += [
            Issue(stage, "MISMATCH", name, current.get(name), chosen.get(name))
            for name in sorted(set(current) | set(chosen))
            if current.get(name) != chosen.get(name)
        ]
    return issues


def load_exceptions(path: Path = EXCEPTIONS) -> list[dict[str, Any]]:
    """알려진 어긋남 목록. 줄마다 stage·kind·field(없으면 null)·reason·ticket·recorded_on이 있어야 한다.

    MISMATCH·DEPLOY_DIFFERS 줄은 deployed(운영값)·adopted(채택값)도 있어야 하고, 두 값이 그대로일 때만 허용된다.
    NO_ADOPTION 줄은 deployed에 그 단계의 지금 설정 전체를 적고, 설정이 그대로일 때만 허용된다.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["exceptions"]:
        missing = [
            k for k in ("stage", "kind", "reason", "ticket", "recorded_on") if not entry.get(k)
        ]
        if missing or entry["kind"] not in ISSUE_KINDS or entry["stage"] not in STAGE_TASKS:
            raise ValueError("PROMOTION_EXCEPTION_FIELDS")
        if entry["kind"] == "NO_ADOPTION" and not isinstance(entry.get("deployed"), dict):
            raise ValueError("PROMOTION_EXCEPTION_FIELDS")
        if entry["kind"] in ("MISMATCH", "DEPLOY_DIFFERS") and not {
            "field",
            "deployed",
            "adopted",
        } <= set(entry):
            raise ValueError("PROMOTION_EXCEPTION_FIELDS")
        if entry["kind"] == "BLOCKER_UNWAIVED":
            # blocker 불합격은 채택 기록의 waivers로만 승인한다(여기서 우회하지 않는다).
            raise ValueError("PROMOTION_EXCEPTION_NOT_ALLOWED")
    return data["exceptions"]


def check(
    config: Mapping[str, Mapping[str, Any]],
    records: Sequence[AdoptionRecord],
    exceptions: Sequence[Mapping[str, Any]],
    drift: Sequence[Issue] = (),
) -> dict:
    """승격 판정. 목록에 없는 문제나 해소됐는데 남은 예외가 있으면 passed=False다.

    Returns:
        dict: passed, blocked(예외 없는 문제), stale(해소된 예외), excepted(알려진 문제와 사유).
    """
    issues = [*find_issues(config, records), *drift]
    allowed = {exception_key(e): e for e in exceptions}
    found = {issue.key(): issue for issue in issues}
    blocked = [i for k, i in found.items() if k not in allowed]
    stale = [e for k, e in allowed.items() if k not in found]
    excepted = [
        {**vars(found[k]), "reason": e["reason"], "ticket": e["ticket"]}
        for k, e in allowed.items()
        if k in found
    ]
    return {
        "passed": not blocked and not stale,
        "blocked": [vars(i) for i in blocked],
        "stale": list(stale),
        "excepted": excepted,
    }


def main() -> int:
    """CI·정기 실행 입구. 결과 JSON을 표준 출력에 내고, 통과하지 못하면 1이다."""
    config = deployed_config()
    result = check(config, load_adoptions(), load_exceptions(), deploy_drift(config))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
