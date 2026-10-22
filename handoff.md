# 다음 세션 인수인계

작성: 2026-10-21, 최신 갱신: 2026-10-22. 작업 저장소는 `/home/canae/ls-final-project`다. 먼저 AGENTS.md와 CONTRIBUTING.md를 읽고, 사람용 결정 요약인 [docs/decisions.md](docs/decisions.md)를 확인한다. 사용자와 확정한 결정, 구현 제안과 실제 클라우드 상태를 섞지 않는다.

사용자는 의사결정의 선택 근거를 명확하게 남기도록 추가 요청했다. decisions.md에는 선택 목적, 실제 비교한 대안, 감수하는 제약과 재검토 조건을 보강했다. 해당 문서의 '대화 근거'는 사용자가 밝힌 목적이나 선택 이력이고, '설계 해석'은 현재 선택에 대한 기술적 설명이다. 설계 해석을 사용자의 원문 발언이나 완료된 비교 실험으로 보고하지 않는다. 후속 변경에도 요구 또는 측정 근거와 계약 및 일정 영향을 함께 기록한다.

## 2026-10-22 온보딩 통합과 이름별 WBS 개발 안내

사용자는 Microsoft 연동을 이어서 진행하면서 팀원 온보딩 문서를 하나로 통일하고 노션에 이름별 WBS 개발 방법을 정리하도록 요청했다. 공통 설치부터 첫 PR까지의 원본은 docs/team-start.md 하나로 유지한다. README와 backend/README.md의 진입 안내를 이 문서로 연결하고 backend의 중복 설치 명령을 제거했다. 노션의 역할 문서에는 설치 명령을 복제하지 않고 담당 업무와 협업 순서 및 완료 조건을 넣었다.

노션 위치는 기존 최종 프로젝트 아래다. 루트 바로가기에서 공통 온보딩으로 연결되며 기존 페이지와 DB는 보존했다.

- 개발 온보딩: https://www.notion.so/401910042c3781ed9768f13ef422a4f8
- 김나영: https://www.notion.so/401910042c3781ecb002d9786de41392
- 오새결: https://www.notion.so/401910042c3781fcad57d0ed3aee8fdb
- 김종하: https://www.notion.so/401910042c3781408cedf75121232986

WBS는 gws CLI로 작업 상세 A1:V170을 읽고 Jira의 wbs-plan 118건 전체와 대조했다. 30개 완료 이력은 제외하고 예비시간 2건은 구분했다. 일정 불일치는 0건이다. 초기 셋업 9건만 이미 합의한 Jira 담당 오새결 및 WBS 담당 김나영 차이가 있다. 이름별 목록은 현재 Jira 실행 담당 기준 김나영 35건, 오새결 48건, 김종하 35건으로 작성했다. WBS의 일정과 완료 조건 및 Jira 선행 작업을 스프린트별 접는 표로 넣었다. 세 페이지를 다시 조회해 담당 티켓 누락 0건을 확인했다. 담당자, 일정과 상태 및 WBS 원본은 변경하지 않았다.

역할별 안내에는 이미 있는 셋업과 남은 기능, 첫 작업과 개발 순서, 실제 및 제안 경로, 계약 및 DB 마이그레이션과 검토 방법을 담았다. 플랫폼에서 김나영은 auth 및 mail, 김종하는 evaluations로 기능 폴더를 나누고 오새결의 룰 외 입력 및 검색과 AI 모듈을 구조화 계약으로 연결한다. 아직 없는 경로는 구현 제안으로 표시했다. WBS 원문에 남은 가칭과 두 백엔드 및 학교 테넌트 표현은 최신 결정과 구분했다. OpenDesign과 data는 변경하지 않았다. 노션 공유 알림은 보내지 않았다.

Microsoft: 사용자는 Entra 앱의 지원 계정 유형을 모든 조직 및 개인 계정 포함으로 설정했다고 보고했다. 이 설정을 다시 요청하지 않는다. 이후 Gmail 주소로 --personal-account --login을 실행해 OpenID discovery는 성공했지만 OAuth invalid_request가 발생했다. Graph 호출 전의 실패이며 구체 원인은 아직 미확정이다. Gmail 주소의 Microsoft 개인 계정과 Outlook 사서함 보유를 구분했고 사용자는 방금 Outlook 계정을 만들어 받은편지함이 보인다고 답했다. 방금 만든 Outlook 주소로 재로그인해 PASS 또는 AADSTS가 포함된 FAIL 행을 알려 달라고 요청한 상태다. 스크립트는 오류 원문을 노출하지 않고 OAuth 오류 enum, AADSTS 숫자와 실패 단계만 출력하도록 보완했다. 사용자가 로그인 창이 보이지 않고 즉시 같은 오류가 난다고 보고해 최신 FAIL 행의 AADSTS와 phase를 요청했다. 쿠키 없는 공개 인증 시작 요청은 login.live.com까지 302와 200으로 이어졌지만 이는 실제 로그인 검증은 아니다. 기존 세션 자동 선택을 줄이도록 로그인 요청에 prompt=select_account를 추가했다. 실제 로그인과 메일은 여전히 미검증이다. 기존 .env.integrations와 시크릿 및 학교 테넌트 설정을 보존했다. scripts/check_integrations.py에 --personal-account 옵션을 추가해 해당 실행만 consumers authority와 개인 계정 고정 테넌트를 사용한다. 공개 OpenID discovery는 실제 성공했고 학교 기본 모드 보존과 예상하지 않은 issuer 차단을 포함한 scripts 테스트 22건 및 Ruff가 통과했다. 이 옵션은 사전 점검용이며 production 인증 및 사용자 등록을 변경하지 않는다. 개인 계정의 Entra 앱 역할은 지원되지 않으므로 기존 DB 역할 정책을 유지한다.

사용자가 앱 설정을 완료하면 본인 터미널에서 `uv run --frozen python scripts/check_integrations.py microsoft --personal-account --login`을 실행한다. 실제 개인 Outlook 계정으로 로그인하고 PASS 또는 오류 코드만 확인한다. 시크릿과 토큰을 채팅에 받지 않는다. 이 스크립트는 읽기만 하고 발송하지 않으며 실제 발송은 통제된 수신자를 확인한 뒤 별도로 진행한다. GCP 변경이나 서비스 재배포가 필요한 변경은 이번에 없다.

## 2026-10-22 Vertex 공통 클라이언트와 평가 연결

사용자가 global 생성 성공 후 다음 작업 진행을 요청했다. backend/shared/ai.py에 명시적 Vertex OAuth 경로를 구현하고 google-genai 및 google-auth를 실행 의존성으로 옮겼다. 기존 버전은 변경하지 않았다. 기본은 교육장 프로젝트, global, gemini-3.6-flash, 출력 한도 1024이며 최종 업무 모델 확정은 아니다. Developer API 자동 폴백과 생성 자동 재시도는 없다. 실제 업무 프롬프트, 룰 및 인증 기능은 변경하지 않았다.

- 합성 영어와 한국어 2건의 실제 SDK 평가가 모두 JSON 스키마를 통과했다. artifacts/eval/c8af4a52-737e-41e7-beb1-8959ecf56dec.json 및 .html에 기록했다. 영어 지연 3200.51ms, 입력 70, 답변 5, 추론 121, 총 196토큰이고 한국어는 1602.21ms, 입력 71, 답변 5, 추론 125, 총 201토큰이다. 가격표가 없어 비용은 미산정이다. 로컬 ADC 신원으로 호출했으며 최종 test 597건을 사용하지 않았다.
- gemini-embedding-001은 Vertex us-central1에서 합성 1건 성공했다. 입력 10토큰과 32 billable characters 및 3072차원을 관측했다. 벡터를 출력하거나 저장하지 않았고 DB 및 production 차원은 변경하지 않았다. 생성과 임베딩 위치를 별도로 설정한다.
- 사용자가 infra/setup_vertex_access.sh --verify-runtime을 실행해 team4VertexInference 사용자 정의 역할을 생성했다. 읽기 전용 조회로 aiplatform.endpoints.predict와 serviceusage.services.use만 포함한 것을 확인했다. 대상은 cs-runtime과 tft-runtime이다. 첫 검증은 방금 추가한 임시 사용자 권한의 토큰 발급에서 RefreshError가 발생했고 임시 바인딩은 제거됐다. 이후 토큰 발급의 IAM_PERMISSION_DENIED만 최대 180초 기다리도록 수정해 사용자에게 재실행을 요청했다. 사용자가 수정 스크립트를 재실행해 약 70초 IAM 전파 대기 후 두 실행 신원 모두 생성 PASS와 STOP을 확인했다. C/S는 입력 4, 답변 1, 추론 60, 총 65토큰이고 TFT는 입력 4, 답변 1, 추론 67, 총 72토큰이다. 읽기 전용 조회로 모델 호출 역할 대상이 두 계정뿐이고 임시 검증 바인딩은 양쪽 모두 0건임을 확인했다. 사용자 재로그인도 완료돼 프로젝트 권한 조회가 정상이다.
- 로컬 API 회귀 32건, 인프라 검사 18건 및 scripts 19건, Ruff와 mypy 및 계약 비교가 통과했다. 실제 SDK HTTP 전송을 모의해 OAuth와 키 경로 분리, 사용량과 빈 응답, 임베딩 잘림 및 차원 불일치, 오류 비노출을 검사했다. IAM 스크립트는 권한 범위와 성공 및 실패 후 임시 바인딩 회수를 모의 검증했다.
- 현재 WSL에서 Docker 명령을 사용할 수 없어 이번 로컬 이미지 검증은 못 했다. 소스 e557c8bf816326dceef2671b7bbb9fb32b34e88a의 원격 CI 39706740083은 백엔드 3개의 Docker 테스트와 이미지 및 오프라인 런타임 import, 프론트 계약 소비자, 평가와 인프라 검사까지 모두 성공했다. 협업 CI 39706740257도 성공했다. 자동 CD 39706861091도 plan과 cs, tft, platform 모두 성공했다. 웹은 배포하지 않았다. 이미지 빌드와 별도 CD 총 6건이 SUCCESS다. runtime 검사에는 공통 AI SDK import를 추가했고 시작 및 상태 검사에서 모델을 호출하지 않는다.
- docs/ai-client.md에 팀원이 재사용할 생성과 임베딩 예제를, docs/integration-checks.md에 ADC와 CLI 로그인 구분 및 실행 계정 검증 명령을 정리했다. docs/team-start.md의 외부 인증 없는 시작 경로는 그대로다. 배포 도우미는 C/S와 TFT에만 명시적인 Vertex 환경변수를 설정한다. API 키와 인증 파일은 배포하지 않는다.

실제 배포 검증: C/S는 team4-cs-00003-ln2, TFT는 team4-tft-00003-fv2, 플랫폼은 team4-platform-00003-sfr다. 세 서비스의 source SHA는 e557c8bf816326dceef2671b7bbb9fb32b34e88a이며 이미지 digest가 성공한 빌드 결과와 일치한다. 모두 인증 ready 200과 database/vector/fts=true, 직접 익명 health 403을 확인했다. C/S와 TFT의 Vertex 환경설정은 실제 배포됐고 플랫폼에는 AI 환경변수가 없다. 웹은 기존 team4-web-00003-nlc와 c111cc3f8f80f6378429c33b249b01263cf718dc를 유지하며 공개 health 및 플랫폼 ready 중계가 200이다. 빌드 ID와 리비전은 docs/service-delivery.md에 기록했다. 배포 검증에서 모델을 추가 호출하지 않았다.

