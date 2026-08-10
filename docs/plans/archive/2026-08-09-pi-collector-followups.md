# Pi Statusline Collector — Payload Truncation Hardening + Raw Provider-Payload Capture

**Status:** Done
**Date:** 2026-08-09
**Goal:** Harden the pi collector against oversized OTLP attributes (per-leaf + top-level caps, observable exporter) and add an always-on raw `before_provider_request` diagnostic supplement, bounded by the same cap.
**Closed:** 2026-08-10 — all §9 acceptance gates green: `npm run typecheck` clean; `npm test` 55 pass / 0 fail (42 baseline + 13 new for A4/B5); `python3 -m pytest tests/` green; `code-reviewer` APPROVED on all six checks (incl. `original_length` semantics, Tier-1/2 shape preservation, B-chain drain, scope containment, security gate). Follow-up A (two-tier truncation + observable exporter) and Follow-up B (`pi.llm.request.payload` raw capture) both shipped as specified in §2.

---

## 1. Problem / Motivation

The pi statusline collector (`collectors/pi/`) emits OTLP GenAI spans to a local Aspire dashboard. Three gaps:

1. **Unbounded content attributes.** Every content attribute is born via the single synchronous chokepoint `otlp.ts::jsonAttr`, which calls `JSON.stringify(value)` with no size limit. `messages.ts` carries base64 image `data` and tool-result bodies **verbatim** into canonical `raw` parts. A single screenshot or file dump can balloon a serialized attribute to multi-MB, inflating span size and risking collector/pi stability.
2. **Silent exporter failures.** `exporter.ts` swallows non-OK HTTP responses (it only `return`s on `response.ok`, then falls through the endpoint loop) and silently catches thrown errors ("Swallow error silently, try next endpoint"). A broken/misconfigured Aspire endpoint is invisible — the collector never crashes pi, but it also never tells anyone nothing is shipping.
3. **No wire-level ground truth.** The dashboard's reconstruction of "what went to the LLM" is built entirely from parsed structured messages (`gen_ai.input.messages`). There is no record of the raw provider payload as actually sent, so divergence between "what we parsed" and "what actually went out" is undetectable.

Follow-up **A** fixes (1) and (2). Follow-up **B** fixes (3).

### Composition-distortion note (why caps are tiered)
`agentdash/views.py::bucket_context` recomputes context composition by **tokenizing the emitted `raw` text** (`count_text(json.dumps(part.get("raw")))`); it does **not** consult `observme.original_length`. Therefore:
- Truncating **base64 image data** is a pure win — image bytes add real tokens to the figure but carry no semantic context.
- Truncating **meaningful text** distorts the composition figure.

This is why Tier-1 per-leaf caps are the primary defense (targeting the worst bloat: base64 images and large file dumps), and the Tier-2 top-level cap is a rare-fire backstop that preserves composition fidelity on legitimate traffic. When any cap does fire, `observme.original_length` records the pre-cap length so the loss is visible.

## 2. Approved decisions

### Follow-up A — payload truncation hardening + observable exporter (two-tier cap)

- **Tier 1 (leaf caps, in `messages.ts`):**
  - base64 image `data` → cap ~256 chars + `…[truncated, N chars]` suffix.
  - other large `raw` / oversized text leaves → cap ~16 KB each.
- **Tier 2 (attribute backstop, in `otlp.ts::jsonAttr`):** hard cap each serialized content attribute at ~256 KB. When the `stringValue` exceeds the cap, truncate it and stamp span attributes `observme.truncated` (bool `true`) and `observme.original_length` (int = serialized length post-leaf-truncation, pre-top-level-cap).
- **Exporter (`exporter.ts`):** log non-OK HTTP status and thrown errors with a `[pi-statusline]` prefix at warn level; never re-throw; keep the endpoint fallback loop. The collector must never crash pi.
- **Rationale:** per-leaf caps are the real defense against multi-MB bloat (screenshots/file dumps); the 256 KB top-level cap is a backstop that rarely fires on legitimate traffic, preserving context-composition fidelity.

