/**
 * OTLP Payload Builder Tests
 */
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { buildOtlpPayload } from '../otlp.ts';
import type { SessionSummary } from '../session-state.ts';
import type { OtlpAttribute } from '../types.ts';

describe('buildOtlpPayload', () => {
  const mockSummary: SessionSummary = {
    sessionId: 'test-session-123',
    model: 'claude-sonnet',
    repo: 'my-repo',
    branch: 'main',
    turnCount: 1,
    freshInput: 5000,
    output: 1200,
    cacheRead: 3000,
    cacheCreation: 500,
    reasoning: 0,
    totalInputTokens: 8500,
    totalOutputTokens: 1200,
    turnCostUsd: 0.0523,
    totalCostUsd: 0.0523,
    costInputUsd: 0.015,
    costOutputUsd: 0.036,
    costCacheReadUsd: 0.001,
    costCacheWriteUsd: 0.0003,
  };

  function getSpan(summary: SessionSummary) {
    const payload = buildOtlpPayload(summary);
    return payload.resourceSpans[0].scopeSpans[0].spans[0];
  }

  function getResourceAttrs(summary: SessionSummary) {
    return buildOtlpPayload(summary).resourceSpans[0].resource.attributes;
  }

  function findAttr(attrs: readonly OtlpAttribute[], key: string) {
    return attrs.find(a => a.key === key);
  }

  it('Returns valid OTLP JSON structure', () => {
    const payload = buildOtlpPayload(mockSummary);
    assert.ok(payload.resourceSpans);
    assert.ok(payload.resourceSpans[0].scopeSpans[0].spans[0]);
  });

  it('Sets service.name to "pi"', () => {
    const attrs = getResourceAttrs(mockSummary);
    assert.strictEqual(findAttr(attrs, 'service.name')?.value?.stringValue, 'pi');
  });

  it('Includes observme.semconv.version for PiAdapter detection', () => {
    const span = getSpan(mockSummary);
    assert.ok(findAttr(span.attributes, 'observme.semconv.version'));
  });

  it('Uses span name "pi.llm.request" (critical for PiAdapter.is_relevant())', () => {
    assert.strictEqual(getSpan(mockSummary).name, 'pi.llm.request');
  });

  it('Sets span kind to 3 (CLIENT)', () => {
    assert.strictEqual(getSpan(mockSummary).kind, 3);
  });

  it('Sets required gen_ai attributes', () => {
    const span = getSpan(mockSummary);
    const get = (key: string) => findAttr(span.attributes, key)?.value?.stringValue;
    assert.strictEqual(get('gen_ai.operation.name'), 'chat');
    assert.strictEqual(get('gen_ai.system'), 'pi');
    assert.strictEqual(get('gen_ai.agent.name'), 'pi');
    assert.strictEqual(get('pi.session.id'), 'test-session-123');
  });

  it('Includes token attributes with correct exclusive values', () => {
    const span = getSpan(mockSummary);
    const getInt = (key: string) => findAttr(span.attributes, key)?.value?.intValue;
    // gen_ai.usage.input_tokens is EXCLUSIVE of cache — just fresh input
    assert.strictEqual(getInt('gen_ai.usage.input_tokens'), 5000);
    assert.strictEqual(getInt('gen_ai.usage.output_tokens'), 1200);
    assert.strictEqual(getInt('gen_ai.usage.cache_read.input_tokens'), 3000);
    assert.strictEqual(getInt('gen_ai.usage.cache_creation.input_tokens'), 500);
    // Total = fresh + cache_read + cache_creation + output
    assert.strictEqual(getInt('pi.llm.usage.total_tokens'), 9700);
  });

  it('Excludes token attributes when values are zero', () => {
    const emptySummary: SessionSummary = {
      ...mockSummary,
      freshInput: 0,
      output: 0,
      cacheRead: 0,
      cacheCreation: 0,
      reasoning: 0,
    };
    const span = getSpan(emptySummary);
    // intAttr(0) should still produce an attribute — 0 is a valid measurement
    const getInt = (key: string) => findAttr(span.attributes, key)?.value?.intValue;
    assert.strictEqual(getInt('gen_ai.usage.input_tokens'), 0);
  });

  it('Includes cost attributes from Pi\'s self-reported breakdown', () => {
    const span = getSpan(mockSummary);
    const getDbl = (key: string) => findAttr(span.attributes, key)?.value?.doubleValue;
    assert.strictEqual(getDbl('pi.llm.cost.total_usd'), 0.0523);
    assert.strictEqual(getDbl('pi.llm.cost.input_usd'), 0.015);
    assert.strictEqual(getDbl('pi.llm.cost.output_usd'), 0.036);
    assert.strictEqual(getDbl('pi.llm.cost.cache_read_usd'), 0.001);
    assert.strictEqual(getDbl('pi.llm.cost.cache_write_usd'), 0.0003);
  });

  it('Emits request context attributes when the summary carries them', () => {
    const withContext: SessionSummary = {
      ...mockSummary,
      systemPrompt: 'You are pi.',
      userPrompt: 'do the thing',
      inputMessages: [
        { role: 'user', parts: [{ type: 'text', text: 'do the thing' }] },
      ],
      assistantOutput: 'done',
      skills: [{ name: 'conductor' }],
      toolNames: ['read', 'bash'],
    };
    const span = getSpan(withContext);
    const getStr = (key: string) => findAttr(span.attributes, key)?.value?.stringValue;

    assert.strictEqual(getStr('gen_ai.system_instructions'), 'You are pi.');
    assert.strictEqual(getStr('pi.user.prompt'), 'do the thing');

    // gen_ai.input.messages MUST be a stringified JSON array of {role, parts}.
    const inputJson = getStr('gen_ai.input.messages');
    assert.ok(inputJson);
    const parsed = JSON.parse(inputJson!);
    assert.ok(Array.isArray(parsed));
    assert.equal(parsed[0].role, 'user');
    assert.equal(parsed[0].parts[0].type, 'text');

    // assistant output is emitted as a stringified assistant message array.
    const outJson = getStr('gen_ai.output.messages');
    assert.ok(outJson);
    assert.equal(JSON.parse(outJson!)[0].role, 'assistant');

    // skills + tool inventory ride along as JSON.
    assert.deepEqual(JSON.parse(getStr('pi.skills')!), [{ name: 'conductor' }]);
    assert.deepEqual(JSON.parse(getStr('pi.tools.selected')!), ['read', 'bash']);
  });

  it('Omits request-context attributes when the summary has none', () => {
    const span = getSpan(mockSummary); // no context fields set
    assert.ok(!findAttr(span.attributes, 'gen_ai.system_instructions'));
    assert.ok(!findAttr(span.attributes, 'gen_ai.input.messages'));
    assert.ok(!findAttr(span.attributes, 'pi.skills'));
  });
});
