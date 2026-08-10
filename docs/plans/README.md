---
id: plans
title: Plan index
doc-type: index
status: current
owner: dpalfery
last-reviewed: 2026-08-10
---

# Plan index

Index of implementation plans. A plan moves to **Done** when every acceptance
gate in its verification harness (§9) passes and code review is APPROVED; the
`Closed` line on the plan records the closeout date and evidence. Completed
plans are archived under `archive/` after closeout.

| Plan | Status | Date | Summary |
|---|---|---|---|
| [pi-statusline-collector](pi-statusline-collector.md) | Ready | 2026-08-09 | Build the pi statusline collector: OTLP GenAI spans + terminal status bar |
| [2026-08-09-pi-collector-followups](archive/2026-08-09-pi-collector-followups.md) | Done (archived) | 2026-08-09 | Payload-truncation hardening (two-tier caps + observable exporter) and raw `before_provider_request` payload capture (`pi.llm.request.payload`) |
| [2026-08-09-gemini-ingestion-provider](2026-08-09-gemini-ingestion-provider.md) | Done | 2026-08-09 | Complete the Gemini/AGY ingestion provider and fix routing + orphan bugs |
| [2026-08-10-kyber-observe-cli](archive/2026-08-10-kyber-observe-cli.md) | Done (archived) | 2026-08-10 | `kyber-observe` CLI installer for plugins, status bars, and hooks |
