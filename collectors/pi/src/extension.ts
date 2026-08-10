/**
 * Pi Statusline Collector Extension Entry Point
 *
 * Hooks into Pi's lifecycle events to render a branded status bar and
 * export OTLP GenAI trace spans to the Agent Session Analysis Dashboard.
 */

import crypto from "node:crypto";

import { SessionState } from "./session-state.ts";
import { gitBranch, invalidateGitBranch } from "./git.ts";
import { toCanonicalMessages } from "./messages.ts";
import type {
  BeforeAgentStartEvent,
  BeforeProviderRequestEvent,
  ContextEvent,
  MessageEndEvent,
  PiExtensionAPI,
  PiHandlerContext,
  SessionStartEvent,
  TurnEndEvent,
} from "./types.ts";
import { buildOtlpPayload } from "./otlp.ts";
import { exportSpan } from "./exporter.ts";
import { renderToUi } from "./statusbar.ts";

function safeStr(v: unknown): string | undefined {
  if (v === null || v === undefined) return undefined;
  return String(v);
}

/**
 * Pull the model's reply text out of an assistant message's content parts
 * (best-effort, for the trace's output view). pi's AssistantMessage.content is
 * an array of {type:"text"|"thinking"|"toolCall", ...}; we keep only the text.
 */
function assistantText(msg: { content?: unknown } | undefined): string | undefined {
  const content = msg?.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return undefined;
  const texts: string[] = [];
  for (const p of content) {
    if (p && typeof p === "object" && (p as { type?: string }).type === "text"
        && typeof (p as { text?: unknown }).text === "string") {
      texts.push((p as { text: string }).text);
    }
  }
  return texts.length ? texts.join("\n") : undefined;
}

/** Basename of a path, used as the repo label when nothing better is known. */
function basename(p: string | undefined): string | undefined {
  if (!p) return undefined;
  const parts = p.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || undefined;
}

/**
 * Read session identity off the handler CONTEXT.
 *
 * `session_start` is `{type, reason, previousSessionFile}` -- it has no id, no
 * cwd and no model. Every one of those comes from ctx instead. Reading them
 * from the event is what made the status bar render "unknown / unknown / 0".
 */
function identify(ctx: PiHandlerContext) {
  const cwd = safeStr(ctx.sessionManager?.getCwd?.()) ?? safeStr(ctx.cwd);
  return {
    sessionId: safeStr(ctx.sessionManager?.getSessionId?.()),
    model: safeStr(ctx.model?.id),
    cwd,
    // The cwd basename, matching what AGY shows in 📁 -- and matching what the
    // exported `pi.cwd.basename` attribute claims to be. The pi session's
    // display name is deliberately NOT used here: it is free text set by
    // `--name`, so it would make that attribute mean something else.
    project: basename(cwd),
  };
}

