# RFP 제안 검토 초안 엔진 (KAN-188)

`backend.engine.rfp.draft.build_draft`는 공개 업무 스냅샷에서 결정적 템플릿으로
`backend.api.tft.rfp.schemas.Draft`를 반환하는 순수 함수다. 모델·DB·파일·worker·라우트·발송·
검토본 저장·내보내기 연결은 포함하지 않는다. 기존 snapshot/출력 계약을 재사용하기 위해
이 엔진에서 API 스키마 타입을 참조하는 경계는 이번 작업의 승인된 설계다.

## 입력과 책임 경계

키워드 인자:

- `project_id`, `version`, `data_origin`, `known_at`: 호출자가 제공하는 프로젝트·초안 메타데이터.
- `requirements: Sequence[RequirementDetail]`: 최신 요구 전체와 현행 인용·판정·재검토 표시.
- `integration: IntegrationResponsibility`: 공개 API의 서버 계산 근거 조항과 사람의 책임자 지정.
- `comparisons: Sequence[Comparison]`, `options: Sequence[SupplementOptions]`: 요구에 대응하는 자사 비교·보완 선택지.
- `selections: Sequence[SupplementSelected]`, `partners: Sequence[PartnerCandidate]`,
  `questions: Sequence[Question]`: 최신 사람 선택·가상 협력사 후보/요구별 등급·고객/내부 질문.
  이들 목록과 비교·선택지는 생략하면 빈 목록이다.

시계나 난수를 호출하지 않는다. 문장 ID는 프로젝트 ID를 namespace로 하여 초안 판본·절·문장·
근거 참조로 계산하는 UUID5다. 같은 협력사가 복수 보완 경로에 있으면 경로를 문장에 표시하고
서로 다른 ID와 조건을 유지한다. 입력은 변경하지 않는다. 요구/질문/후보는 식별자 순,
선택지는 rank·path 순, 절은 `DraftSection` 순으로 출력한다.

호출자는 접근 권한, 동일 프로젝트 소속, 최신 스냅샷 선택 및 인용이 **현행 원문**임을 검증한다.
이 함수는 원문 전체나 DB를 받지 않으므로 인용의 진위·프로젝트 소속·최신 원격 판본을 다시 조회하지 않는다.
내부의 공개 참조·비교 판본·보완 선택지 판본은 다음과 같이 검증한다.

- 중복 요구 ID, 요구당 중복 비교/선택지/사람 선택, 중복 질문/후보 ID, 중복 경로/요구별 후보 등급을 거부한다.
- 비교 `requirement_version`과 선택지 `version`은 해당 요구 `version`과 같아야 한다.
  비교 행 자체 `version`, 사람 선택 `version`, 질문 `version`은 독립 판본이므로 요구 판본과 비교하지 않는다.
- 비교/선택지/사람 선택/질문/후보 등급의 요구 참조와 요구 links의 질문/협력사 참조를 확인한다.
  허용/통합 근거 조항 및 질문의 조항 참조는 제공 요구의 origin 조항에서 찾는다.
  요구와 조항이 함께 있는 질문은 그 요구의 조항과 같아야 한다.
- 파트너 경로의 후보는 제공 협력사와 해당 요구의 등급이 있어야 한다. 다른 경로의 후보 코드는
  제공 비교 결과의 자사 제품 코드에서 찾는다. 단독 제품 코드로 제품을 새로 만들지 않는다.
- 사람 선택은 존재하는 허용 경로와 그 경로 후보에 한정한다. 차단 경로·금지된 외부 경로·
  제외된 협력사를 사람 선택으로 제공해도 추천으로 바꾸지 않고 오류로 거부한다.
- 통합 필요 여부와 근거 조항 목록, 번호·ID 대응을 확인한다. 근거 없는 통합 책임 문장을 만들지 않는다.
- 비어 있는 요구 원문/코드, 비어 있는 질문, 내부 질문의 부서 누락, 스냅샷 간 data_origin 불일치,
  비교 불가인데 MEETS/DIFFERS라는 모순도 거부한다.

