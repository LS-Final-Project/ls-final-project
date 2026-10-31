# 배포 확인과 되돌리기 런북 (KAN-157)

시연 환경을 배포한 뒤 확인할 것, 문제가 생겼을 때 이전 리비전으로 되돌리는 법, DB 백업 복구 훈련 순서를 적는다.
순서대로 따라 하고 결과를 KAN-157(배포 묶음이면 KAN-159)에 남긴다.

- 대상: 교육장 프로젝트 `proj-skkuls0106-183255377938`, 계정 `skkuls0106@skkukdp.re.kr`, 서울 `asia-northeast3`
- 서비스: Cloud Run `team4-web`, `team4-platform`, `team4-cs`, `team4-tft`. worker는 Cloud Run Jobs(`infra/deploy_workers.py`)
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
배포 PR(dev → main)을 만들기 전에 저장소에서 아래를 실행한다. 비용이 없고 GCP를 부르지 않는다.

```bash
git fetch origin
# 정적 검사: 이전 리비전(origin/main) 뒤 마이그레이션이 추가만 됐는지, 이전 표를 깨는 문장이 없는지
uv run --frozen python -m scripts.compat_check migrations --base origin/main

# 실행 검사: 빈 전용 DB에 새 마이그레이션을 적용하고 origin/main의 tests 전체를 돌린다(약 3분)
docker run -d --rm --name kan71-test-db -e POSTGRES_PASSWORD=test-only -e POSTGRES_DB=kan77_auth_test \
  -p 127.0.0.1:55433:5432 pgvector/pgvector:pg17
uv run --frozen python -m scripts.compat_check run --base origin/main \
  --db postgresql://postgres:test-only@127.0.0.1:55433/kan77_auth_test
```

- 정적 검사의 `block`이 비어 있어야 한다. 막는 변경은 다음과 같다.
  - 이미 있던 마이그레이션 파일의 수정, 삭제, 이동
  - 이전 표의 열 삭제, 이름 변경, 형 변경
  - 기존 열의 NOT NULL 강제
  - DEFAULT 없는 NOT NULL 열 추가
  - 표 삭제
- 정적 검사의 `review`는 기계로 판정하지 못하는 변경이다. 넓혀서 다시 만드는 제약과, 이전 앱 역할의 권한 회수가 여기에 든다. 실행 검사가 통과하면 넘어간다.
- 실행 검사는 `"compatible": true`여야 한다. 조건은 두 가지다.
  - 이전 리비전 tests가 모두 통과한다.
  - 그 검사 전후의 스키마·권한 스냅샷이 같다. 이전 검사의 준비 단계가 옛 권한을 다시 줘서 통과한 것처럼 보이는 경우를 여기서 잡는다.
- 한계: 이전 리비전의 tests가 다루는 경로만 확인한다. 검사가 없는 이전 코드 경로가 새 스키마에서 깨지는지는 알 수 없다.

## 2. 배포 뒤: 검증한 이미지가 그대로 나갔는지

main CD는 같은 커밋의 Application checks 성공을 기다린 뒤 빌드한다. 배포는 빌드 결과의 정확한 digest로 한다
(`infra/deploy_private.py`). 배포가 끝나면 서비스마다 다음 셋이 서로 일치하는지 확인한다.

1. 지금 트래픽을 받는 리비전의 이미지 digest와 `commit-sha` 라벨

   ```bash
   for s in web platform cs tft; do
     gcloud run services describe team4-$s $G \
       --format="value(metadata.name,status.traffic[0].revisionName,status.traffic[0].percent,spec.template.metadata.labels.commit-sha)"
     rev=$(gcloud run services describe team4-$s $G --format="value(status.traffic[0].revisionName)")
     gcloud run revisions describe "$rev" $G --format="value(spec.containers[0].image,status.imageDigest)"
   done
   ```

2. 그 커밋 빌드의 결과 digest. Cloud Build의 `results.images`에 `…/team4/<서비스>:<커밋>`의 digest가 나온다.

   ```bash
   gcloud builds list $G --filter="tags=team4-cd" --limit=5 --format="table(id,status,substitutions.COMMIT_SHA,createTime)"
   gcloud builds describe BUILD_ID $G --format="json(status,substitutions.COMMIT_SHA,results.images)"
   ```

3. GitHub Actions에서 그 커밋의 Application checks가 성공했는지

트래픽 100%가 한 리비전에 있고, 그 이미지가 `@sha256:`으로 고정돼 있어야 한다. 그 digest가 빌드 결과와 같고 커밋이
CI 성공 커밋과 같으면 통과다. 하나라도 다르면 **4. 되돌리기**로 간다.

검증한 리비전에만 트래픽을 넘기고 싶으면 수동 배포에서 다음 순서로 한다(선택). 리비전 태그 URL도 서비스와 같은 IAM을
따른다.

```bash
python3 infra/deploy_private.py BUILD_ID --service cs          # 명령만 출력한다
# 출력된 gcloud run deploy 명령 끝에 --no-traffic --tag=candidate를 붙여 실행한다 [변경]
curl -fsS -H "Authorization: Bearer $(gcloud auth print-identity-token --account=$A)" \
  https://candidate---team4-cs-gew6ci4pgq-du.a.run.app/api/v1/cs/ready
gcloud run services update-traffic team4-cs $G --to-tags=candidate=100   # [변경]
```

