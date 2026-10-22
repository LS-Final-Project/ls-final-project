# 서비스별 CI와 CD

프론트 web, C/S cs, TFT tft, 공통 플랫폼 platform은 각각 검사하고 이미지와 배포를 관리한다. CI는 GitHub Actions, 실행 이미지 생성과 CD는 Cloud Build가 맡는다. 이 문서는 준비한 동작과 실제 적용 여부를 구분하며 아래 적용 상태와 검증 절에 실행 근거를 기록한다.

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
4. 제목은 `배포: C/S 로그인과 화면 라우팅`처럼 적고, 본문에 포함 Jira 번호, 검증 결과와 남은 제약을 기록한다. Create pull request를 누르고 검토자 한 명을 지정한다. 배포 PR에는 `[완료 KAN-번호]` 표시를 넣지 않는다.
5. PR의 CI 성공과 동료 승인을 확인한다. 병합 버튼 옆 화살표에서 **Create a merge commit**을 선택하고 Merge pull request → Confirm merge를 누른다. dev와 main 사이에는 Squash and merge를 사용하지 않는다.
6. Actions에서 방금 생성된 main 커밋의 Application checks와 Deploy changed services가 성공했는지 확인한다. 바뀌지 않은 서비스의 skipped는 정상일 수 있다. [공용 웹](https://team4-web-gew6ci4pgq-du.a.run.app/)에서 해당 기능도 확인한다. PR 병합 자체는 배포 성공을 뜻하지 않는다.
7. 배포가 끝나면 [동기화 비교 화면](https://github.com/LS-Final-Project/ls-final-project/compare/dev...main)에서 **base: dev / compare: main**으로 PR을 만들고 **Create a merge commit**으로 병합한다. main의 병합 이력을 dev에 돌려놓는 단계다. 차이가 없으면 생략한다.

dev와 main은 계속 쓰는 브랜치이므로 삭제하지 않는다. 충돌이나 CI 실패가 나오면 병합을 중단하고 원인을 해결한다. 강제 push로 맞추지 않는다. 현재 요금제에서는 검토·CI 조건이 서버에서 강제되지 않아 병합하는 사람이 직접 확인한다.

버전 태그는 검증한 배포를 다시 찾기 위한 이름이다. 예를 들어 첫 검증 배포를 `v0.1.0`으로 기록할 수 있지만, dev → main 병합만으로 버전 번호나 GitHub Release가 자동 생성되지는 않는다. 태그와 Release 발행은 실제 배포 확인 후 별도로 진행한다.

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

## 확인한 적용 상태

2026-10-22 사용자가 최초 웹 빌드와 배포를 실행한 뒤 자동 CD를 활성화했다. [자동 배포 39644133647](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39644133647)에서 네 서비스의 독립 이미지 생성 및 별도 CD가 모두 성공했다. 소스는 afa938cb4ded382f2c25cf2565acb1450831bd93이며 실제 배포된 SHA와 digest가 빌드 결과와 일치함을 확인했다.

| 서비스 | 이미지 Cloud Build | 별도 CD Cloud Build |
| --- | --- | --- |
| cs | 524fc3a1-039c-4e79-ad2f-2c4b675a2e96 | 6b82d2e1-08fc-4bb8-a4e6-cc90170e1fc0 |
| tft | 568ab7ae-efa6-483e-8413-36346627b9cc | aca46953-4160-40e0-8dcc-fad02b261ddb |
| platform | 985c8466-fee0-4a31-b020-aa6f108f4ccf | 05f08544-96ef-4bd3-9641-9217813cda26 |
| web | 3d2538ca-920d-42af-8faf-429a045247eb | dd68e598-933d-497f-9ae0-6ebdb6f0acf6 |

[공용 웹](https://team4-web-gew6ci4pgq-du.a.run.app/)과 플랫폼 health/ready 중계는 200이며 DB, vector, FTS 준비 상태가 정상이다. 사용자 정보와 개발 계정 경로는 404이고 백엔드 3개의 직접 익명 호출은 403이다. 웹은 web-runtime으로 실행하며 DB 연결 및 DB Secret을 설정하지 않았다.

후속 프론트 링크 수정 c111cc3에서는 [CI 39645850820](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39645850820)의 frontend만 실행하고 백엔드와 infrastructure 및 evaluation은 생략했다. [자동 CD 39645939828](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39645939828)도 웹만 선택해 성공했다. 이미지 빌드는 5a69a5d0-bad2-411e-b99e-1430a4226763, CD는 b8bb9ff0-f6bc-4e32-9133-693f7be2a6bc다. 웹만 team4-web-00003-nlc로 바뀌고 백엔드 3개 리비전은 유지되는 것을 확인했다. 실제 새 웹의 안내 링크와 플랫폼 준비 상태도 정상이다.

실제 검증 중 발견한 기존 Docker 빌더의 인자 전달과 전체 예시 검사, 배포 빌드의 미참조 COMMIT_SHA를 수정했다. CD는 실행 확인한 Cloud SDK 이미지 digest를 고정하고 SDK 내장 Python으로 도우미를 실행한다. 빌드 실패나 배포 전 검증 실패를 성공으로 처리하지 않는다.

## 과거 AI SDK 실행 의존성 반영 이력

공통 AI SDK와 Python 실행 의존성을 반영한 소스 e557c8bf816326dceef2671b7bbb9fb32b34e88a의 [CI 39706740083](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39706740083)와 [자동 CD 39706861091](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39706861091)가 성공했다. CI에서 백엔드별 Docker 검사와 네트워크 없는 SDK import, 프론트 계약 소비자와 평가 및 인프라 검사가 통과했다. 공통 백엔드 변경이므로 백엔드 3개만 배포하고 웹은 유지했다.

| 서비스 | 이미지 Cloud Build | 별도 CD Cloud Build | 실제 리비전 |
| --- | --- | --- | --- |
| cs | 566476cd-1c57-496e-aae1-1dad9d9887dc | 2ad53be2-1813-4113-bab2-dd5861ce5a43 | team4-cs-00003-ln2 |
| tft | f788fbfb-a595-48be-81a4-104ec3c96d6d | 613b9b61-0b82-46a2-97e7-a85315c39a55 | team4-tft-00003-fv2 |
| platform | 41b9d490-47f3-4c4e-8d78-69baed964147 | 42ecb74e-cf94-40f7-a801-70f137e32511 | team4-platform-00003-sfr |

아래는 기본 경로를 Gemini API로 되돌리기 전의 배포 이력이다. 현재 코드의 기본값과 구분한다. 총 6건의 빌드가 SUCCESS이며 실제 SHA와 이미지 digest가 빌드 메타데이터와 일치했다. 각 서비스 인증 ready 200, database/vector/fts=true와 익명 접근 403을 확인했다. C/S와 TFT에만 Vertex 제공자 및 프로젝트, global 생성 위치와 us-central1 임베딩 위치 및 초기 모델을 명시했다. 플랫폼에는 모델 환경설정이나 호출 권한을 추가하지 않았다. 웹은 team4-web-00003-nlc 그대로이며 공개 health와 플랫폼 ready 중계가 200이다. 모델 호출은 앱 기동이나 CI 및 health 검사에서 하지 않는다.

## dev 기본 브랜치의 배포 인증

기본 브랜치를 dev로 바꿔도 배포 워크플로는 main에서 실행한다. workflow_run의 GITHUB_REF는 기본 브랜치이므로 main 전용 OIDC 조건과 맞지 않는다. 이를 피하려고 main push에서 시작하고 실제 CI 성공을 먼저 기다리는 방식으로 전환했다. GCP의 저장소 및 main과 배포 워크플로 경로 제한은 변경하지 않았다. 수동 실행에서 dev를 고르면 plan job을 생략해 GCP 인증과 빌드를 실행하지 않는다.

근거: [GitHub workflow_run 실행 문맥](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run).

## 2026-10-22 dev 기본 브랜치 전환 실제 검증

소스 e683910a9615fa255c3aefc90963a16a0bb514c2에서 [main CI 39721051535](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39721051535)와 [dev CI 39721089161](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39721089161)가 모두 성공했다. 양쪽 모두 백엔드 3개와 프론트, 평가 및 인프라 검사를 통과했다. 협업 검사는 main 39721051530과 dev 39721089152가 성공했다.

GitHub 기본 브랜치를 dev로 전환했고 Squash와 Merge commit을 허용하며 Rebase merge는 비활성화했다. [dev 수동 배포 검증 39721193839](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39721193839)에서 plan과 deploy가 모두 skipped였으며 GCP 인증을 실행하지 않았다. [main 배포 39721051542](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39721051542)는 동일 SHA의 CI 성공 대기 후 기존 main 전용 OIDC로 인증하고 네 서비스를 각각 빌드 및 배포했다. 새 CI 기준과 변경 선택 도구의 수정은 기존 보수적 영향 규칙에 따라 네 서비스 검사 및 빌드 대상으로 처리됐다.

| 서비스 | 이미지 Cloud Build | 별도 CD Cloud Build | Ready 리비전 |
| --- | --- | --- | --- |
| web | 807bf5d5-3ebd-4885-817f-aed38025f559 | bd36174a-6b57-43f8-9038-b038c1c33fe2 | team4-web-00004-mm2 |
| cs | 583250f4-c94f-4c09-b84b-9823405af192 | 5222e328-c05b-48f1-9561-13517067d1fd | team4-cs-00004-qnz |
| tft | abd90623-38b0-469b-ae3f-2ca8584b3079 | 49016dac-f38a-49a1-a7ce-40d8b933cbd1 | team4-tft-00004-r7j |
| platform | 38c6ebe9-f005-4fea-b34e-5f043bd11901 | 1fb57a80-ff50-4caa-930e-3a5d7108ed7a | team4-platform-00004-c2r |

네 서비스의 실제 commit-sha가 위 소스와 일치하고 Ready 리비전을 확인했다. 공용 웹 health와 플랫폼 ready 중계는 HTTP 200이며 database/vector/fts=true다. 이번 검증을 위해 GCP IAM을 변경하거나 실제 AI 및 메일 호출을 추가하지 않았다. 후속 결과 문서의 dev 커밋은 main 배포 SHA와 다를 수 있다.

## Gemini API로 전환하는 다음 배포

기본 제공자를 Gemini API로 변경하고 C/S와 TFT에만 AI_PROVIDER=gemini 및 Secret Manager의 GEMINI_API_KEY=gemini-api-key:1을 전달하도록 준비했다. 이전 Vertex 위치 환경 변수는 새 배포에서 제거한다. SDK의 선택형 어댑터는 남아 있지만 기본 경로가 아니다. 플랫폼과 웹에는 모델 키를 전달하지 않는다.

사용자가 `uv run --frozen python infra/register_gemini_secret.py --apply`로 기존 Secret 버전 1을 보존하고 두 실행 계정에 읽기 권한을 추가했다. 스크립트는 기본 실행 시 미리보기이며 키를 명령행이나 로그에 출력하지 않는다. 기존의 다른 IAM 바인딩이나 Vertex IAM은 변경하지 않았다.

Gemini API의 실제 합성 생성 2건과 로컬 코드 검증은 완료했으며, Cloud Run은 아직 이전 배포다. dev → main 배포 후 main CI와 CD, 새 리비전의 제공자 설정 및 Secret 참조를 확인한다. 런타임의 실제 모델 호출은 별도 확인하며 health 성공만으로 생성 성공을 주장하지 않는다.
