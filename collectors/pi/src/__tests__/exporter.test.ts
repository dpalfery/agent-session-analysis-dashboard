/**
 * Exporter observability tests (exporter.ts).
 *
 * The collector must NEVER crash pi: a broken/misconfigured Aspire endpoint is
 * surfaced via a warn-level `[pi-statusline]` log, never a re-throw, and the
 * endpoint fallback loop is preserved. These tests capture console.error to
 * assert both the non-OK HTTP path and the thrown-error path log without
 * throwing.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { exportSpan, OTLP_ENDPOINTS } from '../exporter.ts';

/** Capture every console.error argument (concatenated) while `fn` runs. */
async function captureErrorLog(fn: () => Promise<void>): Promise<string[]> {
  const lines: string[] = [];
  const original = console.error;
  console.error = (...args: unknown[]) => {
    lines.push(args.map((a) => String(a)).join(' '));
  };
  try {
    await fn();
  } finally {
    console.error = original;
  }
  return lines;
}

describe('exportSpan — observability (never re-throw)', () => {
  it('logs a warn on non-OK HTTP status, does not throw, and keeps the fallback loop', async () => {
    const calls: string[] = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL | Request) => {
      calls.push(String(url));
      return { ok: false, status: 503 } as Response;
    }) as typeof fetch;

    try {
      const lines = await captureErrorLog(() => exportSpan({ resourceSpans: [] }));
      // Did not throw.
      // Fallback loop preserved: every endpoint was tried.
      assert.equal(calls.length, OTLP_ENDPOINTS.length,
        `expected fallback to try all ${OTLP_ENDPOINTS.length} endpoints, got ${calls.length}`);
      for (const ep of OTLP_ENDPOINTS) {
        assert.ok(calls.includes(ep), `endpoint ${ep} must be attempted`);
      }
      // A warn-level line was emitted for at least one non-OK response.
      const matched = lines.filter((l) => /OTLP export to .* failed: HTTP 503/.test(l));
      assert.ok(matched.length > 0,
        `expected a non-OK HTTP warn log, got: ${JSON.stringify(lines)}`);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('logs a warn when fetch throws, does not throw, and keeps the fallback loop', async () => {
    const calls: string[] = [];
    const originalFetch = globalThis.fetch;
    const boom = new TypeError('connect ECONNREFUSED');
    globalThis.fetch = (async (url: string | URL | Request) => {
      calls.push(String(url));
      throw boom;
    }) as typeof fetch;

    try {
      const lines = await captureErrorLog(() => exportSpan({ resourceSpans: [] }));
      // Fallback loop preserved even when every fetch throws.
      assert.equal(calls.length, OTLP_ENDPOINTS.length,
        `expected fallback to try all ${OTLP_ENDPOINTS.length} endpoints, got ${calls.length}`);
      // Warn log carries the error type + message.
      const matched = lines.filter((l) => /OTLP export error: TypeError: connect ECONNREFUSED/.test(l));
      assert.ok(matched.length > 0,
        `expected a thrown-error warn log, got: ${JSON.stringify(lines)}`);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
