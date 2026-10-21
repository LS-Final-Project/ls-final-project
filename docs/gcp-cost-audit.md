# GCP 리소스 및 비용 점검

2026-10-21, 교육장 프로젝트 `proj-skkuls0106-183255377938`의 비용 점검 및 후속 정리 기록이다. 최초 점검은 읽기 전용으로 진행했고, 이후 사용자 승인으로 기존 RFI 리소스를 삭제했다. 실제 누적 청구액은 확인하지 못했다. 아래 초기 점검의 비용 표시는 리소스와 과금 조건을 구분한 결과이며, 무료 한도 및 교육장 크레딧 적용 후 실청구액을 뜻하지 않는다.

최신 상태는 11:55 UTC 삭제 후 재조회 기준이다. 기존 RFI 실행 리소스와 DB는 삭제했으나 **GCS는 복구 보존 버킷 1개가 남아 있다**. `gemini-api-key` Secret은 보존했다. 자세한 실행 범위와 검증은 아래 '삭제 실행 및 남은 복구 보존' 절을 따른다.

## 삭제 전 실행 및 저장 리소스

| 리소스 | 확인 결과 | 비용 판단 |
| --- | --- | --- |
| Compute Engine VM | 없음 | 조회된 VM 실행 비용 없음 |
| Compute 디스크, 스냅샷, 예약 IP, 포워딩 규칙, 라우터 | 없음 | 조회된 항목 없음. Cloud SQL의 IP 및 디스크와는 별개 |
| Cloud SQL `rfi-db` | 미국 `us-central1`, STOPPED, activationPolicy NEVER, PostgreSQL 17, db-f1-micro | CPU 및 메모리 인스턴스 비용은 중지. 저장소 및 IP 보관 비용 조건은 유지 |
| SQL 저장소 | 10GB HDD, IPv4 활성 설정 | 중지 상태에서도 저장소와 IP 관련 비용 확인 필요 |
| SQL 백업 | 현재 인스턴스의 backups list 결과 없음 | 조회된 백업 없음 |
| Cloud Run `rfi-web` | 미국 리전, 배포된 서비스 1개, 1 vCPU 및 4GiB, CPU throttling false, 명시적 최소 인스턴스 설정 없음 | 인스턴스 기반 과금 설정. 최근 사용 지표는 아래 참고 |
| Cloud Run Job `rfi-collect` | 마지막 실행 2026-10-01 완료. 조회한 실행 7개 모두 종료 | 현재 조회된 실행 중 작업 없음 |
| Cloud Scheduler `rfi-collect-daily` | 미국 리전, PAUSED | 자동 호출 중지. 일시중지한 작업도 작업 수 과금 대상으로 계산 |
| Artifact Registry `rfi` | 미국 리전, Docker 저장소, 791,973,991 bytes, 약 0.74GiB | 저장 비용 대상. 무료 한도는 결제 계정 단위 |
| GCS `proj-skkuls0106-183255377938-rfi-data` | 미국 리전, 모든 버전 포함 4,864개 객체, 1,975,292,044 bytes, 약 1.84GiB, 모두 STANDARD | 저장 비용 대상. 무료 한도 및 크레딧 적용 여부 미확인 |
| GCS soft-deleted 객체 | 전체 페이지 조회 결과 없음 | 현재 soft-delete 보관 객체 비용 항목 없음 |
| Secret Manager | 비밀 2개, 각각 ENABLED 버전 1개 | 활성 버전 2개와 사용 시 접근량이 비용 산정 대상. 비밀값은 읽지 않음 |
| Cloud Build | WORKING 및 QUEUED 작업 없음 | 현재 조회된 활성 빌드 없음 |
| Spanner | 인스턴스 없음 | 조회된 항목 없음 |
| Pub/Sub | 토픽 없음 | 조회된 항목 없음 |
| Vertex AI 엔드포인트 및 Agent Engine | 미국 us-central1 및 서울 asia-northeast3 조회 결과 없음 | 조회한 두 리전에 해당 배포 없음. 다른 리전까지 전수 확인한 것은 아님 |

## Cloud Run 최근 지표

2026-10-21 08:43 UTC 조회에서 다음 지표의 시계열이 반환되지 않았다.