### Follow-up B — `before_provider_request` raw-payload capture (always-on diagnostic supplement)

- Add `BeforeProviderRequestEvent` to `types.ts`; add a summary field + setter and an `endTurn` drain in `session-state.ts`; add a handler in `extension.ts`; emit `pi.llm.request.payload` via the **same** capped `jsonAttr` in `otlp.ts`.
- Tier-2 cap only (no leaf truncation — the provider blob is unknown-shape JSON). Treat as opaque diagnostic data: the Python adapter keeps it in `raw_attributes` only, **never** parsed into canonical fields (no adapter change).
- **Always-on rationale:** structured messages = our reconstruction; raw payload = wire ground truth; emitting both gives "what we parsed" vs "what actually went out," bounded by the same cap.

### Sequencing (must be honored)
- Serialize **A → B**: the only file both touch is `otlp.ts` (B's new attribute reuses A's capped `jsonAttr`). `extension.ts`, `session-state.ts`, `types.ts` are B-only; `messages.ts` and `exporter.ts` are A-only.
- Because the overlap is one small file and both are small `general-purpose` tasks, the plan offers the orchestrator an explicit choice: **(a)** two sequential `general-purpose` workers A-then-B, or **(b)** one `general-purpose` worker doing both back-to-back to avoid the handoff. **Recommendation: (b)** — one worker. The sole shared file is `otlp.ts`, both deltas are small, and a single worker avoids context handoff entirely.

## 3. Investigation findings

All facts below verified against live source on 2026-08-09:

- `collectors/pi/src/otlp.ts` L49 `jsonAttr(key, value)` → L52 `JSON.stringify(value)`; `buildOtlpPayload` emits attributes via `jsonAttr` (e.g. L119 `gen_ai.input.messages`, L127–130 `pi.*`). This is the single chokepoint for both A's Tier-2 cap and B's new attribute.
- `collectors/pi/src/exporter.ts`: `exportSpan` loops `OTLP_ENDPOINTS`; only `if (response.ok) return;`, otherwise falls through; `catch (e)` is silent. Confirmed swallow-and-continue behavior.
- `collectors/pi/src/messages.ts`: canonical parts carry `raw: c` verbatim for `thinking` (L47), `tool_call` (L51), `image` (L53), `other` (L55), plus merged `raw` (L84) and `other` (L101). Base64 image `data` lives inside these `raw` blobs unbounded.
- `collectors/pi/src/types.ts`: defines `ContextEvent` (L157), `BeforeAgentStartEvent` (L142), `MessageEndEvent`, `TurnEndEvent`, `SessionStartEvent`. **No** `BeforeProviderRequestEvent` — must be added for B.
- `collectors/pi/src/session-state.ts`: `endTurn()` at L244 drains the per-turn request view (comments at L97–98 and L255: "drained at endTurn so the next turn's span reports that turn's …"). The `context` setter (`setInputMessages`, called from extension L141) is the overwrite-on-fire pattern B's provider-payload setter mirrors.
- `collectors/pi/src/extension.ts`: handler registration pattern `pi.on("context", (event, _ctx) => { try { state.setInputMessages(...) } catch (err) { console.error("[pi-statusline] ...") } })` (L139–143). `turn_end` (L165) calls `buildOtlpPayload(summary)` (L200) then `state.endTurn()` (L205). B adds a `pi.on("before_provider_request", …)` handler and the new summary field flows through the existing `buildOtlpPayload(summary)` path.
- `agentdash/adapters/pi.py`: `_jattr` (L137) returns `None` on un-parseable JSON (graceful degradation — an over-cap attribute degrades to `None`, never throws). `observme.truncated` + `observme.original_length` already recognized (L48–49). `raw_attributes=dict(span.get("attributes") or {})` (L276) preserves every attribute verbatim — so `pi.llm.request.payload` lands in `raw_attributes` with **zero** adapter change.
- `agentdash/views.py::bucket_context` (L111): tokenizes **emitted** text via `counter.count_text(json.dumps(part.get("raw")))` (L145, L147); does **not** read `original_length`. Confirms: meaningful-text truncation distorts composition; base64-image truncation does not (and reduces noise).
- `before_provider_request` is a real pi event (`BeforeProviderRequestEvent { type: "before_provider_request"; payload: unknown }`), firing per provider call — same overwrite-on-fire / drain-at-`endTurn` lifecycle as the already-wired `context` event.

