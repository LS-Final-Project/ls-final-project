# KAN-123 요청 부서 후보 추천·검증 API

기반: KAN-119 스키마, KAN-122 요청서 판본·조직 dept_code. 추천은 자동 선발·TFT 최종 확정이 아니다. 사용자 ID와 후보 인력 ID는 다르다.

## 경로

| 경로 | 메서드 | 응답/입력 |
|---|---|---|
| `/api/v1/tft/department-requests` | GET | 본인 부서에 발행된 요청서 목록. limit 1~100, UUID cursor, next_cursor |
| `/api/v1/tft/staffing-requests/{request_id}/candidates` | GET | 요청 부서에 속한 후보만. limit/cursor 동일. 전체 직원 자동 검색·선발 없음 |
| `/api/v1/tft/staffing-requests/{request_id}/recommendations` | GET | 그 요청서의 추천·경험·역량·가용성 별도 기록 |
| 같은 경로 | POST | candidate_id, rationale(비어 있지 않은 추천 근거) |
| `.../recommendations/{recommendation_id}/experience` | POST | summary(수행 업무), project_name, role_title, period_start/end, evidence_source, status, basis |
| `.../recommendations/{recommendation_id}/availability` | POST | period_start/end, allocation_rate(0~100), constraints_note, status, basis |
| `.../recommendations/{recommendation_id}/competency` | POST | 해당 역할의 competency_id, status, basis. 같은 후보·역량의 재검증은 새 행으로 추가하며 기존 확인 기록 보존 |

POST 응답은 `SuccessResponse[CreatedRecord]`: 생성 id와 request_version. ETag는 변경 후 요청서 판본이다. 모든 POST에 If-Match(요청서 판본) 및 Idempotency-Key(1~128자)가 필요하다. 미지정 If-Match는 428, 오래된 값은 412, 키 누락은 422다.

## 권한·상태·기록

- 인증된 dept_staff만 추천/검증. manager·sales라도 해당 역할 없이 대신 작성하지 못한다(403).
- `organizations.dept_code == principal.dept`, 요청 조직과 프로젝트의 company_code 일치, DRAFT 아님을 먼저 확인한다. 범위 밖·없는 요청서는 404, 범위 밖 후보·추천도 404다.
- ISSUED/ANSWERED 요청서와 미확정 프로젝트에만 새 기록을 추가한다. CLOSED, 최종 확정 프로젝트, 철회된 추천은 409다. 재개/승인/반려는 KAN-127 범위다.
- 추천자·검증자·시각은 서버가 채운다. 사용자 입력의 actor·data_origin 등 허용하지 않은 필드는 422다. 같은 담당자가 추천·검증해도 별도 행이며 독립 교차 검증이라 표시하지 않는다.
- NEEDS_CHECK/UNSPECIFIED는 역량 부족이나 가용성 없음으로 바꾸지 않는다. CONFIRMED/UNMET은 비어 있지 않은 근거가 필수다. 가용성 CONFIRMED는 기간·투입률이 추가로 필요하고 역전된 기간은 거부한다.
- 경험·역량·가용성은 추가 전용 입력이다. 과거 행을 덮어쓰지 않는다. KAN-125는 서버 확인 시각과 ID로 최신 기록을 선택해 충돌/공백을 판정한다. 역량 재검증에는 `20261025_KAN-123_competency_rechecks.sql` 적용이 필요하다. 새 기록으로 기존 역할 확정 근거가 달라지면 재검토 대상이 된다.
- 출처는 요청·프로젝트·후보/기존 추천을 보수적으로 결합한다. 하나라도 synthetic이면 synthetic, 아니면 demo가 있으면 demo, 나머지만 real. 사람 입력은 derivation_kind=PROVIDED로 기록한다.
- 추천 등록은 요청서를 ANSWERED로 바꾸고 요청서 version과 감사 이력을 추가한다. 역할은 ISSUED일 때만 RECOMMENDATIONS_RECEIVED로 진행한다. 검증 기록 추가도 요청서 판본과 VERIFY 감사 이력을 남기며 최종 승인/구성안은 만들지 않는다.

## 원자성·중복

요청서 행을 잠근 동일 DB transaction에서 업무 행, 판본, 감사 이력, 멱등 응답을 기록한다. 호출 중 외부 모델·메일·작업 큐를 사용하지 않는다. 실패하면 모두 롤백된다.

멱등 범위는 로그인 사용자 + 요청서 + 작업 종류/추천 ID + 키다. 같은 키·같은 정규화 입력은 최초 응답을 재현하며, 다른 입력은 409다. 재현 전에도 현재 부서 범위를 검사한다. 다른 키로 같은 후보를 중복 추천하면 409(판본이 오래됐으면 412)다. tft_app은 멱등 기록 UPDATE/DELETE 권한이 없다.

## 프로젝트 조회

부서 사용자는 자기 부서의 비DRAFT 요청서가 있는 같은 회사 프로젝트를 목록에서 볼 수 있다. 상세는 해당 역할, 요청서, 해당 요청의 자기 부서 후보, 연결 요구사항의 인용 근거만 포함한다. 다른 부서 요청·후보·전체 구성안/최종확정 기록은 숨긴다. 담당 sales·책임자·관리 범위 manager의 기존 전체 조회는 유지한다.

## 검증·통합 경계

`uv run --python 3.12 --frozen python scripts/check_backend.py tft`와 전용 `TFT_TEST_DATABASE_URL`의 `tests/tft/test_recommendations_database.py`를 실행한다. CI tft-database는 별도 PostgreSQL 서비스에서 실제 SQL/권한/경합/롤백을 검사한다.

현재 경로는 업무 API의 partial 구현이다. 운영 플랫폼/웹의 새 경로 allowlist 연결과 실제 로그인 브라우저 통합은 KAN-140에서 수행한다. 새 서비스 권한·배포·외부 호출은 이 변경에서 활성화하지 않는다. PR은 KAN-122 위의 후속 브랜치이며 해당 PR 승인·병합 없이 dev에 병합하지 않는다.
