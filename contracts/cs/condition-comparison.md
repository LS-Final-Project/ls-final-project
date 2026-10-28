# C/S 조건 비교 결과 계약 (KAN-114)

**상태: 조회·생성·판정 쓰기·근거 제외 구현.** 조회 2개, 비교 생성, 판정 쓰기·재검토, 근거 제외 경로와 저장 구조를 구현했다. 문서 신호·문서 근거 쓰기는 아직 없다. 요청·응답 형식의 원본은 `backend/api/cs/comparisons/schemas.py`·`routes.py`, 전이 규칙의 원본은 `rules.py`, 저장은 `infra/migrations/cs/20261028_KAN-114_condition_comparisons.sql`·`20261028_KAN-114_condition_comparisons_writes.sql`·`20261028_KAN-114_condition_comparisons_writes_removals.sql`(공용 DB는 운영자가 따로 적용), 합성 예시 5종의 원본은 `backend/api/cs/comparisons/examples.py`이며 `docs/openapi/cs.json` 200 응답 examples로 나간다. 기획서는 수정하지 않는다.

같은 고장 묶음 안에서 사례별 사용 조건과 원인을 비교해 **확정·의심·미확인**을 구분하고, 문서 문의 신호를 원인 판정과 섞지 않는다. [개선요청](improvements.md)의 `condition_status`, [문의](tickets.md)의 해결 사례, [데이터 계약](../../docs/data-contract.md)의 출처·근거 규칙, [권한](../platform/permissions.md) 1.3·4.1을 따른다.

## 비교 단위와 판본

- 비교 결과 1건 = `(product_code, fault_id)` 묶음 1개. `fault_id`는 사람이 확인한 해결 사례의 `FLT-000` 형식이다. 고장 개선요청(`FAULT`)의 묶음 키와 같다.
- 조건이 다른 사례는 다른 묶음으로 나눈다는 개선요청 규칙을 유지한다. 비교 결과가 묶음 키를 바꾸지 않는다.
- 대상 요청서: `improvement_request_id`는 이 비교가 연결된 개선요청, `request_version`은 판정할 때 기준으로 삼은 요청서 판본(개선요청의 `version`)이다. 요청서가 없는 묶음은 둘 다 null이다. 화면은 요청서의 현재 `version`과 `request_version`이 다르면 "이전 요청서 판본 기준"으로 표시한다.
- 결과 판본: `version`은 비교 결과의 판본이며 판정 변경마다 1씩 오른다. 판본마다 내용을 고치지 않고 남긴다. `latest_version`은 지금 최신 판본, `is_latest`는 응답이 최신 판본인지다. 계약 판본은 별도 필드 없이 경로의 `/v1`로 식별하고, 호환되지 않는 변경은 새 경로로 낸다.
- 경로: `GET /api/v1/cs/condition-comparisons?product_code=&fault_id=`, `GET /api/v1/cs/improvement-requests/{request_id}/condition-comparison`(그 고장 요청서의 묶음 비교). 기본은 최신 판본이고, `?version=N`이면 그 판본을 `is_latest=false`(N이 최신이면 `true`)로 돌려준다. 없는 비교·판본은 404 `CS_COMPARISON_NOT_FOUND`다.
- 쓰기 경로(cs_agent만, 사람의 판정 `human_input`으로 기록):
  - `POST /api/v1/cs/improvement-requests/{request_id}/condition-comparison`: 그 고장 요청서 묶음의 비교를 만든다(201). 판본 1은 사용 조건 8개 모두 `UNCONFIRMED`(`NO_EVIDENCE`, `system_record`, 기준·대상·요약 null과 `NOT_PROVIDED`)이고 원인·문서 신호는 없다. 묶음에 이미 있으면 409 `CS_COMPARISON_EXISTS`.
  - `POST .../condition-comparison/judgements`: 판정 쓰기와 재검토. 본문은 읽은 `version`, 바꿀 항목(`items`: 조건은 `baseline`·`target`의 값·사유·근거 ID와 `finding`, 원인은 판정만), 새 근거(`evidence`: `evidence_id`·`ticket_id`·`case_id`·`evidence_location`). 보낸 항목만 바뀌고 나머지 항목·근거는 이전 판본에서 이어져 새 판본 전체가 된다. 원인은 처음 쓸 때 생긴다. 판정 기준 요청서는 경로의 요청서와 그 현재 판본이다.
  - 근거의 `data_origin`·`derivation_kind`·`known_at`은 서버가 원본 문의·해결 사례에서 읽는다. 문의는 묶음과 같은 제품, 사례는 그 문의의 것이고 같은 고장이어야 한다. 항목·기준·대상이 가리키는 근거 ID는 새 판본 근거에 있어야 하고, 값이 있는 쪽은 그 값이 나온 근거 ID가 있어야 한다(DB CHECK). 어기면 422 `CS_EVIDENCE_INVALID`.
  - `POST .../condition-comparison/evidence/{evidence_id}/remove`: 근거 하나를 사유와 함께 뺀다(개선요청의 근거 제외와 같은 방식). 본문은 읽은 `version`과 `reason`(필수). 최신 판본에 없거나 이미 뺀 근거는 404 `CS_EVIDENCE_NOT_FOUND`. 근거 하나마다 새 판본이 생긴다.
    - 그 근거 ID는 모든 항목의 `evidence_ids`·기준·대상 근거 ID에서 빠진다. 기준·대상 중 근거 ID가 비게 된 쪽의 값은 null과 `NOT_PROVIDED`가 된다.
    - 판정 조건이 그대로면 판정을 유지한다. 의심 항목에 이미 알려진 근거가 하나도 남지 않으면 `system_record`가 `UNCONFIRMED`로 내린다(사유는 아래 미확인 사유 계산, 보통 `EVIDENCE_REMOVED`). 미확인 항목은 사유를 다시 계산해 바뀌면 `system_record`로 갱신한다.
    - 시스템은 사람의 확정이나 알려진 근거가 남은 의심을 지우지 않는다. 제외가 그 판정의 조건을 깨면 409 `CS_INVALID_TRANSITION`이고 먼저 판정 쓰기로 재검토한다. 예: 확정 항목의 유일한 해결 사례 근거 제외, 또는 알려진 근거가 남은 의심 항목에서 대상 값의 유일한 근거를 빼 그 쪽 값이 비게 되는 제외.
    - 제외 이력은 `cs.condition_comparison_removals`(근거 ID, 제외로 생긴 판본, 그 근거를 인용하던 항목, 사유, 제외한 사람)에 남고 서비스는 읽기·추가만 한다. 이전 판본은 그대로 남는다. 뺀 근거 ID는 다시 쓸 수 없다(422 `CS_EVIDENCE_INVALID`). 제외 이력은 아직 응답에 내지 않는다.
  - 같은 근거 ID를 다시 쓰면 422다.
  - 문서 근거·문서 신호 쓰기는 문서 원천 표가 생긴 뒤 넣는다. 지금은 원천이 없어 출처와 `known_at`을 서버가 확인할 수 없다.
  - 값과 요약은 저장 전에 마스킹한 값을 보낸다. 서버는 마스킹 여부를 판단하지 않는다.
  - 쓰기마다 `cs.audit_events`에 한 행(`entity_type` `condition_comparison`, 새 판본, 항목별 `{from, to, reason}`)을 남긴다. 개선요청의 `condition_status`는 바꾸지 않는다.
