# C/S 개선요청 재요청·조치 후 재발·재개방 계약 (KAN-117, 초안)

**상태: 계약 초안(10/31 약속분).** 코드·마이그레이션은 아직 없다. 김종하 님 KAN-133 화면 준비용이며, 아래 "확인할 것"이 정해지면 같은 티켓에서 구현한다(마감 11/2). 근거는 C/S 기획서 4.4(p.11 "재요청"·"조치 뒤 재발" 표, p.13~14 시연), [권한 문서](../platform/permissions.md) 2.2·2.2.1(새 회차 전이·기본 대상 부서), [개선요청 계약](improvements.md)(KAN-112·116), [후보 규칙](improvement-candidates.md)(KAN-113).

## 무엇을 정하는가

전달된 개선요청에 같은 묶음(같은 제품 + 같은 고장 ID)의 해결 기록이 또 들어올 때, 그 기록을 어떻게 나눠 세고 언제 사람에게 "새 회차를 열지" 묻는지를 정한다. 판정은 **후보**까지이고, 새 회차를 여는 일은 `cs_agent`가 한다(권한 문서 2.2).

| 판정 | 언제 | 사람이 할 일 |
| --- | --- | --- |
| `REQUEST_AGAIN` 재요청 후보 | 조치 전(아래 "조치 전" 상태)에, **직전 요청 단위 전달 이후** 새 해결 기록이 3건 쌓일 때마다 | 검토 뒤 새 회차로 다시 전달 |
| `RECURRENCE` 재발 후보 | 조치 뒤에, **조치가 적용된 장비**에서 같은 고장 기록이 1건이라도 | 적용 여부·근거 확인 뒤 같은 요청을 새 회차로 다시 열어 전달 |
| `PRE_ACTION_UNIT` 조치 전 장비 발생 | 조치 뒤에, 조치가 **적용되지 않은** 장비(구형·수정판 미설치)의 기록 | 재발이 아님. 따로 센다. 펌웨어는 그 고객을 업데이트 안내 대상에 넣는다 |
| `APPLICATION_UNKNOWN` 조치 전후 미확인 | 조치 뒤에, 적용 여부를 판단할 정보가 없는 기록 | 재발로도 조치 전 장비로도 세지 않는다. 확인할 정보를 표시 |
| `NONE` | 위 어느 것도 아님(새 기록 없음, 3건 미만 등) | 없음 |

- 한 요청은 판정 하나를 내지만 **건수는 네 칸을 모두** 보여 준다(재요청 진행 `n/3`, 재발, 조치 전 장비, 미확인).
- 판정은 요청 상태를 바꾸지 않는다. 자동 전달·자동 재개방은 없다.

## 기준 시점과 미래 정보 차단

판정은 `as_of` 시점(시간대 필수) 기준이다. 아래 값 중 `as_of`보다 뒤의 것은 없는 것으로 본다.

| 값 | 어디서 | 쓰임 |
| --- | --- | --- |
| 해결 기록의 `known_at` | `cs.resolution_cases`(KAN-113과 같은 적재 시각) | `as_of`까지 알려졌는지, 직전 회차 뒤에 새로 알려졌는지 |
| 문의 접수 시각 `received_at` | `cs.tickets` | **고장 발생 시각의 대리값.** 조치 전/후 구분에만 쓴다(적재 시각은 발생 시각이 아니다) |
| 직전 요청 단위 전달 시각 | 가장 최근 회차가 열린 시각(첫 전달은 `improvement_requests.sent_at`, 이후 회차는 아래 `request_rounds.opened_at`) | 재요청 집계의 시작점 |
| 조치 적용일 | `request_actions.action_date`(부서가 기록한 실제 적용일)와 그 기록 시각 `recorded_at` | 조치 전/후 구분. `recorded_at`이 `as_of` 뒤면 그 조치는 아직 모르는 것 |
| 펌웨어 수정판 | `ref/firmware_history.csv`(KAN-113 `FirmwareRelease`) | 펌웨어 적용 여부 |