- container/instance_count: 직전 1시간.
- request_count: 직전 24시간.
- container/billable_instance_time: 직전 24시간.

최근 사용을 나타내는 측정값을 찾지 못한 결과다. 데이터 지연 및 지표 공백이 가능하므로 이를 실시간 인스턴스 수 0 또는 청구액 0의 증거로 단정하지 않는다. 서비스에 요청을 보내 기동시키는 검증은 하지 않았다.

## 실제 청구액 확인 한계

교육장 계정으로 결제 계정 상세 조회 시 PERMISSION_DENIED가 반환되었다. 프로젝트 내 BigQuery 데이터셋도 조회 결과 없어 이 프로젝트의 기존 Billing export로 비용을 집계할 수 없었다. 다른 프로젝트나 교육장 공용 결제 데이터셋의 존재 여부는 확인하지 않았다.

Gemini 등 API 호출, 로그, 전송량과 과거 사용료 및 할인 적용 후 실청구액은 이번 리소스 목록으로 확정할 수 없다. 실제 금액은 Billing 보고서에서 이 프로젝트와 기간을 필터링해 확인해야 한다.

## 정리 검토 대상

저장 및 보관 비용을 줄이려면 기존 RFI 서비스와 데이터의 보존 필요를 먼저 확인한 뒤 중지 DB, 이미지 저장소, 버킷과 일시중지된 Scheduler를 정리 대상으로 검토한다. 원본 데이터의 필요 여부를 확인하지 않은 상태에서 삭제하지 않는다. 이번 작업은 조회만 수행했다.

## 후속 실행 차단

사용자의 비용 중단 요청에 따라 2026-10-21 08:50 UTC에 `rfi-web`의 scalingMode를 manual, manualInstanceCount를 0으로 변경하고 재조회로 적용을 확인했다. 이 설정으로 기존 웹 서비스의 인스턴스 실행을 비활성화했다. DB는 이미 STOPPED이고 수집 작업과 자동 실행 일정도 실행 중이지 않았다.

이 단계에서는 DB 및 버킷의 원본 데이터, 이미지와 Secret의 영구 삭제 범위를 사용자에게 확인 중이었고 저장 리소스를 삭제하지 않았다. 이후 승인과 삭제 결과는 다음 절을 따른다.

## 삭제 실행 및 남은 복구 보존

사용자가 GCP 실조회 결과를 확인한 뒤 삭제를 요청했다. 새 환경에도 사용할 수 있는 `gemini-api-key` Secret은 보존하고 기존 RFI 리소스와 전용 DB Secret을 정리하는 범위로 진행했다. DB 최종 백업을 만들지 않는 최초 삭제 명령은 자동 승인 리뷰에서 복구 불가능한 원본 삭제의 명시적 승인이 부족하다는 이유로 거절됐다. 이후 DB 최종 백업을 생략하고 버킷의 7일 복구 보존을 해제한 뒤 모든 파일을 삭제하는 질문에 사용자가 **백업 없이 완전 삭제**를 명시적으로 선택했다. 승인 후 해당 범위로 실행했으며 승인 대기는 없다. 다만 GCS의 복구 보존 만료는 남아 있다.

| 대상 | 실행 및 2026-10-21 11:52 UTC 검증 |
| --- | --- |
| `rfi-collect-daily` Scheduler | 삭제 완료. us-central1 목록 비어 있음 |
| `rfi-web` 서비스 | 삭제 완료. 전체 Cloud Run 서비스 목록 비어 있음 |
| `rfi-collect` Job | 삭제 완료. us-central1 Job 목록 비어 있음 |
| `us-central1/rfi` 이미지 저장소 | 삭제 완료. 전 리전 저장소 목록 비어 있음 |
| `rfi-db` | `--no-enable-final-backup`으로 삭제. 작업 `e8581e6a-b2bc-4462-a235-0e8900000032`는 11:51:57 UTC DONE, 오류 없음. 인스턴스 목록과 프로젝트 전체 백업 목록 비어 있음 |
| `proj-skkuls0106-183255377938-rfi-data` | soft delete를 해제하고 `retentionDurationSeconds=0`을 조회한 뒤 recursive 삭제. 전체 객체 버전 4,864개와 버킷의 삭제 명령은 종료 코드 0 및 ERROR 없음. 일반 버킷 목록은 비어 있으나 복구 보존 버킷 1개가 남아 있어 영구 삭제 완료는 아님 |
| `cloud-database-url` Secret | 삭제 완료. Secret 목록에 gemini-api-key만 남음 |

