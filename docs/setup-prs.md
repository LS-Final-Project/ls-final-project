# 초기 셋업 반영과 실행 순서

2026-10-21 사용자 결정으로 현재 초기 셋업은 PR 및 동료 검토 없이 main에 직접 push한다. 이미 만든 티켓별 커밋을 보존해 main에 통합했다. 과거 Draft PR 3~9는 작업 이력이며 이후 실행을 위해 PR을 열거나 Draft를 해제할 필요가 없다. 이후 업무 기능 개발의 일반 PR 규칙은 유지한다.

| 티켓 | 반영 내용 | 남은 조건 |
| --- | --- | --- |
| KAN-60 | Git과 Jira 및 PR 협업 자동화, 초기 셋업 예외 | 일반 업무 기능의 검토 규칙 유지 |
| KAN-62 | 교육장 GCP 점검과 비용 정리 이력 | 모델 실제 호출 제약 및 서비스 할당량 확인 |
| KAN-65 | 서비스별 DB SQL과 로컬 PostgreSQL 17, pgvector 및 FTS 검증 | 후속 업무 마이그레이션과 프로젝트 권한 테이블 |
| KAN-66, KAN-69 | DB Secret 도우미, 연결 검증과 큐 안내 | 백업 실제 결과, 문서 작업과 OIDC, DB 작업 상태 및 SSE 재접속 |
| KAN-63 | 독립 앱 3개, 실제 DB ready와 서비스별 계약 stub | 후속 업무 기능, 내부 서비스 호출과 실제 권한 검사 |
| KAN-67 | 전용 계정 및 버킷, 실제 Cloud Build 및 이미지 업로드, 계약과 프론트 검사 Actions | 후속 코드 변경의 빌드 및 배포 운영 |
| KAN-68 | 세 API 첫 비공개 배포, 인증 health와 익명 차단 확인 | 프론트 호스팅, 서비스 간 호출과 독립 재배포 검증 |
| KAN-64 | React, Vite, TS와 Query 골격 및 연결 확인 화면 | 실제 업무 화면과 호스팅 |
| KAN-78, KAN-79 | 로컬 users와 역할, 세션 및 production 개발 인증 차단 | 프로젝트 권한, 실제 OAuth 및 업무 범위 검사 |
| KAN-84, KAN-86, KAN-87 | 합성 smoke 제공자, JSON 및 HTML 기록과 사용량 필드 | 실제 생성 성공, 과제별 평가와 가격표 및 결과 관리 API |
| KAN-55, KAN-56 | 서비스 및 기능별 모델, OpenAPI와 예시 분리, 생성 타입 소비자 검사 | 실제 업무 DTO 확장 및 호환성 검증 |

기술 조건이 남은 Jira 작업은 진행 중으로 유지한다. 코드 게시 완료를 실제 클라우드 실행 완료로 보고하지 않는다. 원본의 .env와 data는 커밋하거나 이동하지 않았다.

## 2026-10-22 개발 시작 환경

[팀원 실행 안내](team-start.md)의 최초 설치와 실행, [계약 수정 안내](../contracts/README.md)의 자기 서비스 생성 절차를 따른다. 로컬 사용자와 세션은 개발 전용이며 실제 OAuth는 아직 미구현이다. 평가 smoke의 합성 JSON 및 HTML 기록은 가능하지만 실제 생성 모델 호출은 404 또는 429로 성공하지 않았다.

## 2026-10-22 클라우드 실행 결과와 다음 단계

사용자가 Cloud Build 준비, 실제 빌드와 비공개 배포를 실행했다. 빌드 401a8afb-f9b9-4a51-878e-2a96fc373e99는 main f042981 소스로 성공했고 결과 digest 3개를 Cloud Run에 배포했다. GitHub의 Application checks와 Collaboration checks도 성공했다.

세 서비스의 인증 health 200, 익명 health 403, production 개발 계정 경로 404를 확인했다. 첫 DB ready 503 이후 사용자가 Cloud SQL vector 확장을 적용했고, 세 서비스 모두 인증 ready 200과 database/vector/fts=true를 확인했다. 세부 결과는 [인프라 실측 기록](infra-preflight.md)을 따른다. 프론트 호스팅 및 실제 Microsoft 로그인, 플랫폼 중계와 서비스 간 인증은 후속이다.

빌드와 배포 명령 및 확장 SQL은 [Cloud Build 및 배포 안내](../infra/cloud-build.md)를 따른다. 이미 성공한 초기 빌드 및 배포를 이유 없이 반복할 필요는 없다.

기존 검증: Docker에서 Ruff 및 포맷, mypy 15개 파일, 백엔드 14건과 OpenAPI 일치 검사 통과. DB Secret 도우미 4건 및 배포 계획 3건 통과. Git 연동 12건과 PR 자동화 12건 통과. 실제 소스 업로드 목록에서 .env, data, Git과 인프라 도우미 제외를 확인했다. Cloud Build SDK 파서 및 가짜 gcloud로 커밋 소스와 임시 파일 정리 및 IAM 인자를 확인했다.

2026-10-22 추가 검증: Docker 테스트 23건, mypy 34개 파일, 서비스별 OpenAPI 및 모의 예시와 프론트 타입 검사, 프론트 production 빌드를 통과했다. 커밋 원본의 새 폴더에서 의존성 최초 설치와 별도 새 DB 초기화, 세 API readiness, 계정 세션 및 ACL 검사, 명시적 마이그레이션 적용까지 통과했다. 새 검증 환경은 종료했고 DB 볼륨과 원래 개발 환경은 보존했다.