- 저장: 판정이 바뀔 때마다 그 판본의 항목·근거 전체를 새로 쓰고 이전 판본은 고치거나 지우지 않는다. 서비스 계정은 최신 판본 번호(`latest_version`)만 갱신할 수 있다.

## 판정

조건 항목(`conditions`)과 원인(`causes`)마다 판정(`judgement`) 하나를 둔다.

| 판정 | 의미 |
| --- | --- |
| `CONFIRMED` | 사람(cs_agent)이 확정했다. 이미 알려진(`known_at`가 판정 시각 이전인) 해결 사례 근거가 1개 이상 있다 |
| `SUSPECTED` | 의심 근거가 1개 이상 있지만 사람이 확정하지 않았다. 근거 없는 모델 후보는 `SUSPECTED`가 아니라 `UNCONFIRMED`다 |
| `UNCONFIRMED` | 근거가 없거나 부족해 판단하지 못했다. `unconfirmed_reason` 필수 |

- **항목 식별**: `item_code`는 화면이 키로 쓰는 고정 코드이고 한 비교 안에서 유일하다. 문의의 `provided_info`가 쓰는 info_field_codebook 코드 중 사용 조건 8개(`application`·`site_condition`·`operating_condition`·`load_profile`·`current_fw`·`install_date`·`string_config`·`battery_age`)를 그대로 쓴다. `item_label`은 서버가 주는 한국어 표시명이다. 목록은 `schemas.py`의 `ConditionCode` 한 곳이며 화면은 모르는 코드도 `item_label`로 표시한다.
- **비교 기준과 대상**: 조건 항목마다 `baseline`(비교 기준: 묶음의 이미 알려진 해결 사례 쪽 조건)과 `target`(비교 대상: 요청서가 다루는 문의 쪽 조건)을 따로 둔다. 각각 `masked_value`(마스킹된 값 또는 요약)와 `missing_reason`을 담는다. 요청서가 없는 묶음에서는 `target`이 묶음에 연결된 미해결 문의 쪽 조건이며, 그런 문의도 없으면 `target`은 null 값과 `NOT_PROVIDED`다.
- **`finding`**: 조건 항목에는 `DIFFERS`(기준과 대상의 조건이 다름) 또는 `SAME`(같음)을 둔다. 키는 항상 있고 **`UNCONFIRMED`일 때만 null**이다. `CONFIRMED`·`SUSPECTED`는 반드시 `DIFFERS`나 `SAME`을 가진다. 따라서 "확인했고 조건 차이 없음"(`CONFIRMED` + `SAME`)과 "모름"(`UNCONFIRMED` + null)을 구분한다. 원인에는 `finding`이 없다.
- `judgement`(비교 항목 판정)와 개선요청의 `condition_status`는 서로 다른 enum이다. 아래 관계 절을 따른다.

