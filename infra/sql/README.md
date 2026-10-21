# KAN-65 서비스별 DB 권한 초기 설정

대상은 `team4-sql`의 `team4` DB다. 사용자가 `postgres`로 접속한 psql 터미널에서 실행한다. Google 서비스 계정인 `cs-runtime` 등과 아래 PostgreSQL 역할은 별도다. 이 SQL은 GCP IAM 권한을 부여하지 않는다.

| 실행 계정 | 테이블 생성 및 변경 역할 | 소유 스키마 |
| --- | --- | --- |
| `cs_app` | `cs_migrator` | `cs`, `checkpoint_cs` |
| `tft_app` | `tft_migrator` | `tft`, `checkpoint_tft` |
| `platform_app` | `platform_migrator` | `identity`, `integration`, `catalog`, `evaluation` |

실행 계정에는 자기 서비스 스키마의 USAGE와 앞으로 생성할 테이블의 SELECT, INSERT, UPDATE, DELETE, 시퀀스의 USAGE 및 SELECT, 함수의 EXECUTE를 부여한다. CREATE, TRUNCATE, 역할 관리와 DB 생성 권한은 부여하지 않는다. `public`은 관리자 설치 확장용으로 USAGE만 허용한다.

마이그레이션 역할은 NOLOGIN이다. 관리자가 `SET ROLE`로 전환해 해당 서비스의 테이블을 생성하거나 변경한다. 앱 실행 계정에는 마이그레이션 역할을 부여하지 않는다. 배포용 마이그레이션 로그인 주체는 후속 CI/CD 설정에서 연결한다.

공통 `search`, `runtime`의 쓰기 및 업무별 접근 정책은 후속 마이그레이션에서 정의한다. 이 단계에서 모든 서비스에 해당 스키마의 무제한 권한을 부여하지 않는다. 업무 테이블, pgvector 확장 및 임베딩 차원은 아직 생성하지 않는다.

## 실행

아래 경로는 현재 작업 환경 기준이다. 다른 PC에서는 해당 체크아웃의 절대 경로를 사용한다.

```psql
\i /home/canae/ls-final-project/infra/sql/001_service_roles.sql
\i /home/canae/ls-final-project/infra/sql/002_verify_service_roles.sql
```

첫 파일은 전체 트랜잭션으로 실행한다. 기존 역할이나 스키마와 이름이 겹치면 중단하므로 기존 권한을 덮어쓰지 않는다. 최초 설정용이며 재실행 시 이미 존재하는 역할 오류가 정상적으로 발생한다. 오류가 나면 `ROLLBACK;`을 실행한 뒤 원인을 확인한다.

둘째 파일은 마이그레이션 역할로 검사 테이블, 시퀀스와 함수를 생성하고 각 앱 역할로 CRUD와 다른 스키마 접근 및 DDL 거부를 검증한다. 성공 시 PASS를 출력하고 검사 객체는 ROLLBACK으로 제거한다. 역할 멤버십도 검사한다. 관리자 세션의 SET ROLE 검증이므로 비밀번호 인증, GCP IAM 및 실제 앱 로그인까지 검증한 것은 아니다.

PASS 이후 psql의 비밀번호 입력창에서 서로 다른 비밀번호를 지정한다. 비밀번호를 SQL 문에 직접 적지 않는다.

```psql
\password cs_app
\password tft_app
\password platform_app
```

실행 계정은 생성 시 비밀번호가 없어 비밀번호 설정 전에는 비밀번호 인증으로 로그인할 수 없다. 로컬 앱의 비밀번호는 Git에서 제외된 `.env`, 배포 환경은 Secret Manager에서 관리한다. 관리자 비밀번호와 마이그레이션 권한을 앱에 전달하지 않는다.

## 후속 마이그레이션 규칙

관리자는 테이블 생성 전에 해당 역할로 전환한다. 기본 권한은 실제로 테이블을 생성한 역할에 적용되므로 postgres로 테이블을 만들면 앱에 위 기본 권한이 적용되지 않는다.

```sql
BEGIN;
SET LOCAL ROLE cs_migrator;
-- CREATE TABLE cs.example (...);
COMMIT;
```

LangGraph 체크포인터의 테이블 초기화 역시 마이그레이션 단계에서 해당 역할로 수행한다. 런타임 계정으로 `.setup()` 같은 DDL 초기화를 호출하지 않는다.

근거: [PostgreSQL 스키마 권한](https://www.postgresql.org/docs/17/ddl-schemas.html), [생성자별 기본 권한](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html), [Cloud SQL 사용자 생성](https://docs.cloud.google.com/sql/docs/postgres/create-manage-users).

## 준비 단계의 검증 결과

2026-10-21, 공식 Ubuntu 패키지를 /tmp에 추출한 PostgreSQL 16.15 임시 서버에서 두 SQL을 실행했다. 테스트용 postgres는 SUPERUSER 없이 CREATEDB와 CREATEROLE만 가진 별도 역할로 구성했다. 앱 CRUD, 전체 8개 스키마의 접근 행렬, 다른 서비스 SELECT 및 INSERT 차단, CREATE TABLE 차단, 앱의 역할 멤버십 부재를 통과했다. 검사 후 임시 테이블 및 시퀀스는 0개였고, 잘못된 DB 실행과 초기 설정 재실행은 오류로 중단됨을 확인했다. 테스트 서버는 종료했다.

위 자동 검증은 로컬 PostgreSQL 16 결과다. 이후 사용자가 실제 Cloud SQL PostgreSQL 17에서 두 SQL 실행, PASS 확인과 비밀번호 설정 안내에 대해 완료했다고 응답했다. 2026-10-21 12:36 UTC API 재조회에서도 cs_app, tft_app, platform_app의 존재를 확인했다. 사용자는 이어 계정별 실제 로그인과 스키마 접근 행렬 확인 및 로컬 .env 입력도 완료했다고 응답했다. 클라우드 SQL의 상세 권한과 실제 로그인을 에이전트가 직접 재검증한 결과는 아니며 Cloud SQL Client IAM 부여는 후속 실조회에서 확인했다. 배포 Secret 연결과 실제 Cloud Run DB 연결은 남아 있다.
