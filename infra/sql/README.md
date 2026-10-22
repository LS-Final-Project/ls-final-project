# 서비스별 DB 권한 최초 구성

공용 DB와 로컬 초기화 기반은 준비돼 있다. 이 문서는 인프라 담당자가 새 DB를 최초 구성할 때 참고한다. 팀원의 로컬 시작은 [공통 온보딩](../../docs/team-start.md), 업무 테이블 추가는 서비스별 새 마이그레이션을 사용한다.

| 실행 계정 | 테이블 생성 및 변경 역할 | 소유 스키마 |
| --- | --- | --- |
| `cs_app` | `cs_migrator` | `cs`, `checkpoint_cs` |
| `tft_app` | `tft_migrator` | `tft`, `checkpoint_tft` |
| `platform_app` | `platform_migrator` | `identity`, `integration`, `catalog`, `evaluation` |

실행 계정에는 자기 서비스 스키마의 USAGE와 앞으로 생성할 테이블의 SELECT, INSERT, UPDATE, DELETE, 시퀀스의 USAGE 및 SELECT, 함수의 EXECUTE를 부여한다. CREATE, TRUNCATE, 역할 관리와 DB 생성 권한은 부여하지 않는다. `public`은 관리자 설치 확장용으로 USAGE만 허용한다.

마이그레이션 역할은 NOLOGIN이다. 관리자가 `SET ROLE`로 전환해 해당 서비스의 테이블을 생성하거나 변경한다. 앱 실행 계정에는 마이그레이션 역할을 부여하지 않는다. 배포용 마이그레이션 로그인 주체는 후속 CI/CD 설정에서 연결한다.

공통 `search`, `runtime`의 쓰기 및 업무별 접근 정책은 후속 마이그레이션에서 정의한다. 이 단계에서 모든 서비스에 해당 스키마의 무제한 권한을 부여하지 않는다. 이 초기 SQL은 역할·스키마만 구성한다. pgvector는 별도로 설치돼 있으며 업무 테이블과 검색 인덱스·임베딩 차원은 기능별 마이그레이션으로 추가한다.

## 실행

대상 DB는 `team4`다. DB 관리자 psql 세션을 사용하며 PostgreSQL 역할은 GCP의 runtime 서비스 계정과 별개다. 이 SQL은 GCP IAM을 변경하지 않는다.

새 DB를 구성할 때 저장소 루트에서 연 psql 세션을 사용한다. 기존 공용 DB에 최초 구성 SQL을 다시 실행하지 않는다.

```psql
\i infra/sql/001_service_roles.sql
\i infra/sql/002_verify_service_roles.sql
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