**Resolved open question (settled by user):** the `before_provider_request` payload is always-on diagnostic-only data — Tier-2 cap, no leaf truncation, never parsed by the adapter into canonical fields.

## 4. Task list

| # | Phase | Component | Description | Skills |
|---|-------|-----------|-------------|--------|
| A1 | A | `collectors/pi/src/messages.ts` | Tier-1 leaf caps: base64 image `data` → ~256 chars + `…[truncated, N chars]` suffix; large `raw`/oversized text leaves → ~16 KB each. | general-purpose |
| A2 | A | `collectors/pi/src/otlp.ts` | Tier-2 backstop in `jsonAttr`: hard cap serialized content attribute at ~256 KB; when exceeded, truncate + stamp `observme.truncated=true` and `observme.original_length` (int = post-leaf, pre-top-level-cap length). | general-purpose |
| A3 | A | `collectors/pi/src/exporter.ts` | Surface non-OK HTTP status and thrown errors via warn-level `[pi-statusline]` log; never re-throw; keep the endpoint fallback loop. | general-purpose |
| A4 | A | `collectors/pi/src/__tests__/` | Tests: image `data` over leaf cap truncated w/ marker; large `raw` over cap truncated; under-cap unchanged; `jsonAttr` attribute >256 KB truncated + both markers present, `original_length` = pre-cap length, under-cap unchanged & unmarked; exporter logs (capture the log) on non-OK and on thrown error, neither throws. | general-purpose |
| B1 | B | `collectors/pi/src/types.ts` | Add `BeforeProviderRequestEvent { type: "before_provider_request"; payload: unknown }`. | general-purpose |
| B2 | B | `collectors/pi/src/session-state.ts` | Add provider-request-payload summary field + setter (overwrite-on-fire, mirroring `setInputMessages`); drain it in `endTurn()`. Surface it on the summary object `buildOtlpPayload` consumes. | general-purpose |
| B3 | B | `collectors/pi/src/extension.ts` | Register `pi.on("before_provider_request", …)` handler feeding the new setter, in the same try/catch + `[pi-statusline]` pattern as the `context` handler. | general-purpose |
| B4 | B | `collectors/pi/src/otlp.ts` | Emit `pi.llm.request.payload` via the **same** capped `jsonAttr` (Tier-2 only). No leaf truncation for this attribute. | general-purpose |
| B5 | B | `collectors/pi/src/__tests__/` | Test: a `before_provider_request` payload is emitted as `pi.llm.request.payload`, stringified, and subject to the same ~256 KB Tier-2 cap (with markers when over cap). | general-purpose |

## 5. Sequencing / dependency graph

```
A1 (messages.ts leaf caps) ─┐
                            ├─► A2 (otlp.ts Tier-2 cap) ─► A4 (tests for A)
A3 (exporter.ts logging) ───┘                                    │
                                                                 ▼
B1 (types.ts event) ─► B2 (session-state.ts) ─► B3 (extension.ts) ─► B4 (otlp.ts payload attr) ─► B5 (tests for B)
```

