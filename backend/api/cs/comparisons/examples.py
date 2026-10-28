"""계약의 합성 예시 5종(동일·차이·미확인·가림·과거 판본)을 저장 행으로 정의한다.

OpenAPI 예시와 브라우저 모의 응답은 이 행을 실제 응답 경로와 같은 hide()·build()로 만든다.
DB 테스트는 같은 행을 저장한 뒤 실제 GET 응답이 이 예시와 같은지 확인한다. 값은 모두 합성이며
제공 자료의 제품·고장 ID와 겹치지 않는다(고장 ID FLT-900~904).
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from backend.api.cs.comparisons.view import build, hide
from backend.shared.models import ResponseMeta, SuccessResponse

Row = dict[str, Any]
PRODUCT = "SYN-PRD-01"
VISIBLE_TICKET = UUID("7a1e2b3c-4d5e-4f60-8a71-b2c3d4e5f601")
HIDDEN_TICKET = UUID("8b2f3c4d-5e6f-4a71-9b82-c3d4e5f6a702")
CASE = UUID("0f2d7c1e-8a44-4b1a-9e3e-6a1c2b3d4e01")
AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
KNOWN = datetime(2026, 10, 20, tzinfo=UTC)
META = ResponseMeta(request_id=UUID("11111111-1111-4111-8111-111111111199"))


def item(kind: str, code: str, **values: Any) -> Row:
    """빈 칸을 기본값으로 채운 항목 행을 만든다.

    Args:
        kind: CONDITION·CAUSE·DOCUMENT_SIGNAL.
        code: 항목 코드·원인 유형·문서 신호.
        **values: 기본값과 다른 열.

    Returns:
        Row: condition_comparison_items 한 행.
    """
    row: Row = {
        "kind": kind,
        "code": code,
        "judgement": None,
        "finding": None,
        "unconfirmed_reason": None,
        "baseline_value": None,
        "baseline_missing_reason": None,
        "baseline_evidence_ids": [],
        "target_value": None,
        "target_missing_reason": None,
        "target_evidence_ids": [],
        "masked_summary": None,
        "summary_missing_reason": None,
        "evidence_ids": [],
        "provenance_kind": "system_record",
        "decided_by_name": None,
        "decided_at": None if kind == "DOCUMENT_SIGNAL" else AT,
    }
    return row | values


CASE_EVIDENCE: Row = {
    "evidence_id": "ev-1",
    "case_id": CASE,
    "ticket_id": VISIBLE_TICKET,
    "evidence_location": {"source": "resolution_case", "field": "root_cause"},
    "data_origin": "synthetic",
    "derivation_kind": "SYSTEM",
    "known_at": KNOWN,
}
TICKET_EVIDENCE: Row = {
    "evidence_id": "ev-2",
    "case_id": None,
    "ticket_id": HIDDEN_TICKET,
    "evidence_location": {"source": "ticket", "field": "body"},
    "data_origin": "synthetic",
    "derivation_kind": "PROVIDED",
    "known_at": datetime(2026, 10, 22, tzinfo=UTC),
}
DOCUMENT_EVIDENCE: Row = {
    "evidence_id": "ev-3",
    "case_id": None,
    "ticket_id": None,
    "evidence_location": {
        "doc_id": "doc-syn-manual",
        "version": "docv-3",
        "page": 12,
        "char_start": 340,
        "char_end": 362,
    },
    "data_origin": "synthetic",
    "derivation_kind": "PROVIDED",
    "known_at": datetime(2026, 10, 1, tzinfo=UTC),
}


def request(number: int, **values: Any) -> Row:
    """예시 묶음의 개선요청 행에서 응답이 쓰는 값만 만든다.

    Args:
        number: 묶음 번호(고장 ID 끝자리).
        **values: 기본값과 다른 열.

    Returns:
        Row: id·version과 condition_summary·fw_fixed_version·fw_released_on.
    """
    row: Row = {
        "id": UUID(f"3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e0{number}"),
        "version": 5,
        "condition_summary": None,
        "fw_fixed_version": None,
        "fw_released_on": None,
    }
    return row | values


def version(number: int, request_row: Row, **values: Any) -> Row:
    """예시 묶음의 판본 행을 만든다.

    Args:
        number: 판본 번호.
        request_row: 판정 기준 요청서 행.
        **values: 기본값과 다른 열.

    Returns:
        Row: condition_comparison_versions 한 행.
    """
    row: Row = {
        "version": number,
        "improvement_request_id": request_row["id"],
        "request_version": request_row["version"],
        "data_origin": "synthetic",
        "derivation_kind": "DERIVED",
        "known_at": datetime(2026, 10, 28, 1, 0, tzinfo=UTC),
    }
    return row | values


def comparison(number: int, latest: int = 1) -> Row:
    """예시 묶음의 비교 결과 행을 만든다.

    Args:
        number: 묶음 번호(고장 ID 끝자리).
        latest: 최신 판본.

    Returns:
        Row: condition_comparisons 한 행.
    """
    return {
        "id": UUID(f"5b0f6a52-3c1e-4c7a-9d10-0b6f2a7e9c0{number}"),
        "product_code": PRODUCT,
        "fault_id": f"FLT-90{number}",
        "latest_version": latest,
    }


SAME = item(
    "CONDITION",
    "site_condition",
    judgement="CONFIRMED",
    finding="SAME",
    baseline_value="실내 설치·상온",
    baseline_evidence_ids=["ev-1"],
    target_value="실내 설치·상온",
    target_evidence_ids=["ev-1"],
    masked_summary="현장 조건은 재발과 무관",
    evidence_ids=["ev-1"],
    provenance_kind="human_input",
    decided_by_name="C/S 담당자 A(합성)",
)
DIFFERS = item(
    "CONDITION",
    "application",
    judgement="SUSPECTED",
    finding="DIFFERS",
    baseline_value="정격 부하 운전",
    baseline_evidence_ids=["ev-1"],
    target_value="정격 상한 근처 운전",
    target_evidence_ids=["ev-1"],
    masked_summary="정격 상한 근처 운전 사례에서 재발 의심",
    evidence_ids=["ev-1"],
    provenance_kind="ai_draft",
)
UNCONFIRMED = item(
    "CONDITION",
    "operating_condition",
    judgement="UNCONFIRMED",
    unconfirmed_reason="VALUE_NOT_PROVIDED",
    baseline_value="상온 운전",
    baseline_evidence_ids=["ev-1"],
    target_missing_reason="NOT_PROVIDED",
    summary_missing_reason="NOT_PROVIDED",
    evidence_ids=["ev-1"],
)
HIDDEN = item(
    "CONDITION",
    "load_profile",
    judgement="CONFIRMED",
    finding="DIFFERS",
    baseline_value="주간 운전",
    baseline_evidence_ids=["ev-1"],
    target_value="야간 연속 운전(현장 ***)",
    target_evidence_ids=["ev-2"],
    masked_summary="야간 연속 운전 사례에서만 재발",
    evidence_ids=["ev-1", "ev-2"],
    provenance_kind="human_input",
    decided_by_name="C/S 담당자 B(합성)",
)
FIRMWARE_SUSPECTED = item(
    "CONDITION",
    "current_fw",
    judgement="SUSPECTED",
    finding="DIFFERS",
    baseline_value="1.x",
    baseline_evidence_ids=["ev-1"],
    target_value="2.x",
    target_evidence_ids=["ev-1"],
    masked_summary="펌웨어 2.x 사례에서만 재발 의심",
    evidence_ids=["ev-1"],
    provenance_kind="ai_draft",
)
FIRMWARE_CONFIRMED = FIRMWARE_SUSPECTED | {
    "judgement": "CONFIRMED",
    "provenance_kind": "human_input",
    "decided_by_name": "C/S 담당자 A(합성)",
    "decided_at": datetime(2026, 10, 27, 11, 0, tzinfo=UTC),
}
CAUSE = item(
    "CAUSE",
    "FIRMWARE",
    judgement="SUSPECTED",
    masked_summary="펌웨어 판올림 후 재발 감소",
    evidence_ids=["ev-1"],
    provenance_kind="ai_draft",
)
SIGNAL = item("DOCUMENT_SIGNAL", "VALUE_CONFLICT", evidence_ids=["ev-3"])
DIFFERS_REQUEST = request(
    2,
    condition_summary="정격 상한 근처 운전 사례 위주(합성)",
    fw_fixed_version="SYN-FW-2.1",
    fw_released_on=date(2026, 10, 15),
)

# 이름 → (설명, 비교 행, 요청서 행, [(판본 행, 항목, 근거)], 조회 판본, 볼 수 있는 문의).
# 볼 수 있는 문의가 None이면 전체를 보는 사용자(cs_agent)로 조회한 결과다.
SEEDS: dict[str, tuple[str, Row, Row, list[tuple[Row, list[Row], list[Row]]], int, Any]] = {
    "same": (
        "동일: 확인했고 조건 차이가 없다(CONFIRMED + SAME)",
        comparison(1),
        request(1),
        [(version(1, request(1)), [SAME], [CASE_EVIDENCE])],
        1,
        None,
    ),
    "differs": (
        "차이: 의심 근거가 있으나 사람이 확정하지 않았다(SUSPECTED + DIFFERS). 원인·문서 신호 포함",
        comparison(2),
        DIFFERS_REQUEST,
        [
            (
                version(1, DIFFERS_REQUEST),
                [DIFFERS, CAUSE, SIGNAL],
                [CASE_EVIDENCE, DOCUMENT_EVIDENCE],
            )
        ],
        1,
        None,
    ),
    "unconfirmed": (
        "미확인: 대상 쪽 값이 자료에 없어 판단하지 않는다(VALUE_NOT_PROVIDED)",
        comparison(3),
        request(3),
        [(version(1, request(3)), [UNCONFIRMED], [CASE_EVIDENCE])],
        1,
        None,
    ),
    "hidden": (
        "가림: 판정에 쓰인 문의 하나가 조회 범위 밖이다. 판정은 유지하고 값·요약·근거만 숨긴다",
        comparison(4),
        request(4),
        [(version(1, request(4)), [HIDDEN], [CASE_EVIDENCE, TICKET_EVIDENCE])],
        1,
        {VISIBLE_TICKET},
    ),
    "past_version": (
        "판본 충돌: ?version=3 조회. 최신은 4라 is_latest=false다",
        comparison(0, latest=4),
        request(0),
        [
            (version(3, request(0)), [FIRMWARE_SUSPECTED], [CASE_EVIDENCE]),
            (version(4, request(0), request_version=6), [FIRMWARE_CONFIRMED], [CASE_EVIDENCE]),
        ],
        3,
        None,
    ),
}


def response(name: str) -> SuccessResponse[Any]:
    """예시 하나를 실제 응답 경로와 같은 함수로 만든다.

    Args:
        name: SEEDS의 이름.

    Returns:
        SuccessResponse[Any]: 그 예시의 성공 응답.
    """
    _, compared, request_row, versions, wanted, visible = SEEDS[name]
    version_row, items, evidence = next(v for v in versions if v[0]["version"] == wanted)
    shown, kept, hidden_count = hide(items, evidence, visible)
    return SuccessResponse(
        data=build(compared, version_row, request_row, shown, kept, hidden_count), meta=META
    )


def openapi_examples() -> dict[str, Any]:
    """조회 경로 200 응답에 붙일 이름 있는 예시를 만든다.

    Returns:
        dict[str, Any]: OpenAPI examples 객체.
    """
    return {
        name: {"summary": seed[0], "value": response(name).model_dump(mode="json")}
        for name, seed in SEEDS.items()
    }
