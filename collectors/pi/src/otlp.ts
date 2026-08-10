/**
 * OTLP Span Builder.
 *
 * Constructs OTLP/HTTP JSON payloads matching the format expected by the
 * Aspire dashboard and the PiAdapter in agentdash/adapters/pi.py.
 *
 * CRITICAL: Span name MUST be "pi.llm.request" — PiAdapter.is_relevant()
 * rejects any name not in SPAN_OPS or STRUCTURAL_SPANS.
 */
import crypto from 'node:crypto';
import type { OtlpPayload, OtlpAttribute } from './types.ts';
import type { SessionSummary } from './session-state.ts';

export function generateSpanId(): string {
  return crypto.randomBytes(8).toString('hex');
}

/**
 * Deterministic traceId derived from sessionId via MD5, matching AGY's
 * UUID v5-like approach of making trace identity stable per session.
 */
function getTraceIdForSession(sessionId: string | undefined): string {
  if (!sessionId) return crypto.randomBytes(16).toString('hex');
  const NAMESPACE = '6ba7b810-9dad-11d1-80b4-00c04fd430c8';
  return crypto.createHash('md5').update(sessionId + NAMESPACE).digest('hex');
}

function strAttr(key: string, value: string | undefined): OtlpAttribute | null {
  if (value === undefined || value === null) return null;
  return { key, value: { stringValue: value } };
}

function intAttr(key: string, value: number | undefined): OtlpAttribute | null {
  if (value === undefined || value === null) return null;
  return { key, value: { intValue: value } };
}

function dblAttr(key: string, value: number | undefined): OtlpAttribute | null {
  if (value === undefined || value === null) return null;
  return { key, value: { doubleValue: value } };
}

/**
 * Tier-2 top-level attribute cap: a rare-fire backstop bounding the final
 * serialized OTLP string attribute. Fires only AFTER the Tier-1 leaf caps in
 * messages.ts have already trimmed the worst bloat (base64 images, file dumps).
 * Values are tunable.
 */
const OTLP_ATTR_STRING_CAP = 256 * 1024;

/** A jsonAttr result plus, when the serialized form exceeded the Tier-2 cap, a
 * record of the pre-cap length so buildOtlpPayload can stamp span markers. */
interface JsonAttrResult {
  attr: OtlpAttribute | null;
  /** Present only when the Tier-2 backstop truncated the serialized value. */
  truncated?: { originalLength: number };
}

/**
 * Stringify a structured value (messages, skills, tool inventory) into an OTLP
 * string attribute. gen_ai.input.messages & friends MUST be stringified JSON
 * arrays of objects -- .NET / System.Text.Json in the OTel viewer rejects raw
 * objects, and the dashboard's adapters parse them back with json.loads.
 *
 * TIER-2 BACKSTOP: when the serialized `stringValue` exceeds
 * OTLP_ATTR_STRING_CAP, the string is truncated to the cap (no inline marker --
 * the loss is recorded as span-level `observme.truncated` +
 * `observme.original_length` by buildOtlpPayload). CRITICAL: `originalLength`
 * is the serialized length AFTER Tier-1 leaf caps but BEFORE this Tier-2 cap --
 * i.e. the size the attribute WOULD have been.
 */
function jsonAttr(key: string, value: unknown): JsonAttrResult {
  if (value === undefined || value === null) return { attr: null };
  let s: string;
  try {
    s = JSON.stringify(value);
  } catch {
    return { attr: null };
  }
  if (!s) return { attr: null };
  if (s.length <= OTLP_ATTR_STRING_CAP) {
    return { attr: { key, value: { stringValue: s } } };
  }
  // Tier-2 backstop fired: original_length is the post-leaf, pre-cap length.
  return {
    attr: { key, value: { stringValue: s.slice(0, OTLP_ATTR_STRING_CAP) } },
    truncated: { originalLength: s.length },
  };
}

