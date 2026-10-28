#!/usr/bin/env bash
# KAN-71 후속: 메일 원문·첨부 객체 버킷. 사용자가 직접 실행하며 .env나 비밀값을 읽지 않는다.
# platform-runtime 서비스 계정에는 이 버킷에 한해 객체 생성·읽기만 준다. 삭제·덮어쓰기 권한은
# 주지 않는다(objectCreator에는 storage.objects.delete가 없어 같은 키 재기록도 거부된다).
set +x
set -euo pipefail

LS_MAIL_PROJECT=proj-skkuls0106-183255377938
LS_MAIL_ACCOUNT=skkuls0106@skkukdp.re.kr
LS_MAIL_REGION=asia-northeast3
LS_MAIL_SA="platform-runtime@${LS_MAIL_PROJECT}.iam.gserviceaccount.com"
LS_MAIL_BUCKET="${LS_MAIL_PROJECT}-team4-mail-objects"
LS_MAIL_FLAGS=("--project=$LS_MAIL_PROJECT" "--account=$LS_MAIL_ACCOUNT" --quiet)

# 목록 조회가 성공해야 "없음"을 생성 요청으로 해석한다.
LS_MAIL_ACCOUNTS=$(gcloud iam service-accounts list "${LS_MAIL_FLAGS[@]}" --format='value(email)')
if ! printf '%s\n' "$LS_MAIL_ACCOUNTS" | rg -Fxq "$LS_MAIL_SA"; then
  echo "platform-runtime 서비스 계정이 없다. infra/prepare_microsoft_auth.py를 먼저 적용한다." >&2
  exit 1
fi
LS_MAIL_BUCKETS=$(gcloud storage buckets list "${LS_MAIL_FLAGS[@]}" --format='value(name)')
if ! printf '%s\n' "$LS_MAIL_BUCKETS" | rg -Fxq "$LS_MAIL_BUCKET"; then
  gcloud storage buckets create "gs://${LS_MAIL_BUCKET}" \
    --location="$LS_MAIL_REGION" --uniform-bucket-level-access \
    --public-access-prevention "${LS_MAIL_FLAGS[@]}"
fi

for role in roles/storage.objectCreator roles/storage.objectViewer; do
  gcloud storage buckets add-iam-policy-binding "gs://${LS_MAIL_BUCKET}" \
    --member="serviceAccount:$LS_MAIL_SA" --role="$role" \
    --condition=None "${LS_MAIL_FLAGS[@]}" --format=none
done

printf 'READY: bucket=gs://%s; region=%s; sa=%s (objectCreator+objectViewer)\n' \
  "$LS_MAIL_BUCKET" "$LS_MAIL_REGION" "$LS_MAIL_SA"
printf 'Cloud Run 환경 변수: MAIL_OBJECT_BUCKET=%s\n' "$LS_MAIL_BUCKET"
