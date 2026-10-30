"""불합격 설정의 승격 차단(KAN-147). 운영에 나가는 모델·프롬프트 설정이 사람이 채택한(ADOPTED) 기록과 같은지 본다.

설정값은 복사본을 두지 않고 코드와 배포 스크립트에서 직접 읽는다(`deployed_config`). 그래서 모델·프롬프트 판본·출력
상한을 바꾸면 채택 기록과 어긋나 이 검사가 실패한다. 설정을 바꾸려면 실측(실제 API 평가) 결과로 새 채택 기록을
만들어 ADOPTED로 승인한 뒤 함께 커밋해야 한다(완료 조건 "설정 변경 시 실 API 평가"). 실측은 CI가 하지 않는다
(키·비용·저장소 밖 개발 데이터). CI는 그 결과인 채택 기록이 있는지만 본다.

- 문제 종류: MISMATCH(설정값 ≠ 채택값), NO_ADOPTION(그 단계의 ADOPTED 기록이 없음), BLOCKER_UNWAIVED(ADOPTED
  기록에 예외 승인 없는 blocker 불합격).
- 이미 알고 있는 어긋남은 `eval/policy/promotion-exceptions.json`에 사유·티켓과 함께 적어 둔다. 목록에 없는 문제와,
  더는 생기지 않는 예외(해소됐는데 남은 줄)는 둘 다 실패다. 예외는 숨기지 않고 결과에 그대로 보인다.
"""

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
ISSUE_KINDS = ("MISMATCH", "NO_ADOPTION", "BLOCKER_UNWAIVED")


@dataclass(frozen=True)
class Issue:
    stage: str
    kind: str
    field: str | None = None
    deployed: Any = None
    adopted: Any = None

    def key(self) -> tuple[str, str, str | None]:
        return self.stage, self.kind, self.field


def deploy_model(script: Path = DEPLOY_SCRIPT) -> str:
    """배포 스크립트가 C/S·TFT 서비스에 넣는 GEMINI_MODEL. 한 곳에만 있어야 한다.

    Raises:
        ValueError: 0개이거나 여러 개면 PROMOTION_DEPLOY_MODEL_UNREADABLE(배포 형식이 바뀌면 이 검사도 고친다).
    """
    found = re.findall(r"GEMINI_MODEL=([A-Za-z0-9._-]+)", script.read_text(encoding="utf-8"))
    if len(found) != 1:
        raise ValueError("PROMOTION_DEPLOY_MODEL_UNREADABLE")
    return found[0]


def deployed_config(script: Path = DEPLOY_SCRIPT) -> dict[str, dict[str, Any]]:
    """운영에 나가는 단계별 설정을 코드·배포 스크립트에서 읽는다.

    Returns:
        dict: 단계 → 필드 → 값. 채택 기록의 adopted 키와 같은 이름을 쓴다.
    """
    from backend.agent.cs_runner import DRAFT_DEFAULTS
    from backend.agent.cs_search import CsVectorSettings
    from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS, prompt_fingerprint
    from backend.engine.rfp.requirements import EXTRACTION_VERSION

    model = deploy_model(script)
    vector = CsVectorSettings(_env_file=None)
    # 문서 쪽 적재 판본(embedding_version)의 task_type. 채택 기록의 task_type과 같은 값이다.
    task_type = vector.embedding_version.rsplit(":", 1)[-1]
    return {
        "classification": {
            "model": model,
            "prompt_version": DEFAULT_VERSIONS["classification"],
            "prompt_fingerprint": prompt_fingerprint(
                "classification", DEFAULT_VERSIONS["classification"]
            ),
        },
        "draft": {
            "model": model,
            "prompt_version": DEFAULT_VERSIONS["draft"],
            "max_output_tokens": DRAFT_DEFAULTS["max_output_tokens"],
            "evidence_limit": DRAFT_DEFAULTS["evidence_limit"],
        },
        "embedding": {
            "model": vector.embedding_model,
            "dimension": vector.embedding_dimension,
            "task_type": task_type,
        },
        "rfp_extraction": {"model": model, "extraction_version": EXTRACTION_VERSION},
    }


def load_adoptions(directory: Path = ADOPTIONS) -> list[AdoptionRecord]:
    """채택 기록 전부. AdoptionRecord가 예외 승인 형식과 ADOPTED의 blocker 불합격 승인을 다시 검사한다."""
    return [
        AdoptionRecord(**json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]


def find_issues(
    config: Mapping[str, Mapping[str, Any]], records: Sequence[AdoptionRecord]
) -> list[Issue]:
    """단계마다 설정이 ADOPTED 기록 중 하나와 맞는지 본다.

    기록이 여럿이면 하나라도 채택값이 설정과 모두 같으면 통과다. 없으면 가장 최근 기록(decided_on)과의 차이를
    필드별로 낸다. 채택 기록에 없는 설정 필드는 비교하지 않는다(채택 때 고정한 값만 본다).

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
            issues.append(Issue(stage, "NO_ADOPTION"))
            continue
        current = config[stage]

        def differences(record: AdoptionRecord) -> list[Issue]:
            return [
                Issue(stage, "MISMATCH", name, current.get(name), value)
                for name, value in record.adopted.items()
                if current.get(name) != value
            ]

        if not any(not differences(r) for r in adopted):
            issues += differences(adopted[-1])
    return issues


def load_exceptions(path: Path = EXCEPTIONS) -> list[dict[str, Any]]:
    """알려진 어긋남 목록. 줄마다 stage·kind·field(없으면 null)·reason·ticket·recorded_on이 있어야 한다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["exceptions"]:
        missing = [
            k for k in ("stage", "kind", "reason", "ticket", "recorded_on") if not entry.get(k)
        ]
        if missing or entry["kind"] not in ISSUE_KINDS or entry["stage"] not in STAGE_TASKS:
            raise ValueError("PROMOTION_EXCEPTION_FIELDS")
        if entry["kind"] == "BLOCKER_UNWAIVED":
            # blocker 불합격은 채택 기록의 waivers로만 승인한다(여기서 우회하지 않는다).
            raise ValueError("PROMOTION_EXCEPTION_NOT_ALLOWED")
    return data["exceptions"]


def check(
    config: Mapping[str, Mapping[str, Any]],
    records: Sequence[AdoptionRecord],
    exceptions: Sequence[Mapping[str, Any]],
) -> dict:
    """승격 판정. 목록에 없는 문제나 해소됐는데 남은 예외가 있으면 passed=False다.

    Returns:
        dict: passed, blocked(예외 없는 문제), stale(해소된 예외), excepted(알려진 문제와 사유).
    """
    issues = find_issues(config, records)
    allowed = {(e["stage"], e["kind"], e.get("field")): e for e in exceptions}
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
    result = check(deployed_config(), load_adoptions(), load_exceptions())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