## 3. 시연 준비 확인

서비스 배포 명령은 `--min=1 --max=2`다. 콜드 스타트 없이 시연할 수 있도록 인스턴스가 최소 1개 떠 있다.
`--max=2`는 3배 부하 로컬 시험(`docs/load-test.md`)에서 플랫폼 인스턴스가 2개일 때 통과한 값이다. 그대로 적용됐는지
확인한다.

```bash
for s in web platform cs tft; do
  gcloud run services describe team4-$s $G --format="value(metadata.name,spec.template.metadata.annotations)" \
    | tr ';' '\n' | grep -E "team4-|minScale|maxScale"
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

배포된 리비전의 값을 확인한다.

```bash
for s in platform cs tft; do
  gcloud run services describe team4-$s $G \
    --format="value(metadata.name,spec.template.spec.containers[0].env)" | tr ';' '\n' \
    | grep -E "team4-|ENVIRONMENT|DEV_AUTH_ENABLED"
done
gcloud run services describe team4-web $G --format="value(spec.template.spec.containers[0].env)" \
  | tr ';' '\n' | grep NODE_ENV
```

백엔드 셋은 `ENVIRONMENT=production`, `DEV_AUTH_ENABLED=false`여야 하고, 웹은 `NODE_ENV=production`이어야 한다.

## 5. 이전 리비전으로 되돌리기

배포 뒤 오류율이 오르거나 시연 기능이 깨지면, 원인을 찾기 전에 먼저 트래픽을 직전 리비전으로 돌린다. 문제가 된
서비스만 돌리고, 다른 서비스의 리비전은 건드리지 않는다.

```bash
s=cs
gcloud run revisions list --service=team4-$s $G --limit=5 \
  --format="table(metadata.name,metadata.creationTimestamp,metadata.labels.commit-sha,status.conditions[0].status)"
gcloud run services update-traffic team4-$s $G --to-revisions=<직전 리비전>=100   # [변경]
gcloud run services describe team4-$s $G --format="value(status.traffic)"
```

- 되돌린 뒤 확인할 것:
  - 공용 웹의 `/health`와 `/api/v1/platform/ready`
  - C/S·TFT의 ready(IAM 인증 요청)
  - 로그인
  - 시연 대표 기능 1건
- DB는 되돌리지 않는다. 1번 호환 확인을 통과한 배포라면 직전 리비전이 새 스키마에서 그대로 돈다. 1번을 건너뛴 배포였다면 되돌린 뒤 오류 로그부터 확인한다(`docs/operations-basics.md`).
- worker Job은 서비스 트래픽과 따로 간다. 되돌려야 하면 `infra/deploy_workers.py`로 직전 빌드 digest를 다시 배포한다.
- 다음 main 배포는 CD가 다시 최신 커밋으로 덮는다. 원인을 고친 커밋을 main에 올리기 전까지 수동 배포를 하지 않는다.

## 6. DB 백업 복구 훈련

목표는 "자동 백업으로 실제로 복구되는지"를 운영 DB(`team4-sql`)에 쓰지 않고 확인하는 것이다. 새 인스턴스에 복원하고,
확인한 뒤 바로 지운다.

> **비용**: 복원용 Cloud SQL 인스턴스는 떠 있는 동안 요금이 나온다. 실행 전에 원본과 같은 등급으로 예상 비용을
> 확인하고 승인을 받는다. 훈련이 끝나면 같은 날 삭제한다.

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
   gcloud sql instances create team4-sql-restore-drill --project=$P --account=$A --region=$R \
     --database-version=<1의 값> --tier=<1의 값> --edition=<1의 값> --storage-size=<원본 이상> \
     --no-backup --no-deletion-protection
   ```

3. 가장 최근 성공한 자동 백업을 새 인스턴스에 복원 **[변경]**. 대상이 `team4-sql`이 아닌지 한 번 더 본다.

   ```bash
   gcloud sql backups restore <BACKUP_ID> --backup-instance=team4-sql \
     --restore-instance=team4-sql-restore-drill --project=$P --account=$A
   ```

4. 복원된 DB 확인. `gcloud sql connect`가 잠시 내 IP를 허용한다. 비밀번호는 화면에 남기지 않는다.

   ```bash
   gcloud sql users set-password postgres --instance=team4-sql-restore-drill --project=$P --account=$A --prompt-for-password   # [변경]
   gcloud sql connect team4-sql-restore-drill --user=postgres --database=team4 --project=$P --account=$A
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
   gcloud sql instances delete team4-sql-restore-drill --project=$P --account=$A
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
- 이미지: 서비스별 리비전 · digest · commit-sha = 빌드 digest · CI 성공 커밋 (일치/불일치)
- 시연: minScale 1 / maxScale 2 (서비스별)
- 개발 모드: 백엔드 ENVIRONMENT=production · DEV_AUTH_ENABLED=false, 웹 NODE_ENV=production
- 되돌리기: 실행 안 함 / 실행(서비스, 이전 리비전, 확인 결과)
- 백업 복구: 백업 ID, 복원 소요 시간, 확인 결과, 복원 인스턴스 삭제 확인
```
