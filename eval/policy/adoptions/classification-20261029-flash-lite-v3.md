# 설정 채택 결정 메모: classification · flash-lite-3.5-v3

상태 ADOPTED · 결정자 오새결 · 결정일 2026-10-29

> 최신 근거 실행의 표본 603건 기준 기록이며 합격 판정이 아니다. 게이트 결과 PASS 4 · FAIL 1 · NOT_EVALUABLE 0, blocker 있음. blocker FAIL 1건은 사람이 예외로 승인해 채택했다(아래 예외 승인 표).

## 채택 설정

| 항목 | 값 |
| --- | --- |
| model | gemini-3.5-flash-lite |
| prompt_version | v3 |
| prompt_fingerprint | f299989ca90bcf99 |

## 근거 실행

| 결과 파일 sha256 | 표본 | 시작 | git | 지표 |
| --- | --- | --- | --- | --- |
| 455602178bd4 | 50 | 2026-10-29T11:56:16.063459+00:00 | e00b19e597b97b3405f0a02ac29e8a230d7cda6a | {"category_macro_f1": 0.9312418300653595, "urgency_macro_f1": 0.7846153846153846, "urgency_non_none_recall": 1.0, "critical_miss_count": 0, "schema_violation_rate": 0.0} |
| 3c0d54ed9fcf | 603 | 2026-10-29T11:58:27.853156+00:00 | e00b19e597b97b3405f0a02ac29e8a230d7cda6a | {"category_macro_f1": 0.9405557487714834, "urgency_macro_f1": 0.9303681184912139, "urgency_non_none_recall": 0.9832402234636871, "critical_miss_count": 1, "schema_violation_rate": 0.0} |

## 게이트 (근거 3c0d54ed9fcf)

| 지표 | 비교 | 합격선 | 값 | 판정 | blocker |
| --- | --- | --- | --- | --- | --- |
| category_macro_f1 | >= | 0.9 | 0.9405557487714834 | PASS | True |
| critical_miss_count | == | 0.0 | 1.0 | FAIL | True |
| urgency_macro_f1 | >= | 0.7 | 0.9303681184912139 | PASS | True |
| urgency_non_none_recall | >= | 0.85 | 0.9832402234636871 | PASS | True |
| schema_violation_rate | == | 0.0 | 0.0 | PASS | True |

과제 밖이라 제외한 게이트: hit_at_5, priority_rule_exact, dept_rule_policy_match, dept_pipeline_exact

## 기각 후보

| 후보 | 사유 |
| --- | --- |
| flash-lite-3.5-v2 | valid 603에서 return_subtype 규칙 위반 1건(결정적), v3로 해소 |

## 예외 승인(blocker FAIL)

| 지표 | 값 | 사유 | 결정자 | 결정일 |
| --- | --- | --- | --- | --- |
| critical_miss_count | 1.0 | CSV1-001253: 상담원 표기 SITE_DOWN과 본문(오주문) 불일치. 사용자 결정(2026-10-29)으로 모델 판단(NONE)을 채택, 정답 잠금은 유지 | 오새결 | 2026-10-29 |

## 사유

valid 603 전체, 입력 v4(전화 표기 포함), 프롬프트 v3(return_subtype null 규칙 강조), 하네스 시도 2 재호출 켬. flash-lite v3: 유효 603/603(스키마 위반 0, 재호출 0), 주분류 macro-F1 0.941, 긴급도 macro-F1 0.930, 비NONE 재현율 0.983 → 게이트 5개 중 4개 PASS, blocker 1개(CRITICAL 누락) FAIL을 사용자 예외로 승인해 채택. CRITICAL 누락 1/27은 CSV1-001253(상담원 표기 SITE_DOWN vs 본문 오주문)으로 사용자 결정(2026-10-29)에 따라 모델 판단(NONE)을 채택한 건이며 정답 잠금을 바꾸지 않아 채점상 누락으로 남는다. 비용 0.31 USD, p50 1.1초. 50건 v2 vs v3 비교에서 퇴행 없음. 같은 입력의 v2 실행 2회(0.887/0.847)와 v3(0.930)는 실행 간 변동 범위를 포함한다. 사용자 결정(2026-10-29 21:15 KST): 채택. 제품 기본 프롬프트 판본은 v3로 변경(별도 PR).
