# Pi Status Bar Collector

## Description
A Pi extension that provides a status bar and exports OTLP telemetry for the Agent Session Analysis Dashboard. This collector hooks into the Pi turn events, renders a live status bar, and emits spans for each interaction.

## How it Works
This is a TypeScript-based Pi extension that registers handlers for core lifecycle events (`session_start`, `before_agent_start`, `agent_start`, `context`, `message_end`, `turn_end`, etc.). 

- **Status Bar:** Updates the Pi terminal UI with token usage and cost dynamically after each provider response.
- **OTLP Export:** Emits an OpenTelemetry span at the end of every turn carrying the request context pi sent to the LLM (content attributes are size-capped — see below), not just counters:
  - `gen_ai.system_instructions` — the fully-assembled system prompt (from `before_agent_start`)
  - `gen_ai.input.messages` — the message array sent to the LLM, normalized to `{role, parts:[{type, …}]}` (from the per-call `context` event; user turns, prior assistant text/thinking/tool calls, and tool results)
  - `gen_ai.output.messages` — the model's reply for the turn
  - `pi.llm.request.payload` — the raw provider request body captured from pi's `before_provider_request` event (the wire-format JSON — OpenAI/Anthropic/etc. shape), a diagnostic supplement to the structured `gen_ai.input.messages` above: "what we parsed" vs "what actually went out"
  - `pi.user.prompt`, `pi.skills`, `pi.tools.selected`, `pi.tools.snippets`, `pi.context_files` — the inventory pi loaded

  plus the usage/cost statistics. Content attributes are size-capped to keep spans bounded: oversized leaves are trimmed per-part (base64 image `data` → ~256 chars, other large text → ~16 KB, each with a `…[truncated, N chars]` marker), and every serialized content attribute is backed by a ~256 KB cap that stamps the ObservMe-convention span markers `observme.truncated` and `observme.original_length` when it fires — `pi.llm.request.payload` is subject to the same ~256 KB attribute cap. Spans are exported loopback-only (127.0.0.1/localhost Aspire endpoint). The dashboard's `PiAdapter` reads these back and breaks them into the context-composition buckets (system prompt / conversation history / file contents via tool results), which is the whole point of this collector; `pi.llm.request.payload` stays in the span's raw attributes as opaque diagnostic data and is never parsed into canonical fields.

## Architecture
```
Pi CLI (Agent) → Extension (Status Bar UI)
    ↓
    OTLP HTTP Payload
    ↓
Aspire Dashboard (PiAdapter)
```

## Installation
You can install this via npm or by copying the files manually:

**Via NPM (if published):**
```bash
pi install npm:@dpalfery/pi-statusline
```

**Manual Install:**
Copy the built output to `~/.pi/agent/extensions/`.

## Configuration
The extension exports spans to the OTLP endpoint, which defaults to `http://localhost:4318/v1/traces`.
This targets the Agent Session Analysis (Aspire) Dashboard instance running locally.

## Compatibility
This extension is fully compatible with the existing `PiAdapter` running on the Agent Session Analysis Dashboard. The adapter listens for `pi.llm.request` spans to correlate requests to sessions.

> **Note:** This collector is an alternative to the older `ObservMe` collector. Do not install both simultaneously to avoid duplicate spans being ingested by the dashboard.

## Formatting Note
The generated status bar will show key information compactly:
`[PI] 📁 repo │ 🌿 branch │ 🤖 model │ ⚡ tokens │ 💰 cost │ ⏳ turn #N`
