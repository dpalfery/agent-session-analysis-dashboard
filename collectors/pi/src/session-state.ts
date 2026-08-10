/**
 * Per-session state accumulator.
 *
 * Tracks token counts, cost, and metadata across turns within a single Pi
 * session. Updated on each assistant `message_end` -- NOT on
 * `after_provider_response`, whose payload is `{status, headers}` and carries
 * no usage at all.
 *
 * WHY message_end AND NOT turn_end: one turn can make several LLM calls (every
 * tool round-trip is another call). `turn_end.message` is only the LAST
 * assistant message of the turn, so reading usage there drops every earlier
 * call in a tool loop -- an unbounded undercount on exactly the turns that cost
 * the most. Usage is therefore summed per call as the calls land, and the turn
 * bucket is drained when the turn ends.
 */
import type { PiSkill, PiSystemPromptOptions, PiTokenUsage, SessionStartData } from './types.ts';
import type { CanonicalMessage } from './messages.ts';

export interface SessionSummary {
  sessionId?: string;
  runId?: string;
  turnIndex?: number;
  model?: string;
  repo?: string;
  branch?: string;
  freshInput: number;
  output: number;
  cacheRead: number;
  cacheCreation: number;
  reasoning: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCostUsd: number;
  turnCostUsd: number;
  turnCount: number;
  costInputUsd?: number;
  costOutputUsd?: number;
  costCacheReadUsd?: number;
  costCacheWriteUsd?: number;
  // ---- request context (captured from before_agent_start + context events) ----
  /** Fully assembled system prompt. -> gen_ai.system_instructions */
  systemPrompt?: string;
  /** Expanded user prompt for this request. */
  userPrompt?: string;
  /** The message array sent to the LLM (most recent `context` fire). */
  inputMessages?: CanonicalMessage[];
  /** pi's reported output text for the turn, when captured. */
  assistantOutput?: string;
  /**
   * Raw provider request payload (before_provider_request). Diagnostic-only:
   * emitted verbatim as `pi.llm.request.payload` (Tier-2 cap, opaque JSON) so
   * the dashboard can compare it against gen_ai.input.messages. Never parsed
   * into canonical fields.
   */
  providerRequestPayload?: unknown;
  /** Skills loaded into the prompt. */
  skills?: PiSkill[];
  /** Tool inventory: names + one-line snippets from systemPromptOptions. */
  toolNames?: string[];
  toolSnippets?: Record<string, string>;
  /** Context files folded into the prompt (paths only; content is in the prompt). */
  contextFiles?: string[];
}

export class SessionState {
  private sessionId?: string;
  /**
   * Identifies one agent run -- pi's unit of "a user request". Minted at
   * `agent_start`, which is the real boundary: one submitted prompt drives one
   * agent loop, however many turns and tool calls it takes.
   *
   * The dashboard's PiAdapter builds its Request list from the DISTINCT
   * `pi.agent.run.id` values on child spans. Without this attribute every
   * session renders with zero requests, however many turns it actually ran.
   */
  private runId?: string;
  /** pi's own 0-based turn number, when `turn_end` supplied one. */
  private turnIndex?: number;
  private model?: string;
  private repo?: string;
  private branch?: string;

  private freshInput: number = 0;
  private output: number = 0;
  private cacheRead: number = 0;
  private cacheCreation: number = 0;
  private reasoning: number = 0;

  private totalInputTokens: number = 0;
  private totalOutputTokens: number = 0;
  private totalCostUsd: number = 0;
  private turnCostUsd: number = 0;
  private turnCount: number = 0;

  // Per-turn cost breakdown for OTLP
  private costInputUsd?: number;
  private costOutputUsd?: number;
  private costCacheReadUsd?: number;
  private costCacheWriteUsd?: number;

