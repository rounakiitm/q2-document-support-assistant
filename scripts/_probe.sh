#!/usr/bin/env bash
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
URL=$(gcloud run services describe q2-support-assistant --project=rounak-iitm --region=asia-south1 --format='value(status.url)')
echo "URL=$URL"
curl -sS -m 180 "$URL/health" || true
echo
curl -sS -m 60 -o /dev/null -w "app_http=%{http_code}\n" "$URL/app/" || true
