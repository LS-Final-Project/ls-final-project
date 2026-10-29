"""승인된 C/S 개발 사본의 정제 후보와 사람 검수 목록. 모델을 호출하지 않는다."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from backend.engine.cs.classification import INPUT_VERSION, INPUT_VERSION_STRIPPED, build_input
from scripts.cs_dev_bundle import checked_output, csv_bytes, write_private_bundle
from tests.cs.cs_dev_data import load_dev_tickets

# v3(KAN-105 4차): 기본 입력이 PHONE 상담원 표기를 포함(v4)하므로 PHONE 제거 검수 행은 분석용 옵션에서만 만든다.
VERSION = "cs-input-review-v3"
EXPECTED_FAILURES = {"CS_INVALID_INPUT", "CS_EMPTY_INPUT", "CS_UNSUPPORTED_PHONE_ANNOTATION"}

# 검수 질문은 원천 라벨을 정답으로 확정하지 않으며 자동 판정값을 만들지 않는다.
BOUNDARY_CHECKS = {
    "INPUT_PRIVACY": "원문과 정제 후보의 민감값 누락·과다 마스킹을 확인. 보류 건은 후보 없음.",
    "PHONE_REMOVED": "제거된 PHONE 주석과 업무 본문을 대조하고 과다 삭제·남은 누설을 확인.",
    "PHONE_NO_REMOVAL": "주석 제거 0건이 무누설을 뜻하지 않음. 미지원 주석·변형을 원문에서 확인.",
    "PHONE_HELD": "입력 보류 사유를 확인. 제거 여부 미확정이며 원문 재전송으로 우회하지 않음.",
    "SOURCE_SAFETY": "원천 SAFETY는 선정 근거일 뿐. PHONE 주석을 제외한 위험 근거 위치를 기록.",
    "SOURCE_SITE_DOWN": "원천 SITE_DOWN은 선정 근거일 뿐. 실제 가동 중단 근거 위치를 기록.",
    "SOURCE_SECONDARY": "주분류와 다른 명시적 의도와 우선 근거를 확인. 모호하면 보류.",
    "SOURCE_FOLLOWUP": "회신 횟수만으로 판정하지 않음. 회신 본문·장비/판본 연결·known_at 필요.",
    "SOURCE_BLOCKED_REFERENCE": "원천 참조와 실제 근거 문서 위치를 확인. FLT-042 인용 차단 검증은 별도.",
}
BOUNDARY_FIELDS = (
    "case_id_json",
    "split",
    "channel",
    "check",
    "instruction",
    "source_sha256",
    "source_record",
    "source_fields",
    "candidate_record",
    "input_version",
    "input_status",
    "input_reason",
    "review_status",
    "reviewer",
    "reviewed_at",
    "evidence_location",
    "finding",
    "reason",
)


def boundary_rows(
    item: dict,
    source_hash: str,
    source_record: int,
    candidate_record: int | None,
    *,
    strip_phone_annotation: bool = False,
) -> list[dict[str, str]]:
    """전체 개인정보와 선정된 경계 사례를 근거 참조가 있는 미검수 행으로 만든다.

    Args:
        item: 원문과 정답을 제외한 사례 감사 항목.
        source_hash: 승인된 개발 CSV의 SHA-256.
        source_record: 헤더를 제외한 1부터 시작하는 CSV 논리 레코드 번호.
        candidate_record: 후보 JSONL의 1부터 시작하는 줄 번호. 보류는 None.
        strip_phone_annotation: True(분석용 v3 입력)일 때만 PHONE 제거·미제거·보류 검수 행을 만든다.

    Returns:
        list: 자동 판정 없이 질문·근거 조회 위치를 제공하는 검수 행.
    """
    checks = ["INPUT_PRIVACY"]
    if strip_phone_annotation and item["channel"] == "PHONE":
        checks.append(
            "PHONE_HELD"
            if item["status"] == "held"
            else "PHONE_REMOVED"
            if item["removed_annotations"]
            else "PHONE_NO_REMOVAL"
        )
    checks.extend(group for group in item["source_groups"] if group in BOUNDARY_CHECKS)
    return [
        {
            "case_id_json": json.dumps(item["case_id"], ensure_ascii=False),
            "split": item["split"],
            "channel": item["channel"],
            "check": check,
            "instruction": BOUNDARY_CHECKS[check],
            "source_sha256": source_hash,
            "source_record": str(source_record),
            "source_fields": (
                "customer_followups"
                if check == "SOURCE_FOLLOWUP"
                else "reference_doc_id"
                if check == "SOURCE_BLOCKED_REFERENCE"
                else "inquiry_subject,inquiry_body"
            ),
            "candidate_record": str(candidate_record) if candidate_record is not None else "",
            "input_version": INPUT_VERSION_STRIPPED if strip_phone_annotation else INPUT_VERSION,
            "input_status": item["status"],
            "input_reason": item["reason"] or "",
            "review_status": "pending",
            "reviewer": "",
            "reviewed_at": "",
            "evidence_location": "",
            "finding": "",
            "reason": "",
        }
        for check in checks
    ]


def source_groups(row: dict[str, str]) -> list[str]:
    """원천 라벨로 검수 대상을 표시하되 확정 gold나 모델 입력으로 사용하지 않는다.

    Args:
        row: 승인된 개발 CSV의 한 행.

    Returns:
        list: 사람 감사 파일에만 보관할 검수 그룹 이름.
    """
    groups = []
    if row.get("channel") == "PHONE":
        groups.append("PHONE")
    for code in ("SAFETY", "SITE_DOWN"):
        if row.get("urgency_flag") == code:
            groups.append("SOURCE_" + code)
    if row.get("secondary_category", "").strip():
        groups.append("SOURCE_SECONDARY")
    if row.get("category_code") == "RETURN":
        groups.append("SOURCE_RETURN")
    followups = row.get("customer_followups", "")
    if followups.isascii() and followups.isdecimal() and followups.strip("0"):
        groups.append("SOURCE_FOLLOWUP")
    if re.search(r"(?<![A-Z0-9])FLT-042(?![A-Z0-9])", row.get("reference_doc_id", "")):
        groups.append("SOURCE_BLOCKED_REFERENCE")
    return groups


def prepare_review(
    manifest_path: Path, approved_hash: str, output: Path, *, strip_phone_annotation: bool = False
) -> dict:
    """개발 파일을 기존 build_input으로 검사하고 실패도 분모에 보존한다.

    Args:
        manifest_path: KAN-83에서 인수한 개발 manifest.
        approved_hash: 인수 기록의 manifest 원문 SHA-256.
        output: 새 비공개 출력 디렉터리.
        strip_phone_annotation: 분석용. True면 PHONE 상담원 표기를 지운 v3 입력과 PHONE 제거 검수 행을 만든다.

    Returns:
        dict: 원문/정답/사례 ID 없는 집계. 후보도 개인정보 검수 대기다.
    """
    output = checked_output(output)
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != approved_hash:
        raise ValueError("CS_REVIEW_MANIFEST_MISMATCH")
    manifest = json.loads(raw)
    entry = manifest["tickets"]
    if not entry.get("splits") or not set(entry["splits"]) <= {"train", "valid"}:
        raise ValueError("CS_REVIEW_SPLITS_INVALID")
    path = (manifest_path.parent / entry["path"]).resolve()
    if not path.is_relative_to(manifest_path.parent.resolve()):
        raise ValueError("CS_REVIEW_SOURCE_OUTSIDE_BUNDLE")
    rows = load_dev_tickets(manifest_path)
    if not rows:
        raise ValueError("CS_REVIEW_NO_CASES")
    candidates, audit, review = [], [], []
    strata: dict[str, Counter] = {}
    failures: Counter = Counter()
    groups_total: Counter = Counter()
    for source_record, row in enumerate(rows, start=1):
        record = {
            "channel": row.get("channel"),
            "body_language": row.get("body_language"),
            "subject": row.get("inquiry_subject") or None,
            "body": row.get("inquiry_body"),
        }
        channel = row.get("channel", "")
        if channel not in {"PHONE", "EMAIL", "CHAT", "PARTNER_PORTAL", "WEB_FORM"}:
            channel = "INVALID"
        counts = strata.setdefault(
            row["data_split"] + "/" + channel,
            Counter(total=0, candidate=0, held=0, removed_annotations=0),
        )
        counts["total"] += 1
        groups = source_groups(row)
        groups_total.update(groups)
        item: dict[str, object] = {
            "case_id": row["ticket_id"],
            "split": row["data_split"],
            "channel": channel,
            "source_groups": groups,
            "privacy_review_status": "pending",
        }
        try:
            # 승인된 민감값 목록이 없는 현재 검수 도구는 내장 패턴만 적용한다.
            prepared = build_input(
                record, sensitive_values={}, strip_phone_annotation=strip_phone_annotation
            )
        except ValueError as exc:
            if str(exc) not in EXPECTED_FAILURES:
                raise ValueError("CS_REVIEW_UNEXPECTED_FAILURE") from None
            counts["held"] += 1
            failures[str(exc)] += 1
            item.update(status="held", reason=str(exc), removed_annotations=None)
        else:
            counts["candidate"] += 1
            counts["removed_annotations"] += prepared.removed_annotations
            item.update(
                status="candidate", reason=None, removed_annotations=prepared.removed_annotations
            )
            candidates.append(
                {
                    "case_id": row["ticket_id"],
                    "privacy_review_status": "pending",
                    "payload": prepared.payload.model_dump(),
                }
            )
        audit.append(item)
        review.extend(
            boundary_rows(
                item,
                entry["sha256"],
                source_record,
                len(candidates) if item["status"] == "candidate" else None,
                strip_phone_annotation=strip_phone_annotation,
            )
        )
    # 로컬 파일 관리 경계에서 실행 중 manifest 변경을 감지한다.
    if manifest_path.read_bytes() != raw:
        raise ValueError("CS_REVIEW_MANIFEST_CHANGED")
    summary = {
        "version": VERSION,
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_version": INPUT_VERSION_STRIPPED if strip_phone_annotation else INPUT_VERSION,
        "strip_phone_annotation": strip_phone_annotation,
        "created_at": datetime.now(UTC).isoformat(),
        "manifest_sha256": approved_hash,
        "source_sha256": entry["sha256"],
        "total": len(rows),
        "candidate": len(candidates),
        "held": len(rows) - len(candidates),
        "model_ready": 0,
        "privacy_review_status": "pending",
        "sensitive_values_status": "not_supplied_patterns_only",
        "strata": dict(sorted(strata.items())),
        "hold_reasons": dict(failures),
        "source_groups": dict(groups_total),
        "boundary_review": {
            "rows": len(review),
            "checks": {
                check: sum(r["check"] == check for r in review) for check in BOUNDARY_CHECKS
            },
            "completed": 0,
        },
    }
    payloads = {
        name: "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items).encode()
        for name, items in (("candidates.jsonl", candidates), ("audit.jsonl", audit))
    }
    payloads["review.boundaries.csv"] = csv_bytes(BOUNDARY_FIELDS, review)
    summary["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    payloads["summary.json"] = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode()
    write_private_bundle(output, payloads)
    return summary


def main() -> None:
    """관리자가 지정한 개발 사본만 검사하고 안전한 집계를 출력한다.

    Args:
        없음. manifest/hash/출력 경로는 명령행에서 받는다.

    Returns:
        None: 정제 후보/보류 건수만 출력한다. 오류 원문은 숨긴다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--strip-phone-annotation",
        action="store_true",
        help="분석용. PHONE의 Urgency: <값> 상담원 표기를 지운 v3 입력과 PHONE 제거 검수 행을 만든다.",
    )
    args = parser.parse_args()
    try:
        summary = prepare_review(
            args.manifest,
            args.manifest_sha256,
            args.output,
            strip_phone_annotation=args.strip_phone_annotation,
        )
    except (ValueError, OSError, KeyError, TypeError, csv.Error):
        raise SystemExit("CS_REVIEW_FAILED: 인수 파일·hash·형식·출력 경로를 확인하세요.") from None
    print(json.dumps({key: summary[key] for key in ("total", "candidate", "held", "model_ready")}))


if __name__ == "__main__":
    main()
