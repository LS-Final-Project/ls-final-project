#!/usr/bin/env bash
# KAN-201 O2. WRITE/STATIC CHECK ONLY by coding agents. Operator runs --apply.
# Prepare accounts first with --prepare-identities --apply, then O1, then full O2.
set +x
set -euo pipefail
LS_PUSH_APPLY=false
LS_PUSH_IDENTITIES=false
for LS_PUSH_ARG in "$@"; do
  case "$LS_PUSH_ARG" in
    --apply) LS_PUSH_APPLY=true ;;
    --prepare-identities) LS_PUSH_IDENTITIES=true ;;
    *) echo 'Use [--prepare-identities] [--apply]. Default is preview without cloud calls.' >&2; exit 2 ;;
  esac
done
LS_PUSH_PROJECT=proj-skkuls0106-183255377938
LS_PUSH_ACCOUNT=skkuls0106@skkukdp.re.kr
LS_PUSH_REGION=asia-northeast3
LS_PUSH_WORKER="notification-push-runtime@${LS_PUSH_PROJECT}.iam.gserviceaccount.com"
LS_PUSH_INVOKER="notification-push-invoker@${LS_PUSH_PROJECT}.iam.gserviceaccount.com"
LS_PUSH_PLATFORM="platform-runtime@${LS_PUSH_PROJECT}.iam.gserviceaccount.com"
LS_PUSH_FLAGS=("--project=$LS_PUSH_PROJECT" "--account=$LS_PUSH_ACCOUNT" --quiet)
function ls_push_run() {
  if "$LS_PUSH_APPLY"; then
    gcloud "$@" "${LS_PUSH_FLAGS[@]}"
  else
    printf 'PLAN gcloud '; printf '%q ' "$@" "${LS_PUSH_FLAGS[@]}"; printf '\n'
  fi
}
if "$LS_PUSH_IDENTITIES"; then
  # A failed list is never interpreted as an absent account.
  LS_PUSH_EXISTING=''
  if "$LS_PUSH_APPLY"; then
    LS_PUSH_EXISTING=$(gcloud iam service-accounts list "${LS_PUSH_FLAGS[@]}" --format='value(email)')
  fi
  for LS_PUSH_NAME in notification-push-runtime notification-push-invoker; do
    if ! printf '%s\n' "$LS_PUSH_EXISTING" | grep -Fxq "${LS_PUSH_NAME}@${LS_PUSH_PROJECT}.iam.gserviceaccount.com"; then
      ls_push_run iam service-accounts create "$LS_PUSH_NAME"
    fi
  done
  exit 0