export default function piStatusline(pi: PiExtensionAPI): void {
  try {
    const state = new SessionState();

    // ---- session_start ----
    // `reason` distinguishes a fresh start from a resume/fork. All of them get
    // a clean accumulator: pi rebinds extensions per session, and carrying the
    // previous session's totals across would attribute them to the new id.
    pi.on("session_start", (_event: SessionStartEvent, ctx: PiHandlerContext) => {
      try {
        const id = identify(ctx);
        state.reset();
        invalidateGitBranch();
        state.startSession(id);
        if (id.project) state.setRepo(id.project);
        const branch = gitBranch(id.cwd);
        if (branch) state.setBranch(branch);
        // Paint immediately. Rendering only on turn_end left the TUI with no
        // bar at all until the first turn finished, which reads as "the
        // extension never loaded".
        renderToUi(ctx, state.toSummary());
      } catch (err) {
        console.error("[pi-statusline] Error in session_start handler:", err);
      }
    });

    // ---- before_agent_start ----
    // The ONE hook that carries the fully-assembled system prompt, the expanded
    // user prompt, and the inventory of what pi loaded (tools, skills, context
    // files). Captured here so the trace can show the exact system prompt +
    // user message -- the per-call `context` event has neither. Without this
    // the dashboard's system-prompt and context-composition views have nothing
    // for pi, which is the whole point of this collector.
    pi.on("before_agent_start", (event: BeforeAgentStartEvent, _ctx: PiHandlerContext) => {
      try {
        state.setRequestContext({
          systemPrompt: event?.systemPrompt,
          userPrompt: event?.prompt,
          systemPromptOptions: event?.systemPromptOptions,
        });
      } catch (err) {
        console.error("[pi-statusline] Error in before_agent_start handler:", err);
      }
    });

    // ---- agent_start ----
    // One agent loop == one user request. Minting the run id here is what lets
    // the dashboard group this session's turns into requests.
    pi.on("agent_start", (_event: unknown, _ctx: PiHandlerContext) => {
      try {
        state.startRun(crypto.randomUUID());
      } catch (err) {
        console.error("[pi-statusline] Error in agent_start handler:", err);
      }
    });

    // ---- context ----
    // Fires before each LLM call with the message array pi is about to send.
    // The LAST fire of a turn is the most complete request, so SessionState
    // overwrites on each fire and exports the final view. This is the input
    // messages the dashboard bucketizes into system/history/tool-result context.
    pi.on("context", (event: ContextEvent, _ctx: PiHandlerContext) => {
      try {
        state.setInputMessages(toCanonicalMessages(event?.messages));
      } catch (err) {
        console.error("[pi-statusline] Error in context handler:", err);
      }
    });

    // ---- before_provider_request ----
    // Fires before each provider call with the raw payload pi is about to send
    // on the wire. Captured as diagnostic ground truth ("what actually went
    // out") alongside the reconstructed gen_ai.input.messages ("what we
    // parsed"). Overwrites on each fire and drains at turn_end, same lifecycle
    // as the `context` handler above. Emitted verbatim as
    // pi.llm.request.payload (Tier-2 cap only); never parsed by the adapter.
    pi.on("before_provider_request", (event: BeforeProviderRequestEvent, _ctx: PiHandlerContext) => {
      try {
        state.setProviderRequestPayload(event?.payload);
      } catch (err) {
        console.error("[pi-statusline] Error in before_provider_request handler:", err);
      }
    });

    // ---- message_end ----
    // The only place real usage is available. Assistant messages carry
    // `usage`; user and toolResult messages do not, hence the role guard.
    pi.on("message_end", (event: MessageEndEvent, _ctx: PiHandlerContext) => {
      try {
        const msg = event?.message;
        if (!msg || msg.role !== "assistant" || !msg.usage) return;
        state.addLlmCall(msg.usage, msg.responseModel ?? msg.model);
        // Capture the model's reply text for this turn's output view. The
        // content array is present at runtime even though the loose local type
        // does not declare it.
        state.setAssistantOutput(assistantText(msg as { content?: unknown }));
      } catch (err) {
        console.error("[pi-statusline] Error in message_end handler:", err);
      }
    });

    // ---- turn_end ----
    pi.on("turn_end", (_event: TurnEndEvent | undefined, ctx: PiHandlerContext) => {
      try {
        // Late-binding: on the first turn the session id may not have been
        // resolvable at session_start (pi assigns it as the session file is
        // created). Re-read it here so the span is never exported without the
        // attribute the dashboard groups sessions by.
        if (!state.toSummary().sessionId) {
          const id = identify(ctx);
          if (id.sessionId) state.startSession(id);
          if (id.project) state.setRepo(id.project);
        }

        if (typeof _event?.turnIndex === "number") {
          state.setTurnIndex(_event.turnIndex);
        }

        // Re-read the branch between turns: an agent that runs `git checkout`
        // mid-session would otherwise keep showing the branch it started on.
        const id = identify(ctx);
        invalidateGitBranch();
        const branch = gitBranch(id.cwd);
        if (branch) state.setBranch(branch);
        // Bump before rendering so the first completed turn reads "turn #1"
        // rather than "#0" -- the counter is a human-facing tally, distinct
        // from pi's 0-based turnIndex that goes on the span.
        state.incrementTurn();

        // A turn with no LLM call (a slash command, an interrupted turn) has
        // nothing to report. Exporting it would add a zero-token span that the
        // dashboard counts as a real request.
        const hadActivity = state.hasTurnActivity();
        const summary = state.toSummary();
        renderToUi(ctx, summary);

        if (hadActivity) {
          const payload = buildOtlpPayload(summary);
          exportSpan(payload).catch(() => {
            // Swallow — collector must never crash Pi
          });
        }
        state.endTurn();
      } catch (err) {
        console.error("[pi-statusline] Error in turn_end handler:", err);
      }
    });

    // ---- session_shutdown ----
    // Flushes only a turn that ended mid-flight (calls landed but `turn_end`
    // never fired, e.g. Ctrl-C or exit during streaming). A turn that closed
    // normally already exported and drained its bucket, so re-exporting here
    // would double-count it -- which is why this is guarded on hasTurnActivity
    // rather than exporting unconditionally.
    pi.on("session_shutdown", async (_event: unknown, ctx: PiHandlerContext) => {
      try {
        if (!state.hasTurnActivity()) return;
        const summary = state.toSummary();
        renderToUi(ctx, summary);

        const payload = buildOtlpPayload(summary);
        // Bounded wait so spans flush before exit without hanging the process.
        await Promise.race([
          exportSpan(payload),
          new Promise<void>((resolve) => setTimeout(resolve, 2000)),
        ]).catch(() => {
          // Swallow
        });
        state.endTurn();
      } catch (err) {
        console.error("[pi-statusline] Error in session_shutdown handler:", err);
      }
    });

  } catch (err) {
    console.error("[pi-statusline] Failed to initialize extension:", err);
  }
}