  // ---- request context ----
  // systemPrompt / userPrompt / skills / tools are per-RUN (one user request
  // drives one agent loop): set at before_agent_start, kept across the run's
  // turns. inputMessages / assistantOutput / providerRequestPayload are per-TURN
  // (the latest `context` / `before_provider_request` fire), drained at endTurn
  // so the next turn's span reports that turn's request rather than a stale one.
  private systemPrompt?: string;
  private userPrompt?: string;
  private inputMessages?: CanonicalMessage[];
  private assistantOutput?: string;
  // Raw provider request payload (before_provider_request). Same per-turn
  // overwrite-on-fire / drain-at-endTurn lifecycle as inputMessages above.
  private providerRequestPayload?: unknown;
  private skills?: PiSkill[];
  private toolNames?: string[];
  private toolSnippets?: Record<string, string>;
  private contextFiles?: string[];

  /**
   * Capture the request context pi assembled for this run -- the system prompt,
   * the expanded user prompt, and the inventory of what pi loaded (tools,
   * skills, context files). Called from the `before_agent_start` handler.
   */
  setRequestContext(opts: {
    systemPrompt?: string;
    userPrompt?: string;
    systemPromptOptions?: PiSystemPromptOptions;
  }): void {
    if (opts.systemPrompt !== undefined) this.systemPrompt = opts.systemPrompt;
    if (opts.userPrompt !== undefined) this.userPrompt = opts.userPrompt;
    const o = opts.systemPromptOptions;
    if (o) {
      if (o.selectedTools) this.toolNames = [...o.selectedTools];
      if (o.toolSnippets) this.toolSnippets = { ...o.toolSnippets };
      if (o.skills) this.skills = o.skills.map((s) => ({ name: s.name, description: s.description }));
      if (o.contextFiles) this.contextFiles = o.contextFiles.map((f) => f.path);
    }
  }

  /**
   * Capture the message array pi is about to send to the LLM. Overwrites on
   * each fire -- the LAST fire of a turn is the most complete request, and the
   * one the exported span should report. Called from the `context` handler.
   */
  setInputMessages(messages: CanonicalMessage[]): void {
    this.inputMessages = messages;
  }

  /** Capture the model's reply text for the current turn, if available. */
  setAssistantOutput(text?: string): void {
    if (text !== undefined) this.assistantOutput = text;
  }

  /**
   * Capture the raw provider request payload (before_provider_request event).
   * Overwrites on each fire -- same per-turn lifecycle as setInputMessages:
   * the last fire's payload is what the exported span reports, and endTurn()
   * drains it so the next turn reports its own request. Diagnostic-only.
   */
  setProviderRequestPayload(payload: unknown): void {
    this.providerRequestPayload = payload;
  }

  reset(): void {
    this.sessionId = undefined;
    this.runId = undefined;
    this.turnIndex = undefined;
    this.model = undefined;
    this.repo = undefined;
    this.branch = undefined;
    this.freshInput = 0;
    this.output = 0;
    this.cacheRead = 0;
    this.cacheCreation = 0;
    this.reasoning = 0;
    this.totalInputTokens = 0;
    this.totalOutputTokens = 0;
    this.totalCostUsd = 0;
    this.turnCostUsd = 0;
    this.turnCount = 0;
    this.costInputUsd = undefined;
    this.costOutputUsd = undefined;
    this.costCacheReadUsd = undefined;
    this.costCacheWriteUsd = undefined;
    this.systemPrompt = undefined;
    this.userPrompt = undefined;
    this.inputMessages = undefined;
    this.assistantOutput = undefined;
    this.providerRequestPayload = undefined;
    this.skills = undefined;
    this.toolNames = undefined;
    this.toolSnippets = undefined;
    this.contextFiles = undefined;
  }

  startSession(data: SessionStartData): void {
    this.sessionId = data.sessionId;
    this.model = data.model;
    this.repo = data.project;
  }