fi
# Full setup needs pinned image, matching public key and contact; no private key in env/argv.
: "${NOTIFICATION_PUSH_IMAGE:?Set platform image pinned with @sha256 digest}"
: "${NOTIFICATION_PUSH_PUBLIC_KEY:?Set O1 public output only}"
: "${NOTIFICATION_PUSH_SUBJECT:?Set mailto operational contact}"
: "${NOTIFICATION_PUSH_PROJECT_NUMBER:?Set numeric project number}"
: "${NOTIFICATION_PUSH_SERVICE_URL:?Set private platform HTTPS origin}"
[[ "$NOTIFICATION_PUSH_IMAGE" =~ ^asia-northeast3-docker.pkg.dev/proj-skkuls0106-183255377938/team4/platform@sha256:[0-9a-f]{64}$ ]] || exit 2
[[ "$NOTIFICATION_PUSH_PUBLIC_KEY" =~ ^[A-Za-z0-9_-]{87}$ ]] || exit 2
[[ "$NOTIFICATION_PUSH_SUBJECT" =~ ^mailto:[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$ ]] || exit 2
[[ "$NOTIFICATION_PUSH_PROJECT_NUMBER" =~ ^[1-9][0-9]{5,19}$ ]] || exit 2
[[ "$NOTIFICATION_PUSH_SERVICE_URL" =~ ^https://[a-z0-9-]+\.[a-z0-9.-]+\.run\.app$ ]] || exit 2
# Inspect first; for an existing Scheduler, pause is the first mutating operation.
# A failed list/pause stops setup. Requests/executions started before pause need
# separate operator inspection; this script cannot undo already accepted work.
LS_PUSH_SCHEDULER_ACTION=create
if "$LS_PUSH_APPLY"; then
  LS_PUSH_SCHEDULERS=$(gcloud scheduler jobs list --location="$LS_PUSH_REGION" "${LS_PUSH_FLAGS[@]}" --format='value(name.basename())')
  if printf '%s\n' "$LS_PUSH_SCHEDULERS" | grep -Fxq notification-push-dispatch; then
    LS_PUSH_SCHEDULER_ACTION=update
    ls_push_run scheduler jobs pause notification-push-dispatch --location="$LS_PUSH_REGION"
  fi
fi
# Preparation never enables dispatch. Do this before changing the worker or IAM.
ls_push_run run services update team4-platform --region="$LS_PUSH_REGION" \
  --update-env-vars=NOTIFICATION_PUSH_ENABLED=false
ls_push_run services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com sqladmin.googleapis.com
ls_push_run projects add-iam-policy-binding "$LS_PUSH_PROJECT" --member="serviceAccount:$LS_PUSH_WORKER" --role=roles/cloudsql.client --condition=None --format=none
ls_push_run secrets add-iam-policy-binding platform-db-password --member="serviceAccount:$LS_PUSH_WORKER" --role=roles/secretmanager.secretAccessor --condition=None --format=none
ls_push_run run jobs deploy notification-push --region="$LS_PUSH_REGION" --image="$NOTIFICATION_PUSH_IMAGE" \
  --service-account="$LS_PUSH_WORKER" --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=1800s \
  --command=python --args=-m,backend.api.platform.notifications.push_worker \
  --set-cloudsql-instances="$LS_PUSH_PROJECT:$LS_PUSH_REGION:team4-sql" \
  --set-env-vars="ENVIRONMENT=production,DEV_AUTH_ENABLED=false,SERVICE_NAME=platform,PGHOST=/cloudsql/$LS_PUSH_PROJECT:$LS_PUSH_REGION:team4-sql,PGDATABASE=team4,PGUSER=platform_app,NOTIFICATION_PUSH_ENABLED=true,NOTIFICATION_PUSH_PUBLIC_KEY=$NOTIFICATION_PUSH_PUBLIC_KEY,NOTIFICATION_PUSH_SUBJECT=$NOTIFICATION_PUSH_SUBJECT" \
  --set-secrets=PGPASSWORD=platform-db-password:1,NOTIFICATION_PUSH_PRIVATE_KEY=notification-vapid-key:1
# Existing platform runtime may launch only this Job, with no env overrides permission.
ls_push_run run jobs add-iam-policy-binding notification-push --region="$LS_PUSH_REGION" --member="serviceAccount:$LS_PUSH_PLATFORM" --role=roles/run.invoker --condition=None --format=none
ls_push_run run services add-iam-policy-binding team4-platform --region="$LS_PUSH_REGION" --member="serviceAccount:$LS_PUSH_INVOKER" --role=roles/run.invoker --condition=None --format=none
# Never create/update an active target pointing at dispatch. POST /health is an
# inert parked target (the application's health route accepts GET only). This is
# safe even if create starts enabled, update changes state, or API traffic still
# includes an old revision. No unsupported create --paused flag is assumed.
ls_push_run scheduler jobs "$LS_PUSH_SCHEDULER_ACTION" http notification-push-dispatch --location="$LS_PUSH_REGION" \
  --schedule='* * * * *' --time-zone=Asia/Seoul \
  --uri="$NOTIFICATION_PUSH_SERVICE_URL/health" --http-method=POST \
  --oidc-service-account-email="$LS_PUSH_INVOKER" --oidc-token-audience="$NOTIFICATION_PUSH_SERVICE_URL" \
  --attempt-deadline=30s --max-retry-attempts=0
ls_push_run scheduler jobs pause notification-push-dispatch --location="$LS_PUSH_REGION"
if "$LS_PUSH_APPLY"; then
  LS_PUSH_STATE=$(gcloud scheduler jobs describe notification-push-dispatch --location="$LS_PUSH_REGION" "${LS_PUSH_FLAGS[@]}" --format='value(state)')
  [[ "$LS_PUSH_STATE" == PAUSED ]] || { echo 'Scheduler pause not confirmed; dispatch remains disabled/parked.' >&2; exit 1; }
fi
# Only public config is applied to the API. No VAPID private secret binding there.
ls_push_run run services update team4-platform --region="$LS_PUSH_REGION" \
  --update-env-vars="NOTIFICATION_PUSH_ENABLED=false,NOTIFICATION_PUSH_PUBLIC_KEY=$NOTIFICATION_PUSH_PUBLIC_KEY,NOTIFICATION_PUSH_SERVICE_URL=$NOTIFICATION_PUSH_SERVICE_URL,NOTIFICATION_PUSH_INVOKER_EMAIL=$LS_PUSH_INVOKER,NOTIFICATION_PUSH_PROJECT_NUMBER=$NOTIFICATION_PUSH_PROJECT_NUMBER"
echo 'READY: API config OFF; Scheduler paused with inert POST /health target. Verify serving revisions; no activation/run command is issued.'
echo 'Operator separately verifies serving revisions OFF and prior in-flight work, then configures the dispatch URI while paused, enables the API and explicitly resumes Scheduler.'