GCP IAM 변경은 사용자가 실행한다. 현재 셋업은 기존 승인대로 main 직접 push 대상이다. 공통 패키지 및 실행 의존성 변경은 백엔드 3개의 재빌드 사유이며 웹 코드는 바꾸지 않았다. Microsoft 개인 Outlook 대안은 아직 선택되지 않았고 실제 로그인 및 메일은 미검증이다. 실행 계정, 원격 CI와 자동 배포 검증을 완료했다. 실제 업무 API 내부 모델 호출과 품질 평가는 기능 담당자의 후속 작업이다.

## 2026-10-22 공용 웹과 서비스별 CI 및 CD 검증

사용자가 합의 범위를 실행하도록 요청했다. 초기 셋업은 main 직접 push 예외를 유지한다. GCP 준비와 최초 배포 명령은 사용자가 실행했고 에이전트는 읽기 전용으로 검증했다. 사용자가 GCP_RELEASE_ENABLED=true와 배포 워크플로 수동 실행을 적용했으므로 이후 성공한 main CI가 자동으로 Cloud Build를 실행한다. 아래 과거의 미배포 또는 자동 CD 비활성 설명은 이전 시점의 기록이다.

- [공용 웹](https://team4-web-gew6ci4pgq-du.a.run.app/)은 HTTPS 200이다. 별도 Node 24 웹 서버가 React 정적 파일을 제공하고 플랫폼 health/ready만 IAM 토큰으로 중계한다. 업무 API와 사용자 정보 및 개발 로그인 경로는 404, POST는 405다. web-runtime에는 DB 연결 및 DB Secret을 설정하지 않았다. 제품 인증과 업무 API 중계는 후속이다.
- 소스 afa938cb4ded382f2c25cf2565acb1450831bd93의 Application checks 39644064156과 Collaboration checks 39644064247이 성공했다. 자동 CD 39644133647에서 계획과 cs, tft, platform, web이 모두 성공했다. 네 이미지 생성과 별도 CD 빌드가 각각 SUCCESS이며 실행 계정도 분리했다. 실제 배포 SHA 및 digest가 성공한 빌드 결과와 일치한다. 빌드별 ID는 docs/service-delivery.md에 기록한다.
- 백엔드 리비전은 cs=team4-cs-00002-c4s, tft=team4-tft-00002-r89, platform=team4-platform-00002-nmb다. 모두 인증 ready 200 및 database/vector/fts=true이고 직접 익명 health는 403이다. 웹은 당시 team4-web-00002-bl5였으며 공개 정책과 플랫폼 ready 200을 확인했다.
- 팀원이 안내를 바로 열도록 frontend/src/App.tsx의 링크만 수정한 c111cc3f8f80f6378429c33b249b01263cf718dc를 반영했다. CI 39645850820은 frontend와 최종 결과만 성공하고 backend, infrastructure, evaluation은 생략됐다. 자동 CD 39645939828은 plan과 deploy-web만 실행해 성공했다. 웹 이미지 빌드 5a69a5d0-bad2-411e-b99e-1430a4226763와 CD b8bb9ff0-f6bc-4e32-9133-693f7be2a6bc가 모두 SUCCESS다. 웹은 team4-web-00003-nlc이며 digest sha256:3c236732fe78e01ce69fbec06ff5a954b21ac8a4364646b80de0561e3fc423b0와 소스 SHA가 일치한다. 배포한 JS 안의 안내 링크와 플랫폼 ready 200 및 개발 경로 404를 확인했다. 백엔드 3개의 리비전과 소스는 앞선 값 그대로다.
- scripts/changes.py는 PR 및 마지막 성공 main CI 이후 변경을 판별한다. infra/release.py는 서비스별 실제 배포 SHA 이후 누적 변경을 비교한다. 계약 소비자 검사만 필요한 경우 웹을 배포하지 않고 오래된 main 이벤트는 건너뛴다. 기존 루트 tests는 공통 회귀이며 새 업무 테스트는 tests/cs, tft, platform에 둔다. 자동 CD는 기존 IAM 공개 정책을 검사하고 변경하지 않는다.
- OIDC provider ACTIVE와 저장소/조직 숫자 ID 및 main의 deploy-services.yml 경로 제한을 확인했다. web-runtime, release-orchestrator, clouddeploy-runner를 사용하고 서비스 계정 키는 만들지 않았다. GitHub의 GCP_WIF_PROVIDER에는 공개 provider 리소스명만 저장한다. Cloud Build 소스는 커밋된 허용 경로만 포함하고 .env 및 data는 제외한다.
- 실제 파이프라인에서 발견한 오류도 해결했다. 기존 Docker 빌더의 runtime 생성 시 TEST_SERVICE를 전달하고 all 예시 검사는 서비스마다 순회한다. 배포 빌드에 전달한 COMMIT_SHA는 추적 태그에서 참조한다. Cloud SDK 이미지에는 python3 명령이 없어 SDK가 사용하는 Python 경로로 실행하며 실제 검증한 SDK 이미지 digest를 고정했다. 제출 오류는 SDK의 ERROR 행만 제한해 남기고 토큰 형태를 가린다. 실패한 시도에서는 기존 서비스 배포를 유지했다.
- 로컬 검증: 전체 검사 23건과 타입 및 계약, 서비스 선택 Docker 검사 21건, 프론트 HTTP 3건과 production 빌드, 실제 legacy Docker runtime 및 서비스 소스 격리, SDK 이미지 안에서 도우미 실행을 확인했다. scripts 19건과 infra 15건 및 actionlint가 통과했다. data 및 최종 test 597건을 사용하지 않았다.
- 운영: 자동 백업과 서울 보관 및 7개 보존, 삭제 보호를 확인했다. 사용자 수동 백업 1792600511679와 1792600953011은 SUCCESSFUL이다. docs/operations-basics.md에 안전한 로그 및 백업 조회 명령이 있다. 복구 훈련은 하지 않았다.
- Microsoft: 사용자가 .env.integrations에 시크릿을 입력했다. WSL의 Windows 브라우저 열기를 수정해 로그인 페이지까지 접근했지만 조직 정책의 관리자 승인이 필요하다. 관리자 동의 버튼도 비활성이다. Mail.Read/Send/offline_access 표의 관리자 필요 '아니요'와 조직 정책을 구분했다. 실제 로그인과 메일 읽기 및 발송은 아직 미검증이다. 관리자 요청 가능 여부가 응답 대기 중이며 docs/integration-checks.md에 요청 문구가 있다. 관리자에게 직접 메시지를 보내지 않았다.
- Gemini Developer API: 사용자는 같은 키의 Python 호출이 이전에 성공했고 AI Studio가 Tier 2라고 보고했다. 키 조회로 교육장 프로젝트 소속과 GCP billingEnabled=true를 확인했다. 429 응답의 'Your billing account has exceeded its monthly spending cap.'으로 청구 계정 월간 한도 도달을 확인했다. 사용자 요청의 3.1-flash-lite 재시도와 3.6-flash도 같은 오류였다. 이 API 경로는 교육장 담당자의 월간 한도 확인이 필요하다. 다른 프로젝트와 청구 계정 공유 여부는 미확인이고 개인 결제 및 한도 변경은 하지 않았다.
- Vertex AI global: 사용자의 global 테스트 요청으로 별도 경로에서 gemini-3.6-flash를 호출했고 실제 OK 응답 및 HTTP 200과 STOP을 확인했다. 프로젝트는 동일한 교육장 프로젝트이며 API가 이미 활성화돼 있었다. 기존 교육장 사용자 OAuth 토큰으로 호출했고 토큰을 출력하거나 저장하지 않았다. 첫 출력 한도 64에서는 본문이 없었지만 1024에서 입력 4, 답변 1, thinking 82와 총 87토큰을 확인했다. 이는 사용자 계정의 단건 연결 성공이다. Cloud Run 실행 계정 권한과 Vertex용 SDK 및 평가 제공자 연결은 후속이고 최종 모델 채택도 확정하지 않았다. 인프라와 IAM은 변경하지 않았다. docs/integration-checks.md에 근거를 기록했다.

공용 웹과 서비스별 CI/CD 및 운영 기본 검증 결과를 노션의 기술스택, 아키텍처 및 맨 아래 다이어그램 프롬프트에도 반영했다. Jira KAN-67 댓글 10036 및 KAN-68 댓글 10037에 실제 검증과 남은 범위를 기록했고 담당과 일정 및 상태는 보존했다. 이번 결과 문서만 main에 반영하므로 실행 중인 서비스의 SHA가 문서 커밋과 달라도 정상이다. Microsoft는 관리자 승인이 어렵다는 사용자 의견에 따라 개인 Outlook.com 시연 계정 대안을 제시했고 선택 응답 대기 중이다. Gemini는 Vertex global의 단건 생성 성공을 바탕으로 실제 앱 및 평가 경로 전환을 결정할 수 있으며 기존 Developer API 청구 한도 확인은 별도다. 전체 제품 인증, 업무 권한, Cloud Tasks 처리, Jobs 평가 UI와 상세 모니터링으로 셋업을 확대하지 않는다.

## 2026-10-22 최신 합의: 이번 셋업의 범위와 완료선

사용자가 남은 셋업 전체를 지금 하는 것이 합리적인지 물었고, 개발을 막는 기반과 외부 연동 위험부터 해결하는 아래 범위에 동의했다. 이 절이 아래 과거의 광범위한 다음 작업보다 우선한다. 사람용 원본은 [노션 개발 관련 의사 결정 사항](https://www.notion.so/400910042c3780fb9025d489bf3a075e) 맨 위에 기록했다. 이번 요청에서는 범위를 기록하며 기능과 GCP 설정을 새로 구현하거나 적용하지 않는다.

이번에 진행할 범위:

1. Microsoft 실제 로그인과 메일 읽기 및 발송 검증, Gemini 생성 404/429 원인과 가용 모델 및 할당량 확인. 전체 제품 인증과 메일 업무 구현으로 확대하지 않는다.
2. 프론트 공용 URL 및 HTTPS, 최소 API 연결과 필요한 호출 인증. 기본 화면에서 배포 환경 연결을 확인할 수 있게 한다.
3. 프론트, C/S, TFT, 플랫폼의 CI와 CD를 각각 분리한다. 공통 코드의 영향과 계약 변경의 프론트 호환성 검사도 반영하고, 성공한 이미지 digest로 해당 서비스만 배포한다.
4. 로그 확인 방법과 백업 설정 및 실제 성공 여부를 점검한다. 정교한 모니터링과 복구 훈련은 후속이다.

순서는 외부 연동 확인, 프론트 및 공용 API 경로, 서비스별 CI/CD, 운영 기본 점검이다. 완료 기준은 팀원이 로컬과 공용 환경에서 개발 및 연결을 검증할 수 있고, 외부 연동 가능성이 확인되며, 자기 서비스만 검사하고 배포할 수 있는 상태다. 업무 기능 전체 완성을 셋업으로 포함하지 않는다.

Cloud Tasks 처리 핸들러 및 OIDC, 평가용 Cloud Run Jobs와 결과 관리 화면, 메일 동기화 스케줄러, 프로젝트별 상세 권한과 SSE, 정교한 모니터링 및 복구 검증은 해당 기능 코드가 준비될 때 진행한다. GCP 변경은 기존대로 사용자 터미널 실행 후 에이전트가 읽기 전용 확인한다. 담당자와 일정을 새로 배정하지 않는다.

현재까지 추가 CI/CD 요청에 대해서는 설정 조회와 계획 설명만 했고 선택 빌드 및 배포 기능은 아직 수정하지 않았다. 현재 cloudbuild.yaml과 배포 도우미는 백엔드 3개를 함께 처리한다. 프론트 별도 Cloud Run 웹 서비스는 추천안으로 논의했으며 실제 공용 배포는 아직 없다. 프론트 호스팅과 호출 인증을 구현할 때 구체 설정 및 최소 연결 범위를 확인한다.

## 2026-10-22 개발 시작 환경과 계약 분리

이 절이 아래 과거 상태보다 우선한다. 사용자는 팀원이 안내만 보고 개발을 시작할 수 있는 환경과 API 계약 변경 시 충돌 최소화를 요청했다. 현재 셋업은 사용자 지시에 따라 main 직접 push 대상이다. GCP 변경은 사용자 터미널에서 실행하고 에이전트는 읽기 전용 검증만 한다. Jira 담당자를 새로 배정하지 않는다.

- 팀원 시작 문서: [docs/team-start.md](docs/team-start.md). `python3 scripts/workspace.py setup`, `python3 scripts/workspace.py up`, `npm run dev --prefix frontend` 순서다. Docker, uv, Node 22.12 이상이 필요하다. GCP 키, Microsoft 시크릿과 원본 data 없이 시작한다.
- 로컬 Compose는 PostgreSQL 17과 pgvector 및 API 3개를 사용한다. DB 포트 5433, C/S 8081, TFT 8082, 플랫폼 8083, 프론트 5173이다. 서비스별 계정과 스키마 및 ACL을 실제 확인했다. .env.local은 각 PC에서 별도 생성하며 기존 .env와 data는 수정하지 않는다. down은 DB 볼륨을 보존한다.
- 플랫폼에 합성 사용자 4개의 개발 계정 선택과 DB 세션을 구현했다. DB에서 역할 및 활성 여부를 매번 조회한다. HttpOnly 쿠키와 Origin 검사, 세션 해시 저장 및 로그아웃 무효화를 적용했다. production에서는 개발 경로가 없고 미구현 실제 로그인은 401로 차단한다. 실제 OAuth와 서비스 중계 및 업무 권한은 후속 구현이다.
- [contracts/README.md](contracts/README.md): 각 서비스 기능 폴더의 Pydantic 모델과 라우터가 원본이다. `python3 scripts/workspace.py contracts cs`처럼 자기 서비스의 OpenAPI와 합성 예시만 생성한다. TypeScript 생성물은 Git에서 제외하고 로컬 및 CI에서 재생성한다. 공통 업무 DTO를 만들지 않는다. 다른 서비스 파일을 갱신하지 않는 검증을 추가했다.
- 프론트는 React, Vite, TypeScript와 TanStack Query의 개발 연결 확인 화면이다. OpenDesign 파일은 수정하지 않았다. 실제 DB 계정 선택과 준비 상태 및 합성 C/S, TFT 예시를 확인한다. 실제 목록 API는 명시적인 501 stub이며 실패를 모의 성공으로 숨기지 않는다. production 프론트에서는 모의 응답을 켤 수 없다.
- eval.smoke는 합성 2건만 사용하며 JSON 및 HTML에 모델, 판본, 지연, 스키마, 토큰과 실패 상태를 기록한다. fake 실행 성공은 모델 품질 합격이 아니다. 가격 미등록 비용은 미산정으로 표시한다. 최종 test 597건은 사용하지 않았다.
- 실제 외부 점검: Microsoft 테넌트 OpenID discovery 성공. 사용자 제공 tenant `6c2ea5ee-418c-4e13-8eaa-5163cdb50c67`, client `7160833b-2aa3-497f-bc6c-11c59851c254`. 실제 로그인과 Graph는 아직 실행하지 않았다. 로컬 시크릿과 Web redirect `http://localhost:8765/callback` 준비 후 [연동 안내](docs/integration-checks.md)를 따른다. 시크릿을 채팅으로 요청하지 않는다.
- Gemini 모델 목록 조회와 gemini-embedding-001 합성 임베딩 호출은 성공했다. 기본 차원 3072를 확인했지만 production 모델 및 인덱스 차원 확정은 아니다. 생성 호출은 gemini-2.5-flash-lite와 gemini-2.5-flash가 404, gemini-3.8-flash가 429였다. 생성 토큰 실측 성공으로 보고하지 않는다. SDK 자동 재시도는 비활성화했다. 실제 가용 모델과 할당량 확인이 필요하다.
- 사용자 실행 후 Cloud Build 준비 검증 완료: cloudbuild-runner 계정, 서울 비공개 소스 버킷, 버킷 objectViewer, 저장소 artifactregistry.writer, 프로젝트 logging.logWriter, 교육장 사용자의 빌드 계정 serviceAccountUser를 확인했다. 실제 원격 빌드와 이미지 업로드는 사용자 실행 후 검증했다. 빌드 401a8afb-f9b9-4a51-878e-2a96fc373e99가 SUCCESS이며 소스는 f04298161ea5b234d6e31521d186a6dd29f893f4다. 세 이미지의 SHA 및 BUILD_ID 태그와 결과 digest를 확인했다. 비공개 배포 미리보기도 통과했고 사용자에게 `python3 infra/deploy_private.py 401a8afb-f9b9-4a51-878e-2a96fc373e99 --apply` 실행을 요청했다. 사용자 실행 후 세 서비스의 Ready=True와 이미지 및 실행 계정과 Secret 연결, production 보호 및 최소 인스턴스 1을 검증했다. 세 서비스의 익명 health는 403, 인증 health는 200, 플랫폼 개발 계정 경로는 404였다. 첫 DB ready는 모두 503이었고 사용자가 Cloud SQL Studio에서 vector 확장을 적용한 후 세 서비스의 ready가 모두 200으로 통과했다. database, vector, fts가 전부 true였다. 이번 identity 마이그레이션과 합성 사용자는 클라우드에 적용하지 않았다.
- 검증: 로컬 실제 DB/API 검사 및 권한 행렬, 프론트 TypeScript 및 production 빌드, 브라우저 계정 선택과 로그아웃 및 모의/실제 모드 전환 통과. Docker 테스트 23건, mypy 34개 백엔드 파일, Ruff, OpenAPI 및 예시 일치 검사를 확인했다. 브라우저 스크린샷은 임시 /tmp/team4-onboarding.png다. 최종 커밋 이후 새 체크아웃 검증 및 원격 Actions 결과는 아래 후속 기록을 확인한다.

### main 게시와 원격 결과

구현 d83cef1과 새 체크아웃 검증 f042981을 원격 main에 직접 push했다. f042981의 Application checks 39629875011 및 Collaboration checks 39629874699가 모두 성공했다. 실제 Cloud Build 결과 digest는 infra/cloud-build.md에 기록했다. Jira KAN-56/65/64/79/67/86/76 댓글 10028~10034에 구현과 미완료 조건을 기록했고 담당 및 일정은 유지했다. KAN-67 댓글 10032는 실제 빌드 성공과 원격 검사 성공으로 갱신했다. KAN-68 댓글 10035에는 첫 비공개 API 배포와 접근 차단, vector 적용 후 DB ready 200 및 남은 범위를 기록했다. 새로 맡은 업무 전체가 완료됐다는 뜻은 아니다. 문서 결과 갱신 f830171의 Application checks 39631354485와 Collaboration checks 39631354371도 성공했다. 마지막 문서 갱신으로 실행 코드가 달라진 것은 아니며 실제 배포 이미지는 f042981 소스다.

### 다음 작업

팀원에게 docs/team-start.md와 contracts/README.md를 공유하면 로컬 개발을 시작할 수 있다. 초기 빌드 및 비공개 API 배포와 DB 준비 검사는 완료했으므로 이유 없이 반복하지 않는다. 업무 API는 여전히 stub이며 실제 플랫폼 중계, 서비스 간 IAM 및 사용자 맥락, 권한과 작업 및 SSE 구현은 후속이다. 프론트 호스팅도 아직 없다. Microsoft는 로컬 시크릿과 Web redirect를 준비한 후 scripts/check_integrations.py microsoft --login으로 실제 사용자 로그인 및 Graph를 확인한다. Gemini는 생성 404/429의 원인과 실제 가용 모델 및 할당량 확인 후 합성 smoke를 재실행한다. 원본 .env와 data를 보존하고 최종 test는 사용하지 않는다.

### 새 체크아웃 검증

구현 커밋 d83cef1의 git archive를 /tmp/team4-coldstart-ib9nidi4에 추출했다. 기존 .env, data, 의존성과 DB 볼륨이 없는 상태에서 setup을 실행해 의존성 및 타입 생성을 통과했다. COMPOSE_PROJECT_NAME=team4-onboard-check와 별도 로컬 포트 및 새 DB 볼륨으로 up을 실행해 세 서비스 ready와 계정 세션 및 ACL 검사를 모두 통과했다. 새 폴더의 프론트 production 빌드 및 명시적 migrate 명령도 성공했다. 임시 컨테이너는 down으로 종료했으며 볼륨을 삭제하지 않았다. 원래 team4-local Compose 환경과 프론트 개발 서버는 유지했다. Cloud Build 업로드 목록의 49개 파일에 .env 계열과 data, node_modules, artifacts 및 .git이 없음을 확인했다.

## 이전 작업: 초기 셋업은 main에 직접 push

2026-10-21 사용자가 "셋업인데 pr 하는거 별로임 그냥 main에 push해줘"라고 요청했다. 현재 초기 셋업은 새 PR이나 동료 검토 없이 main에 직접 반영한다. 기존 티켓별 커밋을 main에 통합하고 작업 공간의 파일을 보존한다. 아래 과거 기록의 Draft 해제, PR 7 선행 병합, PR 8의 base 변경 등은 더 이상 진행 조건이 아니다. 이후 업무 기능 개발의 일반 PR 규칙은 유지한다.

CONTRIBUTING.md, docs/decisions.md 및 docs/setup-prs.md를 이 결정에 맞췄다. 티켓별 커밋을 보존한 통합 커밋 c1e07e1과 결정 기록 e953f7d를 원격 main에 직접 push했고 두 커밋의 원격 Collaboration checks 성공을 확인했다. 셋업 전체를 포함하는 main을 Cloud Build 소스로 사용한다. PR 3~7 및 9는 main 포함으로 GitHub가 자동 MERGED 처리했고 PR 8은 커밋의 main 포함을 확인한 뒤 닫았다. 열린 셋업 PR과 검토 요청은 없다. Jira 9건의 기존 결과 댓글 10019~10027에 main 반영을 기록하고 담당 오새결과 일정 및 진행 중 상태를 유지한다. 코드 게시만으로 기술 미완료 항목을 완료 처리하지 않는다.

원본 공간의 파일 47개가 게시 커밋과 일치함을 확인하고 인덱스를 통합 main에 맞춘 뒤 로컬 main으로 전환했다. 작업 공간은 깨끗하며 .env 및 data의 inode, 크기와 수정 시각이 보존됐다. 비밀값과 데이터 내용은 읽지 않았고 해당 경로를 커밋, 이동 또는 삭제하지 않았다. 임시 main worktree는 제거했으며 이전 티켓 worktree 6개는 보존했다. 로컬 훅이 main 커밋을 거부하므로 명시적으로 승인된 이번 main 문서 커밋에만 git -c core.hooksPath=/dev/null commit을 사용했다. 영구 훅 설정은 .githooks 그대로다.

GCP는 아직 변경하지 않았으며 다음은 사용자 터미널에서 bash infra/setup_cloud_build.sh 실행, 에이전트의 읽기 전용 권한 확인, main에서 bash infra/submit_cloud_build.sh 실행 순서다. 실제 원격 빌드와 이미지 업로드 및 배포는 여전히 미실행이다.

## 이전 작업: 티켓별 커밋 및 PR과 Cloud Build 실행 준비

2026-10-21, 사용자의 진행 요청으로 초기 셋업 변경을 별도 worktree의 티켓별 브랜치에 커밋하고 push한 뒤 Draft PR 3~8로 게시했다. 계약 문서는 현재 KAN-55-KAN-56-development-contracts 브랜치의 [PR 9](https://github.com/LS-Final-Project/ls-final-project/pull/9)로 게시했다. 실제 PR 목록과 실행 순서는 [docs/setup-prs.md](docs/setup-prs.md)를 따른다. 원본 공간의 코드 및 인프라 파일은 공유 세션 보존을 위해 그대로 남겼으므로 미커밋 복사본과 원격 게시 상태를 구분한다. .env와 data는 수정하거나 포함하지 않았고 비밀값을 조회 및 출력하지 않았다. 현재 모든 PR은 미병합이고 검토 요청은 없다.

KAN-60의 PR 3에서 Jira 번호가 없는 제목이 [KAN-60]으로 자동 보완되고 관리되는 Jira 링크 블록이 생긴 것을 실제 확인했다. 새 초기 셋업 검토 예외는 정확한 브랜치명과 티켓 조합에만 적용하며 같은 티켓의 후속 브랜치로 확장하지 않는다. 예외는 PR 3이 main에 반영된 뒤 동작하므로 현재는 Draft로 검토 요청을 방지했다.

Jira 9건에 PR 링크와 검증 결과 및 미완료 조건을 추가했다. 댓글 ID는 KAN-55 10019, KAN-56 10020, KAN-60 10021, KAN-62 10022, KAN-63 10023, KAN-65 10024, KAN-66 10025, KAN-67 10026, KAN-69 10027이다. 담당자와 기한 및 진행 중 상태는 변경하지 않았다. 같은 결과를 다시 댓글로 중복 게시하지 않는다. PR 3~7의 원격 Collaboration checks 및 Jira PR metadata 성공을 확인했다. 계약 PR의 최초 c38b245 검사도 성공했고 후속 문서 커밋의 검사는 최종 상태를 재조회한다. PR 8은 base가 KAN-63 브랜치라 main 대상 Actions가 실행되지 않았으며 Jira 링크는 기존 본문을 보존해 직접 추가했다.

KAN-63에 mypy를 추가했고 헤더 Mapping 및 FastAPI 응답 스키마 타입을 보완했다. Docker에서 lint 및 포맷, mypy 15개 파일과 백엔드 테스트 14건 및 OpenAPI 일치 검사를 통과했다. infra 테스트 7건과 협업 자동화 테스트 총 24건도 통과했다. 로컬 샌드박스에서 pytest가 멈춘 실행은 종료했으며 Docker의 통과 결과를 기준으로 삼는다.

KAN-67의 Cloud Build는 전체 커밋 SHA를 검증하고 SHA 및 BUILD_ID 태그와 OCI revision label을 남긴다. submit_cloud_build.sh는 HEAD의 명시적 허용 파일만 git archive로 추출해 업로드한다. setup_cloud_build.sh는 빌드 전용 계정, 서울 비공개 소스 버킷과 필요한 IAM을 사용자 실행으로 준비한다. deploy_private.py는 성공 빌드의 실제 결과 digest 3개를 확인하며 기본은 미리보기다. --apply는 사용자가 실행한다. IAM 인증과 production 및 Secret 버전 1, 최소 인스턴스 1을 적용하며 업무 기능과 실제 FastAPI DB 연결이 구현됐다는 뜻은 아니다.

이번 읽기 전용 조회에서 cloudbuild-runner 및 별도 소스 버킷은 없었고 서울 Cloud Build 목록도 비어 있었다. GCP 변경과 실제 빌드, 이미지 업로드 및 Cloud Run 서비스 배포는 실행하지 않았다. 다음은 사용자가 저장소 루트에서 bash infra/setup_cloud_build.sh를 실행한 뒤 에이전트가 설정을 읽기 전용으로 검증하는 단계다. 빌드는 커밋된 PR 8 worktree 또는 병합된 main에서 실행해야 한다. PR 8은 PR 7 기반이므로 PR 7 병합 후 base를 main으로 바꾸고 원격 검사를 확인한다. Jira 완료 상태는 기술 조건을 확인하기 전 변경하지 않는다.

## 이전 요약: 담당 변경 및 브랜치 정리

- 사용자 요청으로 현재 셋업 Jira KAN-55/56/60/62/63/65/66/67/69의 담당자를 오새결로 변경하고 설명도 맞췄다. 9건 재조회로 담당 변경과 상태 및 기한 보존을 확인했다. 이전의 김나영 담당 유지 기록보다 이 결정이 우선한다. WBS 원본은 변경하지 않았다.
- 현재 초기 셋업은 동료 검토와 승인 대기를 생략한다. 테스트와 실제 실행 및 기술 완료 조건은 확인한다.
- PR #1과 #2는 main 병합 완료. main 및 현재 KAN-55-KAN-56-development-contracts의 기준 커밋은 9a00666이다.
- 병합된 KAN-20 원격 브랜치와 KAN-60 로컬 브랜치 및 임시 worktree를 정리했다. 로컬 main 및 현재 작업 브랜치, 원격 main만 남았고 로컬 Jira 커밋 훅 설치와 자동화 검사도 통과했다.
- 설계, DB SQL, GCP 도우미, 세 FastAPI 앱 및 Docker/Cloud Build 구성은 미커밋으로 보존돼 있다. .env와 data는 건드리지 않았고 신규 PR은 없다.
- 다음은 미커밋 작업을 티켓별 커밋/PR로 정리하고 Cloud Build 실행 계정, 실제 빌드 및 비공개 배포를 진행하는 단계다. 현재 셋업 관련 9건은 기술 미완료 항목이 있어 진행 중이다. GCP는 사용자 터미널 실행 후 에이전트가 읽기 전용으로 확인하는 흐름을 유지한다.

## 이전 결정: 현재 셋업의 동료 검토 생략

2026-10-21 사용자가 "지금 셋업하는건 동료 검토 생략할거야"라고 명시했다. 현재 진행하는 계약 초안 및 GitHub 자동화, 실행 골격, DB와 GCP, 빌드와 비동기 실행 기반의 초기 셋업에서는 동료 검토, 검토 요청과 승인 대기를 진행 조건으로 요구하지 않는다. 적용 대상은 KAN-55/56/60/62/63/65/66/67/69의 현재 셋업 범위다. 아래 과거 기록에서 이 셋업의 동료 검토를 미완료 사유로 든 부분은 이 결정으로 대체한다. 기존 WBS 담당과 검토자 배정 이력을 지우거나 이후 업무 기능 전체의 검토 면제로 확대하지 않는다.

CONTRIBUTING.md와 docs/decisions.md에도 예외를 기록했다. 테스트와 실제 실행 검증, 결과물 게시 등 각 티켓의 남은 기술 조건은 확인한다. 현재 티켓들에는 원격 PR 자동화 실검증, 계약 확정과 업무 OpenAPI, 결과물의 티켓별 커밋/PR, pgvector/FTS, 백업 실제 결과, 타입 검사 및 commit 이미지 태그와 원격 빌드, 실제 작업/OIDC/SSE 등 남은 항목이 있어 검토 면제만으로 완료 처리하지 않는다. Jira에도 각 티켓의 검토 면제와 남은 기술 조건을 기록한다.

## 이전 작업: main 병합 확인과 브랜치 및 Jira 정리

2026-10-21 사용자 요청으로 원격 정보를 갱신하고 GitHub PR 및 Jira 상태를 대조했다. PR #1(KAN-20)은 10월 14일, [PR #2(KAN-60)](https://github.com/LS-Final-Project/ls-final-project/pull/2)는 10월 21일 22:17:23 KST에 main으로 병합됐다. main과 origin/main은 `9a006669474133f048d9be65e116b8467e365a6f`다. main 기준 열린 PR은 없다. PR #2의 Collaboration checks는 성공했지만 reviews 및 reviewRequests는 비어 있어 동료 승인 완료로 보고하지 않는다.

현재 `KAN-55-KAN-56-development-contracts` 브랜치도 최신 main으로 fast-forward했다. 변경 전 작업 파일 47개의 해시를 비교해 모두 보존된 것을 확인했고 .env와 data는 읽거나 수정하지 않았다. README 변경과 신규 설계, SQL, 인프라 및 백엔드 구현은 여전히 미커밋 로컬 작업이다. 이번 정리에서 새 코드 커밋이나 PR을 만들지 않았다. 후속으로 계약, DB 및 인프라, KAN-63 실행 골격과 KAN-67 CI 변경을 실제 티켓별 브랜치/PR로 나눠 검토해야 한다.

main 포함 여부와 깨끗한 상태를 확인한 뒤 사용이 끝난 `/tmp/ls-kan60-github-automation` worktree 및 로컬 KAN-60 브랜치를 제거했다. 원격 KAN-60은 이미 삭제돼 있었으며 남아 있던 병합 완료 원격 KAN-20 브랜치를 삭제했다. 로컬 브랜치는 main과 현재 작업 브랜치, 원격 브랜치는 main만 남았다. `python3 scripts/dev.py setup`으로 현재 작업 공간의 Jira 커밋 훅도 설치했다.

Jira KAN-20은 기존 완료를 유지한다. KAN-60 댓글 10001을 실제 병합 결과와 main 문서 링크로 수정하고, 동료 검토 및 후속 PR의 제목/링크/검토 요청 실검증이 남아 진행 중을 유지했다. KAN-55/56은 진행 중을 유지하고 댓글 10002/10003에 최신 계약 및 미구현 범위를 기록했다. 실제 착수한 KAN-62/63/65/66/67/69는 해야 할 일에서 진행 중으로 옮겼으며 댓글 10004~10009에 검증 근거와 남은 완료 조건을 기록했다. KAN-63 제목과 설명은 C/S, TFT, 플랫폼 앱 3개의 확정 구조로 맞췄다. 기존 담당자, 검토자와 일정은 보존했다. 백업 실제 결과, pgvector/FTS, 타입 검사 및 commit 이미지 태그, 원격 빌드, 실제 작업/OIDC/SSE 등은 해당 티켓의 미완료 조건이다.

## 이전 작업: GitHub 협업 자동화 KAN-60

사용자가 다른 세션으로 넘기려던 GitHub 자동화를 이 세션에서 진행하도록 요청했다. 원래 작업 공간과 다른 세션 파일을 보존하기 위해 당시 KAN-60-github-automation 브랜치와 /tmp/ls-kan60-github-automation 작업 공간을 만들었다. 원래 저장소는 KAN-55-KAN-56-development-contracts 브랜치 그대로다. KAN-60 작업 공간과 브랜치는 위 최신 정리에서 제거했다.

[PR 2](https://github.com/LS-Final-Project/ls-final-project/pull/2)에 구현과 사용법을 올렸다. 커밋 247aae2에 최초 구현이 있으며 후속 문서 커밋은 PR에서 확인한다. 위 최신 확인에서 main 병합을 확인했다. 동료 검토 기록은 없다. PR 작성자는 오새결이며 KAN-60의 기존 공식 담당 김나영 및 검토자 김종하 배정은 유지한다.

- scripts/dev.py setup으로 현재 저장소의 로컬 Git 훅을 설치한다. 기존 훅은 덮어쓰지 않는다.
- scripts/dev.py start KAN-63 backend-skeleton처럼 실제 Jira 번호를 지정하면 최신 origin/main에서 작업 브랜치를 만든다. 변경 중인 파일이 있으면 자동 stash나 삭제 없이 중단한다.
- 일반 커밋 제목에 브랜치 번호를 추가하며 중복과 다른 티켓 번호를 검사한다. 기존 여러 번호의 브랜치, 본문과 CRLF 보존, merge 및 rebase와 autosquash 예외를 처리한다.
- PR 자동화는 신뢰하는 base SHA의 파일만 사용해 제목과 Jira 링크를 보완하고 번호 불일치를 검사한다. 개인 Jira API 토큰을 요구하지 않으며 실제 티켓 존재를 API로 검증하는 기능은 아니다.
- .github/jira-reviewers.json은 현재 WBS 118개 작업의 티켓별 검토 배정이다. 공식 담당의 A->C, B->A, C->B 순환을 옮겼으며 실제 PR 작성자로 배정을 바꾸지 않는다. WBS의 검토 배정 변경 시 이 표도 갱신한다.
- Draft를 해제하면 등록된 검토자를 요청한다. 이미 요청하거나 검토한 사람에게 반복 요청하지 않고 자기 검토 및 미등록 작업은 직접 지정하도록 안내한다. Jira 최종 완료 상태는 자동 변경하지 않는다.
- 앱 CI/CD는 Cloud Build 그대로다. GitHub Actions는 협업 자동화와 해당 자동화의 테스트를 담당한다.

로컬 Python Git 연동 테스트 12건 및 Node PR 처리 테스트 10건이 통과했다. 원격 Collaboration checks도 최신 커밋 9d3f8f4 기준 성공했고 실행 ID는 39603968392다. 파일 테스트 통과를 실제 PR 자동 보완 검증 완료로 혼동하지 않는다. 최초 자동화 PR은 당시 main의 워크플로가 없어 PR 메타데이터 자동화 대상이 아니었다. 이제 main 병합은 확인했으며 이후 실제 PR로 자동 보완을 검증해야 한다. Jira KAN-60에 PR과 검증 결과를 댓글 10001로 기록하고 실제 착수에 맞게 진행 중 상태로 옮겼다. 공식 담당자 및 기한은 변경하지 않았다.

GitHub 실제 설정: 비공개 조직 저장소, main, 현재 사용자 ADMIN. Actions 활성 및 allowed_actions=all, 기본 토큰 권한 read. 워크플로는 메타데이터 작업에만 contents:read 및 pull-requests:write를 명시한다. Squash merge 허용은 기존 설정이며 이번에 squash_merge_commit_title=PR_TITLE로 변경 후 확인했다. 다른 병합 방식과 자동 브랜치 삭제는 변경하지 않았다. 브랜치 보호 및 규칙 조회는 요금제 제한 403이어서 검사 및 승인 누락 시 병합 강제 차단이 보장되지 않는다. 유료 전환이나 공개 전환은 수행하지 않았다.

사용자가 GitHub for Atlassian 공식 앱의 LS-Final-Project 조직 및 ls-final-project 저장소 연결을 완료했다고 답했다. 재조회한 Jira KAN-60의 development 필드에도 GitHub Draft PR 1건이 표시됐다. 값은 cachedValue 및 isStale=true인 요약이므로 모든 브랜치와 커밋의 최신 전체 동기화까지 확인한 결과로 표현하지 않는다. 공식 앱 연결을 다시 설치하도록 요청하지 않는다. 조직 앱 설치 목록 API 조회는 gh 인증의 admin:org 범위 부족으로 실패했으나 이 추가 권한을 요구하거나 OAuth 로그인을 다시 시작하지 않았다. 이 연동은 Codex의 Atlassian 플러그인 연결과 별개다. 남은 단계는 동료 검토와 실제 후속 PR의 자동 제목, 링크 및 검토 요청 확인이다. main 병합은 위 최신 점검에서 확인했다.

팀원 안내와 전체 설정 근거는 [자동화 안내](https://github.com/LS-Final-Project/ls-final-project/blob/main/docs/github-automation.md)에 있다. 원래 작업 공간의 data, infra 및 다른 세션의 파일은 stage하거나 커밋하지 않았다. 실제 적용 파일은 PR의 12개 파일뿐이다. 이 작업 중 Jira 조회에서 S1은 active 상태와 사용자가 바꾼 이름 'S1 공통 기반 구축과 평가 파이프라인'으로 확인됐다. 아래 스프린트 절의 future 표기는 과거 배정 완료 시점의 기록이다.

## 이전 작업: 스프린트 분할

사용자는 기존 WBS 일정과 의존성을 고려해 Jira 작업을 스프린트로 나누도록 요청했다. [docs/sprints.md](docs/sprints.md)에 근거, 완료 조건, 공수와 작업별 배정을 작성했고 [docs/sprint-plan.json](docs/sprint-plan.json)에 후속 배정용 데이터를 남겼다.

- 1차 2026-10-21~10-27: 공통 기반과 평가 실행, 42건.
- 2차 2026-10-28~11-03: 기능 완성과 통합 배포, 66건.
- 3차 2026-11-04~11-10: 안정화와 발표 준비, 8건.
- KAN-169와 KAN-170은 3차 예비시간 2건이며 기본 스프린트 배정 및 완료율에서 제외하고 필요할 때만 투입한다.
- 기존 담당자, 작업별 날짜와 상태는 유지한다. KAN-79와 KAN-103은 1차 착수 및 2차 완료로 계획했다. 선행 작업을 뒤 스프린트에 두는 배정은 0건이다.
- Jira KAN-14 댓글 10000에 분할 근거와 현재 적용 상태를 기록했다. 과거 미정 SETUP 작업과 완료 이력을 새 개발 스프린트에 일괄 편입하거나 완료 처리하지 않는다.

실제 배정과 재조회 검증을 완료했다. 스프린트 ID 1 'KAN 1 스프린트'에 42건, ID 2 'KAN 1 스프린트 2'에 66건, ID 3 'KAN 1 스프린트 3'에 8건이며 예비시간 2건은 스프린트 미배정 상태로 백로그에 남아 있다. 누락 및 예상 밖 배정은 0건이고 기존 담당자, 작업별 시작일과 기한, 상태 및 기존 라벨이 유지된 것을 확인했다. KAN-14 댓글 10000도 최종 배정 결과로 갱신했다.

처음 보드 1은 스프린트 미지원으로 응답했고 사용자도 기능 항목을 찾지 못했다. 기능 설정 링크로 안내한 뒤 사용자가 활성화, 빈 스프린트 생성과 날짜 설정을 완료했다. 최초 종료시간이 각 마지막 날 00:00으로 저장돼 있었으므로 사용자가 해당 날짜 23:59로 수정했다. API의 UTC 시각을 한국시간으로 변환해 위 세 기간을 확인했고 현재 모두 future, 시작 전 상태다. 스프린트 날짜와 실제 배정 결과는 sprint-plan.json에 보관했다. 사용자에게 이미 끝낸 설정을 다시 요청하지 않는다.

계획 라벨 plan-sprint-1 등은 실행 중 일부 62건에 추가된 보조 정보다. 전체 스프린트 배정 조회 및 진행률의 기준은 실제 Sprint 필드이며 라벨 유무로 작업 누락을 판단하지 않는다. JSON의 plannedLabel도 전체 라벨 반영 완료를 뜻하지 않는다. 일부 장시간 응답의 호출을 중단한 뒤에는 성공 기록 수에 의존하지 않고 Jira를 재조회해 저장된 배정을 확인하고 빠진 항목만 처리했다.

Atlassian 연결 도구에는 스프린트 기능 활성화, 생성과 날짜 편집이 없어 사용자 화면 조작이 필요했다. 기존 팀 관리형 KAN을 유지하며 새 프로젝트로 이전하지 않는다. 보드 ID를 알고 있을 때 getJiraBoardSprintData에 boardId와 sprintName을 전달하면 미래 스프린트 목록 및 ID를 확인할 수 있다. 활성 스프린트 없이 이름도 생략하면 'No active sprint' 오류만 오므로 미지원 상태와 혼동하지 않는다. 작업 배정은 transitionJiraIssue에 issueIdOrKey와 sprintId만 전달하고 기존 업무 상태는 바꾸지 않는다.

이전 /tmp 파일은 현재 환경에서 없었다. 후속 작업은 저장소에 보관한 배정 데이터와 최신 Jira 조회를 기준으로 진행한다.

## 이전 GCP 비용 중단 요청의 미완료 상태

2026-10-21 11:55 UTC 검증 기준, 기존 RFI 실행 리소스와 DB 정리는 완료했지만 **GCS 버킷은 복구 보존 상태가 남아 있다**. 사용자는 GCP 실조회 결과를 확인한 뒤 삭제를 요청했고, DB 최종 백업을 생성하지 않고 버킷의 7일 복구 보존을 해제해 모든 원본을 삭제하는 질문에 **백업 없이 완전 삭제**를 명시적으로 선택했다. 이 승인 범위를 다시 질문하지 않는다.

삭제한 대상은 `rfi-web`, `rfi-db`, `us-central1/rfi` 이미지 저장소, `rfi-collect` Job, `rfi-collect-daily` Scheduler, `cloud-database-url` Secret이다. DB 삭제 작업은 DONE이며 프로젝트 백업 목록도 비어 있다. `proj-skkuls0106-183255377938-rfi-data`와 객체 4,864개 전체 버전의 삭제 명령도 성공했고 일반 버킷 목록은 비어 있다. 다만 Storage API의 `softDeleted=true` 전체 페이지 조회에서 버킷 1개가 남았으며 `hardDeleteTime=2026-10-28T11:51:25.198Z`다. 한국 시각 10월 28일 20:51:25 이후 영구 삭제 상태를 재조회한다. Google 정책상 보존 기간 중 강제 영구 삭제는 지원하지 않는다.

버킷은 soft delete가 0초로 반영된 것을 조회했지만 이것만으로 모든 삭제가 영구 삭제됐다고 보장할 수 없었다. 정책 해제 요청은 11:50:25 UTC, 버킷 삭제는 11:51:35 UTC였다. 정책 변경은 최대 30초 전파 지연이 있을 수 있어 최초 객체 삭제 전에 대기가 필요하다. 이번 객체별 잔여 수와 용량은 확인하지 못했으며, 버킷을 복원하지 않으면 해당 목록을 조회할 수 없다. 이 확인만을 위해 버킷이나 원본 객체를 복원하지 않았다.

새 환경에도 사용할 수 있는 `gemini-api-key` Secret은 보존했다. 교육장 프로젝트, 결제 연결, 활성 API, 실행 서비스 계정과 로컬 data 디렉터리는 변경하지 않았다. 실제 API 키를 폐기하지 않았으며 Secret 값도 읽지 않았다. 현재까지의 실청구액과 프로젝트 전체 비용이 0원이라고 안내하지 않는다.

DB를 최종 백업 없이 삭제하는 최초 명령은 자동 승인 리뷰에서 원본의 복구 불가능한 삭제에 대한 명시적 승인이 부족하다는 이유로 거절됐다. 이후 위 질문에 사용자 답변을 받은 뒤 같은 범위로 재실행해 완료했다. 현재 삭제 승인 대기나 자동 승인 리뷰로 막힌 작업은 없다.

## 작업 방식과 사용자 선호

- 사용자는 오새결이며 실제 인프라 세팅을 맡는다. WBS 및 Jira의 기존 담당자 배정은 유지한다.
- 큰 의사결정을 순서대로 제안하고 사용자가 선택하는 방식으로 진행했다. 이미 확정한 사항은 다시 미정으로 돌리지 않는다.
- 사용자는 시연을 위해 승인 절차와 역할을 과도하게 복잡하게 만드는 것을 원하지 않는다. 핵심 범위 및 권한 변경만 질문하고 나머지는 합리적인 기본값으로 정리한다.
- 최근 인프라 설정은 사용자가 터미널에서 명령을 실행하고, 에이전트가 읽기 전용으로 확인하는 흐름이었다. 리소스 비용 정리 요청은 직접 조치하도록 별도로 요청했다.
- Google Workspace 자료는 사용자가 지정한 gws CLI로 접근한다. GCP 리소스는 gcloud CLI를 사용한다.
- Google Workspace 연결과 gcloud 계정은 별개다. 이번 세션의 gws 문서 및 폴더 조회는 정상 동작했으므로 GCP 계정에 맞추려는 이유만으로 Workspace 인증을 다시 바꾸지 않는다.
- 생성 문서에는 긴 대시, 가운데점과 장식적인 인용부호를 사용하지 않는다.
- 계정 토큰, API 키와 Secret 값을 출력하거나 문서 및 커밋에 넣지 않는다. 이번 비용 점검에서는 Secret의 이름과 버전 상태만 확인했다.
- 다른 세션 소유 코드와 OpenDesign 프로토타입을 임의로 수정하지 않는다. data 디렉터리는 읽기 전용이다.
- 로컬 쓰기는 이 저장소와 /tmp에서 진행했다. /home/canae/ls_main은 읽기 자료다. GCP와 gws의 네트워크 명령은 해당 실행 환경의 권한 승인을 거쳐 수행했다.

## 확정된 제품 및 아키텍처

| 항목 | 최신 결정 |
| --- | --- |
| 프로젝트 1 | 해외 C/S 문의 대응과 반복 문제 개선요청 |
| 프로젝트 2 | RFP에서 과업 및 역할 도출, 부서 후보 추천 및 검증, TFT 구성 지원 |
| TFT 조직 범위 | 회사 한 곳 내부의 부서 협업. 구체적인 주관회사는 미정 |
| 서비스 | C/S 업무 앱, TFT 업무 앱, 공통 플랫폼 API를 독립 배포 |
| 공통 플랫폼 | 인증, 실제 메일, 파일, 평가 관리 API와 브라우저의 업무 API 요청 중계 |
| 공유 코드 | DTO, 오류 및 인증 검사 등 순수 공통 코드와 계약을 패키지로 공유 |
| DB | Cloud SQL 인스턴스 1개, PostgreSQL DB 1개, 서비스별 스키마와 접속 계정 |
| 클라우드 | 교육장 GCP, 서울 asia-northeast3 |
| 환경 | 각자 로컬 개발, GCP 공용 시연 환경 한 세트 |
| 공용 환경 인증 | environment=production, 실제 Microsoft 로그인. 개발 계정 선택 차단 |
| 로컬 개발 인증 | DB에 준비한 시연 사용자 선택. 가짜 Microsoft 계정 생성 불필요 |
| 회사 시스템 | 이번 범위에서 연동하지 않음 |
| 메일 | 실제 읽기와 발송 포함. 내용은 사람이 확인 |
| 개발 완료 | 2026-11-03 |
| 발표 | 2026-11-10 |

공통 플랫폼은 처음에는 C/S 앱에 함께 배포하는 안을 제안했지만 사용자가 독립 서비스를 선택했다. 이 이전 제안으로 되돌리지 않는다. 업무 백엔드 2개와 플랫폼 1개, 총 3개다.

스택은 Python 3.11 이상, FastAPI, React 및 Vite와 TypeScript, TanStack Query, Gemini, Gemini 다국어 임베딩, PostgreSQL 및 pgvector와 FTS, Cloud Storage, MSAL 및 PKCE, LangChain 및 LangGraph, PostgreSQL 체크포인터, LangSmith, Cloud Build, Artifact Registry, Cloud Tasks, Cloud Run Jobs, Scheduler, Secret Manager, SSE다. UI 키트, Redis, Kubernetes, BigQuery, FAISS는 사용하지 않는다.

검색은 접근 및 문서 버전 필터, FTS 및 벡터 후보, RRF 결합, 재정렬 순서로 합의했다. 모델 ID와 임베딩 차원, LangSmith 요금제는 미정이다. 교육장 키로 지원 목록과 호출 제약을 확인한 후 평가로 모델을 선택한다.

## 확정된 로그인과 권한

플랫폼 서버에서 Microsoft 로그인과 토큰을 관리한다. 브라우저에는 불투명 세션 ID를 HttpOnly 및 Secure 쿠키로 전달하고, C/S 및 TFT API는 플랫폼이 중계한다. 업무 서비스도 내부 호출자와 사용자 및 업무 권한을 검사한다. 세션과 MSAL 상태를 앱 메모리만으로 공유한다고 가정하지 않는다.

사용자 식별은 tid + oid다. 이메일이나 Entra 앱 역할만으로 서비스 사용자를 자동 등록하거나 역할을 부여하지 않는다. 한 사용자에게 여러 역할과 별도의 부서 및 프로젝트 권한을 지정한다.

| 역할 | 기준 |
| --- | --- |
| cs_agent | 권한 있는 C/S 업무 처리 |
| dept_staff | 소속 부서 필수, 배정된 요청 회신 및 후보 추천과 검증 |
| sales | 담당 프로젝트 요구 검토, 부서 인력요청 발행, TFT 구성안 준비 |
| manager | 회사 전체 운영 집계, 관리 대상으로 지정된 C/S 부서 및 TFT 프로젝트의 상세 조회와 일반 업무 담당자 재배정 |

메일, 첨부, 후보 경험 자료는 조회 가능한 업무에 연결된 범위만 허용한다. 전체 집계 권한을 원문이나 개인 메일함 전체 열람 권한으로 확대하지 않는다.

프로젝트 책임자는 새 로그인 역할이 아니다. 해당 프로젝트의 sales 또는 manager 중 해당 프로젝트를 관리하는 manager가 지정 및 변경한다. 현재 지정 책임자만 TFT 구성안을 최종 확정한다. 책임자 미지정이면 확정을 거부하며 변경자, 이전 및 신규 책임자, 사유와 시각을 기록한다. 다른 manager나 프로젝트 생성자에게 최종 확정권이 자동 발생하지 않는다.

sales는 요청 내용을 검토한 뒤 부서 인력요청을 직접 발행한다. manager 또는 책임자의 중간 승인을 추가하지 않는다. 같은 부서 담당자가 추천과 경험 및 가용성 확인을 수행할 수 있도록 단순화한다. 추천 및 검증 기록은 구분하고 최종 구성안만 책임자가 확정한다.

후보 인력과 로그인 사용자는 별도다. 후보마다 Microsoft 계정을 만들지 않는다. RMA 번호, 방문일과 보증 승인을 AI가 만들지 않는다. 업무 상태와 AI 작업 상태도 별도로 관리한다.

## 확정하지 않은 세부사항

- 시연 주관회사. LS ELECTRIC은 추천만 했으며 사용자 확정은 없다.
- 배포용 마이그레이션 로그인 주체와 공유 search 및 runtime의 세부 접근 정책. 앱 DB 계정 3개와 기본 스키마 권한, 버전 및 사양과 Connector 연결은 아래 실제 적용 결과를 따른다.
- 웹 정적 파일 호스팅, 내부 서비스 인증 및 사용자 맥락 전달의 상세 구현.
- 사용자 등록 및 역할 부여의 관리 주체, 평가 실행 및 설정 변경의 허용 역할.
- 프로젝트 생성과 참여자 추가의 세부 권한, 자기 자신을 책임자로 지정할 수 있는지 등 추가 제약.
- Gemini 모델 ID, 임베딩 모델과 차원, LangSmith 요금제.
- TFT, OCR, 검색과 생성 초안의 합격선 및 성능과 부하 조건.
- 데이터 및 API 계약 초안의 DTO, 세부 스키마명, outbox, UUID, 커서, 작업 enum, 15초 heartbeat, 7일 이벤트 보관 및 24시간 멱등성 키 보관 등. 이 항목들은 구현 제안이며 모두 사용자 확정이라고 보고하지 않는다.

## GCP 현재 설정

최신 인프라 상태: 2026-10-21 13:00 UTC 실조회에서 서울 rfp-document-processing 큐가 RUNNING이고 tft-runtime의 큐 수준 roles/cloudtasks.enqueuer를 확인했다. 동시 처리 2, 전송률 2/s, maxAttempts=5, backoff 10~300초이며 자동 burst 크기는 10이다. 큐 처리 API 및 OIDC 연결과 실제 문서 작업은 아직 없다. DB, 앱 계정 3개와 스키마, 서울 이미지 및 파일 저장소와 Secret 3개가 준비됐고 Cloud Run 검증 Jobs 3개에서 Secret 버전 1로 실제 DB 접속 성공을 확인했다. 세부 ACL 및 로컬 로그인 검증은 사용자 보고이며 클라우드 Job 결과와 접속 사용자 로그는 에이전트가 실조회했다. 비밀번호 값은 조회하거나 출력하지 않았다. Cloud Run 서비스 목록과 서울 Cloud Build 트리거 목록은 비어 있다. 다음은 소유 경계에 맞춰 C/S, TFT, 플랫폼 FastAPI 실행 골격 및 Docker 빌드 구성을 준비하고, Cloud Build 실행 계정 및 파이프라인과 실제 배포를 연결하는 단계다. Microsoft 로그인/메일과 Gemini 지원 모델 및 할당량 사전 확인도 남아 있다. 13:00 UTC 당시에는 앱 코드가 없었으며 이후 구현한 실행 골격은 아래 최신 코드 상태를 따른다. 사용자 터미널 실행과 에이전트 조회 확인 흐름을 유지한다.

최신 코드 상태: 사용자가 세 앱의 실행 골격 작성을 요청해 KAN-63으로 backend/api/{cs,tft,platform}/main.py와 공통 HTTP 설정, backend/shared 설정 및 응답 DTO를 구현했다. pyproject.toml과 uv.lock, 공통 Dockerfile, 소스 허용 목록인 .dockerignore 및 .gcloudignore, 빌드 전 테스트 후 이미지 3개를 만드는 cloudbuild.yaml을 추가했다. backend/README.md가 로컬 실행과 검증 기준이다. health는 프로세스 상태만 확인하며 실제 DB, OAuth, 메일이나 LLM 준비 상태를 보고하지 않는다. production 개발 인증 설정은 시작 시 거부하고 HTTP 문서 경로를 숨긴다. 업무 API 및 실제 인증, 작업과 SSE는 아직 없다.

검증: 로컬 및 Docker에서 테스트 18건과 하위 사례 2건, Ruff, 실제 생성한 docs/openapi/{cs,tft,platform}.json 일치 검사를 통과했다. runtime 이미지 3개를 localhost에서 실행해 health 응답과 앱 선택, UID/GID 10001 및 production 문서 경로 차단, 비밀값 및 data 파일 부재를 확인했다. 임시 컨테이너는 제거했다. Cloud Build SDK 파서와 실제 소스 업로드 목록도 검증했다. 실제 Cloud Build 실행과 이미지 업로드, 서비스 배포 및 GitHub 트리거는 아직 하지 않았다. 다음은 Cloud Build 실행 계정의 이미지 쓰기와 로그 및 소스 읽기 권한을 설정하고 빌드한 뒤, 서비스를 비공개로 배포하고 내부 인증 및 Tasks OIDC를 연결하는 단계다. 사용자 터미널 실행과 에이전트 조회 확인 흐름을 유지한다. 커밋, push와 PR은 만들지 않았다.

이전 준비 이력: 사용자가 `team4` DB에 postgres로 관리자 접속하고 진단 SELECT 결과를 확인했다고 답했다. 12:24 UTC 당시 API 조회에서는 postgres만 존재했다. 다음 사용자 실행용으로 `infra/sql/001_service_roles.sql`과 `002_verify_service_roles.sql`을 준비했다. 앱 계정 3개 및 NOLOGIN 마이그레이션 역할 3개와 서비스별 8개 스키마를 분리한다. 로컬 PostgreSQL 16.15에서 비슈퍼유저 관리자로 적용 및 격리 검증을 통과했으나 실제 Cloud SQL 17 적용은 아직 하지 않았다. `infra/sql/README.md`의 두 파일 실행 후 PASS 확인과 앱 계정별 `\password` 설정, 별도 로그인 검증이 다음 단계다. 공유 search와 runtime 정책 및 업무 테이블은 아직 없다. 실제 비밀번호는 읽거나 출력하지 않았고 `.env.example`에만 앱 접속 정보 예시를 추가했다.

2026-10-21 12:18 UTC 후속 확인: 사용자가 새 서울 Cloud SQL `team4-sql`을 생성했다. `RUNNABLE`, PostgreSQL 17 Enterprise, 2 vCPU 및 8GiB, ZONAL, SSD 20GB 및 자동 확장 상한 50GB다. 공인 IPv4 활성, 허용 네트워크 목록 없음, Connector 강제 및 ENCRYPTED_ONLY, 일일 백업 7개와 삭제 보호 활성으로 확인했다. 12:24 UTC 후속 재조회에서 업무 DB `team4`의 생성도 확인했다. 사용자는 postgres만 있고 서비스별 스키마와 접속 계정은 아직 없다. 자세한 실제 설정은 docs/infra-preflight.md를 따른다. 다음 안내는 관리자 접속 확인과 서비스별 계정 및 스키마 분리이며 실행 계정 IAM 및 실제 DB 연결 검증은 남아 있다.

| 항목 | 값 |
| --- | --- |
| 계정 | skkuls0106@skkukdp.re.kr |
| 프로젝트 ID | proj-skkuls0106-183255377938 |
| 프로젝트 이름 | skkuls0106 |
| 프로젝트 번호 | 1015616094444 |
| CLI 설정 | default 구성에서 위 계정 및 프로젝트 활성 |
| 프로젝트 상태 | ACTIVE, 결제 연결 활성 |
| 직접 부여 역할 | roles/owner와 roles/editor |
| 배포 리전 | asia-northeast3 |

계정 재인증을 사용자가 완료했고, 요청에 따라 CLI 기본 프로젝트를 algomon에서 위 교육장 프로젝트로 변경했다. 접근 가능 목록에 genial-insight-x4r4b도 있었지만 배포 대상으로 선택하지 않았다. 앞으로도 명령에 교육장 프로젝트 ID와 계정을 명시한다.

Cloud Tasks와 Cloud Build는 사용자가 활성화했고 재조회로 확인했다. Run, SQL Admin, Scheduler, Artifact Registry, Secret Manager, Storage, IAM 및 IAM Credentials, Logging과 Monitoring도 활성 상태였다. Service Networking은 미활성이었으며 DB 연결 방식에 따라 필요 여부를 정한다.

11:52 UTC 재조회에서도 다음 실행 계정 세 개의 존재와 disabled=false를 확인했다. 프로젝트 IAM 정책에 세 계정의 직접 역할 부여는 없었다. 리소스별 IAM 및 상속 권한과 Cloud Run 연결은 아직 검증하지 않았다.

```text
cs-runtime@proj-skkuls0106-183255377938.iam.gserviceaccount.com
tft-runtime@proj-skkuls0106-183255377938.iam.gserviceaccount.com
platform-runtime@proj-skkuls0106-183255377938.iam.gserviceaccount.com
```

기존 Compute 기본 계정, Gemini 관련 계정과 gh-deploy 계정도 있다. 기존 gh-deploy가 있다고 이번 CI/CD가 GitHub Actions로 확정된 것은 아니다. 이번 선택은 Cloud Build다.

리전 제한과 IAM 도메인 제한에 대해 조회한 유효 조직 정책은 ALLOW였으나 모든 조직 정책과 실제 생성 권한을 검증한 것은 아니다. 서비스별 할당량과 Gemini 키 제약은 남아 있다. 새 서울 Cloud SQL은 위와 같이 생성했고 나머지는 앞선 점검에서 미생성이었다. 서울과 미국의 Tasks 큐, 서울 Scheduler 및 Jobs는 없었고, Cloud Build 트리거도 서울, 미국, global 조회에서 없었다. 이번 DB 점검에서는 나머지 리소스를 재조회하지 않았다.

## 기존 RFI 비용 정리 상태

| 리소스 | 2026-10-21 11:52 UTC 재조회 상태 |
| --- | --- |
| us-central1의 rfi-web | 삭제 완료. 전체 Cloud Run 서비스 목록 비어 있음 |
| rfi-db | 최종 백업 없이 삭제 완료. 삭제 작업 DONE, SQL 인스턴스 목록 비어 있음 |
| DB 백업 | 프로젝트 전체 backups list 비어 있음 |
| 버킷 및 객체 | 객체 4,864개 전체 버전과 버킷 삭제 명령 성공. 일반 버킷 목록 비어 있으나 영구 삭제 완료는 아님 |
| 버킷 soft delete | 삭제 전에 retentionDurationSeconds=0 조회. 11:55 UTC API 검증에서 복구 보존 버킷 1개 확인, 2026-10-28 20:51:25 KST 만료 예정 |
| 이미지 저장소 | us-central1/rfi 삭제 완료. 전 리전 저장소 목록 비어 있음 |
| rfi-collect Job | 삭제 완료. us-central1 Job 목록 비어 있음 |
| rfi-collect-daily Scheduler | 삭제 완료. us-central1 Scheduler 목록 비어 있음 |
| Secret | cloud-database-url 삭제 완료. gemini-api-key만 보존, 값은 읽지 않음 |

Compute VM, Compute 디스크와 예약 IP, 스냅샷, 포워딩 규칙, 라우터, Spanner, Pub/Sub 토픽과 실행 중 Cloud Build는 조회 결과 없었다. Vertex AI 엔드포인트 및 Agent Engine은 us-central1과 asia-northeast3에서 없었으며 다른 리전까지 전수 확인한 것은 아니다.

삭제 전 점검에서 Cloud Run 인스턴스 및 요청과 과금 지표는 시계열이 반환되지 않았다. 지표 공백을 실시간 실행 수나 과금액 0의 증거로 단정하지 않았다. 현재는 실행 리소스와 DB 등의 부재를 확인했지만 GCS 복구 보존 기간의 저장 비용 조건은 남을 수 있다. 보존한 Gemini Secret과 실제 과거 청구액까지 없어졌다는 뜻도 아니다.

결제 계정 상세 조회는 PERMISSION_DENIED였다. 이 프로젝트의 BigQuery 데이터셋도 없어서 기존 Billing export로 실제 비용을 집계하지 못했다. 무료 한도와 교육장 크레딧 적용 후 실제 청구액, 다른 프로젝트의 Gemini 호출 비용과 과거 청구액까지 확인한 것은 아니다.

DB 삭제 작업 ID는 `e8581e6a-b2bc-4462-a235-0e8900000032`이며 11:51:57 UTC에 DONE을 확인했다. 버킷 삭제 로그는 `/tmp/ls-rfi-bucket-delete.log`에 있으며 객체와 버킷을 포함한 Removing 기록 4,865개, 명령 종료 코드 0, ERROR 없음으로 확인했다. 이 로그는 삭제 명령의 성공 증거이며 복구 보존 종료 증거는 아니다. 복구 보존 버킷 generation은 `1790669428479733565`다. 삭제 전 크기와 비용 점검 이력은 docs/gcp-cost-audit.md에 보존한다.

## WBS와 담당자

현재 파일은 [업무별 WBS](https://docs.google.com/spreadsheets/d/11X85xRDncd_xg3EPhcoke6dhF1fhTByKzhpRxLk6ow8/edit#gid=1680447802)다. 이전 파일 대신 이 파일을 사용한다.

148개 기록은 완료 이력 30개와 신규 계획 118개로 구성되며 신규 계획에 예비시간 2개가 포함된다. 과거 완료 이력을 새 개발 완료 실적으로 합산하지 않는다. 업무별 접기 및 펼치기, 날짜와 공수 계산, 실제 Jira 링크를 반영했다.

| WBS 구분 | 담당자 | 기존 담당 |
| --- | --- | --- |
| A | 김나영 | 인프라, 인증, 메일, C/S API와 규칙 |
| B | 오새결 | 데이터, 검색, AI와 평가 |
| C | 김종하 | 프론트, TFT 백엔드와 평가 관리 API |

실제 인프라 수행은 오새결이 맡지만 위 배정은 바꾸지 않는다. 검토자는 A 작업에 C, B 작업에 A, C 작업에 B를 기본으로 등록했다. 공통 플랫폼 분리에 따른 구현 분담 조율은 담당자 변경과 별개다.

WBS의 주요 시트 ID는 WBS 1680447802, 작업 상세 1361536552, 사용 안내 1869531214다. 원본 템플릿의 숨김 안내 및 사용조건 시트를 보존했다. 전체 시트를 다시 쓰거나 템플릿 서식을 초기화하지 않는다.

## Jira 상태와 연결

Atlassian 플러그인은 설치 및 로그인 완료 상태다. 같은 연결이 보이면 다시 설치하거나 OAuth 연결을 요청하지 않는다.

```text
Site: https://lsteam4.atlassian.net
cloudId: e0304739-9601-430a-95dc-13b4ea50240e
Project: KAN
```

148개 WBS 기록은 중복 연결 3개 때문에 실제 작업 이슈 146개에 연결된다. 신규 작업 144개를 생성하고 기존 2개를 재사용했다. 에픽 9개와 Blocks 의존성 263개를 연결 및 검증했다. 이번 결정 과정에서 추가 작업 이슈를 새로 만들거나 완료 처리하지 않았다.

| 이슈 | 용도 | 인수인계 조회 상태 |
| --- | --- | --- |
| KAN-55 | 공통 ID, 데이터 계약과 스키마 | 진행 중 |
| KAN-56 | OpenAPI, 오류, 작업과 SSE 계약 | 진행 중 |
| KAN-57 | 화면, 상태 전이와 권한 행렬 | 해야 할 일. 결정은 설명에 반영 |
| KAN-62 | 교육장 GCP 권한, 서비스와 할당량 | 해야 할 일. 사전 점검 결과는 설명에 반영 |
| KAN-63 | FastAPI 실행 골격 | 해야 할 일. 제목은 이전 두 앱 표현이 남음 |
| KAN-65 | 로컬 PostgreSQL과 초기 마이그레이션 | 해야 할 일 |
| KAN-119 | TFT 프로젝트, 역할, 요청 및 후보 API | 해야 할 일 |
| KAN-122 | 부서 인력요청 | 해야 할 일. 추가 승인 없는 발행 결정 반영 |
| KAN-127 | 책임자 확정과 변경 영향 | 해야 할 일. 책임자 지정 규칙 반영 |

위 상태는 구현 및 동료 검토 완료와 구분해 유지했다. KAN-25 배포 에픽에도 공용 시연 환경 결정을 기록했다. 비용 정리 실행을 Jira에 완료 처리한 상태는 아니다. 다음 세션에서 현재 상태를 읽고 결과를 갱신한다.

초기 재개 시 KAN-11 범위 정합화, KAN-54 C/S 필드 충돌, KAN-61 기획 원본 정합화, KAN-76 실제 OAuth 및 Graph 사전 검증, KAN-82 평가 기준, KAN-83 데이터 분할 이력도 확인한다.

Jira 도구 응답은 content의 JSON 문자열 안에 data가 중첩될 수 있다. 최신 설명을 읽어 보존한 뒤 수정한다. 날짜 확인은 view=full과 명시적인 duedate 필드를 사용한다. 일반 설명 수정으로 상태를 완료로 만들지 않는다.

## 저장소 현재 상태

```text
작업 브랜치: KAN-55-KAN-56-development-contracts
확인한 마지막 커밋: 3907f55
원격: https://github.com/LS-Final-Project/ls-final-project
```

이번 세션에서 앱 코드를 구현하거나 커밋, push, PR을 생성하지 않았다. 기존 tracked 파일은 README.md, CONTRIBUTING.md와 PR 양식이었다. AGENTS.md, CLAUDE.md, .env.example, .gitignore, data 디렉터리는 기존부터 untracked였으므로 사용자 파일로 보존한다. 이번 docs 파일과 handoff.md도 아직 커밋되지 않았다. git add .로 data와 비밀값을 함께 추가하지 않는다.

| 파일 | 용도 |
| --- | --- |
| docs/decisions.md | 사람이 읽을 최신 결정 요약 |
| docs/architecture.md | 확정 사항과 구현 제안을 포함한 개발 아키텍처 |
| docs/data-contract.md | 공통 데이터 계약 초안 |
| docs/api-contract.md | API, 작업 및 SSE 규약 초안. 실제 OpenAPI 파일은 아직 없음 |
| docs/infra-preflight.md | 계정, 프로젝트, API와 실행 계정 확인 기록 |
| docs/gcp-cost-audit.md | 비용 점검, 실행 차단과 RFI 삭제 완료 기록 |
| handoff.md | 다음 세션 재개 절차와 주의사항 |

README에는 과거 견적 범위와 이전 개발 일정이 남아 있다. AGENTS의 단일 role 필드 표현은 최신 다중 역할 설계와 맞추는 작업이 필요하다. 사용자 소유 파일을 임의로 덮어쓰지 말고 새 결정과의 차이를 검토한다.

## 기획 및 참고 자료

- [C/S 기획 원본](https://docs.google.com/document/d/1DhnpC5vyPEIhk3QJ3IRSTLX5f-U-HfpRMdCaGDVnGpg/edit)
- [RFP 기획 원본](https://docs.google.com/document/d/1Zz_ps2r9oRM5wTt75L-RboCc1knqJ1WYGZZ9DZc09fQ/edit)
- [스토리보드 초안](https://docs.google.com/document/d/1fprJf4b1P0dqKcv7KbFFSxnX2xn_cZM0/edit)
- [Notion 프로젝트 페이지](https://www.notion.so/79f910042c37834bb0a18103863d1986)
- [조직 조사 자료 폴더](https://drive.google.com/drive/folders/1LK1Qa6bFtsBwChqV3vt_P4V1KLP9rEau)

RFP 원본에는 이전 견적 및 제안 준비 흐름과 ChromaDB 등 기술 후보가 남아 있었다. 현재 TFT 범위와 이번 스택으로 되돌려 읽지 말고 차이를 정합화한다. C/S 원본에는 secondary_category를 포함한 5개 출력 표현이 남아 있지만 AGENTS와 최신 분류 계약은 4개다.

위 차이는 이번 세션의 원본 조회 시점 기준이다. 팀원이 이후 수정했을 수 있으므로 재개 시 최신 내용을 다시 확인한다. gws의 일반 JSON 응답은 stdout을 임시 파일로 리다이렉트해 필요한 본문만 파싱한다. --output은 바이너리 다운로드용이므로 Google Docs JSON 저장에 사용하지 않는다. Drive Markdown 다운로드는 alt=media와 --output을 사용하고 출력 파일을 만들 디렉터리를 먼저 준비한다.

조직 조사 폴더의 Markdown 10개를 gws로 읽었다. DART 기반 회사별 조직, 2022~2026 변화, 공식 홈페이지의 사업 및 직군 조사다. 자료의 출처와 분석 매핑은 구분되어 있다. 이번 세션에서 그 DART 원문 전체를 재검증한 것은 아니다. 과거 조직을 자동으로 현재 조직에 병합하거나 개인 경력 및 가용성을 공시로 추정하지 않는다.

로컬 설계 및 기획 자료는 `/home/canae/ls_main/`에 있으며 과거 범위의 문서가 섞여 있다. 오래된 자료에 나온 BigQuery, IAP, 특정 Gemini 모델명과 견적 기능을 최신 결정으로 사용하지 않는다.

## 데이터 및 평가 제약

data는 읽기 전용이다. 파일 생성, 수정과 삭제를 하지 않는다. Cloud Storage의 이전 RFI 자료 정리 요청을 로컬 data 삭제 요청으로 해석하지 않는다.

C/S 분류 LLM 출력은 category_code, urgency_flag, provided_info, return_subtype 네 개다. priority, assigned_dept, product_family는 결정론적 규칙으로 계산한다. build_input 한 곳에서 입력 화이트리스트를 강제하고 정답 필드가 들어가지 않게 한다.

FLT-042는 인용 금지다. PHONE 180건의 본문 긴급도 라벨 누설과 SAFETY 28건의 표본 부족을 고려하고 채널별 결과를 분리한다. test 597건은 반복 비교에 사용하지 않고 최종 자료의 기존 사용 및 노출 이력을 먼저 확인한다.

C/S 합격선은 AGENTS와 decisions 문서의 확정 표를 따른다. 평가 파이프라인과 UI를 먼저 마련해 품질, 토큰, 비용, 지연, 오류, 재시도와 캐시를 비교한다. 아직 파이프라인을 구현하거나 실제 모델 평가를 수행하지 않았다.

## 다음 작업 순서

1. KAN-63 실행 골격과 Docker 이미지 및 health OpenAPI는 로컬 검증을 완료했다. backend/README.md를 읽고 Cloud Build 실제 실행과 배포 준비로 이어간다. DB, 저장소, Secret 및 큐 생성 절차를 다시 요구하지 않는다.
2. Cloud Build 실행 및 배포 계정과 최소 권한, 이미지 빌드 및 배포 파이프라인을 구성하고 실제 서비스를 배포한다. 내부 호출자 인증과 사용자 권한 검증을 함께 연결한다.
3. TFT 처리 API URL 및 OIDC 호출 주체와 Cloud Run 호출 권한을 Cloud Tasks에 연결하고, PostgreSQL 작업 상태와 중복 방지 및 재시도를 검증한다. 공유 search 및 runtime 정책, 업무 테이블 마이그레이션은 남아 있다. 모델 차원은 고정하지 않는다.
4. 실제 Microsoft 로그인과 메일 접근 및 발송, Gemini 키 지원 모델과 할당량 확인을 초반에 병행한다.
5. 최소 평가 실행 및 결과 화면을 연결한 뒤 모델과 검색 구성을 비교한다. 김종하의 TFT 기획 검토와 KAN-61 원본 정합화도 연결한다.
6. 기존 RFI 복구 보존 버킷은 10월 28일 20:51:25 KST 이후 부재를 재조회한다. 영구 삭제 승인은 이미 받았으며 예약 작업은 만들지 않았다.

## 임시 보조 자료

이 파일과 docs가 인수인계의 기준이다. 아래 /tmp 자료는 같은 환경에서 남아 있을 때만 참고하며, 존재 여부와 클라우드 상태를 다시 확인한다.

- /tmp/jira-wbs-ready-plan.json: 148개 WBS 기록, 담당자, 완료 조건과 실제 Jira 키 매핑.
- /tmp/jira-wbs-completion.json: Jira 생성 및 의존성 검증 결과.
- /tmp/jira-run-journal.json: 이슈와 의존성 생성 기록.
- /tmp/ls-rfi-cleanup-plan.json: 삭제 전 후보 목록과 확인 대기 상태를 담은 과거 자료. 현재 상태는 이 handoff와 삭제 후 실조회를 따른다.
- /tmp/ls-rfi-bucket-delete.log: 승인 후 버킷과 전체 객체 버전 삭제 로그. 명령 종료 코드 0.
- /tmp/ls-rfi-web-disabled-check.json: manual scaling 0 재조회 기록.
- /tmp/ls-cost-complete-storage.jsonl: 버킷 전체 페이지 용량 집계와 결제 데이터셋 조회 결과.
- /tmp/ls-handoff-service-accounts.json: 실행 계정 존재 확인.
- /tmp/ls-drive-business-research/: 공유한 조직 조사 Markdown 10개.

Cloud Run 조회 임시 JSON에는 환경 설정이 포함될 수 있으므로 파일 전체를 채팅에 출력하거나 커밋하지 않는다. 필요한 메타데이터만 선택해 읽는다.
