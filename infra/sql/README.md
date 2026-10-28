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

## 사서함 보관 준비 (KAN-81)

현재 구현은 내부 보관 경계이며 공용 환경에는 미적용이다. 기존003 인증 점검과 기본
로그인은 새 보관 테이블·키 없이 유지한다. 아래는 별도 운영 승인 후 운영자 터미널에서
진행할 준비이며, 에이전트가 공유 DB나 Secret/IAM을 변경하지 않는다.

1. 현재 연결 대상이 공용 프로젝트의 team4 DB인지 읽기 전용으로 확인한다. 기존 역할·identity
   테이블과 새 mailbox_auth 스키마 존재/소유자를 확인하고, 기존 스키마가 있으면 덮어쓰거나
   마이그레이션을 반복하지 않는다. 임시 검사 DB는 kan77_auth_test만 허용한다.
2. 운영자 postgres 연결에서 `infra/migrations/platform/20261027_KAN-81_mailbox_vault.sql`을
   `psql -X -v ON_ERROR_STOP=1 -f ...`로 한 번 적용한다. 이 파일은 새 스키마를 생성하므로
   이미 있으면 실패한다. 플랫폼 런타임에는 새 스키마 USAGE와 테이블 DML만 부여하고
   C/S·TFT·평가 worker에는 접근을 부여하지 않는다.
3. platform_app 연결에서 `infra/sql/004_check_mailbox_vault.sql`을 같은 psql 검사 옵션으로
   실행한다. 스크립트는 BEGIN READ ONLY/ROLLBACK으로 테이블·최소 권한·서비스 격리를
   확인하며 캐시나 사용자 식별자를 출력하지 않는다. 기존003 점검에 보관 조건을 합치지 않는다.
   PostgreSQL 행 잠금에는 identity.users/user_roles/sessions의 SELECT·UPDATE 권한도 필요하므로
   기존 권한을 별도로 확인한다. 새 마이그레이션은 이 identity 권한을 추가하거나 확대하지 않는다.
4. 별도 승인으로 보관 전용 32바이트 임의 키를 만들고 기존 플랫폼 계정만 읽을 수 있는
   Secret Manager의 고정 버전에 등록한다. 표준 base64로 서버에 주입하며 시크릿 원문은
   터미널 출력·명령행 인자·채팅·.env 예시에 남기지 않는다. 로그인 client secret이나 Gemini
   키를 재사용하지 않는다. 실제 Secret 이름·버전/IAM·Cloud Run 주입은 운영자 작업이며 이번에 적용하지 않는다.
   플랫폼의 `MAILBOX_AUTH_ENCRYPTION_KEY`·`MAILBOX_AUTH_KEY_VERSION`으로 받으며 웹에는 키를 주입하지 않는다.
5. 초기 HTTP 경로는 웹·플랫폼의 `MAILBOX_AUTH_ENABLED=false`로 기본 차단된다. 기존 Web 앱에
   정확한 사서함 callback을 추가하고 플랫폼 `MAILBOX_AUTH_REDIRECT_URI`를 준비한 뒤 별도 승인으로
   배포·활성화한다. [HTTP/쿠키·CSRF 계약](../../contracts/platform/auth.md#사서함-초기-연결-http-기본-비활성-운영-미적용)을 따른다.
   소비자 getter·실제 Graph 검증은 별도이며 키/SQL 준비만으로 업무 수집을
   활성화하거나 KAN-80·81 전체 완료로 표시하지 않는다. 단일 키 버전이며 자동 키 회전은 없다.

새 연결/해제/재연결은 현재 세션과 DB 권한을 확인해야 한다. 작업용 조회·갱신은 인증한
서버 작업의 소유자를 사용하며, 브라우저가 보낸 ID를 그대로 넘기지 않는다. 외부 토큰 갱신
중 DB 잠금을 유지하지 않고 저장 단계에서 현재 연결 개정을 재검사한다. 상세 호출 계약은
[플랫폼 인증](../../contracts/platform/auth.md#사서함-보관-계층-kan-81-부분-구현-운영-미적용)을 따른다.

## 메일 수집 표 적용 (KAN-71)

현재 구현은 서버 내부 수집 함수이며 공용 환경에는 미적용이다. 아래도 별도 운영 승인 후 운영자 터미널에서
진행하며, 에이전트가 공유 DB를 변경하지 않는다.

1. 위 사서함 보관 준비 1~3단계(KAN-81 마이그레이션 적용과 `004_check_mailbox_vault.sql` 확인)가 끝났는지 확인한다.
   KAN-71 표는 `mailbox_auth.connections`를 참조하므로 그 뒤에만 적용할 수 있다.
2. 운영자 postgres 연결에서 `infra/migrations/platform/20261028_KAN-71_mail_sync.sql`을
   `psql -X -v ON_ERROR_STOP=1 -f ...`로 한 번 적용한다. 로컬은 `python3 scripts/workspace.py migrate platform 20261028_KAN-71_mail_sync.sql`이다.
   전체가 한 트랜잭션이라 KAN-81 표가 없거나 수집 표가 이미 있으면 오류로 멈추고 아무것도 남기지 않는다.
   이때 순서를 바로잡아 다시 적용하며, 이미 적용된 DB에 반복하지 않는다.
3. 적용 뒤 `integration` 스키마에 `mail_sync_runs`·`mail_sync_cursors`·`mail_messages`·`mail_attachments` 네 표가 있고,
   `platform_app`은 SELECT·INSERT·UPDATE만, `cs_app`·`tft_app`은 권한이 없는지 읽기 전용으로 확인한다.
   이 확인용 검사 SQL은 아직 없다.
4. 연결 행은 수집 표가 참조하므로 지우지 않는다(해제는 `state='disconnected'`). 참조가 있는 연결의 DELETE는 FK로 거부된다.

표 적용만으로 수집이 켜지지 않는다. 수집을 부르는 워커·정기 실행은 KAN-72, 운영 객체 저장소는 후속 작업이다.
상세는 [메일 수집 계약](../../contracts/platform/mail.md)을 따른다.

## 메일 발송 표 적용 (KAN-74)

위 메일 수집 표 적용(KAN-71)이 끝난 뒤, 같은 방식으로 `infra/migrations/platform/20261028_KAN-74_mail_send.sql`을
한 번 적용한다. 로컬은 `python3 scripts/workspace.py migrate platform 20261028_KAN-74_mail_send.sql`이다.
`integration.mail_send_intents`·`mail_send_events` 두 표가 생기고, `platform_app`은 의도에 SELECT·INSERT·UPDATE,
이력에 SELECT·INSERT만 가진다(이력 수정·삭제 불가). `identity.users`와 `mailbox_auth.connections`를 참조한다.
표 적용만으로 발송이 켜지지 않는다. 상세는 [메일 수집 계약](../../contracts/platform/mail.md)의 발송 절을 따른다.

근거: [PostgreSQL 스키마 권한](https://www.postgresql.org/docs/17/ddl-schemas.html), [생성자별 기본 권한](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html), [Cloud SQL 사용자 생성](https://docs.cloud.google.com/sql/docs/postgres/create-manage-users).