- 전달 시각과 같은 시각에 알려진 기록은 "이후"가 아니다(엄격히 뒤만 센다).
- **회차 기준점**: 재요청·재발 모두 **가장 최근 회차가 열린 시각 뒤에 알려진 기록만** 새 후보의 근거가 된다. 그 앞의 기록은 이전 회차에서 다룬 것으로 `class=IN_EARLIER_ROUND`로만 보인다. 그래서 같은 기록으로 회차를 두 번 열 수 없다.
- **날짜 비교**: `action_date`는 날짜(시각 없음)라 UTC 날짜로 본다. 문의 접수 시각의 UTC 날짜가 적용일보다 **앞이면 조치 전 발생, 뒤면 조치 뒤 발생, 같은 날이면 미확인**(`SAME_DAY_AS_ACTION`)이다. 같은 날을 재발로 세지 않는다.
- 원본 날짜는 고치지 않는다. 해결 시각보다 수정판 배포일이 늦은 "시간상 불일치" 기록(기획서 7건)은 판정에서 빼지 않고 `TIME_INCONSISTENT` 표시를 붙인다.

## 재요청 (`REQUEST_AGAIN`)

- **대상 상태(조치 전)**: 요청 상태(2.2.1 요약)가 `SENT`·`ACKNOWLEDGED`·`ADOPTED`·`INFO_REQUESTED`이거나 반려 종료. 일부 부서만 조치한 요청은 요약이 `ADOPTED`라 여기에 든다(권한 문서 2.2.1 기본 대상이 재요청에서 `ACTION_TAKEN` 부서를 빼는 것과 같은 전제). 이때 이미 등록된 조치가 있으면 기록마다 아래 재발 분류도 함께 한다. 재발로 분류된 기록은 재요청 3건에 넣지 않고, 재발 후보가 있으면 판정은 `RECURRENCE`가 우선이다.
- 요청 상태가 `ACTION_TAKEN`·`FOLLOW_UP`·`COMPLETED`이면 재요청 규칙을 쓰지 않고 재발 규칙만 쓴다.
- **세는 것**: 가장 최근 회차가 열린 시각 뒤, `as_of`까지 알려진 같은 묶음의 해결 기록 수(재발로 분류된 기록 제외). **고객 수가 아니라 기록 수**다(기획서 "새 해결 기록 3건마다"). 같은 고객의 기록도 센다.
- **"3건마다"**: `n < 3`이면 `NONE`(`BELOW_3`, 진행 `n/3`). `n ≥ 3`이면 `REQUEST_AGAIN`이고 후보 번호는 `candidate_index = floor(n/3)`다. 즉 3~5건은 1번, 6~8건은 2번 후보이며, 4·5건째에도 1번 후보가 그대로 보인다(새 후보가 아니라 같은 후보에 근거가 늘어난 것). 새 회차를 열면 시작점이 그 회차 시각으로 바뀌어 다시 0부터 센다.
- **반려 종료**: 기록은 자동으로 누적하고 같은 3건 규칙으로 후보를 낸다. 권한 문서대로 반려 종료 요청은 이 후보 또는 "반려 사유와 다른 새 근거"(사람이 붙임) 중 하나가 있어야 새 회차를 연다.
- 사유 코드: `NEW_RECORDS_3`(후보), `BELOW_3`(진행 `n/3`, 판정 `NONE`).

## 조치 뒤 재발 (`RECURRENCE`)과 조치 전 장비 (`PRE_ACTION_UNIT`)

조치 = 이 요청에 등록된 부서 조치(`request_actions`). 고객 한 명의 장비 수리와 다르다. 기록 하나를 아래 순서로 나눈다.