검증 실패는 `RFP_DRAFT_*`의 `ValueError`다. 빈 초안으로 실패를 숨기지 않는다.
이 함수가 소비하지 않는 변경 제안 ID·과거 초안 문장 ID는 여기서 조회하거나 검증하지 않는다.

## 절과 표현

| 절 | 근거에 따른 내용 |
| --- | --- |
| `CUSTOMER_GOAL_SCOPE` | 고객 요구의 현행 인용으로 제안 검토 범위를 표시. 별도 근거 없는 고객 목표를 만들지 않음 |
| `OUR_RESPONSE` | 문서 근거가 있는 자사 후보의 해당 속성·요구값·후보값·비교 결과. 적용 범위·조건·제한을 보존 |
| `DEVIATIONS` | 사양 차이 판정 기록, 자사/대체 사양 보완 경로의 검토안 |
| `PARTNER_SUPPLEMENT` | 허용된 협력사 경로·후보의 가상 프로필과 요구별 등급, 미확인 조건 |
| `ASSUMPTIONS_EXCLUSIONS` | 조건부/근거 부족·보류·내부 가정, 통합 책임의 확보 여부와 미확보 조건 |
| `QUESTIONS_NEXT_ACTIONS` | 요구의 다음 확인 담당, 고객 질문 또는 내부 부서의 다음 행동 |

항상 여섯 절을 반환하고 근거 자료가 없는 절은 빈 문장 목록으로 둔다. 고객 목표 자유문장과
일반 가정을 발명하지 않는다. 선택지는 검토안/미선택으로 표시하며 사람 선택도 이행 확약으로 바꾸지 않는다.
`CONDITIONAL`·`INSUFFICIENT_EVIDENCE`·`SPEC_GAP`, 조건부 필수와 미정 항목·판정 미확인,
후보의 적용 조건과 자료 제한, R&D `PENDING`/`WAIVED`, 재검토 표시는 조건/미확인으로 남긴다.
제공된 `spec.tolerance`는 원문 그대로 조건에 보존하며 null을 허용 오차 없음으로 확정하지 않는다.
`needs_review`(시스템 추출 검토 표시)와 `needs_rereview` 중 하나라도 true이면 관련 문장의
`needs_rereview`·`unconfirmed`에 반영한다. 두 입력 필드 자체를 수정하거나 같은 의미로 저장하지 않는다.
모든 문장은 `provenance_kind=system_record`이며, 사람 입력의 내용을 인용해도 코드 생성 문장 자체를
사람이 승인한 문장으로 표시하지 않는다. 근거 있음은 해당 요구의 기록이며 전체 공급 가능 판단이 아니다.

자사 후보의 문서 근거가 비어 있으면 그 후보 문장은 만들지 않는다. 차단 경로와 EXCLUDED 후보는
추천하지 않는다. 공급·납기·보증·수주를 확약하지 않으며 가상 프로필의 납기/보증을 실제 계약으로 복사하지 않는다.
통합 책임이 미확보이면 조립·공장 시험·연동·보증 확인 조건과 제공 fallback 경로를 보존하고 유닛 공급을 확정하지 않는다.
책임자가 지정되어 있어도 입력의 책임자 기록일 뿐 공급 확약이 아니다.

CUSTOMER는 고객 확인 질문, INTERNAL은 담당 부서 다음 행동으로 표시한다. DRAFT는 초안·미발송,
OPEN은 확인된 질문이지만 발송 여부는 별도 확인, REPLIED는 회신 수신 기록이며 판정 반영은 별도 확인이다.
회신을 받았다는 사실만으로 요구의 조건/상태를 해소하지 않는다. `shown_in_draft=true`인 내부 가정은
질문 근거와 함께 표시하고 충족 판정에 사용하지 않는다는 문구를 유지한다.

