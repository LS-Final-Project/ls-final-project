#!/usr/bin/env bash
# User-run setup. No API keys, account keys, Cloud Run Jobs or business endpoints are created.
set +x
set -euo pipefail
LS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LS_PROJECT=proj-skkuls0106-183255377938
LS_ACCOUNT=skkuls0106@skkukdp.re.kr
LS_ROLE_ID=team4VertexInference
LS_ROLE="projects/$LS_PROJECT/roles/$LS_ROLE_ID"
LS_FLAGS=(--project="$LS_PROJECT" --account="$LS_ACCOUNT" --quiet)
LS_VERIFY=false
if [[ "${1:-}" == --verify-runtime && $# == 1 ]]; then
  LS_VERIFY=true
elif [[ $# != 0 ]]; then
  printf '%s\n' 'Usage: bash infra/setup_vertex_access.sh [--verify-runtime]' >&2
  exit 2
fi

LS_ENABLED=$(gcloud services list --enabled --filter='config.name=aiplatform.googleapis.com' --format='value(config.name)' "${LS_FLAGS[@]}")
if [[ "$LS_ENABLED" != aiplatform.googleapis.com ]]; then
  printf '%s\n' 'Vertex API must already be enabled. No API was enabled by this script.' >&2
  exit 1
fi
LS_ROLES=$(gcloud iam roles list --format='value(name)' "${LS_FLAGS[@]}")
LS_ROLE_ACTION=create
if printf '%s\n' "$LS_ROLES" | rg -Fxq "$LS_ROLE"; then LS_ROLE_ACTION=update; fi
gcloud iam roles "$LS_ROLE_ACTION" "$LS_ROLE_ID" \
  --title='Team4 Vertex inference' --description='Model inference only for C/S and TFT' \
  --permissions=aiplatform.endpoints.predict,serviceusage.services.use --stage=GA \
  "${LS_FLAGS[@]}" --format=none
for LS_SERVICE in cs tft; do
  gcloud projects add-iam-policy-binding "$LS_PROJECT" \
    --member="serviceAccount:$LS_SERVICE-runtime@$LS_PROJECT.iam.gserviceaccount.com" \
    --role="$LS_ROLE" --condition=None "${LS_FLAGS[@]}" --format=none
done
printf '%s\n' 'Vertex inference access prepared for cs-runtime and tft-runtime only.'
if [[ "$LS_VERIFY" == false ]]; then exit 0; fi

# The caller can mint five-minute runtime tokens only during this verification.
# The temporary binding expires in ten minutes even if the terminal is interrupted.
LS_EXPIRES=$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))')
LS_CONDITION="expression=request.time<timestamp('$LS_EXPIRES'),title=team4-vertex-setup-verification"
LS_TEMP_SERVICES=()
cleanup() {
  LS_EXIT=$?
  trap - EXIT
  for LS_CLEAN_SERVICE in "${LS_TEMP_SERVICES[@]}"; do
    if ! gcloud iam service-accounts remove-iam-policy-binding \
      "$LS_CLEAN_SERVICE-runtime@$LS_PROJECT.iam.gserviceaccount.com" \
      --member="user:$LS_ACCOUNT" --role=roles/iam.serviceAccountTokenCreator \
      --condition="$LS_CONDITION" "${LS_FLAGS[@]}" --format=none; then
      printf 'Remove the temporary verification binding on %s-runtime manually; it expires at %s.\n' "$LS_CLEAN_SERVICE" "$LS_EXPIRES" >&2
      LS_EXIT=1
    fi
  done
  exit "$LS_EXIT"
}
trap cleanup EXIT
cd "$LS_ROOT"
for LS_SERVICE in cs tft; do
  gcloud iam service-accounts add-iam-policy-binding \
    "$LS_SERVICE-runtime@$LS_PROJECT.iam.gserviceaccount.com" \
    --member="user:$LS_ACCOUNT" --role=roles/iam.serviceAccountTokenCreator \
    --condition="$LS_CONDITION" "${LS_FLAGS[@]}" --format=none
  LS_TEMP_SERVICES+=("$LS_SERVICE")
done
for LS_SERVICE in cs tft; do
  uv run --frozen python scripts/check_integrations.py vertex \
    --project="$LS_PROJECT" --location=global --model=gemini-3.6-flash \
    --runtime-service="$LS_SERVICE" --gcloud-account="$LS_ACCOUNT" --wait-runtime-iam=180
done
printf '%s\n' 'Both runtime identities passed. Temporary caller bindings are being removed.'
