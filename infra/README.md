# 인프라 담당 안내

대상은 `proj-skkuls0106-183255377938`, 계정 `skkuls0106@skkukdp.re.kr`, 서울 `asia-northeast3`다. 담당은 오새결이다. GCP 변경은 담당자 터미널에서 수행하고 에이전트는 읽기 전용으로 확인한다. 일반 팀원은 [공통 온보딩](../docs/team-start.md)만 실행한다.

## 현재 구성

| 대상 | 구성 |
| --- | --- |
| 앱 | 공개 team4-web, 비공개 team4-cs·team4-tft·team4-platform |
| DB | Cloud SQL team4-sql / PostgreSQL team4, 서비스별 스키마·실행 및 마이그레이션 계정 |
| 비밀값 | cs-db-password·tft-db-password·platform-db-password, gemini-api-key 버전 1 |
| 이미지·배포 | 서울 Artifact Registry team4, Cloud Build, GitHub OIDC와 main 전용 CD |
| 문서 처리 큐 | rfp-document-processing. 동시 2·초당 2·최대 시도 5회, backoff 10~300초 |
| 백업 | 서울 자동 백업 7개 보존, 삭제 보호. 실제 백업 성공 확인 |

DB Secret은 해당 실행 계정에만, Gemini Secret의 새 직접 권한은 C/S·TFT에 부여한다. 비밀값을 읽어 출력하거나 이미지·Git에 넣지 않는다. 기존 Secret 버전은 보존하며 키 회전은 별도 작업이다.

큐와 TFT의 작업 등록 권한은 준비돼 있다. 문서 처리 핸들러·OIDC·작업 상태·멱등성은 KAN-69 및 기능 티켓에서 연결한다. 큐 존재를 문서 처리 완료로 보지 않는다.

## 작업별 안내

- 평소 배포와 main 동기화: [서비스별 CI와 CD](../docs/service-delivery.md)
- 이미지 수동 빌드·비공개 배포 점검: [Cloud Build 안내](cloud-build.md)
- 새 DB 최초 준비·계정 정책: [SQL 안내](sql/README.md)
- 새 업무 테이블: [마이그레이션 안내](../docs/team-start.md#필요할-때-참고-db-테이블-추가)
- 로그·백업·운영 담당 후속 확인: [운영 확인](../docs/operations-basics.md)
- 배포 뒤 확인·되돌리기·백업 복구 훈련: [배포 확인 런북](../docs/release-runbook.md)
- 실제 모델·메일 연결: [외부 연동 점검](../docs/integration-checks.md)
- 기존 공용 Microsoft 인증 준비: `prepare_microsoft_auth.py`의 기본 미리보기와
  [운영자 적용 순서](../docs/integration-checks.md#운영자-터미널에서-제품-인증-준비)를 따른다.
  운영자의 `--apply`도 인증 비활성 상태까지만 준비한다.

## 새 환경을 만드는 경우에만

기존 공용 환경에 반복 실행하지 않는다. 새 프로젝트나 자원 재구성이 필요할 때 대상 프로젝트·권한·기존 리소스를 확인한 뒤 도구를 선택한다.

| 도구 | 용도 |
| --- | --- |
| register_db_secrets.py | 로컬 CS_DB_PASSWORD·TFT_DB_PASSWORD·PLATFORM_DB_PASSWORD를 서비스별 Secret으로 등록. 기존 값·버전은 덮어쓰지 않음 |
| verify_cloud_db.sh | 서비스별 Cloud Run 검증 Job으로 실제 Secret·DB 접속 확인 |
| setup_cloud_build.sh | 이미지 생성 계정·저장소·빌드 소스 버킷 준비 |
| setup_mail_bucket.sh | 메일 원문·첨부 객체 버킷 준비, platform-runtime에 객체 생성·읽기만 허용(KAN-71 후속) |
| setup_rfp_bucket.sh | RFP 원문 객체 버킷 준비, tft-runtime에 객체 생성·읽기만 허용(KAN-188). 사용자가 직접 실행한다 |
| setup_worker_jobs.py | C/S·RFP worker 실행 계정·권한·전송 주기·서비스 설정(KAN-159). `prepare`·`grant`·`enable` 단계, 기본 미리보기, `--apply`만 바꾼다. `enable`이 실제 처리와 모델 호출을 켠다 |
| deploy_workers.py | worker 이미지 빌드 결과로 Cloud Run Job 배포(KAN-159). 기본 미리보기, `--apply`. 이미지는 `python3 infra/release.py build --service tft-worker`로 만든다. 자동 배포에 들어가지 않는다 |
| setup_service_delivery.sh | 실행·배포 계정과 저장소 main 전용 GitHub OIDC 준비 |
| register_gemini_secret.py | Gemini Secret 준비 및 C/S·TFT 접근 권한 연결 |

각 스크립트의 옵션을 확인하고 미리보기가 있으면 먼저 검토한다. 업무 개발을 시작하기 위한 설치 명령으로 배포 도구를 실행하지 않는다.
