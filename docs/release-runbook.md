# 배포 확인과 되돌리기 런북 (KAN-157)

시연 환경을 배포한 뒤 확인할 것, 문제가 생겼을 때 이전 리비전으로 되돌리는 법, DB 백업 복구 훈련 순서를 적는다.
순서대로 따라 하고 결과를 KAN-157(배포 묶음이면 KAN-159)에 남긴다.

- 대상: 교육장 프로젝트 `proj-skkuls0106-183255377938`, 계정 `skkuls0106@skkukdp.re.kr`, 서울 `asia-northeast3`
- 서비스: Cloud Run `team4-web`, `team4-platform`, `team4-cs`, `team4-tft`. worker는 Cloud Run Jobs(`infra/deploy_workers.py`).
  TFT는 새 개발을 멈췄지만 서비스는 배포돼 있으므로 확인 대상에 넣는다.
- GCP 명령은 사용자(또는 배포 담당) 터미널에서 실행한다. 이 문서의 명령 가운데 상태를 바꾸는 것에는 **[변경]**을 붙였다.
- 출력에 토큰, Secret 값, 원문 메일, 인증 콜백 URL이 섞이면 공유하지 않는다.

아래 예시는 셸 변수를 쓴다.

```bash
P=proj-skkuls0106-183255377938; A=skkuls0106@skkukdp.re.kr; R=asia-northeast3
G="--project=$P --account=$A --region=$R"
```

## 1. 배포 전: 이전 앱 호환 확인

배포 뒤 이전 리비전으로 트래픽을 되돌려도 DB는 되돌리지 않는다. CD는 마이그레이션을 자동 실행하지 않고, 되돌리는
SQL도 없다. 그래서 **새 마이그레이션을 적용한 DB에서 지금 운영 중인 코드가 그대로 돌아야** 되돌리기가 안전하다.
배포 PR(dev → main)을 만들기 전에 아래를 확인한다.

먼저 "이전 리비전"이 무엇인지 정한다(읽기 전용).

- 서비스별로 지금 트래픽을 받는 커밋을 본다. CD가 일부 서비스만 실패했으면 서비스마다 다를 수 있다.

  ```bash
  for s in web platform cs tft; do
    gcloud run services describe team4-$s $G --format="value(metadata.name,metadata.labels.commit-sha)"
  done
  git rev-parse origin/main
  ```

  모두 `origin/main`과 같으면 아래 `--base origin/main`을 그대로 쓴다. 다르면 다른 커밋마다 `--base <그 커밋>`으로 한 번씩
  더 돌린다.
