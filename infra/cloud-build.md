# KAN-67 빌드와 비공개 배포

대상은 교육장 프로젝트 `proj-skkuls0106-183255377938`, 계정 `skkuls0106@skkukdp.re.kr`, 서울 `asia-northeast3`다. GCP 변경 명령은 사용자가 실행한다. 아래는 준비된 절차이며 실제 빌드 및 배포 완료 기록이 아니다.

## 1. 빌드 실행 기반

저장소 루트에서 실행한다. `.env`를 source할 필요가 없다.

```bash
bash infra/setup_cloud_build.sh
```

스크립트는 다음 리소스를 만들거나 필요한 직접 권한을 추가한다. 기존 DB, 파일 버킷, 큐와 Secret 값은 변경하지 않는다.

| 대상 | 설정과 근거 |
| --- | --- |
| `cloudbuild-runner` 서비스 계정 | 이미지 생성 및 업로드 전용. Cloud Run 배포와 Secret 읽기 권한은 주지 않는다 |
| 서울 `team4` Artifact Registry | 해당 실행 계정에 저장소 수준 `roles/artifactregistry.writer` |
| `proj-skkuls0106-183255377938-team4-build-source` 버킷 | 서울, 균일한 버킷 접근 및 공개 접근 차단. 업로드된 소스 읽기만 실행 계정에 허용 |
| 프로젝트 로그 | 실행 계정에 `roles/logging.logWriter`. 빌드 로그는 Cloud Logging에 기록 |
| 실행 계정 사용 | 교육장 사용자에게 해당 빌드 계정의 `roles/iam.serviceAccountUser` |

교육장 사용자는 기존 프로젝트 Owner 권한으로 소스를 업로드하고 빌드를 제출한다. 팀원 전체에 Owner를 부여하는 절차가 아니다. 리소스 생성 직후 IAM 전파가 늦어질 수 있으므로 실패 시 오류를 확인하고 같은 명령을 다시 실행한다. 기존 리소스의 상속 권한을 제거하는 작업은 하지 않는다.

## 2. 실제 빌드

현재 초기 셋업은 사용자 요청으로 main에 직접 반영했다. 최신 main의 빌드 구성과 API 골격을 사용해 실행한다. 과거 PR 브랜치의 순차 병합이나 임시 worktree를 준비할 필요가 없다.

```bash
bash infra/submit_cloud_build.sh
```

이 스크립트는 HEAD의 허용된 파일만 `git archive`로 임시 디렉터리에 추출해 업로드한다. `.env`, `data`, Git 메타데이터, 인프라 설정 스크립트와 미커밋 파일은 업로드하지 않는다. 추적 중인 빌드 입력에 변경이 있으면 중단한다. 아직 커밋하지 않은 신규 파일은 HEAD에 포함되지 않으므로 게시한 커밋에 들어갔는지 먼저 확인한다. 작업 공간의 원본은 변경하지 않고 임시 디렉터리는 종료 시 제거한다.

Cloud Build에서 전체 40자리 `COMMIT_SHA`가 없으면 실패한다. lint, 포맷, mypy, 백엔드 단위 테스트 및 OpenAPI 일치 검사 후 세 이미지를 빌드한다. 각 이미지는 전체 커밋 SHA 태그, BUILD_ID 태그와 OCI revision label을 가진다. 로컬 Python 실행이 아니라 Linux Docker 빌드 결과를 배포 대상으로 삼는다. DB 비밀번호 등록 도우미 테스트는 별도 `python3 -m unittest discover -s infra/tests -v`로 검증한다.

빌드 ID와 로그 URL을 보관한다. 성공한 빌드의 결과는 아래처럼 확인한다.

```bash
gcloud builds describe BUILD_ID \
  --project=proj-skkuls0106-183255377938 \
  --account=skkuls0106@skkukdp.re.kr \
  --region=asia-northeast3 \
  --format='yaml(id,status,substitutions.COMMIT_SHA,serviceAccount,results.images,logUrl)'
```

`BUILD_ID`는 위 제출 명령에서 나온 실제 UUID로 바꾼다. 원격 `SUCCESS`, 커밋 SHA, 실행 계정과 이미지 3개의 digest를 확인한 뒤 배포한다. 실제 빌드가 실패하면 로그를 확인하고 수정한 커밋으로 다시 빌드한다. GitHub 자동 빌드 트리거와 프론트 빌드는 이 초기 백엔드 빌드 구성에 포함하지 않았다.

## 3. 비공개 배포 미리보기

```bash
python3 infra/deploy_private.py BUILD_ID
```

기본 동작은 빌드 메타데이터 읽기와 명령 출력이다. 실제 배포하려면 사용자가 실행한다.

```bash
python3 infra/deploy_private.py BUILD_ID --apply
```

도우미는 성공한 빌드의 커밋 태그와 서비스별 결과 digest를 모두 확인한 뒤 `team4-cs`, `team4-tft`, `team4-platform`을 순서대로 배포한다. 태그를 다시 조회해 최신 이미지를 선택하지 않는다. 중간 실패 시 앞서 배포한 서비스는 남고 후속 배포는 중단한다. 3개 서비스를 원자적으로 교체하는 배포는 아니다.

