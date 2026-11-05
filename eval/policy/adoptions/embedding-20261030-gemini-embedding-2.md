# 설정 채택 결정 메모: embedding · gemini-embedding-2

상태 ADOPTED · 결정자 오새결 · 결정일 2026-10-30

> 최신 근거 실행의 표본 50건 기준 기록이며 합격 판정이 아니다. 게이트 결과 PASS 1 · FAIL 0 · NOT_EVALUABLE 0, blocker 없음.

## 채택 설정

| 항목 | 값 |
| --- | --- |
| dimension | 768 |
| task_type | RETRIEVAL_DOCUMENT |
| model | gemini-embedding-2 |

## 근거 실행

| 결과 파일 sha256 | 표본 | 시작 | git | 지표 |
| --- | --- | --- | --- | --- |
| d0c73a004b4b | 50 | 2026-10-30T02:09:51.464572+00:00 | 63c58d5ec134e2125f981637b4fc003268e77957 | {"hit_at_5": 0.98, "mrr": 0.7933333333333333} |

## 게이트 (근거 d0c73a004b4b)

| 지표 | 비교 | 합격선 | 값 | 판정 | blocker |
| --- | --- | --- | --- | --- | --- |
| hit_at_5 | >= | 0.9 | 0.98 | PASS | True |

과제 밖이라 제외한 게이트: category_macro_f1, critical_miss_count, urgency_macro_f1, urgency_non_none_recall, schema_violation_rate, priority_rule_exact, dept_rule_policy_match, dept_pipeline_exact

## 기각 후보

| 후보 | 사유 |
| --- | --- |
| gemini-embedding-001 | 같은 입력에서 hit@5 0.94·MRR 0.748로 gemini-embedding-2(0.98·0.793)보다 낮다 |

## 예외 승인(blocker FAIL)

예외 승인 없음.

## 사유

로컬 chunking-v2 1,098청크·질의 50건(라벨 round2 flash-lite-3.5-v2)을 규칙 기대값 기준(search-expected-v1, 사람 검수 전)으로 잰 결과다. 정답은 기대 문서의 v2 청크 전체이고, hit@5는 그 문서의 청크가 상위 5에 하나라도 든 비율이다. gemini-embedding-2(768차원, task_type RETRIEVAL_DOCUMENT)가 hit@5 0.98(49/50)·MRR 0.793으로 가장 높았다. 비교군은 gemini-embedding-001(hit@5 0.94·MRR 0.748), FTS 전역(0.76·0.412), FTS 제품 우선(hit@5 0.90)이다. 같은 입력으로 두 번 실행(run-20261030-embed-v2, -v2b)한 지표가 같았다. 한계: 하네스가 질의·문서에 같은 task_type을 쓴다(대칭). 표본이 50건이다. 그중 코드 없는 비영어 문의는 2건뿐이다. Gemini API 응답이 임베딩 토큰 수를 주지 않아 비용은 문자/4 근사(후보당 약 $0.012)다. 후속: 11/4~6 본시험 때 603건 중 코드 없는 비영어 32건을 포함해 다시 잰다. 벡터 열·하이브리드 RRF 연결은 KAN-99/100 후속이다.
