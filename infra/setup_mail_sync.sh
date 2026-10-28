#!/usr/bin/env bash
# KAN-72: 메일 수집 워커의 큐·호출 계정·Scheduler 작업. 인프라 담당자가 직접 실행한다.
# .env나 비밀값을 읽지 않는다. Scheduler 작업은 일시 중지 상태로 만들고, 앱의
# MAIL_SYNC_ENABLED를 켠 뒤 담당자가 resume한다(그 전에는 내부 경로가 404다).
set +x
set -euo pipefail

LS_SYNC_PROJECT=proj-skkuls0106-183255377938
LS_SYNC_ACCOUNT=skkuls0106@skkukdp.re.kr
LS_SYNC_REGION=asia-northeast3
LS_SYNC_SERVICE=team4-platform
LS_SYNC_QUEUE=mail-sync
LS_SYNC_JOB=mail-sync-dispatch
LS_SYNC_RUNTIME="platform-runtime@${LS_SYNC_PROJECT}.iam.gserviceaccount.com"
LS_SYNC_INVOKER_NAME=mail-sync-invoker
LS_SYNC_INVOKER="${LS_SYNC_INVOKER_NAME}@${LS_SYNC_PROJECT}.iam.gserviceaccount.com"
LS_SYNC_FLAGS=("--project=$LS_SYNC_PROJECT" "--account=$LS_SYNC_ACCOUNT" --quiet)

gcloud services enable cloudtasks.googleapis.com cloudscheduler.googleapis.com "${LS_SYNC_FLAGS[@]}"

# 목록 조회가 성공해야 "없음"을 생성 요청으로 해석한다.
LS_SYNC_ACCOUNTS=$(gcloud iam service-accounts list "${LS_SYNC_FLAGS[@]}" --format='value(email)')
if ! printf '%s\n' "$LS_SYNC_ACCOUNTS" | grep -Fxq "$LS_SYNC_RUNTIME"; then
  echo "platform-runtime 서비스 계정이 없다. infra/prepare_microsoft_auth.py를 먼저 적용한다." >&2
  exit 1
fi
if ! printf '%s\n' "$LS_SYNC_ACCOUNTS" | grep -Fxq "$LS_SYNC_INVOKER"; then
  gcloud iam service-accounts create "$LS_SYNC_INVOKER_NAME" \
    --display-name="Mail sync Scheduler/Tasks invoker" "${LS_SYNC_FLAGS[@]}"
fi

LS_SYNC_URL=$(gcloud run services describe "$LS_SYNC_SERVICE" --region="$LS_SYNC_REGION" \
  "${LS_SYNC_FLAGS[@]}" --format='value(status.url)')
if [[ ! "$LS_SYNC_URL" =~ ^https://[a-z0-9.-]+$ ]]; then
  echo "$LS_SYNC_SERVICE 서비스 주소를 확인하지 못했다." >&2
  exit 1
fi

# 호출 계정은 플랫폼 서비스 호출만 할 수 있다.
gcloud run services add-iam-policy-binding "$LS_SYNC_SERVICE" --region="$LS_SYNC_REGION" \
  --member="serviceAccount:$LS_SYNC_INVOKER" --role=roles/run.invoker \
  "${LS_SYNC_FLAGS[@]}" --format=none

LS_SYNC_QUEUES=$(gcloud tasks queues list --location="$LS_SYNC_REGION" "${LS_SYNC_FLAGS[@]}" \
  --format='value(name.basename())')
if ! printf '%s\n' "$LS_SYNC_QUEUES" | grep -Fxq "$LS_SYNC_QUEUE"; then
  gcloud tasks queues create "$LS_SYNC_QUEUE" --location="$LS_SYNC_REGION" \
    --max-attempts=3 --min-backoff=30s --max-backoff=300s \
    --max-concurrent-dispatches=4 --max-dispatches-per-second=1 "${LS_SYNC_FLAGS[@]}"
fi

# 플랫폼 실행 계정은 이 큐에 작업을 넣고, 작업 ID 토큰에 호출 계정을 지정할 수만 있다.
gcloud tasks queues add-iam-policy-binding "$LS_SYNC_QUEUE" --location="$LS_SYNC_REGION" \
  --member="serviceAccount:$LS_SYNC_RUNTIME" --role=roles/cloudtasks.enqueuer \
  "${LS_SYNC_FLAGS[@]}" --format=none
gcloud iam service-accounts add-iam-policy-binding "$LS_SYNC_INVOKER" \
  --member="serviceAccount:$LS_SYNC_RUNTIME" --role=roles/iam.serviceAccountUser \
  "${LS_SYNC_FLAGS[@]}" --format=none

LS_SYNC_JOBS=$(gcloud scheduler jobs list --location="$LS_SYNC_REGION" "${LS_SYNC_FLAGS[@]}" \
  --format='value(name.basename())')
if ! printf '%s\n' "$LS_SYNC_JOBS" | grep -Fxq "$LS_SYNC_JOB"; then
  gcloud scheduler jobs create http "$LS_SYNC_JOB" --location="$LS_SYNC_REGION" \
    --schedule='*/5 * * * *' --time-zone=Asia/Seoul \
    --uri="${LS_SYNC_URL}/internal/v1/platform/mail/dispatch" --http-method=POST \
    --oidc-service-account-email="$LS_SYNC_INVOKER" --oidc-token-audience="$LS_SYNC_URL" \
    --attempt-deadline=120s --max-retry-attempts=3 --min-backoff=30s --max-backoff=120s \
    "${LS_SYNC_FLAGS[@]}"
  gcloud scheduler jobs pause "$LS_SYNC_JOB" --location="$LS_SYNC_REGION" "${LS_SYNC_FLAGS[@]}"
fi

printf 'READY: queue=projects/%s/locations/%s/queues/%s; invoker=%s; job=%s (paused)\n' \
  "$LS_SYNC_PROJECT" "$LS_SYNC_REGION" "$LS_SYNC_QUEUE" "$LS_SYNC_INVOKER" "$LS_SYNC_JOB"
printf 'Cloud Run 환경 변수: MAIL_SYNC_QUEUE=projects/%s/locations/%s/queues/%s MAIL_SYNC_SERVICE_URL=%s MAIL_SYNC_INVOKER_EMAIL=%s\n' \
  "$LS_SYNC_PROJECT" "$LS_SYNC_REGION" "$LS_SYNC_QUEUE" "$LS_SYNC_URL" "$LS_SYNC_INVOKER"
