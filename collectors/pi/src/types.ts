/**
 * Type definitions for the Pi status bar collector.
 *
 * These mirror the real shapes exported by @earendil-works/pi-coding-agent
 * (dist/core/extensions/types.d.ts) and @earendil-works/pi-ai
 * (dist/types.d.ts) as of pi 0.84.1. They are restated locally rather than
 * imported so the collector has no hard build-time dependency, but they are
 * NOT guesses -- each field below exists in those declarations.
 *
 * THE THING TO GET RIGHT: pi's token/cost vocabulary is camelCase and lives on
 * the assistant MESSAGE, not on an event payload. An earlier version of this
 * file guessed OTel-style snake_case names (`input_tokens`, `cost.total_usd`)
 * and read them off `after_provider_response`. Every one of those reads
 * returned undefined, so the collector rendered a status bar of zeros and
 * exported spans with no usage on them. See PiTokenUsage below for the real
 * names.
 */

/**
 * Token usage pi reports for a single LLM call (`Usage` in pi-ai).
 *
 * `input` EXCLUDES both cache classes -- the same convention the dashboard's
 * PiAdapter documents and validates:
 *     input + cacheRead + cacheWrite + output == totalTokens
 * `reasoning` is a SUBSET of `output`, not an addition to it.
 */
export interface PiTokenUsage {
  readonly input: number;
  readonly output: number;
  readonly cacheRead: number;
  readonly cacheWrite: number;
  /** Subset of `cacheWrite` written with 1h retention. Anthropic only. */
  readonly cacheWrite1h?: number;
  /** Subset of `output`. Undefined for providers that report no breakdown. */
  readonly reasoning?: number;
  readonly totalTokens: number;
  readonly cost: PiCostBreakdown;
}

/** Cost breakdown pi computes for a single LLM call (`Usage.cost` in pi-ai). */
export interface PiCostBreakdown {
  readonly input: number;
  readonly output: number;
  readonly cacheRead: number;
  readonly cacheWrite: number;
  readonly total: number;
}

/**
 * The assistant message carried by `message_end` / `turn_end`.
 *
 * Only the assistant role carries `usage`; user and toolResult messages do
 * not, which is why handlers must discriminate on `role` before reading it.
 */
export interface PiAssistantMessage {
  readonly role: "assistant";
  readonly model: string;
  /** What the provider actually served, when it differs from the request. */
  readonly responseModel?: string;
  readonly provider?: string;
  readonly usage: PiTokenUsage;
  readonly stopReason?: string;
  readonly timestamp?: number;
}

/** Any message pi emits; narrow to PiAssistantMessage before reading usage. */
export interface PiAgentMessage {
  readonly role: string;
  readonly usage?: PiTokenUsage;
  readonly model?: string;
  readonly responseModel?: string;
}

/** `message_end` payload. */
export interface MessageEndEvent {
  readonly type?: "message_end";
  readonly message: PiAgentMessage;
}

/** `turn_end` payload. `message` is the LAST assistant message of the turn. */
export interface TurnEndEvent {
  readonly type?: "turn_end";
  readonly turnIndex: number;
  readonly message: PiAgentMessage;
}

/**
 * `session_start` payload -- deliberately tiny.
 *
 * It carries NO session id, cwd, or model. Those live on the handler context
 * (`ctx.sessionManager.getSessionId()`, `ctx.cwd`, `ctx.model`), which is
 * where this collector now reads them from.
 */
export interface SessionStartEvent {
  readonly type?: "session_start";
  readonly reason: "startup" | "reload" | "new" | "resume" | "fork";
  readonly previousSessionFile?: string;
}

/** Session identity resolved from the handler context, not from an event. */
export interface SessionStartData {
  readonly sessionId?: string;
  readonly model?: string;
  readonly cwd?: string;
  readonly project?: string;
}

/** A skill loaded into the system prompt (subset of pi's Skill). */
export interface PiSkill {
  readonly name: string;
  readonly description?: string;
}

/**
 * Subset of pi's BuildSystemPromptOptions carrying the context inventory -- the
 * tools, skills, and context files pi folded into the prompt. Restated locally
 * (not imported) to keep the collector free of a hard build-time dependency,
 * same convention as every other type in this file.
 */
