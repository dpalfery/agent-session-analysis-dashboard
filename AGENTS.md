---
id: agents-guidelines
title: Agent Session Analysis Architecture and Guidelines
doc-type: architecture
status: current
component: agentdash
source-root: agentdash
owner: dpalfery
last-reviewed: 2026-08-10
code-refs:
  - CanonicalSpan
  - TokenUsage
  - Session
  - Request
---

# Agent Session Analysis Dashboard (`agent-session-analysis-dashboard`)

Welcome AI Assistant! This file provides essential context, architecture details, and coding conventions for working in this codebase.

---

## 🎯 Project Mission & Vision

The purpose of this project is to **collect coding agent turn traces so that we can tune and optimize our agentic coding flows regardless of tool choice**.

- **Multi-Harness Collectors**: Write OTLP/OTel telemetry collectors for as many AI agent harnesses as possible (e.g. Gemini/AGY, GitHub Copilot, pi/ObservMe, OpenCode, etc.).
- **Aspire OTel Dashboard Ingestion**: Collectors feed real-time agent turn spans and traces into the local **Aspire OTel Dashboard** (or OTel collector on port 4318).
- **Data Pipeline & Normalization**: An ingestion pipeline pulls trace data from the OTel storage/stream into a unified canonical data structure (`CanonicalSpan`, `Session`, `TokenUsage`).
- **Turn-Level Analysis UI**: The custom dashboard renders interactive, cross-harness turn analytics, cost breakdown, token composition, and performance comparison to optimize agentic flows.

---

## 🏗️ Core Architecture & Directory Layout

```
.
├── agentdash/                  # Core Python library
│   ├── canonical.py            # Canonical data model (CanonicalSpan, TokenUsage, Session, Request)
│   ├── adapters/               # Harness adapters (Copilot, Pi, Registry)
│   │   ├── base.py             # Base Adapter class & interface
│   │   ├── copilot.py          # GitHub Copilot OTel GenAI adapter
│   │   ├── pi.py               # pi / ObservMe adapter
│   │   └── registry.py         # Attribute fingerprint detection & dispatch
│   ├── store.py                # SQLite database management (sessions.db)
│   ├── pipeline.py             # Span ingestion, normalization, and session derivation
│   └── cost.py                 # Token pricing & model cost estimation
├── collectors/                 # Harness telemetry collectors
│   ├── gemini/
│   │   ├── statusline.py       # Custom Gemini / AGY status bar & OTLP span collector
│   │   └── agy-otel-telemetry/ # AGY plugin + hooks (deployed by kyber-observe)
│   └── pi/                     # Pi extension (deployed by kyber-observe)
├── kyber_observe/              # Installer CLI: installs collectors into harness configs
├── ui/                         # Web dashboard frontend (dashboard.html, JS, CSS)
├── serve.py                    # Local HTTP API & static dashboard web server
├── generate_sample_data.py     # Sample telemetry generator for testing
├── rates.json                  # Model token pricing table (USD per 1M tokens)
├── pyproject.toml              # Packaging; console script kyber-observe
├── tests/                      # Unit & integration tests
└── README.md                   # Primary project documentation
```

---

## 🧠 Key Design Principles & Data Models

### 1. Disjoint Token Model (`canonical.py`)
Harnesses interpret token counters differently:
- **GitHub Copilot**: `gen_ai.usage.input_tokens` **INCLUDES** `cache_read` and `cache_creation`.
- **pi / ObservMe**: `gen_ai.usage.input_tokens` **EXCLUDES** `cache_read` and `cache_creation`.

To avoid double-counting or negative fresh token figures, `TokenUsage` stores token classes **disjointly**:
- `fresh_input` (uncached prompt tokens)
- `cache_read` (prompt tokens served from cache)
- `cache_creation` (prompt tokens written to cache)
- `output` (completion output tokens)
- `reasoning` (thinking tokens, subset of output)

*Rule*: Never coerce missing token counters (`None`) to `0`. A missing counter and a measured zero represent different telemetry facts.

### 2. Standardized OTel GenAI Attributes
- Spans use `gen_ai.operation.name` (`"chat"`, `"invoke_agent"`, `"execute_tool"`).
- `gen_ai.input.messages` & `gen_ai.tool.definitions` must be stringified **valid JSON arrays** of objects to avoid `.NET / System.Text.Json` deserialization errors in OTel viewers.
- Session grouping uses `copilot_chat.chat_session_id` or `gen_ai.session.id`.

### 3. Harness Collectors (`collectors/`)
- Each harness directory inside `collectors/` (e.g. `collectors/gemini/`, `collectors/copilot/`) holds the instrumentation code that hooks into the CLI / agent status line / telemetry emitter.
- Collectors emit OTLP Spans (`/v1/traces`) adhering to OTel GenAI Semantic Conventions.

---

## 🛠️ Development & Testing Workflow

- **Run Dashboard Server**:
  ```bash
  python3 serve.py
  ```
  Access at `http://localhost:8899/`.

- **Test Statusline Collector**:
  ```bash
  cat ~/.gemini/antigravity-cli/statusline_last_stdin.json | python3 collectors/gemini/statusline.py
  ```

- **Install Collectors with `kyber-observe`** (replaces `collectors/gemini/install.sh`):
  ```bash
  pip install -e .             # one-time install of the kyber-observe CLI
  kyber-observe list           # gemini: statusline, plugin / pi: extension, observme
  kyber-observe install gemini
  kyber-observe install pi
  kyber-observe status         # installed components + backup locations
  ```

- **Run Unit Tests**:
  ```bash
  pytest tests/
  ```

---

## 📋 Guidelines for AI Agents Working in this Codebase

1. **Schema Modifications**: If derived schema or adapter normalization logic changes, bump `SCHEMA_VERSION` in `agentdash/canonical.py` so SQLite derived rows are safely recomputed.
2. **Preserve API Contracts**: Ensure adapter return types conform strictly to `CanonicalSpan` and `TokenUsage`.
3. **Empirical Verification**: Always verify code changes by running `python3 serve.py` or executing collector scripts.

## Repository Configuration & Paths Registry (Config Reg)

Agents and skills should look up the following properties dynamically to find the relevant documentation and references for this repository:

- **<docs-root>**: `docs`
- **<documentation-index>**: `docs/catalog.md`  
- **<documentation-ontology>**: `docs/documentation-ontology.md`