1. 문의 접수 시각(발생 대리값)의 UTC 날짜가 그 조치의 적용일보다 앞이면 그 조치와 무관하다(`BEFORE_ACTION_DATE`, 집계만). 같은 날이면 `SAME_DAY_AS_ACTION`(미확인). 조치 전에 발생했지만 조치 뒤에 적재된 기록이 재발로 잡히지 않게, 적재 시각은 여기서 쓰지 않는다.
2. 적용일 뒤에 발생한 기록은 **그 장비에 조치가 적용됐는지**로 나눈다.

| 조치 종류 | 적용됨(→ 재발) | 적용 안 됨(→ 조치 전 장비) | 모름(→ 미확인) |
| --- | --- | --- | --- |
| 펌웨어 (`scope_kind=FIRMWARE_VERSION`) | 고장 당시 버전 ≥ 조치 버전 | 고장 당시 버전 < 조치 버전. 그 고객을 업데이트 안내 대상에 넣음 | 고장 당시 버전이 없거나 비교 불가(`1.72`·`v2.0.1` 형식이 아님) |
| 출하 기준 (`scope_kind=SHIPPED_FROM`) | 장비 출하일 ≥ 기준일 | 출하일 < 기준일 | 출하일 없음(ERP 출하일은 추가 제공 가정 데이터라 지금은 늘 미확인) |
| 로트 (`scope_kind=LOT`) | 장비 로트가 목록 안 | 목록 밖 | 로트 없음 |
| 그 밖 (`scope_kind=OTHER`) | — | — | 늘 미확인(사람이 판단) |

- 재발은 **1건이라도** 후보다. 근거에는 이전 회신·조치(회차·부서·조치 내용·적용일)와 새 기록을 함께 붙인다.
- 재발은 `ACTION_TAKEN`·`FOLLOW_UP`·`COMPLETED` 어디서든 후보가 된다. 완료된 요청도 감시를 계속한다.
- 조치 전 장비 발생은 재발이 아니고 재요청 3건 집계에도 넣지 않는다(조치 뒤에는 재요청 규칙을 쓰지 않는다). 따로 센다.
- 조치가 여럿이면 기록마다 **적용일이 가장 늦은 조치**부터 본다. 그 조치로 "적용됨"이면 재발이다.
- 사유 코드: `APPLIED_UNIT_RECURRENCE`, `PRE_ACTION_UNIT_FIRMWARE`, `PRE_ACTION_UNIT_SHIPMENT`, `PRE_ACTION_UNIT_LOT`, `UNKNOWN_FW_VERSION`, `UNKNOWN_SHIP_DATE`, `UNKNOWN_LOT`, `UNKNOWN_SCOPE`.

## 결과 형태 (화면용)

`GET /api/v1/cs/improvement-requests/{id}/followup-signals?as_of=` (조회 범위는 요청 조회와 같음). 저장하지 않고 읽을 때 계산한다. 아래 값은 형태를 보이기 위한 예시다.

```json
{
  "request_id": "…",
  "as_of": "2026-02-24T00:00:00Z",
  "phase": "BEFORE_ACTION",
  "decision": "REQUEST_AGAIN",
  "reason_code": "NEW_RECORDS_3",
  "baseline": {"last_sent_at": "2025-11-16T…Z", "round": 1, "action_dates": []},
  "counts": {"new_since_sent": 3, "threshold": 3, "candidate_index": 1,
             "recurrence": 0, "pre_action_unit": 0, "application_unknown": 0},
  "records": [
    {"ticket_id": "…", "case_id": "…", "known_at": "2026-01-09T…Z", "customer_id": "…",
     "class": "NEW_SINCE_SENT", "reason_code": "NEW_RECORDS_3", "basis": null,
     "time_inconsistent": false}
  ],
  "update_notice_customers": [],
  "next_action": "OPEN_ROUND_REQUEST_AGAIN"
}
```