원인의 `cause_type`은 해결 사례의 원인 유형(`SITE_CONDITION`·`HARDWARE`·`SETTING`·`WEAR_PART`·`FIRMWARE`)을 쓴다. 새 enum을 만들지 않는다.

## 값이 없거나 가려진 경우

- `masked_value`·`masked_summary`가 null이면 사유를 같은 객체의 `missing_reason`(항목 요약은 `summary_missing_reason`)에 둔다. 값이 있으면 사유는 null이다.

| 사유 | 의미 | 판정에 미치는 영향 |
| --- | --- | --- |
| `NOT_PROVIDED` | 근거 자료에 그 값이 없다 | 기준·대상 중 하나라도 이 사유면 그 항목은 `UNCONFIRMED`, `finding` null. 근거는 있는데 값이 없으면 `unconfirmed_reason: VALUE_NOT_PROVIDED`, 근거 자체가 없으면 `NO_EVIDENCE`다. 빈 값을 `SAME`이나 `DIFFERS`로 판단하지 않는다 |
| `HIDDEN_BY_SCOPE` | 사용자의 조회 범위 밖 근거에서 온 값이다 | 판정과 `finding`은 유지한다(조회 범위 절) |
| `MASKING_FAILED` | 마스킹할 수 없어 내지 않는다 | 판정과 `finding`은 유지한다 |

- 사유가 둘 이상 해당하면 `HIDDEN_BY_SCOPE` > `NOT_PROVIDED` > `MASKING_FAILED` 순으로 하나만 낸다. 숨긴 근거에서 온 값은 자료에 없었더라도 `HIDDEN_BY_SCOPE`이며 판정과 `finding`을 유지한다(값의 존재 여부도 숨긴다).

## 상태 전이

| 현재 | 다음 | 작성 주체(`provenance_kind`) | 조건 |
| --- | --- | --- | --- |
| (없음) | `UNCONFIRMED` | `system_record` | 묶음의 비교 대상 항목을 만들 때 기본값 |
| `UNCONFIRMED` | `SUSPECTED` | `ai_draft` 또는 `human_input` | 의심 근거 1개 이상(후보만으로는 올리지 않음), 기준·대상 어느 쪽도 `NOT_PROVIDED`가 아님 |
| `UNCONFIRMED`·`SUSPECTED` | `CONFIRMED` | `human_input`(cs_agent) | 알려진 해결 사례 근거 1개 이상, 기준·대상 어느 쪽도 `NOT_PROVIDED`가 아님, 확정 사유 |
| `CONFIRMED` | `SUSPECTED`·`UNCONFIRMED` | `human_input`(cs_agent) | 근거 제외 또는 재검토, 사유 필수 |
| `SUSPECTED` | `UNCONFIRMED` | `human_input` 또는 `system_record` | 근거 제외로 남은 근거가 0개 |
| 모든 판정 | `CONFIRMED` | `ai_draft` | **금지.** 모델 후보는 사람 확정 전 `SUSPECTED` 이하 |
| 같은 판정 | 같은 판정 | 그 판정으로 가는 행의 주체(`UNCONFIRMED`는 `human_input`·`system_record`) | 값·근거만 고치는 쓰기. 그 판정의 조건을 다시 지킨다(확정은 사유 포함) |

