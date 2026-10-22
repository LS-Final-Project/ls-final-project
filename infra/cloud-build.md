# Cloud Build 수동 점검

인프라 담당용이다. 공용 빌드·배포 기반은 구성돼 있으며 평소 배포는 [dev → main 절차](../docs/service-delivery.md#오새결의-평소-배포-dev에서-main으로)를 따른다. GCP 변경은 담당자 터미널에서 실행한다.

대상 프로젝트 `proj-skkuls0106-183255377938`, 계정 `skkuls0106@skkukdp.re.kr`, 리전 `asia-northeast3`를 명시한다. 빌드 계정은 이미지 생성·업로드, 배포 계정은 Cloud Run 갱신을 담당한다. 소스 버킷은 공개 접근을 차단하고 Secret 값은 빌드 입력에 넣지 않는다.

## 서비스 하나를 빌드할 때

검사한 커밋이 원격에 반영됐는지 확인하고 저장소 루트에서 실행한다. web은 cs·tft·platform 중 필요한 값으로 바꾼다.

```bash
bash infra/submit_cloud_build.sh --service web
```

HEAD의 허용 파일을 git archive로 추출한다. `.env*`, data, Git 메타데이터와 미커밋 파일은 업로드하지 않는다. 추적 중인 빌드 입력에 변경이 있으면 중단한다. 신규 파일도 커밋에 들어갔는지 확인한다.

성공한 BUILD_ID를 사용해 소스 SHA·실행 계정·이미지 digest를 확인한다.

```bash
gcloud builds describe BUILD_ID \
  --project=proj-skkuls0106-183255377938 \
  --account=skkuls0106@skkukdp.re.kr \
  --region=asia-northeast3 \
  --format='yaml(id,status,substitutions.COMMIT_SHA,serviceAccount,results.images,logUrl)'
```

## 최초 백엔드 배포 도우미

`infra/deploy_private.py`는 백엔드 3개를 함께 빌드한 성공 BUILD_ID를 받는 최초 배포 도구다. 서비스별 평소 CD와 구분한다. 기본은 읽기와 명령 미리보기이며 `--apply`일 때만 변경한다.

```bash
python3 infra/deploy_private.py BUILD_ID
```

설정은 production, 개발 인증 차단, 서비스별 실행·DB 계정과 Secret 버전 1, CPU 1·512MiB·최소 1·최대 2, 동시 20·timeout 300초다. 빌드 결과의 digest를 사용하며 중간 실패 시 후속 서비스를 중단한다. 여러 서비스의 원자적 교체는 아니다.

## 배포 후 확인

- 백엔드의 익명 호출 차단과 IAM 정책, 대응 실행 계정·이미지 digest·Secret 버전을 확인한다.
- health는 프로세스, ready는 실제 서비스 DB·스키마·pgvector·FTS를 확인한다. 업무·메일·AI 성공은 별도 통합 검증이다.
- 운영자 확인은 IAM 인증 로컬 프록시를 사용할 수 있다.

```bash
gcloud run services proxy team4-cs \
  --project=proj-skkuls0106-183255377938 \
  --account=skkuls0106@skkukdp.re.kr \
  --region=asia-northeast3 --port=8085
```

다른 터미널에서 `curl --fail http://127.0.0.1:8085/health`와 `/api/v1/cs/ready`를 확인한다. 프록시는 운영자 점검용이며 제품 로그인 경로가 아니다. 배포 결과는 해당 PR·Jira와 Actions에 기록한다.
