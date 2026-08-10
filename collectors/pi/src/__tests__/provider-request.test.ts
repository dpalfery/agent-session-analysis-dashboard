/**
 * before_provider_request raw-payload capture tests (Follow-up B).
 *
 * End-to-end through the extension: the `before_provider_request` event feeds
 * SessionState.setProviderRequestPayload, which flows onto the SessionSummary
 * and is emitted by buildOtlpPayload as `pi.llm.request.payload` (stringified
 * JSON) via the SAME capped jsonAttr as every other content attribute -- so the
 * ~256 KB Tier-2 backstop + observme.* markers apply automatically. Tier-2 only:
 * no Tier-1 leaf truncation (opaque unknown-shape JSON).
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import piStatusline from "../extension.ts";

/** Tier-2 cap constant mirrored from otlp.ts (kept private there). */
const OTLP_ATTR_STRING_CAP = 256 * 1024;

type Handler = (event: Record<string, unknown>, ctx: Record<string, unknown>) => void | Promise<void>;

async function runTurn(opts: {
  payload: unknown;
  onPayloadAttr?: (attrValue: unknown, spanAttrs: Array<Record<string, unknown>>) => void;
}): Promise<void> {
  const handlers = new Map<string, Handler>();
  const mockApi = {
    on: (event: string, handler: Handler) => handlers.set(event, handler),
  };
  piStatusline(mockApi as any);

  const beforeProvider = handlers.get("before_provider_request");
  assert.ok(beforeProvider, "before_provider_request handler must be registered");

  // Capture the exported OTLP payload.
  const fetchCalls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
    fetchCalls.push(String(init?.body ?? ""));
    return { ok: true, status: 200 } as Response;
  }) as typeof fetch;

  // Suppress status-bar stderr output.
  const originalWrite = process.stderr.write;
  process.stderr.write = (() => true) as typeof process.stderr.write;

  try {
    const mockCtx = {
      ui: { setStatus: () => {} },
      mode: "print",
      cwd: "/home/user/my-project",
      model: { id: "claude-sonnet-4-20250514" },
      sessionManager: {
        getSessionId: () => "test-session-001",
        getCwd: () => "/home/user/my-project",
        getSessionName: () => "my-project",
      },
    };

    await handlers.get("session_start")!({ reason: "startup" }, mockCtx);
    await handlers.get("agent_start")!({}, mockCtx);
    await beforeProvider!({ payload: opts.payload }, mockCtx);
    // message_end with usage so the turn has activity (otherwise turn_end
    // exports nothing).
    await handlers.get("message_end")!({
      message: {
        role: "assistant",
        model: "claude-sonnet-4-20250514",
        usage: {
          input: 100, output: 10, cacheRead: 0, cacheWrite: 0,
          totalTokens: 110,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
        },
      },
    }, mockCtx);
    await handlers.get("turn_end")!({ turnIndex: 0, message: { role: "assistant" } }, mockCtx);

    // Let the fire-and-forget exportSpan settle.
    await new Promise((r) => setTimeout(r, 50));

    assert.equal(fetchCalls.length, 1, `expected exactly 1 export, got ${fetchCalls.length}`);
    const span = JSON.parse(fetchCalls[0]).resourceSpans[0].scopeSpans[0].spans[0];
    const attrs: Array<Record<string, unknown>> = span.attributes;
    const attr = (key: string) => attrs.find((a) => a.key === key)?.value;

    opts.onPayloadAttr?.(attr("pi.llm.request.payload"), attrs);
  } finally {
    globalThis.fetch = originalFetch;
    process.stderr.write = originalWrite;
  }
}

