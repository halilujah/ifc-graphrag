# Deployment Guide — IFC 4.3 Semantic Query Engine

This guide covers deploying the application to **Google Cloud** using **Cloud Run** (web app) and **Neo4j AuraDB** (graph database). The setup targets an academic demo with cost-free or minimal-cost services.

---

## Architecture Overview

```
┌─────────────┐       ┌──────────────────┐       ┌───────────────┐
│   Browser    │──────▶│  Cloud Run       │──────▶│ Neo4j AuraDB  │
│              │◀──────│  (Flask/Gunicorn)│◀──────│ (Free Tier)   │
└─────────────┘       └──────────────────┘       └───────────────┘
                              │
                              ▼
                      ┌───────────────┐
                      │ Google Gemini │
                      │ API           │
                      └───────────────┘
```

- **Cloud Run** — Serverless container hosting (free tier: 2M requests/month)
- **Neo4j AuraDB Free** — Managed graph database (free tier: 200K nodes, 400K relationships)
- **Gemini API** — Free tier available via Google AI Studio

---

## Prerequisites

| Tool | Install |
|------|---------|
| Google Cloud SDK (`gcloud`) | https://cloud.google.com/sdk/docs/install |
| Docker (for local testing only) | https://docs.docker.com/get-docker/ |
| Python 3.10+ | https://www.python.org/downloads/ |
| Git | https://git-scm.com/ |

---

## Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g., `ifc-query-engine`)
3. Note the **Project ID** (e.g., `ifc-query-engine-12345`)
4. Enable billing (required even for free tier services)

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

5. Enable required APIs:

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com
```

---

## Step 2: Set Up Neo4j AuraDB (Free Tier)

1. Go to [Neo4j AuraDB](https://neo4j.com/cloud/aura-free/) and sign up
2. Create a **Free** instance:
   - Name: `ifc-graph`
   - Region: Choose one close to your Cloud Run region
3. **Save the credentials** shown after creation — you won't see the password again:
   - Connection URI: `neo4j+s://xxxxxxxx.databases.neo4j.io`
   - Username: `neo4j`
   - Password: (auto-generated)

---

## Step 3: Ingest Data into Neo4j

The knowledge graph must be populated before the app works. Run ingestion from your local machine (the data files are too large for the container image).

1. Update your local `.env` with the AuraDB credentials:

```env
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-aura-password
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=gemini-2.5-flash
```

2. Install dependencies and run ingestion:

```bash
pip install -r requirements.txt
python ingest_graph.py
```

3. Verify the data loaded (you should see ~1400 classes, ~700 property sets):

```bash
python -c "
from neo4j import GraphDatabase
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
d = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
with d.session() as s:
    r = s.run('MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt ORDER BY cnt DESC')
    for rec in r: print(f'{rec[\"label\"]}: {rec[\"cnt\"]}')
d.close()
"
```

---

## Step 4: Get a Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Click **Create API Key**
3. Copy the key (starts with `AIza...`)

The free tier gives 15 RPM for Gemini 2.5 Flash, which is sufficient for a demo.

---

## Step 5: Generate an API Secret Key

This key protects your backend API endpoints from unauthorized access. Only the served frontend (which gets the key injected) can call the API.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Save the output — you'll need it in the next step.

---

## Step 5b: Generate a UI Access Token

This token controls who can access the web UI. When set, users must visit `https://your-url/?token=YOUR_TOKEN` — anyone without the token sees "Access denied".

```bash
python -c "import secrets; print(secrets.token_urlsafe(16))"
```

Share the full link (`https://your-url/?token=abc123...`) with your advisor or collaborators. To revoke access, just change the token:

```bash
gcloud run services update ifc-query-engine \
  --region europe-west1 \
  --update-env-vars UI_ACCESS_TOKEN=new-token-here
```

No rebuild needed — Cloud Run restarts in seconds.

---

## Step 6: Deploy to Cloud Run

### Option A: Using the deploy script

Set environment variables and run:

```bash
export NEO4J_URI="neo4j+s://xxxxxxxx.databases.neo4j.io"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="your-aura-password"
export GEMINI_API_KEY="AIza..."
export API_SECRET_KEY="your-generated-secret"
export UI_ACCESS_TOKEN="your-generated-token"

chmod +x deploy.sh
./deploy.sh
```

### Option B: Manual step-by-step

