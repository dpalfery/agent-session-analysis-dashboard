/**
 * Statusbar Tests
 */
import { describe, it } from 'node:test';
import assert from 'node:assert';
import {
  fmtK, fmtUsd, packSegments, renderStatusBar, renderStatusBarLines,
  statusBarSegments, visibleWidth,
} from '../statusbar.ts';
import type { SessionSummary } from '../session-state.ts';

describe('fmtK', () => {
  it('formats large numbers to k notation', () => {
    assert.strictEqual(fmtK(22000), '22.0k');
    assert.strictEqual(fmtK(22500), '22.5k');
    assert.strictEqual(fmtK(1000), '1.0k');
  });

  it('keeps small numbers as-is', () => {
    assert.strictEqual(fmtK(500), '500');
    assert.strictEqual(fmtK(999), '999');
    assert.strictEqual(fmtK(0), '0');
  });

  it('returns -- for undefined', () => {
    assert.strictEqual(fmtK(undefined), '--');
  });
});

describe('fmtUsd', () => {
  it('formats large USD values with 2 decimal places', () => {
    assert.strictEqual(fmtUsd(1.5), '$1.50');
    assert.strictEqual(fmtUsd(10.0), '$10.00');
  });

  it('formats small USD values with 4 decimal places', () => {
    assert.strictEqual(fmtUsd(0.0523), '$0.0523');
    assert.strictEqual(fmtUsd(0), '$0.0000');
  });

  it('returns -- for undefined', () => {
    assert.strictEqual(fmtUsd(undefined), '--');
  });
});

describe('renderStatusBar', () => {
  const summary: SessionSummary = {
    sessionId: 'test',
    model: 'claude-3-5',
    repo: 'my-project',
    branch: 'main',
    freshInput: 5000,
    output: 500,
    cacheRead: 3000,
    cacheCreation: 200,
    reasoning: 0,
    totalInputTokens: 22000,
    totalOutputTokens: 500,
    totalCostUsd: 1.5,
    turnCostUsd: 0.3,
    turnCount: 5,
  };

  it('contains PI badge and key data', () => {
    const output = renderStatusBar(summary);
    assert.ok(output.includes('PI'), 'should include PI badge');
    assert.ok(output.includes('claude-3-5'), 'should include model');
    assert.ok(output.includes('my-project'), 'should include repo');
    assert.ok(output.includes('main'), 'should include branch');
    assert.ok(output.includes('$1.50'), 'should include cost');
    assert.ok(output.includes('turn #5'), 'should include turn count');
  });

  it('handles missing optional values gracefully', () => {
    const minimal: SessionSummary = {
      freshInput: 0, output: 0, cacheRead: 0, cacheCreation: 0, reasoning: 0,
      totalInputTokens: 0, totalOutputTokens: 0, totalCostUsd: 0, turnCostUsd: 0, turnCount: 0,
    };
    const output = renderStatusBar(minimal);
    assert.ok(output.includes('PI'), 'should still include PI badge');
    assert.ok(output.length > 0);
  });
});

/**
 * Chrome parity with the AGY collector (collectors/gemini/statusline.py).
 *
 * These pin the ESCAPE SEQUENCES and segment order, not the data -- pi and AGY
 * deliberately report different figures. Asserting on the raw ANSI is the point:
 * a bar that merely "looks similar" in a screenshot can still differ in
 * separator color or a missing reset, and only the bytes show that.
 */
