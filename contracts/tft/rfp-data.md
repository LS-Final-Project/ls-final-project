# RFP 자사 대응 데이터 구조와 API 골격 (KAN-182, A01)

원본 합의: [RFP 자사 대응·협력사 보완 API 명세 v1.1](https://docs.google.com/document/d/1lh6blP9IqACKvr3nyPp7NuU07jOmliIvIH_h87HDu9g)(2026-10-28, 기획서 최종본 수정본 4·6장). 이 문서는 그 명세의 §2.1 상태 값, §4.1 프로젝트 확장, §6 저장 구조를 코드로 옮긴 결과와 설계 이유다.

| 원본 | 파일 |
| --- | --- |
| 상태 값·HTTP 모델(OpenAPI 초안의 원본) | `backend/api/tft/rfp/schemas.py` |
| 경로 골격 5개(구현 전 501 `TFT_NOT_IMPLEMENTED`, OpenAPI `x-implementation-status: planned`). 15·16·18·19·20은 구현(KAN-189), 11·30 접수는 [작업 계약](rfp-jobs.md#tft-접수-경로-1130-kan-199kan-188) | `backend/api/tft/rfp/routes.py`, `routes_supplement.py`, `routes_questions.py`, `jobs.py` |
| 저장 구조 | `infra/migrations/tft/20261029_KAN-182_rfp_response.sql` |
| 생성 OpenAPI | `docs/openapi/tft.json` |

**구현 범위 구분**: 이 PR은 데이터 구조·상태 값·경로 선언까지다. 경로 동작은 KAN-188(A07: 1~19·25·28·29)·KAN-189(A08: 20~24·26·27)·KAN-192(A12: 30)가 구현한다. 1·3번(목록·상세)은 기존 `projects/routes.py`에 있고 응답 확장(`RfpProjectSummary`·`RfpProjectDetail`)은 A07이 바꾼다.

## 왜 점수 하나 대신 네 가지 상태인가

요구 하나의 판단을 0~100 같은 적합도 점수 하나로 두지 않고 네 가지 상태로 둔다.

| 상태 | 뜻 | 다음 행동 |
| --- | --- | --- |
| `EVIDENCE_FOUND` 자사 대응 근거 있음 | 같은 속성·단위·조건으로 비교한 자사 근거가 있음 | 초안에 근거와 함께 쓴다 |
| `CONDITIONAL` 조건부 대응 검토 | 조건(온도 상승, 2차 전압 등)이 맞을 때만 대응 | 조건을 확인하거나 질문한다 |
| `SPEC_GAP` 사양 차이 확인 | 자사 근거가 요구와 다름(예: 15kV 요구에 12kV) | 보완 경로를 고른다 |
| `INSUFFICIENT_EVIDENCE` 판단 근거 부족 | 자료가 없거나 비교할 수 없음 | 자료를 요청하거나 질문한다 |

1. **평균이 필수 미충족을 숨긴다.** 필수 요구 하나가 사양 차이인데 나머지가 다 맞으면 점수는 높게 나온다. 그 하나가 입찰 탈락 사유다. 상태로 두면 미해결 필수 요구 수(`unresolved_mandatory_count`)가 따로 보인다.
2. **상태마다 다음 행동이 다르다.** 근거 부족이면 자료 요청, 조건부면 조건 확인·질문, 사양 차이면 보완 경로다. 점수 72와 68은 무엇을 해야 하는지 알려 주지 않는다.
3. **"자료 없음"과 "안 맞음"을 섞지 않는다.** 자료가 없는 것은 판단 근거 부족이지 미충족이 아니다. 점수로는 0점(근거 없음)과 0점(불충족)이 같아진다.
4. **사람이 확인·뒤집을 수 있다.** 상태와 사유(`reason`)·미확인 사항(`unresolved`)은 담당자가 읽고 변경 제안으로 바꿀 수 있다. 점수는 근거를 거슬러 설명하기 어렵다.

협력사 후보도 같은 원칙이다. 후보 × 요구마다 등급(`MANDATORY_CONFIRMED`·`REVIEW`·`REFERENCE_ONLY`·`EXCLUDED`)을 계산하고, 필수 제약을 어긴 후보는 점수와 무관하게 `EXCLUDED`다. 유사도 점수는 응답에 내지 않는다.

## 상태 값 (명세 §2.1)

| 구분 | 값 |
| --- | --- |
| 산출물 상태 `output_status` | `INTAKE`·`ANALYZING`·`REVIEW_DRAFT` (입찰 통과·수주와 별개) |
| 판단 상태 `judgement_status` | `NEEDS_REVIEW`·`CONDITION_CHECK`·`OWNER_CONFIRMED` (산출물 상태와 서로 대체하지 않음) |
| 요구 상태 `status` | `EVIDENCE_FOUND`·`CONDITIONAL`·`SPEC_GAP`·`INSUFFICIENT_EVIDENCE` |
| 요구 성격 `necessity` | `MANDATORY`·`PREFERRED`·`CONDITION_BOUND`·`UNDETERMINED` (요구 상태 `CONDITIONAL`과 다른 단어) |
| 보완 경로 `path` | `OTHER_OWN_PRODUCT`·`OWN_DEVELOPMENT`(자사 개발·수행 확인, R&D)·`OWN_TERMS_REVIEW`(자사 조건 변경 검토, 명세 추가 요청 중)·`ALTERNATIVE_SPEC`·`EXTERNAL_PRODUCT`·`TASK_SUBCONTRACT`·`JOINT_PROPOSAL`·`CUSTOMER_SPECIFIED_PROCUREMENT`(고객 지정품 조달)·`NONE` (명세 v1.3, 마이그레이션 T9) |
| 다음 담당 `next_owner_field` | `SALES`·`TECH`·`PURCHASE`·`CUSTOMER`·`RND`(R&D 자문 질문이 열린 요구, 명세 v1.3) |
| R&D 확인 `rnd_check`(요구 판본) | `NOT_REQUIRED`(기본)·`PENDING`·`CONFIRMED_FEASIBLE`·`CONFIRMED_NOT_FEASIBLE`·`CONFIRMED_SCHEDULE_MISMATCH`·`WAIVED` (마이그레이션 T10) |
| 질문 대상 `target` / `target_dept` | `CUSTOMER`(기본, v1.2 동작)·`INTERNAL`(부서 필수). 내부 자문 유형 `RND_FEASIBILITY`·`TECH_ADVICE`·`PURCHASE_TERMS`는 `INTERNAL`에만 |
| R&D 답변 | `rnd_feasibility` `FEASIBLE`·`FEASIBLE_WITH_SCHEDULE`·`NOT_FEASIBLE`·`UNDETERMINED`, `earliest_available_on`, `schedule_fits`(개발 가능성 없이 일정 칸만은 저장하지 않음) |
| 면제 사유 `rnd_check_waiver_reason`(보완 선택) | 20~1000자. 열린 R&D 질문이 있는 협력사 경로 선택에만 |
| 부서 분류 `tft.department_kinds` | `SALES`·`RND`·`TECH`·`PURCHASE`·`OTHER`. tft_app은 읽기만 |
| 전략 고객 `projects.strategic_customer`(+`_note`·`_set_by`·`_set_at`) | true·false·null(미입력). 영업 입력 표시값(가설)이고 상태·등급·경로 순위·검토본 조건·평가 입력에 쓰지 않는다. 값을 넣으면 넣은 사람·시각이 함께 있다(T11) |
| 통합 책임 `projects.integration_holder`(+`_candidates`·`_note`·`_set_by`·`_set_at`) | `OWN`·`SINGLE_PARTNER`·`JOINT`·`UNRESOLVED`(기본). 미확보가 아니면 정한 사람·시각이 있다. 필요 여부·근거 조항은 서버 계산([통합 책임](rfp-integration.md)) |
| 지정품 `requirement_versions.designated_manufacturer`·`designated_procurement`·`designated_clause_id` | 처리 방식 `SPECIFIED_PROCUREMENT`·`ALTERNATIVE_PROPOSAL`·`UNDECIDED`. `designated_model`(T7)이 있을 때만 값이 있다 |
| 선택의 R&D 확인 `supplement_selections.rnd_check` | 6값. 면제 사유는 `WAIVED`일 때만 |
| 감사 기록(앱 규칙, A07·A08) | 전략 고객·통합 책임 변경, INTERNAL 질문 생성·답변을 `audit_events`에 from/to로 남긴다 |
| 부족 원인·고객 허용 | `RATING`·`CERTIFICATION`·`FORM`·`TASK`·`DESIGNATED_MODEL`(타사 지정 모델)·`COMMERCIAL`(상업 조건)·`CORE_EQUIPMENT_MISSING`(핵심 기기 부재) / `ALLOWED`·`FORBIDDEN`·`UNSTATED` |
| 후보 등급 `grade` | `MANDATORY_CONFIRMED`·`REVIEW`·`REFERENCE_ONLY`·`EXCLUDED` (KAN-187 `grading.Grade` 재사용) |
| 고객 질문 `status` | `OPEN`·`REPLIED` |
| 변경 제안 `origin`/`status` | `web`·`customer_reply`·`pdf_annot` / `PENDING`·`APPROVED`·`REJECTED` |
| 작업 `kind`/`step` | `rfp.process`·`rfp.export`·`rfp.annotate` / `parse`…`annotate` (플랫폼 `runtime.jobs`, owner=`tft`) |
| 출처 | `data_origin`(`synthetic`·`demo`·`real`, 기본값 없이 필수), `derivation_kind`(`PROVIDED`·`DERIVED`·`SYSTEM`·`ASSUMED`), `provenance_kind`(`ai_draft`·`human_input`·`system_record`) |

## ERD

```mermaid
erDiagram
    projects ||--o{ project_assignments : "담당·책임자 이력"
    projects ||--o{ documents : ""
    documents ||--o{ document_versions : "판본(추가만)"
    document_versions ||--o{ clauses : "조항 트리"
    clauses ||--o{ clause_deleted_values : "취소선 값"
    clauses ||--o{ clause_flags : ""
    projects ||--o{ requirements : ""
    requirements ||--o{ requirement_versions : "판본(판단 포함)"
    requirement_versions }o--|| document_versions : "원문"
    requirement_versions }o--o| clauses : "조항"
    requirement_versions ||--o{ requirement_conditions : ""
    requirement_versions ||--o{ product_comparisons : "자사 비교"
    product_comparisons ||--o{ comparison_evidence : "근거"
    requirement_versions ||--o{ supplement_options : "보완 선택지"
    requirements ||--o{ supplement_selections : "사람 선택(판본)"
    partner_profiles ||--o{ partner_grades : "후보 × 요구"
    requirement_versions ||--o{ partner_grades : ""
    partner_profiles ||--o{ partner_grade_summaries : "후보 요약"
    projects ||--o{ customer_questions : ""
    customer_questions ||--o| question_replies : "회신"
    projects ||--o{ change_proposals : "승인 대기"
    requirements ||--o{ change_proposals : ""
    projects ||--o| drafts : ""
    drafts ||--o{ draft_versions : "판본"
    draft_versions ||--o{ draft_sentences : ""
    draft_sentences ||--o{ draft_sentence_evidence : "근거 링크"
    draft_versions ||--o{ review_versions : "검토본"
    review_versions ||--o{ exports : "내보내기"
    change_proposals ||--o{ rereview_marks : "승인 → 재검토 표시"
    projects ||--o{ rfp_commands : "멱등 기록"
    projects ||--o{ rfp_audit_events : "감사(제안자·승인자)"
```

## 저장 구조 (명세 §6)

| 묶음 | 표(`tft` 스키마) | 판본 | `tft_app`이 바꿀 수 있는 열 |
| --- | --- | --- | --- |
| 프로젝트 | `projects`(열 추가: `output_status`·`judgement_status`·`purpose`·`customer_goal`·`review_departments`·`review_version`), `project_assignments` | 프로젝트 `version` | 기존 KAN-119 권한 그대로(표 전체 UPDATE). 상태 두 개는 서버 규칙으로만 바꾼다(앱이 지킴, 권한 축소는 Z 단계) |
| 문서 | `documents`, `document_versions`, `clauses`, `clause_deleted_values`, `clause_flags` | 문서 판본 | `documents.latest_version` |
| 요구 | `requirements`, `requirement_versions`(원문 위치·내용·판단), `requirement_conditions` | 요구 판본 | `requirements.latest_version` |
| 자사 비교 | `product_comparisons`, `comparison_evidence` | 요구 판본에 종속 | 없음 |
| 보완 경로 | `supplement_options`(계산), `supplement_selections`(사람 선택) | 선택 판본 | 없음 |
| 협력사 | `partner_profiles`(KAN-184 `profiles.json` 키, `synthetic`·`PROVIDED` 고정), `partner_grades`, `partner_grade_summaries`(KAN-187 출력, `DERIVED`) | 프로필 판본 | 없음 |
| 고객 질문 | `customer_questions`, `question_replies` | 질문 판본 | `status`·`version`·`updated_at` |
| 변경 제안 | `change_proposals` | 제안 판본 | `status`·`decided_by`·`decided_at`·`decision_note`·`version` |
| 초안·검토본 | `drafts`, `draft_versions`, `draft_sentences`, `draft_sentence_evidence`, `review_versions`, `exports` | 초안 판본 | `drafts.latest_version`, `exports`의 `job_id`(접수 뒤)·파일 열(완료 기록) |
| 재검토 표시 | `rereview_marks`(제안 승인 때 연결 요구·비교·보완·초안 문장마다 한 행) | — | `cleared_by`·`cleared_at`(사람이 다시 확인) |
| 멱등 기록 | `rfp_commands`(사용자·동작·`Idempotency-Key`마다 첫 결과) | — | 없음 |
| 감사 | `rfp_audit_events`(HTTP 쓰기). `commands.remember`가 멱등 쓰기·처리 접수를 기록하고(`proposal.approve`는 제안자·승인자와 함께 경로가 기록), 멱등 키가 없는 4·5와 쓰기의 부수 행(회신 제안, R&D 자동 질문)은 `commands.audit`을 직접 부른다. 재전송은 다시 기록하지 않는다. 멱등 쓰기의 detail은 쓰기 뒤 상태(`to`)만, 4·5는 `from`·`to`다. worker가 쓰는 행은 접수 감사(`job`)로 대신한다 | — | 없음 |

- **읽기 경로(KAN-188, `rfp/routes_read.py`)**: 7~10·12~14·17이다. 범위 밖은 없는 것과 같이 404다(`TFT_PROJECT_NOT_FOUND`·`TFT_DOCUMENT_NOT_FOUND`·`TFT_REQUIREMENT_NOT_FOUND`).
  - 문서 경로 8~10은 문서의 최신 판본 기준이다. 목록(7)은 판본 전부다.
  - 조항(9)은 `parent`가 없으면 전체 조항을 문서 순서(`ordinal`)로 낸다. 화면이 `parent_clause_id`로 트리를 만든다.
  - 원문(10)은 항상 `attachment`·`nosniff`이고, 저장 해시·크기가 맞을 때만 낸다. 저장소(`TFT_DOCUMENT_BUCKET` 또는 `TFT_DOCUMENT_ROOT`)가 없거나 읽지 못하면 503 `TFT_DOCUMENT_UNAVAILABLE`이다.
  - 요구 목록(12)의 필터는 모두 SQL에서 걸어 쪽이 어긋나지 않는다. `open_questions`는 `OPEN`만 센다(`DRAFT` 제외).
  - `tolerance`(원문 표기, `spec`)와 `needs_review`(시스템 표시, 목록·상세 최상위)는 T15 칸이다. `tolerance`의 `null`은 '오차 없음'이 아니라 '없거나 모름'이다(T15 전 판본은 재처리 전까지 `null`·`false`). `attribute_key`·`equipment_family`는 저장만 하고 응답에 내지 않는다.
  - `links.comparison_summary`는 후보 수와 결과 요약, `links.supplement_summary`는 최신 선택의 경로 코드다. 화면 표시용 자유 문자열이다.
  - 비교(14)는 비교가 저장된 가장 최근 요구 판본을 내고 그 판본을 `requirement_version`에 적는다. 후보가 없으면 저장 행이 없으므로 저장된 `equipment_family`로 `search_trace.filters`를 만든다(`family=…`, 모르면 `family=NONE`).
  - 협력사 후보(17)는 저장된 등급 행이 있는 후보만 낸다. `overall_grade`는 `grading.overall_grade`로 저장 등급 전체에서 계산하고(`partner_grade_summaries`는 쓰지 않음), `requirement_id` 필터는 `grades`만 좁힌다. `conditions_to_confirm`은 `mandatory_check.unknown` 합집합, `on_customer_manufacturer_list`는 제조사 목록 입력 전이라 `null`이다.
- **쓰기 경로(KAN-188, `rfp/routes_write.py`)**: 2·4·5·6이다. 응답은 `RfpProjectDetail`이다.
  - 2 접수: `sales`만. 호출자가 담당이 되고 책임자는 자동 지정하지 않는다. `review_departments`는 `department_kinds`에 있어야 한다.
  - 4 수정: 담당 `sales`·책임자만. 본문에 있는 칸만 바꾼다. `name`·`review_departments`·`integration_responsibility`의 `null`은 422, `strategic_customer: null`은 입력자·메모와 함께 지운다.
  - 5 지정: 관리 대상 `manager`만(범위 밖 404, 볼 수만 있으면 403). 호출마다 `project_assignments` 한 행이다. 다른 manager의 관리 범위는 tft가 읽지 못해 책임자 후보는 새 담당 `sales`·기존 책임자·호출한 manager다. `sales_user_id`는 검증하지 않는다: identity를 읽지 못하므로 없는 사용자나 `sales` 역할이 없는 ID도 들어간다.
  - 6 업로드: 담당 `sales`·책임자만. 25 MiB 초과 413 `TFT_DOCUMENT_TOO_LARGE`(Content-Length가 없어도 받는 동안 멈춘다). 형식은 내용으로 판별한다(PDF는 `%PDF-`, DOCX는 zip 안 `word/document.xml`). 다음 판본은 같은 프로젝트·같은 `kind`만 되고 제목·메모는 첫 판본 것을 유지한다. 저장소가 없으면 503 `TFT_DOCUMENT_UNAVAILABLE`이다.
  - 원문은 `rfp/{project_id}/{document_version_id}`에 쓴 뒤 DB를 커밋한다. DB 실패 시 객체가 남을 수 있다(실행 계정에 삭제 권한이 없다).
- **변경 제안(KAN-189, `rfp/routes_proposals.py`)**: 21~24다. 작성은 담당 `sales`·책임자·검토 부서 `dept_staff`, 승인·기각은 담당 `sales`·책임자다. 범위 밖은 404 `TFT_PROPOSAL_NOT_FOUND`다.
  - 22는 값이 온 칸만 바꾸고 나머지는 지금 판단을 이어 `proposed`에 전체를 저장한다. 바뀌는 칸이 없으면 422다.
  - 23은 제안의 `base_requirement_version`이 최신일 때만 새 판본(`human_input`, `proposal_id`)을 쓴다. 다르면 409 `TFT_VERSION_CONFLICT`로, 다시 제안해야 한다(기각은 된다). 요구 없는 메모 제안의 승인은 422, 이미 결정한 제안은 409 `TFT_PROPOSAL_DECIDED`다.
  - 새 판본은 판단 칸만 바꾸고 나머지 칸은 잇는다. 비교·보완 선택지는 새로 만들지 않고 그 요구의 가장 최근 저장 판본을 읽는다.
  - 승인은 요구·비교·보완·최신 초안 문장마다 `rereview_marks` 한 행을 남긴다(승인 결과가 `EVIDENCE_FOUND`면 요구 표시는 남기지 않는다: 사람이 근거를 확인한 승인 자체가 재확인이고 고를 보완 경로도 없다. 비교·보완·초안 문장 표시는 남긴다). 또한 감사에 제안자·승인자를 함께 적는다.
  - **재검토 해소**: 해소 경로는 따로 없다. 같은 요구의 보완 선택을 다시 저장(경로 16)하면 사람이 다시 확인한 것으로 보고 요구·비교·보완 표시를 해소한다. 초안 문장 표시는 지우지 않는다. 초안 문장은 사람이 고쳐 저장(`PATCH draft`)하면 그 문장만 풀린다(명세 결정 10). 근거 요구에 재검토가 남아 있으면 응답의 `needs_rereview`는 그대로다. **한계**: `EVIDENCE_FOUND`가 아닌데 보완 경로를 고를 수 없는 요구는 표시를 해소할 수 없어 `needs_rereview`와 미해결 필수 집계에 남는다.
- **경로 1·3(KAN-188)**: 명세 칸을 더했다. 목록은 `output_status`·`judgement_status`·`q`(이름·고객명 부분 일치, 대소문자 무시, 2~100자, `%`·`_`는 글자 그대로) 필터와 상태 2개·건수 4개(`requirements_count`·`unresolved_mandatory_count`·`pending_proposals_count`·`open_questions_count`)를 준다. 건수는 `repository.PROJECT_COUNTS` 한 문장으로 계산하고 목록과 상세가 같이 쓴다. `open_questions_count`는 프로젝트의 `OPEN` 질문 전부다(요구 없는 질문 포함, `DRAFT` 제외). 옛 칸(`status`·`external_id`·`rfp_requirements`)은 화면이 바뀔 때까지 남긴다. 상세와 쓰기 응답의 `latest_job`은 최신 처리 접수(`rfp_commands`의 `project.process`)의 `job_id`만 있고 `status`·`step`은 null이다(`JobRef.status`는 null 허용, 목록에는 이 칸이 없다). 상태는 플랫폼 소유라 화면이 작업 상태 경로로 읽는다. 그 경로는 요청한 사용자에게만 열린다. `output_status`는 플랫폼에 접수를 보내기 전에 `ANALYZING`이 되고, 접수가 안 되면 이전 값으로 돌아간다(worker가 응답보다 먼저 끝나도 덮어쓰지 않게). 성공하면 검토본 저장 전까지 그대로이고 `REVIEW_DRAFT`는 검토본 저장이 정한다. worker가 실패·취소로 끝내면 `INTAKE`로 돌린다(저장된 검토본이 있으면 `REVIEW_DRAFT`). **한계**: 플랫폼이 `JOB_NOT_STARTED`로 닫은 작업은 tft를 건드릴 수 없어 `ANALYZING`이 남고, 다음 접수가 다시 `ANALYZING`으로 만든다. 첫 접수 둘이 동시에 들어오면 거절된 쪽이 표시를 이전 값으로 되돌려, 작업이 도는 동안 값이 틀릴 수 있다.
- **지정품 상세(KAN-199 댓글 10926)**: 처리 함수가 지정 모델이 있는 요구에 `designated_clause_id`(요구의 조항)와 `designated_procurement`(시작값 `SPECIFIED_PROCUREMENT`)를 저장한다. 이 칸이 비어 있는 이전 행은 상세 응답에서 요구 조항과 기본값으로 낸다.
- **웹 중계(KAN-188)**: 구현한 RFP 경로는 `backend/api/platform/relay.py`·`frontend/server.mjs` 허용표와 공유 픽스처 `docs/openapi/business-relay-routes.json`에 함께 등록한다(셋이 다르면 검사가 실패한다). 파일이 지나가는 두 경로만 JSON·1 MiB 규칙의 예외다: 업로드(6)는 `multipart/form-data` 요청 26 MiB, 원문(10)은 PDF·DOCX 응답 26 MiB이고 `attachment`·`nosniff`를 중계가 강제한다. 세션·CSRF·Origin 검사와 사용자 헤더 재발급은 같고, 원문 범위(manager는 관리 대상 프로젝트만)는 tft가 판단한다. 두 층이 파일을 메모리에 모으므로 동시 업로드 수만큼 메모리를 쓴다. 업로드 외 쓰기 요청은 `application/json`만 받으므로 본문이 없는 경로 31도 화면은 `{}`를 보낸다. 처리 작업 `status_url`(`/api/v1/platform/jobs/{id}`, `/events`)은 플랫폼 자체 경로라 허용표가 아니라 웹의 작업 상태 중계로 열리고(업무 중계를 켜면 함께 열림), 접근은 플랫폼이 요청 사용자 본인으로 검사한다.
- **질문 초안(KAN-199)**: 처리 함수가 `generate_question_drafts` 결과를 조항·요구와 같은 트랜잭션에서 `customer_questions`에 `DRAFT`로 넣는다(`CUSTOMER`, `DERIVED`, 요청자가 `created_by`). `draft_key`(종류와 조항 번호·원문의 sha256)로 프로젝트 안 같은 질문을 한 번만 만든다. 조항 ID는 판본마다 달라 키에 쓰지 않고, 미정 항목 문구는 조항과 무관해 문구도 쓰지 않는다. 종류 매핑은 `PLACEHOLDER`→`EMPTY_VALUE`, `INCONSISTENCY`→`CONFLICT`, `APPROVAL_PROCESS`→`CONDITION_MISSING`이고, 회신 전 메모는 `internal_assumption.value`, 출처 조항은 `basis_clause`다. `requirement_id`는 같은 조항에서 인용이 출처 원문과 같거나 서로 포함되는 요구가 정확히 하나일 때만 연결하고, 없거나 갈리면 NULL이다. `DRAFT`는 보낸 질문이 아니다: 목록에 상태로 구분되고, `has_customer_question`에서 빠지며, 회신은 409다. 사람이 경로 31(`POST .../questions/{id}/confirm`)로 `OPEN`으로 바꾼다. `OPEN`·`REPLIED`에 부르면 409 `TFT_VERSION_CONFLICT`이고 같은 멱등 키 재전송은 첫 결과를 준다.
- **협력사 프로필 시드(KAN-184)**: `profiles.json`이 원천이고 `scripts/rfp_seed_partner_profiles.py`가 `tft.partner_profiles`로 옮긴다(로컬 DB·`tft_app`만). 기본은 파일과 DB 대조(`SAME`·`MISSING`·`DIFFERENT`, 어긋나면 종료 1)이고, `--execute`는 없는 판본만 넣는다(`ON CONFLICT DO NOTHING`). 같은 판본의 내용을 바꿨으면 덮어쓰지 않으니 `profile_version`을 올린다. `known_at`은 그날 00:00 UTC다. 행이 없으면 RFP 처리 함수의 등급 단계는 건너뛴다.
- **모든 업무 행**에 `version`·`data_origin`·`derivation_kind`·`known_at`을 둔다. 예외는 업무 판단이 아니라 사람 행동·시스템 기록이거나 부모 판본에 딸린 행이며, 출처는 부모 또는 행위자·시각 열로 대신한다.
  - 부모를 따르는 세부 행: `clause_deleted_values`·`clause_flags`(조항), `comparison_evidence`(비교), `draft_sentences`·`draft_sentence_evidence`(초안 판본), `requirement_conditions`(요구 판본)
  - 사람 행동 기록(행위자·시각 열): `project_assignments`, `review_versions`, `exports`, `rereview_marks`
  - 시스템 기록: `rfp_commands`(멱등), `rfp_audit_events`(감사)
- **추가만**: 위 표에 `tft_app`은 SELECT·INSERT만 가지고 UPDATE·DELETE·TRUNCATE는 없다. 최신 판본 포인터와 상태 열만 열 단위 UPDATE를 연다. 판본을 고치지 않고 새 판본을 쓴다(명세 §2.3).
- **재검토 표시는 판본과 따로 둔다.** 판본 행은 고치지 않으므로 `needs_rereview` 열을 판본 표에 두지 않는다. 응답의 `needs_rereview`는 대상에 해소 안 된 `rereview_marks` 행이 있는지로 계산한다.
  - 요구의 `needs_rereview`는 위 조건에 하나를 OR로 더한다(KAN-189): 그 요구의 INTERNAL R&D 자문 답변(`issue_type=RND_FEASIBILITY`) 기록 시각이 최신 보완 선택(`supplement_selections.decided_at`)보다 뒤면 참이다. 보완 선택이 없으면 이 조건은 보지 않고, 새 선택이 생기면 꺼진다. R&D 답변은 변경 제안을 만들지 않으므로(명세 v1.3 §5.3) 제안에 묶인 `rereview_marks` 행을 남길 수 없어 계산으로 둔다. 구현은 `backend/api/tft/rfp/repository.py`의 `needs_rereview`다.
  - 후속 제안(KAN-182): 답변을 표시 원인으로 저장하려면 `rereview_marks`에 `cause_question_id`(질문 FK, `cause_proposal_id`와 둘 중 하나)를 허용하는 확장이 필요하다. 그때까지는 위 계산이 원천이다.
- **R&D 확인 `rnd_check`는 읽을 때 계산한다(KAN-189).** 원천은 `rnd.rnd_check_of(그 요구의 R&D 자문 질문·답변, WAIVED로 저장된 최신 보완 선택의 결정 시각)`이다(`repository.rnd_check`). 이 값을 바꾸려고 요구 판본을 새로 쓰거나 제자리 갱신하지 않는다. 비교·보완 선택지가 요구 판본에 묶여 있어 판본만 올리면 최신 판본의 비교가 비기 때문이다. `requirement_versions.rnd_check`(T10)는 다른 흐름이 판본을 새로 만들 때만 그 시점 값으로 채운다. 보완 선택 판본의 `rnd_check`는 선택 시점의 값(면제면 `WAIVED`)을 남긴다.
- **DB가 지키는 규칙**: 질문·제안·후보 등급의 요구는 같은 프로젝트 요구만 가리킨다((project_id, requirement_id) 복합 참조), 사람 판단 판본은 판단자·시각 필수, 비교 불가면 결과가 `NOT_COMPARABLE`/`NO_DATA`, 고객이 금지한 보완 경로는 `allowed=false`·사유 필수, 제외 사유가 있으면 등급 `EXCLUDED`, 조항에 못 맞춘 제안은 요구 없이 `clause_unresolved=true`, 사용자 제안은 제안자 필수, 기각은 사유 필수.
- **판본 유일 키**(`20261030_KAN-182_rfp_keys.sql`): 비교·보완 선택지·후보 등급·후보 요약은 같은 대상의 같은 `version`을 한 번만 저장한다. 다시 계산할 때는 version을 늘린다. 재검토 표시의 원인 제안은 같은 프로젝트 제안만 가리킨다.
- **앱이 검사하는 프로젝트 소속**: 아래 참조는 DB가 프로젝트 일치를 보증하지 않는다. 경로 구현(KAN-188·189)이 쓰기 전에 같은 프로젝트인지 확인하고, 다르면 404로 처리한다.
  - `requirement_versions.document_version_id`·`clause_id`
  - `customer_questions.clause_id`
  - `supplement_options.permission_clause_id`
  - `rereview_marks.target_id`
  - 근거 링크(`comparison_evidence`·`draft_sentence_evidence`)
  - 복합 참조로 바꾸려면 `document_versions`·`clauses`에 `project_id`를 복제해야 해서 두지 않았다.
- **손대지 않은 것**: 기존 `projects.status`(옛 TFT 단계)와 CHECK, KAN-119의 인력 구성 표. 기존 `rfp_requirements`는 현재 상세 응답이 읽으므로 그대로 두고, 새 요구 표로 옮기는 일은 경로 구현(A07) 때 읽기 전환과 함께 한다.
- 작업 저장은 플랫폼 `runtime.jobs`를 owner=`tft`로 쓴다(명세 결정 4). 작업 종류 등록 마이그레이션과 내부 접수 엔드포인트는 플랫폼 쪽 후속 PR이다.

## 인증

모든 RFP 경로는 라우터 단위 의존성(`rfp_principal`)으로 사용자 맥락(없으면 401)과 TFT 역할(cs_agent는 403)을 먼저 검사한다. 구현 전 501보다 앞선다. 프로젝트 범위(404)는 경로마다 구현 티켓이 검사한다.

## 판본·동시 수정

문서의 새 판본은 업로드 입력 `supersedes_document_id`로 기존 문서를 지정해 올린다. 별도 대체 관계 열을 두지 않고 같은 `document_id` 아래 `document_versions.version`이 하나 늘어난 행으로 저장한다(다른 프로젝트 문서는 404). 원문 판본이 바뀐 요구는 새 문서 판본을 가리키는 새 요구 판본으로 쌓인다.

쓰기 요청은 읽은 `version`을 본문에 보내고, 최신과 다르면 409 `TFT_VERSION_CONFLICT`다([API 계약](../../docs/api-contract.md#오류와-동시-수정)). 상태를 만드는 POST 12개는 `Idempotency-Key`(8~128자)가 필수이고 OpenAPI에 헤더로 선언돼 있다. 같은 사용자·동작·키의 재전송은 `rfp_commands`에 저장한 첫 결과를 돌려주고, 본문 hash가 다르면 409 `IDEMPOTENCY_CONFLICT`다. 요구의 상태·성격·조건·사유는 변경 제안 승인으로만 새 판본이 된다(승인 전 반영 금지). 예외는 사람의 결정 자체인 보완 선택과 초안 문장 수정이다.

## 경로 15·16·18·19·20 동작 (KAN-189)

- **범위**: 프로젝트를 볼 수 없으면(`can_view_rfp`: 담당·관리 범위 또는 검토 부서 `dept_staff`) 없는 것과 같은 404(`TFT_PROJECT_NOT_FOUND`·`TFT_REQUIREMENT_NOT_FOUND`·`TFT_QUESTION_NOT_FOUND`)다. 볼 수는 있지만 쓸 수 없으면 403이다. 질문 작성은 담당 sales·책임자·검토 부서 `dept_staff`, 보완 선택은 담당 sales·책임자, 회신은 CUSTOMER면 담당 sales이고 INTERNAL이면 `target_dept` `dept_staff`·담당 sales·책임자다(`projects/access.py`).
- **멱등**: 쓰기 셋(`question.create`·`question.reply`·`supplement.select`)은 `commands.run_command`가 한 트랜잭션에서 기록 재생 → 처리 → 결과 기록을 한다. 판본 409 같은 상태 검사는 처리 안쪽에 있어 재전송은 상태가 바뀐 뒤에도 첫 결과를 받는다. 같은 키가 동시에 처리돼 결과 기록이 기본 키에 걸리면 409 `IDEMPOTENCY_CONFLICT`다(트랜잭션이 중단돼 다시 읽을 수 없다. 다시 보내면 먼저 끝난 결과를 재생한다). 늦은 쪽이 기록 전에 같은 질문 행 잠금을 기다렸다면(20 회신) 먼저 끝난 쪽이 바꾼 판본을 보고 409 `TFT_VERSION_CONFLICT`가 날 수 있다. 어느 409든 데이터는 롤백되고, 같은 키로 다시 보내면 첫 결과를 재생한다.
- **15 선택지**: 최신 요구 판본의 `supplement_options` 행에서 부족 원인·고객 허용·허용 조항·경로별 후보만 읽는다. 순위·게이트·차단은 저장된 `allowed`·순위를 쓰지 않고 `supplement.options_for`(판단 상태·원인, 허용, `designated_model` 유무, 지금의 `rnd_check`, 그 요구의 고객 질문 유무)로 다시 계산한다. 그래서 R&D 답이 오면 협력사 경로 차단이 바로 보인다. 저장 행이 없으면 원인 없음·허용 `UNSTATED`로 계산한다(지정품만 경로가 나온다).
- **15 근거 부족(KAN-188)**: `INSUFFICIENT_EVIDENCE` 요구에 제외되지 않은 등급의 후보가 있으면 `EXTERNAL_PRODUCT`를 낸다(기획서 시연 B-2). 자사 비교 후보가 있는 인증 요구이고 고객 허용이 `FORBIDDEN`이 아니면 `ALTERNATIVE_SPEC`도 낸다(둘 다 순위 3, 이 순서). 판본에 추출 `kind`가 없어 인증 요구는 `operator=TEXT`, `standard_system≠NONE`, `attribute_key`가 없거나 `OTHER`인 요구로 읽는다. 처리는 등급보다 먼저 선택지를 저장하므로 `EXTERNAL_PRODUCT` 후보 코드는 판단 상태와 상관없이(사양 차이 포함) 저장 행이 비었을 때 `partner_grades`에서 읽는다. R&D 게이트와 RFP 금지는 그대로다. 지정품·수행 과업·계약 조건 규칙은 먼저 적용돼 바뀌지 않는다.
- **16 선택**: 읽은 요구 판본이 최신과 다르면 409 `TFT_VERSION_CONFLICT`다. 그 뒤 15와 같은 계산의 선택지로 `rnd.check_selection`을 한다. 면제로 넘긴 선택에만 사유를 남긴다. `OWN_DEVELOPMENT`를 고르면 열린 R&D 질문이 없을 때 `rnd.rnd_question_for`의 내부 질문을 같은 트랜잭션에서 만들고 선택 판본의 `rnd_check`는 `PENDING`이다.
- **19 질문**: INTERNAL의 `target_dept`가 프로젝트 검토 부서 밖이거나 원문 조항이 프로젝트 문서의 조항이 아니면 422 `VALIDATION_ERROR`다.
- **16 후보 검사**: 고른 `candidate_codes`는 그 경로의 저장 후보여야 한다(밖이면 422 `VALIDATION_ERROR`). 이 요구의 최신 저장 등급이 `EXCLUDED`인 후보는 422 `TFT_CANDIDATE_EXCLUDED`다(경로 후보여도 거절). 거절된 코드는 `details`에 적는다.
- **20 회신**: 응답은 회신된 질문(`QuestionReplyResult`)이고, 제안을 만들었을 때만 `created_proposal`이 있다. INTERNAL 회신은 제안을 만들지 않는다. CUSTOMER 회신은 요구가 있는 질문이면 판단 필드(`status`·`necessity`·`reason`·`conditions`·`unresolved`)를 `current`로 하고 `unresolved`에서 질문 본문에 그대로 들어 있는 항목만 뺀 값을 `proposed`로 한다(명세 v1.2 §4.7·§4.8). 빠지는 항목이 없거나 질문에 요구가 없으면 제안을 만들지 않는다. 제안은 `origin=customer_reply`, `proposer_kind=system`(`proposer_user_id` 없음), `source_ref={question_id, reply_id}`, 근거 문장 "회신 내용 미검증(담당자 확인 필요). 고객 회신: " + 회신 앞 200자(짝짓기가 회신 내용을 보지 않으므로 승인자가 확인한다), `PENDING`이다. `internal_assumption`은 가정(ASSUMED)이라 제안에 쓰지 않는다.
  - 후속 제안(KAN-188/182): 질문과 미해결 항목을 잇는 열이 없어 본문 포함으로 짝짓는다. 질문을 만들 때 대상 `unresolved` 항목을 지정하게 하면 이 추정이 필요 없다.
- **`next_owner_field=RND`**: 판본 열이라 쓰려면 판본을 올려야 하므로 `rnd_check`와 같이 저장하지 않는다. 요구의 `rnd_check`가 `PENDING`이면 읽을 때 `RND`로 계산한다(요구 상세 응답을 구현하는 쪽, KAN-188).

## 미해결 필수 요구 집계 (KAN-196, 2026-10-29)

사용자 결정: `CONDITION_BOUND`도 `MANDATORY`와 함께 집계 대상에 포함한다.
최신 요구 판본의 성격이 이 두 값 중 하나이고 아래 조건 중 하나라도 참이면
`unresolved_mandatory_count`에 요구당 1을 더한다.

- `judgement.status != EVIDENCE_FOUND`
- `judgement.unresolved`가 빈 목록이 아님
- 요구의 `needs_rereview == true`

`PREFERRED`·`UNDETERMINED`는 제외하되 `by_necessity`에서 별도로 표시한다.
보완 경로·후보 선택 자체는 해결이 아니다. 조건부 필수의 적용 여부를 자동 추정해
제외하지 않는다. 사람이 요구 성격을 바꿀 때는 기존 변경 제안·승인 절차를 따른다.
조건부 요구라도 근거 있음·미확인 사항 없음·재검토 없음이면 미해결 건수는 0이다.

공용 함수: `backend.api.tft.rfp.counts.count_unresolved_mandatory(requirements)`.
입력은 같은 프로젝트의 **최신 요구 판본 전체**이며 화면의 페이지 결과를 세지 않는다.
중복 requirement_id는 오류로 거부한다. 최신 판본 선택·프로젝트 접근 검사는 호출자가
수행한다. KAN-188 목록·상세와 KAN-189 검토본 저장은 이 함수를 재사용한다.
현재 해당 경로는 골격이므로 이 함수가 HTTP 응답에 연결됐다는 뜻은 아니다.

예: MANDATORY SPEC_GAP 1건 + CONDITION_BOUND INSUFFICIENT_EVIDENCE 1건 +
PREFERRED SPEC_GAP 1건이면 미해결 필수는 2건이다. EVIDENCE_FOUND라도 unresolved와
재검토가 모두 남은 요구는 1건만 센다.

건수 0만으로 `OWNER_CONFIRMED`로 자동 변경하지 않는다. §4.9의 검토본 저장 조건을
통과하고 담당 sales 또는 책임자가 저장한 시점에만 판단 상태를 정한다. 미해결이
남아도 요구별 상태·근거 또는 다음 행동이 갖춰지고 초안 문장의 근거·재검토 조건을
통과하면 검토본 저장은 가능하되 `CONDITION_CHECK`를 유지한다.
