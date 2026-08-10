---
id: system-architecture
title: Agent Session Analysis Dashboard System Architecture
doc-type: architecture
status: current
component: agentdash
source-root: agentdash
owner: dpalfery
last-reviewed: 2026-08-09
code-refs:
  - CanonicalSpan
  - TokenUsage
  - Session
  - Request
  - Store
  - rebuild
---

# Agent Session Analysis Dashboard System Architecture

The **Agent Session Analysis Dashboard** collects, normalizes, and analyzes execution turn traces across disparate AI agent harnesses (such as Gemini/AGY, GitHub Copilot, pi/ObservMe, and OpenCode). It enables AI engineers to evaluate and optimize turn-level token efficiency, cost breakdown, reasoning overhead, and tool usage across frameworks.

---

## 🎯 System Mission & Goals

1. **Multi-Harness Instrumentation**: Ingest telemetry spans from any agent harness emitting OpenTelemetry (OTel) GenAI semantics.
2. **Central Ingestion Point**: Utilize the **Aspire OTel Dashboard** (or OTel collector on port 4318) as the single central collection point for all agent trace streams, statusline sidecars, and out-of-the-box telemetry emitters.
3. **Disjoint Token Normalization**: Resolve conflicting token counter definitions across harnesses into a unified disjoint token model (`CanonicalSpan`, `TokenUsage`, `Session`).
4. **Interactive Analysis UI**: Provide turn-level timeline visualization, cost breakdown, reasoning output inspection, and MCP tool call performance metrics.

---

## 🏗️ End-to-End System Topology

```
┌────────────────────────────────────────────────────────────────────────┐
│                        AGENT HARNESSES & SIDECARS                     │
│  Gemini / AGY        GitHub Copilot        pi / ObservMe     OpenCode   │
│  (Statusline Hook)  (OTel Exporter)      (OTel Exporter)   (Exporter)  │
└───────────────────┬────────────────────────┬───────────────────┬───────┘
                    │                        │                   │
                    └──────────────────┐     │     ┌─────────────┘
                                       ▼     ▼     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    CENTRAL ASPIRE OTEL DASHBOARD                      │
│                  (OTLP Receiver on Port 4318 / Stream)                │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    │ Fetch / Pull Traces
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    API SERVER & PIPELINE BACKEND                      │
│                                (serve.py)                              │
│                                    │                                   │
│                        ┌───────────┴───────────┐                       │
│                        ▼                       ▼                       │
│                 Harness Adapters       Ingestion Pipeline              │
│               (Explicit Resource       (Normalization,                 │
│                Header Matching)         Validation & Cost)             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       SQLITE STORAGE STORE                             │
│                           (sessions.db)                                │
│   [span]  [token_cache]  [session]  [session_span]  [problem]  [meta]  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        WEB DASHBOARD FRONTEND                          │
│                          (ui/dashboard.html)                           │
│   Turn Timeline · Disjoint Token Composition · Cost · MCP Tool Usage   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🧩 Architectural Sub-Components

The system is organized into four modular components:

| Component | Source Root | Architecture Document | Purpose |
|---|---|---|---|
| **`agentdash`** | [`agentdash/`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/agentdash) | [`docs/agentdash/architecture.md`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/docs/agentdash/architecture.md) | Core Python library providing canonical data models, SQLite storage, adapter registry, cost evaluation, and pipeline normalization. |
| **`collectors`** | [`collectors/`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/collectors) | [`docs/collectors/architecture.md`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/docs/collectors/architecture.md) | Harness statusline hooks and telemetry collectors emitting OTLP GenAI trace spans to the central collector. |
| **`ui`** | [`ui/`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/ui) | [`docs/ui/architecture.md`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/docs/ui/architecture.md) | Web frontend rendering turn timelines, token distributions, cost breakdowns, and MCP tool call metrics. |
| **`serve`** | [`serve.py`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/serve.py) | [`docs/serve/architecture.md`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/docs/serve/architecture.md) | HTTP API server, Aspire OTel integration provider, and static dashboard host. |

---

## 🧠 Key System Invariants & Guarantees

### 1. Centralized Ingestion via Aspire OTel Dashboard
All agent harnesses, sidecars, and status line hooks emit OTLP traces directly to the **Aspire OTel Dashboard**. The local API server (`serve.py`) connects to the central Aspire stream/collector to pull raw traces, guaranteeing that ingestion remains decoupled from agent execution runtime.

### 2. Explicit Source Mapping & Adapter Dispatch
Incoming trace streams declare their harness explicitly via OTel resource headers (e.g. `gen_ai.harness.name`). The adapter registry (`registry.py`) matches these explicit declarations to dispatch spans to the correct harness adapter (`CopilotAdapter`, `PiAdapter`, `GeminiAdapter`).

### 3. Disjoint Token Accounting (`TokenUsage`)
To avoid double-counting or reporting negative uncached prompt tokens across harnesses with conflicting definitions:
- `fresh_input` (uncached prompt tokens)
- `cache_read` (prompt tokens served from cache)
- `cache_creation` (prompt tokens written to cache)
- `output` (completion tokens)
- `reasoning` (thinking tokens, subset of output)

`fresh_input + cache_read + cache_creation` always equals the billable total input. Missing counters (`None`) are never silently coerced to `0`.

### 4. Non-Blended Cost Accounting
Model pricing uses two explicit bases:
- **`published_rates`**: Derived from [`rates.json`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/rates.json) with context-tier pricing.
- **`harness_reported`**: Directly extracted from telemetry attributes (e.g., `pi.llm.cost.*_usd`).

These two bases are tracked independently and never silently blended.
