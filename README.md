# Agent Session Analysis

Analyses OpenTelemetry spans emitted by coding-agent harnesses and serves a
dashboard over them: tool-schema cost ranking, per-turn token spend, context
composition, trace timeline, and credits-based cost.

Multi-harness by design; **GitHub Copilot Chat is the implemented adapter**.

## Run it

```bash
pip install -r requirements.txt
python3 -m agentdash.ingest       # seed from any exports already in .spans/
python3 serve.py                  # then open http://localhost:8899/
```

A fresh clone needs neither step: `serve.py` creates the store on first run and
supervises a live `aspire otel spans --follow` stream, so new spans land as they
arrive. **Pull latest** forces a full export and reconcile, writing each export
into `.spans/`.

The UI is **served-only**. `ui/dashboard.html` is a static shell that fetches its
data from the API at runtime; it will not work opened from `file://`. That is a
deliberate trade: it keeps captured telemetry out of the HTML entirely, which is
why `ui/` is tracked in git while the store is not.

```
python3 -m agentdash.ingest [FILE...]  ingest exports (default .spans/*.json)
python3 -m agentdash.pipeline          rebuild derived sessions
python3 -m agentdash.store --schema    print the schema this version creates
python3 -m agentdash.store --live      print the schema an existing db has
python3 tests/test_parity.py           verify against the frozen baseline
python3 tests/test_no_data_leak.py     verify the tracked UI carries no data
```

## What is and isn't committed

| Path | Tracked | Why |
| --- | --- | --- |
| `agentdash/`, `serve.py`, `ui/`, `tests/`, `rates.json` | yes | code and config; no captured data |
| `.spans/` | **no** | raw exports — real repo content from tool results |
| `sessions.db` | **no** | derived from `.spans/`; same content |

**The schema needs no separate artifact.** It is version-controlled as code —
the `SCHEMA` constant in `agentdash/store.py`, run by `executescript` on every
`Store()` construction — so any clone builds an empty store on first use and the
`.db` file is pure local data. Inspect it with `--schema` (what this version
creates) or `--live` (what a given file actually has, which is how you spot a
store built by an older version).

## Architecture

```
raw spans ──▶ adapters/<harness> ──▶ sessions.db ──▶ views.py ──▶ JSON API ──▶ ui/
 (export or    detect + normalize     canonical      queries      serve.py     fetches
  --follow)    + validate             + token cache
```

| Module | Responsibility |
| --- | --- |
| `agentdash/canonical.py` | The contract: `CanonicalSpan`, `TokenUsage`, `Session`, `Problem` |
| `agentdash/adapters/` | All harness-specific knowledge. `registry.py` detects, `copilot.py` implements |
| `agentdash/store.py` | SQLite schema, idempotent upsert, token cache, quarantine |
| `agentdash/tokens.py` | Cache-backed tokenization |
| `agentdash/views.py` | Canonical spans → view payload |
| `agentdash/cost.py` | Credits/USD from `rates.json` |
| `agentdash/pipeline.py` | normalize → group → validate → payload → persist |
| `serve.py` | JSON API, static host, follow supervisor |

**The invariant:** nothing downstream of an adapter may name a harness-specific
attribute. `tests/test_no_data_leak.py` enforces it for the UI — it caught three
real leaks (`gen_ai.input.messages`, `github.copilot.agent.type`,
`…parameters.skill_name`) that were then promoted to canonical fields.

### Why SQLite

- **Idempotent ingest.** `span_id` is the primary key; re-ingesting an export or
  a restarted follow stream is a no-op.
- **Token cache.** Measured: 0.46s of a 0.9s run was `tiktoken` over 13.4 MB,
  recomputed every run for unchanged spans. Now a re-run is a 100% cache hit.
- **Selectivity.** In a real buffer only ~0.085% of spans carry LLM telemetry.
- **Concurrency.** The follow-ingest writes while the API reads; WAL handles it.