- 표의 "`NOT_PROVIDED`가 아님" 조건은 `baseline`/`target`이 있는 조건 항목에만 적용하고, 원인은 근거 수 조건만 따른다.
- `SUSPECTED` → `UNCONFIRMED`: 사람(`human_input`)은 사유를 적고, `system_record`는 근거 제외로 남은 근거가 0개일 때만 쓴다(근거 제외 경로에서 구현).
- 근거 수는 판정 시각에 이미 알려진(`known_at` ≤ 판정 시각) 근거만 센다. 아직 알려지지 않은 근거만 있으면 `UNCONFIRMED`(`NOT_YET_KNOWN`)로 쓴다.
- 오류: 표에 없는 전이·주체 409 `CS_INVALID_TRANSITION`, 근거 부족 422 `CS_EVIDENCE_REQUIRED`, 사유 없음 422 `CS_REASON_REQUIRED`, `NOT_PROVIDED` 쪽이 있는 판단 422 `CS_VALUE_NOT_PROVIDED`. 지금 HTTP 쓰기는 사람만 하며 `ai_draft` 쓰기는 같은 규칙(`rules.py`)으로 KAN-113 작업자 경로에서 쓴다.

- 판정 변경마다 결과 판본(`version`)이 오르고 이전 판정·주체·시각·사유를 이력으로 남긴다. 판정 행을 지우지 않는다.
- 판정 쓰기는 읽은 `version`을 보낸다. 최신 판본과 다르면 409 `CS_COMPARISON_VERSION_CONFLICT`이며 화면은 최신 판본을 다시 조회해 사용자에게 변경을 재확인하게 한다.
- 응답의 `provenance_kind`는 현재 판정을 만든 주체다. 모델 후보는 `ai_draft`, 사람 확정·재검토는 `human_input`, 기본값·근거 제외에 따른 자동 전이는 `system_record`다.

## 개선요청 `condition_status`와의 관계

개선요청의 `condition_status`(`CONFIRMED`/`UNKNOWN`)와 비교 항목의 `judgement`(`CONFIRMED`/`SUSPECTED`/`UNCONFIRMED`)는 **별개 enum**이다. `UNKNOWN`을 `UNCONFIRMED`로, 또는 그 반대로 일대일 치환하지 않는다. `condition_status`에 새 값을 더하지 않는다.

비교 결과의 조건 항목이 1개 이상이고 **모두** `CONFIRMED`일 때만 요청을 `CONFIRMED`로 둘 수 있다. 하나라도 `SUSPECTED`·`UNCONFIRMED`이거나 조건 항목이 없으면 `UNKNOWN`이다. 응답의 `computed_condition_status`는 이 규칙으로 계산한 값이고, 요청서에 저장된 `condition_status`와 다를 수 있다. 요청을 실제로 바꾸는 것은 개선요청 쓰기 경로(KAN-112·116)다. 화면의 "전체 조건 확정"은 `computed_condition_status`가 `CONFIRMED`일 때만 표시한다.

## 선택 필드

값이 있을 때만 채우고, 없으면 null이다. 서버가 추정해 채우지 않으며 화면은 null을 "미확인"으로 표시한다.

- `fw_fixed_version`·`condition_summary`는 저장 전 마스킹한 값만 내고, 마스킹할 수 없으면 사유 필드 없이 null이다. 조회 범위가 요청서 조회와 같으므로 `HIDDEN_BY_SCOPE`는 해당하지 않는다.
- `fw_fixed_version`: 대상 요청서의 같은 이름 값. 요청서가 없으면 null.
- `fw_released_on`: 수정 펌웨어 배포일(date). 대상 요청서의 같은 이름 값이다. 요청서가 없으면 null.
- `condition_summary`: 대상 요청서의 사용 조건 요약(같은 이름 값). 요청서가 없으면 null.
- 항목의 `decided_by_name`(판정자 표시명)과 `decided_at`(판정 시각): `decided_by_name`은 `human_input` 판정에만 있고 `ai_draft`·`system_record`면 null이다. 표시명은 플랫폼 중계 단계에서 붙인다. C/S는 사용자 표시명을 읽지 못해 현재는 항상 null이며 판정자 ID만 저장한다.
- 점수·신뢰도 필드는 두지 않는다.

## 문서 문의 신호

문서 문의 신호는 원인 판정이 아니다. 별도 목록 `document_signals`에 개선요청과 같은 `doc_signal` 4종(`NOT_FOUND`·`VALUE_CONFLICT`·`BROKEN_LINK`·`REPEATED_SPEC`)으로 둔다. 문서 신호가 있어도 조건·원인 판정을 바꾸지 않으며, 이어지는 개선요청은 문서 요청(`DOCUMENT`)이다.

## 비교 내용과 근거의 분리

