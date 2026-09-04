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
URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
       --format 'value(status.url)')"
echo "Service URL: $URL"
echo

# Un deploy che risponde non e' un deploy che funziona: se il database non ha
# le colonne che questo codice scrive, l'insert viene rifiutato e *ogni*
# generazione fallisce, novita' o meno. E' successo davvero — fra un push e la
# migrazione applicata dopo — e da fuori sembrava che il sito fosse rotto senza
# motivo. Qui si vede in due secondi, prima di chiudere il terminale.
echo "Checking the deploy (database + schema)..."
HEALTH="$(curl -fsS --max-time 30 "$URL/healthz?deep=true" || true)"
echo "$HEALTH"
case "$HEALTH" in
  *'"status":"ok"'*)
    echo "OK." ;;
  *)
    echo
    echo "!! The service is up but not healthy."
    echo "!! If it names a missing column, apply the pending files in"
    echo "!! supabase/migrations/ from the Supabase SQL editor, then reload this URL."
    echo "!! Nothing else needs redeploying: the schema is read at every request."
    exit 1 ;;
esac
echo
echo "Remember to set the environment variables (once, or in the console):"
echo "  gcloud run services update $SERVICE --project $PROJECT --region $REGION \\"
echo "    --set-env-vars SUPABASE_URL=...,SUPABASE_BUCKET=generations,CORS_ORIGINS=https://your.pages.dev \\"
echo "    --set-secrets SUPABASE_SERVICE_KEY=supabase-service-key:latest,CLEANUP_TOKEN=cleanup-token:latest,IP_HASH_SALT=ip-hash-salt:latest"
