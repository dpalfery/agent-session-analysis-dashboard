# AGY OTLP Telemetry Plugin

This plugin exports Antigravity/AGY model invocations and tool calls to an
OTLP/HTTP collector such as Aspire.

Deployment is handled by the `kyber-observe` CLI:

```bash
pip install -e .             # one-time install of the kyber-observe CLI
kyber-observe install gemini --component plugin --method copy
```

This stages the whole directory (as a unit, because `hooks.json` uses relative
`./telemetry.py`) at `~/.gemini/antigravity-cli/plugins/agy-otel-telemetry/`.
`--method agy` delegates to the native `agy plugin install <path>` instead.
`kyber-observe uninstall gemini` removes it. The manifest schema of `plugin.json`
is intentionally minimal (`name`, `description`, `$schema`) because the official
Antigravity manifest schema rejects extra keys; package metadata (version,
author, license) lives in `METADATA.md` next to this file.

## Configuration

```bash
export AGY_OTEL_ENDPOINT=http://127.0.0.1:4318/v1/traces
export AGY_OTEL_CAPTURE_CONTENT=1
export AGY_OTEL_STATE_DIR=~/.gemini/agy-otel-telemetry
```

`AGY_OTEL_CAPTURE_CONTENT=0` keeps prompts, responses, tool arguments, tool
results, and thoughts out of OTLP while retaining metadata and token counts.

## Events

- `PreInvocation` initializes the hook lifecycle without emitting a partial span.
- `PostInvocation` drains completed model transcript records.
- `PostToolUse` drains tool records without prematurely emitting a model span.
- `Stop` performs a final drain.

The plugin reads the `transcriptPath` supplied by AGY. It folds duplicate
transcript updates, uses the transcript's token counters, and persists emitted
record IDs under `AGY_OTEL_STATE_DIR` so repeated hook invocations do not create
duplicate spans.

The plugin intentionally does not use the statusline. Statusline input is a
rendering snapshot and can contain fabricated or incomplete context counters;
the transcript is the source for turn-level data.
