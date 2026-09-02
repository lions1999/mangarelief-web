#!/usr/bin/env bash
# Deploy the backend to Cloud Run from source (Cloud Build does the docker build).
#
#   ./scripts/deploy_cloudrun.sh my-gcp-project
#
# Two flags here are not tuning, they are correctness:
#
#   --no-cpu-throttling  Cloud Run throttles CPU to near zero outside a request.
#                        A generation runs in a background thread *after* the
#                        202 response, so with the default throttling it would
#                        crawl or stall. This keeps the CPU allocated for the
#                        instance's whole life.
#   --memory 2Gi         A draft (800px) generation peaks around 900 MB RSS and
#                        full quality around 2 GB. 1Gi gets OOM-killed mid-mesh.
set -euo pipefail

PROJECT="${1:?usage: deploy_cloudrun.sh <gcp-project-id> [region]}"
REGION="${2:-europe-west1}"
SERVICE="mangarelief-api"

gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 1 \
  --no-cpu-throttling \
  --concurrency 20 \
  --max-instances 2 \
  --timeout 300 \
  --port 8080

echo
echo "Service URL:"
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format 'value(status.url)'
echo
echo "Remember to set the environment variables (once, or in the console):"
echo "  gcloud run services update $SERVICE --project $PROJECT --region $REGION \\"
echo "    --set-env-vars SUPABASE_URL=...,SUPABASE_BUCKET=generations,CORS_ORIGINS=https://your.pages.dev \\"
echo "    --set-secrets SUPABASE_SERVICE_KEY=supabase-service-key:latest,CLEANUP_TOKEN=cleanup-token:latest,IP_HASH_SALT=ip-hash-salt:latest"
