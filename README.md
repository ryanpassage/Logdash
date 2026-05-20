# LogDash

A live monitoring dashboard for Logstash clusters, deployable to Azure App Service. LogDash polls each Logstash node's monitoring API on a 10-second interval, displays real-time health status with drill-down pages, and persists time-series metrics to Azure Table Storage for historical charts.

## Features

- **Live dashboard** — server grid with green/yellow/red health, events/sec rates, auto-refresh via HTMX
- **Server detail** — JVM heap, process CPU, OS load average, pipeline table, 1H–30D Chart.js charts
- **Pipeline detail** — per-plugin event counts and throughput chart
- **Hot threads** — on-demand hot-thread capture per server
- **Persistence** — per-minute samples and hourly rollups in Azure Table Storage (30-day / 1-year retention)
- **No auth required** — designed for internal network deployment

---

## Local Development

### Prerequisites

- Python 3.11+
- (Optional) [Azurite](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azurite) for local Table Storage emulation

### Setup

```bash
git clone <repo>
cd logdash

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# Edit .env — set LOGSTASH_SERVERS at minimum
```

### Start the dev server

```bash
.venv/bin/flask --app app run --debug
```

Visit http://localhost:5000. The dashboard auto-refreshes every 10 seconds.

### Enable local persistence (optional)

Install and start Azurite:

```bash
npm install -g azurite
azurite --silent --location ./.azurite
```

In `.env`, set:

```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tiq1iagFHMlM==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;
```

### Run tests

```bash
.venv/bin/pytest tests/ -v
```

---

## Configuration

All configuration is loaded from environment variables. Set them in `.env` for local dev or in Azure App Service > Configuration > Application settings for production.

| Variable | Required | Default | Description |
|---|---|---|---|
| `LOGSTASH_SERVERS` | Yes | — | JSON array: `[{"name":"ls-01","url":"http://ls-01:9600"}]` |
| `AZURE_STORAGE_CONNECTION_STRING` | No | — | Azure Table Storage connection string (disables persistence if unset) |
| `LOGDASH_POLL_INTERVAL_SECONDS` | No | `10` | How often to poll Logstash APIs (seconds) |
| `LOGDASH_SAMPLE_INTERVAL_SECONDS` | No | `60` | How often to write a sample row to Table Storage (seconds) |
| `LOGDASH_SAMPLE_RETENTION_DAYS` | No | `30` | Days to keep per-minute sample rows |
| `LOGDASH_ROLLUP_RETENTION_DAYS` | No | `365` | Days to keep hourly rollup rows |
| `LOGDASH_HTTP_TIMEOUT_SECONDS` | No | `5` | HTTP timeout for Logstash API calls (seconds) |
| `LOG_FORMAT` | No | — | Set to `json` to emit structured JSON log lines (recommended for Azure) |
| `PORT` | No | `5000` | Port to bind (set automatically by Azure App Service) |

---

## Azure Deployment

### Prerequisites

- Azure subscription
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) installed and logged in (`az login`)
- An Azure Storage Account (for Table Storage persistence)

### 1. Create App Service resources

```bash
az group create --name logdash-rg --location eastus

az appservice plan create \
  --name logdash-plan \
  --resource-group logdash-rg \
  --sku B1 \
  --is-linux

az webapp create \
  --name <your-app-name> \
  --resource-group logdash-rg \
  --plan logdash-plan \
  --runtime "PYTHON:3.11"
```

### 2. Configure application settings

```bash
APP=<your-app-name>
RG=logdash-rg

az webapp config appsettings set \
  --name $APP --resource-group $RG \
  --settings \
    LOGSTASH_SERVERS='[{"name":"ls-01","url":"http://ls-01.internal:9600"}]' \
    AZURE_STORAGE_CONNECTION_STRING="<your-storage-connection-string>" \
    LOG_FORMAT=json \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true
```

### 3. Set the startup command

In Azure Portal → App Service → Configuration → General settings → Startup Command:

```
gunicorn -c gunicorn.conf.py app:app
```

Or via CLI:

```bash
az webapp config set \
  --name $APP --resource-group $RG \
  --startup-file "gunicorn -c gunicorn.conf.py app:app"
```

### 4. Deploy

**From local:**

```bash
az webapp up \
  --name $APP \
  --resource-group $RG \
  --runtime "PYTHON:3.11"
```

**Via ZIP deploy:**

```bash
zip -r deploy.zip . \
  --exclude ".venv/*" ".git/*" ".azurite/*" "*.pyc" "__pycache__/*" ".env"

az webapp deployment source config-zip \
  --name $APP --resource-group $RG \
  --src deploy.zip
```

### Important: single-worker constraint

Gunicorn **must run with 1 worker** (enforced by `gunicorn.conf.py`). APScheduler runs in-process and is a singleton — multiple workers would each start their own scheduler and write duplicate samples. If you need to scale horizontally, migrate the collector jobs to an Azure Function Timer Trigger.

### Viewing logs

```bash
az webapp log tail --name $APP --resource-group $RG
```

---

## Architecture

```
Flask app (single process)
├── APScheduler BackgroundScheduler
│   ├── poll job      — every 10s: fetches Logstash APIs, updates in-memory snapshot
│   ├── sample job    — every 60s: writes EventSamples, JvmSamples, PipelineSamples
│   ├── rollup job    — every 1h:  aggregates previous hour → HourlyRollups
│   └── purge job     — every 24h: deletes rows older than retention thresholds
├── Flask routes
│   ├── /                              dashboard (HTMX auto-refresh)
│   ├── /server/<name>                 server detail + Chart.js charts
│   ├── /server/<name>/pipeline/<id>   pipeline detail
│   ├── /server/<name>/hot-threads     on-demand hot thread capture
│   ├── /api/snapshot                  live JSON snapshot
│   ├── /api/server/<name>/series      time-series JSON for charts
│   └── /healthz                       liveness probe
└── Azure Table Storage
    ├── EventSamples    (per-server per-minute)
    ├── PipelineSamples (per-pipeline per-minute)
    ├── JvmSamples      (per-server per-minute)
    ├── HourlyRollups   (per-server per-hour)
    ├── Servers         (latest metadata)
    └── Health          (status audit log)
```
