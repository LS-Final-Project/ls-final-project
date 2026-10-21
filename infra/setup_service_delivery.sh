#!/usr/bin/env bash
# User runs this once. No GitHub or service-account key secrets are created.
set +x
set -euo pipefail
LS_PROJECT=proj-skkuls0106-183255377938
LS_ACCOUNT=skkuls0106@skkukdp.re.kr
LS_NUMBER=1015616094444
LS_REGION=asia-northeast3
LS_FLAGS=(--project="$LS_PROJECT" --account="$LS_ACCOUNT" --quiet)
LS_POOL=team4-github
LS_PROVIDER=github-main
LS_RELEASE="release-orchestrator@$LS_PROJECT.iam.gserviceaccount.com"
LS_DEPLOY="clouddeploy-runner@$LS_PROJECT.iam.gserviceaccount.com"
LS_BUILDER="cloudbuild-runner@$LS_PROJECT.iam.gserviceaccount.com"
LS_SOURCE="gs://$LS_PROJECT-team4-build-source"

gcloud services enable iamcredentials.googleapis.com sts.googleapis.com "${LS_FLAGS[@]}"
for LS_NAME in web-runtime release-orchestrator clouddeploy-runner; do
  if ! gcloud iam service-accounts describe "$LS_NAME@$LS_PROJECT.iam.gserviceaccount.com" "${LS_FLAGS[@]}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$LS_NAME" --display-name="Team4 $LS_NAME" "${LS_FLAGS[@]}"
  fi
done

for LS_ROLE in roles/cloudbuild.builds.editor roles/run.viewer; do
  gcloud projects add-iam-policy-binding "$LS_PROJECT" --member="serviceAccount:$LS_RELEASE" --role="$LS_ROLE" "${LS_FLAGS[@]}" >/dev/null
done
for LS_ROLE in roles/run.developer roles/logging.logWriter roles/cloudbuild.builds.viewer; do
  gcloud projects add-iam-policy-binding "$LS_PROJECT" --member="serviceAccount:$LS_DEPLOY" --role="$LS_ROLE" "${LS_FLAGS[@]}" >/dev/null
done
for LS_SERVICE in cs tft platform web; do
  gcloud iam service-accounts add-iam-policy-binding "$LS_SERVICE-runtime@$LS_PROJECT.iam.gserviceaccount.com" --member="serviceAccount:$LS_DEPLOY" --role=roles/iam.serviceAccountUser "${LS_FLAGS[@]}" >/dev/null
done
for LS_RUNNER in "$LS_BUILDER" "$LS_DEPLOY"; do
  gcloud iam service-accounts add-iam-policy-binding "$LS_RUNNER" --member="serviceAccount:$LS_RELEASE" --role=roles/iam.serviceAccountUser "${LS_FLAGS[@]}" >/dev/null
done
gcloud storage buckets add-iam-policy-binding "$LS_SOURCE" --member="serviceAccount:$LS_RELEASE" --role=roles/storage.objectUser "${LS_FLAGS[@]}" >/dev/null
gcloud storage buckets add-iam-policy-binding "$LS_SOURCE" --member="serviceAccount:$LS_RELEASE" --role=roles/storage.legacyBucketReader "${LS_FLAGS[@]}" >/dev/null
gcloud storage buckets add-iam-policy-binding "$LS_SOURCE" --member="serviceAccount:$LS_DEPLOY" --role=roles/storage.objectViewer "${LS_FLAGS[@]}" >/dev/null
gcloud artifacts repositories add-iam-policy-binding team4 --location="$LS_REGION" --member="serviceAccount:$LS_DEPLOY" --role=roles/artifactregistry.reader "${LS_FLAGS[@]}" >/dev/null
gcloud run services add-iam-policy-binding team4-platform --region="$LS_REGION" --member="serviceAccount:web-runtime@$LS_PROJECT.iam.gserviceaccount.com" --role=roles/run.invoker "${LS_FLAGS[@]}" >/dev/null

if ! gcloud iam workload-identity-pools describe "$LS_POOL" --location=global "${LS_FLAGS[@]}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$LS_POOL" --location=global --display-name="Team4 GitHub delivery" "${LS_FLAGS[@]}"
fi
LS_CONDITION="assertion.repository_id=='1418330144' && assertion.repository_owner_id=='338908553' && assertion.ref=='refs/heads/main' && assertion.workflow_ref=='LS-Final-Project/ls-final-project/.github/workflows/deploy-services.yml@refs/heads/main'"
LS_MAPPING='google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref'
if gcloud iam workload-identity-pools providers describe "$LS_PROVIDER" --workload-identity-pool="$LS_POOL" --location=global "${LS_FLAGS[@]}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$LS_PROVIDER" --workload-identity-pool="$LS_POOL" --location=global --attribute-mapping="$LS_MAPPING" --attribute-condition="$LS_CONDITION" "${LS_FLAGS[@]}"
else
  gcloud iam workload-identity-pools providers create-oidc "$LS_PROVIDER" --workload-identity-pool="$LS_POOL" --location=global --issuer-uri=https://token.actions.githubusercontent.com --attribute-mapping="$LS_MAPPING" --attribute-condition="$LS_CONDITION" "${LS_FLAGS[@]}"
fi
gcloud iam service-accounts add-iam-policy-binding "$LS_RELEASE" --member="principalSet://iam.googleapis.com/projects/$LS_NUMBER/locations/global/workloadIdentityPools/$LS_POOL/attribute.repository_id/1418330144" --role=roles/iam.workloadIdentityUser "${LS_FLAGS[@]}" >/dev/null
printf '%s\n' 'Service delivery IAM and keyless GitHub authentication prepared.'
printf 'Provider: projects/%s/locations/global/workloadIdentityPools/%s/providers/%s\n' "$LS_NUMBER" "$LS_POOL" "$LS_PROVIDER"
printf '%s\n' 'Automatic CD is not enabled by this script. Bootstrap the web service, verify it, then enable the GitHub repository variable.'