- **A before B is mandatory.** B4 reuses A2's capped `jsonAttr`; A2 must land first so B4 inherits the backstop automatically.
- Within A: A1 and A3 are independent; A2 can proceed in parallel with A1/A3; A4 depends on A1+A2+A3.
- Within B: B1 → B2 → B3 → B4 → B5 (strict chain; each consumes the prior's output).
- `otlp.ts` is the **only** file both phases touch (A2, B4). All other files are phase-exclusive.

**Orchestrator choice (explicit):** (a) two sequential `general-purpose` workers A-then-B, or (b) one `general-purpose` worker doing both back-to-back. **Recommend (b)** — single worker; shared file is one small file, both deltas are small, and it removes handoff overhead.

## 6. Residual decisions / risks

- **Composition distortion on over-cap meaningful text.** Mitigated by design: Tier-1 caps target base64 images and oversized dumps (not typical prose), Tier-2 rarely fires on legitimate traffic, and every truncation stamps `observme.original_length` so the loss is visible downstream. No `bucket_context` change is in scope (see §7) — distortion is bounded and observable rather than eliminated.
- **Exact cap constants (~256 chars / ~16 KB / ~256 KB).** "~" in the design is deliberate; the implementer should pick clean named constants in a shared spot (e.g. a caps block at the top of `messages.ts` / `otlp.ts`) and use those constants in tests. Values are tunable later without redesign.
- **`original_length` semantics.** Must equal the serialized length **after** Tier-1 leaf caps but **before** the Tier-2 top-level cap (i.e. the length the attribute *would* have been). This is the value that makes downstream loss visible. Test A4 asserts this exact relationship.
- **Provider payload shape is unknown.** B is Tier-2-only precisely because the blob is unknown-shape JSON; `_jattr` returning `None` on un-parseable JSON means an over-cap/over-large payload degrades gracefully (attribute absent), never throws. No adapter change required.

## 7. Out of scope

- **No `agentdash/adapters/pi.py` change.** `pi.llm.request.payload` is diagnostic-only in `raw_attributes`; the adapter never parses it into canonical fields. `_jattr` already handles it (or drops it) via `raw_attributes`. In scope **only if** a future plan decides to surface raw payload in the UI.
- **No `agentdash/views.py::bucket_context` change.** Composition-distortion mitigation is the `observme.original_length` stamp + conservative tiering, not a re-tokenization scheme. Recomputing composition from `original_length` is a separate, larger effort.
- **No new pi lifecycle events beyond `before_provider_request`.** Other provider-level hooks (e.g. `after_provider_response` deeper parsing) are out of scope.
- **No change to the OTLP endpoint list / fallback topology in `exporter.ts`** — only observability of failures, not the strategy.
- **No SemConv/schema bumps or version-stamp changes** beyond emitting the new `observme.*` attributes already understood by the adapter.

## 8. Required skills

- `general-purpose` — all implementation and test tasks (A1–A4, B1–B5). No source edit touches a domain requiring a specialist agent; the Python adapter and views are intentionally untouched.

## 9. Verification harness

All gates must pass before the plan is considered done.

**Collector (TypeScript / Node ≥22):**
- `cd collectors/pi && npm run typecheck` — clean (`tsc --noEmit`).
- `cd collectors/pi && npm test` — all **existing** tests still green (`node --test`), **plus** new tests per A4 and B5:
  - `messages.ts`: image `data` over the leaf cap truncated with a marker suffix; large `raw` over its cap truncated; under-cap content unchanged.
  - `otlp.ts` (`jsonAttr`): an attribute whose serialized form exceeds ~256 KB is truncated and the span carries `observme.truncated=true` + `observme.original_length` = the pre-cap length; under-cap attributes unchanged and unmarked.
  - `exporter.ts`: a non-OK response and a thrown error are each surfaced via a warn-level log (capture the log) and do not throw; the fallback loop is preserved.
  - `extension.ts`/`otlp.ts`: a `before_provider_request` payload is emitted as `pi.llm.request.payload`, stringified, and subject to the same Tier-2 cap.
- `python3 -m pytest tests/` — stays **green** (no adapter change expected; the new `pi.llm.request.payload` and `observme.*` attributes flow into `raw_attributes` only).

**Review gates:**
- Code review by `code-reviewer` (general-purpose deltas): cap-constant naming/placement, `original_length` semantics match §6, exporter never re-throws, `before_provider_request` setter drains in `endTurn`.
- No security-review-blocking concerns anticipated (local-only collector, local Aspire endpoint, no secrets handling changes); `security-review` to confirm no new PII/secret exposure in the raw provider payload capture before merge.
- No Azure validation required — this is a local collector + local dashboard with no cloud resources.
