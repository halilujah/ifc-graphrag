#!/bin/bash
# Deploy IFC Semantic Query Engine to Google Cloud Run
#
# Prerequisites:
#   1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
#   2. gcloud auth login
#   3. gcloud config set project YOUR_PROJECT_ID
#   4. Enable APIs: gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh

set -euo pipefail

# ---------- Configuration ----------
PROJECT_ID=$(gcloud config get-value project)
REGION="europe-west1"           # Change to your preferred region
SERVICE_NAME="ifc-query-engine"
REPO_NAME="ifc-ai"

echo "=== Deploying to project: $PROJECT_ID, region: $REGION ==="

# ---------- 1. Create Artifact Registry repo (if not exists) ----------
echo ">> Setting up Artifact Registry..."
gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --quiet 2>/dev/null || true

# ---------- 2. Build and push container ----------
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:latest"

echo ">> Building container image..."
gcloud builds submit --tag "$IMAGE" .

# ---------- 3. Deploy to Cloud Run ----------
echo ">> Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 120 \
  --set-env-vars "NEO4J_URI=${NEO4J_URI},NEO4J_USER=${NEO4J_USER},NEO4J_PASSWORD=${NEO4J_PASSWORD},GEMINI_API_KEY=${GEMINI_API_KEY},GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash},API_SECRET_KEY=${API_SECRET_KEY},ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-*},RATE_LIMIT_PER_MINUTE=${RATE_LIMIT_PER_MINUTE:-30}"

# ---------- 4. Get URL ----------
URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)')
echo ""
echo "=== Deployment complete! ==="
echo "URL: $URL"
echo ""
echo "Set ALLOWED_ORIGINS to $URL for tighter security:"
echo "  gcloud run services update $SERVICE_NAME --region $REGION --set-env-vars ALLOWED_ORIGINS=$URL"
