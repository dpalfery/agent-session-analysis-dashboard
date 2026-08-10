/**
 * Tier-2 attribute cap tests (otlp.ts::jsonAttr via buildOtlpPayload).
 *
 * The ~256 KB top-level cap is a rare-fire backstop that bounds the final
 * serialized OTLP string attribute AFTER Tier-1 leaf caps have trimmed the
 * worst bloat. When it fires, the value is truncated to the cap (no inline
 * marker) and the SPAN is stamped with:
 *   - observme.truncated = true (bool)
 *   - observme.original_length = <int> (the serialized length AFTER Tier-1 leaf
 *     caps but BEFORE the Tier-2 cap -- i.e. the size the attribute WOULD have
 *     been). Under-cap attributes are unchanged and carry NO observme.* markers.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { buildOtlpPayload } from '../otlp.ts';
import type { SessionSummary } from '../session-state.ts';
import type { OtlpAttribute } from '../types.ts';

/** Tier-2 cap constant mirrored from otlp.ts (kept private there). Re-stated
 *  here so the test builds an over-cap payload without importing the value. */
const OTLP_ATTR_STRING_CAP = 256 * 1024;

const baseSummary: SessionSummary = {
  sessionId: 'test-session',
  model: 'claude-sonnet',
  repo: 'my-repo',
  branch: 'main',
  turnCount: 1,
  freshInput: 100,
  output: 10,
  cacheRead: 0,
  cacheCreation: 0,
  reasoning: 0,
  totalInputTokens: 100,
  totalOutputTokens: 10,
  turnCostUsd: 0,
  totalCostUsd: 0,
};

function spanAttrs(summary: SessionSummary): OtlpAttribute[] {
  return buildOtlpPayload(summary).resourceSpans[0].scopeSpans[0].spans[0].attributes;
}

function findAttr(attrs: readonly OtlpAttribute[], key: string): OtlpAttribute | undefined {
  return attrs.find((a) => a.key === key);
}

describe('buildOtlpPayload — Tier-2 attribute cap (jsonAttr)', () => {
  it('truncates an over-cap attribute and stamps observme.truncated + observme.original_length', () => {
    // A single text part large enough that its serialized gen_ai.input.messages
    // exceeds the 256 KB Tier-2 cap.
    const hugeText = 'A'.repeat(OTLP_ATTR_STRING_CAP + 5000);
    const summary: SessionSummary = {
      ...baseSummary,
      inputMessages: [{ role: 'user', parts: [{ type: 'text', text: hugeText }] }],
    };

    const attrs = spanAttrs(summary);

    // Pre-cap length = serialized size of gen_ai.input.messages AFTER Tier-1
    // leaf caps (Tier-1 does not touch plain text parts) and BEFORE Tier-2.
    const preCapLength = JSON.stringify(summary.inputMessages).length;
    assert.ok(preCapLength > OTLP_ATTR_STRING_CAP, 'fixture must exceed the cap');

    const attr = findAttr(attrs, 'gen_ai.input.messages');
    assert.ok(attr?.value?.stringValue, 'gen_ai.input.messages must be present');
    assert.equal(
      attr!.value!.stringValue!.length, OTLP_ATTR_STRING_CAP,
      'over-cap attribute must be truncated to exactly the cap',
    );

    // Span-level markers.
    const truncated = findAttr(attrs, 'observme.truncated');
    assert.ok(truncated, 'observme.truncated must be stamped');
    assert.strictEqual(truncated!.value?.boolValue, true);

    const originalLength = findAttr(attrs, 'observme.original_length');
    assert.ok(originalLength, 'observme.original_length must be stamped');
    assert.strictEqual(
      originalLength!.value?.intValue, preCapLength,
      'original_length must equal the pre-cap (post-leaf) serialized length',
    );
  });

  it('leaves under-cap attributes unchanged and stamps NO observme.* truncation markers', () => {
    const summary: SessionSummary = {
      ...baseSummary,
      inputMessages: [{ role: 'user', parts: [{ type: 'text', text: 'hello pi' }] }],
    };

    const attrs = spanAttrs(summary);

    const attr = findAttr(attrs, 'gen_ai.input.messages');
    assert.ok(attr?.value?.stringValue);
    // Under-cap: emitted verbatim, parseable back to the original.
    assert.deepEqual(JSON.parse(attr!.value!.stringValue!), [
      { role: 'user', parts: [{ type: 'text', text: 'hello pi' }] },
    ]);

    // No Tier-2 truncation markers. (observme.semconv.version is unrelated.)
    assert.equal(findAttr(attrs, 'observme.truncated'), undefined,
      'under-cap span must NOT carry observme.truncated');
    assert.equal(findAttr(attrs, 'observme.original_length'), undefined,
      'under-cap span must NOT carry observme.original_length');
  });
});
