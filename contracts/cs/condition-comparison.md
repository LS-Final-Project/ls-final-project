# C/S 조건 비교 결과 계약 (KAN-114)

**상태: 제안(미구현).** 구현 전 김종하 화면 연동의 필드 요청을 반영할 예정이다. 이 문서는 응답 형식과 판정·전이 규칙의 초안이며 DTO·라우터·OpenAPI·마이그레이션은 아직 없다. 구현하면 원본은 해당 기능의 `schemas.py`·`routes.py`가 되고 이 문서는 그에 맞춰 고친다. 기획서는 수정하지 않는다.

같은 고장 묶음 안에서 사례별 사용 조건과 원인을 비교해 **확정·의심·미확인**을 구분하고, 문서 문의 신호를 원인 판정과 섞지 않는다. [개선요청](improvements.md)의 `condition_status`, [문의](tickets.md)의 해결 사례, [데이터 계약](../../docs/data-contract.md)의 출처·근거 규칙, [권한](../platform/permissions.md) 1.3·4.1을 따른다.

## 비교 단위

- 비교 결과 1건 = `(product_code, fault_id)` 묶음 1개. `fault_id`는 사람이 확인한 해결 사례의 `FLT-000` 형식이다. 고장 개선요청(`FAULT`)의 묶음 키와 같다.
- 조건이 다른 사례는 다른 묶음으로 나눈다는 개선요청 규칙을 유지한다. 비교 결과가 묶음 키를 바꾸지 않는다.
- 제안 경로(미구현): `GET /api/v1/cs/condition-comparisons?product_code=&fault_id=`, `GET /api/v1/cs/improvement-requests/{id}/condition-comparison`. 확정·재검토 쓰기 경로는 구현 단위에서 정하며, 아래 전이 규칙을 따른다.

## 판정

조건 항목(`conditions`)과 원인(`causes`)마다 판정 하나를 둔다.

| 판정 | 의미 |
| --- | --- |
| `CONFIRMED` | 사람(cs_agent)이 확정했다. 이미 알려진(`known_at`가 판정 시각 이전인) 해결 사례 근거가 1개 이상 있다 |
| `SUSPECTED` | 모델 후보이거나 근거가 일부만 있다. 사람이 확정하지 않았다 |
| `UNCONFIRMED` | 근거가 없거나 부족해 판단하지 못했다 |

조건 항목에는 `finding`을 함께 둔다. `DIFFERS`(사례 간 조건이 다름) 또는 `SAME`(같음)이며, `UNCONFIRMED`일 때만 null이다. 따라서 "확인했고 조건 차이 없음"(`CONFIRMED` + `SAME`)과 "모름"(`UNCONFIRMED` + null)을 구분한다.

원인의 `cause_type`은 해결 사례의 원인 유형(`SITE_CONDITION`·`HARDWARE`·`SETTING`·`WEAR_PART`·`FIRMWARE`)을 쓴다. 새 enum을 만들지 않는다.

## 상태 전이

| 현재 | 다음 | 작성 주체(`provenance_kind`) | 조건 |
| --- | --- | --- | --- |
| (없음) | `UNCONFIRMED` | `system_record` | 묶음의 비교 대상 항목을 만들 때 기본값 |
| `UNCONFIRMED` | `SUSPECTED` | `ai_draft` 또는 `human_input` | 후보 또는 근거 1개 이상 |
| `UNCONFIRMED`·`SUSPECTED` | `CONFIRMED` | `human_input`(cs_agent) | 알려진 해결 사례 근거 1개 이상, 확정 사유 |
| `CONFIRMED` | `SUSPECTED`·`UNCONFIRMED` | `human_input`(cs_agent) | 근거 제외 또는 재검토, 사유 필수 |
| `SUSPECTED` | `UNCONFIRMED` | `human_input` 또는 `system_record` | 근거 제외로 남은 근거가 0개 |
| 모든 판정 | `CONFIRMED` | `ai_draft` | **금지.** 모델 후보는 사람 확정 전 `SUSPECTED` 이하 |

- 판정 변경마다 판본(`version`)이 오르고 이전 판정·주체·시각·사유를 이력으로 남긴다. 판정 행을 지우지 않는다.
- 응답의 `provenance_kind`는 현재 판정을 만든 주체다. 모델 후보는 `ai_draft`, 사람 확정·재검토는 `human_input`, 기본값·근거 제외에 따른 자동 전이는 `system_record`다.

## 개선요청 `condition_status`와의 관계

