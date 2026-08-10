/**
 * Tier-1 leaf truncation tests (messages.ts).
 *
 * Pins the per-leaf caps that bound oversized content BEFORE it is serialized
 * into an OTLP attribute: base64 image `data` is capped hard (~256 chars);
 * other large raw leaves (thinking, tool_call, other, tool_result) are capped
 * at ~16 KB. Each truncation appends an in-band `…[truncated, N chars]` marker
 * carrying the ORIGINAL length; under-cap content is byte-identical.
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { toCanonicalMessages, IMAGE_DATA_CAP, LEAF_TEXT_CAP } from "../messages.ts";

describe("toCanonicalMessages — Tier-1 leaf caps", () => {
  it("truncates oversized base64 image `data` with an original-length marker, preserving part shape", () => {
    const original = "x".repeat(IMAGE_DATA_CAP + 500);
    const out = toCanonicalMessages([
      {
        role: "user",
        content: [
          { type: "image", data: original, mimeType: "image/png" },
        ],
      },
    ]);
    const part = out[0].parts[0];
    assert.equal(part.type, "image");
    // Shape preserved: raw is still an object carrying mimeType + truncated data.
    const raw = part.raw as { type: string; data: string; mimeType: string };
    assert.equal(raw.type, "image");
    assert.equal(raw.mimeType, "image/png");
    // data is the cap-length prefix + the marker carrying the ORIGINAL length.
    assert.ok(raw.data.endsWith(`…[truncated, ${original.length} chars]`),
      `expected marker with original length ${original.length}, got: ${raw.data.slice(-60)}`);
    assert.ok(raw.data.length < original.length, "image data must be truncated");
    assert.ok(raw.data.startsWith("x".repeat(IMAGE_DATA_CAP)),
      "truncated data must begin with the first `cap` chars of the original");
  });

  it("leaves under-cap image data byte-identical", () => {
    const small = "x".repeat(IMAGE_DATA_CAP - 10);
    const out = toCanonicalMessages([
      { role: "user", content: [{ type: "image", data: small, mimeType: "image/png" }] },
    ]);
    const raw = out[0].parts[0].raw as { data: string };
    assert.equal(raw.data, small, "under-cap image data is unchanged");
  });

  it("truncates an oversized thinking raw with the marker", () => {
    // A thinking part whose stringified form blows past LEAF_TEXT_CAP.
    const big = { type: "thinking", thinking: "y".repeat(LEAF_TEXT_CAP + 2000) };
    const out = toCanonicalMessages([{ role: "assistant", content: [big] }]);
    const part = out[0].parts[0];
    assert.equal(part.type, "thinking");
    // Over-cap raw degrades to a truncated stringified form + marker carrying
    // the ORIGINAL (pre-cap) stringified length.
    assert.equal(typeof part.raw, "string");
    const raw = part.raw as string;
    const expectedOriginal = JSON.stringify(big);
    assert.ok(raw.endsWith(`…[truncated, ${expectedOriginal.length} chars]`),
      `expected marker with original length ${expectedOriginal.length}`);
    assert.ok(raw.length < expectedOriginal.length, "thinking raw must be truncated");
  });

  it("truncates an oversized tool_result body with the marker", () => {
    const hugeBody = "z".repeat(LEAF_TEXT_CAP + 5000);
    const out = toCanonicalMessages([
      {
        role: "toolResult",
        toolCallId: "c1",
        toolName: "read",
        isError: false,
        content: [{ type: "text", text: hugeBody }],
      },
    ]);
    const part = out[0].parts[0];
    assert.equal(part.type, "tool_result");
    assert.equal(typeof part.raw, "string", "over-cap tool_result raw becomes a bounded string");
    const raw = part.raw as string;
    assert.ok(raw.includes("…[truncated,"), "tool_result raw carries the truncation marker");
  });

  it("leaves under-cap content byte-identical across all leaf types", () => {
    const out = toCanonicalMessages([
      {
        role: "assistant",
        content: [
          { type: "thinking", thinking: "small thought" },
          { type: "toolCall", id: "c1", name: "read", arguments: { path: "/x" } },
          { type: "somethingNew", foo: "bar" },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "c1",
        toolName: "read",
        isError: false,
        content: [{ type: "text", text: "small body" }],
      },
    ]);
    // thinking raw unchanged (same object reference as input content part).
    assert.deepEqual(out[0].parts[0].raw, { type: "thinking", thinking: "small thought" });
    // tool_call raw unchanged.
    assert.deepEqual(out[0].parts[1].raw, { type: "toolCall", id: "c1", name: "read", arguments: { path: "/x" } });
    // other raw unchanged.
    assert.deepEqual(out[0].parts[2].raw, { type: "somethingNew", foo: "bar" });
    // tool_result raw is an object (not a truncated string) carrying toolName.
    const trRaw = out[1].parts[0].raw as { toolName: string; isError: boolean };
    assert.equal(trRaw.toolName, "read");
    assert.equal(trRaw.isError, false);
  });
});
