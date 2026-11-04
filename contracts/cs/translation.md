# C/S 문의 한국어 번역 계약

원본: `backend/api/cs/translation/`(조회·생성·저장), `backend/engine/cs/translation.py`(입력·프롬프트·출력 검사), 마이그레이션 `infra/migrations/cs/20261110_KAN-213_ticket_translations.sql`(README C28). 구조는 [한국어 요약](summary.md)과 같다.

**상태: API·화면 구현(가짜 모델 검사). 기본 비활성.** 기획서에는 아직 없는 기능이다(4.2절 다국어 분석은 "번역 없이 5칸만 판정"). 문의 처리 화면(CS0102) "고객 원문" 칸의 원문/한국어 전환과, 목록(문의 조회·고객 이력·업무 홈)의 한국어 제목에 쓴다. 영어가 아닌 문의(es·fr·th·it·id·zh·tr·ru·ar·vi·pt·de·ja·pl 등)가 많아 담당자가 원문 전체를 한국어로 읽을 수 있게 한다. 기획서 반영은 팀에서 따로 정한다.

## 무엇을 하나

- 고객 문의 원문(제목·본문)을 **요약하지 않고 전체를** 한국어로 옮긴다. 문단·줄바꿈은 원문 그대로 둔다. 영어 문의도 번역할 수 있다.
- **판정이 아니다.** 유형·긴급도·보증·원인은 분류(5칸)와 규칙표가 정한다. 번역은 사실을 더하거나 빼거나 해석하지 않고, 원인 추정·약속을 쓰지 않는다. 업무 상태·분류·규칙 결과를 바꾸지 않는다. 출처는 늘 `ai_draft`이고 원문이 기준이다.
- 원문이 이미 한국어(`body_language`가 `ko` 또는 `ko-…`)면 모델을 부르지 않는다. POST는 422 `CS_TRANSLATION_NOT_NEEDED`, 화면은 한국어 탭에도 원문을 보인다.
- 분류·요약과 따로 부른다. 프롬프트는 `translation.py`에 두고 판본은 `v1`(채택 전)이다. 판본이 바뀌면 이전 번역은 오래된 것(`stale`)으로 보이고 다시 만들 수 있다.

## 입력과 출력 검사

- 입력: 분류·요약과 같은 허용 필드·마스킹(이메일 → `[PERSONAL_PROVIDED]`, 라벨 붙은 시리얼 → `[SERIAL_PROVIDED]`)을 거친 `channel`·`body_language`·`subject`·`body`. 입력 판본은 `cs-translation-input-v1`이고, 마스킹된 입력의 SHA-256(`input_sha256`)으로 원문 변경을 알아챈다.
- 출력 `{"subject_ko": "...", "body_ko": "..."}` 하나. 원문에 제목이 없으면 `subject_ko`는 빈 문자열이다. 앞뒤 공백만 떼고 저장한다. 아래를 어기면 저장하지 않고 실패로 기록한다.

| 코드 | 규칙 |
| --- | --- |
| `CS_TRANSLATION_SCHEMA` | JSON 객체 하나, 두 키만, 중복 키 없음. `body_ko`는 공백만은 안 되고 마스킹한 원문 본문 길이의 3배(최소 200자)와 8000자 중 작은 값 이하. `subject_ko`는 500자 이하이고, 원문에 제목이 있으면 비어 있으면 안 되며 없으면 빈 문자열이어야 한다 |
| `CS_TRANSLATION_NOT_KOREAN` | `body_ko`에 한글이 있어야 한다 |
| `CS_TRANSLATION_UNGROUNDED` | 요약의 `CS_SUMMARY_UNGROUNDED`와 같은 검사를 `subject_ko`+`body_ko` 전체와 원문 제목+본문 사이에 건다. 글자와 숫자가 섞인 코드(모델명·시리얼·오류 코드·펌웨어 판본)는 원문에 그대로, 나머지 수(단위·날짜 포함)는 값으로 원문에 있어야 한다. 점으로 이은 수(펌웨어 판본 `4.2.1`, 소수 `35.5`)는 원문에 그대로 있어야 한다(요약 검사만으로는 `4.2.2`가 통과해서 더했다). 가림 표시(`[…_PROVIDED]`)도 원문에 있는 것만 쓸 수 있다. 원문에 글자로 쓴 수(three days)는 한국어 낱말(사흘)로 옮겨야 하며 숫자로 바꾸면 이 코드다. 한계는 요약과 같다(원문 어딘가에 같은 값의 수가 있으면 통과) |
| `CS_TRANSLATION_COMMITMENT` | 승인·확정 약속 문구를 사실처럼 쓴 문장이 없어야 한다(요약과 같은 검사). `…승인되었다고 들었습니다`처럼 고객이 들었다·주장한다는 문장은 된다 |

## API (`/api/v1/cs/tickets/{ticket_id}/translation`)

| 메서드 | 허용 | 동작 |
| --- | --- | --- |
| `GET` | 문의 조회 범위 | 지금 원문·프롬프트 판본에 맞는 성공한 번역, 없으면 가장 최근의 성공한 번역(`translation`, 없으면 null)과 가장 최근 시도(`latest_attempt`, 실패 포함). 모델을 부르지 않는다. 번역 뒤 원문이나 프롬프트 판본이 바뀌었으면 `translation.stale = true` |
| `POST` | cs_agent, 이관받은 부서의 dept_staff(범위 안, 2026-11-04 검토 답변 화면) | 원문이 한국어면 422 `CS_TRANSLATION_NOT_NEEDED`(모델 호출 없음). 같은 원문·프롬프트 판본의 성공한 번역이 있으면 모델을 부르지 않고 그것을 200으로 준다. 없으면 모델을 불러 저장하고 201. 동시 요청 처리(프로세스 안 줄 세우기, 다른 인스턴스 저장 시 고유 색인)는 요약과 같다. 실패하면 시도를 저장하고 502 `CS_TRANSLATION_FAILED`(`details[0].code`에 위 코드 또는 `CS_PROVIDER_TIMEOUT`·`CS_RATE_LIMITED`·`CS_PROVIDER_ERROR`) |