개선요청의 `condition_status`(`CONFIRMED`/`UNKNOWN`)에 새 값을 더하지 않는다. 비교 결과의 조건 항목이 1개 이상이고 **모두** `CONFIRMED`일 때만 요청을 `CONFIRMED`로 둘 수 있다. 하나라도 `SUSPECTED`·`UNCONFIRMED`면 요청은 `UNKNOWN`이다. 응답의 `condition_status`는 이 규칙으로 계산한 값이며, 요청을 실제로 바꾸는 것은 개선요청 쓰기 경로(KAN-112·116)다.

## 문서 문의 신호

문서 문의 신호는 원인 판정이 아니다. 별도 목록 `document_signals`에 개선요청과 같은 `doc_signal` 4종(`NOT_FOUND`·`VALUE_CONFLICT`·`BROKEN_LINK`·`REPEATED_SPEC`)으로 둔다. 문서 신호가 있어도 조건·원인 판정을 바꾸지 않으며, 이어지는 개선요청은 문서 요청(`DOCUMENT`)이다.

## 비교 내용과 근거의 분리

- `conditions`·`causes`·`document_signals`는 **마스킹된 비교 내용**이다. 항목 코드, 판정, `finding`, 마스킹된 요약(`masked_summary`)과 근거 참조 ID(`evidence_ids`)만 담는다.
- `evidence`는 **근거 참조**다. `case_id`·`ticket_id`·`evidence_location`과 근거 자신의 출처(`data_origin`·`derivation_kind`·`known_at`)만 담고 본문을 담지 않는다.
- `evidence_location`은 텍스트 없는 위치다. 문의·해결 사례는 `{ "source": "ticket" | "resolution_case", "field": "<필드 이름>" }`, 문서는 데이터 계약의 인용 위치(`doc_id`·`version`·`page`·`char_start`·`char_end` 등)를 쓴다. 위치를 추적할 수 없으면 근거로 채택하지 않는다.
- 응답에 원문(제목·본문·메일)·정답 열·개인정보·시리얼 원값·RMA 번호를 넣지 않는다. `masked_summary`는 저장 전에 마스킹한 값이며, 마스킹할 수 없으면 null로 두고 판정은 유지한다.

## 미확인 보존

- `UNCONFIRMED` 항목은 응답에서 빼지 않고 null 판정이나 "해당 없음"으로 바꾸지 않는다. 미확인 사유(`unconfirmed_reason`: `NO_EVIDENCE`·`EVIDENCE_REMOVED`·`NOT_YET_KNOWN`)를 함께 둔다.
- `NOT_YET_KNOWN`은 근거의 `known_at`이 아직 오지 않은 경우다. 미래의 해결 기록으로 과거 판정을 채우지 않는다.
- 판정 집계(`summary`)는 조건 항목(`conditions`)의 세 판정 건수를 모두 낸다. 원인 판정은 세지 않는다. 미확인을 0이나 확정으로 합치지 않는다.

## 출처 표기

