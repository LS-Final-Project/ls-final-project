# 설정 채택 결정 메모: classification · flash-3.6-v4-out4096

상태 ADOPTED · 결정자 오새결 · 결정일 2026-11-05

> 최신 근거 실행의 표본 603건 기준 기록이며 합격 판정이 아니다. 게이트 결과 PASS 5 · FAIL 0 · NOT_EVALUABLE 0, blocker 없음.

## 채택 설정

| 항목 | 값 |
| --- | --- |
| model | gemini-3.6-flash |
| prompt_version | v4 |
| max_output_tokens | 4096 |
| prompt_fingerprint | 08619d15b472d41c |

## 근거 실행

| 결과 파일 sha256 | 표본 | 시작 | git | 지표 |
| --- | --- | --- | --- | --- |
| 23b1d3ed08cf | 603 | 2026-11-05T07:11:57.480001+00:00 | 21283ab750a54daace9c617517ef351c1fedc2f9 | {"category_macro_f1": 0.9569615486277062, "urgency_macro_f1": 0.9894754024541258, "urgency_non_none_recall": 0.994413407821229, "critical_miss_count": 0, "schema_violation_rate": 0.0} |

## 게이트 (근거 23b1d3ed08cf)

| 지표 | 비교 | 합격선 | 값 | 판정 | blocker |
| --- | --- | --- | --- | --- | --- |
| category_macro_f1 | >= | 0.9 | 0.9569615486277062 | PASS | True |
| critical_miss_count | == | 0.0 | 0.0 | PASS | True |
| urgency_macro_f1 | >= | 0.7 | 0.9894754024541258 | PASS | True |
| urgency_non_none_recall | >= | 0.85 | 0.994413407821229 | PASS | True |
| schema_violation_rate | == | 0.0 | 0.0 | PASS | True |

과제 밖이라 제외한 게이트: hit_at_5, priority_rule_exact, dept_rule_policy_match, dept_pipeline_exact

## 기각 후보

| 후보 | 사유 |
| --- | --- |
| flash-3.6-v3-out4096 | 같은 표본에서 이미 준 정보 재요청 71/561(v4 4/561). 제공 정보 코드 혼동(purchase_doc·po_number, error_code·failure_description) |

## 예외 승인(blocker FAIL)

예외 승인 없음.

## 사유

사용자 승인 기준으로 채택(2026-11-05). 기준은 게이트 5개 PASS, 이미 준 정보 재요청이 71/561보다 낮음, 다른 지표가 반복 차이 이상 나빠지지 않음(CRITICAL 누락 0, 주분류·긴급도 macro-F1 하락 0.01 이하)이다. 검증 603건(대상 해시 3d47474c, v3 채택 실행과 같은 표본), 입력 v4, gemini-3.6-flash, 출력 상한 4096, 재호출 켬에서 지시문만 v3에서 v4(헷갈린 정보 코드 네 개의 코드북 칸 이름 한 줄 추가)로 바꿨다. v4는 유효 603/603(형식 위반 0, 재호출 0), 주분류 macro-F1 0.957(v3 0.962, 정답 575 대 578), 긴급도 macro-F1 0.989(v3 0.984), 비NONE 재현율 178/179(v3 176/179), CRITICAL 누락 0/27이다. 재요청 문의 4/561(v3 71/561), 항목 4/1,643(v3 71/1,643), 제공 정보 FULL precision 0.859·recall 0.988·F1 0.919(v3 0.719·0.952·0.819), PRECISION_ONLY precision 0.670(v3 0.554)이다. 반품 하위유형 53/53은 같고, 보조 분류 일치 579/603(v3 581), 담당부서 파이프라인 580/603(v3 582)이다. v3와 v4의 주분류 일치는 591/603으로, 같은 v3를 50건 두 번 돌린 일치 48/50과 같은 수준이라 주분류 3건 차이는 반복 차이 안으로 본다. 비용은 3.33 USD(v3 3.39 USD), 평균 지연 7.57초(v3 6.81초)다. 이 603건은 지시문 v1~v4 조정에 쓰였으므로 독립 성능이 아니다. v4 문구도 이 표본의 혼동 분석에서 나왔다.
