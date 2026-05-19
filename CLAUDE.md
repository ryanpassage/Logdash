# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LogDash is a web application that displays a dashboard showing various health metrics for multiple Logstash servers that are all processing the same pipelines using data coming in via a load balancer. The data sources are primarily network infrastructure: firewalls, switches, load balancers, zero trust components, etc. The dashboard will display health statistics from the Logstash servers using the monitoring APIs documented at https://www.elastic.co/docs/api/doc/logstash/. It should use all of the available APIs to provide up-to-date information about the state of the servers in the cluster, including a high-level status of the health of each server and its pipelines. 

The UI should be attractive and present the information in an organized and easily discoverable manner. Use modern Javascript techniques to make the UI responsive and update the data its displaying without requiring the whole page to be reloaded. Use graphs, pie charts, and other visual elements where appropriate to convey important status information. The user should be able to drill down on graphical elements and navigate to sub-pages from the main dashboard to view more details. 

## Description

The underlying app will be an Azure Web App written in Python using Flask. It will need to regularly fetch data from the Logstash APIs on each server (configured in a settings screen or config file), refresh the visual elements on the dashboard, and maintain historical metrics for reporting on trends such as ingestion volumes (# of events, size of events over time, etc.). We will not be standing up an Azure SQL server to support this, so another form of simple persistence is required as supported in Azure.

## Tech Stack
- **Backend:** Python 3.11+, Flask 3.x, APScheduler 3.x (in-process background scheduler)
- **Persistence (Phase 2+):** Azure Table Storage via `azure-data-tables` SDK
- **Frontend:** Jinja2 templates, HTMX (partial refresh), Alpine.js, Chart.js (CDN-loaded)
- **Production server:** Gunicorn with 1 worker (scheduler must be a singleton)

## Architecture
- `app.py` — Flask app factory; starts the collector scheduler on startup
- `config.py` — Loads all configuration from environment variables (see `.env.example`)
- `logdash/logstash_client.py` — HTTP client for the Logstash monitoring APIs
- `logdash/snapshot.py` — Thread-safe in-memory cache of latest state per server; computes events/sec rates
- `logdash/health.py` — Derives green/yellow/red health status from snapshot data
- `logdash/collector.py` — APScheduler background jobs: poll every 10s, (Phase 2+) write samples every 60s
- `logdash/routes/` — Flask Blueprints: dashboard (page views), api (JSON endpoints)
- `templates/` — Jinja2 templates; `partials/` holds HTMX-refreshable fragments

## Key Environment Variables
- `LOGSTASH_SERVERS` — **Required.** JSON array: `[{"name":"ls-01","url":"http://ls-01:9600"}]`
- `AZURE_STORAGE_CONNECTION_STRING` — Required in Phase 2+
- See `.env.example` for all optional tuning variables and Azurite local-dev connection string

## Important Implementation Notes
- Gunicorn **must use a single worker** (`gunicorn -w 1 app:app`) so APScheduler runs as a singleton. Horizontal scaling requires migrating the collector to an Azure Function Timer Trigger.
- `app.py` wraps `collector.start()` in a `WERKZEUG_RUN_MAIN` guard to prevent double-start when Flask's dev reloader is active.
- Rate computation (events/sec) uses `time.monotonic()` deltas across successive polls, clamped to 0 on counter resets.

## Local Development
```bash
cp .env.example .env   # fill in LOGSTASH_SERVERS at minimum
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/flask --app app run --debug
```
Visit http://localhost:5000. Dashboard auto-refreshes every 10s via HTMX.

## Running Tests
```bash
.venv/bin/pytest tests/ -v
```

## Instructions 
 - Read the PLAN.md file in the project root folder for implementation details, progress notes, and other important memory artifacts.
 - Follow secure coding practices, do not hard code any credentials or secrets. Load them from environment variables instead, and document in code comments which ones need to be created.
 - Update this file with useful, relevant, and specific information that needs to be retained about how this project works and is architected. Do not fill this file with frivolous details, but treat it as a set of guidelines and instructions for a developer, and keep it updated accordingly.
 - After completing any full phase of the plan, commit the changes to the repo using a descriptive changelog message