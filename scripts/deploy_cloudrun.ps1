# Deploy Q2 Support Assistant to Cloud Run in project rounak-iitm.
# Usage (from repo root q2_support_assistant):
#   .\scripts\deploy_cloudrun.ps1
# Prerequisites: gcloud authenticated, billing enabled, Cloud Run + Cloud Build APIs.

$ErrorActionPreference = "Stop"

$Project = if ($env:GCP_PROJECT) { $env:GCP_PROJECT } else { "rounak-iitm" }
$Region = if ($env:GCP_REGION) { $env:GCP_REGION } else { "asia-south1" }
$Service = if ($env:CLOUD_RUN_SERVICE) { $env:CLOUD_RUN_SERVICE } else { "q2-support-assistant" }
$Gcloud = if (Test-Path "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd") {
  "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
} else { "gcloud" }

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "Project=$Project Region=$Region Service=$Service"

& $Gcloud config set project $Project

# Enable required APIs (idempotent)
& $Gcloud services enable `
  run.googleapis.com `
  cloudbuild.googleapis.com `
  artifactregistry.googleapis.com `
  secretmanager.googleapis.com `
  --project $Project

# Load keys from local .env (never commit). Prefer GEMINI_API_KEYS.
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) { throw ".env not found — add GEMINI_API_KEYS before deploy" }
$keys = ""
$model = "gemini-2.5-flash-lite"
Get-Content $EnvFile | ForEach-Object {
  if ($_ -match '^\s*GEMINI_API_KEYS=(.+)$') { $keys = $Matches[1].Trim() }
  elseif ($_ -match '^\s*GEMINI_API_KEY=(.+)$' -and -not $keys) { $keys = $Matches[1].Trim() }
  elseif ($_ -match '^\s*MODEL_NAME=(.+)$') { $model = $Matches[1].Trim() }
}
if (-not $keys -or $keys -eq "your_key_here") { throw "GEMINI_API_KEYS missing in .env" }

# Store/update secret
$SecretName = "q2-gemini-api-keys"
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value $keys -NoNewline
$exists = & $Gcloud secrets describe $SecretName --project $Project 2>$null
if ($LASTEXITCODE -ne 0) {
  & $Gcloud secrets create $SecretName --replication-policy=automatic --project $Project
}
& $Gcloud secrets versions add $SecretName --data-file=$tmp --project $Project | Out-Null
Remove-Item $tmp -Force

# Grant Cloud Run runtime SA access to the secret
$ProjectNumber = & $Gcloud projects describe $Project --format="value(projectNumber)"
$RuntimeSa = "$ProjectNumber-compute@developer.gserviceaccount.com"
& $Gcloud secrets add-iam-policy-binding $SecretName `
  --member="serviceAccount:$RuntimeSa" `
  --role="roles/secretmanager.secretAccessor" `
  --project $Project | Out-Null

Write-Host "Building from source and deploying to Cloud Run…"
& $Gcloud run deploy $Service `
  --source $Root `
  --region $Region `
  --project $Project `
  --platform managed `
  --allow-unauthenticated `
  --port 8080 `
  --cpu 2 `
  --memory 2Gi `
  --timeout 3600 `
  --concurrency 20 `
  --max-instances 2 `
  --cpu-boost `
  --execution-environment gen2 `
  --set-secrets "GEMINI_API_KEYS=${SecretName}:latest" `
  --set-env-vars "MODEL_NAME=$model,EMBEDDING_MODEL=gemini-embedding-001,AGENT_MAX_ITERATIONS=20" `
  --quiet

$url = & $Gcloud run services describe $Service --region $Region --project $Project --format="value(status.url)"
Write-Host ""
Write-Host "Deployed: $url"
Write-Host "UI:       $url/app/"
Write-Host "Health:   $url/health"
