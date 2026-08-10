---
id: ui/architecture
title: Web Dashboard Frontend Architecture
doc-type: architecture
status: current
component: ui
source-root: ui
owner: dpalfery
last-reviewed: 2026-08-09
code-refs:
  - render
---

# Web Dashboard Frontend Architecture

The **`ui`** component ([`ui/`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/ui)) provides the web-based visual analytics dashboard for analyzing AI agent session execution traces.

---

## 🎯 Architectural Goals

1. **Turn-Level Timeline Visualizer**: Render hierarchical agent turn execution sequences, subagent invocations, tool execution durations, and thinking traces.
2. **Disjoint Token Breakdown**: Display stacked visualizations of fresh input, cache reads, cache creations, output, and reasoning tokens without double-counting.
3. **Cross-Harness Cost Analysis**: Compare financial spend per model and harness using published credit rate tables and harness-reported costs side-by-side.
4. **MCP Tool & Schema Efficiency Metrics**: Identify total tool call count, execution latencies, and overhead caused by unused tool schema definitions resident in context.
5. **Validation Triage & Quarantine**: Highlight normalization warnings, token count inconsistencies, and unparsed spans.

---

## 📁 Component Directory Layout

```
ui/
├── dashboard.html   # Main HTML entrypoint and visual layout
├── app.js           # Single-page application router, API client, and state management
├── styles.css       # Custom styling and modern dark mode theme
└── vendor/          # Lightweight UI visualizer libraries (e.g. Chart.js / D3)
```

---

## 🖥️ User Interface Views & Features

### 1. Session Overview Header
Displays active session metadata:
- Harness Name & Agent Version
- Repository, Branch, and Commit SHA
- Session Duration and Total Turn Count
- Total Cost (USD) with explicit rate basis indicator (`published_rates` vs `harness_reported`)

### 2. Turn Timeline View
Renders interactive step-by-step turn cards:
- Prompt text and formatted system instructions
- Model response text and reasoning/thinking trace blocks
- Tool call inputs, return values, and execution duration bars
- Per-turn disjoint token bar charts (`Fresh Input`, `Cache Read`, `Cache Creation`, `Output`, `Reasoning`)

### 3. Disjoint Token & Cost Breakdown Panels
- **Token Composition**: Visualizes cached vs uncached token ratios over time.
- **Cost Matrix**: Breaks down financial costs by model tier and harness provider.

### 4. MCP Tool Performance & Schema Overhead
- Renders call frequency and duration distribution for each invoked MCP tool.
- Calculates estimated token waste incurred by resident tool schemas that were never called during the session.

### 5. Validation & Problem Inspector
Surfaces validation warnings and errors recorded in `sessions.db`, enabling developers to debug adapter normalization issues or missing telemetry attributes.
