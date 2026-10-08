#!/usr/bin/env bash
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
echo "== q2 build =="
gcloud builds list --project=rounak-iitm --region=asia-south1 --limit=3 \
  --filter='source.storageSource.object~q2-support-assistant' \
  --format='table(id,status,createTime,duration)'
BID=$(gcloud builds list --project=rounak-iitm --region=asia-south1 --limit=1 \
  --filter='source.storageSource.object~q2-support-assistant' \
  --format='value(id)')
echo "latest=$BID"
if [[ -n "$BID" ]]; then
  echo "== last log lines =="
  gcloud builds log "$BID" --project=rounak-iitm --region=asia-south1 2>&1 | tail -40
fi
echo "== run services =="
gcloud run services list --project=rounak-iitm --region=asia-south1