(Rotation risk was the original argument, but the Docker dashboard persists —
volume and incremental cost are what justify it now.)

## Harness identification

By **attribute fingerprint**, never by the OTel `source`:

- `source` carries a per-instance suffix — `opencode-4dbe5ffe`,
  `opencode-d09d2218` are one harness.
- It does not track content: the bare source `opencode` held the LLM spans while
  the suffixed instances held only app-internal telemetry.
- It is not stable — the same harness changed signature after reconfiguration.

Detection runs in two passes: fingerprint vote per `(source, trace_id)` group,
then source inheritance for still-undecided spans of a source already
confidently mapped. (Needed for 15 `execute_tool` spans that carry `gen_ai.*`
but no Copilot namespace and sit alone in their traces.)

**Unknown spans are quarantined, never guessed at.** Verified: 20,000 OpenCode
spans ingest to `quarantine` with 0 mis-parsed as Copilot.

## The token model

Harnesses disagree about their own counters, silently:

| Harness | Convention |
| --- | --- |
| Copilot (OTel GenAI) | `input_tokens` **includes** cache_read + cache_creation |
| OpenCode (OpenInference) | `llm.token_count.prompt` **excludes** cache_read |

Mapping one onto the other yields plausible, wrong numbers. So `TokenUsage`
stores classes **disjointly** — `fresh_input + cache_read + cache_creation` is
always the true input — and each adapter converts on the way in.

`validate_tokens()` runs over **every** normalized span, including orphans, and
fails loudly on negative fresh input or a decomposition that does not add back
up to what the harness reported. (Orphans were initially skipped; a corrupted
title-generation span slipped through, which is why coverage is now universal.)

Adapters must also reconcile per-turn sums against each request root. All 11
requests reconcile exactly.

## Cost

Credits billing, 1 credit = $0.01. Rates in `rates.json`, transcribed from
[GitHub's published table](https://docs.github.com/copilot/reference/copilot-billing/models-and-pricing)
(retrieved 2026-08-06) — USD per 1M tokens ×100. Column mapping: `Input` →
fresh, `Cached Input` → cache-read, `Cache Write` → cache-creation.

Context tiers (Grok 4.5, GPT-5.6 Luna split at 200K input) are encoded even
though no current turn crosses them. Models absent from the published table
(`gpt-4o-mini`, `text-embedding-3-small-512`) stay `null` → "no published rate",
never `$0.00`. Next-edit suggestions are explicitly free (`not_billed: true`).

Current: **197.75 credits / $1.98** across 62 turns.

## Tokenizer caveat

`o200k_base` is a proxy and fidelity varies sharply: **2.8–4.4%** unattributed
residual on grok-4.5, **35–41%** on claude-sonnet-5. Not a bucketing bug — all
content is present and untruncated; the proxy simply undercounts Claude on dense
JSON. For those sessions treat bucket sizes as a **lower bound**; the UI says so
and names the model. Don't remove that warning.

## Adding a harness

1. Implement `Adapter` in `agentdash/adapters/<name>.py`: `detect`,
   `is_relevant`, `normalize`, `group`, `validate`.
2. Register it in `adapters/registry.py`.
3. Convert token semantics in `normalize()` so the canonical classes stay
   disjoint, and set `reported_input` so `validate()` can prove it.
4. Run `tests/test_parity.py` — Copilot output must not move.

Ship `validate()` with real assertions. Every correctness bug in this project
was caught by reconciling against a number the harness reported itself; a
silently-wrong adapter is the failure mode the design exists to prevent.

## Data handling

Captured spans embed real repo content — source pulled into tool results, git
remotes, infra URLs, filesystem paths. `.spans/` and `sessions.db` are
gitignored. Git history is permanent, so committing them once
survives every later cleanup.

`tests/test_no_data_leak.py` asserts the tracked UI stays clean; the parity
baseline is a **content-free digest** (`tests/fixtures/parity-digest.json`) for
the same reason.
