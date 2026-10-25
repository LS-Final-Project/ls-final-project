# 서비스별 CI와 CD

프론트 web, C/S cs, TFT tft, 공통 플랫폼 platform은 각각 검사하고 이미지와 배포를 관리한다. CI는 GitHub Actions, 실행 이미지 생성과 CD는 Cloud Build가 맡는다. 평소 배포는 아래 dev → main 절차를 따른다. 이미 구성된 공용 환경을 사용하며 팀원의 로컬 시작에는 인프라 준비 명령이 필요 없다.

## 변경에 따른 실행

| 변경 | CI | 이미지 및 CD |
| --- | --- | --- |
| frontend 실행 코드와 의존성 | 웹 테스트와 TypeScript 및 빌드 | web |
| backend/api/cs, tft, platform | 해당 앱, 공통 회귀와 계약 소비자 검사 | 해당 앱 |
| backend/shared, api/common, Python 의존성 | 백엔드 전체와 계약 소비자 | 백엔드 전체 |
| backend/engine, backend/agent | 영향받는 C/S와 TFT 검사 | C/S와 TFT |
| OpenAPI와 개발용 모의 예시 | 원본 일치 및 프론트 호환성 | OpenAPI는 해당 앱, 개발 모의 예시만 바꾸면 웹 CD 없음 |
| eval 및 tests/eval | Ruff·포맷, 공통 회귀 및 평가 테스트 재귀 수집 | API CD 없음 |
| infra·scripts Python과 배포 도구 | Ruff·포맷, 도구 unittest | 자동 DB 마이그레이션 없음 |
| 설명 Markdown | Application result만 확인 | 없음 |

알 수 없는 실행 코드 경로는 전체 검사 대상으로 보수적으로 처리한다. 공통 백엔드·AI 의존성과 공유 엔진·에이전트 변경은 평가 소비자 검사도 실행한다. 서비스별 테스트는 해당 앱에서, tests/eval은 평가에서, 그 밖의 새 테스트 폴더는 공유 범위로 재귀 수집한다. 공유 엔진과 에이전트가 플랫폼에서도 쓰이기 시작하면 scripts/changes.py의 영향 범위를 함께 수정해야 한다. 현재 루트 tests의 기존 사례는 공통 회귀 검사이며, 새 업무 테스트는 tests/cs, tests/tft, tests/platform 아래에 추가한다.

PR은 공통 기준 브랜치와의 변경을 검사한다. dev와 main은 각 브랜치의 마지막 성공한 Application checks 이후 누적 변경을 검사하므로 실패한 CI 뒤에 문서만 수정해도 실패 범위를 건너뛰지 않는다. 최종 Application result는 작업의 실패와 취소를 모아 확인한다. 서비스별 작업이 정상 생략되는 경우도 이 결과는 완료된다.

## 배포 판단

main push 또는 main을 명시한 수동 실행으로만 CD를 시작한다. GCP 인증과 빌드 전에 동일한 main 커밋의 Application checks 성공을 최대 20분 기다린다. 실패 또는 취소, 시간 초과와 main 전진 시 빌드하지 않는다. dev의 CI 성공은 배포 조건이 아니다. 각 서비스의 실제 배포 commit-sha와 현재 main을 비교해 변경을 찾는다. 직전 push만 비교하지 않으므로 배포를 건너뛴 중간 커밋도 반영한다. 오래된 CI 이벤트와 빌드 중 main 변경은 배포 전에 다시 확인해 이전 버전 배포를 막는다. 서비스별 배포는 직렬화하고 다른 서비스는 독립 실행한다.

Cloud Build의 서비스 이미지 빌드와 CD는 별도 실행 및 별도 계정이다. CD는 성공한 빌드 메타데이터의 정확한 SHA 및 digest를 검증한다. 기존 IAM 공개 범위는 변경하지 않으며 기대한 웹/백엔드 경계와 다르면 중단한다. 실패한 앱 때문에 다른 앱의 이미지를 임의로 바꾸지 않는다. 자동 롤백은 아직 제공하지 않는다.

