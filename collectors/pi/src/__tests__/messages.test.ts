/**
 * pi-ai -> canonical message conversion tests.
 *
 * The output shape is a CONTRACT consumed by PiAdapter._normalize_messages()
 * in agentdash/adapters/pi.py: [{role, parts:[{type, ...}]}]. These tests pin
 * the mapping for each role pi emits so the dashboard's context-composition
 * buckets (text / reasoning / tool_call / tool_result) fire correctly.
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { toCanonicalMessages } from "../messages.ts";

describe("toCanonicalMessages", () => {
  it("returns [] for non-array input", () => {
    assert.deepEqual(toCanonicalMessages(undefined), []);
    assert.deepEqual(toCanonicalMessages({ not: "an array" }), []);
  });

  it("converts a plain-string user message to one text part", () => {
    const out = toCanonicalMessages([
      { role: "user", content: "hello pi" },
    ]);
    assert.equal(out.length, 1);
    assert.equal(out[0].role, "user");
    assert.equal(out[0].parts.length, 1);
    assert.equal(out[0].parts[0].type, "text");
    assert.equal(out[0].parts[0].text, "hello pi");
  });

  it("splits an assistant message into text / thinking / tool_call parts", () => {
    const out = toCanonicalMessages([
      {
        role: "assistant",
        content: [
          { type: "thinking", thinking: "reasoning here" },
          { type: "text", text: "the answer" },
          { type: "toolCall", id: "c1", name: "read", arguments: { path: "/x" } },
        ],
      },
    ]);
    const parts = out[0].parts;
    assert.equal(parts.length, 3);
    assert.equal(parts[0].type, "thinking");
    assert.equal(parts[1].type, "text");
    assert.equal(parts[1].text, "the answer");
    assert.equal(parts[2].type, "tool_call");
    assert.deepEqual((parts[2].raw as { name: string }).name, "read");
  });

  it("surfaces a toolResult message as one tool_result part (buckets on type, not role)", () => {
    const out = toCanonicalMessages([
      {
        role: "toolResult",
        toolCallId: "c1",
        toolName: "read",
        isError: false,
        content: [{ type: "text", text: "file body" }],
      },
    ]);
    assert.equal(out.length, 1);
    assert.equal(out[0].role, "toolResult");
    assert.equal(out[0].parts.length, 1);
    assert.equal(out[0].parts[0].type, "tool_result");
    const raw = out[0].parts[0].raw as { toolName: string; isError: boolean };
    assert.equal(raw.toolName, "read");
    assert.equal(raw.isError, false);
  });

  it("maps image and unknown part types safely", () => {
    const out = toCanonicalMessages([
      {
        role: "user",
        content: [
          { type: "image", data: "base64", mimeType: "image/png" },
          { type: "somethingNew", foo: "bar" },
        ],
      },
    ]);
    const parts = out[0].parts;
    assert.equal(parts[0].type, "image");
    assert.equal(parts[1].type, "other");
  });

  it("preserves order across a realistic turn (user, assistant, toolResult, user)", () => {
    const out = toCanonicalMessages([
      { role: "user", content: "do the thing" },
      { role: "assistant", content: [{ type: "text", text: "ok" }] },
      { role: "toolResult", toolCallId: "t1", toolName: "bash", content: [{ type: "text", text: "done" }], isError: false },
      { role: "user", content: "thanks" },
    ]);
    assert.deepEqual(out.map((m) => m.role), ["user", "assistant", "toolResult", "user"]);
  });
});
