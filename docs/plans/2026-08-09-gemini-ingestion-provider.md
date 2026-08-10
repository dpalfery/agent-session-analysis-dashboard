# Complete the Gemini/AGY ingestion provider and fix the routing + orphan bugs

**Status:** Done
**Date:** 2026-08-09
**Goal:** Finish the Gemini adapter so AGY spans route to it (not Copilot), render in the dashboard with correct exclusive token semantics, and stop the orphan-problem accumulation in the store.
**Closed:** 2026-08-10 — all §9 acceptance gates green: `python3 -m pytest tests/ -q` green (incl. rewritten `test_gemini_adapter.py` + new `test_registry_gemini_detection.py` and `test_store_problems.py`; `test_parity.py` self-skips as expected); `python3 -m agentdash.pipeline` reports `gemini: 14 sessions / 3279 spans / 0 orphans / 0 validation errors` and `copilot: 2 sessions / 17 spans` (real spans only); `0 problems`, stable across a second rebuild (no accumulation, no `harness IS NULL` rows); `code-reviewer` **APPROVED** with zero actionable findings (detection deferral, exclusive token semantics, orphan-problem fix, reclassify wiring, scope). D1–D10 shipped as specified in §2.

---

## 1. Problem / Motivation

The Gemini/AGY collector (`collectors/gemini/statusline.py`) is the only ingestion provider left incomplete. Three coupled bugs currently prevent its data from rendering correctly; each was verified against the live `sessions.db` (3296 spans stored under `harness='copilot'`).

**Bug A — misdetection (the core blocker).** The AGY collector mimics Copilot attributes: it stamps `copilot_chat.chat_session_id` and (when skills are active) `github.copilot.tool.parameters.skill_name` on every span, **and** sets `gen_ai.system="gemini"`. So `CopilotAdapter.detect` returns 1.0 (`has_genai ∧ has_copilot`) and `GeminiAdapter.detect` also returns 1.0 (`gen_ai.system=="gemini"`). `registry.score_span` uses strict `>` with Copilot first in `ADAPTERS`, so Copilot wins every tie. Result: **3279/3296** "copilot" spans are actually Gemini (verified: 3279 carry `gen_ai.system="gemini"`; the other 17 real Copilot spans carry `gen_ai.system=None`).

