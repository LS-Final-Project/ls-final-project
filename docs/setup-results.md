# 셋업 결과와 Jira 반영

기준일: 2026-10-22. 사용자의 결과 반영 요청에 따라 기존 완료 조건과 구현 및 검증 근거를 대조했다. 검토한 15건 중 8건을 완료로 변경하고 7건은 기존 상태를 유지했다. 이는 점검 대상 15건의 분류이며 전체 프로젝트 진행률이 아니다.

15건 모두 확인한 결과와 남은 범위, 코드 및 검증 링크를 Jira 댓글로 기록했다. 이후 Jira를 재조회해 상태와 담당자 및 마감일과 라벨을 대조했고 불일치는 없었다. 스프린트와 선행관계는 수정하지 않았다. WBS 시트는 이번에 수정하지 않았다. 초기 셋업 동료 검토 생략은 기존 사용자 결정이며 후속 기능 개발의 검토 규칙은 유지한다.

## 완료 처리한 작업

| 작업 | 담당 | 반영 전 | 근거와 완료 범위 | Jira 결과 |
| --- | --- | --- | --- | --- |
| [KAN-60](https://lsteam4.atlassian.net/browse/KAN-60) [D08] WBS 작업, Jira 번호, PR 추적 연결 | 오새결 | 진행 중 | WBS 계획 118건을 실제 Jira 키와 선행관계 및 완료 조건에 매핑했고, 이름별 온보딩에서 현재 담당자와 일정 불일치를 대조했습니다. GitHub for Atlassian 연결과 실제 PR의 Jira 번호 및 링크 자동 보완, main 병합과 원격 협업 검사를 확인했습니다. | [댓글 10043](https://lsteam4.atlassian.net/browse/KAN-60?focusedCommentId=10043) |
| [KAN-62](https://lsteam4.atlassian.net/browse/KAN-62) [P01] 교육장 GCP 서비스, IAM, 할당량 확인 | 오새결 | 진행 중 | algomon이 아닌 교육장 proj-skkuls0106-183255377938에서 Cloud Run/SQL/Tasks/Jobs/Build/Storage 접근과 실행 기반을 확인했습니다. 실제 DB 검증 Jobs와 이미지 빌드 및 배포가 성공했습니다. Developer API 월간 지출 한도 차단을 식별했고 별도 Vertex global 생성 및 us-central1 임베딩 호출과 C/S 및 TFT 실행 신원의 생성 PASS를 확인했습니다. | [댓글 10044](https://lsteam4.atlassian.net/browse/KAN-62?focusedCommentId=10044) |
| [KAN-63](https://lsteam4.atlassian.net/browse/KAN-63) [P02] C/S, TFT, 플랫폼 FastAPI 앱과 공통 패키지 실행 골격 | 오새결 | 진행 중 | C/S, TFT와 플랫폼의 독립 entry/config/health/OpenAPI 및 공통 Python 패키지와 의존성 lock을 main에 반영했습니다. 서비스별 Docker CI와 타입 및 계약 검사, 네트워크 없는 SDK import, 실제 배포의 인증 ready 및 DB/vector/FTS 연결을 확인했습니다. | [댓글 10045](https://lsteam4.atlassian.net/browse/KAN-63?focusedCommentId=10045) |
| [KAN-65](https://lsteam4.atlassian.net/browse/KAN-65) [P04] 로컬 PostgreSQL, 초기 마이그레이션 | 오새결 | 진행 중 | 새 체크아웃과 빈 DB 볼륨에서 로컬 PostgreSQL 17 및 pgvector/FTS, 서비스별 계정과 스키마, 초기 identity 마이그레이션을 일회성 setup으로 구성했습니다. 세 API readiness와 CRUD, 스키마 격리 및 DDL 거부 행렬, 명시적 로컬 migrate 명령을 검증했습니다. | [댓글 10047](https://lsteam4.atlassian.net/browse/KAN-65?focusedCommentId=10047) |
| [KAN-66](https://lsteam4.atlassian.net/browse/KAN-66) [P05] Cloud SQL, Storage, Secret Manager 구성 | 오새결 | 진행 중 | 서울 Cloud SQL team4-sql 및 team4 DB, 서비스별 DB 계정과 스키마, 비공개 파일 버킷, 실행 계정별 Secret 연결을 구성했습니다. DB 검증 Jobs 및 실제 Cloud Run ready에서 DB/vector/FTS 연결을 확인했고 익명 백엔드 접근은 차단했습니다. 자동 백업, 서울 보관과 7개 보존 및 삭제 보호, 수동 백업 1792600511679와 1792600953011의 SUCCESSFUL을 확인했습니다. | [댓글 10048](https://lsteam4.atlassian.net/browse/KAN-66?focusedCommentId=10048) |
| [KAN-67](https://lsteam4.atlassian.net/browse/KAN-67) [P06] 컨테이너, Cloud Build 기본 CI | 오새결 | 진행 중 | lint/type/build/단위 및 계약 검사, 서비스별 Docker 이미지와 commit SHA/digest 추적, Artifact Registry 업로드와 별도 CD를 실제 검증했습니다. GitHub OIDC와 빌드/배포 실행 계정을 분리했고 커밋된 허용 소스만 업로드해 .env, data와 키를 제외했습니다. 네 서비스 최초 배포, 웹 단독 재배포, 공통 SDK 변경의 백엔드 3개 재배포가 성공했습니다. | [댓글 10049](https://lsteam4.atlassian.net/browse/KAN-67?focusedCommentId=10049) |
| [KAN-68](https://lsteam4.atlassian.net/browse/KAN-68) [P07] 통합 환경 웹, CS, TFT 첫 배포 | 김나영 | 해야 할 일 | 공용 웹 HTTPS 및 플랫폼 health/ready 중계 200, DB/vector/FTS 정상과 비공개 백엔드 3개의 인증 ready 및 익명 health 403을 확인했습니다. 네 서비스가 각각 독립 이미지와 CD 실행으로 배포됐고 후속 웹 단독 재배포에서는 백엔드 리비전이 유지됐습니다. 공통 SDK 변경 시에는 백엔드 3개만 갱신됐습니다. | [댓글 10050](https://lsteam4.atlassian.net/browse/KAN-68?focusedCommentId=10050) |
| [KAN-76](https://lsteam4.atlassian.net/browse/KAN-76) [U00] 학교 계정, 실제 앱 OAuth/Graph 조기 검증 | 김나영 | 해야 할 일 | 학교 테넌트의 관리자 동의 차단과 사용자 관리 권한 부재를 식별했습니다. 사용자가 진행한 개인 Microsoft 계정 대안에서 동일 앱의 Web 콜백, MSAL/PKCE 로그인 및 Mail.Read와 Mail.Send 동의를 확인했습니다. 사용자가 통제한 본인 주소로 probe 0e977472cfe7449a8f46a0e8526d7062를 실제 발송해 Graph HTTP 202 및 보낸편지함 저장을 확인했고 동일 메일의 정크메일함 수신을 사용자 화면으로 확인했습니다. | [댓글 10053](https://lsteam4.atlassian.net/browse/KAN-76?focusedCommentId=10053) |

## 기존 상태를 유지한 작업

| 작업 | 상태 | 남은 범위 | Jira 결과 |
| --- | --- | --- | --- |
| [KAN-55](https://lsteam4.atlassian.net/browse/KAN-55) [D03] 공통 ID, 데이터 계약, 소유 스키마 정의 | 진행 중 | docs/data-contract.md와 docs/architecture.md는 검토안입니다. 선행 범위 정합화와 업무별 필드의 계약 확정이 남아 있어 전체 완료로 처리하지 않습니다. | [댓글 10041](https://lsteam4.atlassian.net/browse/KAN-55?focusedCommentId=10041) |
| [KAN-56](https://lsteam4.atlassian.net/browse/KAN-56) [D04] OpenAPI, 오류, 작업, SSE 계약 작성 | 진행 중 | 업무 목록은 501 stub입니다. 작업 ID, 중복키와 페이지네이션, 오류 및 SSE의 구체 계약과 실행 가능한 계약 검증이 남아 있어 진행 중을 유지합니다. | [댓글 10042](https://lsteam4.atlassian.net/browse/KAN-56?focusedCommentId=10042) |
| [KAN-64](https://lsteam4.atlassian.net/browse/KAN-64) [P03] React, Vite, TS, Query 실행 골격 | 해야 할 일 | frontend/src/App.tsx는 개발 환경 확인 화면입니다. 완료 조건의 화면 라우팅과 업무 화면 간 이동이 아직 없어 현재 해야 할 일 상태를 유지합니다. 실행 기반은 준비됐으며 라우팅을 담당자가 이어서 구현합니다. | [댓글 10046](https://lsteam4.atlassian.net/browse/KAN-64?focusedCommentId=10046) |
| [KAN-69](https://lsteam4.atlassian.net/browse/KAN-69) [P08] Tasks, Jobs, 서비스 계정, SSE 기반 | 진행 중 | 실제 문서 처리 핸들러와 OIDC/invoker 연결, 작업 DB 상태와 중복 실행 및 재시도, 무인증 작업 차단과 SSE 재접속 검증이 남아 있어 진행 중을 유지합니다. DB 검증 Job의 성공을 평가 Job 구현 완료로 표시하지 않습니다. | [댓글 10051](https://lsteam4.atlassian.net/browse/KAN-69?focusedCommentId=10051) |
| [KAN-70](https://lsteam4.atlassian.net/browse/KAN-70) [P09] 구조화 로그, 오류, 요청ID, 추적 설정 | 해야 할 일 | 업무 ID, 작업 ID와 실험 ID를 연결한 구조화 로그 및 추적과 전체 경로의 민감정보 비노출 검증은 아직 없어 현재 해야 할 일 상태를 유지합니다. 기본 조회 안내를 상세 모니터링 구축 완료로 간주하지 않습니다. | [댓글 10052](https://lsteam4.atlassian.net/browse/KAN-70?focusedCommentId=10052) |
| [KAN-84](https://lsteam4.atlassian.net/browse/KAN-84) [V03] 모델 공급자, 과제별 호출 인터페이스 | 해야 할 일 | 문의 분류 및 리랭킹 등 모든 과제의 설정과 호출 인터페이스 분리 및 후보별 비교는 아직 남아 있습니다. 공통 SDK 연결을 전체 완료로 표시하지 않고 현재 해야 할 일 상태를 유지합니다. | [댓글 10054](https://lsteam4.atlassian.net/browse/KAN-84?focusedCommentId=10054) |
| [KAN-86](https://lsteam4.atlassian.net/browse/KAN-86) [V05] 공통 평가 실행기, 설정 로딩 구현 | 해야 할 일 | 과제별 채점과 비교, 실효 설정과 seed, 실패와 중단 및 건별 재개를 갖춘 전체 실행기는 아직 아닙니다. 가격표도 없어 비용은 미산정이며 현재 해야 할 일 상태를 유지합니다. Jobs와 결과 관리 화면은 KAN-90 등 후속 범위입니다. | [댓글 10055](https://lsteam4.atlassian.net/browse/KAN-86?focusedCommentId=10055) |

## 검증의 해석

- Microsoft는 사용자 개인 계정으로 실제 로그인과 Graph 읽기, HTTP 202 및 보낸편지함 저장과 정크메일함 수신을 확인했다. 학교 관리자 동의 성공이나 제품 세션 및 토큰 갱신 구현을 뜻하지 않는다. 수정한 정크메일함 검사 코드의 실제 API 재실행은 하지 않았다.
- 프론트는 실행과 빌드 및 배포 기반이 준비됐지만 현재 App.tsx에 업무 화면 라우팅이 없어 KAN-64를 완료 처리하지 않았다.
- 실제 백엔드 이미지 및 배포 검증은 [CI 39706740083](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39706740083)과 [CD 39706861091](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39706861091)를 근거로 한다. d1392df의 [최신 CI 39719172458](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39719172458)는 인프라 및 scripts 검사만 실행했고 정상 생략된 백엔드와 프론트 전체 검사를 다시 실행한 것은 아니다.
- d1392df의 [배포 실행 39719220331](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39719220331)은 계획 성공 후 모든 서비스의 배포를 생략했다. 이 실행을 새 이미지 배포로 표시하지 않는다.
- GCP 리소스와 IAM 변경, 실제 메일 추가 발송, 모델 추가 호출 및 data 수정은 이번 결과 반영에서 하지 않았다.