export interface PiSystemPromptOptions {
  readonly customPrompt?: string;
  /** Tool names included in the prompt. */
  readonly selectedTools?: string[];
  /** One-line tool descriptions keyed by tool name. */
  readonly toolSnippets?: Record<string, string>;
  readonly promptGuidelines?: string[];
  readonly appendSystemPrompt?: string;
  readonly cwd?: string;
  /** Pre-loaded context files (AGENTS.md, etc.) folded into the prompt. */
  readonly contextFiles?: ReadonlyArray<{ readonly path: string; readonly content: string }>;
  /** Pre-loaded skills. */
  readonly skills?: PiSkill[];
}

/**
 * `before_agent_start` -- fires after the user submits a prompt but before the
 * agent loop. The ONE hook that carries the fully-assembled system prompt, the
 * expanded user prompt, and the structured inventory of what pi loaded (tools,
 * skills, context files). Captured here so the trace can show the exact system
 * prompt + user message, which the per-call `context` event does not.
 */
export interface BeforeAgentStartEvent {
  readonly type?: "before_agent_start";
  /** The raw user prompt text (after expansion). */
  readonly prompt: string;
  readonly images?: unknown[];
  /** The fully assembled system prompt string. */
  readonly systemPrompt: string;
  readonly systemPromptOptions: PiSystemPromptOptions;
}

/**
 * `context` -- fires before each LLM call with the message array pi is about to
 * send. The LAST fire of a turn carries the most complete request, so the
 * collector overwrites on each fire and exports the final view at turn_end.
 */
export interface ContextEvent {
  readonly type?: "context";
  readonly messages: unknown[];
}

/**
 * `before_provider_request` -- fires before each provider call with the raw
 * payload pi is about to send on the wire. Captured as diagnostic ground
 * truth so the dashboard can compare "what we parsed" (gen_ai.input.messages,
 * reconstructed from the `context` event) against "what actually went out". It
 * is emitted verbatim as `pi.llm.request.payload` (Tier-2 cap only -- the blob
 * is unknown-shape JSON) and NEVER parsed into canonical fields by the adapter;
 * it rides along in raw_attributes only.
 */
export interface BeforeProviderRequestEvent {
  readonly type?: "before_provider_request";
  readonly payload: unknown;
}

/** Minimal Pi ExtensionAPI shape for type safety without hard dependency. */
export interface PiExtensionAPI {
  on(event: string, handler: (event: any, ctx: PiHandlerContext) => void | Promise<void>): void;
  registerCommand?(name: string, handler: (...args: unknown[]) => unknown): void;
}

/** Handler context provided by Pi to event handlers (`ExtensionContext`). */
export interface PiHandlerContext {
  readonly ui?: {
    notify?: (message: string, type?: "info" | "warning" | "error") => void;
    setStatus?: (key: string, text: string | undefined) => void;
    /**
     * Persistent lines rendered above or below the editor. This is how a full
     * status bar reaches the TUI: pi owns the screen there, so raw writes to
     * stderr are torn up by the next repaint, but a widget is part of what pi
     * repaints. Pass undefined as content to remove it.
     */
    setWidget?: (
      key: string,
      content: string[] | undefined,
      options?: { placement?: "aboveEditor" | "belowEditor" },
    ) => void;
  };
  /** "tui" means a live terminal UI owns the screen -- do not write to it. */
  readonly mode?: "tui" | "rpc" | "json" | "print";
  readonly cwd?: string;
  readonly model?: { id?: string; name?: string; provider?: string };
  readonly sessionManager?: {
    getSessionId?: () => string;
    getCwd?: () => string;
    getSessionName?: () => string | undefined;
  };
}

// ---- OTLP payload types ----

export interface OtlpAttributeValue {
  stringValue?: string;
  intValue?: number;
  doubleValue?: number;
  boolValue?: boolean;
}

export interface OtlpAttribute {
  key: string;
  value: OtlpAttributeValue;
}

export interface OtlpSpan {
  traceId: string;
  spanId: string;
  name: string;
  kind: number;
  startTimeUnixNano: string;
  endTimeUnixNano: string;
  attributes: OtlpAttribute[];
  status: { code: number };
}

export interface OtlpPayload {
  resourceSpans: Array<{
    resource: { attributes: OtlpAttribute[] };
    scopeSpans: Array<{
      scope: { name: string };
      spans: OtlpSpan[];
    }>;
  }>;
}

// ---- Internal Types ----
