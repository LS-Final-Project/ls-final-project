# C/S 고객 회신 반영 분석 계약

원본: `backend/api/cs/reply_analysis/`(조회·생성·저장), `backend/engine/cs/reply.py`(KAN-109 입력 정제·출력 검사·초안 입력 갱신), 마이그레이션 `infra/migrations/cs/20261113_KAN-230_reply_analyses.sql`(README C31). 구조는 [한국어 번역](translation.md)과 같다. 회신 원문은 C30(`cs.ticket_customer_replies.subject`·`body`, [문의 계약](tickets.md) 회신 통지)에 있다.

**상태: API·화면 구현(가짜 모델 검사). 기본 비활성.** KAN-230 2단계. 추가 정보를 요청한 뒤 고객이 회신하면, 담당자가 문의 처리 화면에서 버튼을 눌러 회신이 요청한 정보에 답했는지 AI 해석을 받는다.

## 무엇을 하나

- 연결된 고객 회신 하나를 해석한다: 같은 요청·같은 장비·같은 판본에 대한 회신인지(각각 이유와 함께), 요청한 정보 코드 중 답한 것(`answered_codes`), 고객이 새로 말한 사실(`new_facts`, STATED/UNCONFIRMED), 아직 답하지 않은 요청(`remaining_questions`).
- 해석 결과로 초안 입력 갱신값(`update`)을 계산한다: 제공 코드(`provided_codes` = 지금 제공 정보 ∪ 답한 코드), 부족 코드(`still_missing` = 요청 − 제공), 다음 행동(`next_action`: 다른 요청·장비·판본이면 `ESCALATE_TO_AGENT`, 불명확하면 `CONFIRM_WITH_CUSTOMER`, 부족 코드가 있으면 `REQUEST_AGAIN`, 없으면 `DRAFT_ANSWER`). 다른 요청·장비·불명확 회신의 답은 제공 코드에 합치지 않는다(규칙은 `apply_reply`).
- **판정이 아니고 아무것도 바꾸지 않는다.** 업무 상태(KAN-110 전이)·분석 칸·정정·장비 연결·과거 사례를 바꾸지 않는다. 답한 정보를 제공 정보에 반영하는 것은 사람이 기존 정정 API(`POST /api/v1/cs/tickets/{id}/analysis/corrections`의 `provided_info`)로 한다. 출처는 늘 `ai_draft`다.
- **담당자가 누를 때만 돈다(2026-11-05 결정).** 회신이 들어와도 자동으로 해석하지 않는다. 한 번 누르면 모델을 한 번 부른다. 같은 입력이면 저장본을 다시 쓴다.

## 입력과 출력 검사

- 대상 회신: 요청의 `message_id`, 없으면 그 문의의 회신 중 본문이 있는 가장 최근 것(`received_at` 기준). 본문이 없는 회신(C30 이전 기록, 원문을 못 읽은 회신)은 해석할 수 없다.
- 맥락(`ReplyContext`):
  - `requested_info_codes`: 고객에게 실제로 나간 메시지(`cs.ticket_customer_sends.recorded_at`이 있는 것)에서 물은 코드, 정렬. 분석 화면 `missing_info.already_asked`와 같은 규칙이다. 승인만 되고 나가지 않은 질문은 넣지 않는다. 물은 것이 없으면 빈 목록이고, 그때 모델은 답한 코드를 낼 수 없다.
  - `already_provided_codes`: 사람 정정을 반영한 유효 제공 정보(`provided_info`의 effective 값, 분석 화면과 같은 계산), 정렬·중복 제거. 유효 분석과 정정이 모두 없으면 빈 목록으로 해석한다(막지 않는다).
  - `prior_options`: 가장 최근의 성공한 답변 초안(3안). 없거나 지금 스키마로 읽을 수 없으면 null.
  - `ticket_product_code`·`fw_version_reported`: 문의 값.
