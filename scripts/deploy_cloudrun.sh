#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/google-cloud-sdk/bin:$PATH"

PROJECT="${GCP_PROJECT:-rounak-iitm}"
REGION="${GCP_REGION:-asia-south1}"
SERVICE="${CLOUD_RUN_SERVICE:-q2-support-assistant}"

# Resolve Windows project path when run under WSL
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

echo "==> gcloud auth"
gcloud auth list
echo "==> project target: $PROJECT"
gcloud config set project "$PROJECT"

echo "==> verify project access"
gcloud projects describe "$PROJECT" --format='value(projectId)'

echo "==> enable APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project "$PROJECT"

ENV_FILE="$ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2
  exit 1
fi

KEYS="$(grep -E '^GEMINI_API_KEYS=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '\r')"
if [[ -z "$KEYS" ]]; then
  KEYS="$(grep -E '^GEMINI_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '\r')"
fi
MODEL="$(grep -E '^MODEL_NAME=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '\r')"
MODEL="${MODEL:-gemini-2.5-flash-lite}"

if [[ -z "$KEYS" || "$KEYS" == "your_key_here" ]]; then
  echo "ERROR: GEMINI_API_KEYS missing in .env" >&2
  exit 1
fi

SECRET_NAME="q2-gemini-api-keys"
TMP="$(mktemp)"
printf '%s' "$KEYS" > "$TMP"

if ! gcloud secrets describe "$SECRET_NAME" --project "$PROJECT" >/dev/null 2>&1; then
  echo "==> create secret $SECRET_NAME"
  gcloud secrets create "$SECRET_NAME" --replication-policy=automatic --project "$PROJECT"
fi
echo "==> add secret version"
gcloud secrets versions add "$SECRET_NAME" --data-file="$TMP" --project "$PROJECT" >/dev/null
rm -f "$TMP"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "==> grant secret access to $RUNTIME_SA"
gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --project "$PROJECT" >/dev/null

echo "==> deploy Cloud Run service $SERVICE (source build)"
gcloud run deploy "$SERVICE" \
  --source "$ROOT" \
  --region "$REGION" \
  --project "$PROJECT" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 2 \
  --memory 2Gi \
  --timeout 3600 \
  --concurrency 20 \
  --max-instances 2 \
  --cpu-boost \
  --execution-environment gen2 \
  --set-secrets "GEMINI_API_KEYS=${SECRET_NAME}:latest" \
  --set-env-vars "MODEL_NAME=${MODEL},EMBEDDING_MODEL=gemini-embedding-001" \
  --quiet

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.url)')"
echo ""
echo "Deployed: $URL"
echo "UI:       $URL/app/"
echo "Health:   $URL/health"
