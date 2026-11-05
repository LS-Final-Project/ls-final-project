"""KAN-92 CS 정규화 입력의 검수 경계. 파일 적재·모델 전송은 하지 않는다."""

from collections.abc import Mapping
from typing import Literal

from backend.engine.cs.classification import PreparedInput, build_input


def prepare_cs_input(
    record: Mapping[str, object],
    *,
    privacy_review_complete: bool = False,
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]] | None = None,
    strip_phone_annotation: bool = False,
) -> PreparedInput:
    """검수 대기 입력을 보류하고 기존 입력 정제 계약(build_input)을 적용한다.

    기본은 제품 기본 경로(v4)라 PHONE 상담원 표기를 그대로 둔다(사용자 결정 2026-10-29).
    분석용으로 표기를 지우는 v3 경로는 strip_phone_annotation=True로만 쓴다.

    Args:
        record: 정규화한 CS 문의. 레코드 안의 검수/승인 표시는 신뢰하지 않는다.
        privacy_review_complete: 호출자가 별도 검수 근거로 확인한 값. 실제 검수 상태
            매핑은 미확정이며 이 인자로 검수·전송 승인을 생성하지 않는다.
        sensitive_values: 검수에서 식별한 전체 원값 매핑. 빈 매핑만으로 검수 완료가 아니다.
        strip_phone_annotation: build_input에 그대로 넘긴다. True면 v3(PHONE 표기 제거)다.

    Returns:
        PreparedInput: 네 필드 payload와 분리된 감사 메타데이터. 외부 전송 승인이 아니다.

    Raises:
        ValueError: 검수/매핑 미확인 또는 입력 오류. 원문 재전송 폴백은 없다.
    """
    if privacy_review_complete is not True:
        raise ValueError("CS_PRIVACY_REVIEW_REQUIRED")
    if sensitive_values is None:
        raise ValueError("CS_SENSITIVE_VALUES_REQUIRED")
    if not isinstance(record, Mapping) or not isinstance(sensitive_values, Mapping):
        raise ValueError("CS_INVALID_INPUT")
    return build_input(
        record, sensitive_values=sensitive_values, strip_phone_annotation=strip_phone_annotation
    )