## 근거 집계와 고정 고지

각 문장의 `evidence`는 입력에서 확인된 `requirement`·`clause`·`product`·`partner`·`question` 참조다.
제품 비교 문장에는 요구/제품, 협력사 문장에는 요구/협력사, 질문에는 질문 및 연관 요구/조항 근거를 붙인다.
입력에 없는 참조나 근거가 없는 문장을 생성하려 하면 오류다. 제목/고지는 제안 문장이 아니다.

`evidence_missing_count = sum(sentence.evidence_missing for 모든 출력 문장)`이며 고지/제목을 제외한다.
근거 없는 제안 문장을 만들지 않으므로 정상 생성은 각 flag가 false이고 합은 0이다. 자료가 부족한 경우
자료 부족 자체를 **근거 있는 요구의 판정 기록**으로 표시할 수 있지만 제품 사실을 만들어 채우지는 않는다.
빈 입력도 문장 0개이므로 count는 0이다. 이것이 업무 정확도나 추적률 100%를 증명하지 않는다.

`disclaimer`는 다음 문자열을 그대로 유지한다.

> 시연용 검토본이며 실제 제안서가 아니다.

## 검증 범위와 남은 연결

`tests/tft/test_rfp_draft.py`는 일반 합성 데이터로 스키마·여섯 절·결정성·입력 불변성·참조/판본,
추적 불변식과 count, 조건/미확인/재검토, 복수 경로의 후보 ID 유일성, 통합 책임과 질문 상태를 확인한다.
실제 기획 사례 문구·제품 코드를 하드코딩하지 않는다. `scripts/check_backend.py tft`가 새 테스트를 수집한다.

공식 `rfp_draft_trace_rate` 측정과 `eval.rfp_metrics.gate_values` 연결은 **후속 작업**이다.
현재는 생성 문장의 근거 추적 불변식만 검사하며 공식 평가 통과로 보고하지 않는다.
분모가 0인 빈 초안의 추적률도 1.0으로 보고하지 않는다.
기획서 4.10 원문과 Jira 전체 완료조건은 이 구현에 별도로 제공되지 않았으므로 이 계약은
supervisor가 승인한 순수 엔진 범위다. 기획 원문 전체 구현·HTTP/저장/배포·실제 업무 성능 완료를 뜻하지 않는다.

## 처리 흐름·저장·HTTP 연결 (KAN-188 후속)

순수 엔진 자체는 그대로이며 `drafts.py`가 같은 DB 연결의 공개 snapshot 변환을 조합한다.
처리 흐름은 compare~draft를 한 트랜잭션으로 묶고, 초안 작업 ID당 한 판본만 저장한다.
같은 `source_job_id` 재시도는 원래 생성 판본을 읽으며 그 사이 사람 편집으로 바뀐 head를 덮어쓰지 않는다.
다른 작업 ID로 명시적으로 재처리하면 새 초안 판본을 만든다. 생성/편집/검토본 저장/내보내기는
동일 프로젝트 advisory transaction lock으로 직렬화한다.

T17은 기존 표에 `draft_sentences.needs_rereview`(생성 당시 검토 표시), `draft_versions.source_job_id`
(부분 unique index) 두 열만 추가하고 필요한 표의 worker 조회·초안 추가·head/내보내기 결과 열 갱신 권한만 준다.
기존 `rereview_marks`는 `cause_proposal_id`가 필수이므로 시스템 추출 검토 표시 저장을 대신할 수 없다.
표는 새로 만들지 않는다. 로컬·배포 DB에는 운영자가 T14 이후 T17을 적용해야 한다.

- **25 GET draft**: 최신 또는 `?version=N`. 고지·여섯 절·문장 근거·조건·미확약·검토 표시를 반환한다.
  공개 `sentence_id`는 `sentence_key`, 물리 판본 행 ID는 내부 전용이다. 요구 상세 링크도 최신 공개 키를 반환한다.
