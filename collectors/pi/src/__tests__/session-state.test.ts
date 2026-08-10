/**
 * Session State Tests
 */
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SessionState } from '../session-state.ts';
import type { PiTokenUsage } from '../types.ts';

/** Build a pi `Usage` — camelCase, nested cost, totalTokens self-consistent. */
function usage(o: {
  input?: number; output?: number; cacheRead?: number; cacheWrite?: number;
  reasoning?: number; cost?: number;
}): PiTokenUsage {
  const input = o.input ?? 0, output = o.output ?? 0;
  const cacheRead = o.cacheRead ?? 0, cacheWrite = o.cacheWrite ?? 0;
  const total = o.cost ?? 0;
  return {
    input, output, cacheRead, cacheWrite, reasoning: o.reasoning,
    totalTokens: input + output + cacheRead + cacheWrite,
    cost: { input: total, output: 0, cacheRead: 0, cacheWrite: 0, total },
  };
}

describe('SessionState', () => {
  it('Initial state has zero totals', () => {
    const state = new SessionState();
    const summary = state.toSummary();
    assert.strictEqual(summary.totalInputTokens, 0);
    assert.strictEqual(summary.totalOutputTokens, 0);
    assert.strictEqual(summary.totalCostUsd, 0);
    assert.strictEqual(summary.turnCount, 0);
  });

  it('addLlmCall SUMS calls within a turn rather than keeping only the last', () => {
    // The regression this guards: reading usage off turn_end.message captured
    // only the final call of a turn, so every tool round-trip vanished.
    const state = new SessionState();
    state.startSession({ sessionId: 'test-session' });

    state.addLlmCall(usage({ input: 100, output: 50, cost: 0.1 }), 'glm-5.2');
    state.addLlmCall(usage({ input: 200, output: 100, cacheRead: 50, cost: 0.2 }), 'glm-5.2');

    const summary = state.toSummary();
    assert.strictEqual(summary.freshInput, 300);        // 100 + 200, not 200
    assert.strictEqual(summary.output, 150);
    assert.strictEqual(summary.cacheRead, 50);
    assert.strictEqual(summary.totalInputTokens, 350);  // 100 + (200 + 50 + 0)
    assert.strictEqual(summary.totalOutputTokens, 150);
    assert.ok(Math.abs(summary.totalCostUsd - 0.3) < 1e-9);
    assert.strictEqual(summary.model, 'glm-5.2');
  });

  it('input is EXCLUSIVE of cache, so total_tokens is the sum of all four classes', () => {
    // Mirrors PiAdapter.validate()'s token_total_mismatch check exactly.
    const state = new SessionState();
    state.addLlmCall(usage({ input: 10, output: 4, cacheRead: 20, cacheWrite: 6, cost: 0 }));

    const s = state.toSummary();
    const computedTotal = s.freshInput + s.cacheRead + s.cacheCreation + s.output;
    assert.strictEqual(computedTotal, 40);
  });

  it('endTurn() drains the turn bucket but keeps session totals', () => {
    const state = new SessionState();
    state.addLlmCall(usage({ input: 100, output: 50, cost: 0.1 }));
    state.incrementTurn();   // the handler tallies the turn before rendering
    state.endTurn();         // endTurn only drains; it does not count

    const summary = state.toSummary();
    assert.strictEqual(summary.freshInput, 0);          // turn bucket drained
    assert.strictEqual(summary.output, 0);
    assert.strictEqual(summary.turnCostUsd, 0);
    assert.strictEqual(summary.totalInputTokens, 100);  // session totals kept
    assert.strictEqual(summary.totalOutputTokens, 50);
    assert.ok(Math.abs(summary.totalCostUsd - 0.1) < 1e-9);
    assert.strictEqual(summary.turnCount, 1);
  });

  it('pi.turn.index prefers pi own 0-based numbering over the local counter', () => {
    const state = new SessionState();
    state.incrementTurn();
    assert.strictEqual(state.toSummary().turnIndex, 0);  // derived, 0-based

    state.setTurnIndex(7);                               // pi says this is turn 7
    assert.strictEqual(state.toSummary().turnIndex, 7);
  });

  it('hasTurnActivity() is false for a turn with no LLM call', () => {
    // Guards against exporting a zero-token span the dashboard would count as
    // a real request (slash commands, interrupted turns).
    const state = new SessionState();
    assert.strictEqual(state.hasTurnActivity(), false);
    state.addLlmCall(usage({ input: 1, output: 1, cost: 0 }));
    assert.strictEqual(state.hasTurnActivity(), true);
    state.endTurn();
    assert.strictEqual(state.hasTurnActivity(), false);
  });

  it('reset() clears all state', () => {
    const state = new SessionState();
    state.startSession({ sessionId: 'test-session' });
    state.setRepo('my-repo');
    state.startRun('run-1');
    state.addLlmCall(usage({ input: 100, output: 50, cost: 0.1 }));

    state.reset();
    const summary = state.toSummary();
    assert.strictEqual(summary.totalInputTokens, 0);
    assert.strictEqual(summary.totalCostUsd, 0);
    assert.strictEqual(summary.sessionId, undefined);
    assert.strictEqual(summary.repo, undefined);
    assert.strictEqual(summary.runId, undefined);
  });

  it('turn count increments via incrementTurn()', () => {
    const state = new SessionState();
    state.incrementTurn();
    state.incrementTurn();
    assert.strictEqual(state.toSummary().turnCount, 2);
  });
});
