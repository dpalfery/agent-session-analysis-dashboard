---
id: agentdash/architecture
title: Agentdash Core Library Architecture
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
  - Counter
  - rebuild
  - load_rates
---

# `agentdash` Core Library Architecture

The **`agentdash`** Python library ([`agentdash/`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/agentdash)) forms the data modeling, telemetry normalization, SQLite persistence, and session derivation engine of the Agent Session Analysis Dashboard.

---

## 🏗️ Library Structure & Core Modules

```
agentdash/
├── canonical.py    # Canonical data structures (CanonicalSpan, TokenUsage, Session, Request)
├── store.py        # SQLite persistence (sessions.db), WAL mode, schema & token cache
├── pipeline.py     # Ingestion pipeline, session derivation & validation
├── cost.py         # Cost derivation from published rate table vs harness reporting
├── tokens.py       # Tokenizer caching (tiktoken integration)
├── views.py        # View payload construction for dashboard UI
└── adapters/       # Harness adapters and fingerprint registry
    ├── base.py     # Base Adapter interface
    ├── copilot.py  # GitHub Copilot GenAI adapter
    ├── pi.py       # pi / ObservMe adapter
    ├── gemini.py   # Gemini / AGY adapter
    └── registry.py # Adapter dispatch and source matching
```

---

## 🧠 Key Data Models (`canonical.py`)

### 1. `CanonicalSpan`
Represents a single telemetry span normalized from raw OTel spans into a common format:
- `span_id`, `trace_id`, `parent_span_id`
- `harness`, `source`, `name`, `op` (`invoke_agent`, `chat`, `execute_tool`, etc.)
- `timestamp`, `duration_ms`, `status`
- `model`, `tokens` (`TokenUsage`)
- `session_id`, `agent_name`, `is_auxiliary`
- `tool_name`, `tool_type`, `mcp_server`, `skill_name`
- `content` (normalized dict adhering to `CONTENT_KEYS`)
- `raw_attributes` (verbatim key-value pairs)

### 2. Disjoint `TokenUsage` Model
Different harnesses interpret token counters inconsistently:
- **GitHub Copilot**: `gen_ai.usage.input_tokens` **includes** `cache_read` and `cache_creation`.
- **pi / ObservMe**: `gen_ai.usage.input_tokens` **excludes** `cache_read` and `cache_creation`.

`TokenUsage` stores token classes **disjointly** to prevent double-counting or negative fresh token calculations:
```python
@dataclass
class TokenUsage:
    fresh_input: Optional[int] = None
    cache_read: Optional[int] = None
    cache_creation: Optional[int] = None
    output: Optional[int] = None
    reasoning: Optional[int] = None
    reported_input: Optional[int] = None
    reported_output: Optional[int] = None
```
Invariant: `fresh_input + cache_read + cache_creation == total_input`.

### 3. `Session` & `Request`
- **`Request`**: Represents a single user request root (`invoke_agent`) and the turns executed beneath it.
- **`Session`**: Represents a conversation session grouping one or more user requests sharing a `session_id`. Supports hierarchical subagent tracking via `parent_session` and `is_subagent`.

---

## 💾 Storage & Persistence (`store.py`)

The backend uses **SQLite** (`sessions.db`) operating in **WAL (Write-Ahead Logging)** mode for high-concurrency read/write access.

### Database Schema Overview
- **`span`**: Raw span JSON storage indexed by `span_id`, `trace_id`, `parent_span_id`, and `harness`.
- **`token_cache`**: Caches token counts by `(span_id, attr_key, encoding)` to prevent costly re-tokenization.
- **`quarantine`**: Quarantines spans that no registered adapter claimed.
- **`session`**: Derived session payloads ready for immediate UI rendering.
- **`problem`**: Stores validation warnings and errors detected during normalization or session assembly.
- **`meta`**: Stores system metadata and schema versioning (`SCHEMA_VERSION = 2`).

---

## ⚙️ Ingestion & Normalization Pipeline (`pipeline.py`)

1. **Adapter Resolution**: Dispatches raw spans to registered harness adapters (`CopilotAdapter`, `PiAdapter`, `GeminiAdapter`).
2. **Span Normalization**: Transforms raw OTel attributes into `CanonicalSpan` instances.
3. **Session Grouping**: Clusters spans into sessions via `session_id` and hierarchically links subagent runs.
4. **Validation & Problem Detection**: Executes `validate_tokens(span)` and adapter-specific validation rules. Validation failures are recorded as `Problem` instances in `sessions.db`.
5. **View Payload Generation**: Executes `views.build_payload(...)` to derive per-session metrics, cost breakdowns, tool usage summaries, and timeline events.