describe("before_provider_request -> pi.llm.request.payload", () => {
  it("registers a before_provider_request handler", () => {
    const handlers = new Map<string, Handler>();
    piStatusline({ on: (e: string, h: Handler) => handlers.set(e, h) } as any);
    assert.ok(handlers.has("before_provider_request"),
      "extension must register a before_provider_request handler");
  });

  it("emits the payload as stringified JSON under pi.llm.request.payload", async () => {
    const payload = { model: "claude-sonnet", messages: [{ role: "user", content: "hi" }] };
    await runTurn({
      payload,
      onPayloadAttr: (value) => {
        assert.ok(value && typeof value === "object" && "stringValue" in (value as object),
          "pi.llm.request.payload must be a string attribute");
        const sv = (value as { stringValue: string }).stringValue;
        assert.deepEqual(JSON.parse(sv), payload,
          "pi.llm.request.payload must round-trip to the original payload");
      },
    });
  });

  it("applies the ~256 KB Tier-2 cap + observme.* markers when the payload is oversized", async () => {
    const huge = "Q".repeat(OTLP_ATTR_STRING_CAP + 5000); // serialized form exceeds the cap
    await runTurn({
      payload: huge,
      onPayloadAttr: (value, attrs) => {
        const sv = (value as { stringValue: string }).stringValue;
        assert.equal(sv.length, OTLP_ATTR_STRING_CAP,
          "oversized payload must be truncated to exactly the Tier-2 cap");

        const truncated = attrs.find((a) => a.key === "observme.truncated")?.value as { boolValue?: boolean } | undefined;
        assert.strictEqual(truncated?.boolValue, true,
          "oversized payload span must carry observme.truncated=true");

        const originalLength = attrs.find((a) => a.key === "observme.original_length")?.value as { intValue?: number } | undefined;
        // Pre-cap length = serialized size AFTER Tier-1 (none for this opaque
        // blob) and BEFORE Tier-2: JSON.stringify(huge).length.
        assert.strictEqual(originalLength?.intValue, JSON.stringify(huge).length,
          "observme.original_length must equal the pre-cap serialized length");
      },
    });
  });

  it("drains the payload at endTurn so the next turn reports its own payload", async () => {
    // After runTurn, a fresh turn (with no before_provider_request fire) must
    // NOT carry the previous turn's payload.
    const handlers = new Map<string, Handler>();
    piStatusline({ on: (e: string, h: Handler) => handlers.set(e, h) } as any);

    const fetchCalls: string[] = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
      fetchCalls.push(String(init?.body ?? ""));
      return { ok: true, status: 200 } as Response;
    }) as typeof fetch;
    const originalWrite = process.stderr.write;
    process.stderr.write = (() => true) as typeof process.stderr.write;

    try {
      const mockCtx = {
        ui: { setStatus: () => {} },
        mode: "print", cwd: "/x", model: { id: "m" },
        sessionManager: { getSessionId: () => "s", getCwd: () => "/x", getSessionName: () => "x" },
      };
      await handlers.get("session_start")!({ reason: "startup" }, mockCtx);
      await handlers.get("agent_start")!({}, mockCtx);
      // Turn 1 fires the payload event.
      await handlers.get("before_provider_request")!({ payload: { first: true } }, mockCtx);
      await handlers.get("message_end")!({
        message: { role: "assistant", usage: { input: 1, output: 1, totalTokens: 2, cost: { total: 0 } } },
      }, mockCtx);
      await handlers.get("turn_end")!({ turnIndex: 0, message: { role: "assistant" } }, mockCtx);

      // Turn 2 makes an LLM call but fires NO before_provider_request.
      await handlers.get("message_end")!({
        message: { role: "assistant", usage: { input: 2, output: 2, totalTokens: 4, cost: { total: 0 } } },
      }, mockCtx);
      await handlers.get("turn_end")!({ turnIndex: 1, message: { role: "assistant" } }, mockCtx);

      await new Promise((r) => setTimeout(r, 50));
      assert.equal(fetchCalls.length, 2);

      const span2 = JSON.parse(fetchCalls[1]).resourceSpans[0].scopeSpans[0].spans[0];
      const hasPayload = span2.attributes.some((a: Record<string, unknown>) => a.key === "pi.llm.request.payload");
      assert.equal(hasPayload, false,
        "turn 2 must not carry turn 1's payload -- endTurn drains it");
    } finally {
      globalThis.fetch = originalFetch;
      process.stderr.write = originalWrite;
    }
  });
});