**Bug B — wrong token semantics.** `GeminiAdapter.normalize` computes `fresh = max(0, inp - cr - cc)` (Copilot's *inclusive* convention). AGY's `gen_ai.usage.input_tokens` is **exclusive** of the cache classes, identical to pi: **20/3279** spans have `cache_read > input_tokens` (e.g. input=19590, cache_read=24205), which is impossible under the inclusive reading. The `max(0, …)` silently masks the bug (no negative fires); the mismatch surfaces instead as `token_decomposition_mismatch`. The existing `tests/test_gemini_adapter.py` asserts the wrong value (`fresh==1000` for input=5000/cache_read=4000; correct is `fresh==5000`).

**Bug C — orphan-problem accumulation.** `pipeline.rebuild()` runs the universal `validate_tokens` on every normalized span, including spans that belong to no session; those `Problem`s carry `session_id=None`. `Store.replace_sessions` deletes old problems with `DELETE FROM problem WHERE session_id IN (<old session ids>)`, which never matches `NULL`. The `problem` table has **no `harness` column** (verified), so the delete cannot be scoped by harness either. Measured: 20 distinct spans produced **4257** problem rows (one span had 454 copies).

**Bug D (discovered during planning) — misdetection is baked into storage.** `Store.add_spans` uses `INSERT OR IGNORE`, so a span's `harness` column is set once at first ingest and never revisited. `pipeline.rebuild()` reads `raw_spans(harness=…)` and feeds spans to that adapter. Therefore fixing `detect()` alone only routes *new* ingests — the **3279 already-stored spans stay `harness='copilot'`** and would keep being normalized by CopilotAdapter. A one-time reclassification of stored spans is required for Gemini to render at all.

## 2. Approved decisions

- **D1 — Detection fix lives in `CopilotAdapter.detect`.** Add a check at the *top* of `detect`, before the namespace scoring: if the span explicitly declares a foreign `gen_ai.system` (present and `!= "copilot"`), return `0.0` (defer). Real Copilot spans set `gen_ai.system=None` (verified 17/17), so they are unaffected; the AGY spans declaring `gen_ai.system="gemini"` defer and `GeminiAdapter.detect` (1.0) wins via the existing strict-`>` registry. Rationale: an explicit self-declaration of system is a stronger signal than mimicked attribute namespaces; the registry comment already states dispatch is by fingerprint, not source.
- **D2 — Gemini tokens are EXCLUSIVE (pi convention), not inclusive.** In `GeminiAdapter.normalize`: `fresh_input = input_tokens` (pass-through); `cache_read`/`cache_creation`/`output`/`reasoning` passed through unchanged; `reported_input = fresh+cache_read+cache_creation` (the disjoint sum). `reported_input` is the UI's per-turn "input" value (`views.build_payload` reads `t.reported_input`), so it must be the *full* input, and it must equal `total_input` so `validate_tokens.is_consistent` stays consistent. Document that this is our own sum, not a harness-reported figure (Gemini emits no per-call total). Remove the wrong `max(0, inp-cr-cc)` and the fabricated `or`-fallbacks on `source`/`name`/`model`/`agent_name` (the "never synthesize" rule from `canonical.py`).
- **D3 — `group()` is by session-id attribute, flat (pi model).** One `Session` per distinct `copilot_chat.chat_session_id` (`gen_ai.session.id` is the same value) containing all its chat spans. `requests=[]` (no `invoke_agent` roots exist — verified 0 among AGY spans). `repo` from `vcs.repository.name`, `branch` from `vcs.ref.head.name`, `agent_name` from `gen_ai.agent.name`, `label` from repo + model.
- **D4 — `nearest_root()` returns `None` always.** Required because `views.build_payload` and `CopilotAdapter.validate` call it. The reconciliation loop iterates `session.requests` (empty), so `None` is safe and renders an empty reconciliation section.
- **D5 — `validate()` returns `[]`, honestly.** There is no harness-reported per-call token total to reconcile against (only `input_tokens`/`output_tokens`/`cache_read`/`cache_creation` plus a 5-section breakdown that is a *different decomposition*, not a total). The universal `validate_tokens` (non-negativity) already runs on every normalized span in the pipeline; `is_consistent` is tautological here because `reported_input` is our own sum. No decorative checks are added. The absence is documented in the method docstring and surfaced to the UI via `notes()`.
- **D6 — `notes()` explains the coverage gaps.** Flat one-span-per-turn spans (no request roots → no request-level breakdown / reconciliation); no harness per-call total (reconciliation unavailable); no `execute_tool` spans and no tool-RESULT content (tool rows come from `gen_ai.tool.definitions` only, so schema-cost works; result tokens read "not recorded"); `gen_ai.agent.name` always `"antigravity"`, `gen_ai.system` always `"gemini"`; exclusive semantics (cache_read can exceed fresh).
- **D7 — `store.py` orphan-problem fix: add a `harness` column and delete by harness.** Idempotent migration in `Store.__init__`: `PRAGMA table_info(problem)`; if `harness` missing, `ALTER TABLE problem ADD COLUMN harness TEXT` (O(1) in SQLite). Update the `SCHEMA` string's `problem` CREATE to include `harness TEXT` for fresh DBs. `replace_sessions` deletes `WHERE harness=?` (replacing the `session_id IN (...)` delete) and inserts problems with the `harness` param. The migration also runs a one-time `DELETE FROM problem WHERE harness IS NULL` to clear the 4257 stale accumulated rows (fully regenerable on next rebuild).
- **D8 — Stored-span reclassification (required for Gemini to render).** Add `Store.reclassify(resolver)`: re-run detection over all stored raw spans and `UPDATE span SET harness=?` where the resolved harness differs. `pipeline.rebuild()` calls it once per `SCHEMA_VERSION`, gated by meta key `harness_reclassify_version`. `add_spans`/`INSERT OR IGNORE` never revisits stored spans, so without this the 3279 spans stay mis-stored.
- **D9 — Bump `SCHEMA_VERSION` 2 → 3** in `canonical.py` (AGENTS.md guideline #1: derived schema + adapter normalization changed). Gates D8.
- **D10 — Rewrite `tests/test_gemini_adapter.py`** for exclusive semantics and add coverage for `group()`/`nearest_root()`/`validate()`/`notes()` plus a registry-routing regression (a Copilot-mimicking span with `gen_ai.system="gemini"` resolves to Gemini, not Copilot). Add a new store regression test proving repeated `replace_sessions` does not accumulate problems.

## 3. Investigation findings

- **DB state (read-only queries):** 3296 spans under `harness='copilot'` (3279 `gen_ai.system="gemini"`, 17 real Copilot with `gen_ai.system=None`); 1021 under `harness='pi'`. 14 distinct Gemini session ids. 0 `invoke_agent` ops among AGY spans (2 among the real Copilot). AGY spans are flat (0 `parentSpanId`). `problem` table columns: `id, session_id, span_id, severity, code, message, at` — **no harness**. All 4257 stored problems are `session_id IS NULL`.
- **Collector (`collectors/gemini/statusline.py`):** emits `gen_ai.operation.name="chat"`, `gen_ai.system="gemini"`, `gen_ai.provider.name="google"`, `gen_ai.request.model`/`gen_ai.response.model`, `gen_ai.agent.name="antigravity"`, `copilot_chat.chat_session_id`, `gen_ai.session.id` (same value), `vcs.repository.name`, `vcs.ref.head.name`, optional `gen_ai.system_instructions`/`gen_ai.input.messages`/`gen_ai.tool.definitions`/`github.copilot.tool.parameters.skill_name`/`gen_ai.skills`, the four standard `gen_ai.usage.*` token counters, and a 5-section breakdown (`sys_tokens`/`tool_tokens`/`skill_tokens`/`rule_tokens`/`msg_tokens`). It does **not** emit a per-call token total, tool results, or an `invoke_agent` root.
- **`views.build_payload` for empty `requests`/`roots`:** safe — reconciliation loop is empty, `traceId` falls back to `spans[0].trace_id`, timeline renders a flat list (all spans have `parent_span_id=None` → all become top-level nodes).
- **`test_parity.py`:** self-skips on this DB ("baseline corpus is not in sessions.db"), gated by fixture-digest session-id presence. Not affected by these changes.

## 4. Task list

| # | Phase | Component | Description | Skills |
|---|-------|-----------|-------------|--------|
| 1 | foundation | `agentdash/canonical.py` | Bump `SCHEMA_VERSION` 2 → 3 (D9). One-line change; gates the reclassification. | python-dev |
| 2 | detection | `agentdash/adapters/copilot.py` | Add foreign-`gen_ai.system` deferral at the top of `CopilotAdapter.detect` (D1). No other Copilot logic changes. | python-dev |
| 3 | adapter | `agentdash/adapters/gemini.py` | Complete the adapter (D2–D6): fix `normalize` to exclusive semantics + remove fabricated fallbacks; add `group` (flat, by session-id attr, `requests=[]`), `nearest_root` (→`None`), `validate` (→`[]`, documented), `notes` (coverage gaps). Keep existing `detect`/`is_relevant`/`_normalize_messages`. | python-dev |
| 4 | store | `agentdash/store.py` | DDL migration: add `harness TEXT` to `problem` (idempotent `PRAGMA table_info` + `ALTER TABLE`), clear stale `harness IS NULL` rows, update the `SCHEMA` string (D7). Add `reclassify(resolver)` method (D8). Change `replace_sessions` to delete `WHERE harness=?` and insert problems with the harness param. | python-dev |
| 5 | pipeline | `agentdash/pipeline.py` | At the top of `rebuild`, if `store.get_meta("harness_reclassify_version") != str(SCHEMA_VERSION)`, call `store.reclassify(registry.harness_resolver(store.raw_spans()))` then set the meta key (D8). Imports `registry` (already imported). | python-dev |
| 6 | tests | `tests/test_gemini_adapter.py` | Rewrite the token assertion to exclusive semantics (`fresh==5000`, `cache_read==4000`, `reported_input==9000`); add a cache_read>input case proving no negative; add cases for `group` (shared session_id → one session, `requests=[]`), `nearest_root` (→`None`), `validate` (→`[]`), `notes` (non-empty). | test-dev |
| 7 | tests | `tests/test_registry_gemini_detection.py` (new) | Regression for D1: a span mimicking Copilot attrs but with `gen_ai.system="gemini"` resolves to `gemini` via `registry.detect_groups`/`score_span`; a real Copilot span (`gen_ai.system=None`) still resolves to `copilot`. | test-dev |
| 8 | tests | `tests/test_store_problems.py` (new) | Regression for D7: opening a fresh Store adds the `harness` column; calling `replace_sessions` twice with orphan (`session_id=None`) problems does not accumulate rows; old harness rows are cleared on the next call. | test-dev |
| 9 | verify | manual | `pytest tests/`; `python3 -m agentdash.pipeline` (rebuild); `python3 serve.py` then confirm `/api/status` lists `gemini` with sessions, `/api/session/<gemini-id>` returns a payload, `/api/problems` shows no accumulation, and the dashboard renders Gemini turns/schema-cost. | python-dev |

## 5. Sequencing / dependency graph

```
1 (SCHEMA_VERSION) ─┐
2 (copilot detect) ─┼─► 5 (pipeline reclassify) ─┐
3 (gemini adapter) ─┘                             ├─► 9 (verify)
4 (store) ────────────────────────────────────────┘
        │
        └─► 6,7,8 (tests)  [6 & 7 also depend on 2 & 3]
```

- Task 5 depends on 1 (the version key) and 2 (registry must route correctly for reclassify to move spans).
- Tasks 6/7 depend on 2 + 3; task 8 depends on 4.
- Task 9 (verification) runs last and depends on all of 1–5.
- 2, 3, 4 are mutually independent and may proceed in parallel.

## 6. Residual decisions / risks

- **`validate()` returning `[]`** (D5) is the honest call given no harness total, but it weakens Gemini's runtime guard vs. Copilot/pi. Risk: a future collector change that flips token semantics would not be caught per-span (only the empirical `cache_read>input` probe documents the current contract). Mitigated by D6 `notes()` and the documented docstring; revisit if the collector ever emits a per-call total.
- **`reported_input` as our own sum** (D2) deviates from canonical.py's "verbatim harness value" intent, but matches pi's precedent and is required for the UI's input chart. Flagged in the adapter docstring.
- **Reclassify cost** (D8): ~4300 spans, cheap attribute checks; gated to run once per `SCHEMA_VERSION`, so not per-rebuild. No residual risk.
- **`test_parity.py`** is currently skipped on this DB; if the baseline Copilot corpus is re-ingested later, it will re-arm and must still pass (the real Copilot spans keep `gen_ai.system=None` → still detected as Copilot → figures unchanged).

## 7. Out of scope

- **Changing the AGY collector** to stop mimicking Copilot attributes / to emit a per-call token total or tool results. That is collector work and belongs in a separate change; the dashboard must tolerate the spans as emitted today.
- **Reconciling Gemini tokens against the 5-section breakdown.** Verified it is a different decomposition (sums to ~22040, ≠ input), not a usable total.
- **pi / Copilot adapter logic** beyond the one-line D1 deferral.
- **UI/CSS changes.** The existing payload shape renders Gemini correctly once the adapter produces it; `requests`/`reconciliation` are simply empty.

## 8. Required skills

- **python-dev** — adapter completion (gemini.py), detection fix (copilot.py), store migration + reclassify (store.py), pipeline wiring (pipeline.py), SCHEMA_VERSION bump, and the manual web verification.
- **test-dev** — rewriting test_gemini_adapter.py and authoring the two new test files (registry detection routing, store problem accumulation).

(No Azure or security-review scope. The change is local-data-only and adds no network/secret surface.)

## 9. Verification harness

- **Unit:** `pytest tests/` must pass, including the rewritten `test_gemini_adapter.py`, the new `test_registry_gemini_detection.py`, and the new `test_store_problems.py`.
- **Parity gate:** `python3 tests/test_parity.py` continues to self-skip cleanly (baseline absent) and must not newly fail.
- **Pipeline rebuild:** `python3 -m agentdash.pipeline` reports a `gemini` line with 14 sessions / 3279 spans / 0 orphans / 0 validation errors, and `copilot` reports only its 17 real spans.
- **Store health:** after two rebuilds, `problem` row count is stable (no growth) and no `harness IS NULL` problems remain.
- **Web (the user's explicit ask):** `python3 serve.py` → `GET /api/status` shows `gemini` in `harnesses`; `GET /api/session/<gemini-session-id>` returns turns + tool schema-cost + context composition and empty reconciliation; loading the dashboard renders Gemini sessions.