- `phase`: `BEFORE_ACTION`(재요청 규칙) / `AFTER_ACTION`(재발 규칙) / `NOT_SENT`(아직 전달 전, 판정 없음).
- `records[].class`: `NEW_SINCE_SENT`·`RECURRENCE`·`PRE_ACTION_UNIT`·`APPLICATION_UNKNOWN`·`BEFORE_ACTION_DATE`·`IN_EARLIER_ROUND`. `APPLICATION_UNKNOWN`에는 `SAME_DAY_AS_ACTION`도 든다. `basis`는 적용 판단에 쓴 값(예: `{"fw_version": "1.60", "action_version": "1.72"}`).
- `next_action`: `OPEN_ROUND_REQUEST_AGAIN`·`OPEN_ROUND_RECURRENCE`·`NONE`. 화면은 이 값으로 버튼을 보이고, 문구는 사유 코드로 만든다.
- 시연(기획서 4.5): 가스켓 누유 요청을 2025-11-15 직후 전달하고 조치·반려가 없다고 가정하면, 2026-02-24 기준 새 기록 3건으로 첫 재요청 후보다.

## 새 회차 열기 (상태 전이)

`POST /api/v1/cs/improvement-requests/{id}/rounds` (`cs_agent`)

| 입력 | 내용 |
| --- | --- |
| `version` | 읽은 요청 판본. 다르면 409 `CS_REQUEST_VERSION_CONFLICT` |
| `kind` | `REQUEST_AGAIN` 또는 `RECURRENCE` |
| `as_of` | 판정을 읽은 시점(서버 현재 시각 이하). 서버가 **현재 시각**으로 다시 계산해 같은 종류의 후보가 여전히 있는지만 확인한다. 회차 기준 시각 `opened_at`은 서버 현재 시각이다 |
| `dept_codes` | 생략하면 권한 문서 2.2.1의 기본 대상: 재요청은 `ACTION_TAKEN`과 반려 확인된 `REJECTED`를 뺀 기존 수신 부서(반려 종료면 기존 수신 부서 전체), 재발은 재발한 조치를 등록한 부서 |
| `new_evidence` | 반려 종료 요청을 후보 없이 열 때만: 반려 사유와 다른 새 근거(근거 문의 ID 1개 이상 + 설명). 없으면 422 `CS_EVIDENCE_REQUIRED` |
| `reply_due_days` | 기본 7 |

- **허용 상태**: 재요청은 조치 전 상태(위)에서만, 재발은 `ACTION_TAKEN`·`FOLLOW_UP`·`COMPLETED`에서만. 아니면 409 `CS_REQUEST_INVALID_TRANSITION`. 판정이 그 종류가 아니면(후보 없음) 409 `CS_FOLLOWUP_NOT_CANDIDATE`(반려 종료의 새 근거 경로 제외).
- **효과**: `current_round` + 1, 대상 부서는 `SENT`, 나머지 기존 부서는 이월(`carried_over`). 요청 상태는 2.2.1 요약으로 다시 계산한다. 근거 기록은 `as_of`가 아니라 **`opened_at`까지 알려진** 후보 기록 전부를 요청 근거(`request_evidence`)에 더한다. 그래서 `as_of`와 `opened_at` 사이에 들어온 기록도 이 회차 근거로 들어가고, 다음 집계에서 빠져 사라지지 않는다. 판본이 오르고 감사에 `transition`과 종류·후보 번호·`as_of`를 남긴다.
- **완료 기록 보존**: `COMPLETED`·`FOLLOW_UP`에서 재발로 열어도 `completed_by`·`completed_at`·`followup_plan`·`followup_result`는 지우지 않고 그 회차의 기록으로 남긴다. 요청은 다시 요약 상태(`SENT` 등)가 되고, 새 회차를 다시 완료하면 새 완료 기록이 쌓인다.
- 회차 기록: 새 표 `cs.request_rounds`(요청, 회차, 종류 `FIRST`·`REQUEST_AGAIN`·`RECURRENCE`, 열린 시각 `opened_at`, 연 사람, 후보 번호, `as_of`, 새 근거 설명, 이전 회차의 완료 기록 사본). 첫 전달(KAN-116 `send`)도 `FIRST` 행을 남기도록 맞춘다.