export function buildOtlpPayload(summary: SessionSummary): OtlpPayload {
  const traceId = getTraceIdForSession(summary.sessionId);
  const spanId = generateSpanId();

  const nowMs = Date.now();
  const startNano = (BigInt(nowMs - 500) * 1000000n).toString();
  const endNano = (BigInt(nowMs) * 1000000n).toString();

  const resourceAttributes: OtlpAttribute[] = [
    strAttr('service.name', 'pi'),
    strAttr('service.instance.id', summary.repo),
    strAttr('vcs.repository.name', summary.repo),
  ].filter((a): a is OtlpAttribute => a !== null);

  // Pi's input_tokens is EXCLUSIVE — total = fresh + cache_read + cache_creation + output
  const computedTotal = (summary.freshInput ?? 0) + (summary.cacheRead ?? 0)
    + (summary.cacheCreation ?? 0) + (summary.output ?? 0);

  // Tier-2 truncations observed while building spanAttributes (see jsonAttr).
  // If any fire, the span is stamped with observme.truncated +
  // observme.original_length (the max pre-cap length among truncated attrs) so
  // the loss is visible downstream. jattr is the recording wrapper used in the
  // attribute array below.
  const spanTruncations: number[] = [];
  const jattr = (key: string, value: unknown): OtlpAttribute | null => {
    const r = jsonAttr(key, value);
    if (r.truncated) spanTruncations.push(r.truncated.originalLength);
    return r.attr;
  };

  const spanAttributes: OtlpAttribute[] = [
    // Detection — PiAdapter.detect() returns 1.0 on this attribute
    strAttr('observme.semconv.version', '1.0.0'),
    // Session grouping — PiAdapter groups by this
    strAttr('pi.session.id', summary.sessionId),
    // Session labelling. PiAdapter reads repo/label off the SPAN, so the
    // resource-level vcs.repository.name below never reaches it: without this
    // every session renders as the bare "pi session <8 hex chars>".
    strAttr('pi.cwd.basename', summary.repo),
    // Request grouping — PiAdapter builds its Request list from the distinct
    // run ids seen on child spans, and numbers turns from pi.turn.index.
    strAttr('pi.agent.run.id', summary.runId),
    intAttr('pi.turn.index', summary.turnIndex),
    // Standard OTel GenAI attributes
    strAttr('gen_ai.operation.name', 'chat'),
    strAttr('gen_ai.system', 'pi'),
    strAttr('gen_ai.request.model', summary.model),
    strAttr('gen_ai.response.model', summary.model),
    strAttr('gen_ai.agent.name', 'pi'),
    strAttr('gen_ai.session.id', summary.sessionId),
    // Token attributes (input is EXCLUSIVE of cache classes)
    intAttr('gen_ai.usage.input_tokens', summary.freshInput),
    intAttr('gen_ai.usage.output_tokens', summary.output),
    intAttr('gen_ai.usage.cache_read.input_tokens', summary.cacheRead),
    intAttr('gen_ai.usage.cache_creation.input_tokens', summary.cacheCreation),
    intAttr('gen_ai.usage.reasoning.output_tokens', summary.reasoning),
    // Pi-specific totals
    intAttr('pi.llm.usage.total_tokens', computedTotal),
    // Cost attributes — Pi's own reported cost
    dblAttr('pi.llm.cost.total_usd', summary.turnCostUsd),
    dblAttr('pi.llm.cost.input_usd', summary.costInputUsd),
    dblAttr('pi.llm.cost.output_usd', summary.costOutputUsd),
    dblAttr('pi.llm.cost.cache_read_usd', summary.costCacheReadUsd),
    dblAttr('pi.llm.cost.cache_write_usd', summary.costCacheWriteUsd),
    // ---- request context (system prompt + messages + tools + skills) ----
    // Captured from before_agent_start + context events. The dashboard's
    // PiAdapter reads gen_ai.system_instructions / gen_ai.input.messages /
    // gen_ai.output.messages and breaks them into context-composition buckets
    // (system prompt vs. conversation history vs. file contents from tool
    // results). Skills/tools/context-files ride along as pi.* attributes for
    // the timeline inspector.
    strAttr('gen_ai.system_instructions', summary.systemPrompt),
    jattr('gen_ai.input.messages', summary.inputMessages),
    summary.assistantOutput
      ? jattr('gen_ai.output.messages', [{
          role: 'assistant',
          parts: [{ type: 'text', text: summary.assistantOutput }],
        }])
      : null,
    strAttr('pi.user.prompt', summary.userPrompt),
    jattr('pi.skills', summary.skills),
    jattr('pi.tools.selected', summary.toolNames),
    jattr('pi.tools.snippets', summary.toolSnippets),
    jattr('pi.context_files', summary.contextFiles),
    // Raw provider request payload (before_provider_request). Diagnostic-only
    // wire ground truth vs. the reconstructed gen_ai.input.messages above.
    // Tier-2 cap only (opaque unknown-shape JSON -- no Tier-1 leaf truncation);
    // the shared jattr wrapper applies the ~256 KB backstop + observme.* markers
    // automatically. The adapter keeps this in raw_attributes and never parses
    // it into canonical fields.
    jattr('pi.llm.request.payload', summary.providerRequestPayload),
  ].filter((a): a is OtlpAttribute => a !== null);

  // Surface Tier-2 truncation at the span level. original_length is the largest
  // pre-cap serialized length among the attributes that were truncated.
  if (spanTruncations.length > 0) {
    spanAttributes.push({ key: 'observme.truncated', value: { boolValue: true } });
    spanAttributes.push({
      key: 'observme.original_length',
      value: { intValue: Math.max(...spanTruncations) },
    });
  }

  return {
    resourceSpans: [
      {
        resource: { attributes: resourceAttributes },
        scopeSpans: [
          {
            scope: { name: 'opentelemetry.instrumentation.gen_ai' },
            spans: [
              {
                traceId,
                spanId,
                name: 'pi.llm.request',
                kind: 3,
                startTimeUnixNano: startNano,
                endTimeUnixNano: endNano,
                attributes: spanAttributes,
                status: { code: 1 },
              },
            ],
          },
        ],
      },
    ],
  };
}