- 순서는 permissions.md 1.3: 범위 밖 문의 404 `CS_TICKET_NOT_FOUND`, 역할 403. 번역할 수 없는 문의(빈 본문·잘못된 언어 표기 등)에 POST하면 422 `CS_TRANSLATION_INPUT_INVALID`이고, GET은 저장본을 `stale = true`로 보여 준다.
- `translation`: `id`·`subject_ko`·`body_ko`·`created_at`(AI 작성 시각)·`requested_model`·`model_version`·`prompt_version`·`input_version`·`stale`.
- 웹 중계 허용표에 `GET`·`POST`(query 없음)로 있다.

## 접수 때 자동 번역

- 새 문의(수동 접수 `POST /api/v1/cs/tickets`, 메일 접수 `POST /internal/v1/cs/tickets/from-mail`)는 접수 응답을 보낸 뒤 배경에서 한 번 번역한다. 자동 배정(assignment.md)처럼 접수의 보조 단계라 실패해도 접수는 201 그대로이고, 시도만 남긴다(로그 `CS_TRANSLATE_ON_INTAKE_FAILED` 또는 저장된 실패 코드). 담당자는 문의 처리 화면에서 다시 번역할 수 있다.
- 번역이 꺼져 있거나 원문이 한국어면 모델을 부르지 않는다. 같은 원문·판본의 성공한 번역이 있으면 다시 만들지 않는다(POST와 같은 줄 세우기·고유 색인).
- `requested_by`는 고정 값 `INTAKE_ACTOR`(`uuid5(NAMESPACE_URL, "team4-cs/translation-on-intake")`)다. 로그인 사용자가 아니다.
- 과거 적재 문의(`scripts/cs_import_provided.py` 등)는 번역하지 않는다. 일괄 번역은 비용 때문에 두지 않았다.
- 비용: 새 문의마다 모델을 한 번 부른다(제목·본문을 한 번에). 로컬 runner는 real 모드·예산(`enable-real`) 안에서만 부른다.

## 목록의 한국어 제목

- 문의 목록(`GET /api/v1/cs/tickets`)의 각 항목과 고객 이력(`GET /api/v1/cs/stats/customers/{id}`)의 `tickets[]`에 `subject_ko`(지금 원문과 같은 입력으로 만든 가장 최근의 성공한 번역 제목, 없으면 null)를 같이 준다. 문의 처리 화면의 `stale`과 같은 기준이라, 접수 뒤 제목·본문을 고치면 옛 한국어 제목은 빠지고 원문 제목이 보인다(다시 번역하면 다시 보인다). 번역 표(C28)가 없는 DB에서는 null이다(`CS_TRANSLATION_TABLE_MISSING` 로그).
- 화면은 한국어 제목을 먼저 보이고 원문 제목을 툴팁("원문: …")으로 남긴다. 검색은 두 제목을 모두 찾는다. 엑셀은 "제목(한국어)"와 "원문 제목"을 따로 낸다.

## 저장 (`cs.ticket_translations`, 추가만)

요약 표(`cs.ticket_summaries`)와 같은 열에서 `summary_ko` 대신 `subject_ko`(0~500자)·`body_ko`(1~8000자)를 둔다. 시도마다 한 행(같은 원문·판본의 `VALID`는 하나만, 고유 색인 `ticket_translations_valid_once`). `VALID`일 때만 두 번역 열이 있다. C/S 실행 계정(`cs_app`)은 SELECT·INSERT만 한다. 원문과 모델 원시 응답은 저장하지 않는다.

## 켜기

- C/S 서비스 `CS_TRANSLATION_ENABLED=true`(기본 false, 꺼져 있으면 두 경로 모두 404이고 화면은 "번역 기능이 이 배포에서 꺼져 있습니다"). 모델 `CS_TRANSLATION_MODEL`(기본 `gemini-3.6-flash`), 출력 상한 `CS_TRANSLATION_MAX_OUTPUT_TOKENS`(기본 16384, thinking 포함, 본문 전체라 요약보다 크게), 프로세스당 동시 호출 `CS_TRANSLATION_MAX_CONCURRENT_CALLS`(기본 4, 번역 전용 스레드 풀). Gemini 키는 서비스의 기존 `GEMINI_API_KEY`·`AI_PROVIDER`를 쓴다.
- 켜기 전에 공용 DB에 C28을 적용한다. 켜면 C/S 서비스가 번역마다 모델을 한 번 부른다(비용).
- 로컬 QA runner는 요약과 같이 CS driver의 인증된 내부 bridge(`/translation` 경로, 출력 상한 16384)를 주입하고 기본 활성이다. 저장본 조회·재사용은 driver 없이 된다.
- 남은 것: 실제 모델로 번역 품질과 검사 통과율 확인(사용자 승인 뒤, 특히 날짜·단위를 현지 표기로 바꾸는 경우의 `UNGROUNDED` 비율), 기획서 반영(팀 논의).