## 오새결의 평소 배포: dev에서 main으로

처음 셋업을 끝낸 뒤에는 아래 순서만 따른다. main에 병합하면 공용 환경의 자동 배포가 시작되므로 포함할 기능이 준비됐을 때 진행한다.

1. 포함할 기능 PR이 dev에 병합됐고, 최신 dev의 CI가 성공했는지 확인한다.
2. GitHub 저장소에서 Pull requests → New pull request를 누른다. [배포 비교 화면](https://github.com/LS-Final-Project/ls-final-project/compare/main...dev)을 바로 열어도 된다.
3. 왼쪽 **base: main**, 오른쪽 **compare: dev**로 선택한다. base는 받을 곳, compare는 가져올 변경이다. Files changed에서 이번 배포 범위를 확인한다.
4. 제목은 `배포: C/S 로그인과 화면 라우팅`처럼 적고, 본문에 포함 Jira 번호, 검증 결과와 남은 제약을 기록한다. Create pull request를 누르고 최신 head/base SHA의 AI 코드 리뷰 결과를 확인한다. 배포 PR에는 `[완료 KAN-번호]` 표시를 넣지 않는다.
5. PR의 CI 성공과 최신 SHA AI 코드 리뷰 통과를 확인하고 사용자가 배포를 판단한다. 병합 버튼 옆 화살표에서 **Create a merge commit**을 선택하고 Merge pull request → Confirm merge를 누른다. dev와 main 사이에는 Squash and merge를 사용하지 않는다.
6. Actions에서 방금 생성된 main 커밋의 Application checks와 Deploy changed services가 성공했는지 확인한다. 바뀌지 않은 서비스의 skipped는 정상일 수 있다. [공용 웹](https://team4-web-gew6ci4pgq-du.a.run.app/)에서 해당 기능도 확인한다. PR 병합 자체는 배포 성공을 뜻하지 않는다.
7. 배포가 끝나면 [동기화 비교 화면](https://github.com/LS-Final-Project/ls-final-project/compare/dev...main)에서 **base: dev / compare: main**으로 PR을 만들고 **Create a merge commit**으로 병합한다. main의 병합 이력을 dev에 돌려놓는 단계다. 차이가 없으면 생략한다.

dev와 main은 계속 쓰는 브랜치이므로 삭제하지 않는다. 충돌이나 CI 실패가 나오면 병합을 중단하고 원인을 해결한다. 강제 push로 맞추지 않는다. 현재 요금제에서는 검토·CI 조건이 서버에서 강제되지 않아 병합하는 사람이 직접 확인한다.

버전 태그는 검증한 배포를 다시 찾기 위한 이름이다. 예를 들어 첫 검증 배포를 `v0.1.0`으로 기록할 수 있지만, dev → main 병합만으로 버전 번호나 GitHub Release가 자동 생성되지는 않는다. 태그와 Release 발행은 실제 배포 확인 후 별도로 진행한다.

## 인프라 담당 전용: 새 환경 최초 구성

저장소 루트에서 준비한다. 서비스 계정 키를 만들거나 GitHub에 DB Secret을 넣지 않는다.

```bash
bash infra/setup_service_delivery.sh
```

준비하는 항목은 다음과 같다.

- web-runtime: 플랫폼 서비스의 run.invoker만 직접 부여. DB와 DB Secret 접근 없음.
- cloudbuild-runner: 기존 이미지 생성 계정 유지. Cloud Run 배포 권한 없음.
- clouddeploy-runner: run.developer, 빌드 조회와 로그, 이미지 읽기 및 해당 runtime 계정 사용 권한. IAM 공개 설정 변경 권한 없음.
- release-orchestrator: 빌드 제출 및 소스 업로드, 서비스 버전 조회와 빌드/배포 계정 사용 권한.
- GitHub OIDC: 저장소 ID 1418330144와 조직 ID 338908553, refs/heads/main 및 deploy-services.yml 경로로 제한. PR은 신뢰 대상이 아니다.

초기 웹 이미지는 사용자 터미널에서 빌드한다. 새 도구는 커밋된 소스만 올리므로 먼저 main 반영 상태를 확인한다.

```bash
bash infra/submit_cloud_build.sh --service web
```

성공한 BUILD_ID로 미리본 뒤 사용자가 배포한다. 이때 web-runtime 계정과 웹의 초기 공개 정책을 설정한다. backend의 공개 정책은 바꾸지 않는다.

```bash
python3 infra/deploy_private.py BUILD_ID --service web
python3 infra/deploy_private.py BUILD_ID --service web --apply
```

이후 웹 HTTPS와 플랫폼 ready 연결 및 업무 경로 차단을 확인한다. 저장소에는 health/ready와 별도로 KAN-77의 인증 전용 중계 초안을 추가했다. 플랫폼 Microsoft 인증은 기본 비활성이며, 로그인·callback·로그아웃은 설정 전 503을 반환한다. 허용 경로·쿠키·CSRF·설정은 [인증 계약](../contracts/platform/auth.md)을 따른다. 임의 API, 개발 로그인, 메일은 중계하지 않는다. KAN-178 업무 JSON 중계도 기본 비활성이며 아래 별도 확인 후 활성화한다. 이 코드 변경으로 현재 배포의 인증이 활성화되지는 않으며 실제 화면·Outlook 검증과 main 배포는 별도다.

## KAN-178 업무 중계 활성화 전 확인

현재 배포·IAM·운영 잠금은 이 코드 변경으로 바뀌지 않는다. `BUSINESS_RELAY_ENABLED`는
웹·플랫폼·C/S·TFT 모두 기본 false다. 활성화는 사용자 판단 후 운영자 터미널에서 진행한다.
설정값·현재 허용 JSON API·별도 호출자 검증은 [인증 계약](../contracts/platform/auth.md#업무-json-중계와-호출자-검증-kan-178)이 기준이다.

1. 배포할 정확한 SHA의 CI와 AI 리뷰, 필요한 업무·identity 마이그레이션, 실제 Microsoft
   등록 사용자와 로그인/CSRF 화면 연결을 확인한다. 로컬 합성 서명·DB 검사는 실제 공용
   로그인·Google IAM 검증을 대신하지 않는다. 새 TFT/C/S API는 허용 목록 확장도 필요하다.
2. 기존 web-runtime → platform의 run.invoker는 유지한다. platform-runtime → cs 및
   platform-runtime → tft의 **서비스 단위** roles/run.invoker가 필요한지 현재 정책을 읽어
   확인한다. 준비 스크립트의 웹 호출 권한만으로 이 두 호출이 허용된다고 가정하지 않는다.
   프로젝트 전체 invoker나 allUsers 공개 권한을 부여하지 않는다.
3. 서로 다른 정확한 CS/TFT 서비스 URL과 실제 플랫폼 실행 계정 이메일을 백엔드 세 곳에
   주입하고, 플랫폼 Microsoft 운영 인증과 고정 callback을 검증한다. C/S·TFT는 토큰
   audience를 각각 자기 URL과 대조한다. 웹은 기존 PLATFORM_URL을 사용한다. C/S의 CS_MANUAL_DATA_ORIGIN도
   명시한다. 공용 시연은 demo, 실제 문의만 처리하는 배포는 real이며 production 인증
   정책만으로 실제 자료를 판정하지 않는다. 혼합 접수·기존 행의 일괄 출처 변경은 지원하지 않는다.
4. 수신 서비스 호출자 검증이 준비된 뒤 플랫폼·웹 중계를 연결한다. 직접 호출·위조 맥락·
   다른 서비스 audience·미인증·만료·CSRF·범위 밖 프로젝트·권한 회수·upstream 장애를
   실제 공용 환경에서 확인한다. 실제 토큰이나 Cookie를 로그/PR/터미널 기록에 출력하지 않는다.
5. 실패하면 먼저 웹과 플랫폼의 업무 플래그를 false로 되돌려 진입을 닫고 수신 서비스도
   false로 복원한다. 개발 인증으로 대체하지 않는다. 변경 전 revision/환경 값을 기준으로
   복구하고 서비스별 배포 결과를 기록한다.

이 절은 활성화 순서와 검증 조건이며 IAM/환경 변경을 수행하는 자동 스크립트가 아니다.
SSE·비동기 작업과 최종 업무 승인 규칙은 각 기능 티켓에서 별도로 검증한다.

## 인프라 담당 전용: CD 활성화 설정

GitHub 저장소 변수 GCP_WIF_PROVIDER에는 다음 공개 리소스명을 넣는다.

```text
projects/1015616094444/locations/global/workloadIdentityPools/team4-github/providers/github-main
```

GCP 계정과 OIDC 및 첫 웹 배포 검증 후 사용자가 활성화한다. 이 명령 이후 main의 성공한 CI가 서비스별 빌드와 CD를 시작한다.

```bash
gh variable set GCP_RELEASE_ENABLED --body true --repo LS-Final-Project/ls-final-project
gh workflow run deploy-services.yml --repo LS-Final-Project/ls-final-project --ref main
```

비활성화는 같은 변수에 false를 넣는다. 이미 시작한 Cloud Build는 별도로 확인해야 한다.

## 수동으로 한 서비스만 처리

```bash
bash infra/submit_cloud_build.sh --service cs
python3 infra/deploy_private.py BUILD_ID --service cs
python3 infra/deploy_private.py BUILD_ID --service cs --apply
```

cs 대신 tft, platform, web을 사용할 수 있다. 인자 없는 기존 submit 명령은 초기 호환을 위해 백엔드 전체 빌드를 유지한다. 기본 배포 도우미도 예전 전체 빌드의 백엔드 3개를 처리한다. 평소에는 service를 명시한다.

읽기 전용 배포 계획은 아래 명령으로 확인한다.

```bash
python3 infra/release.py plan
```

환경 파일, data와 의존성 및 평가 산출물은 Git archive 및 업로드 허용 목록으로 제외한다. 실제 빌드 SHA는 현재 문서 커밋이나 다른 서비스의 배포 SHA와 다를 수 있다.

## 현재 적용 범위

공개 Cloud Run 웹과 비공개 API 3개, 서비스별 이미지·CD 및 GitHub OIDC가 구성돼 있다. 웹은 플랫폼 health/ready를 중계한다. 로컬의 계정 선택과 합성 업무 예시는 공용 환경의 제품 로그인·업무 기능 완료를 뜻하지 않는다.

Gemini API 키 기반 설정은 dev에 준비돼 있다. 다음 main 배포에서 C/S·TFT의 `AI_PROVIDER=gemini`와 `GEMINI_API_KEY`의 Secret 버전 참조, 실행 계정과 ready를 확인한다. Secret 값은 출력하지 않는다. 모델 호출은 health/ready에 포함하지 않으며 실제 호출 검증은 별도로 수행한다.

배포 워크플로는 main의 정확한 SHA에 대한 CI 성공을 확인한다. 수동 실행에도 `--ref main`을 사용한다. dev에서 배포하거나 OIDC 신뢰 범위를 넓히지 않는다. 변경 서비스의 실제 리비전·이미지 digest·사용 커밋은 Actions와 Cloud Run에서 확인하고 해당 Jira·PR에 결과를 남긴다.

## 후속 구현

제품 OAuth·세션과 업무 API 중계, 업무별 권한, Tasks 처리·OIDC·중복 방지, SSE 및 평가 Jobs·화면은 각 기능 티켓에서 통합한다. DB 마이그레이션은 현재 CD에서 자동 실행하지 않는다. 배포 전에 필요한 SQL 적용 계획과 제공자·소비자 호환성을 확인한다.
