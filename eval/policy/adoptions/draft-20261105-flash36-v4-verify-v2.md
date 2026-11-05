# 설정 채택 결정 메모: draft · flash-3.6-draft-v4-out6000-verify8192

상태 ADOPTED · 결정자 오새결 · 결정일 2026-11-05

> 표본 50건과 다국어 고정물 187문장 기준 기록이다. 합격 결정은 아래 사용자 합격선 절의 사용자 결정이고 게이트 표의 판정이 아니다. 게이트 결과 PASS 1 · FAIL 0 · NOT_EVALUABLE 0, blocker 없음.

## 채택 설정

| 항목 | 값 |
| --- | --- |
| model | gemini-3.6-flash |
| prompt_version | v4 |
| max_output_tokens | 6000 |
| verification_max_output_tokens | 8192 |
| prompt_fingerprint | 1c35a30973e5c1b7 |
| evidence_limit | 5 |
| verification_prompt_version | v2 |
| verification_prompt_fingerprint | 8e7561653cd0b38e (스키마를 붙인 지시문 96c0b219edd63500) |

## 근거 실행

| 결과 파일 sha256 | 표본 | 시작 | git | 지표 |
| --- | --- | --- | --- | --- |
| 6744f290ba32 | 50 | 2026-11-05T07:12:29.192888+00:00 | ccb5933fcbeeea0b77cace8c4082de0019d95c53 | {"schema_violation_rate": 0.0, "forbidden_rate": 0.0, "grounded_sentence_rate": 1.0, "grounded_case_rate": 1.0, "info_f1": {"CONCISE": 1.0, "DETAILED": 1.0, "INFO_FIRST": 1.0}} |

## 사용자 합격선 (2026-11-05 사용자 결정)

gates-v1에 없는 값이며 사용자가 이 채택을 위해 정했다. 측정 기록은 `artifacts/presentation-fixes-20261105/7-commitment-check/`(git 제외)에 있다.

| 항목 | 합격선 | 값 |
| --- | --- | --- |
| 다국어 금지 재현율(고정물 `tests/cs/fixtures/forbidden_commitments_multilang.json`) | 19/119(정규식)보다 분명히 높음 | 119/119 (입력 구성 두 가지 모두) |
| 통제 오탐 | 0/68 (설명이 있으면 1까지) | 0/68 |
| 새 50건 최종 VERIFIED·NEEDS_REVIEW·NOT_VERIFIED | 기준 48·2·0보다 나쁘지 않음 | 50·0·0 |
| 최종 문장 판정 SUPPORTED·UNSUPPORTED·UNVERIFIABLE | 기준 221·2·0 | 227·0·0 |
| 형식 위반(처음·최종) | 0 | 0/50 · 0/50 |
| 확정 약속 표시(검증 판정 490줄) | 표시된 문장은 모두 확인 | 0 |

## 한계

- 합성 데이터 50건을 한 번 실행했다. 초안도 새로 만들어서, 기준과의 차이를 검증 판본만의 효과로 나눌 수 없다.
- 고정물 문장은 모델(Claude)이 쓴 명확한 예문이고 경계 사례가 없다.
- 정보 요청 질문(info_requests)은 v2 검증에 들어가지 않고 정규식만 본다.

## 게이트 (근거 6744f290ba32)

| 지표 | 비교 | 합격선 | 값 | 판정 | blocker |
| --- | --- | --- | --- | --- | --- |
| schema_violation_rate | == | 0.0 | 0.0 | PASS | True |

과제 밖이라 제외한 게이트: category_macro_f1, critical_miss_count, urgency_macro_f1, urgency_non_none_recall, hit_at_5, priority_rule_exact, dept_rule_policy_match, dept_pipeline_exact

## 기각 후보

없음

## 예외 승인(blocker FAIL)

예외 승인 없음.

## 사유

사용자 합격 결정(2026-11-05)이다. 바뀐 것은 검증 지시문 v1에서 v2(KAN-161, 문장별 확정 약속 표시)로 가는 것 하나이고, 초안 설정(gemini-3.6-flash, 초안 v4, 출력 6000, 검증 출력 8192, 근거 5건)은 draft-20261101-flash36-v4와 같다. 사용자가 정한 합격선은 네 가지다. 다국어 재현율이 19/119보다 분명히 높을 것, 통제 오탐 0/68(설명이 있으면 1까지), 새 50건 초안 지표가 나빠지지 않을 것, 형식 위반 0이다. 측정값은 다음과 같다. 고정물 187문장(16개 언어)에서 금지 119/119, 통제 오탐 0/68이었고, 입력 구성 두 가지(EVIDENCE 한 문장, 중립 EVIDENCE와 CONDITION 두 문장)가 같았다. 2026-11-05 새 50건(사례 해시 893f57f3)을 같은 근거와 인자로 다시 돌렸다. 최종 상태는 VERIFIED 50, NEEDS_REVIEW 0, NOT_VERIFIED 0이었다(기준 48/2/0). 최종 문장 판정은 SUPPORTED 227, UNSUPPORTED 0이었다(기준 221/2/0). 형식 위반은 처음과 최종 모두 0/50, 금지 약속(정규식)은 0, 근거 밖 인용은 0, 인용 일치는 1.0이었다. 확정 약속 표시는 검증 판정 490줄 가운데 0이었다. 한계: 합성 데이터이고 50건 1회 실행이다. 초안도 새로 생성해서 기준과의 차이를 검증 판본 하나의 효과로 나눌 수 없다. 고정물 문장은 모델(Claude)이 쓴 명확한 예문이라 경계 사례가 없다. 정보 요청 질문은 v2 검증에 들어가지 않고 정규식만 본다.