  /**
   * Fold one completed LLM call into the current turn.
   *
   * Called once per assistant message. Both the turn bucket and the session
   * totals accumulate; the turn bucket is what the exported span reports, and
   * `endTurn()` drains it.
   */
  addLlmCall(usage: PiTokenUsage, model?: string): void {
    if (model) {
      this.model = model;
    }

    // camelCase, and `input` already EXCLUDES both cache classes -- so total
    // input is the sum of the three disjoint classes, never `input` alone.
    this.freshInput += usage.input ?? 0;
    this.output += usage.output ?? 0;
    this.cacheRead += usage.cacheRead ?? 0;
    this.cacheCreation += usage.cacheWrite ?? 0;
    this.reasoning += usage.reasoning ?? 0;

    this.totalInputTokens += (usage.input ?? 0) + (usage.cacheRead ?? 0) + (usage.cacheWrite ?? 0);
    this.totalOutputTokens += usage.output ?? 0;

    const cost = usage.cost;
    if (cost) {
      this.turnCostUsd += cost.total ?? 0;
      this.totalCostUsd += cost.total ?? 0;
      this.costInputUsd = (this.costInputUsd ?? 0) + (cost.input ?? 0);
      this.costOutputUsd = (this.costOutputUsd ?? 0) + (cost.output ?? 0);
      this.costCacheReadUsd = (this.costCacheReadUsd ?? 0) + (cost.cacheRead ?? 0);
      this.costCacheWriteUsd = (this.costCacheWriteUsd ?? 0) + (cost.cacheWrite ?? 0);
    }
  }

  /** True when the current turn folded in at least one LLM call. */
  hasTurnActivity(): boolean {
    return (this.freshInput + this.output + this.cacheRead + this.cacheCreation) > 0;
  }

  incrementTurn(): void {
    this.turnCount++;
  }

  /**
   * Record pi's OWN turn number for this turn (`turn_end.turnIndex`, 0-based).
   *
   * Preferred over the local counter for the exported `pi.turn.index`: the
   * dashboard derives a run's turn total as max(pi.turn.index) + 1 and warns
   * when fewer turn spans arrived than that implies. Reporting pi's numbering
   * keeps that check meaningful; reporting our own would just restate how many
   * spans we sent and could never detect a gap.
   */
  setTurnIndex(index: number): void {
    this.turnIndex = index;
  }

  /**
   * Close the turn: clear the per-turn bucket.
   *
   * Session totals (totalInputTokens/totalOutputTokens/totalCostUsd) are NOT
   * cleared -- only the per-turn view is, so the next turn's span reports that
   * turn rather than a running sum.
   */
  endTurn(): void {
    this.freshInput = 0;
    this.output = 0;
    this.cacheRead = 0;
    this.cacheCreation = 0;
    this.reasoning = 0;
    this.turnCostUsd = 0;
    this.costInputUsd = undefined;
    this.costOutputUsd = undefined;
    this.costCacheReadUsd = undefined;
    this.costCacheWriteUsd = undefined;
    // Per-turn request view: drain so the next turn reports its own request.
    this.inputMessages = undefined;
    this.assistantOutput = undefined;
    this.providerRequestPayload = undefined;
  }

  startRun(runId: string): void {
    this.runId = runId;
  }

  setRepo(repo: string): void {
    this.repo = repo;
  }

  setBranch(branch: string): void {
    this.branch = branch;
  }

  toSummary(): SessionSummary {
    return {
      sessionId: this.sessionId,
      runId: this.runId,
      // pi's own numbering when it gave us one; otherwise derive a 0-based
      // index from the local counter so the attribute is never wrong-by-one.
      turnIndex: this.turnIndex ?? Math.max(0, this.turnCount - 1),
      model: this.model,
      repo: this.repo,
      branch: this.branch,
      freshInput: this.freshInput,
      output: this.output,
      cacheRead: this.cacheRead,
      cacheCreation: this.cacheCreation,
      reasoning: this.reasoning,
      totalInputTokens: this.totalInputTokens,
      totalOutputTokens: this.totalOutputTokens,
      totalCostUsd: this.totalCostUsd,
      turnCostUsd: this.turnCostUsd,
      turnCount: this.turnCount,
      costInputUsd: this.costInputUsd,
      costOutputUsd: this.costOutputUsd,
      costCacheReadUsd: this.costCacheReadUsd,
      costCacheWriteUsd: this.costCacheWriteUsd,
      systemPrompt: this.systemPrompt,
      userPrompt: this.userPrompt,
      inputMessages: this.inputMessages,
      assistantOutput: this.assistantOutput,
      providerRequestPayload: this.providerRequestPayload,
      skills: this.skills,
      toolNames: this.toolNames,
      toolSnippets: this.toolSnippets,
      contextFiles: this.contextFiles,
    };
  }
}
