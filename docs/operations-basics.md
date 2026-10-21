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

2026-10-22 점검에서 자동 백업 활성화, 서울 백업 위치와 7개 보존 및 삭제 보호 설정을 확인했다. 첫 자동 실행 전에는 목록이 비어 있었다. 사용자가 수동 백업을 실행한 후 다음 두 건의 실제 성공을 조회했다.

| ID | 상태 | 유형 | 완료 시각 UTC |
| --- | --- | --- | --- |
| 1792600511679 | SUCCESSFUL | ON_DEMAND | 2026-10-21 16:36:32 |
| 1792600953011 | SUCCESSFUL | ON_DEMAND | 2026-10-21 16:43:54 |

추가 수동 백업은 필요한 시점에만 실행한다. 백업 성공은 복구 실행 검증과 다르며 복구 훈련은 이번 셋업 범위 이후에 진행한다.
