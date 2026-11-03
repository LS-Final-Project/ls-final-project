# RFP 맥락별 정리 (KAN-188)

기존 추출 요구를 설비·과업 및 검토 보고서 분류로 정리하는 **검토 후보**다. 원문 요구 추출,
자사·협력사 대응 판정, 사람의 승인과 구분한다. 기존 요구를 삭제·합치거나 상태를 바꾸지 않는다.
원문에 있었지만 추출되지 않은 요구까지 망라했다는 뜻은 아니다.

## 모델과 검증

LangGraph는 입력 검증 → 맥락 후보 추출 → 요구 연결 → 결정론적 검증 → 제한된 보정 순서다.
후보 추출은 80개, 연결은 24개씩 최대 3개 병렬 호출한다. 애매한 연결 보정은 최대 24개에 대해
한 번만 한다. 입력은 최대 4,000개 요구/직렬화 5M자이며 초과하면 명시 거부한다. 외부 추적은 차단한다.
공유 Gemini factory/generate를 사용해 기존 호출 원장·lease·실패 경계를 유지한다.
프롬프트 v2는 분류별 필드를 모두 요구하고 근거 목록에 해당 요구 ID를 명시하도록 강화한다.
저장 스키마 v1과 과거 프롬프트 v1 결과는 유지하며 읽기·사람 수정 시 판본을 임의로 바꾸지 않는다.
모델/정산 실패는 빈 성공으로 대체하지 않는다. 스키마·근거 불명확은 미분류/검토 필요로 남긴다.

주 분류는 EQUIPMENT(장비), TASK(과업), DELIVERABLE(제출물), INTERFACE(인터페이스),
QUALITY(시험·품질), SCHEDULE(일정), QUALIFICATION(자격), COMMERCIAL(계약·상업조건)이며
불명확하면 UNCLASSIFIED다. 부 분류가 여럿이어도 원 요구 ID는 하나다.
그룹은 SYSTEM/EQUIPMENT/TASK/COMMON, 관계는 SAME_EQUIPMENT/PART_OF/APPLIES_TO/RELATED다.
물리적 동일 설비는 명시 태그와 원문 범위 근거가 필요하다. 이름이 같은 제품군만으로 자동 확정하지 않는다.
문서·절 범위와 태그·현장을 보존하며 도면 참조도 원본 ID가 있는 경우에만 연결한다.
도면 참조가 전기적 적합성이나 실제 연결의 확정을 뜻하지 않는다.

## API와 저장

- `GET /api/v1/tft/projects/{id}/context?version=N`: 현재 또는 지정 맥락 판본과 당시 요구·출처를 반환한다.
  `NOT_ANALYZED`, `CURRENT`, `STALE`를 구분한다. 현재 원문/요구 fingerprint가 달라지면 과거 결과는 STALE다.
- `POST /api/v1/tft/projects/{id}/context/process {reason?}`: 저장된 요구를 대상으로 `rfp.context` 작업을 접수한다.
  기존 문서를 재추출하지 않는다. 요청 시 서버가 원문/요구 fingerprint와 맥락 head를 입력 hash에 고정한다.
- `PATCH /api/v1/tft/projects/{id}/context`: `expected_version`, `expected_source_fingerprint`,
  `reason`, 전체 `result`를 받는다. 원문 근거 검증과 버전 CAS 후 불변 새 판본만 추가한다.
  기존 판본 삭제/수정이나 승인 상태 변경은 없다. 사람이 수정해도 검토 후보다.

프로젝트 가시성과 기존 processing 권한을 적용한다. 쓰기는 세션/CSRF/멱등키를 유지한다.
PATCH는 프로젝트·actor·멱등키와 본문 hash를 결속한다. 다른 본문 재사용은 409다.
AI 호출 중 DB 트랜잭션을 유지하지 않고, 저장 시 원문 fingerprint/맥락 head를 다시 대조한다.
다른 수정이 먼저 저장되면 후보 저장을 거부한다. 모델 비용은 이미 발생할 수 있다.
원 요구/문서 테이블에 대한 광역 잠금은 추가하지 않는다. 저장 뒤 원문이 바뀌어도 조회 시 STALE로 표시한다.

`tft.context_versions`는 source snapshot, 결과, provenance, actor, 사유를 불변 저장한다.
`tft.review_context_snapshots`는 검토본 저장 트랜잭션에서 당시 맥락을 고정한다.
DOCX는 그 snapshot만 읽어 8개 분류/설비 구조/원 요구 ID·판본·문서·절·쪽·인용/미확정을 덧붙인다.
기존 6개 초안 구역을 보존한다. 과거 검토본에 맥락 snapshot이 없으면 미포함을 명시하고 나중 결과를 끼워 넣지 않는다.

## 작업·진행 표시

`rfp.context`는 기존 한 프로젝트 한 활성 작업/lease/단일 dispatcher/정산 규칙을 쓴다.
SSE `context_map`, `context_link`, `context_validate`는 실제 후보 정리·연결·검증 관측이다.
호출 전후 이벤트로 lease를 갱신하며 완료는 terminal 상태 GET 확인 이후에만 표시한다.
보정 횟수나 batch 건수에서 가짜 진행률/남은 시간을 만들지 않는다.
정규 `rfp.process`는 기존 추출·비교·초안 저장 뒤 같은 job 안에서 맥락 정리를 실행한다.
맥락 처리 실패 시 기존 추출·초안 커밋은 남을 수 있고 job은 실패한다. 전체를 원자적 성공으로 설명하지 않는다.
그림만 있고 추출 요구가 0개인 경우 모델을 추가 호출하지 않으며 도면 검토와 구분한다.

## 적용과 검증 경계

플랫폼 `20261118_KAN-188_context_jobs.sql`, TFT `20261118_KAN-188_context_analysis.sql` 및
`20261119_KAN-188_context_commands.sql`이 필요하다. additive 저장이며 기존 결과를 재계산하지 않는다.
원래 worker 권한 재적용 migration은 새 두 표의 필요한 권한도 조건부 복원한다.
합성 엔진 회귀/전용 DB/실모델 품질/사용자 UI 검토는 각각 별도 증거로 기록한다.
