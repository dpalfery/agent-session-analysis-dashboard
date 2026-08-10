---
id: serve/architecture
title: HTTP API & Server Architecture
doc-type: architecture
status: current
component: serve
source-root: serve.py
owner: dpalfery
last-reviewed: 2026-08-09
code-refs:
  - ServeHandler
---

# HTTP API & Server Architecture

The **`serve`** component ([`serve.py`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/serve.py)) is the local HTTP server hosting the dashboard UI assets, providing REST API endpoints for session analytics, and interfacing with the central **Aspire OTel Dashboard**.

---

## 🎯 Architectural Role

1. **Static Host**: Serves frontend UI assets (`ui/dashboard.html`, `app.js`, `styles.css`) on `http://localhost:8899/`.
2. **REST API Server**: Exposes JSON endpoints for retrieving derived session lists, turn timelines, problem logs, store status, and manual pipeline trigger execution.
3. **Aspire OTel Integration Provider**: Interoperates with the central Aspire OTel Dashboard to pull and process raw OTel traces into SQLite (`sessions.db`).

---

## 🔌 API Endpoints Reference

| Route | Method | Description |
|---|---|---|
| `/` | `GET` | Serves the main static UI (`ui/dashboard.html`). |
| `/api/sessions` | `GET` | Returns list of all derived sessions in the store with metadata. |
| `/api/session/<id>` | `GET` | Returns full derived view payload for a specific session ID. |
| `/api/status` | `GET` | Returns store telemetry summary (span counts, session counts, last ingest info). |
| `/api/rebuild` | `POST` | Triggers pipeline rebuild (`agentdash.pipeline.rebuild`) across stored spans. |
| `/api/problems` | `GET` | Returns all validation problems and normalization warnings. |
| `/api/quarantine` | `GET` | Returns list of unparsed or quarantined spans. |
| `/v1/traces` | `POST` | OTLP HTTP trace receiver endpoint accepting raw OTel span payloads. |

---

## 🚀 Running the Server

Start the local server with standard Python 3:

```bash
python3 serve.py
```

By default, the server listens on port `8899`. Open `http://localhost:8899/` in any browser to access the dashboard.