| 항목 | 초기 배포값 |
| --- | --- |
| 이미지 | 빌드 결과의 `image@sha256:...` |
| 호출 | IAM 검사 활성, 익명 호출 차단. `ingress=all`은 외부 네트워크 경로를 허용하되 IAM 인증을 요구하는 설정 |
| 서비스 계정 | 대응하는 cs-runtime, tft-runtime, platform-runtime |
| 환경 | `ENVIRONMENT=production`, `DEV_AUTH_ENABLED=false` |
| 크기 | 서비스당 CPU 1, 메모리 512MiB, 최소 1 및 최대 2 인스턴스 |
| 요청 | 동시 처리 20, timeout 300초. 부하 및 SSE 검증 후 조정할 초기값 |
| DB | 기존 team4-sql Connector 연결, 서비스별 DB 사용자와 Secret 버전 1 |

최소 1 인스턴스는 확정된 배포 방향이며 배포하면 유휴 상태에도 비용이 발생한다. health는 프로세스 상태만 의미한다. 현재 골격은 전달된 DB 설정을 사용한 업무 CRUD나 OAuth, 메일, Tasks 처리 및 SSE를 구현하지 않았다. `/api/v1/<service>/ready`는 실제 FastAPI DB 연결과 계정 및 스키마, vector 및 FTS를 검사한다. Cloud SQL에서 vector 확장이 준비되지 않았다면 503으로 표시된다. 로컬 검증만으로 클라우드 준비를 완료 처리하지 않는다.

`roles/run.invoker`는 아직 추가하지 않는다. 업무 서비스의 플랫폼 호출, Tasks OIDC와 사용자 맥락 검증은 후속 구현에서 연결한다. 네트워크 ingress를 internal로 제한하는 배치는 VPC 경로와 함께 따로 결정한다.

배포 후 익명 요청 거절과 IAM 정책에 allUsers 및 allAuthenticatedUsers가 없는지 확인한다. 운영자 확인은 인증을 처리하는 로컬 프록시를 사용할 수 있다.

```bash
gcloud run services proxy team4-cs \
  --project=proj-skkuls0106-183255377938 \
  --account=skkuls0106@skkukdp.re.kr \
  --region=asia-northeast3 --port=8085
```

다른 터미널에서 `curl --fail http://127.0.0.1:8085/health`로 확인한다. 프록시는 로컬 확인용이며 브라우저의 최종 로그인 구조는 아니다.

근거: [사용자 지정 Cloud Build 실행 계정](https://docs.cloud.google.com/build/docs/securing-builds/configure-user-specified-service-accounts), [빌드 제출](https://docs.cloud.google.com/sdk/gcloud/reference/builds/submit), [Cloud Run 배포 인자](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy).

## 2026-10-22 실행 확인

준비 명령과 실제 원격 빌드는 사용자 터미널에서 실행했다. 에이전트는 읽기 전용 조회로 확인했다.

| 항목 | 확인 결과 |
| --- | --- |
| 빌드 | 401a8afb-f9b9-4a51-878e-2a96fc373e99, SUCCESS |
| 소스 | main f04298161ea5b234d6e31521d186a6dd29f893f4 |
| C/S digest | sha256:c2b5fb55358750195684f0b2980f538ebbc37d32ccece860399a3054494da864 |
| TFT digest | sha256:7a7ba9603b34fc797f1cfc1760cb995e30bbaf74b2dcc1f51e05fd386fd6ce4f |
| 플랫폼 digest | sha256:87df6b7d82c227456026568f4cb34fe19b3cc9f0b2fb8b2cd6a6ba170967f74e |
| GitHub Application checks | [39629875011 성공](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39629875011) |
| GitHub Collaboration checks | [39629874699 성공](https://github.com/LS-Final-Project/ls-final-project/actions/runs/39629874699) |

이 빌드의 digest 기준 미리보기 후 사용자가 실제 비공개 배포를 실행했다. 세 Cloud Run 서비스의 Ready=True, digest 및 실행 계정, Secret 버전 1과 production 및 최소 인스턴스 1을 확인했다. 세 서비스 모두 익명 health 403, 인증 health 200이고 플랫폼 개발 계정 경로는 인증 요청에도 404다. 첫 DB ready 검사는 세 서비스 모두 503이어서 Cloud SQL vector 확장 적용을 사용자에게 요청했다. 후속 DB 검증은 infra-preflight.md와 handoff.md를 따른다. 문서만 추가한 후속 main과 실제 빌드 소스 SHA를 혼동하지 않는다.

### Cloud SQL 확장 확인

첫 ready 검사가 503일 때 무작정 재배포하지 않는다. SQL 연결 오류와 스키마 및 확장 미준비를 구분한다. 이 배포에서는 아직 vector 적용을 확인하지 못했으므로 사용자에게 Cloud SQL Studio의 team4 DB에서 관리자 권한으로 다음 SQL 실행을 요청했다. 앱 계정에 확장 생성 권한을 주지 않는다.

```sql
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

확장 확인 후 인증한 `/api/v1/cs/ready`, `/api/v1/tft/ready`, `/api/v1/platform/ready`를 다시 조회한다. 성공은 DB 계정과 스키마, vector 및 FTS까지 확인한다. 신규 identity 테이블과 실제 OAuth는 별도 업무 마이그레이션이며 로컬 합성 계정을 클라우드에 넣지 않는다.
