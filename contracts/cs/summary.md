# C/S 문의 한국어 요약 계약 (KAN-131 화면 요청)

원본: `backend/api/cs/summary/`(조회·생성·저장), `backend/engine/cs/summary.py`(입력·프롬프트·출력 검사), 마이그레이션 `infra/migrations/cs/20261106_KAN-131_ticket_summaries.sql`(README C25).

**상태: API 구현(가짜 모델 검사). 기본 비활성.** 기획서에는 아직 없는 기능이다(4.2절 다국어 분석은 "번역 없이 5칸만 판정"). 문의 처리 화면(CS0102)의 "한국어 요약" 칸에 쓰며, 기획서 반영은 팀에서 따로 정한다.

## 무엇을 하나

- 해외 고객 문의 원문(제목·본문)을 한국인 담당자가 빨리 파악하도록 한국어 1~3문장(300자 이하)으로 줄인다.
- **판정이 아니다.** 유형·긴급도·보증·원인은 분류(5칸)와 규칙표가 정한다. 요약은 고객이 쓴 내용만 옮기고, 업무 상태·분류·규칙 결과를 바꾸지 않는다. 출처는 늘 `ai_draft`다.
- 분류와 따로 부른다. 채택된 분류 프롬프트(3.6-flash v3)와 평가를 바꾸지 않기 위해서다. 프롬프트는 `summary.py`에 두고 판본은 `v1`(채택 전)이다.

## 입력과 출력 검사

- 입력: 분류와 같은 허용 필드·마스킹(이메일, 라벨 붙은 시리얼)을 거친 `channel`·`body_language`·`subject`·`body`. 입력 판본은 `cs-summary-input-v1`이고, 마스킹된 입력의 SHA-256(`input_sha256`)으로 원문 변경을 알아챈다.
- 출력 `{"summary_ko": "..."}` 하나. 아래를 어기면 저장하지 않고 실패로 기록한다.

| 코드 | 규칙 |
| --- | --- |
| `CS_SUMMARY_SCHEMA` | JSON 객체 하나, 중복 키·다른 키 없음, 1~300자, 공백만은 안 됨 |
| `CS_SUMMARY_NOT_KOREAN` | 한글이 있어야 한다 |
| `CS_SUMMARY_UNGROUNDED` | 글자와 숫자가 섞인 코드(모델명·시리얼·오류 코드)는 원문에 그대로 있어야 한다(끝의 `.`·`,`·`-`는 떼고 `/`는 나눈다). 나머지 수는 값으로 원문에 있어야 한다(앞자리 0·천 단위 쉼표 무시, 날짜를 `2026년 10월 2일`처럼 바꿔 쓰는 것은 된다). 한계: 원문 어딘가에 같은 값의 수가 있으면 다른 뜻으로 써도 통과한다 |
| `CS_SUMMARY_COMMITMENT` | 승인·확정 약속 문구(초안과 같은 금지 문구)를 사실처럼 쓴 문장이 없어야 한다. `고객은 …승인되었다고 함`처럼 주장을 옮기는 문장(`…다고·라고 함/주장/들었`)은 된다 |

## API (`/api/v1/cs/tickets/{ticket_id}/summary`)

| 메서드 | 허용 | 동작 |
| --- | --- | --- |
| `GET` | 문의 조회 범위 | 가장 최근의 성공한 요약(`summary`, 없으면 null)과 가장 최근 시도(`latest_attempt`, 실패 포함). 모델을 부르지 않는다. 요약 뒤 원문이나 프롬프트 판본이 바뀌었으면 `summary.stale = true` |
| `POST` | cs_agent(범위 안) | 같은 원문·프롬프트 판본의 성공한 요약이 있으면 모델을 부르지 않고 그것을 200으로 준다. 없으면 모델을 불러 저장하고 201. 같은 문의에 동시에 온 요청은 프로세스 안에서 줄 세워 모델을 한 번만 부르고, 다른 인스턴스가 먼저 저장했으면 고유 색인으로 중복 없이 그것을 200으로 준다. 실패하면 시도를 저장하고 502 `CS_SUMMARY_FAILED`(`details[0].code`에 위 코드 또는 `CS_PROVIDER_TIMEOUT`·`CS_RATE_LIMITED`·`CS_PROVIDER_ERROR`) |

- 순서는 permissions.md 1.3: 범위 밖 문의 404 `CS_TICKET_NOT_FOUND`, 역할 403. 요약할 수 없는 문의(빈 본문·잘못된 언어 표기 등)에 POST하면 422 `CS_SUMMARY_INPUT_INVALID`이고, GET은 저장본을 `stale = true`로 보여 준다.
- `summary`: `id`·`summary_ko`·`created_at`(AI 작성 시각)·`requested_model`·`model_version`·`prompt_version`·`input_version`·`stale`.
- 웹 중계 허용표에 `GET`·`POST`(query 없음)로 있다.

## 저장 (`cs.ticket_summaries`, 추가만)

시도마다 한 행(같은 원문·판본의 `VALID`는 하나만, 고유 색인 `ticket_summaries_valid_once`): 결과(`VALID`·`INVALID_OUTPUT`·`PROVIDER_ERROR`)·오류 코드·요약·입력 판본과 SHA-256·프롬프트 판본·요청 모델·실제 모델 판본·요청자·지연·토큰 5종·비용·가격표 판본(KAN-85와 같은 이름). C/S 실행 계정(`cs_app`)은 SELECT·INSERT만 하고 고치거나 지우지 못한다. 원문과 모델 원시 응답은 저장하지 않는다.

## 켜기

- C/S 서비스 `CS_SUMMARY_ENABLED=true`(기본 false, 꺼져 있으면 두 경로 모두 404). 모델 `CS_SUMMARY_MODEL`(기본 `gemini-3.6-flash`), 출력 상한 `CS_SUMMARY_MAX_OUTPUT_TOKENS`(기본 4096, thinking 포함), 프로세스당 동시 호출 `CS_SUMMARY_MAX_CONCURRENT_CALLS`(기본 4, 요약 전용 스레드 풀). 공급자 오류도 공급자가 알려 준 사용량을 저장한다. Gemini 키는 서비스의 기존 `GEMINI_API_KEY`·`AI_PROVIDER`를 쓴다.
- 켜기 전에 공용 DB에 C25를 적용한다. 켜면 C/S 서비스가 요약마다 모델을 한 번 부른다(비용).
- 남은 것: 실제 모델로 요약 품질과 검사 통과율 확인(사용자 승인 뒤), 화면 연결(KAN-131), 기획서 반영(팀 논의).