- `conditions`·`causes`·`document_signals`는 **마스킹된 비교 내용**이다. 항목 코드·표시명, 판정, `finding`, 마스킹된 기준·대상 값과 요약(`masked_summary`), 근거 참조 ID(`evidence_ids`)만 담는다.
- `evidence`는 **근거 참조**다. `case_id`·`ticket_id`·`evidence_location`과 근거 자신의 출처(`data_origin`·`derivation_kind`·`known_at`)만 담고 본문을 담지 않는다. 해결 사례 근거는 그 사례가 속한 문의 ID(`ticket_id`)를 반드시 함께 저장하며(DB CHECK), 조회 범위 숨김은 이 문의 ID로 판정한다.
- `evidence_location`은 텍스트 없는 위치다. 문의·해결 사례는 `{ "source": "ticket" | "resolution_case", "field": "<필드 이름>" }`, 문서는 데이터 계약의 인용 위치(`doc_id`·`version`·`page`·`char_start`·`char_end` 등)를 쓴다. 위치를 추적할 수 없으면 근거로 채택하지 않는다.
- 응답에 원문(제목·본문·메일)·정답 열·개인정보·시리얼 원값·RMA 번호를 넣지 않는다. `masked_value`·`masked_summary`는 저장 전에 마스킹한 값이며, 마스킹할 수 없으면 null(`MASKING_FAILED`)로 두고 판정은 유지한다.

## 미확인 보존