- 회신(`CustomerReply`): 채널은 `EMAIL`(연결 회신은 메일로만 들어온다), 언어는 문의의 `body_language`, 본문은 회신 본문(인용 이력은 플랫폼이 잘라 저장), `received_at`은 회신 수신 시각(모델 입력에 넣지 않음). **회신 제목은 넣지 않는다**(엔진 규칙).
- 마스킹: 분류와 같은 `build_input`(이메일 → `[PERSONAL_PROVIDED]`, 라벨 붙은 시리얼 → `[SERIAL_PROVIDED]`). 확정 장비 시리얼이 본문에 있으면 그 토큰 전체도 `[SERIAL_PROVIDED]`로 가린다. 어댑터가 식별한 민감값(`sensitive_values`)은 아직 없어 빈 집합이다.
- 장비 대조: 문의에 확정 연결된 장비(`cs.ticket_unit_links.link_status = 'CONFIRMED'`)의 `serial_no`와 회신의 시리얼을 코드가 정확히 대조해 `reply_serial_match`(`NOT_MENTIONED`·`MATCHES`·`DIFFERS`·`NO_CONFIRMED_UNIT`)만 모델에 준다. 시리얼 원값은 모델 입력·저장·오류 어디에도 남지 않는다.
- 입력 판본은 `cs-reply-input-v2`, 프롬프트 판본은 레지스트리 `reply` 기본값(`v2`). 정제 입력 전체의 SHA-256(`input_sha256`, 판본 포함)으로 맥락 변경을 알아챈다.
- 출력은 `ReplyInterpretation` JSON 하나. 아래를 어기면 저장하지 않고 실패로 기록한다(`validate_output`).

| 코드 | 규칙 |
| --- | --- |
| `CS_INVALID_OUTPUT` | 형식(키·타입·중복 키·코드 중복, 답한 코드와 남은 질문 코드 겹침), 요청 코드 밖의 답·질문, 시리얼 대조 모순(DIFFERS인데 same_unit ≠ DIFFERENT, MATCHES인데 DIFFERENT, NO_CONFIRMED_UNIT인데 UNCLEAR·DIFFERENT가 아님) |
| `CS_FORBIDDEN_COMMITMENT` | 판정 이유·새 사실·남은 질문에 승인·확정 약속 문구(초안 금지 약속과 같은 검사) |

## API (`/api/v1/cs/tickets/{ticket_id}/reply-analysis`)

| 메서드 | 허용 | 동작 |
| --- | --- | --- |
| `GET` (`?message_id=` 선택) | cs_agent와 C/S 부서 관리자(범위 안). 고객 회신은 C/S 업무라 부서 사용자는 403(tickets.md 조회 범위) | 대상 회신의 지금 입력·프롬프트 판본에 맞는 성공한 해석, 없으면 가장 최근의 성공한 해석(`analysis`, 없으면 null)과 가장 최근 시도(`latest_attempt`, 실패 포함). 모델을 부르지 않는다. 본문이 있는 회신이 없으면 `message_id`·`analysis`·`latest_attempt`가 모두 null |
| `POST` (본문 `{"message_id": uuid}` 선택) | cs_agent만(범위 안). 부서 담당자는 403 | 본문이 있는 회신이 없거나 지정한 회신에 본문이 없으면 422 `CS_REPLY_TEXT_MISSING`(모델 호출 없음). 같은 회신·입력·프롬프트 판본의 성공한 해석이 있으면 모델을 부르지 않고 200. 없으면 모델을 한 번 불러 저장하고 201. 동시 요청은 번역과 같다(프로세스 안 줄 세우기, 다른 인스턴스 저장은 고유 색인). 다른 인스턴스가 먼저 저장했는데 다시 읽지 못하면 409 `CS_REPLY_ANALYSIS_CONFLICT`. 실패하면 시도를 저장하고 502 `CS_REPLY_ANALYSIS_FAILED`(`details[0].code`에 위 코드 또는 `CS_PROVIDER_TIMEOUT`·`CS_RATE_LIMITED`·`CS_PROVIDER_ERROR`) |

- 순서는 permissions.md 1.3: 범위 밖 문의 404 `CS_TICKET_NOT_FOUND`, 역할 403, 다른 문의의 회신(`message_id`) 404 `CS_REPLY_NOT_FOUND`. 맥락으로 입력을 만들 수 없으면(잘못된 언어 표기 등) POST는 422 `CS_REPLY_INPUT_INVALID`이고, GET은 저장본을 `stale = true`로 보여 준다.
- `stale`: 해석 뒤 입력이 바뀌었다(요청 코드가 늘었거나, 사람이 제공 정보를 정정했거나, 새 분석·초안이 생겼거나, 확정 장비가 바뀌어 대조 결과가 달라졌거나, 프롬프트 판본이 바뀜). 다시 누르면 새로 만든다.
- 웹 중계 허용표에 `GET`(query `message_id`)·`POST`로 있다.

응답 예(`POST` 201, 줄임):