- 운영 DB에 이전 리비전의 마이그레이션이 실제로 다 적용돼 있는지 본다. [SQL 안내의 전체 적용 순서](../infra/sql/README.md#전체-적용-순서)
  표에 있는 "적용 전 확인" 쿼리(읽기 전용)로 확인하고 공용 상태 열에 날짜를 적는다. 이 전제가 틀리면 아래 결과도 믿을 수 없다.

그다음 저장소에서 아래를 실행한다. 비용이 없고 GCP를 부르지 않는다.

```bash
git fetch origin
# 정적 검사: 이전 리비전(origin/main) 뒤 마이그레이션이 추가만 됐는지, 이전 표를 깨는 문장이 없는지
uv run --frozen python -m scripts.compat_check migrations --base origin/main

# 실행 검사: 빈 전용 DB에 새 마이그레이션을 적용하고 origin/main의 tests 전체를 돌린다(약 3분).
# Mac은 도중에 잠들면 수십 분 걸리므로 caffeinate -i를 앞에 붙인다.
docker rm -f kan71-test-db 2>/dev/null   # 같은 이름의 검사 DB가 남아 있으면 지운다(빈 DB에서만 시작한다)
docker run -d --rm --name kan71-test-db -e POSTGRES_PASSWORD=test-only -e POSTGRES_DB=kan77_auth_test \
  -p 127.0.0.1:55433:5432 pgvector/pgvector:pg17
caffeinate -i uv run --frozen python -m scripts.compat_check run --base origin/main \
  --db postgresql://postgres:test-only@127.0.0.1:55433/kan77_auth_test
```

- 정적 검사의 `block`이 비어 있어야 한다. 막는 변경은 다음과 같다.
  - 이미 있던 마이그레이션 파일의 수정, 삭제, 이동
  - 이전 표의 열 삭제, 이름 변경, 형 변경, DEFAULT 삭제, 스키마 이동
  - 기존 열의 NOT NULL 강제
  - DEFAULT 없는 NOT NULL 열 추가
  - 표 삭제
- 정적 검사의 `review`는 기계로 판정하지 못하는 변경이다. 실행 검사가 통과하면 넘어간다. 여기에 드는 것은 다음과 같다.
  - 이전 표의 제약 추가·삭제·검증(넓히는지 좁히는지), 행 보안·정책
  - 이전 앱 역할(cs_app·tft_app·platform_app)의 권한 회수(표·스키마·함수·시퀀스)
  - 이전 함수의 교체·삭제, 인덱스·타입 삭제와 타입 변경
- 정적 검사는 정규식이라 문자열 안의 세미콜론 같은 경우를 놓칠 수 있다. 판정은 실행 검사가 기준이다.
- 실행 검사는 `"compatible": true`여야 한다. 조건은 두 가지다.
  - 이전 리비전 tests가 모두 통과한다.
  - 그 검사 전후의 스키마·권한 스냅샷이 같다. 이전 검사의 준비 단계가 옛 권한을 다시 줘서 통과한 것처럼 보이는 경우를 여기서 잡는다.
    스냅샷에는 열, 표·열·함수·시퀀스·스키마 권한, 기본 권한, 제약, 인덱스, 뷰, 함수, 정책, 트리거, enum, 역할과 멤버십이 든다.
- 한계: 이전 리비전의 tests가 다루는 경로만 확인한다. 검사가 없는 이전 코드 경로가 새 스키마에서 깨지는지는 알 수 없다.
  마이그레이션 적용은 현재 CI의 검사 준비를 따른다. 검사 준비가 어떤 파일도 언급하지 않으면 실행 전에 멈추지만,
  언급만 하고 적용하지 않는 경우까지는 잡지 못한다.

## 2. 배포 뒤: 검증한 이미지가 그대로 나갔는지

main CD는 같은 커밋의 Application checks 성공을 기다린 뒤 빌드한다. 배포는 빌드 결과의 정확한 digest로 한다
(`infra/deploy_private.py`). 배포가 끝나면 서비스마다 다음 셋이 서로 일치하는지 확인한다.

1. 트래픽 배분과 지금 트래픽을 받는 리비전의 이미지 digest, `commit-sha` 라벨. 트래픽 항목은 0% 리비전·태그까지
   여러 개일 수 있으므로 전체를 보고 **100%인 항목**의 리비전을 쓴다. `latestRevision: true`가 있어야 다음 배포가
   자동으로 트래픽을 받는다(5번 참고).

   ```bash
   s=cs
   gcloud run services describe team4-$s $G --format="yaml(status.traffic,metadata.labels.commit-sha)"
   gcloud run revisions describe <100%인 리비전> $G --format="yaml(spec.containers[0].image,status.imageDigest)"
   ```

2. 그 리비전을 만든 이미지 빌드의 결과 digest. CD는 두 단계다.
   - 이미지 빌드: `cloudbuild.service.yaml`·`cloudbuild.web.yaml`. 여기의 `results.images`에 digest가 있다.
   - 배포 빌드: 태그 `team4-cd`. 이미지를 만들지 않고 `_BUILD_ID`로 이미지 빌드를 가리킨다.

   그래서 배포 빌드에서 `_BUILD_ID`를 꺼내 그 빌드를 본다.

   ```bash
   gcloud builds list $G --filter="tags=team4-cd" --limit=8 \
     --format="table(id,status,substitutions._SERVICE,substitutions._BUILD_ID,substitutions.COMMIT_SHA,createTime)"
   gcloud builds describe <_BUILD_ID> $G --format="yaml(status,substitutions.COMMIT_SHA,results.images)"
   ```

3. GitHub Actions에서 그 커밋의 Application checks가 성공했는지

트래픽 100%가 한 리비전에 있고, 그 이미지가 `@sha256:`으로 고정돼 있어야 한다. 그 digest가 이미지 빌드의
`…/team4/<서비스>:<커밋>` digest와 같고 커밋이 CI 성공 커밋과 같으면 통과다. 하나라도 다르면 **5. 되돌리기**로 간다.

검증한 리비전에만 트래픽을 넘기고 싶으면 수동 배포에서 다음 순서로 한다(선택). 리비전 태그 URL도 서비스와 같은 IAM을
따른다. `--no-traffic`과 `--to-tags`는 "최신 리비전 자동 배분"을 끈다. 끝나면 마지막 줄로 다시 켠다. 켜지 않으면 다음 CD
배포가 트래픽 0%로 만들어져 아무도 새 코드를 받지 못한다.

```bash
python3 infra/deploy_private.py BUILD_ID --service cs          # 명령만 출력한다
# 출력된 gcloud run deploy 명령 끝에 --no-traffic --tag=candidate를 붙여 실행한다 [변경]
curl -fsS -H "Authorization: Bearer $(gcloud auth print-identity-token --account=$A)" \
  https://candidate---team4-cs-gew6ci4pgq-du.a.run.app/api/v1/cs/ready
gcloud run services update-traffic team4-cs $G --to-tags=candidate=100   # [변경]
gcloud run services update-traffic team4-cs $G --to-latest                # [변경] 최신 리비전 자동 배분을 다시 켠다
```

## 3. 시연 준비 확인

서비스 배포 명령은 `--min=1 --max=2`다(`infra/deploy_private.py`). 콜드 스타트 없이 시연할 수 있도록 인스턴스가 최소
1개 떠 있다. `--max=2`는 설계 초기값이다(`docs/decisions.md`). 로컬 모의 모델 부하 시험(`docs/load-test.md`)에서는 플랫폼
인스턴스 2개를 한 프로세스의 슬롯 40으로 근사해 3배 부하를 통과했지만 여유가 작았고, 배포 환경에서는 아직 재지
않았다. 그대로 적용됐는지 확인한다.

```bash
for s in web platform cs tft; do
  echo team4-$s
  gcloud run services describe team4-$s $G --format="yaml(spec.template.metadata.annotations)" \
    | grep -E "minScale|maxScale"
done
```

`autoscaling.knative.dev/minScale`이 1, `maxScale`이 2여야 한다. 인스턴스 1개가 계속 떠 있으므로 시연이 없는 기간에도
요금이 나간다. 시연 기간이 끝난 뒤 줄일지는 배포 담당이 정한다.

```bash
gcloud run services update team4-cs $G --min=0   # [변경] 시연 기간이 끝난 뒤에만
```

## 4. 운영에서 개발 모드 차단 확인

코드 차단은 두 겹이다(`backend/shared/settings.py`, 검사 `tests/test_applications.py`).

- `ENVIRONMENT=production`에서 `DEV_AUTH_ENABLED=true`이면 앱이 시작하지 않는다.
- Cloud Run(`K_SERVICE`가 있음)에서 `ENVIRONMENT`가 production이 아니어도 앱이 시작하지 않는다.

웹의 개발 확인 화면은 빌드할 때 빠진다(`frontend/src/App.tsx`의 `import.meta.env.DEV`). 운영 웹 첫 화면은 계정 화면이다.
개발 계정 선택 API(`/api/v1/platform/dev/…`)는 local·test 환경에서 `DEV_AUTH_ENABLED`가 켜져 있을 때만 열리고,
그 밖에는 404다(`backend/api/platform/auth/routes.py`).

배포된 리비전의 값과 실제 응답을 확인한다.

```bash
for s in platform cs tft; do
  echo team4-$s
  gcloud run services describe team4-$s $G --format="yaml(spec.template.spec.containers[0].env)" \
    | grep -A1 -E "name: (ENVIRONMENT|DEV_AUTH_ENABLED)$"
done
# 개발 계정 목록 API가 404인지(플랫폼은 IAM 인증 요청)
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token --account=$A)" \
  https://team4-platform-gew6ci4pgq-du.a.run.app/api/v1/platform/dev/accounts
```

다음을 확인한다.
- 백엔드 셋이 `ENVIRONMENT=production`, `DEV_AUTH_ENABLED=false`다.
- 개발 계정 API가 `404`다.
- 공용 웹(<https://team4-web-gew6ci4pgq-du.a.run.app/>) 첫 화면이 개발 확인 화면이 아니라 계정(로그인) 화면이다.

## 5. 이전 리비전으로 되돌리기

배포 뒤 오류율이 오르거나 시연 기능이 깨지면, 원인을 찾기 전에 먼저 트래픽을 직전 리비전으로 돌린다. 문제가 된
서비스만 돌리고, 다른 서비스의 리비전은 건드리지 않는다.

```bash
s=cs
gcloud run revisions list --service=team4-$s $G --limit=5 \
  --format="table(metadata.name,metadata.creationTimestamp,metadata.labels.commit-sha,status.conditions[0].type,status.conditions[0].status)"
gcloud run services update-traffic team4-$s $G --to-revisions=<직전 리비전>=100   # [변경]
gcloud run services describe team4-$s $G --format="yaml(status.traffic)"
```

`--to-revisions`로 고정하면 "최신 리비전 자동 배분"이 꺼진다. 이 상태에서 CD가 새 리비전을 만들면 트래픽은 0%로 남는다.
CD는 서비스의 `commit-sha` 라벨만 보고 배포가 끝났다고 판단하므로 아무도 알아채지 못한다. 원인을 고친 배포가 끝나면
반드시 아래로 자동 배분을 다시 켜고, 2번 확인을 다시 한다.

```bash
gcloud run services update-traffic team4-$s $G --to-latest   # [변경] 고친 리비전이 배포된 뒤에만
```

- 되돌린 뒤 확인할 것:
  - 공용 웹의 `/health`와 `/api/v1/platform/ready`
  - C/S·TFT의 ready(IAM 인증 요청)
  - 로그인
  - 시연 대표 기능 1건
- DB는 되돌리지 않는다. 1번 호환 확인을 통과한 배포라면 직전 리비전이 새 스키마에서 돌 가능성이 높다(검사가 다루는 경로 기준). 되돌린 뒤 오류 로그부터 확인한다(`docs/operations-basics.md`).
- worker Job은 서비스 트래픽과 따로 간다. 되돌려야 하면 `infra/deploy_workers.py`로 직전 빌드 digest를 다시 배포한다.
- 원인을 고친 커밋을 main에 올리기 전까지 수동 배포를 하지 않는다. 고친 배포 뒤에는 위의 `--to-latest`를 잊지 않는다.

## 6. DB 백업 복구 훈련

목표는 "자동 백업으로 실제로 복구되는지"를 운영 DB(`team4-sql`)에 쓰지 않고 확인하는 것이다. 새 인스턴스에 복원하고,
확인한 뒤 바로 지운다.

> **비용**: 복원용 Cloud SQL 인스턴스는 떠 있는 동안 요금이 나온다. 실행 전에 원본과 같은 등급으로 예상 비용을
> 확인하고 승인을 받는다. 훈련이 끝나면 같은 날 삭제한다.
>
> **데이터 노출**: 복원본에는 운영과 같은 데이터와 앱 계정 비밀번호가 들어간다. 인가 네트워크를 비워 두고(아무 IP도
> 허용하지 않음), 접속은 `gcloud sql connect`의 5분 임시 허용만 쓴다. 출력 결과에 원문을 남기지 않는다.
>
> **덮어쓰기 주의**: `--restore-instance`가 `team4-sql`이면 운영 DB가 백업 시점으로 덮어써진다. 삭제 보호는 복원을
> 막지 않는다. 복원 대상은 아래 변수로만 지정한다.

```bash
DRILL=team4-sql-restore-$(date +%m%d)   # 지운 인스턴스 이름은 한동안 다시 쓸 수 없어 날짜를 붙인다
test "$DRILL" != team4-sql && echo "복원 대상: $DRILL"
```

1. 원본 설정과 백업 확인(읽기 전용)

   ```bash
   gcloud sql instances describe team4-sql --project=$P --account=$A \
     --format="value(databaseVersion,settings.tier,settings.edition,settings.dataDiskSizeGb,settings.backupConfiguration.enabled,settings.backupConfiguration.pointInTimeRecoveryEnabled)"
   gcloud sql backups list --instance=team4-sql --project=$P --account=$A --limit=5 \
     --format="table(id,status,type,startTime,endTime)"
   ```

   `pointInTimeRecoveryEnabled`가 true이면 시점 복구도 쓸 수 있다. 값을 기록한다.

2. 복원용 인스턴스 만들기 **[변경, 비용]**. DB 버전·등급·에디션은 1의 값과 같게, 디스크는 원본 이상으로 한다.

   ```bash
   gcloud sql instances create "$DRILL" --project=$P --account=$A --region=$R \
     --database-version=<1의 값> --tier=<1의 값> --edition=<1의 값> --storage-size=<원본 이상> \
     --no-backup --no-deletion-protection
   gcloud sql instances describe "$DRILL" --project=$P --account=$A \
     --format="yaml(ipAddresses,settings.ipConfiguration.authorizedNetworks)"   # 인가 네트워크가 비었는지
   ```

3. 가장 최근 성공한 자동 백업을 새 인스턴스에 복원 **[변경]**. 대상이 `team4-sql`이 아닌지 한 번 더 본다.

   ```bash
   gcloud sql backups restore <BACKUP_ID> --backup-instance=team4-sql \
     --restore-instance="$DRILL" --project=$P --account=$A
   ```

4. 복원된 DB 확인. `gcloud sql connect`가 잠시 내 IP를 허용한다. 비밀번호는 화면에 남기지 않는다.

   ```bash
   gcloud sql users set-password postgres --instance="$DRILL" --project=$P --account=$A --prompt-for-password   # [변경]
   gcloud sql connect "$DRILL" --user=postgres --database=team4 --project=$P --account=$A
   ```

   ```sql
   SELECT nspname FROM pg_namespace WHERE nspname IN
     ('identity','runtime','evaluation','cs','tft','integration','mailbox_auth','checkpoint_cs','notify') ORDER BY 1;
   SELECT count(*) AS users FROM identity.users;
   SELECT count(*) AS tickets, max(created_at) AS latest_ticket FROM cs.tickets;
   SELECT count(*) AS jobs, max(created_at) AS latest_job FROM runtime.jobs;
   ```

   다음을 확인한다.
   - 스키마가 모두 있다.
   - 행 수가 0이 아니다.
   - 최신 생성 시각(`created_at`)이 백업 시각(`endTime`) 이전이다.

   표나 열 이름이 다르면 `\dt cs.*`로 확인해 맞춘다.

5. 복원 인스턴스 삭제 **[변경]**

   ```bash
   gcloud sql instances delete "$DRILL" --project=$P --account=$A
   gcloud sql instances list --project=$P --account=$A --format="table(name,state)"
   ```

6. 기록할 것:
   - 백업 ID와 시각
   - 복원 시작·완료 시각(복구에 걸린 시간)
   - 4의 확인 결과
   - 삭제 확인

## 7. 기록 양식

KAN-157(또는 KAN-159) 댓글에 아래를 붙인다.

```
배포 확인 (YYYY-MM-DD HH:MM KST, 실행: 이름)
- 호환: migrations block 0 / review N, run compatible=true (base <sha>, head <sha>, 이전 tests N passed)
- 이전 리비전: 서비스별 commit-sha, 운영 DB 적용 상태 확인 결과
- 이미지: 서비스별 100% 리비전 · digest · commit-sha = 이미지 빌드 digest · CI 성공 커밋 (일치/불일치), latestRevision 여부
- 시연: minScale 1 / maxScale 2 (서비스별)
- 개발 모드: 백엔드 ENVIRONMENT=production · DEV_AUTH_ENABLED=false, dev API 404, 웹 첫 화면 계정 화면
- 되돌리기: 실행 안 함 / 실행(서비스, 이전 리비전, 확인 결과, --to-latest 복구 시각)
- 백업 복구: 백업 ID, 복원 소요 시간, 확인 결과, 복원 인스턴스 삭제 확인
```
