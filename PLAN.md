# LogDash Implementation Plan

## Context

LogDash is a greenfield project. The repo currently contains only `CLAUDE.md` and a `.git` directory — no source code yet. The goal is to build a Flask web app, deployable to Azure App Service, that polls the Logstash monitoring APIs on multiple cluster nodes, displays live and historical health metrics with drill-down pages, and persists time-series data in Azure Table Storage. The dashboard targets internal use (no auth on the app itself, no auth required to call Logstash), and serves operators who need at-a-glance cluster health plus the ability to investigate a specific server or pipeline.

This plan covers the full v1 build, phased so each step lands a runnable, demonstrable increment.

## Decisions (confirmed)

- **Persistence:** Azure Table Storage
- **Frontend:** Flask + Jinja templates + HTMX (partial updates) + Alpine.js (light interactivity) + Chart.js (graphs)
- **Logstash auth:** None (internal network)
- **Dashboard auth:** None (internal network)
- **Server config:** `LOGSTASH_SERVERS` env var, JSON array of `{name, url}`
- **Poll interval:** 10s for live metrics; 60s sample written to storage
- **Retention:** 30 days of per-minute samples, 1 year of hourly rollups; daily purge job

## Architecture

### Process model
- Single Flask process running on Azure App Service (Linux, Python 3.11).
- **APScheduler `BackgroundScheduler`** runs three in-process jobs:
  - `collector` — every 10s, fetches all Logstash APIs for each configured server, updates in-memory snapshot, writes a sample row to Table Storage every 60s.
  - `rollup` — hourly, aggregates the last hour of per-minute samples into hourly rows.
  - `purge` — daily, deletes per-minute rows older than 30 days and hourly rows older than 1 year.
- Single-instance assumption is documented; scaling out requires moving the collector to an Azure Function Timer Trigger or adding a distributed lock. Out of scope for v1.

### Logstash APIs consumed (per node)
From https://www.elastic.co/docs/api/doc/logstash/:
- `GET /` — version, name, http_address
- `GET /_node` — node info (pipelines list, OS, JVM info)
- `GET /_node/stats` — top-level: jvm, process, events, pipelines, os, reloads
- `GET /_node/stats/pipelines` — per-pipeline events / plugins / queue stats
- `GET /_node/hot_threads?human=true` — drill-down only (lazy fetched)

Single composite call `GET /_node/stats` returns most of what's needed; supplement with `GET /` for version and `GET /_node` for pipeline topology.

### Storage schema (Azure Table Storage)

| Table | PartitionKey | RowKey | Purpose |
|---|---|---|---|
| `Servers` | `server` | `<name>` | Latest known metadata (version, address, last_seen) |
| `EventSamples` | `<server>` | `<inverted_ticks>` | Per-minute snapshot: events in/out/filtered, duration, queue depth |
| `PipelineSamples` | `<server>\|<pipeline_id>` | `<inverted_ticks>` | Per-pipeline per-minute snapshot |
| `JvmSamples` | `<server>` | `<inverted_ticks>` | Heap used/max, threads, GC counts/time |
| `HourlyRollups` | `<server>\|<metric>` | `<inverted_hour_ticks>` | min/max/avg/sum per hour, per metric family |
| `Health` | `<server>` | `<timestamp>` | Computed status with reason; append-only audit |

`inverted_ticks` = `f"{(MAX_TICKS - now_unix_ms):020d}"` so latest rows sort first — standard Table Storage pattern for newest-first queries.

### Health rollup logic
- **Green:** node reachable, `status=green`, all pipelines have `state=loaded` or `running`, no reload failures in last 5 min.
- **Yellow:** `status=yellow`, OR pipeline queue growing >2x baseline, OR recent reload failure, OR JVM heap >80%.
- **Red:** node unreachable, `status=red`, JVM heap >95%, OR any pipeline `state=stopped` / `crashed`.

### URL map
- `GET /` — dashboard: grid of server cards
- `GET /server/<name>` — server detail: JVM/process/OS panels + pipeline table + time-series charts
- `GET /server/<name>/pipeline/<id>` — pipeline detail: per-plugin events, queue, throughput chart
- `GET /server/<name>/hot-threads` — on-demand hot threads view
- `GET /api/snapshot` — JSON of latest in-memory snapshot (HTMX/JS refresh)
- `GET /api/server/<name>/series?metric=…&range=…` — time-series JSON for charts
- `GET /healthz` — liveness for App Service

### File layout
```
/
├── app.py                       # Flask app factory, scheduler bootstrap
├── config.py                    # Env var loading + validation
├── requirements.txt
├── .env.example                 # Documented env vars
├── README.md                    # Setup + deploy
├── PLAN.md                      # This plan
├── logdash/
│   ├── __init__.py
│   ├── logstash_client.py       # HTTP client (requests w/ timeouts + retry)
│   ├── collector.py             # APScheduler jobs: poll, rollup, purge
│   ├── snapshot.py              # In-memory thread-safe latest-state cache
│   ├── storage.py               # Azure Table Storage adapter
│   ├── health.py                # Green/yellow/red rollup rules
│   └── routes/
│       ├── __init__.py
│       ├── dashboard.py
│       ├── server.py
│       ├── pipeline.py
│       └── api.py
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── server.html
│   ├── pipeline.html
│   └── partials/
│       ├── server_card.html
│       ├── pipeline_row.html
│       └── stat_tile.html
├── static/
│   ├── css/site.css
│   └── js/
│       ├── charts.js
│       └── app.js
├── tests/
│   ├── test_logstash_client.py
│   ├── test_storage.py
│   ├── test_health.py
│   └── test_collector.py
└── .github/workflows/azure-deploy.yml   # CI/CD (optional v1.5)
```

