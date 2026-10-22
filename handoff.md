# 다음 세션 인수인계

2026-10-22 기준. 작업 저장소는 /home/canae/ls-final-project다. 먼저 AGENTS.md와 CONTRIBUTING.md를 읽는다. 과거 셋업 보고서와 일정 사본은 삭제했으며 변경 이력은 Git, 작업 상태는 Jira에서 확인한다.

## 시작할 때 읽을 문서

- [공통 개발 온보딩](docs/team-start.md): 설치부터 로컬 실행 및 첫 PR까지의 단일 원본
- [노션 온보딩과 팀원별 안내](https://www.notion.so/401910042c3781ed9768f13ef422a4f8): 자기 첫 티켓과 담당 경계
- [Jira KAN 백로그](https://lsteam4.atlassian.net/jira/software/projects/KAN/boards/1/backlog): 최신 상태 및 선행 조건

아래는 필요한 작업에서만 참고한다. 기능 개발 시작 전에 모두 읽을 필요는 없다.

- 설계 및 선택 근거: [의사결정](docs/decisions.md), [아키텍처](docs/architecture.md)
- 계약 변경: [데이터 계약](docs/data-contract.md), [API 규약](docs/api-contract.md), [계약 수정 안내](contracts/README.md)
- 인프라 담당: [서비스별 CI와 CD](docs/service-delivery.md), [운영 확인](docs/operations-basics.md)
- 실제 모델 및 메일 연동: [외부 연동 확인](docs/integration-checks.md), [AI 클라이언트](docs/ai-client.md)
- 일정 원본: [현재 WBS](https://docs.google.com/spreadsheets/d/11X85xRDncd_xg3EPhcoke6dhF1fhTByKzhpRxLk6ow8/edit#gid=1680447802)

## 작업 방식

- 기본 브랜치는 dev. 최신 origin/dev에서 KAN 번호 작업 브랜치를 만들고 dev 대상 PR을 Squash merge한다.
- dev → main 배포와 main → dev 동기화 PR은 merge commit을 사용한다. 장기 브랜치 삭제나 강제 push를 하지 않는다.
- main의 정확한 SHA에 대한 CI 성공 뒤에만 변경 서비스가 배포된다. dev push는 공용 환경을 배포하지 않는다.
- 수동 배포는 --ref main을 지정한다. GCP OIDC 신뢰 조건은 main과 배포 워크플로로 제한돼 있다.
- GitHub 요금제 제약으로 보호 규칙을 활성화하지 못했다. 문서의 PR 규칙을 서버에서 강제한다고 보고하지 않는다.
- Jira의 KAN 최종 PR dev 병합 시 자동 완료 규칙을 생성하고 ENABLED 및 프로젝트 범위를 확인했다. 제목의 `[완료 KAN-번호]`와 소스 브랜치 번호가 현재 티켓과 일치하며 대상이 dev인 이 저장소 PR만 완료한다. 중간 PR과 dev/main 통합 PR은 제외한다. 첫 실제 병합 이벤트 검증은 남아 있다.
- 사용자가 dev → main 배포를 맡는다. 화면 순서는 docs/service-delivery.md의 평소 배포 절을 따른다.
- 초기 셋업의 동료 검토 생략은 사용자 승인에 따른 과거 예외다. 이후 기능 개발에는 일반 검토 절차를 적용한다.
- GCP 리소스 및 IAM 변경은 사용자가 터미널에서 실행하고 에이전트는 읽기 전용으로 확인한다.
- .env와 개인 설정 및 data를 보존한다. data와 OpenDesign 원본을 수정하지 않으며 정답 누설과 FLT-042 인용, 최종 test 597건 반복 사용을 금지한다.
- 담당과 일정, 원본 WBS 서식을 임의로 바꾸지 않는다. 사용자에게 이미 완료한 인증이나 셋업을 다시 요구하지 않는다.

## 실제 완료한 기반

Cloud SQL PostgreSQL 17과 pgvector 및 FTS, 서비스별 DB 계정과 스키마, Storage와 Secret, 독립 FastAPI 앱 3개 및 웹을 구성했다. Cloud Build 및 OIDC 기반의 서비스별 CI/CD와 실제 배포를 검증했다. 새 로컬 환경에서도 설치와 DB, 세 API 및 Vite 프록시, 네 시연 계정의 세션과 로그아웃, 계약 생성과 fake 평가를 확인했다.

아스트라 독립 검토로 Compose의 SERVICE_NAME 빌드 인자 누락을 수정했다. Compose 변경, mock-only 계약 변경 및 공유 agent/engine 검사 누락도 보완했다. 수정본을 재검토했고 scripts 38건, infra 21건, PR 자동화 14건과 원격 CI를 통과했다. 기존 환경과 별도인 검증 컨테이너 및 볼륨은 정리했다.

- 로컬 셋업 수정 커밋: ceae3959f49993e3ca4e5470b04d402e53b14328 (dev에만 반영)
- [앱 CI 39723152910](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39723152910): 전체 성공
- [협업 검사 39723152994](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39723152994): 성공
- 마지막 확인한 공용 배포 소스: e683910a9615fa255c3aefc90963a16a0bb514c2. dev 문서 및 로컬 셋업 수정은 아직 이 공용 배포에 포함되지 않는다.

공통 온보딩과 김나영, 오새결, 김종하의 노션 안내를 수정했다. 이름별 표 118건의 작업 링크와 날짜를 보존했고 원본 WBS와 Jira 담당 및 일정은 변경하지 않았다. 설치 안내를 이름별 문서에 복제하지 않는다.

## 외부 연동의 확인 범위

- 모델 기본 경로를 Gemini API와 API 키 방식으로 변경했다. 기존 키의 gemini-3.6-flash 합성 영어·한국어 2건이 실제 PASS이며 스키마와 토큰을 기록했다. 사용자가 기존 gemini-api-key:1 보존 및 cs/tft-runtime의 Secret 접근 권한 추가를 실행했고 읽기 전용 IAM 조회로 확인했다. Gemini API 공통 임베딩 합성 1건도 실제 성공했으며 3072차원을 관측했다. 코드와 배포 설정은 dev에 반영하며 실제 Cloud Run 전환은 사용자의 다음 main 배포 후 확인한다. Vertex 어댑터는 선택형 호환 코드이고 팀원 ADC 준비는 필요 없다. 과거 Vertex IAM은 이번에 변경하지 않았다. 최종 업무 모델과 임베딩 차원은 미확정이다.
- 개인 Microsoft 계정의 MSAL 로그인 및 Graph 읽기, sendMail HTTP 202와 보낸편지함 저장을 확인했다. 동일 합성 메일의 정크메일함 수신은 사용자 화면으로 확인했다. 스크립트의 정크 폴더 조회 보완 후 실제 API 재실행은 하지 않았다. 추가 발송을 다시 요구하지 않는다.
- 학교 테넌트 관리자 동의는 막혀 있다. 개인 계정 대안으로 검증했으며 콜백 localhost:8765/callback은 SPA가 아닌 Web 등록이 필요하다. 제품의 OAuth, 토큰 갱신과 업무 메일 기능 완료는 아니다.
- 백업 설정 및 수동 백업 두 건의 성공을 확인했다. 복구 훈련은 후속이다.

## 다음 개발과 Jira

온보딩을 단순화해 첫 개발 작업은 김나영 KAN-77, 오새결 KAN-84, 김종하 KAN-64로 안내한다. KAN-77 선행 KAN-57과 KAN-84 선행 KAN-82가 2026-10-22 조회에서 미완료여서 착수 시 함께 확인한다. Jira 담당·일정·상태는 변경하지 않았다. AGENTS와 Claude 지침은 현재 실명 담당 및 기능 경계로 맞췄고 개인 PC 절대경로를 필수 조건에서 제거했다.

사용자 설명 원고는 오새구리 워크스페이스의 [개발 설명회](https://www.notion.so/401910042c3781ba8e37e7a9f640709a) 비공개 페이지에 저장했다. 구조와 구현 상태, 담당 간 결과 전달, 첫 티켓 및 Git 규칙을 10~15분 순서로 설명한다. 원래 지정한 개인 워크스페이스는 현재 앱 연결과 달라 사용자 요청으로 저장 위치를 바꿨다. 팀원용 설치 문서는 기존 공통 안내 하나로 유지한다.

배포 및 시연 목표는 11월 3일, 발표는 11월 10일이다. 스프린트는 10/21~10/27 기반과 평가, 10/28~11/3 기능과 통합, 11/4~11/10 안정화와 발표 준비다. 현재 상태와 선행 관계는 Jira 원문을 조회한다.

- 완료 반영: KAN-60/62/63/65/66/67/68/76. 결과 근거는 해당 Jira 댓글에 있다.
- 미완료 범위 유지: KAN-55/56 계약, KAN-64 라우팅, KAN-69 비동기 처리, KAN-70 추적, KAN-84 과제별 모델 인터페이스, KAN-86 전체 평가 실행기.
- 업무 API는 일부 501 stub이며 프론트는 개발 연결 확인 화면이다. 실행 기반을 제품 전체 완성으로 보고하지 않는다.
- 다음은 담당별 계약 확장, 제품 인증과 권한 및 메일, C/S와 TFT 업무 기능, Tasks 처리 및 OIDC와 SSE, 평가 Jobs 및 결과 화면이다. 상세 모니터링과 복구 검증, 버전 및 태그 운영도 후속이다.
- 실제 인프라 수행은 오새결이며 기존 WBS의 공식 역할 배정은 그대로다. 최신 담당과 검토자는 Jira 및 이름별 노션을 확인한다.

## 클라우드와 후속 운영 확인

프로젝트 proj-skkuls0106-183255377938, 번호 1015616094444, 계정 skkuls0106@skkukdp.re.kr, 리전 asia-northeast3다. DB는 team4-sql의 team4이며 공용 웹은 https://team4-web-gew6ci4pgq-du.a.run.app/ 이다. 백엔드 익명 접근은 차단한다. 최신 리비전 및 빌드 근거는 서비스별 배포 문서에서 확인한다.

이전 RFI 버킷의 복구 보존 만료 후 부재 확인이 2026-10-28 20:51:25 KST 이후 남아 있다. 예약 작업은 만들지 않았다. 상세 식별자와 비용 확인 한계는 운영 문서에 보존했다. 현재 team4 리소스를 다시 중단하거나 삭제하라는 뜻은 아니다.

Atlassian 사이트는 lsteam4.atlassian.net, cloudId는 e0304739-9601-430a-95dc-13b4ea50240e다. 기존 연결이 있으면 재설치나 재로그인을 요구하지 않는다. /tmp 파일은 임시 자료이며 문서와 Jira 대신 상태 기준으로 사용하지 않는다.
