# 다음 세션 시작 안내

작업 저장소는 이 레포다. 먼저 [AGENTS](AGENTS.md)와 현재 git status·브랜치 및 맡은 Jira 티켓을 확인한다. 사람의 시작 경로는 [공통 온보딩](docs/team-start.md), 역할별 상세는 [노션 개발 온보딩](https://www.notion.so/401910042c3781ed9768f13ef422a4f8)이다.

## 현재 출발점

최신 최종 DOCX/PDF를 개발 기준으로 사용하며 기획서 자체는 수정하지 않는다. 분류 출력은 secondary_category를 포함한 5개이며 [C/S 계약](contracts/cs/tickets.md)을 따른다. 출처의 두 축은 [데이터 계약](docs/data-contract.md), 평가 계산법·분모·안전 조건은 [평가 명세](docs/evaluation.md)가 기준이다. 기존 4필드 기록은 이전 결정이며 이번 사용자 결정으로 대체한다. 담당·개발 범위는 유지한다. 명세 보완과 실제 기능 구현·데이터 검수·품질 합격은 별개다.

- 로컬 DB·API 3개·프론트, 시연 계정 4개와 DB 세션, 계약 생성 및 합성 평가 실행 사용 가능.
- 공용 GCP의 DB·Storage·Secret, 공개 웹과 비공개 API 3개, 서비스별 CI/CD 사용 가능. 업무 화면·API는 구현 단계이며 일부 목록은 stub이다.
- Gemini API 키 방식이 기본이며 초기 생성 모델은 gemini-3.6-flash다. 최종 모델과 임베딩 차원은 평가로 선택한다. dev의 새 설정은 다음 main 배포 후 공용 환경에서 확인한다.
- 개인 Outlook의 로그인·Graph 읽기·실제 송수신 사전 검증 완료. 제품 OAuth·토큰·세션 및 메일 통합은 구현 대상이다. 완료한 사전 검증을 새 온보딩 조건으로 반복하지 않는다.

## 담당과 첫 작업

2026-10-25 담당 변경: 평가 API KAN-90과 평가 화면 KAN-137·138·139는 사용자 결정으로 오새결이 맡는다. 아래 최초 온보딩 표보다 이 변경과 최신 Jira가 우선한다. KAN-85 완료 이력·TFT·다른 프론트 기능은 변경하지 않았다.

| 담당 | 시작점 | 담당 범위 |
| --- | --- | --- |
| 김나영 | KAN-102 계약·합성 예시 준비 | C/S API·규칙, 메일, 사용자 등록·업무 권한 KAN-78·79, 통합 회귀 |
| 오새결 | KAN-77 인증 경계 | KAN-77·80·81 인증·토큰·서버 세션, 인프라, 데이터·검색·AI·평가 |
| 김종하 | KAN-64 화면 라우팅 | 프론트, TFT API, 플랫폼 평가 관리 API와 화면 |

최신 상태와 선행 합의는 [Jira](https://lsteam4.atlassian.net/jira/software/projects/KAN/boards/1/backlog), 날짜·공수는 [WBS](https://docs.google.com/spreadsheets/d/11X85xRDncd_xg3EPhcoke6dhF1fhTByKzhpRxLk6ow8/edit#gid=1680447802)를 따른다. 계약 초안 준비와 티켓 전체 완료를 구분한다. 개발·배포 완료는 11/3, 발표는 11/10이다.

## TFT 정리(KAN-181)

2026-10-28 범위 변경(TFT 팀 구성 → RFP 자사 대응·협력사 보완)으로 쓰지 않게 된 TFT 인력 구성 코드를 지웠다. 새 RFP 작업은 KAN-182~192(A01~A12)다.

- 유지: TFT 서비스(compose·cloudbuild·Dockerfile·설정·준비 검사), 프로젝트 목록·상세 GET 2개(`backend/api/tft/projects/`), 접근 검사(`projects/access.py`: manager 관리 대상·책임자·담당 sales), 상세의 RFP 요구사항(문서·조항·페이지·발췌), 중계 허용표의 TFT 2행(`frontend/server.mjs`, `backend/api/platform/relay.py`), 화면 `/tft/projects`·`/tft/projects/:id`, [프로젝트 계약](contracts/tft/projects.md).
- 제거: 부서 인력요청(`staffing_requests/`, 발행 권한 `authz.py`), 후보 추천·경험·역량·가용성(`recommendations/`), 검증·공백 판정(`verification/`), 구성안·책임자 결정(`decisions/`), 상세의 역할·요청서·추천·구성안·책임자·확정 필드, 인력요청을 받은 부서의 dept_staff 조회 범위, 계약 4개, 테스트 5개, 화면 5개(역할 요청서·내 부서 요청·부서 요청·구성안 검토·구성안 편집)와 라우트·메뉴, `docs/tft-screens.md`, CI `tft-database` 잡.
- 복원: 제거 직전 dev는 `1d17f84ef4f2726a3e365862b0832105315d6270`이다. `git show 1d17f84:<경로>` 또는 `git checkout 1d17f84 -- <경로>`로 꺼낸다. 원 PR은 KAN-122 #31·#68, KAN-123 #37, KAN-125 #38, KAN-127 #39, KAN-130 #45, KAN-134 #32, KAN-135 #40, KAN-136 #67이며 해당 Jira 티켓은 scope-dropped로 닫혀 있다.
- DB와 계정은 그대로 둔다. `infra/migrations/tft`의 5개 파일과 테이블은 공용 DB 적용 이력을 확인하지 않아 DROP 마이그레이션을 만들지 않았다(되돌릴 수 없고 공용 DB 적용은 사용자 승인 대상). `tests/platform/test_business_relay.py`가 이 마이그레이션을 계속 적용·검사한다. `tft_app`은 프로젝트 조회가 쓰고, platform 마이그레이션과 `infra/sql/001·002·004`가 이 이름을 권한 회수·검증 대상으로 쓰므로 지우면 그 파일들이 깨진다.

## 작업 규칙

- 기본 dev에서 KAN 작업 브랜치를 만들고 dev PR을 검사·검토 후 Squash merge한다. dev push는 배포하지 않는다.
- dev → main 배포와 main → dev 동기화는 merge commit이다. 사용자가 배포 판단을 맡는다. main의 동일 SHA CI 성공 뒤 변경 서비스만 배포한다.
- 최종 PR 제목의 `[완료 KAN-번호]` 하나가 Jira 완료 신호다. 중간 PR과 dev/main 통합에는 넣지 않는다. 병합 후 실제 전환을 확인한다. 규칙은 활성화돼 있으며 첫 실제 최종 PR 이벤트 확인은 남아 있다.
- 브랜치 보호의 서버 강제는 적용되지 않는다. CI와 최신 head/base SHA의 AI 코드 리뷰 결과를 직접 확인한다. 팀원을 자동 지정하지 않으며 P0~P2 지적은 수정 후 다시 검토한다.
- AGENTS의 최소 구현·실제 검증 규칙과 공통 안내의 범위별 검사 명령을 적용한다. 마지막 코드에 대한 명령·결과·미실행 이유·CI 링크를 PR과 Jira에 남긴다.
- GCP 리소스·IAM 변경은 사용자 터미널에서 실행한다. 에이전트는 읽기 전용으로 확인한다. 기능 작업에서 재셋업·전체 재배포를 요구하지 않는다.
- `.env*`, 사용자 변경, `data/`와 OpenDesign 원본을 보존한다. 정답 누설·FLT-042 인용·최종 test 597건 반복 사용을 금지한다.

## 필요할 때만 참고

[아키텍처](docs/architecture.md)와 [선택 근거](docs/decisions.md), [계약 수정](contracts/README.md), [AI 클라이언트](docs/ai-client.md), [외부 연동 점검](docs/integration-checks.md), [서비스별 배포](docs/service-delivery.md), [운영 확인](docs/operations-basics.md).

GCP 대상은 `proj-skkuls0106-183255377938`, 계정 `skkuls0106@skkukdp.re.kr`, 리전 `asia-northeast3`, DB는 `team4-sql/team4`다. 공용 웹은 https://team4-web-gew6ci4pgq-du.a.run.app/ 이다. 운영 담당의 버킷 보존 만료 후 확인은 운영 문서에 남아 있다. 변경 이력·검증 근거는 Git·PR·Jira에서 확인한다.