- `UNCONFIRMED` 항목은 응답에서 빼지 않고 null 판정이나 "해당 없음"으로 바꾸지 않는다. 미확인 사유(`unconfirmed_reason`: `NO_EVIDENCE`·`EVIDENCE_REMOVED`·`NOT_YET_KNOWN`·`VALUE_NOT_PROVIDED`)를 함께 둔다. 다른 판정에서는 null이다. 둘 이상 해당하면 `NOT_YET_KNOWN` > `EVIDENCE_REMOVED` > `VALUE_NOT_PROVIDED` > `NO_EVIDENCE` 순으로 하나만 낸다.
- 미확인 사유는 근거 상태로 정해진다(`rules.expected_reason`). `NOT_YET_KNOWN`: 인용 근거가 있고 모두 아직 알려지지 않음, `EVIDENCE_REMOVED`: 이 항목이 인용하던 근거가 제외된 이력이 있음, `VALUE_NOT_PROVIDED`: 인용 근거가 있고 기준·대상 중 `NOT_PROVIDED`가 있음, `NO_EVIDENCE`: 인용 근거 없음. 쓰기에서 보낸 사유가 이 값과 다르면 422 `CS_REASON_MISMATCH`이며 서버가 바꿔 쓰지 않는다.
- 알려진 근거가 있고 기준·대상 값이 모두 있으면 맞는 미확인 사유가 없으므로 사람도 `UNCONFIRMED`로 쓸 수 없다(422 `CS_REASON_MISMATCH`, 메시지 "SUSPECTED로 내리세요"). 따라서 확정을 재검토로 내릴 때 근거가 그대로면 `SUSPECTED`로 내린다. 이전 단위(#151)보다 좁힌 규칙이다.
- `NOT_YET_KNOWN`은 근거의 `known_at`이 아직 오지 않은 경우다. 미래의 해결 기록으로 과거 판정을 채우지 않는다.
- 판정 집계(`summary`)는 조건 항목(`conditions`)의 세 판정 건수를 모두 낸다. 원인 판정은 세지 않는다. 미확인을 0이나 확정으로 합치지 않는다.

## 출처 표기

[데이터 계약](../../docs/data-contract.md#식별자와-시간)의 두 축을 그대로 쓴다.

- `derivation_kind`: 비교 결과와 그 안의 판정(확정 포함)은 모두 근거에서 계산·판단한 파생 기록이므로 `DERIVED`다. 사람 확정이라고 `SYSTEM`이나 `PROVIDED`로 바꾸지 않는다. 근거 항목은 원본 레코드에 저장된 `data_origin`·`derivation_kind`를 그대로 옮긴다. 종결 화면에서 등록한 해결 사례는 `SYSTEM`이고 `data_origin`은 그 문의의 값을 따른다([문의 계약](tickets.md)의 해결 사례 등록), 제공 데이터에서 적재한 문의·해결 사례·문서는 `PROVIDED`다. 비교 응답이 이 값을 바꾸거나 `PROVIDED`를 실제 회사 데이터로 해석하지 않는다.
- `provenance_kind`: 판정을 만든 주체(`ai_draft`·`human_input`·`system_record`)는 이 축으로만 구분한다.
- `data_origin`: 근거의 출처를 따른다. 근거가 모두 `synthetic`이면 `synthetic`, 실제·합성·시연 자료가 섞이면 `demo`다. 합성·가정 근거를 포함한 결과를 `real`로 승격하지 않는다.

## 조회 범위와 오류

[권한](../platform/permissions.md) 1.3의 검사 순서를 그대로 쓴다.

- 조회: cs_agent와 C/S 부서 manager는 전체. dept_staff와 다른 부서 manager는 4.1 "개선요청 조회"에서 볼 수 있는 요청에 연결된 비교만 본다. 이때 기준은 **선택한 판본의 판정 기준 요청서**이며, 그 요청서가 보이지 않으면 요청서 경로로 조회해도 404 `CS_COMPARISON_NOT_FOUND`다(다른 요청서를 기준으로 한 과거 판본 포함). 요청서가 없는 판본도 404다. sales는 볼 수 없다.
- 근거 중 사용자의 문의 조회 범위(4.1 "문의 목록·상세 조회") 밖의 문의는 `evidence`에 넣지 않고 `hidden_evidence_count`로 건수만 낸다. 판정은 숨기지 않는다.
- 조회 범위 밖 근거의 ID는 `conditions`·`causes`·`document_signals`의 `evidence_ids`에서도 제외하고 `hidden_evidence_count`에만 반영한다. 응답 수준 값은 숨긴 근거의 전체 건수, 항목 수준 값은 그 항목이 가리키던 숨긴 근거 건수다.
- 근거가 모두 숨겨진 항목은 `judgement`·`finding`을 유지하되 `evidence_ids`는 빈 배열이고, 항목에 `hidden_evidence_count`를 둔다(존재 여부만 건수로).
- 숨긴 근거가 기여한 항목(`evidence_ids`에서 제외된 근거가 1개 이상)은 `judgement`·`finding`을 유지하되 `masked_summary`를 null(`summary_missing_reason: HIDDEN_BY_SCOPE`)로 내고, 숨긴 근거에서 온 기준·대상 값도 null(`missing_reason: HIDDEN_BY_SCOPE`)로 낸다. 문의 조회 권한이 없는 사용자에게 그 문의에서 파생된 값을 보이지 않기 위한 보수적 규칙이다.
- 확정·재검토 같은 판정 변경은 cs_agent만 한다.
- 조회 오류(구현): sales 등 C/S 범위가 없는 역할 403, 범위 밖 요청서 404 `CS_REQUEST_NOT_FOUND`, 없는 비교·판본·고장 요청이 아닌 요청서·볼 수 있는 요청서에 연결되지 않은 묶음 404 `CS_COMPARISON_NOT_FOUND`, `product_code`·`fault_id`·`version` 형식 오류 422.
- 쓰기 오류(구현): 범위 밖 요청서 404 `CS_REQUEST_NOT_FOUND`, 문서 요청서·비교 없음 404 `CS_COMPARISON_NOT_FOUND`, cs_agent가 아닌 사용자 403, 이미 있는 비교 409 `CS_COMPARISON_EXISTS`, 판본 불일치 409 `CS_COMPARISON_VERSION_CONFLICT`(개선요청의 `CS_REQUEST_VERSION_CONFLICT`와 같은 방식, 재조회 안내; 같은 비교의 쓰기는 비교 행 잠금으로 한 줄로 처리), 허용하지 않는 전이 409 `CS_INVALID_TRANSITION`(판정 조건을 깨는 근거 제외 포함), 없거나 이미 뺀 근거 404 `CS_EVIDENCE_NOT_FOUND`, 판정 조건·근거 위반 422(상태 전이 절), 근거 상태와 다른 미확인 사유 422 `CS_REASON_MISMATCH`.

## 응답 예시 (합성, 마스킹 값)

```json
{
  "data": {
    "comparison_id": "5b0f6a52-3c1e-4c7a-9d10-0b6f2a7e9c01",
    "product_code": "SYN-PRD-01",
    "fault_id": "FLT-900",
    "improvement_request_id": "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e01",
    "request_version": 5,
    "version": 3,
    "latest_version": 3,
    "is_latest": true,
    "data_origin": "synthetic",
    "derivation_kind": "DERIVED",
    "known_at": "2026-10-28T01:00:00Z",
    "computed_condition_status": "UNKNOWN",
    "condition_summary": "옥외 설치 사례 위주(합성)",
    "fw_fixed_version": "SYN-FW-2.1",
    "fw_released_on": "2026-10-15",
    "summary": { "confirmed": 1, "suspected": 1, "unconfirmed": 1 },
    "conditions": [
      {
        "item_code": "current_fw",
        "item_label": "현재 펌웨어 판",
        "judgement": "CONFIRMED",
        "finding": "DIFFERS",
        "unconfirmed_reason": null,
        "baseline": { "masked_value": "1.x", "missing_reason": null },
        "target": { "masked_value": "2.x", "missing_reason": null },
        "masked_summary": "펌웨어 2.x 사례에서만 재발",
        "summary_missing_reason": null,
        "provenance_kind": "human_input",
        "decided_by_name": "C/S 담당자 A(합성)",
        "decided_at": "2026-10-27T09:00:00Z",
        "evidence_ids": ["ev-1", "ev-2"],
        "hidden_evidence_count": 0
      },
      {
        "item_code": "site_condition",
        "item_label": "현장 조건(주변 온도·고도·습도)",
        "judgement": "SUSPECTED",
        "finding": "DIFFERS",
        "unconfirmed_reason": null,
        "baseline": { "masked_value": "실내", "missing_reason": null },
        "target": { "masked_value": "고온 옥외(현장 ***)", "missing_reason": null },
        "masked_summary": "고온 환경 의심(현장 ***)",
        "summary_missing_reason": null,
        "provenance_kind": "ai_draft",
        "decided_by_name": null,
        "decided_at": "2026-10-27T10:00:00Z",
        "evidence_ids": ["ev-2"],
        "hidden_evidence_count": 0
      },
      {
        "item_code": "load_profile",
        "item_label": "부하 전류·온도 기록",
        "judgement": "UNCONFIRMED",
        "finding": null,
        "unconfirmed_reason": "NO_EVIDENCE",
        "baseline": { "masked_value": null, "missing_reason": "NOT_PROVIDED" },
        "target": { "masked_value": null, "missing_reason": "NOT_PROVIDED" },
        "masked_summary": null,
        "summary_missing_reason": "NOT_PROVIDED",
        "provenance_kind": "system_record",
        "decided_by_name": null,
        "decided_at": "2026-10-27T08:00:00Z",
        "evidence_ids": [],
        "hidden_evidence_count": 0
      }
    ],
    "causes": [
      {
        "cause_type": "FIRMWARE",
        "judgement": "SUSPECTED",
        "unconfirmed_reason": null,
        "masked_summary": "펌웨어 판올림 후 재발 감소",
        "summary_missing_reason": null,
        "provenance_kind": "ai_draft",
        "decided_by_name": null,
        "decided_at": "2026-10-27T10:00:00Z",
        "evidence_ids": ["ev-1"],
        "hidden_evidence_count": 0
      }
    ],
    "document_signals": [
      {
        "doc_signal": "VALUE_CONFLICT",
        "provenance_kind": "system_record",
        "evidence_ids": ["ev-3"],
        "hidden_evidence_count": 0
      }
    ],
    "evidence": [
      {
        "evidence_id": "ev-1",
        "case_id": "0f2d7c1e-8a44-4b1a-9e3e-6a1c2b3d4e01",
        "ticket_id": "7a1e2b3c-4d5e-4f60-8a71-b2c3d4e5f601",
        "evidence_location": { "source": "resolution_case", "field": "cause" },
        "data_origin": "synthetic",
        "derivation_kind": "SYSTEM",
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

예시의 `computed_condition_status`는 `SUSPECTED`·`UNCONFIRMED` 항목이 있어 `UNKNOWN`이다. 값은 모두 합성이며 실제 제품·현장·사례가 아니다.

## 합성 예시 5종

화면 연동 확인용이다. 1~4는 `conditions`의 항목 1개, 5는 과거 판본 조회 응답의 머리 부분과 판본 충돌 오류다. 값은 모두 합성이다. 전체 응답 5종(`same`·`differs`·`unconfirmed`·`hidden`·`past_version`)은 `docs/openapi/cs.json`의 두 조회 경로 200 examples에 있고, DB 테스트가 같은 행을 저장해 실제 응답과 일치함을 확인한다. OpenAPI 예시는 생성기(FastAPI)가 null 키를 빼고 보여 준다. 실제 응답과 `frontend/src/mocks/generated/cs.json`은 null 값 키를 모두 가진다. 409 본문 예시는 두 쓰기 경로의 409 examples에 있다.

**1. 동일** — 확인했고 조건 차이가 없다.

```json
{
  "item_code": "site_condition",
  "item_label": "현장 조건(주변 온도·고도·습도)",
  "judgement": "CONFIRMED",
  "finding": "SAME",
  "unconfirmed_reason": null,
  "baseline": { "masked_value": "벽면 고정", "missing_reason": null },
  "target": { "masked_value": "벽면 고정", "missing_reason": null },
  "masked_summary": "설치 방식은 재발과 무관",
  "summary_missing_reason": null,
  "provenance_kind": "human_input",
  "decided_by_name": "C/S 담당자 A(합성)",
  "decided_at": "2026-10-27T09:30:00Z",
  "evidence_ids": ["ev-1"],
  "hidden_evidence_count": 0
}
```

**2. 차이** — 의심 근거가 있으나 사람이 확정하지 않았다.

```json
{
  "item_code": "application",
  "item_label": "적용 조건(전압·부하·운전 주기)",
  "judgement": "SUSPECTED",
  "finding": "DIFFERS",
  "unconfirmed_reason": null,
  "baseline": { "masked_value": "정격 범위", "missing_reason": null },
  "target": { "masked_value": "정격 상한 근처", "missing_reason": null },
  "masked_summary": "전압 상한 근처 사례에서 재발 의심",
  "summary_missing_reason": null,
  "provenance_kind": "ai_draft",
  "decided_by_name": null,
  "decided_at": "2026-10-27T10:30:00Z",
  "evidence_ids": ["ev-2"],
  "hidden_evidence_count": 0
}
```

**3. 미확인** — 대상 쪽 값이 자료에 없어 판단하지 않는다. 빈 값을 `SAME`으로 보지 않는다.

```json
{
  "item_code": "operating_condition",
  "item_label": "고장 시 운전 조건(부하·속도·온도)",
  "judgement": "UNCONFIRMED",
  "finding": null,
  "unconfirmed_reason": "VALUE_NOT_PROVIDED",
  "baseline": { "masked_value": "상온", "missing_reason": null },
  "target": { "masked_value": null, "missing_reason": "NOT_PROVIDED" },
  "masked_summary": null,
  "summary_missing_reason": "NOT_PROVIDED",
  "provenance_kind": "system_record",
  "decided_by_name": null,
  "decided_at": "2026-10-27T08:00:00Z",
  "evidence_ids": ["ev-1"],
  "hidden_evidence_count": 0
}
```

**4. 가림** — 판정에 쓰인 문의가 사용자의 조회 범위 밖이다. 판정은 유지하고 그 문의에서 온 값·요약·근거 ID만 숨긴다.

```json
{
  "item_code": "load_profile",
  "item_label": "부하 전류·온도 기록",
  "judgement": "CONFIRMED",
  "finding": "DIFFERS",
  "unconfirmed_reason": null,
  "baseline": { "masked_value": "주간 운전", "missing_reason": null },
  "target": { "masked_value": null, "missing_reason": "HIDDEN_BY_SCOPE" },
  "masked_summary": null,
  "summary_missing_reason": "HIDDEN_BY_SCOPE",
  "provenance_kind": "human_input",
  "decided_by_name": "C/S 담당자 B(합성)",
  "decided_at": "2026-10-27T11:00:00Z",
  "evidence_ids": ["ev-1"],
  "hidden_evidence_count": 1
}
```

**5. 판본 충돌** — 사용자가 판본 3을 보고 있는 사이 다른 담당자가 판정을 바꿔 최신이 4가 되었다. `?version=3` 조회는 `is_latest=false`로 과거 판본임을 알린다(항목 생략).

```json
{
  "data": {
    "comparison_id": "5b0f6a52-3c1e-4c7a-9d10-0b6f2a7e9c01",
    "product_code": "SYN-PRD-01",
    "fault_id": "FLT-900",
    "improvement_request_id": "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e01",
    "request_version": 5,
    "version": 3,
    "latest_version": 4,
    "is_latest": false,
    "computed_condition_status": "UNKNOWN"
  },
  "meta": { "request_id": "22222222-2222-4222-8222-222222222201" }
}
```

판본 3을 기준으로 판정 쓰기를 보내면 409로 거절한다. 화면은 최신 판본을 다시 조회해 변경을 재확인하게 한다.

```json
{
  "error": {
    "code": "CS_COMPARISON_VERSION_CONFLICT",
    "message": "The comparison has changed.",
    "retryable": false,
    "details": []
  },
  "meta": { "request_id": "22222222-2222-4222-8222-222222222202" }
}
```

## 이 계약의 범위 밖

- KAN-114 제목의 "반복 이슈 의미 보조 분석"(의미 유사도로 묶음 후보를 찾는 일)은 이 계약에 넣지 않는다. 후보 자동 생성은 KAN-113의 범위다.
- 비교 계산 방식·모델 프롬프트·평가 지표와 합격선은 정하지 않는다([평가 명세](../../docs/evaluation.md)의 KAN-82 잠금 대상).
- 실제 구현, 화면, OpenAPI 등록은 이후 단위다.