## 확인할 것 (구현 전)

1. **조치 적용 범위를 구조화**: 지금 `request_actions.action_scope`는 자유 문장이라 프로그램이 장비 적용 여부를 판단할 수 없다. 조치 등록에 `scope_kind`(`FIRMWARE_VERSION`·`SHIPPED_FROM`·`LOT`·`OTHER`)와 `scope_value`(버전·기준일·로트 목록)를 더하는 마이그레이션을 제안한다. KAN-116 조치 등록 API에 선택 입력으로 붙이고, 없으면 `OTHER`(늘 미확인)다. **김종하 님 화면의 조치 입력 칸에 영향이 있다.**
2. **출하일·로트**: ERP 출하일·로트는 추가 제공 가정 데이터(기획서 6.1)다. 지금 제공 데이터에는 없어 출하·로트 기준 조치는 시연에서 늘 "미확인"이다. 시연 가정값을 둘지 정한다.
3. **3건의 단위**: 기획서 문구대로 기록 수로 정했다. 같은 고객의 반복 기록을 1건으로 볼지(고객 수) 팀 확인이 필요하다.
4. **반려 사유와 다른 새 근거**의 판단은 사람이 한다(근거 문의와 설명을 남김). 자동 판정하지 않는다.
5. **일부 부서만 조치한 요청**: 이 초안은 권한 문서 2.2.1 요약을 따라 재요청 집계와 재발 분류를 함께 쓰고 재발을 우선한다(위 "대상 상태"). 부서별로 따로 셀 필요가 있는지 화면 검토 때 확인한다.

## 검사 계획 (구현 때)

KAN-117 완료 조건 "직전요청후 새3건, 조치적용장비 재발1건, 구형/미적용 별도, 미래정보 차단"에 맞춘 합성 기록 검사:

- 전달 직후 2건 → `NONE` `2/3`, 3건 → `REQUEST_AGAIN` 1번, 4·5건 → 1번 유지, 6건 → 2번, 새 회차 뒤 0부터.
- 재발 회차를 연 뒤 같은 기록은 `IN_EARLIER_ROUND`이고 다시 후보가 되지 않음. 그 뒤 새 재발 1건이면 다시 후보.
- 조치 전에 발생(접수)했지만 조치 뒤에 적재된 기록 → `BEFORE_ACTION_DATE`. 적용일과 같은 날 접수 → `APPLICATION_UNKNOWN`(`SAME_DAY_AS_ACTION`).
- 일부 부서만 조치(요약 `ADOPTED`): 비적용 장비 기록은 재요청 3건에 들고, 적용 장비 1건이면 `RECURRENCE`가 우선.
- `as_of` 뒤, 회차 열기 전에 알려진 기록이 그 회차 근거에 포함되고 다음 집계에서 `IN_EARLIER_ROUND`.
- 전달 시각과 같은 시각의 기록은 세지 않음. `as_of` 뒤에 알려진 기록·조치·회차는 판정에 없음.
- 펌웨어 조치 1.72 뒤: 1.72 장비 1건 → `RECURRENCE`, 1.60 장비 → `PRE_ACTION_UNIT`과 업데이트 안내 대상, 버전 없음 → `APPLICATION_UNKNOWN`. 조치 적용일 전 기록은 `BEFORE_ACTION_DATE`.
- `COMPLETED`에서 재발 회차 열기 → 완료 기록 보존, 상태 재요약. 조치 전 상태에서 재발 열기·조치 뒤 재요청 열기는 409.
- 기획서 시연 두 흐름(가스켓 누유 재요청, 펌웨어 9건 구버전)을 합성 기록으로 재현.
