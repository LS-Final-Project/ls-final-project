# C/S 회신 초안 AI 보조 계약 (KAN-251)

원본: `backend/api/cs/reply_assist/routes.py`(경로·저장), `backend/engine/cs/reply_assist.py`(입력·프롬프트·출력 검사), 마이그레이션 `infra/migrations/cs/20261116_KAN-251_reply_draft_assists.sql`(README C34). 구조는 [한국어 번역](translation.md)과 같다. 검사는 `tests/cs/test_reply_assist.py`(출력 검사)와 `tests/cs/test_reply_assist_db.py`(가짜 모델, 실제 PostgreSQL)다.

**상태: API·화면 구현(가짜 모델 검사). 기본 비활성. 실제 모델 품질은 확인 전이다.**

## 무엇을 하나

문의 처리 화면(CS0102) "고객 회신 준비" 칸의 세 보조다. 모두 AI 작성물(`provenance=ai_draft`)이고 **아무것도 보내지 않는다.** 담당자가 회신 상자에 넣고, 고치고, 보내기 확인에서 승인해야 고객에게 나간다(기존 보내기 경로 그대로).

| 종류 | 경로(`/api/v1/cs/tickets/{ticket_id}/reply-draft/…`) | 설정 | 하는 일 |
| --- | --- | --- | --- |
| `DEPT_DRAFT` | `POST dept-answer` `{"dept_result_id"}` | `CS_DEPT_REPLY_DRAFT_ENABLED` | 확인 완료(`ACCEPTED`)한 부서 답변(`result`)과 부서 제안 문안(`reply_content`)으로 고객 언어 회신 초안 하나 |
| (조회) | `GET dept-answer?dept_result_id=` | `CS_DEPT_REPLY_DRAFT_ENABLED` | 그 부서 답변으로 저장된 가장 최근 초안(없으면 `data: null`, 모델 호출 없음). 화면이 다시 열릴 때 쓴다 |
| `TO_KO` | `POST korean` `{"text"}` | `CS_REPLY_TRANSLATION_ENABLED` | 회신 상자의 초안(AI 선택지 CONCISE·DETAILED·INFO_FIRST, 부서 답변 기반 초안, 담당자가 쓰거나 고친 글)의 한국어 번역 |
| `FROM_KO` | `POST korean-edit` `{"text", "edited_ko"}` | `CS_REPLY_TRANSLATION_ENABLED` | 담당자가 고친 한국어를 원래 초안(`text`)에 반영한 고객 언어 글. 같은 문장은 원래 초안 그대로 두고 고친 부분만 바꾸게 지시한다 |

- 허용: `cs_agent`만(고객 회신은 C/S 담당자의 일, 보내기와 같다). 범위 밖 문의는 404, 범위 안 다른 역할(이관받은 부서 담당자 포함)은 403이다(permissions.md 1.3).
- 설정이 꺼져 있으면(기본) 그 경로는 404다. 켜면 C/S 서비스가 모델을 직접 부른다(비용, 단추마다 1회).
- 같은 종류·같은 입력(아래 SHA-256)·같은 프롬프트 판본의 성공한 결과가 있으면 모델을 부르지 않고 돌려준다(`reused=true`). 시도는 실패도 사용량과 함께 `cs.reply_draft_assists`에 더한다(고치거나 지우지 않음).
- `TO_KO`: 고객 언어가 한국어(`ko`, `ko-…`)이거나 글의 절반 이상이 한글이면 모델을 부르지 않고 422 `CS_REPLY_TRANSLATION_NOT_NEEDED`다.
- `FROM_KO`: 같은 `text`의 `TO_KO` 저장본이 기준 번역이다. 없으면 422 `CS_REPLY_TRANSLATION_BASE_MISSING`, `edited_ko`가 기준 번역과 같으면 422 `CS_REPLY_TRANSLATION_UNCHANGED`다(모델 호출 없음).
- `DEPT_DRAFT`: 이 문의의 `ACCEPTED` 부서 답변이 아니면 422 `CS_DEPT_RESULT_NOT_ACCEPTED`다.
- 모델 호출 실패나 검사 위반은 502 `CS_REPLY_ASSIST_FAILED`(`details[0].code`에 아래 코드나 호출 실패 코드)다.

응답 `data`: `ticket_id`, `kind`, `text`, `dept_result_id`(DEPT_DRAFT만), `commitment_warning`, `reused`, `created_at`, `requested_model`, `model_version`, `prompt_version`, `provenance`.

## 입력