```json
{
  "data": {
    "ticket_id": "…",
    "message_id": "…",
    "analysis": {
      "id": "…",
      "message_id": "…",
      "interpretation": {
        "applicability": {
          "same_request": "APPLIES", "same_request_reason": "…",
          "same_unit": "NOT_MENTIONED", "same_unit_reason": "…",
          "same_version": "NOT_MENTIONED", "same_version_reason": "…"
        },
        "answered_codes": ["photos"],
        "new_facts": [{"text": "…", "kind": "STATED"}],
        "remaining_questions": [{"info_code": "serial_no", "question": "…"}]
      },
      "update": {
        "provided_codes": ["photos"], "still_missing": ["serial_no"], "next_action": "REQUEST_AGAIN",
        "applicability": {"…": "…"}, "new_facts": [], "remaining_questions": []
      },
      "created_at": "2026-11-05T01:00:00Z",
      "requested_model": "gemini-3.6-flash", "model_version": "gemini-3.6-flash-001",
      "prompt_version": "v2", "input_version": "cs-reply-input-v2", "stale": false
    },
    "latest_attempt": {"id": "…", "message_id": "…", "outcome": "VALID", "error_code": null, "created_at": "…"},
    "provenance": "ai_draft"
  },
  "meta": {"request_id": "…"}
}
```

## 저장 (`cs.ticket_reply_analyses`, 추가만)

번역 표(C28)와 같은 열에서 번역 칸 대신 `message_id`(→ `cs.ticket_customer_replies`), `interpretation`(검사를 통과한 해석)·`reply_update`(갱신값) jsonb를 둔다. 시도마다 한 행(같은 회신·입력·판본의 `VALID`는 하나만, 고유 색인 `ticket_reply_analyses_valid_once`). `VALID`일 때만 두 jsonb가 있고 실패 행에는 `error_code`가 있다. 사용량·비용 열은 요약·번역과 같다. C/S 실행 계정(`cs_app`)은 SELECT·INSERT만 한다. 회신 원문과 모델 원시 응답은 이 표에 저장하지 않는다.

## 켜기

- C/S 서비스 `CS_REPLY_ANALYSIS_ENABLED=true`(기본 false, 꺼져 있으면 두 경로 모두 404). 모델 `CS_REPLY_ANALYSIS_MODEL`(기본 `gemini-3.6-flash`), 출력 상한 `CS_REPLY_ANALYSIS_MAX_OUTPUT_TOKENS`(기본 8192, thinking 포함), 프로세스당 동시 호출 `CS_REPLY_ANALYSIS_MAX_CONCURRENT_CALLS`(기본 4, 전용 스레드 풀). Gemini 키는 서비스의 기존 `GEMINI_API_KEY`·`AI_PROVIDER`를 쓴다.
- 켜기 전에 공용 DB에 C30·C31을 적용한다.
- 비용: 담당자가 누를 때마다(같은 입력이면 재사용) 모델 1회. 입력은 회신 본문(최대 20,000자)과 이전 초안 3안이라 번역보다 입력 토큰이 많을 수 있다.
- 로컬 QA runner는 번역과 같이 CS driver의 인증된 내부 bridge(`/reply-analysis` 경로, 출력 상한 8192, 정확한 프롬프트만 받음)를 주입하고 기본 활성이다. 저장본 조회·재사용은 driver 없이 된다.
## 화면

문의 처리(CS0102) 원문 상자의 "이후 대화" 위에 회신 반영 분석 카드가 있다(`ReplyAnalysisCard`). 본문이 있는 고객 회신이 있을 때만 보이고, GET이 404(꺼진 배포)면 숨긴다.

- 단추 "회신 반영 분석"(C/S 담당자, 실제 API)이 POST를 부른다. 해석 뒤 새 회신이 오거나 `stale`이면 "다시 해석"이다.
- 결과: 다음 행동(`next_action`), 같은 요청·장비·판본(각 이유), 답한 정보·아직 부족한 정보, 고객이 새로 말한 내용(고객 진술/불확실), 아직 답하지 않은 요청. 마지막 시도가 실패면 그 사유를 보인다.
- "답한 정보 반영": 사유(기본값 자동 작성, 수정 가능)를 확인하면 분류 정정 API에 `provided_info = update.provided_codes`를 보낸다. 새로 더할 코드가 없거나, `stale`이거나, 검토중이 아니면 꺼진다. 다음 행동이 담당자 판단·고객 확인이면 단추가 없다.

- 남은 것: 실제 모델로 해석 품질 확인(사용자 승인 뒤), 기획서 반영(팀 논의).