### Environment variables (documented in `.env.example` and code)
- `LOGSTASH_SERVERS` — JSON array, e.g. `[{"name":"ls-01","url":"http://ls-01.internal:9600"}]`
- `AZURE_STORAGE_CONNECTION_STRING` — Table Storage connection string
- `LOGDASH_POLL_INTERVAL_SECONDS` (default 10)
- `LOGDASH_SAMPLE_INTERVAL_SECONDS` (default 60)
- `LOGDASH_SAMPLE_RETENTION_DAYS` (default 30)
- `LOGDASH_ROLLUP_RETENTION_DAYS` (default 365)
- `LOGDASH_HTTP_TIMEOUT_SECONDS` (default 5)
- `FLASK_ENV` (development / production)
- `PORT` (App Service sets this)

## Phased delivery

Each phase ends with a commit and a runnable app per CLAUDE.md instructions.

### Phase 1 — Skeleton + live dashboard (no persistence) ✅ COMPLETE
- Project structure, `requirements.txt`, `app.py`, `config.py`, `.env.example`
- `logstash_client.py` calling `/`, `/_node/stats`, `/_node/hot_threads`
- `snapshot.py` thread-safe in-memory cache with monotonic-clock rate calculation
- `collector.py` with APScheduler poll job (seeds snapshot immediately in a daemon thread, then polls on interval)
- `health.py` rollup — green/yellow/red with reasons; escalation logic
- Dashboard page with HTMX partial refresh every 10s (`/_partial/server-cards` endpoint)
- Dark monitoring dashboard UI: teal/amber/red status theme, server card grid, relative timestamps
- Template globals: `format_uptime`, `format_rate`, `format_bytes` (registered in `app.py`)
- 23 unit tests — all passing (health, client, snapshot)
- **Notes:**
  - Rate guard threshold is 0.01s (not 0.5s) to allow test isolation without long sleeps
  - `WERKZEUG_RUN_MAIN` guard prevents double-start of scheduler in Flask debug/reload mode
  - `Details →` link on cards points to `/server/<name>` — implemented in Phase 3

### Phase 2 — Persistence
- `storage.py` wrapping `azure-data-tables` SDK; lazy table creation
- Sample-write job (every 60s) for `EventSamples`, `PipelineSamples`, `JvmSamples`, `Servers`, `Health`
- Storage emulator (Azurite) wiring documented for local dev
- **Verify:** run with Azurite, confirm rows appear via Azure Storage Explorer; restart app and confirm `Servers.last_seen` is preserved.

### Phase 3 — Drill-down pages + charts
- `server.html` page with JVM/process/OS panels and pipeline table
- `pipeline.html` page with per-plugin breakdown
- `/api/server/<name>/series` endpoint returning recent samples as JSON
- Chart.js line charts: events/sec, JVM heap %, pipeline throughput
- Alpine.js for chart range toggles (1h / 6h / 24h)
- **Verify:** navigate from dashboard → server → pipeline, observe charts populated from Table Storage.

### Phase 4 — Rollups + retention
- Hourly rollup job writing `HourlyRollups`
- Daily purge job deleting expired rows
- Chart range >24h pulls from `HourlyRollups` instead of raw samples
- **Verify:** seed sample data with timestamps spanning >24h; confirm rollups generated and old rows purged.

### Phase 5 — Hot threads + polish + deploy
- `/server/<name>/hot-threads` on-demand fetch
- `/healthz` endpoint, structured logging via `logging` module
- `gunicorn` entrypoint configured for App Service (single worker so scheduler stays singleton)
- `README.md` with local-dev + Azure deploy steps (env-var configuration, App Service setup, Azurite for local)
- Optional: GitHub Actions workflow to deploy via `azure/webapps-deploy@v3`
- **Verify:** deploy to a test Azure Web App, confirm dashboard loads and metrics flow.

## Critical libraries
- `Flask` — web framework
- `apscheduler` — in-process background jobs
- `requests` — Logstash HTTP calls
- `azure-data-tables` — Azure Table Storage SDK
- `gunicorn` — production WSGI server
- `python-dotenv` — local env loading
- `pytest` + `responses` (mock HTTP) for tests

CDN-loaded on the frontend: htmx, alpinejs, chart.js (or vendored under `static/vendor/` for offline-friendly deploys).

## Risks & open considerations
- **Single-instance scheduler:** documented; if horizontal scale is later needed, migrate the collector to an Azure Function Timer Trigger or use `apscheduler` with a Table Storage-backed lock.
- **Table Storage write rate:** ~1 write per server per minute per table is well within Table Storage limits (2000 entities/sec per partition).
- **Hot threads endpoint** can be slow; render with a loading state and short cache.
- **No auth** is acceptable per requirements but should be revisited if the app moves outside the private network — Azure App Service Easy Auth can be enabled without code changes.

## Verification (end-to-end)
1. Start Azurite locally: `azurite --silent --location ./.azurite`.
2. Set env vars in `.env`, including `LOGSTASH_SERVERS` pointing at one or more Logstash instances (or a mock served via `responses`).
3. `pip install -r requirements.txt && flask --app app run --debug`.
4. Visit `http://localhost:5000/`; confirm server cards appear and refresh.
5. Click into a server, then a pipeline; confirm charts render with recent data.
6. `pytest` passes for client, storage, health, and collector modules.
7. Deploy to a test Azure App Service; confirm logs show scheduler running and dashboard reachable.
