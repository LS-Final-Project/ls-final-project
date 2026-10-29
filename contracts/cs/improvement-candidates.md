# C/S 개선요청 후보 결정 규칙 (KAN-113)

원본: `backend/engine/cs/improvement_candidates.py`. 기획서 4.4(묶는 기준·후보 생성·날짜와 집계·문서 개선요청)와 [권한 문서](../platform/permissions.md) 2.2(시스템은 `DRAFT` 후보만 만들고 전달은 `cs_agent`)를 따른다. 검사는 `tests/cs/test_improvement_candidates.py`(합성 기록).

순수 함수다. DB를 읽어 후보를 만들고 `improvement_requests`에 `origin=SYSTEM_CANDIDATE`·`DRAFT`로 저장하는 일, 재요청·재발 후보(직전 전달 뒤 새 3건, 조치 적용 장비 재발 1건)는 KAN-117이다. 후보는 **사람이 검토할 거리**이며 요청 생성·전달·완료를 뜻하지 않는다.

## 입력

| 입력 | 내용 | 안 받는 것 |
| --- | --- | --- |
| `CaseRecord` | 해결 기록: 사례 ID, 문의 ID, 제품, 고장 ID, 고객 식별자, **적재 시각 `known_at`**, 원인 유형, 고장 당시 펌웨어 | 증상·원인·조치 원문 |
| `FirmwareRelease` | 제품, 판본, 배포일(`ref/firmware_history.csv`) | — |
| `DocumentSignalRecord` | KAN-114 조건 비교의 문서 신호: 문의 ID, 제품, 신호 4종, 문서 ID, 적재 시각, 근거 위치 유무 | 본문 |
| `as_of` | 판정 시점(시간대 필수) | — |
| `condition_confirmed` | 묶음(제품, 고장)의 사용 조건 확인 여부(KAN-114 결과) | — |

- **적재 시각 기준**: 모든 시간 계산은 `known_at`으로 한다. 기획서대로 지금 데이터에서는 해결 시각이 적재 시각의 대리값이다. `as_of`보다 뒤에 알려진 기록은 보지 않는다(미래 정보 차단, 1년 데이터 되돌리기 재현의 전제).
- 후보 생성일(`reached_at`)과 실제 전달일은 다르다. 전달은 사람이 한다.

## 고장 후보 (`FaultCandidate`)

묶음 = 같은 제품 + 같은 고장 ID. 묶음마다 결과 하나.

| 결과 | 사유 코드 | 조건 |
| --- | --- | --- |
| `ELIGIBLE` | `REPEAT_90D_3_CUSTOMERS` | 어느 90일 구간 안에 서로 다른 고객 3곳. `reached_at`은 세 번째 고객의 기록이 알려진 시각. 같은 고객의 재발은 고객 수를 늘리지 않는다 |
| `ELIGIBLE` | `FIRMWARE_FIX_RELEASED` | 원인 유형 `FIRMWARE`이고, 그 제품에 **고장 당시 버전보다 높은 수정 펌웨어가 기록이 알려진 시각 이전에 배포**된 기록이 1건 이상. 고객 1곳부터. `fw_fixed_version`·`fw_released_on`은 가장 이른 적용 가능 수정판. 반복 입증이 아니라 업데이트 안내 필요성을 일찍 검토하기 위한 기준이다 |
| `INSUFFICIENT_EVIDENCE` | `NO_CUSTOMER_ID` | 고객 식별자 없는 기록을 더하면 3곳이 될 수 있는데 셀 수 없음 |
| `INSUFFICIENT_EVIDENCE` | `FW_VERSION_UNKNOWN` / `FW_VERSION_NOT_COMPARABLE` | 펌웨어 원인인데 고장 당시 버전이 없거나(`1.72`·`v2.0.1` 형식이 아니라) 비교할 수 없어 1곳 예외를 판단하지 못함(일반 기준도 미달일 때) |
| `NOT_ELIGIBLE` | `BELOW_THRESHOLD` | 위 어느 것도 아님. 최대 고객 수를 분모와 함께 적는다(예: `2/3`) |

- 수정판이 기록 **뒤에** 배포됐으면 펌웨어 예외가 아니다. 기획서의 "시간상 불일치 7건"은 이렇게 일반 기준으로만 본다. 원본 날짜는 고치지 않는다.
- 고장 당시 버전이 수정판 이상이면 그 배포는 수정판이 아니다.
- 사용 조건이 다르면 다른 묶음이어야 하지만 조건 판정은 KAN-114가 한다. 여기서는 `condition_status`를 붙여 `UNKNOWN`이면 "조건 미확인 상태의 후보"임을 표시한다. 후보를 숨기지 않는다.
- `evidence`는 `as_of`까지 알려진 그 묶음의 기록 전부(문의 ID·사례 ID·고객·시각)다. 요청서의 "근거 문의"와 "발생 시점과 규모"는 여기서 만든다.

## 문서 후보 (`DocumentCandidate`)

묶음 = 같은 제품 + 같은 신호 + 같은 문서 ID(없으면 null끼리). 근거 위치(`has_evidence_location`)가 없는 신호는 세지 않는다. 같은 문의의 신호 여러 개는 1건이다.

| 신호 | 기준 | 사유 코드 |
| --- | --- | --- |
| `NOT_FOUND`·`VALUE_CONFLICT`·`BROKEN_LINK` | 근거 있는 문의 **1건** | `DOCUMENT_ERROR_EVIDENCE` |
| `REPEATED_SPEC` | 근거 있는 서로 다른 문의 **3건** | `REPEATED_SPEC_3_TICKETS` |
| 신호는 있지만 근거 위치가 모두 없음 | — | `INSUFFICIENT_EVIDENCE` / `NO_EVIDENCE_LOCATION` |

문서 오류는 반복이 아니라 근거로 판단한다. 받는 부서는 부서 목록에 없어 `receiver_unconfirmed=true`이고, 요청서는 KAN-112 규칙대로 제안 부서 없이 만들어져 검토 뒤 `CONFIRM_RECEIVER`가 다음 할 일이다.

## 기준값과 한계

| 값 | 기본 | 근거 |
| --- | --- | --- |
| 창 | 90일 | 기획서 4.4 |
| 고장 고객 수 | 3곳 | 기획서 4.4 표(2곳 118·3곳 76·4곳 50·5곳 24 묶음 중 중간값). 실제 데이터를 받으면 다시 정한다 |
| 펌웨어 예외 고객 수 | 1곳 | 기획서 4.4 |
| 반복 사양 질문 문의 수 | 3건 | 이 계약의 제안값. 기획서는 수치를 적지 않았다(상위 26개 항목 150건, 예 11회). 팀 확인 대상 |

- 제공 데이터의 기준선(묶음 148개 중 76개가 90일·3곳 도달)과 대조하는 재현 검사는 개발 분할 manifest(`CS_DEV_MANIFEST`)가 있는 환경에서 KAN-117과 함께 추가한다. 이 PR은 합성 기록 검사만 있다.
- 후보 판정은 묶음 단위 사실 집계이며 원인·책임을 단정하지 않는다. 요청서 문장은 KAN-115가 만든다.