버킷 삭제 로그는 `/tmp/ls-rfi-bucket-delete.log`에 있다. Removing 기록은 객체 및 버킷을 포함해 4,865개다. 이 기록은 명령 성공을 뜻하며 영구 삭제를 보장하지 않는다. Secret 값이나 계정 토큰을 출력하지 않았다. Storage API 조회 인증용 토큰은 프로세스 메모리에서만 사용했다. 새 환경용 실행 계정 3개는 모두 disabled=false이며 교육장 프로젝트, 결제 연결과 활성 API, 로컬 data는 변경하지 않았다. 새 서울 환경은 아직 만들지 않았다.

### GCS 복구 보존 검증

gcloud의 일반 버킷 목록은 비어 있었고 soft-deleted 목록 명령은 HTTP 404를 반환했다. Storage JSON API의 `buckets.list?project=proj-skkuls0106-183255377938&softDeleted=true`를 직접 호출해 전체 1페이지를 확인한 결과 다음 버킷 1개가 남았다.

| 필드 | 값 |
| --- | --- |
| name | proj-skkuls0106-183255377938-rfi-data |
| generation | 1790669428479733565 |
| softDeleteTime | 2026-10-21T11:51:36.340Z |
| hardDeleteTime | 2026-10-28T11:51:25.198Z |
| 영구 삭제 예정 한국 시각 | 2026-10-28 20:51:25 KST |

감사 로그에서 정책 변경은 11:50:25.151 UTC, 버킷 삭제는 11:51:35.924 UTC였다. 정책을 0초로 조회한 것만으로 객체 삭제의 전파 완료를 보장하지 못했다. Google은 soft delete 해제 후 최소 30초 대기를 권장하며 이미 복구 보존 상태인 리소스는 이전 보존 기간이 끝날 때까지 남는다고 설명한다. 객체별 최초 삭제 시각과 잔여 수 및 용량은 이번 점검에서 확인하지 못했으므로 원인을 단정하거나 전체 파일이 영구 삭제됐다고 보고하지 않는다. [정책 해제와 전파 지연](https://docs.cloud.google.com/storage/docs/disable-soft-delete)

Google 정책상 soft-deleted 버킷은 보존 기간 중 강제로 영구 삭제할 수 없다. 내용 목록은 버킷을 복원한 뒤에만 조회할 수 있어 이번 확인을 위해 복원하지 않았다. 위 만료 시각 이후 같은 API로 부재를 확인할 일이 남아 있으며 별도 예약 작업은 만들지 않았다. [복구 보존 버킷의 제약](https://docs.cloud.google.com/storage/docs/use-soft-deleted-buckets)

이번 정리는 기존 RFI 실행 리소스와 DB 등의 삭제를 뜻하며 GCS 복구 보존 기간의 저장 비용 조건은 남을 수 있다. Gemini Secret 보존, 과거 사용량과 다른 서비스의 실제 청구액까지 0원으로 만들거나 확인한 결과가 아니다. API 및 데이터 계약과 개발 일정 변경은 없으며 다음 작업은 서울 신규 환경 구성이다.

## 과금 기준 출처

- [Cloud SQL 중지 시 저장소 및 IP 과금](https://docs.cloud.google.com/sql/docs/postgres/start-stop-restart-instance)
- [Cloud Run 과금 방식](https://docs.cloud.google.com/run/docs/configuring/billing-settings)
- [Cloud Storage 과금](https://cloud.google.com/storage/pricing)
- [Artifact Registry 과금](https://cloud.google.com/artifact-registry/pricing)
- [Cloud Scheduler 과금](https://cloud.google.com/scheduler/pricing)
- [Secret Manager 과금](https://cloud.google.com/secret-manager/pricing)
- [Billing 보고서와 프로젝트별 비용 조회](https://docs.cloud.google.com/billing/docs/how-to/reports)
