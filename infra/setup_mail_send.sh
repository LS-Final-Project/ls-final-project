#!/usr/bin/env bash
# KAN-74: 승인 메일 발송 큐·C/S 호출 권한·정리 작업. 인프라 담당자가 직접 실행한다.
# infra/setup_mail_sync.sh(호출 계정 mail-sync-invoker)를 먼저 적용한다. .env나 비밀값을 읽지 않는다.
# Scheduler 작업은 일시 중지로 만들고, 앱의 MAIL_SEND_ENABLED를 켠 뒤 담당자가 resume한다.
set +x
set -euo pipefail

LS_SEND_PROJECT=proj-skkuls0106-183255377938
LS_SEND_ACCOUNT=skkuls0106@skkukdp.re.kr
LS_SEND_REGION=asia-northeast3
LS_SEND_SERVICE=team4-platform
LS_SEND_QUEUE=mail-send
LS_SEND_JOB=mail-send-sweep
LS_SEND_RUNTIME="platform-runtime@${LS_SEND_PROJECT}.iam.gserviceaccount.com"
LS_SEND_CS="cs-runtime@${LS_SEND_PROJECT}.iam.gserviceaccount.com"
LS_SEND_INVOKER="mail-sync-invoker@${LS_SEND_PROJECT}.iam.gserviceaccount.com"
LS_SEND_FLAGS=("--project=$LS_SEND_PROJECT" "--account=$LS_SEND_ACCOUNT" --quiet)

# 목록 조회가 성공해야 "없음"을 판단한다. 필요한 계정이 하나라도 없으면 아무것도 바꾸지 않는다.
LS_SEND_ACCOUNTS=$(gcloud iam service-accounts list "${LS_SEND_FLAGS[@]}" --format='value(email)')
for account in "$LS_SEND_RUNTIME" "$LS_SEND_CS" "$LS_SEND_INVOKER"; do
  if ! printf '%s\n' "$LS_SEND_ACCOUNTS" | grep -Fxq "$account"; then
    echo "$account 계정이 없다. setup_mail_sync.sh·서비스 준비를 먼저 적용한다." >&2
    exit 1
  fi
done

LS_SEND_URL=$(gcloud run services describe "$LS_SEND_SERVICE" --region="$LS_SEND_REGION" \
  "${LS_SEND_FLAGS[@]}" --format='value(status.url)')
if [[ ! "$LS_SEND_URL" =~ ^https://[a-z0-9.-]+$ ]]; then
  echo "$LS_SEND_SERVICE 서비스 주소를 확인하지 못했다." >&2
  exit 1
fi

# C/S 실행 계정이 플랫폼 내부 발송 입구를 부를 수 있게 한다(앱이 ID 토큰을 다시 검증한다).
gcloud run services add-iam-policy-binding "$LS_SEND_SERVICE" --region="$LS_SEND_REGION" \
  --member="serviceAccount:$LS_SEND_CS" --role=roles/run.invoker \
  "${LS_SEND_FLAGS[@]}" --format=none

LS_SEND_QUEUES=$(gcloud tasks queues list --location="$LS_SEND_REGION" "${LS_SEND_FLAGS[@]}" \
  --format='value(name.basename())')
if ! printf '%s\n' "$LS_SEND_QUEUES" | grep -Fxq "$LS_SEND_QUEUE"; then
  # 미룬 전송(429 등)과 사본 확인을 다시 시도한다. 결과 불명은 앱이 2xx로 끝내 재시도되지 않는다.
  gcloud tasks queues create "$LS_SEND_QUEUE" --location="$LS_SEND_REGION" \
    --max-attempts=5 --min-backoff=30s --max-backoff=600s \
    --max-concurrent-dispatches=2 --max-dispatches-per-second=1 "${LS_SEND_FLAGS[@]}"
fi
gcloud tasks queues add-iam-policy-binding "$LS_SEND_QUEUE" --location="$LS_SEND_REGION" \
  --member="serviceAccount:$LS_SEND_RUNTIME" --role=roles/cloudtasks.enqueuer \
  "${LS_SEND_FLAGS[@]}" --format=none
gcloud iam service-accounts add-iam-policy-binding "$LS_SEND_INVOKER" \
  --member="serviceAccount:$LS_SEND_RUNTIME" --role=roles/iam.serviceAccountUser \
  "${LS_SEND_FLAGS[@]}" --format=none

LS_SEND_JOBS=$(gcloud scheduler jobs list --location="$LS_SEND_REGION" "${LS_SEND_FLAGS[@]}" \
  --format='value(name.basename())')
if ! printf '%s\n' "$LS_SEND_JOBS" | grep -Fxq "$LS_SEND_JOB"; then
  gcloud scheduler jobs create http "$LS_SEND_JOB" --location="$LS_SEND_REGION" \
    --schedule='*/5 * * * *' --time-zone=Asia/Seoul \
    --uri="${LS_SEND_URL}/internal/v1/platform/mail/send-sweep" --http-method=POST \
    --oidc-service-account-email="$LS_SEND_INVOKER" --oidc-token-audience="$LS_SEND_URL" \
    --attempt-deadline=120s --max-retry-attempts=3 --min-backoff=30s --max-backoff=120s \
    "${LS_SEND_FLAGS[@]}"
  gcloud scheduler jobs pause "$LS_SEND_JOB" --location="$LS_SEND_REGION" "${LS_SEND_FLAGS[@]}"
fi

printf 'READY: queue=projects/%s/locations/%s/queues/%s; job=%s (paused)\n' \
  "$LS_SEND_PROJECT" "$LS_SEND_REGION" "$LS_SEND_QUEUE" "$LS_SEND_JOB"
printf 'Cloud Run 환경 변수: MAIL_SEND_QUEUE=projects/%s/locations/%s/queues/%s MAIL_SEND_SERVICE_URL=%s MAIL_SEND_INVOKER_EMAIL=%s MAIL_SEND_CS_CALLER_EMAIL=%s\n' \
  "$LS_SEND_PROJECT" "$LS_SEND_REGION" "$LS_SEND_QUEUE" "$LS_SEND_URL" "$LS_SEND_INVOKER" "$LS_SEND_CS"
