# C/S 같은 고장 AI 추천 (KAN-245)

원본: `backend/api/cs/faults/suggestion.py`. 판단 입력·프롬프트·출력 검사는 KAN-204에서 평가한 `backend/engine/cs/fault_matching.py`(`fault-match-v2`)를 그대로 쓴다. 평가 결과는 [평가 문서](../../docs/evaluation.md) "고장 코드 없이 같은 고장 판단"이다. 검사는 `tests/cs/test_fault_suggestion_db.py`(가짜 모델)다.

## 무엇을 하는가

담당자가 종결한 문의의 해결 기록을 남길 때, 고장 코드를 고르기 전에 "AI로 같은 고장 찾기"를 누른다. AI는 이 문의 원문을 같은 제품의 알려진 고장 묶음과 비교해 하나를 고른다.

| decision | 뜻 | 화면 |
| --- | --- | --- |
| `EXISTING` | 증상이 같은 기존 고장이 분명함. `fault_id`가 그 고장 | 고장 ID·카탈로그 증상·묶음 해결 기록 수와 "이 고장으로 고르기" |
| `NEW` | 증상은 있지만 어느 기존 고장과도 다름 | 새 고장일 수 있다는 안내 |
| `UNSURE` | 원문에 증상이 없거나 둘 이상과 구별되지 않음 | 판단 보류, 직접 고르라는 안내 |

- 추천은 AI 작성물(`provenance=ai_draft`)이다. 저장하지 않고 아무 상태도 바꾸지 않는다. 고장 ID는 사람이 골라 해결 기록 등록(`POST …/resolution-case`)으로 확정한다. 반복 판정(90일·3곳, [후보 규칙](improvement-candidates.md))은 확정된 고장 ID로 규칙이 한다.
- 추천을 골라도 바로 저장되지 않는다. 담당자가 나머지 칸과 함께 저장해야 한다.

## API

`POST /api/v1/cs/tickets/{ticket_id}/fault-suggestion` (본문 없음)

- 허용: `cs_agent`만. 다른 고객의 문의 원문을 예시로 쓰기 때문이다. 범위 밖 문의는 404, 범위 안 다른 역할은 403이다.
- `CS_FAULT_SUGGESTION_ENABLED`가 꺼져 있으면 404다(기본 꺼짐). 로컬 실행 환경은 CS 실행기 중계(`/fault-suggestion`)로 켠다.
- 비교 대상: 같은 `product_code`·같은 `data_origin`이고 지금까지 알려진(`known_at <= now()`) 다른 문의의 해결 기록 중 `FLT-000` 형식 고장 ID가 있는 것. 고장 ID별로 묶고, 처음 알려진 순서로 `G1..`을 붙인다. 묶음마다 처음 알려진 문의 원문 2건을 예시로 보낸다.
- 모델에 가지 않는 것: 고장 ID, 해결 기록의 증상·원인·조치 문장, 고객 식별자. 원문은 분류·번역과 같은 허용 필드·마스킹(`build_input`)을 거친다.
- 비교할 묶음이 없으면 모델을 부르지 않고 `NEW`, `model=null`이다.
- 호출 실패나 규칙 밖 응답(없는 참조 등)은 한 번 더 부른다. 두 번 다 안 되면 502 `CS_FAULT_SUGGESTION_FAILED`(`details[0].code`에 마지막 실패 코드)다.

응답 `data`: `decision`, `fault_id`, `fault_symptom`(카탈로그 증상), `matched_cases`, `evidence_quote`(이 문의 원문의 짧은 구절), `reason`, `compared_faults`, `compared_cases`, `prompt_version`, `model`, `provenance`.

## 한계

- 평가는 교육용 합성 데이터다. 예시 문의가 템플릿에 가깝고, 실제 해결 기록 문장은 고장 ID와 1:1이 아닐 것이다.
- 설치·보증·반품 문의는 원문에 증상이 없는 경우가 많아 `UNSURE`가 많다. 의도한 동작이다.
- 같은 문의로 다시 누르면 모델을 다시 부른다(저장·재사용 없음). 비용이 문제가 되면 번역처럼 저장본을 둔다.
