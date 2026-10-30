# RFP 고객 질문 초안 엔진 (KAN-189)

`backend.engine.rfp.questions.generate_question_drafts(tree, requirements)`는
`ClauseTree`와 `Sequence[ExtractedRequirement]`를 받아 `list[QuestionDraft]`를 반환하는
순수 함수다. DB·모델·worker·HTTP 연결과 고객 발송은 포함하지 않는다.

각 초안은 `kind`, `sources: [{clause_no, quote}]`, `question`, `pre_reply_note`로 구성한다.
`quote`는 조항의 현행 제목·본문·표 칸 또는 그 안의 추출 인용을 그대로 보존한다.
복수 조항 불일치에는 양쪽 출처를 함께 싣는다. 출력은 입력 조항/요구 순서로 결정되며
자리표시자 → 불일치 후보 → 승인 절차 순서다. 동일 출처 쌍의 불일치 질문은 한 번만 낸다.

## 유형과 판정 범위

| kind | 조건 | 회신 전 처리 |
| --- | --- | --- |
| `PLACEHOLDER` | 현행 제목·본문·표 칸에 `<Insert …>`, `TBD`, 빈 대괄호 `[ ]`, 밑줄 3개 이상 | 미정 항목의 확정 값·단위 질문. 회신 전 해당 항목을 적합·부적합 판정에서 제외한다는 메모 |
| `INCONSISTENCY` | 서로 다른 조항의 같은 `equipment_family`·`attribute_key`에 값·형식·연산자·허용오차 차이 | 같은 장비인지와 적용 값·우선 조항 확인. 불일치 후보이며 부적합을 확정하지 않음 |
| `APPROVAL_PROCESS` | `or approved equal`, `with Owner approval`, `subject to Owner approval`이 있지만 승인 절차 또는 제출 자료 지시가 없음 | 빠진 항목만 질문. 대체 허용을 승인 완료로 취급하지 않음 |

삭제 값(`deleted_values`, 제목·표의 삭제 이력 포함)은 읽거나 복원하지 않는다.
현행 값으로 질문하며, 없는 조항·현행 원문에 없는 인용·인용에 없는 값은 비교에서 제외한다.
숫자 일부만 일치하는 오래된 값도 제외한다(현행 125에 대해 이전 값 25를 근거로 삼지 않음).
원문에 값이 없는 문장형 요구는 추측해서 비교하지 않는다.

## 불일치 비교 경계

- `kind=SPEC`만 비교한다. family가 `OTHER`/`None`, attribute_key가 `OTHER`, 값 없음,
  인용 내 자리표시자, 같은 조항의 복수 요구는 비교 대상이 아니다.
- 장비명의 완전 일치를 요구하지 않는다. 같은 family의 형식 명칭 차이도 질문해야 한다.
  장비명에 명시된 `unit A`, `tag …`, `equipment/transformer/switchgear no./id/# …`,
  `SWG-1` 같은 식별자를 양쪽에서 읽을 수 있고 서로 겹치지 않을 때만 다른 장비로 제외한다.
  식별자가 없거나 한쪽에만 있으면 장비 동일성을 질문 안에서 확인한다.
  임의 장비명 동의어·서로 다른 실장비를 의미 모델로 판별하지 않는다.
- `conditions` 이름·값·단위를 정규화한 목록이 같을 때만 비교한다. 조건 순서는 무관하다.
  조건이 다르거나 한쪽에만 있는 경우 정당한 차이일 수 있으므로 불일치로 만들지 않는다.
  조건 이름의 동의어 및 상하위 조건의 논리적 포함 관계는 추론하지 않는다.
- 숫자는 Decimal로 비교하며 천 단위 쉼표·소수·과학 표기, Unicode 호환 문자,
  공백·대소문자·일부 대시 표기의 동등성을 처리한다. 단위는 별도 필드 또는 단순 숫자 뒤 접미사다.
- 지원 환산: V/kV, A/kA, VA/kVA/MVA, W/kW/MW, s/ms, Hz/kHz, m/cm/mm,
  percent/%, °C/degC. 조건 값에도 같은 규칙을 적용한다. 기존 엔진에 재사용할 순수 환산 함수가
  없어 이 모듈의 작은 목록만 사용한다. API 모듈의 비공개 함수를 역참조하지 않는다.
- 미지원 단위·복합 단위·RMS/ASYM 수식어·범위·복합 수치·허용오차는 문자열로 보존한다.
  동등성을 입증하지 못한 차이는 확인 후보다. 연산자가 다르거나 허용오차 표기가 다르면
  충돌을 확정하지 않고 우선 적용 기준을 질문한다. 텍스트의 의미 동등성은 추론하지 않는다.

## 대체 승인 문맥과 한계

대체 허용 조항 자체, 상위 조항, 해당 조항의 하위 조항을 확인한다. 이 문맥에 있는
`resolved=true`, `target_kind=CLAUSE` 참조의 `target_block_id`가 같은 트리의 `block_ids`에
연결되면 그 대상 조항도 포함한다. 상위의 다른 자식·무관한 전역 조항·미해결 참조·외부 문서는
근거로 삼지 않으며 참조의 참조는 확장하지 않는다. 관련 절차가 다른 외부 문서에 있으면
초안을 검토하는 사람이 확인해야 한다.

긍정적인 `Submit … for [Owner] approval`, `Obtain/Request … approval … before/prior/within`,
`approval … before/prior/within`을 절차 근거로 인식한다. 제출 자료는
`Submit/Provide/Attach/Include … datasheets/drawings/test reports/certificates/comparison tables/sheets/technical data`
지시를 인식한다. 절차나 자료 이름의 단순 언급만으로는 충족 처리하지 않는다.

마침표·줄바꿈으로 나눈 문장에서 자리표시자, `not/never/no/without/pending/undetermined/undefined/unspecified`,
`to be defined/provided/confirmed/determined`가 있으면 그 문장의 지시를 확정 근거로 쓰지 않는다.
세미콜론으로 이어진 미정 문구도 같은 문장에 속한다. 이 좁은 영문 규칙은 부정·추후 제공을
긍정 지시로 오인해 질문을 숨기는 것을 막으며, `not later than` 같은 표현에도 보수적으로 질문이 남을 수 있다.
임의 언어·복잡한 부정 범위·승인 절차의 충분성·실제 승인 여부를 판정하는 기능은 아니다.

## 검증과 연결

`tests/tft/test_rfp_question_drafts.py`는 일반 합성 문장으로 현행/삭제 구분, 자리표시자,
장비 형식 차이, 장비 식별자, 조건·단위 동등성, 오래된 추출 결과 배제, 승인 문맥 및
부정/미정 지시를 검증한다. 실제 문서·정답·외부 API는 사용하지 않는다.
기존 `scripts/check_backend.py tft`는 `tests/tft`를 재귀 수집하므로 새 테스트도 CI 대상이다.
이 계약은 엔진 반환 형식이며 API 스키마·상태 전이·후속 판정 엔진은 변경하지 않는다.
