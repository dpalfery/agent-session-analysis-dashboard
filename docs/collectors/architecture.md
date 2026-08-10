---
id: collectors/architecture
title: Telemetry Collectors & Statusline Hooks Architecture
doc-type: architecture
status: current
component: collectors
source-root: collectors
owner: dpalfery
last-reviewed: 2026-08-10
code-refs:
  - GeminiStatuslineCollector
---

# Telemetry Collectors & Statusline Hooks Architecture

The **`collectors`** component ([`collectors/`](file:///Users/dave/git/personal/agent-session-analysis-dashboard/collectors)) contains harness-specific telemetry collectors, statusline sidecars, and instrumentation hooks.

---

## 🎯 Architectural Role

While standard OpenTelemetry-native agent harnesses export OTLP traces directly to the central **Aspire OTel Dashboard**, CLI tools and statusline-based harnesses (e.g. Gemini / AGY) require specialized sidecar collectors. 

These collectors intercept status updates, extract token usage and turn telemetry, format OTLP GenAI trace spans, and forward them to the central collector or SQLite store.

---

## 📁 Component Directory Layout

```
collectors/
└── gemini/
    ├── statusline.py   # Custom Gemini / AGY status bar & OTLP trace collector
    ├── agy-otel-telemetry/  # AGY plugin + hooks (plugin.json, hooks.json, telemetry.py)
    └── install.sh      # Deprecated installer; kept for compatibility — use kyber-observe
```

---

## 📡 Gemini / AGY Statusline Collector (`collectors/gemini/statusline.py`)

### How It Works
1. **Hook Interception**: The statusline hook reads JSON payloads passed via `stdin` by the Antigravity / Gemini CLI statusline trigger (`~/.gemini/antigravity-cli/statusline_last_stdin.json`).
2. **Telemetry Extraction**: Parses session identifiers, active model names, input/output token counts, thinking/reasoning token breakdowns, context-aware 5-hour quota remaining percentages, and duration until quota reset (`⏳ 5h: 23% (1h 4m)`).
3. **OTLP Span Formatting**: Constructs valid OpenTelemetry GenAI trace spans adhering to `gen_ai.*` semantic conventions:
   - Sets explicit resource headers: `gen_ai.harness.name = "gemini"`
   - Populates operation names: `gen_ai.operation.name = "chat"`
   - Encodes model parameters and token counters.
4. **Export Target**: Emits OTLP HTTP trace payloads (`/v1/traces`) to the central **Aspire OTel Dashboard** (port 4318) or directly updates local store caches.

---

## 🚀 Publishing & Installation (`kyber-observe`)

The `kyber-observe` CLI (source `kyber_observe/`) installs and uninstalls all
harness collectors, including the Gemini/AGY statusline. The legacy
`collectors/gemini/install.sh` is **deprecated** — it prints a banner pointing
to the CLI and is kept only for compatibility.

To publish and install the statusline collector into your local CLI environment:
```bash
pip install -e .             # one-time install of the kyber-observe CLI
kyber-observe install gemini --component statusline
```
The installer:
- Copies `collectors/gemini/statusline.py` to `~/.gemini/antigravity-cli/statusline.py`.
- Makes the script executable (`chmod +x`).
- Backs up and merges the `statusLine` block into `~/.gemini/antigravity-cli/settings.json`, preserving all existing keys (notably `mcpServers`).
- Records the install in `~/.config/kyber-observe/manifest.json`; backups live under `~/.config/kyber-observe/backups/`.
- Supports `--dry-run` (prints the plan, writes nothing) and idempotent re-install (`--force` to re-install).

The same CLI installs the AGY OTel plugin + hooks
(`kyber-observe install gemini --component plugin`, `--method copy` or `--method agy`)
and the Pi extension + ObservMe config (`kyber-observe install pi`).
`kyber-observe status` reports installed components and backup locations;
`kyber-observe uninstall <harness>` removes installed files and restores backups.

---

## 🔌 Adding New Collectors

When instrumenting a new agent harness (e.g., Claude Code, Cursor, Windsurf):
1. Create a dedicated directory under `collectors/<harness_name>/`.
2. Implement the sidecar / statusline parser script.
3. Ensure every emitted span sets the explicit resource header `gen_ai.harness.name = "<harness_name>"`.
4. Forward trace spans to the central Aspire OTel collector endpoint (`http://localhost:4318/v1/traces`).