[데이터 계약](../../docs/data-contract.md#식별자와-시간)의 두 축을 그대로 쓴다.

- `derivation_kind`: 비교 결과와 그 안의 판정(확정 포함)은 모두 근거에서 계산·판단한 파생 기록이므로 `DERIVED`다. 사람 확정이라고 `SYSTEM`이나 `PROVIDED`로 바꾸지 않는다. 근거 항목은 근거 자신의 값(예: 제공 해결 사례는 `PROVIDED`)을 유지한다.
- `provenance_kind`: 판정을 만든 주체(`ai_draft`·`human_input`·`system_record`)는 이 축으로만 구분한다.
- `data_origin`: 근거의 출처를 따른다. 근거가 모두 `synthetic`이면 `synthetic`, 실제·합성·시연 자료가 섞이면 `demo`다. 합성·가정 근거를 포함한 결과를 `real`로 승격하지 않는다.

## 조회 범위와 오류

[권한](../platform/permissions.md) 1.3의 검사 순서를 그대로 쓴다.

- 조회: cs_agent와 C/S 부서 manager는 전체. dept_staff와 다른 부서 manager는 4.1 "개선요청 조회"에서 볼 수 있는 요청에 연결된 비교만 본다. sales는 볼 수 없다.
- 근거 중 사용자의 문의 조회 범위(4.1 "문의 목록·상세 조회") 밖의 문의는 `evidence`에 넣지 않고 `hidden_evidence_count`로 건수만 낸다. 판정은 숨기지 않는다.
- 확정·재검토 같은 판정 변경은 cs_agent만 한다.
- 오류: 범위 밖 404(존재를 숨김), 보이지만 행동 불가 403, 허용하지 않는 전이(예: `ai_draft`의 확정) 409, 판본 불일치 409/412, 근거 없는 확정 422. 서비스별 오류 코드 이름은 개선요청 계약처럼 구현 단위에서 `CS_` 접두로 정한다.

## 응답 예시 (합성, 마스킹 값)

```json
{
  "data": {
    "comparison_id": "5b0f6a52-3c1e-4c7a-9d10-0b6f2a7e9c01",
    "product_code": "SYN-PRD-01",
    "fault_id": "FLT-017",
    "version": 3,
    "data_origin": "synthetic",
    "derivation_kind": "DERIVED",
    "known_at": "2026-10-28T01:00:00Z",
    "condition_status": "UNKNOWN",
    "summary": { "confirmed": 1, "suspected": 1, "unconfirmed": 1 },
    "conditions": [
      {
        "item_code": "FIRMWARE_VERSION",
        "judgement": "CONFIRMED",
        "finding": "DIFFERS",
        "masked_summary": "펌웨어 2.x 사례에서만 재발",
        "provenance_kind": "human_input",
        "decided_at": "2026-10-27T09:00:00Z",
        "evidence_ids": ["ev-1", "ev-2"]
      },
      {
        "item_code": "INSTALL_ENVIRONMENT",
        "judgement": "SUSPECTED",
        "finding": "DIFFERS",
        "masked_summary": "고온 환경 의심(현장 ***)",
        "provenance_kind": "ai_draft",
        "decided_at": "2026-10-27T10:00:00Z",
        "evidence_ids": ["ev-2"]
      },
      {
        "item_code": "LOAD_PROFILE",
        "judgement": "UNCONFIRMED",
        "finding": null,
        "unconfirmed_reason": "NO_EVIDENCE",
        "masked_summary": null,
        "provenance_kind": "system_record",
        "decided_at": "2026-10-27T08:00:00Z",
        "evidence_ids": []
      }
    ],
    "causes": [
      {
        "cause_type": "FIRMWARE",
        "judgement": "SUSPECTED",
        "masked_summary": "펌웨어 판올림 후 재발 감소",
        "provenance_kind": "ai_draft",
        "decided_at": "2026-10-27T10:00:00Z",
        "evidence_ids": ["ev-1"]
      }
    ],
    "document_signals": [
      {
        "doc_signal": "VALUE_CONFLICT",
        "provenance_kind": "system_record",
        "evidence_ids": ["ev-3"]
      }
    ],
    "evidence": [
      {
        "evidence_id": "ev-1",
        "case_id": "0f2d7c1e-8a44-4b1a-9e3e-6a1c2b3d4e01",
        "ticket_id": "7a1e2b3c-4d5e-4f60-8a71-b2c3d4e5f601",
        "evidence_location": { "source": "resolution_case", "field": "cause" },
        "data_origin": "synthetic",
        "derivation_kind": "PROVIDED",
        "known_at": "2026-10-20T00:00:00Z"
      },
      {
        "evidence_id": "ev-2",
        "case_id": "1a2b3c4d-5e6f-4a70-8b81-c9d0e1f2a302",
        "ticket_id": "8b2f3c4d-5e6f-4a71-9b82-c3d4e5f6a702",
        "evidence_location": { "source": "ticket", "field": "firmware_version" },
        "data_origin": "synthetic",
        "derivation_kind": "PROVIDED",
        "known_at": "2026-10-22T00:00:00Z"
      },
      {
        "evidence_id": "ev-3",
        "case_id": null,
        "ticket_id": null,
        "evidence_location": { "doc_id": "doc-syn-manual", "version": "docv-3", "page": 12, "char_start": 340, "char_end": 362 },
        "data_origin": "synthetic",
        "derivation_kind": "PROVIDED",
        "known_at": "2026-10-01T00:00:00Z"
      }
    ],
    "hidden_evidence_count": 0
  },
  "meta": { "request_id": "11111111-1111-4111-8111-111111111199" }
}
```

예시의 `condition_status`는 `SUSPECTED`·`UNCONFIRMED` 항목이 있어 `UNKNOWN`이다. 값은 모두 합성이며 실제 제품·현장·사례가 아니다.

## 이 계약의 범위 밖

- KAN-114 제목의 "반복 이슈 의미 보조 분석"(의미 유사도로 묶음 후보를 찾는 일)은 이 계약에 넣지 않는다. 후보 자동 생성은 KAN-113의 범위다.
- 비교 계산 방식·모델 프롬프트·평가 지표와 합격선은 정하지 않는다([평가 명세](../../docs/evaluation.md)의 KAN-82 잠금 대상).
- 실제 구현, 화면, OpenAPI 등록은 이후 단위다.
