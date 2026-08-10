---
id: readme
title: Agent Session Analysis Dashboard Overview
doc-type: index
status: current
owner: dpalfery
last-reviewed: 2026-08-10
---

# Agent Session Analysis

Analyses OpenTelemetry spans emitted by coding-agent harnesses and serves a
dashboard over them: tool-schema cost ranking, per-turn token spend, context
composition, trace timeline, and credits-based cost.

Multi-harness by design. Implemented adapters: **GitHub Copilot Chat**,
**pi** (via the [ObservMe](https://github.com/senad-d/ObservMe) extension), and
**Gemini / AGY** (via custom status line collector). The
`⇄ Compare harnesses` view puts them side by side on canonical metrics.

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
kyber-observe install gemini           install Gemini/AGY statusline + plugin
kyber-observe install pi               install Pi extension + ObservMe config
python3 tests/test_statusline.py       verify statusline quota reset parsing & formatting
python3 tests/test_parity.py           verify copilot against the frozen baseline
python3 tests/test_pi_adapter.py       verify pi's token/grouping/cost conventions
python3 tests/test_no_data_leak.py     verify the tracked UI carries no data
```

`test_parity.py` needs the original Copilot corpus in `.spans/`; it is
gitignored, so on a fresh clone it reports that there is nothing to compare
against rather than failing. `test_pi_adapter.py` is self-contained.

## Installing collectors with `kyber-observe`

`kyber-observe` is the unified installer for the harness telemetry collectors.
It replaces the ad-hoc `./collectors/gemini/install.sh` (now deprecated and kept
only for compatibility). Install it once, then use it to deploy the Gemini/AGY
statusline + AGY OTel plugin/hooks and the Pi extension + ObservMe config into
your harness configurations:

```bash
pip install -e .             # one-time install of the kyber-observe CLI
kyber-observe list           # gemini: statusline, plugin / pi: extension, observme
kyber-observe install gemini --component statusline
kyber-observe install gemini --component plugin --method copy
kyber-observe install pi --component extension --method copy
kyber-observe install pi --component observme --endpoint http://localhost:4318
kyber-observe status         # what is installed + where backups live
kyber-observe uninstall gemini
```

Before mutating any config file, `kyber-observe` backs it up under
`~/.config/kyber-observe/backups/` and records every installed component in
`~/.config/kyber-observe/manifest.json`. Re-install is idempotent; `--force`
re-installs; `--dry-run` prints the plan without writing anything.
`python3 -m kyber_observe …` works as an alternative to the console script.

## What is and isn't committed

| Path | Tracked | Why |
| --- | --- | --- |
| `agentdash/`, `collectors/`, `kyber_observe/`, `serve.py`, `ui/`, `tests/`, `rates.json`, `pyproject.toml`, `requirements.txt` | yes | code, collectors, installer CLI, and config; no captured data |
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
| `agentdash/canonical.py` | The contract: `CanonicalSpan`, `TokenUsage`, `Session`, `Problem`, `CONTENT_KEYS` |
| `agentdash/adapters/` | All harness-specific knowledge. `registry.py` detects; `copilot.py`, `pi.py`, and `gemini.py` implement |
| `collectors/` | Telemetry collectors (`collectors/gemini/statusline.py`) emitting OTLP GenAI trace spans |
| `agentdash/compare.py` | Cross-harness metric table; decides what is comparable |
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
| Copilot (OTel GenAI) | `gen_ai.usage.input_tokens` **includes** cache_read + cache_creation |
| pi / ObservMe | `gen_ai.usage.input_tokens` **excludes** both — *same key, opposite meaning* |
| OpenCode (OpenInference) | `llm.token_count.prompt` **excludes** cache_read |

The first two rows are the whole argument for this design. pi and Copilot emit
the **identical attribute key** with **opposite semantics**, so no amount of
reading the OTel spec tells you which you have — only the harness does. Applying
Copilot's subtraction to pi yields negative fresh input on **293 of 307**
measured spans; applying pi's pass-through to Copilot double-counts input by up
to 2×. Verified against pi's own `pi.llm.usage.total_tokens`, which
`fresh + cache_read + cache_creation + output` reproduces exactly on 303/307
spans (the other 4 have both cache classes at zero, where the conventions are
indistinguishable).

Mapping one onto the other yields plausible, wrong numbers. So `TokenUsage`
stores classes **disjointly** — `fresh_input + cache_read + cache_creation` is
always the true input — and each adapter converts on the way in.

`validate_tokens()` runs over **every** normalized span, including orphans, and
fails loudly on negative fresh input or a decomposition that does not add back
up to what the harness reported. (Orphans were initially skipped; a corrupted
title-generation span slipped through, which is why coverage is now universal.)

Adapters must also reconcile per-turn sums against each request root. All 11
requests reconcile exactly.

## Cost — two bases, never blended

`summary.cost.basis` says where a dollar figure came from, and it travels with
every number:

- **`published_rates`** — derived from a published table (Copilot credits).
- **`harness_reported`** — the figure the harness computed itself. pi knows its
  provider's prices and emits `pi.llm.cost.*_usd`; that beats anything this
  dashboard could derive, so it is carried verbatim.

`rates.json` declares **`applies_to`**, and a table is only consulted for a
harness it names. This is load-bearing, not bookkeeping: the table prices
`gpt-5.6-luna` and pi *also* ran `gpt-5.6-luna`, through a different provider on
a plan GitHub does not bill. Unguarded, 143 of 307 pi turns would have silently
acquired GitHub's credit rate and totalled $0.27 against the $1.57 actually
charged — wrong by 5.8×, in the *understating* direction, and entirely
plausible-looking. A harness outside the table's scope is priced by itself or
not at all.

### Copilot credits

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

## The pi adapter

pi is instrumented by the ObservMe extension, which exports OTLP to the same
Aspire dashboard. Point it there with `~/.pi/agent/observme.yaml`:

```yaml
observme:
  enabled: true
  otlp: {endpoint: http://localhost:4318, protocol: http/protobuf}
  privacy: {allowInsecureTransport: true}
```

Its vocabulary is ObservMe's published semantic convention, stamped on every
span as `observme.semconv.version`. Span tree:

```
pi.session ─▶ pi.agent.run ─▶ pi.turn ─┬─▶ pi.llm.request   (op: chat)
 (structural)  (op:            (structural) └─▶ pi.tool.call  (op: execute_tool)
                invoke_agent)
```

`pi.turn` is deliberately **not** mapped to `chat`: it *wraps* the LLM span (1:1,
measured), so opping it would double every turn in the charts.

**Membership is an attribute, not ancestry.** Copilot resolves sessions by
nearest-`invoke_agent`-ancestor because its session id is only on some spans. pi
stamps `pi.session.id` on **1009/1009** spans, and its parent links are *not*
reliable — the dashboard is a ring buffer, and 25 of 1009 spans had already lost
their parent to eviction. Grouping by ancestry silently drops those turns and
their tokens, so `group()` and `nearest_root()` both resolve by attribute
(`pi.session.id`, `pi.agent.run.id`) instead. Same reason requests are built
from run *ids* rather than run *spans*: 17 sessions held 27 run ids but only 20
run spans survived.

### What pi does not export

Named explicitly, because a blank view otherwise reads as "nothing happened":

| Not exported | Consequence |
| --- | --- |
| Tool definitions (only `tool_schema_count`) | Schema-cost ranking is impossible; view 3 falls back to invocations-only |
| Message structure, under `capture.prompts: false` | Context composition cannot be bucketed |
| `ttft`, MCP server names, skill names, git remote/branch/sha | Rendered "not reported", never 0 |

The prompt string pi *does* emit is a reduced rendering, not the request:
measured against pi's own `input_chars` it carries a median **19.6%** of the
characters sent (range 4.4–78%) while `observme.truncated` stays false. Bucketing
it would show a ~80% residual that the UI attributes to tokenizer drift, which it
is not — so it is deliberately not charted, and `adapter.notes()` says why and
how to enable it.

## Comparing harnesses

`⇄ Compare harnesses` (or `GET /api/compare`) renders `compare.py`'s metric
table. Three rules, each earned:

1. **Absent is not zero, and the reason matters.** pi's "tools offered" is 0
   only because it never exports definitions — while it invoked 14 distinct
   tools across 368 calls. Every metric declares whether it is *measurable* for
   a harness separately from its value.
2. **Ratios before totals.** The corpora are whatever was in the ring buffer
   (1009 pi spans vs 17 Copilot). Totals measure how long each was left running;
   the per-turn column is the comparison.
3. **Cost is only comparable through its basis** — see above.

## Adding a harness

1. Implement `Adapter` in `agentdash/adapters/<name>.py`: `detect`,
   `is_relevant`, `normalize`, `group`, `validate`, plus `nearest_root` (views
   uses it) and `notes` (say what the harness omits).
2. Register it in `adapters/registry.py`.
3. Convert token semantics in `normalize()` so the canonical classes stay
   disjoint, and set `reported_input` so `validate()` can prove it. **Do not
   assume `gen_ai.usage.input_tokens` means what it means elsewhere** — Copilot
   and pi read that exact key in opposite directions.
4. Map content onto `canonical.CONTENT_KEYS`. Nothing downstream may address a
   raw attribute name; `tests/test_no_data_leak.py` enforces it for the UI.
5. Add the harness to `rates.json`'s `applies_to` only if that published table
   genuinely bills it. Otherwise let it price itself via `reported_cost_usd`.
6. Run `tests/test_parity.py` — Copilot output must not move — and write the
   equivalent gate for the new harness (`tests/test_pi_adapter.py` is the model:
   it asserts both that the conventions are right *and* that `validate()` catches
   them being wrong).

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
