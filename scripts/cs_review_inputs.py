"""승인된 C/S 개발 사본의 정제 후보와 사람 검수 목록. 모델을 호출하지 않는다."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from backend.engine.cs.classification import INPUT_VERSION, build_input
from scripts.cs_dev_bundle import checked_output, write_private_bundle
from tests.cs.cs_dev_data import load_dev_tickets

VERSION = "cs-input-review-v1"
EXPECTED_FAILURES = {"CS_INVALID_INPUT", "CS_EMPTY_INPUT", "CS_UNSUPPORTED_PHONE_ANNOTATION"}


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


def prepare_review(manifest_path: Path, approved_hash: str, output: Path) -> dict:
    """개발 파일을 기존 build_input으로 검사하고 실패도 분모에 보존한다.

    Args:
        manifest_path: KAN-83에서 인수한 개발 manifest.
        approved_hash: 인수 기록의 manifest 원문 SHA-256.
        output: 새 비공개 출력 디렉터리.

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
    candidates, audit = [], []
    strata: dict[str, Counter] = {}
    failures: Counter = Counter()
    groups_total: Counter = Counter()
    for row in rows:
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
            prepared = build_input(record, sensitive_values={})
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
    # 로컬 파일 관리 경계에서 실행 중 manifest 변경을 감지한다.
    if manifest_path.read_bytes() != raw:
        raise ValueError("CS_REVIEW_MANIFEST_CHANGED")
    summary = {
        "version": VERSION,
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_version": INPUT_VERSION,
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
    }
    payloads = {
        name: "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items).encode()
        for name, items in (("candidates.jsonl", candidates), ("audit.jsonl", audit))
    }
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
    args = parser.parse_args()
    try:
        summary = prepare_review(args.manifest, args.manifest_sha256, args.output)
    except (ValueError, OSError, KeyError, TypeError, csv.Error):
        raise SystemExit("CS_REVIEW_FAILED: 인수 파일·hash·형식·출력 경로를 확인하세요.") from None
    print(json.dumps({key: summary[key] for key in ("total", "candidate", "held", "model_ready")}))


if __name__ == "__main__":
    main()
