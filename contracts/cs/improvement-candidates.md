# C/S 개선요청 후보 결정 규칙 (KAN-113)

원본: `backend/engine/cs/improvement_candidates.py`. 기획서 4.4(묶는 기준·후보 생성·날짜와 집계·문서 개선요청)와 [권한 문서](../platform/permissions.md) 2.2(시스템은 `DRAFT` 후보만 만들고 전달은 `cs_agent`)를 따른다. 검사는 `tests/cs/test_improvement_candidates.py`(합성 기록).

규칙은 순수 함수다. DB를 읽어 판정하고 저장하는 경로는 아래 [저장과 조회](#저장과-조회-kan-202)(KAN-202)다. 재요청·재발 후보(직전 전달 뒤 새 3건, 조치 적용 장비 재발 1건)는 [KAN-117](improvement-followups.md)이다. 후보는 **사람이 검토할 거리**이며 요청 생성·전달·완료를 뜻하지 않는다.

## 입력

| 입력 | 내용 | 안 받는 것 |
| --- | --- | --- |
| `CaseRecord` | 해결 기록: 사례 ID, 문의 ID, 제품, 고장 ID, 고객 식별자, **적재 시각 `known_at`**, 원인 유형, 고장 당시 펌웨어 | 증상·원인·조치 원문 |
| `FirmwareRelease` | 제품, 판본, 배포일(`backend/engine/cs/policy/firmware_history.csv`, `ref/` 원본 복사) | — |
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
| `ELIGIBLE` | `FIRMWARE_FIX_RELEASED` | 원인 유형 `FIRMWARE`이고, 그 제품에 **고장 당시 버전보다 높은 수정 펌웨어가 기록이 알려진 시각 이전에 배포**된 기록(고객 식별자 있는 것)이 1건 이상. 고객 1곳부터. `fw_fixed_version`·`fw_released_on`은 가장 이른 적용 가능 수정판. 반복 입증이 아니라 업데이트 안내 필요성을 일찍 검토하기 위한 기준이다 |
| `INSUFFICIENT_EVIDENCE` | `NO_CUSTOMER_ID` | 고객 식별자 없는 기록을 더하면 3곳이 될 수 있는데 셀 수 없음 |
| `INSUFFICIENT_EVIDENCE` | `FW_VERSION_UNKNOWN` / `FW_VERSION_NOT_COMPARABLE` | 펌웨어 원인인데 고장 당시 버전이 없거나(`1.72`·`v2.0.1` 형식이 아니라) 비교할 수 없어 1곳 예외를 판단하지 못함(일반 기준도 미달일 때) |
| `NOT_ELIGIBLE` | `BELOW_THRESHOLD` | 위 어느 것도 아님. 최대 고객 수를 분모와 함께 적는다(예: `2/3`) |

- 일반 기준과 펌웨어 예외가 모두 성립하면 **먼저 충족된 쪽**이 `reached_at`과 사유 코드가 된다(후보 생성일은 1년 되돌리기 재현의 전제라 늦은 시각으로 밀리지 않는다).
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

## 저장과 조회 (KAN-202)

원본: `backend/api/cs/improvements/candidates.py`. 고장 후보만 잇는다.

| 경로 | 허용 | 설명 |
| --- | --- | --- |
| `GET /api/v1/cs/improvement-candidates?as_of=&outcome=` | cs_agent, C/S 부서 manager | 읽을 때 판정한다. 저장되지 않는 `NOT_ELIGIBLE`·`INSUFFICIENT_EVIDENCE` 묶음도 사유·근거와 함께 낸다. `outcome`으로 거른다. 쪽 나눔 없음 |
| `POST /api/v1/cs/improvement-candidates/collect` (`as_of`?) | cs_agent, C/S 부서 manager | `ELIGIBLE` 묶음을 `SYSTEM_CANDIDATE`·`DRAFT`로 저장한다. 사람이 명시 호출한다. 스케줄러는 없다 |

- **허용 범위**: 근거에 고객 식별자가 있어 전체 문의를 보는 사용자만 쓴다. 그 밖의 역할은 403이다.
- **`as_of`**: 시간대 필수, 기본은 현재 시각이다. 미래 시각은 422 `CS_CANDIDATE_AS_OF_FUTURE`다. 해결 기록은 `known_at`, 사용 조건은 그 시각까지 알려진 최신 비교 판본으로 판정해 뒤에 알려진 값을 쓰지 않는다.
- **`display_state`**: 화면은 이 값으로 표시한다. `CONFIRMED_CANDIDATE`만 확정된 후보로 읽는다.

| `display_state` | 조건 | 표시 |
| --- | --- | --- |
| `CONFIRMED_CANDIDATE` | `ELIGIBLE`, `condition_status=CONFIRMED` | 조건까지 확인된 후보 |
| `CONDITION_UNCONFIRMED` | `ELIGIBLE`, `condition_status=UNKNOWN` | 기준은 넘었지만 조건 미확인. 확정으로 보이지 않는다 |
| `INSUFFICIENT_EVIDENCE` | 판단에 필요한 값 없음 | 근거 부족. 후보가 아니라고 단정하지 않는다 |
| `NOT_ELIGIBLE` | 기준 미달 | 미달. 분모와 함께 표시 |

- **`condition_status`**: 조건 비교의 `computed_condition_status`와 같은 규칙이다. 조건 항목이 1개 이상이고 모두 `CONFIRMED`일 때만 `CONFIRMED`다.
- **`data_origin`**: 출처별로 따로 판정한다. 합성 기록이 실제 기록의 고객 수를 채우지 않는다. 응답 항목과 저장 요청에 그 출처가 있다.
- **멱등**: 같은 묶음(출처·제품·고장)에 요청이 이미 있으면 사람이 쓴 요청이어도 만들지 않는다. 동시 실행은 부분 유일 색인 `improvement_requests_candidate_group`이 막는다. 다시 돌려도 근거를 덧붙이지 않는다. 재요청·재발은 KAN-117 경로다. `already_requested`는 `ELIGIBLE`이지만 이번에 만들지 않은 묶음 수다.
- **저장 내용**: 근거는 `as_of`까지 알려진 그 묶음의 기록 전부다. 문장은 규칙의 사실만 옮긴다(요청서 문장 작성은 KAN-115). `cs_owner`는 비어 있고 검토한 사람이 맡는다. 제안 부서는 근거 사례의 `resolved_by_dept` 중 이관 부서 코드다. `candidate_reason_code`·`candidate_reached_at`·`candidate_rules_version`·`candidate_as_of`에 판정 당시 값을 남기고 `GET /improvement-requests/{id}`로 읽는다. 이후 검토·전달은 일반 요청과 같다.
- **감사**: 호출마다 `audit_events`에 `improvement_candidate_run` 한 행(`as_of`, 규칙 판본, `eligible`, `created`, 실행자)을 남긴다. 만든 요청은 `improvement_request`의 `create`에 `run_id`를 남긴다.
- **제외되는 기록**: `fault_id`가 `FLT-000` 형식이 아닌 해결 기록은 요청이 될 수 없어 보지 않는다. 목록 밖 `root_cause_type`은 없는 값으로 본다.
- **문서 후보 미연결**: 조회·수집 응답의 `document_candidates`는 지금 항상 `NOT_CONNECTED`다. 문서 신호를 쓰는 원천이 아직 없기 때문이다([조건 비교 계약](condition-comparison.md)). 규칙(`document_candidates()`)은 있지만 호출하지 않는다. 문서 후보가 0건이라는 뜻이 아니다.
- **비용**: 조회마다 알려진 해결 기록 전부를 다시 판정한다. 기록이 늘어 느려지면 표를 둔다(`ponytail:` 주석).

## 기준값과 한계

| 값 | 기본 | 근거 |
| --- | --- | --- |
| 창 | 90일 | 기획서 4.4 |
| 고장 고객 수 | 3곳 | 기획서 4.4 표(2곳 118·3곳 76·4곳 50·5곳 24 묶음 중 중간값). 실제 데이터를 받으면 다시 정한다 |
| 펌웨어 예외 고객 수 | 1곳 | 기획서 4.4 |
| 반복 사양 질문 문의 수 | 3건 | 이 계약의 제안값. 기획서는 수치를 적지 않았다(상위 26개 항목 150건, 예 11회). 팀 확인 대상 |

- 제공 데이터의 기준선(묶음 148개 중 76개가 90일·3곳 도달)과 대조하는 재현 검사는 개발 분할 manifest(`CS_DEV_MANIFEST`)가 있는 환경에서 KAN-117과 함께 추가한다. 이 PR은 합성 기록 검사만 있다.
- 후보 판정은 묶음 단위 사실 집계이며 원인·책임을 단정하지 않는다. 요청서 문장은 KAN-115가 만든다.

## 완료 조건 대조 (KAN-113, 2026-10-29)

Jira 완료 조건 "해결기록 적재시각, 90일 고객3곳, 확인된 펌웨어1고객, 문서오류근거 기준 테스트"와 검사 위치다. 모두 `tests/cs/test_improvement_candidates.py`의 합성 기록 검사다.

| 완료 조건 | 이 문서의 규칙 | 검사 |
| --- | --- | --- |
| 해결 기록 적재 시각 | `known_at` 기준, `as_of` 뒤에 알려진 기록 제외 | `test_records_known_after_as_of_are_not_used` |
| 90일·고객 3곳 | 90일 창 안 서로 다른 고객 3곳, 창 이동, 91일 간격 제외, 고객 식별자 누락은 1곳으로 세지 않음, 도달 시점 기준 사유 | `test_three_distinct_customers_within_90_days_reach_the_threshold`, `test_the_window_slides_and_a_91_day_spread_does_not_count`, `test_missing_customer_ids_block_the_count_instead_of_counting_as_one`, `test_reason_reports_the_threshold_at_reach_time_not_the_group_maximum` |
| 확인된 펌웨어 수정판 뒤 1고객 | 수정판 배포 뒤 기록이면 1곳으로 충분, 모르는·이상한 판본은 근거 부족, 먼저 충족된 경로가 `reached_at` | `test_firmware_fix_released_before_the_record_makes_one_customer_enough`, `test_unknown_or_odd_firmware_versions_are_insufficient_not_eligible`, `test_the_earlier_route_sets_reached_at_when_both_apply` |
| 문서 오류 근거 | 문서 오류는 근거 위치가 있으면 1건, 반복 사양 질문은 3건 | `test_document_errors_need_evidence_not_repetition_and_repeated_specs_need_three` |
| (공통) | 제품·고장 단위 묶음, 규칙 판본 | `test_groups_are_per_product_and_fault_and_carry_condition_status`, `test_evaluate_bundles_both_kinds_with_the_rule_version` |

**완료 조건 밖으로 남긴 것**
- 반복 사양 질문 3건은 이 계약의 제안값이다. 기획서에 수치가 없어 **팀 확인 대기**다. 확인되면 위 기준값 표만 고친다.
- 후보 판정 결과를 DB에 저장하는 연결은 KAN-202로 넣었다(고장 후보만). 문서 후보는 원천이 없어 미연결이다.
- 제공 데이터 기준선(묶음 148개 중 76개) 재현은 위 한계대로 개발 분할 manifest가 있는 환경에서 KAN-117과 함께 한다.
