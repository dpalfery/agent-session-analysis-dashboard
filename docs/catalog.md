---
id: catalog
title: Component and owner catalog
doc-type: reference
status: draft
owner: dpalfery
last-reviewed: 2026-08-09
---

# Component and owner catalog

This table is the **authoritative vocabulary** for the `component` and `owner`
frontmatter keys. A document naming a component with no row here fails
`KW-DOC-SPEC-004`. The check exists so that components cannot be invented one
document at a time until nobody can say how many there are.

| Component | Type | Source root | Overview | Detailed documentation | Owner | Last reviewed | Status |
|---|---|---|---|---|---|---|---|
| agentdash | Library | agentdash/ | Core python library for adapter normalization, canonical token models, SQLite store, and pipeline ingestion. | [agentdash/architecture.md](agentdash/architecture.md) | dpalfery | 2026-08-09 | active |
| collectors | Collector | collectors/ | Telemetry collectors emitting OTLP GenAI trace spans from agent harnesses (e.g. Gemini/AGY statusline). | [collectors/architecture.md](collectors/architecture.md) | dpalfery | 2026-08-09 | active |
| ui | Frontend | ui/ | Static HTML/JS dashboard frontend for turn-level analytics and cross-harness comparisons. | [ui/architecture.md](ui/architecture.md) | dpalfery | 2026-08-09 | active |
| kyber_observe | CLI | kyber_observe/ | Installer CLI (`kyber-observe`) for harness telemetry collectors: Gemini/AGY statusline + plugin, Pi extension + ObservMe config. Backup + manifest + idempotent uninstall. | — | dpalfery | 2026-08-10 | active |
| serve | API Server | serve.py | Local HTTP API server, Aspire OTel integration, and static host for the dashboard. | [serve/architecture.md](serve/architecture.md) | dpalfery | 2026-08-09 | active |

## How the columns are read

Only **Component** (index 1) and **Owner** (index 6) are parsed, counting the empty
cell produced by the leading pipe. The other columns are for human readers and may
be reworded freely. Moving either parsed column requires a matching
`ontology.catalog` override in `.kyber-weave/kyber-weave.yml`.
