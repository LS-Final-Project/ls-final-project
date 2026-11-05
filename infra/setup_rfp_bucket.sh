#!/usr/bin/env bash
# KAN-188: RFP 원문 객체 버킷. 사용자가 직접 실행하며 .env나 비밀값을 읽지 않는다.
# tft-runtime 서비스 계정에는 이 버킷에 한해 객체 생성·읽기만 준다. 삭제·덮어쓰기는
# 주지 않는다(objectCreator에는 storage.objects.delete가 없어 같은 키 재기록도 거부된다).
set +x
set -euo pipefail

LS_RFP_PROJECT=proj-skkuls0106-183255377938
LS_RFP_ACCOUNT=skkuls0106@skkukdp.re.kr
LS_RFP_REGION=asia-northeast3
LS_RFP_SA="tft-runtime@${LS_RFP_PROJECT}.iam.gserviceaccount.com"
LS_RFP_BUCKET="${LS_RFP_PROJECT}-team4-rfp-documents"
LS_RFP_FLAGS=("--project=$LS_RFP_PROJECT" "--account=$LS_RFP_ACCOUNT" --quiet)

# 목록 조회가 성공해야 "없음"을 생성 요청으로 해석한다.
LS_RFP_ACCOUNTS=$(gcloud iam service-accounts list "${LS_RFP_FLAGS[@]}" --format='value(email)')
if ! printf '%s\n' "$LS_RFP_ACCOUNTS" | rg -Fxq "$LS_RFP_SA"; then
  echo "tft-runtime 서비스 계정이 없다. 서비스 배포 준비를 먼저 적용한다." >&2
  exit 1
fi
LS_RFP_BUCKETS=$(gcloud storage buckets list "${LS_RFP_FLAGS[@]}" --format='value(name)')
if ! printf '%s\n' "$LS_RFP_BUCKETS" | rg -Fxq "$LS_RFP_BUCKET"; then
  gcloud storage buckets create "gs://${LS_RFP_BUCKET}" \
    --location="$LS_RFP_REGION" --uniform-bucket-level-access \
    --public-access-prevention "${LS_RFP_FLAGS[@]}"
fi

for role in roles/storage.objectCreator roles/storage.objectViewer; do
  gcloud storage buckets add-iam-policy-binding "gs://${LS_RFP_BUCKET}" \
    --member="serviceAccount:$LS_RFP_SA" --role="$role" \
    --condition=None "${LS_RFP_FLAGS[@]}" --format=none
done

printf 'READY: bucket=gs://%s; region=%s; sa=%s (objectCreator+objectViewer)\n' \
  "$LS_RFP_BUCKET" "$LS_RFP_REGION" "$LS_RFP_SA"
printf 'Cloud Run 환경 변수: TFT_DOCUMENT_BUCKET=%s\n' "$LS_RFP_BUCKET"
