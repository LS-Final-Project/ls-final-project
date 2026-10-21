#!/usr/bin/env bash
# KAN-62: one read-only PostgreSQL connection per Cloud Run runtime identity.
set +x
set -euo pipefail

if [[ "${LS_PROJECT_ID:-}" != "proj-skkuls0106-183255377938" ||
      "${LS_GCP_ACCOUNT:-}" != "skkuls0106@skkukdp.re.kr" ||
      "${LS_REGION:-}" != "asia-northeast3" ||
      "${DB_NAME:-}" != "team4" ||
      "${CLOUD_SQL_INSTANCE_CONNECTION_NAME:-}" != "proj-skkuls0106-183255377938:asia-northeast3:team4-sql" ]]; then
  echo "Check exported GCP target, DB_NAME and CLOUD_SQL_INSTANCE_CONNECTION_NAME settings." >&2
  exit 1
fi

for LS_SERVICE in cs tft platform; do
  gcloud run jobs deploy "verify-${LS_SERVICE}-db" \
    --image=docker.io/library/postgres:17 \
    --region="$LS_REGION" \
    --project="$LS_PROJECT_ID" \
    --account="$LS_GCP_ACCOUNT" \
    --service-account="${LS_SERVICE}-runtime@${LS_PROJECT_ID}.iam.gserviceaccount.com" \
    --set-cloudsql-instances="$CLOUD_SQL_INSTANCE_CONNECTION_NAME" \
    --set-env-vars="PGHOST=/cloudsql/${CLOUD_SQL_INSTANCE_CONNECTION_NAME},PGDATABASE=${DB_NAME},PGUSER=${LS_SERVICE}_app,PGCONNECT_TIMEOUT=15" \
    --set-secrets="PGPASSWORD=${LS_SERVICE}-db-password:1" \
    --command=psql \
    --args='-X,-v,ON_ERROR_STOP=1,-c,SELECT current_user;' \
    --cpu=1 \
    --memory=512Mi \
    --tasks=1 \
    --parallelism=1 \
    --max-retries=0 \
    --task-timeout=120s \
    --execute-now \
    --wait \
    --quiet
done

echo "PASS: all three Cloud Run DB connection jobs completed successfully."