```bash
# Variables
PROJECT_ID=$(gcloud config get-value project)
REGION="europe-west1"
SERVICE_NAME="ifc-query-engine"
REPO_NAME="ifc-ai"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:latest"

# 1. Create Artifact Registry repository
gcloud artifacts repositories create $REPO_NAME \
  --repository-format=docker \
  --location=$REGION

# 2. Build and push container image
gcloud builds submit --tag $IMAGE .

# 3. Deploy to Cloud Run
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE \
  --region $REGION \
  --platform managed \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 120 \
  --set-env-vars "\
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io,\
NEO4J_USER=neo4j,\
NEO4J_PASSWORD=your-aura-password,\
GEMINI_API_KEY=AIza...,\
GEMINI_MODEL=gemini-2.5-flash,\
API_SECRET_KEY=your-generated-secret,\
ALLOWED_ORIGINS=*,\
RATE_LIMIT_PER_MINUTE=30"
```

The deploy command outputs the service URL (e.g., `https://ifc-query-engine-xxxxx-ew.a.run.app`).

---

## Step 7: Post-Deploy Security Hardening

After getting your Cloud Run URL, restrict CORS to only allow requests from your own domain:

```bash
gcloud run services update ifc-query-engine \
  --region europe-west1 \
  --set-env-vars ALLOWED_ORIGINS=https://ifc-query-engine-xxxxx-ew.a.run.app
```

---

## Local Development with Docker

To test the full stack locally before deploying:

```bash
# Make sure .env has your GEMINI_API_KEY and API_SECRET_KEY
docker compose up --build
```

This starts Neo4j + the web app. Access at `http://localhost:8080`.

Note: You still need to ingest data into the local Neo4j. With Docker Compose running:

```bash
# Set NEO4J_URI to the Docker container
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=password123 python ingest_graph.py
```

---

## Security Measures

| Measure | Description |
|---------|-------------|
| **UI Access Token** | The web UI requires `?token=...` in the URL. Share the link with collaborators; revoke by changing the token (no rebuild needed). |
| **API Secret Key** | All `/api/*` endpoints require `X-API-Key` header. The key is injected into the frontend server-side — external scripts cannot call the API without it. |
| **Rate Limiting** | 30 requests/minute per IP address (configurable via `RATE_LIMIT_PER_MINUTE`). |
| **CORS Whitelist** | Set `ALLOWED_ORIGINS` to your Cloud Run URL to block cross-origin requests. |
| **Message Length Limit** | Chat messages capped at 2000 characters (configurable via `MAX_CHAT_MESSAGE_LENGTH`). |
| **Read-Only Cypher** | The `run_cypher` tool only allows `MATCH`/`RETURN` queries — no mutations. |

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NEO4J_URI` | Yes | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | Yes | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | Yes | `password` | Neo4j password |
| `GEMINI_API_KEY` | Yes | — | Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model to use |
| `API_SECRET_KEY` | Recommended | — | Secret for API authentication (empty = no auth) |
| `UI_ACCESS_TOKEN` | Recommended | — | Token required in URL to access UI (empty = open access) |
| `ALLOWED_ORIGINS` | No | `*` | Comma-separated allowed CORS origins |
| `RATE_LIMIT_PER_MINUTE` | No | `30` | Max API requests per IP per minute |
| `MAX_CHAT_MESSAGE_LENGTH` | No | `2000` | Max chat message characters |
| `PORT` | No | `8080` | Server port (set automatically by Cloud Run) |

---

## Updating the Deployment

After code changes, redeploy with:

```bash
# Rebuild and push
gcloud builds submit --tag $IMAGE .

# Deploy new revision
gcloud run deploy ifc-query-engine --image $IMAGE --region europe-west1
```

---

## Cost Estimate (Academic Demo)

| Service | Free Tier | Expected Cost |
|---------|-----------|---------------|
| Cloud Run | 2M requests/month, 360K vCPU-seconds | $0 |
| Neo4j AuraDB Free | 200K nodes, 400K relationships | $0 |
| Gemini API (free tier) | 15 RPM, 1M tokens/min | $0 |
| Artifact Registry | 500 MB storage | $0 |
| Cloud Build | 120 min/day | $0 |

For a demo with moderate usage, **total cost should be $0**.

---

## Troubleshooting

**Container fails to start**
```bash
gcloud run services logs read ifc-query-engine --region europe-west1 --limit 50
```

**Neo4j connection refused**
- Verify the AuraDB instance is running (check Neo4j Console)
- Ensure the URI uses `neo4j+s://` (TLS required for AuraDB)

**401 Unauthorized on API calls**
- Check that `API_SECRET_KEY` env var in Cloud Run matches what the app uses
- If testing via curl: `curl -H "X-API-Key: your-secret" https://your-url/api/classes`

**429 Rate Limited**
- Increase `RATE_LIMIT_PER_MINUTE` or wait 60 seconds

**Gemini 429 (quota exceeded)**
- The free tier has 15 RPM — reduce usage or upgrade to pay-as-you-go