- 문의는 분류·번역과 같은 허용 필드·마스킹(`build_input`)을 거친 `channel`·`body_language`·`subject`·`body`다. 부서 답변과 초안·한국어 글도 같은 연락처·시리얼 가림(`mask_contacts_and_serials`)을 거친다. 그래서 결과에 `[PERSONAL_PROVIDED]`·`[SERIAL_PROVIDED]`가 남을 수 있고, 화면이 보내기 전에 실제 값으로 바꾸라고 안내한다.
- 입력 판본 `cs-reply-assist-input-v1`, 프롬프트 판본 `dept-draft-v2`(v2: 고객을 2인칭으로 직접 부른다)·`draft-ko-v1`·`draft-from-ko-v1`(채택 전, prompt_registry 밖). 입력 SHA-256은 종류·입력 판본·마스킹한 입력 전체로 만든다.

## 출력 검사

출력은 `{"text": "..."}` 하나다. 앞뒤 공백만 떼고 저장한다.

| 코드 | 규칙 |
| --- | --- |
| `CS_REPLY_ASSIST_SCHEMA` | JSON 객체 하나, `text` 키만, 공백만은 안 됨. 기준 글(DEPT_DRAFT는 부서 답변+제안 문안, TO_KO는 원래 초안, FROM_KO는 고친 한국어) 길이의 3배(최소 400자)와 8000자 중 작은 값 이하 |
| `CS_REPLY_ASSIST_LANGUAGE` | 한국어로 쓸 출력(TO_KO, 고객 언어가 한국어인 회신)에는 한글이 있어야 하고, 고객 언어가 한국어가 아니면 고객에게 갈 출력(DEPT_DRAFT·FROM_KO)에 한글이 없어야 한다 |
| `CS_REPLY_ASSIST_UNGROUNDED` | [번역](translation.md)의 `CS_TRANSLATION_UNGROUNDED`와 같은 검사. 근거 글은 DEPT_DRAFT가 부서 답변·제안 문안·문의 제목·본문, TO_KO가 원래 초안, FROM_KO가 원래 초안+고친 한국어다. 부서 답변에 없는 판본·기간·수량을 만들면 이 코드다 |
| `CS_BLOCKED_EVIDENCE` | 인용 차단 코드(FLT-042)를 말하면 안 된다 |
| `CS_FORBIDDEN_COMMITMENT` | 분석 초안과 같은 승인·확정 약속 검사(`find_forbidden_commitment`). DEPT_DRAFT는 있으면 거부한다(부서가 승인했어도 AI 초안은 확정형으로 쓰지 않고 담당자가 확정한다). FROM_KO는 고친 한국어에 같은 검사로 잡히는 약속이 있으면 담당자의 결정이라 통과시키고 `commitment_warning=true`로 알린다. 고친 한국어에 없던 약속이 생기면 거부한다. TO_KO는 원문을 그대로 옮기는 읽기 보조라 보지 않는다 |

## 화면

- **부서 답변 기반 초안**: "다음 행동"이 답변이고 확인 완료한 부서 답변이 있을 때 보이는 "확인한 부서 답변" 상자의 "부서 답변으로 AI 회신 초안" 단추. 결과는 그 상자 안에 "부서 답변 기반 AI 초안"으로 보이고, "이 초안 쓰기"를 눌러야 회신 상자에 들어간다. 회신 상자 머리는 "부서 답변 기반 AI 초안"으로 표시한다.
- **한국어 번역·한국어로 고쳐 반영**: 회신 상자 바로 아래 "한국어로 보기". 번역을 연 뒤 "한국어로 고쳐 반영"으로 한국어를 고치고 "고객 언어로 반영"을 누르면 결과를 검토 상자에 보인다. "회신 상자에 넣기"를 눌러야 회신 상자의 글이 바뀌고 보내기 승인은 풀린다. 번역 뒤 회신 상자의 글이 바뀌면 번역은 "초안이 바뀌었습니다"로 표시되고 반영을 막는다.
- 로컬 실행 환경은 CS 실행기 중계로 켠다(`/reply-draft-dept`, `/reply-draft-translation`. 번역과 한국어 수정 반영은 한 공급자라 한 경로다).
- 꺼진 배포(404)는 "이 배포에서 꺼져 있습니다"로 안내하고 단추를 막는다.

## 한계

- 약속 정규식은 영어·한국어 문구만 잡는다. 다른 언어 회신의 약속은 담당자가 한국어 번역으로 확인한다. 분석 초안의 문장 검증(verification) 단계는 거치지 않는다.
- "고친 부분만 바꾼다"는 프롬프트 지시다. 결정론적 비교는 하지 않으므로 담당자가 결과를 읽고 넣는다.
- 부서 답변 기반 초안은 단추를 누를 때만 만든다(확인 완료 때 자동 생성 없음). 화면을 다시 열면 저장본을 조회로 보인다.
