/**
 * Integration Test — full Pi session lifecycle simulation.
 */
import { describe, it, mock } from 'node:test';
import assert from 'node:assert';
import piStatusline from '../extension.ts';

describe('Integration: full session lifecycle', () => {
  it('exports OTLP spans on turn_end and session_shutdown', async () => {
    // Capture registered event handlers
    const handlers = new Map<string, (event: Record<string, unknown>, ctx: Record<string, unknown>) => void | Promise<void>>();
    const mockApi = {
      on: (event: string, handler: (event: Record<string, unknown>, ctx: Record<string, unknown>) => void | Promise<void>) => {
        handlers.set(event, handler);
      },
    };

    piStatusline(mockApi as any);

    // Mock fetch to capture OTLP payloads
    const fetchCalls: Array<{ url: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      fetchCalls.push({ url: String(url), body: String(init?.body ?? '') });
      return { ok: true, status: 200 } as Response;
    }) as typeof fetch;

    // Also suppress stderr output from renderToUi
    const originalWrite = process.stderr.write;
    process.stderr.write = (() => true) as typeof process.stderr.write;

    try {
      // Session identity comes from the CONTEXT, not from the event -- pi's
      // session_start payload is only {type, reason, previousSessionFile}.
      const mockCtx = {
        ui: { setStatus: mock.fn() },
        mode: 'print',
        cwd: '/home/user/my-project',
        model: { id: 'claude-sonnet-4-20250514' },
        sessionManager: {
          getSessionId: () => 'test-session-001',
          getCwd: () => '/home/user/my-project',
          getSessionName: () => 'my-project',
        },
      };

      const sessionStart = handlers.get('session_start')!;
      const agentStart = handlers.get('agent_start')!;
      const messageEnd = handlers.get('message_end')!;
      const turnEnd = handlers.get('turn_end')!;
      const sessionShutdown = handlers.get('session_shutdown')!;

      assert.ok(sessionStart, 'session_start handler registered');
      assert.ok(agentStart, 'agent_start handler registered');
      assert.ok(messageEnd, 'message_end handler registered');
      assert.ok(turnEnd, 'turn_end handler registered');
      assert.ok(sessionShutdown, 'session_shutdown handler registered');

      const assistant = (u: Record<string, number>, cost: Record<string, number>) => ({
        message: {
          role: 'assistant',
          model: 'claude-sonnet-4-20250514',
          usage: { ...u, cost },
        },
      });

      await sessionStart({ reason: 'startup' }, mockCtx);
      await agentStart({}, mockCtx);

      // Turn 1 — two LLM calls (a tool round-trip), which must SUM.
      await messageEnd(assistant(
        { input: 3000, output: 700, cacheRead: 2000, cacheWrite: 300, totalTokens: 6000 },
        { input: 0.009, output: 0.021, cacheRead: 0.0006, cacheWrite: 0.0002, total: 0.0308 },
      ), mockCtx);
      await messageEnd(assistant(
        { input: 2000, output: 500, cacheRead: 1000, cacheWrite: 200, totalTokens: 3700 },
        { input: 0.006, output: 0.015, cacheRead: 0.0004, cacheWrite: 0.0001, total: 0.0215 },
      ), mockCtx);
      await turnEnd({ turnIndex: 0, message: { role: 'assistant' } }, mockCtx);

      // Turn 2 — a single call.
      await messageEnd(assistant(
        { input: 6000, output: 800, cacheRead: 0, cacheWrite: 0, totalTokens: 6800 },
        { input: 0.018, output: 0.024, cacheRead: 0, cacheWrite: 0, total: 0.042 },
      ), mockCtx);
      await turnEnd({ turnIndex: 1, message: { role: 'assistant' } }, mockCtx);

      // Shutdown with no in-flight turn must NOT re-export the last turn.
      await sessionShutdown({}, mockCtx);

      // Wait for fire-and-forget promises to settle
      await new Promise(resolve => setTimeout(resolve, 50));

      // Exactly 2 exports: one per turn. The shutdown adds none, because both
      // turns already closed and drained -- a third would be a double-count.
      assert.strictEqual(fetchCalls.length, 2,
        `Expected exactly 2 fetch calls, got ${fetchCalls.length}`);

      // Verify payload structure of the first export
      const firstPayload = JSON.parse(fetchCalls[0].body);
      const span = firstPayload.resourceSpans[0].scopeSpans[0].spans[0];
      const resourceAttrs = firstPayload.resourceSpans[0].resource.attributes;

      // service.name = pi
      const serviceName = resourceAttrs.find((a: Record<string, unknown>) => a.key === 'service.name') as Record<string, Record<string, unknown>> | undefined;
      assert.strictEqual(serviceName?.value?.stringValue, 'pi');

      // Span name
      assert.strictEqual(span.name, 'pi.llm.request');

      // observme.semconv.version present (PiAdapter detection)
      const semconvAttr = span.attributes.find((a: Record<string, unknown>) => a.key === 'observme.semconv.version');
      assert.ok(semconvAttr, 'observme.semconv.version must be present');

      // pi.session.id matches
      const sessionAttr = span.attributes.find((a: Record<string, unknown>) => a.key === 'pi.session.id') as Record<string, Record<string, unknown>> | undefined;
      assert.strictEqual(sessionAttr?.value?.stringValue, 'test-session-001');

      const attr = (key: string) => span.attributes
        .find((a: Record<string, unknown>) => a.key === key)?.value;

      // Turn 1's two calls SUM: 3000 + 2000 fresh input, not just the last.
      assert.strictEqual(attr('gen_ai.usage.input_tokens')?.intValue, 5000);
      assert.strictEqual(attr('gen_ai.usage.output_tokens')?.intValue, 1200);
      assert.strictEqual(attr('gen_ai.usage.cache_read.input_tokens')?.intValue, 3000);
      assert.strictEqual(attr('gen_ai.usage.cache_creation.input_tokens')?.intValue, 500);

      // The identity PiAdapter.validate() enforces:
      //   input + cache_read + cache_creation + output == total_tokens
      assert.strictEqual(attr('pi.llm.usage.total_tokens')?.intValue, 9700);

      // Run id present, so the dashboard can group turns into requests.
      assert.ok(attr('pi.agent.run.id')?.stringValue, 'pi.agent.run.id must be present');

      // Cost parts must sum to the reported total (cost_decomposition_mismatch).
      const total = attr('pi.llm.cost.total_usd')?.doubleValue;
      const parts = ['input', 'output', 'cache_read', 'cache_write']
        .reduce((sum, k) => sum + (attr(`pi.llm.cost.${k}_usd`)?.doubleValue ?? 0), 0);
      assert.ok(Math.abs(parts - total) < 1e-9,
        `cost parts ${parts} must sum to total ${total}`);

      // Turn 2 exported its own numbers, not a running session total.
      const span2 = JSON.parse(fetchCalls[1].body).resourceSpans[0].scopeSpans[0].spans[0];
      const attr2 = (key: string) => span2.attributes
        .find((a: Record<string, unknown>) => a.key === key)?.value;
      assert.strictEqual(attr2('gen_ai.usage.input_tokens')?.intValue, 6000);
      assert.strictEqual(attr2('gen_ai.usage.output_tokens')?.intValue, 800);
    } finally {
      globalThis.fetch = originalFetch;
      process.stderr.write = originalWrite;
    }
  });
});
