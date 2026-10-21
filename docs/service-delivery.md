# 서비스별 CI와 CD

프론트 web, C/S cs, TFT tft, 공통 플랫폼 platform은 각각 검사하고 이미지와 배포를 관리한다. CI는 GitHub Actions, 실행 이미지 생성과 CD는 Cloud Build가 맡는다. 이 문서는 준비한 동작과 실제 적용 여부를 구분하며 최종 실행 기록은 handoff.md를 따른다.

## 변경에 따른 실행

| 변경 | CI | 이미지 및 CD |
| --- | --- | --- |
| frontend 실행 코드와 의존성 | 웹 테스트와 TypeScript 및 빌드 | web |
| backend/api/cs, tft, platform | 해당 앱, 공통 회귀와 계약 소비자 검사 | 해당 앱 |
| backend/shared, api/common, Python 의존성 | 백엔드 전체와 계약 소비자 | 백엔드 전체 |
| backend/engine, backend/agent | 영향받는 C/S와 TFT 검사 | C/S와 TFT |
| OpenAPI와 개발용 모의 예시 | 원본 일치 및 프론트 호환성 | OpenAPI는 해당 앱, 개발 모의 예시만 바꾸면 웹 CD 없음 |
| eval | 합성 평가 테스트 | API CD 없음 |
| infra 마이그레이션과 배포 도구 | 도구 테스트 | 자동 DB 마이그레이션 없음 |
| 설명 Markdown | Application result만 확인 | 없음 |

알 수 없는 실행 코드 경로는 전체 검사 대상으로 보수적으로 처리한다. 공유 엔진과 에이전트가 플랫폼에서도 쓰이기 시작하면 scripts/changes.py의 영향 범위를 함께 수정해야 한다. 현재 루트 tests의 기존 사례는 공통 회귀 검사이며, 새 업무 테스트는 tests/cs, tests/tft, tests/platform 아래에 추가한다.

PR은 공통 기준 브랜치와의 변경을 검사한다. main은 마지막 성공한 Application checks 이후 누적 변경을 검사하므로 실패한 CI 뒤에 문서만 수정해도 실패 범위를 건너뛰지 않는다. 최종 Application result는 작업의 실패와 취소를 모아 확인한다. 서비스별 작업이 정상 생략되는 경우도 이 결과는 완료된다.

## 배포 판단

CI 성공한 main만 CD 대상으로 삼는다. 각 서비스의 실제 배포 commit-sha와 현재 main을 비교해 변경을 찾는다. 직전 push만 비교하지 않으므로 배포를 건너뛴 중간 커밋도 반영한다. 오래된 CI 이벤트와 빌드 중 main 변경은 배포 전에 다시 확인해 이전 버전 배포를 막는다. 서비스별 배포는 직렬화하고 다른 서비스는 독립 실행한다.

Cloud Build의 서비스 이미지 빌드와 CD는 별도 실행 및 별도 계정이다. CD는 성공한 빌드 메타데이터의 정확한 SHA 및 digest를 검증한다. 기존 IAM 공개 범위는 변경하지 않으며 기대한 웹/백엔드 경계와 다르면 중단한다. 실패한 앱 때문에 다른 앱의 이미지를 임의로 바꾸지 않는다. 자동 롤백은 아직 제공하지 않는다.

## 처음 한 번: 사용자 실행

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

이후 웹 HTTPS와 플랫폼 ready 연결 및 업무 경로 차단을 확인한다. 아직 실제 Microsoft 인증이 없는 공용 웹은 정적 개발 안내와 플랫폼 health/ready만 중계한다. 임의 API, 개발 로그인, 메일과 업무 데이터는 중계하지 않는다. 실제 플랫폼 중계와 사용자 세션은 별도 업무 구현이다.

## 자동 CD 활성화

GitHub 저장소 변수 GCP_WIF_PROVIDER에는 다음 공개 리소스명을 넣는다.

```text
projects/1015616094444/locations/global/workloadIdentityPools/team4-github/providers/github-main
```

GCP 계정과 OIDC 및 첫 웹 배포 검증 후 사용자가 활성화한다. 이 명령 이후 main의 성공한 CI가 서비스별 빌드와 CD를 시작한다.

```bash
gh variable set GCP_RELEASE_ENABLED --body true --repo LS-Final-Project/ls-final-project
gh workflow run deploy-services.yml --repo LS-Final-Project/ls-final-project
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
