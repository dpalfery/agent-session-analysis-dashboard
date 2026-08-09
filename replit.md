# Agent Session Analysis

Analyses OpenTelemetry spans from coding-agent harnesses (GitHub Copilot Chat) and serves an analysis dashboard: tool-schema cost ranking, per-turn token spend, context composition, trace timeline, and credits-based cost.

## How to run

The workflow **Start application** runs the server:

```
PORT=5000 AGENTDASH_FOLLOW=0 python3 serve.py
```

The dashboard is then available in the Replit preview pane.

To reload data without restarting the server, use the **↻ Pull latest** button in the UI (requires the `aspire` CLI, which is not available on Replit).

## Adding span data

Two ways to get data into the dashboard:

### Option A — Sample data (already loaded)

```bash
python3 generate_sample_data.py          # writes .spans/sample.json
python3 -m agentdash.ingest .spans/sample.json
# then refresh the dashboard
```

### Option B — Real Copilot exports

Place raw export JSON files in `.spans/` (gitignored) and run:

```bash
python3 -m agentdash.ingest              # ingests all .spans/*.json
```

After ingesting new spans, hit **↻ Pull latest** or restart the server; the pipeline rebuilds sessions automatically on startup when spans exist but sessions don't.

## Project layout

| Path | Purpose |
| --- | --- |
| `serve.py` | HTTP server — JSON API + static host |
| `agentdash/` | Core library (ingest, pipeline, store, views, cost, tokens) |
| `agentdash/adapters/` | Harness-specific adapters (only Copilot implemented) |
| `ui/` | Static dashboard shell (HTML + JS + CSS) |
| `rates.json` | Copilot model pricing (credits per 1M tokens) |
| `generate_sample_data.py` | Generates demo spans into `.spans/sample.json` |
| `.spans/` | Raw exports — gitignored, may contain repo content |
| `sessions.db` | Derived SQLite store — gitignored |

## Environment variables

| Variable | Default | Notes |
| --- | --- | --- |
| `PORT` | `8899` | Set to `5000` in the Replit workflow |
| `AGENTDASH_FOLLOW` | `1` | Set to `0` to disable the `aspire` live-follow subprocess |
| `AGENTDASH_DB` | `sessions.db` | Path to the SQLite database |
| `AGENTDASH_SPANS_DIR` | `.spans/` | Directory for raw JSON exports |

## User preferences

- Keep the existing Python + vanilla JS stack — no framework migrations.
- The `ui/` directory (HTML, CSS, JS) is where visual design work happens.
