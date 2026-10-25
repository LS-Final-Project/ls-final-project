# 플랫폼 작업 접수 저장 계약 — KAN-69 초기 구현

현재 구현은 평가 실행 접수에 필요한 저장 함수와 마이그레이션이다. KAN-90의 local/test 전용 합성 접수 API에서 사용하며 [평가 접수 계약](evaluations.md)을 따른다. 로컬 합성 worker의 상태 전이·lease는 평가 계약에 추가했다. 실제 평가·Cloud Run Jobs 전송·SSE는 아직 연결하지 않았다. KAN-90의 운영 실행 잠금은 그대로 유지한다. 공통 규약은 [작업 계약](../../docs/api-contract.md)을 따른다.

## 저장과 소유권

`infra/migrations/platform/20261025_KAN-69_job_admission.sql`을 관리자 연결로 한 번 적용한다. 기존 초기 역할과 identity 마이그레이션이 선행한다. 새 로컬 DB의 초기화 루프에도 포함된다. 기존 환경의 적용은 명시적으로 수행하며 공용 DB에는 이번 작업에서 적용하지 않았다.

```bash
python3 scripts/workspace.py migrate platform 20261025_KAN-69_job_admission.sql
```

`runtime` 소유자는 `platform_migrator`다. 초기 지원 조합은 `owner=platform`, `kind=evaluation.run`, `POST /api/v1/platform/evaluations/runs` 하나다. `platform_app`은 기본 세 테이블에 SELECT·INSERT만 가능하다. 후속 KAN-90 smoke worker migration은 jobs.status 열의 UPDATE만 추가하며 DELETE·TRUNCATE는 없다. C/S·TFT 계정에는 schema 접근과 테이블 권한을 부여하지 않는다. 다른 업무 종류와 상태 변경 권한은 실제 소비자 구현 시 별도 마이그레이션으로 확장한다.

| 테이블 | 저장 단위와 제약 |
| --- | --- |
| `runtime.jobs` | 작업 UUID, 서버 생성 run UUID, 동결 입력 SHA-256, 내부 사용자 UUID, 멱등 범위와 요청 hash. 사용자·owner·method·route·key가 유일하며 업무 run당 작업 하나 |
| `runtime.job_events` | 작업별 양의 sequence. 최초 접수는 sequence=1, type=job.queued, 빈 payload |
| `runtime.dispatch_outbox` | 작업당 전송 의도 한 건. dispatcher 상태·전송 확인·lease 구현 전의 최소 저장 구조 |

임의 명령·URL·서비스 계정·원문·정답·자격증명은 받거나 저장하지 않는다. 멱등 키에는 고객 정보나 토큰을 넣지 않는다. 현재 삭제/만료 기능은 없으며 24시간 이후 자동으로 새 실행을 허용하지 않는다.

## 호출자 트랜잭션

`backend/api/platform/jobs/admission.py`의 `admit_evaluation_job()`은 현재 열린 READ COMMITTED 트랜잭션과 `dict_row` 연결을 받는다. 함수 내부에서 commit·새 연결·외부 네트워크 호출을 하지 않는다. 연결이 transaction 밖이면 SQL 실행 전에 거부한다.

1. 호출자는 현재 세션·지정 평가자·실험 접근 범위·시험 차단을 검사한다. 재전송도 동일 검사를 먼저 수행한다.
2. 요청 본문과 조건 헤더를 정규화한 hash, 동결 입력 판본, 서버 생성 run UUID로 접수 함수를 호출한다. hash는 신뢰한 서버 계산값이어야 한다. 브라우저가 제공한 hash를 그대로 사용하지 않는다.
3. 함수는 유일 키로 작업을 INSERT한다. 같은 키 경합은 PostgreSQL이 이전 transaction 종료까지 직렬화한다. 같은 hash·판본이면 기존 job/run과 `created=false`를 반환하며 다른 내용이면409 충돌을 발생시킨다.
4. 신규면 최초 이벤트·outbox까지 저장하고 `created=true`를 반환한다. 호출자는 이 경우에만 반환된 run ID로 run/cases/control 등 업무 기록을 같은 연결에 저장한다.
5. 어느 단계든 실패하면 호출자가 전체 rollback한다. commit 성공 전에는202를 반환하지 않는다. 재전송의 임시 생성 run ID는 버리고 반환된 기존 run ID를 사용한다.

이 순서는 작업 유일 키를 먼저 확보하므로 run을 중복 생성하지 않게 한다. 작업·업무 레코드의 최종 원자성은 KAN-90 통합에서 검증해야 하며, 이 함수 자체가 평가 run이나 허용 실험 검증을 생성·수행하지 않는다. 입력 hash·사용자 권한이 맞다는 것을 저장 함수만으로 보장하지 않는다.

DB 대기 시간 제한과 오류→HTTP 변환은 호출자 경계의 후속 구현이다. DB 장애·commit 불명확 시 새 키를 발급하지 말고 현재 권한 확인 후 동일 키로 재조회/재요청한다. 현재 함수가 보존하는 것은 최초 job/run 식별자이며 최초 HTTP 응답 envelope·Location의 완전 재생은 KAN-90에서 추가해야 한다.

## 검증 범위

`tests/platform/test_job_admission.py`는 전용 PostgreSQL과 platform_app 계정으로 동시 동일 요청, 다른 hash 충돌, 후속 업무 SQL 실패, outbox INSERT 실패, transaction 없는 호출 거부, 다른 서비스 권한을 검사한다. 기존 auth-database CI에 이 파일을 추가해 DB 테스트가 단순 skip으로 끝나지 않게 한다.

공통 작업 전송·인증·재시도·SSE 재접속과 평가 run 소유권은 이 접수 저장과 별도다. 이 저장 구조의 성공을 모델 호출의 중복 방지, 최종 시험 보호 전체 또는 KAN-69 전체 완료로 보고하지 않는다.
