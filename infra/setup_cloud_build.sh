#!/usr/bin/env bash
# KAN-67: user-run setup; this file does not load .env or read secret values.
set +x
set -euo pipefail

LS_BUILD_PROJECT=proj-skkuls0106-183255377938
LS_BUILD_ACCOUNT=skkuls0106@skkukdp.re.kr
LS_BUILD_REGION=asia-northeast3
LS_BUILD_SA="cloudbuild-runner@${LS_BUILD_PROJECT}.iam.gserviceaccount.com"
LS_BUILD_BUCKET="${LS_BUILD_PROJECT}-team4-build-source"
LS_BUILD_FLAGS=("--project=$LS_BUILD_PROJECT" "--account=$LS_BUILD_ACCOUNT" --quiet)

# Lists must succeed before a missing resource is interpreted as a create request.
LS_BUILD_ACCOUNTS=$(gcloud iam service-accounts list "${LS_BUILD_FLAGS[@]}" --format='value(email)')
if ! printf '%s\n' "$LS_BUILD_ACCOUNTS" | rg -Fxq "$LS_BUILD_SA"; then
  gcloud iam service-accounts create cloudbuild-runner \
    --display-name='Team4 Cloud Build runner' "${LS_BUILD_FLAGS[@]}"
fi
LS_BUILD_BUCKETS=$(gcloud storage buckets list "${LS_BUILD_FLAGS[@]}" --format='value(name)')
if ! printf '%s\n' "$LS_BUILD_BUCKETS" | rg -Fxq "$LS_BUILD_BUCKET"; then
  gcloud storage buckets create "gs://${LS_BUILD_BUCKET}" \
    --location="$LS_BUILD_REGION" --uniform-bucket-level-access \
    --public-access-prevention "${LS_BUILD_FLAGS[@]}"
fi

gcloud artifacts repositories add-iam-policy-binding team4 \
  --location="$LS_BUILD_REGION" --member="serviceAccount:$LS_BUILD_SA" \
  --role=roles/artifactregistry.writer --condition=None "${LS_BUILD_FLAGS[@]}" --format=none
gcloud storage buckets add-iam-policy-binding "gs://${LS_BUILD_BUCKET}" \
  --member="serviceAccount:$LS_BUILD_SA" --role=roles/storage.objectViewer \
  --condition=None "${LS_BUILD_FLAGS[@]}" --format=none
gcloud projects add-iam-policy-binding "$LS_BUILD_PROJECT" \
  --member="serviceAccount:$LS_BUILD_SA" --role=roles/logging.logWriter \
  --condition=None "${LS_BUILD_FLAGS[@]}" --format=none
gcloud iam service-accounts add-iam-policy-binding "$LS_BUILD_SA" \
  --member="user:$LS_BUILD_ACCOUNT" --role=roles/iam.serviceAccountUser \
  --condition=None "${LS_BUILD_FLAGS[@]}" --format=none

printf 'READY: %s; source=gs://%s; region=%s\n' "$LS_BUILD_SA" "$LS_BUILD_BUCKET" "$LS_BUILD_REGION"
