/**
 * pi-ai Message -> canonical {role, parts:[{type, ...}]} conversion.
 *
 * The dashboard's PiAdapter._normalize_messages() (agentdash/adapters/pi.py)
 * consumes EXACTLY this shape: a list of {role, parts}, where each part carries
 * a `type` it recognizes (text | thinking | reasoning | tool_call | tool_use |
 * tool_result | ...). Anything unrecognized falls through to {type:"other",
 * raw}. This module produces that shape from pi-ai's native messages
 * (UserMessage | AssistantMessage | ToolResultMessage) so the full conversation
 * pi sends to the LLM is reconstructable in the trace -- user turns, prior
 * assistant turns (text + thinking + tool calls), and tool results.
 *
 * This is what lets the dashboard break down context composition (system prompt
 * vs. conversation history vs. file contents from tool results) for pi, the
 * same way it does for Copilot. Without it the pi collector exports only token
 * counts and the context view has nothing to bucket.
 */

export interface CanonicalPart {
  readonly type: string;
  readonly text?: string;
  readonly raw?: unknown;
}

// ---- Tier-1 leaf caps --------------------------------------------------
// Bound oversized content BEFORE it is serialized into an OTLP attribute.
// Per-leaf caps are the primary defense against multi-MB bloat (base64 image
// data, large file-dump tool results); the ~256 KB Tier-2 attribute cap in
// otlp.ts is only a rare-fire backstop. See the plan's composition-distortion
// note: base64 truncation is a pure win, meaningful-text truncation is bounded
// and made visible via the in-band original-length marker. Values are tunable.

/** base64 image `data`: pure noise to the tokenizer, capped hard. */
export const IMAGE_DATA_CAP = 256
/** Other large raw/text leaves (tool-result bodies, thinking, tool_call args). */
export const LEAF_TEXT_CAP = 16 * 1024

/** Marker appended to a truncated leaf, carrying the ORIGINAL length so the
 * loss is visible downstream (the dashboard's bucket_context tokenizes the
 * emitted text and does not consult observme.original_length for leaf caps,
 * so the marker is the in-band signal). */
function truncationMarker(originalLength: number): string {
  return `…[truncated, ${originalLength} chars]`
}

/** Bound a string to `cap`. Under-cap returns the string unchanged
 * (byte-identical); over-cap returns `cap` chars + the marker. */
function boundString(s: string, cap: number): string {
  if (s.length <= cap) return s
  return s.slice(0, cap) + truncationMarker(s.length)
}

/** Bound an unknown-shape leaf (object or string) by its stringified size.
 * Under-cap returns the original value unchanged (byte-identical); over-cap
 * returns the truncated stringification + marker. Primitives are returned
 * as-is (they are never large). */
function boundLeaf(v: unknown, cap: number): unknown {
  let s: string
  if (typeof v === 'string') {
    s = v
  } else if (v !== null && typeof v === 'object') {
    try {
      s = JSON.stringify(v)
    } catch {
      return v // un-stringifiable (e.g. cyclic): leave untouched
    }
  } else {
    return v
  }
  if (s.length <= cap) return v
  return s.slice(0, cap) + truncationMarker(s.length)
}

/** Bound a base64 image content blob: PRESERVE THE PART SHAPE, truncating only
 * the `data` field when it exceeds the image cap. Under-cap returns the
 * original object reference unchanged (byte-identical). */
function boundImageContent<T extends Record<string, any>>(c: T): T {
  const data = c.data
  if (typeof data === 'string' && data.length > IMAGE_DATA_CAP) {
    return { ...c, data: boundString(data, IMAGE_DATA_CAP) }
  }
  return c
}

export interface CanonicalMessage {
  readonly role: string;
  readonly parts: CanonicalPart[];
}

function isRecord(v: unknown): v is Record<string, any> {
  return typeof v === "object" && v !== null;
}

/** One pi-ai content part -> one canonical part. */
function partFromContent(c: unknown): CanonicalPart {
  if (!isRecord(c)) {
    return { type: "text", text: String(c ?? "") };
  }
  switch (c.type) {
    case "text":
    case "input_text":
    case "output_text":
      return { type: "text", text: String(c.text ?? c.content ?? "") };
    case "thinking":
    case "reasoning":
      // PiAdapter maps "thinking"/"reasoning" -> reasoning bucket.
      return { type: "thinking", raw: boundLeaf(c, LEAF_TEXT_CAP) };
    case "toolCall":
    case "tool_use":
      // PiAdapter maps "tool_call"/"tool_use" -> tool_call bucket.
      return { type: "tool_call", raw: boundLeaf(c, LEAF_TEXT_CAP) };
    case "image":
      return { type: "image", raw: boundImageContent(c) };
    default:
      return { type: "other", raw: boundLeaf(c, LEAF_TEXT_CAP) };
  }
}

/**
 * Convert the messages array pi sends to the LLM (the `context` event payload)
 * into the canonical {role, parts} shape the PiAdapter ingests.
 *
 * Robust to the three roles pi emits:
 *   - user:        content is `string | (TextContent | ImageContent)[]`
 *   - assistant:   content is `(TextContent | ThinkingContent | ToolCall)[]`
 *   - toolResult:  pi folds the result onto the message; we surface it as a
 *                  single tool_result part so the adapter's tool-result bucket
 *                  fires (it buckets on part TYPE, never role).
 */
export function toCanonicalMessages(messages: unknown): CanonicalMessage[] {
  if (!Array.isArray(messages)) return [];
  const out: CanonicalMessage[] = [];
  for (const m of messages) {
    if (!isRecord(m)) continue;
    const role = String(m.role ?? "unknown");
    const content = m.content;

    // ToolResultMessage: one tool_result part carrying pi's own fields.
    if (role === "toolResult") {
      out.push({
        role,
        parts: [{
          type: "tool_result",
          raw: boundLeaf({
            toolCallId: m.toolCallId,
            toolName: m.toolName,
            content,
            isError: m.isError,
          }, LEAF_TEXT_CAP),
        }],
      });
      continue;
    }

    const parts: CanonicalPart[] = [];
    if (typeof content === "string") {
      parts.push({ type: "text", text: content });
    } else if (Array.isArray(content)) {
      for (const c of content) parts.push(partFromContent(c));
    } else if (content !== undefined && content !== null) {
      parts.push({ type: "other", raw: content });
    }
    out.push({ role, parts });
  }
  return out;
}