- **26 PATCH draft**: 담당 sales·책임자만 가능. `DraftEdit.version` CAS, 문장별 사유와 audit,
  불변 이전 판본을 유지한다. 기존 문장 수정/절 이동에서도 조건·미확약·검토 표시·공개 ID를 보존한다.
  새 문장은 section이 필수다. 원문 없는 자유문장 또는 프로젝트 안에서 확인 못한 evidence는
  `evidence_missing=true`로 표시·합산하고 검토본 저장을 막는다. 변경 DTO로 고지나 검토 표시를 지울 수 없다.
- **27 POST review-versions**: 담당 sales·책임자, Idempotency-Key 필수. 최신 초안 version 일치,
  근거 누락/문장 재검토 0, 필수 요구 해결 또는 `unresolved+next_owner_field`, 통합 책임 필요 시 확보,
  빈 초안 아님을 확인한다. 실패는 422 `TFT_DRAFT_INCOMPLETE`(대상 ID를 details에 표시), stale은 409.
  성공 시 `REVIEW_DRAFT`가 되고 미해결 필수가 있으면 `CONDITION_CHECK`, 없으면 `OWNER_CONFIRMED`다.
  이는 담당자의 저장 행위이며 공급·수주 승인이 아니다. 같은 키 재전송은 원래 검토본을 반환한다.
- **28 POST exports**: 담당 sales·책임자, Idempotency-Key 필수. 지정된 불변 review_version 예약과 플랫폼
  `rfp.export` 접수. `ExportAccepted(JobAccepted)`에 `export_id`를 추가하여 파일 GET과 연결하며,
  같은 요청 재전송은 같은 export_id다. process/annotate는 기존 JobAccepted를 유지한다.
- **29 GET exports/{export_id}/file**: 기존 프로젝트 조회 범위, 다른 프로젝트/없는 자원은 404,
  미완료는 409 `TFT_EXPORT_NOT_READY`. 완료 파일은 sha256/size 확인 후 DOCX MIME·attachment·no-store·nosniff로 반환한다.
  파일 누락/손상은 안전한 503이며 내부 storage_key는 응답에 포함하지 않는다.

### 내보내기 포맷과 파일

ExportFormat의 `DOCX`·`PDF` enum은 바꾸지 않는다. **이번 구현은 DOCX만**이며 PDF는 예약/DB 쓰기/
플랫폼 접수 전에 422 `TFT_EXPORT_FORMAT_UNAVAILABLE`다. PDF 생성은 별도 후속 작업이다.
reportlab·글꼴·의존성·이미지를 추가하지 않는다.

DOCX는 stdlib ZIP/XML의 실제 OOXML 패키지로, 고정 고지·모든 절/문장·공개 ID·조건·근거·미확약/검토 표시를 싣는다.
XML escaping 및 `w:br`·`w:tab`으로 한글/특수문자/줄바꿈을 보존하고 유효하지 않은 XML 문자는 삭제하지 않고 실패한다.
검토본 판본에 대해 바이트가 결정적이다. 기존 원문 DocumentStore(private GCS 또는 `TFT_DOCUMENT_ROOT`)에
`rfp/{project}/exports/{export}.docx`로 저장하며 25 MiB 상한·해시·크기를 기록한다.
객체 생성 후 DB commit 전에 실패하면 private 객체만 남을 수 있다(기존 업로드와 동일).
재시도는 같은 바이트일 때만 기존 객체를 인정하며 다른 바이트를 덮어쓰지 않는다.

서버/플랫폼 웹 중계는 이 다섯 경로와 정확한 export 파일 경로의 DOCX 응답을 허용한다.
본체 JSON 경로에 바이너리를 허용하지 않는다. 실제 모델·외부 발송·공식 추적률 평가 연결은 포함하지 않는다.
로컬 일반 합성/파일/HTTP 검증과 전용 PostgreSQL CI 검증을 분리하며 DB 미실행을 통과로 보고하지 않는다.
