# 기본 운영 확인

대상은 교육장 프로젝트 proj-skkuls0106-183255377938, 계정 skkuls0106@skkukdp.re.kr, 서울 asia-northeast3다. GCP 변경은 사용자 터미널에서 실행한다. 이 문서는 오류 확인과 백업 성공 점검 범위이며 복구 훈련 또는 상세 모니터링 구축을 뜻하지 않는다.

## 서비스와 오류 확인

```bash
gcloud run services list \
  --project=proj-skkuls0106-183255377938 \
  --account=skkuls0106@skkukdp.re.kr \
  --region=asia-northeast3 \
  --format='table(metadata.name,status.conditions[0].status,status.url)'
```

오류를 확인할 때는 서비스 이름과 시각, HTTP 상태 및 요청 ID부터 공유한다. 토큰이나 인증 콜백 URL, 원문 메일을 포함한 전체 로그를 그대로 공유하지 않는다.

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND severity>=ERROR' \
  --project=proj-skkuls0106-183255377938 \
  --account=skkuls0106@skkukdp.re.kr \
  --freshness=1d --limit=20 \
  --format='table(timestamp,resource.labels.service_name,httpRequest.status,jsonPayload.request_id)'
```

프론트 /health는 웹 프로세스 확인이다. 공용 웹의 /api/v1/platform/ready는 실제 플랫폼을 호출해 DB와 확장을 확인한다. C/S와 TFT는 기존 IAM 인증 요청으로 각각 /api/v1/cs/ready, /api/v1/tft/ready를 확인한다. health 성공을 메일이나 모델 연동 성공으로 해석하지 않는다.

## 백업 확인

```bash
gcloud sql backups list --instance=team4-sql \
  --project=proj-skkuls0106-183255377938 \
  --account=skkuls0106@skkukdp.re.kr \
  --limit=5 --format='table(id,status,type,startTime,endTime)'
```

자동 백업은 서울에 7개를 보존하며 삭제 보호가 설정돼 있다. 수동 백업 성공도 확인했다. 위 명령으로 현재 성공 기록을 확인하고 필요할 때만 추가 백업한다. 복구 훈련 순서는 [배포 확인 런북](release-runbook.md#6-db-백업-복구-훈련)에 있다.

## 운영 담당 후속 확인

- 2026-10-28 20:51:25 KST 이후 `proj-skkuls0106-183255377938-rfi-data` 버킷의 부재를 읽기 전용으로 확인한다. 복구 보존 generation은 `1790669428479733565`이며 예약 작업은 없다.
- 누적 청구액은 결제 계정 권한이 있는 담당자가 별도로 확인한다. 리소스 상태만으로 프로젝트 비용 0원을 단정하지 않는다.
