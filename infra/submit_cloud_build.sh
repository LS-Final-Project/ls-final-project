#!/usr/bin/env bash
# KAN-67: upload an explicit, committed build context, never the working tree.
set +x
set -euo pipefail

LS_BUILD_ROOT=$(git rev-parse --show-toplevel)
cd "$LS_BUILD_ROOT"
LS_BUILD_INPUTS=(.gcloudignore .dockerignore Dockerfile cloudbuild.yaml pyproject.toml uv.lock backend tests docs/openapi)
if ! git diff --quiet HEAD -- "${LS_BUILD_INPUTS[@]}"; then
  echo 'Commit changes to build inputs before submitting a build.' >&2
  exit 1
fi
# Missing/untracked required roots fail in git archive before any cloud request.
LS_BUILD_SHA=$(git rev-parse HEAD)
LS_BUILD_CONTEXT=$(mktemp -d /tmp/team4-cloud-build.XXXXXXXX)
trap 'rm -rf -- "$LS_BUILD_CONTEXT"' EXIT
git archive HEAD -- "${LS_BUILD_INPUTS[@]}" | tar -x -C "$LS_BUILD_CONTEXT"
for LS_BUILD_FILE in Dockerfile cloudbuild.yaml pyproject.toml uv.lock .gcloudignore .dockerignore; do
  test -f "$LS_BUILD_CONTEXT/$LS_BUILD_FILE"
done
for LS_BUILD_DIR in backend tests docs/openapi; do
  test -d "$LS_BUILD_CONTEXT/$LS_BUILD_DIR"
done
printf 'Building committed source only: %s\n' "$LS_BUILD_SHA"
gcloud builds submit "$LS_BUILD_CONTEXT" \
  --config="$LS_BUILD_CONTEXT/cloudbuild.yaml" \
  --ignore-file="$LS_BUILD_CONTEXT/.gcloudignore" \
  --project=proj-skkuls0106-183255377938 \
  --account=skkuls0106@skkukdp.re.kr \
  --region=asia-northeast3 \
  --service-account=projects/proj-skkuls0106-183255377938/serviceAccounts/cloudbuild-runner@proj-skkuls0106-183255377938.iam.gserviceaccount.com \
  --gcs-source-staging-dir=gs://proj-skkuls0106-183255377938-team4-build-source/source \
  --substitutions="COMMIT_SHA=$LS_BUILD_SHA" \
  --quiet