describe('AGY chrome parity', () => {
  // Transcribed from statusline.py's colour table and its final join.
  const AGY = {
    badge: (label: string) => `\x1b[1;37;44m ${label} \x1b[0m`,
    cyan: '\x1b[1;36m', green: '\x1b[1;32m', magenta: '\x1b[1;35m',
    yellow: '\x1b[1;33m', blue: '\x1b[1;34m', gray: '\x1b[38;5;244m',
    reset: '\x1b[0m', sep: ' │ ',
  };

  const summary: SessionSummary = {
    model: 'glm-5.2', repo: 'my-project', branch: 'main',
    freshInput: 5000, output: 500, cacheRead: 3000, cacheCreation: 200,
    reasoning: 0, totalInputTokens: 22000, totalOutputTokens: 500,
    totalCostUsd: 1.5, turnCostUsd: 0.3, turnCount: 5,
  };

  it('uses AGY badge styling, differing only in the label', () => {
    assert.ok(renderStatusBar(summary).startsWith(AGY.badge('PI')));
  });

  it('joins segments with AGY plain uncolored separator', () => {
    const out = renderStatusBar(summary);
    assert.ok(out.includes(AGY.sep), 'must join with a plain " │ "');
    assert.ok(!out.includes(`${AGY.gray}│${AGY.reset}`),
      'separator must not be gray-wrapped as it once was');
  });

  it('colors each segment the way AGY colors it', () => {
    const out = renderStatusBar(summary);
    assert.ok(out.includes(`${AGY.cyan}\u{1F4C1} my-project${AGY.reset}`), 'repo: cyan 📁');
    assert.ok(out.includes(`${AGY.green}\u{1F33F} main${AGY.reset}`), 'branch: green 🌿');
    assert.ok(out.includes(`${AGY.magenta}\u{1F916} glm-5.2${AGY.reset}`), 'model: magenta 🤖');
    assert.ok(out.includes(`${AGY.blue}\u{23F3} `), 'trailing gauge: blue ⏳');
  });

  it('spells the token segment as AGY does: {YELLOW}total{RESET} {GRAY}(..){RESET}', () => {
    // 5000 + 3000 + 200 + 500 = 8700 -> "8.7k"
    const out = renderStatusBar(summary);
    assert.ok(out.includes(`${AGY.yellow}\u{26A1} 8.7k${AGY.reset} ${AGY.gray}(`),
      'the total must reset before the gray parenthetical');
    assert.ok(out.includes(`)${AGY.reset}`), 'the parenthetical must reset after');
  });

  it('wraps rather than clipping when the terminal is too narrow', () => {
    // The regression: pi clips a widget line at the terminal edge, so on an
    // 80-column window the bar lost its cost and turn segments silently.
    const wide = renderStatusBarLines(summary, 200);
    assert.strictEqual(wide.length, 1, 'a wide terminal keeps one line');
    assert.strictEqual(wide[0], renderStatusBar(summary));

    const narrow = renderStatusBarLines(summary, 80);
    assert.ok(narrow.length > 1, 'an 80-column terminal must wrap');
    for (const line of narrow) {
      assert.ok(visibleWidth(line) <= 80, `line exceeds 80 cols: ${visibleWidth(line)}`);
    }

    // Nothing may be dropped: every segment survives somewhere.
    const joined = narrow.join('');
    for (const seg of statusBarSegments(summary)) {
      assert.ok(joined.includes(seg), 'every segment must survive wrapping');
    }
  });

  it('keeps an over-wide segment rather than dropping it', () => {
    const packed = packSegments(['\u{1F4C1} a-very-long-repository-name-here'], 10);
    assert.strictEqual(packed.length, 1);
    assert.ok(packed[0].includes('a-very-long-repository-name-here'));
  });

  it('visibleWidth ignores ANSI and counts emoji as two columns', () => {
    assert.strictEqual(visibleWidth('\x1b[1;36mabc\x1b[0m'), 3);
    assert.strictEqual(visibleWidth('\u{1F4C1}'), 2);
    assert.strictEqual(visibleWidth('\u{2502}'), 1);
  });

  it('emits segments in AGY order: repo, branch, model, tokens, then gauge', () => {
    const out = renderStatusBar(summary);
    const at = (needle: string) => out.indexOf(needle);
    const order = [at('\u{1F4C1}'), at('\u{1F33F}'), at('\u{1F916}'), at('\u{26A1}'), at('\u{23F3}')];
    assert.deepStrictEqual(order, [...order].sort((a, b) => a - b),
      'segments must appear in AGY order');
    assert.ok(order.every(i => i > 0), 'every segment must be present');
  });
});
