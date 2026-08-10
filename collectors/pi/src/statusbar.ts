/**
 * Status Bar Renderer.
 *
 * Renders a branded [PI] ANSI status bar to the terminal and/or
 * Pi's UI status line, mirroring the AGY collector's visual format.
 */
import type { PiHandlerContext } from './types.ts';
import type { SessionSummary } from './session-state.ts';

/** Format a number in compact "k" notation. */
export function fmtK(val: number | undefined): string {
  if (val === undefined) return '--';
  if (val >= 1000) return (val / 1000).toFixed(1) + 'k';
  return val.toString();
}

/** Format a USD value for display. */
export function fmtUsd(val: number | undefined): string {
  if (val === undefined) return '--';
  if (val >= 1.0) return '$' + val.toFixed(2);
  if (val >= 0.01) return '$' + val.toFixed(4);
  return '$' + val.toFixed(4);
}

/** The separator AGY joins with: a plain, uncolored box-drawing pipe. */
const SEP = ' \u{2502} ';

/** Strip SGR escapes so a string can be measured as the terminal sees it. */
// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;]*m/g;

/**
 * Approximate rendered column count.
 *
 * Emoji outside the BMP occupy two columns in every terminal that matters here,
 * and JS string length counts them as two UTF-16 units already -- so counting
 * code points and adding one back per astral character lands on the right
 * number without pulling in a full wcwidth table.
 */
export function visibleWidth(s: string): number {
  const plain = s.replace(ANSI, '');
  let w = 0;
  for (const ch of plain) w += (ch.codePointAt(0) ?? 0) > 0xffff ? 2 : 1;
  return w;
}

/**
 * Pack segments into lines that each fit `columns`.
 *
 * pi clips a widget line at the terminal edge rather than wrapping it, so a bar
 * wider than the window silently loses its tail -- on an 80-column terminal
 * that is the cost and turn segments, the two a person is most likely watching.
 * Wrapping keeps every segment on screen; a terminal too narrow for even one
 * segment still gets that segment, since dropping it would be worse.
 */
export function packSegments(segments: string[], columns: number): string[] {
  const sepWidth = visibleWidth(SEP);
  const lines: string[] = [];
  let line = '';
  let width = 0;

  for (const seg of segments) {
    const segWidth = visibleWidth(seg);
    if (line === '') {
      line = seg;
      width = segWidth;
      continue;
    }
    if (width + sepWidth + segWidth <= columns) {
      line += SEP + seg;
      width += sepWidth + segWidth;
    } else {
      lines.push(line);
      line = seg;
      width = segWidth;
    }
  }
  if (line !== '') lines.push(line);
  return lines;
}

/** The individual colored segments, in AGY's order. */
export function statusBarSegments(summary: SessionSummary): string[] {
  const PI_BADGE = '\x1b[1;37;44m PI \x1b[0m';
  const CYAN = '\x1b[1;36m';
  const GREEN = '\x1b[1;32m';
  const MAGENTA = '\x1b[1;35m';
  const YELLOW = '\x1b[1;33m';
  const BLUE = '\x1b[1;34m';
  const GRAY = '\x1b[38;5;244m';
  const RESET = '\x1b[0m';

  const parts: string[] = [PI_BADGE];

  if (summary.repo) parts.push(`${CYAN}\u{1F4C1} ${summary.repo}${RESET}`);
  if (summary.branch) parts.push(`${GREEN}\u{1F33F} ${summary.branch}${RESET}`);
  if (summary.model) parts.push(`${MAGENTA}\u{1F916} ${summary.model}${RESET}`);

  const totalTokens = (summary.freshInput ?? 0) + (summary.cacheRead ?? 0)
    + (summary.cacheCreation ?? 0) + (summary.output ?? 0);
  const tokenBreak = `${GRAY}(in:${fmtK(summary.freshInput)} out:${fmtK(summary.output)} cache:${fmtK((summary.cacheRead ?? 0) + (summary.cacheCreation ?? 0))})${RESET}`;
  // Spelled exactly as AGY spells it -- {YELLOW}⚡ {total}{RESET} {GRAY}(..){RESET}
  // -- including the reset before the gray parenthetical, so the two bars carry
  // identical escape sequences rather than merely looking alike.
  parts.push(`${YELLOW}\u{26A1} ${fmtK(totalTokens)}${RESET} ${tokenBreak}`);

  parts.push(`${GREEN}\u{1F4B0} ${fmtUsd(summary.totalCostUsd)}${RESET}`);
  parts.push(`${BLUE}\u{23F3} turn #${summary.turnCount}${RESET}`);

  return parts;
}

/**
 * Render the full ANSI-colored status bar as one line.
 *
 * Joined with AGY's plain, uncolored separator (`" │ ".join(parts)`), so the
 * two bars line up visually rather than merely resembling each other.
 */
export function renderStatusBar(summary: SessionSummary): string {
  return statusBarSegments(summary).join(SEP);
}

/** The bar wrapped to `columns`, for surfaces that clip instead of wrapping. */
export function renderStatusBarLines(summary: SessionSummary, columns: number): string[] {
  return packSegments(statusBarSegments(summary), columns);
}

/**
 * Render the status bar by whichever route the current mode actually shows.
 *
 * THE THING TO GET RIGHT: there is no single output that works in both modes.
 *
 *   - In "tui" pi owns the screen and repaints it. Raw escape sequences written
 *     to stderr are torn up by the next repaint, so the bar must be a WIDGET --
 *     content pi itself redraws. An earlier version simply skipped the bar in
 *     this mode, which is why interactive `pi` showed nothing at all.
 *   - Outside "tui" (print/json/rpc) there is no widget surface and nothing
 *     repaints, so a plain stderr write is both safe and the only option.
 *
 * The compact footer via setStatus is set in every mode; it is a no-op where
 * there is no footer. It carries SESSION totals rather than the current turn's,
 * because the footer persists -- showing one turn there makes the number look
 * like it resets to near-zero after every turn.
 */
export function renderToUi(ctx: PiHandlerContext, summary: SessionSummary): void {
  const sessionTokens = (summary.totalInputTokens ?? 0) + (summary.totalOutputTokens ?? 0);
  const shortStatus = `\u{1F916} ${summary.model || '--'} \u{2502} \u{26A1} ${fmtK(sessionTokens)} \u{2502} \u{1F4B0} ${fmtUsd(summary.totalCostUsd)}`;

  try {
    ctx.ui?.setStatus?.('pi-statusline', shortStatus);
  } catch {
    // Swallow — status bar is best-effort
  }

  if (ctx.mode === 'tui') {
    // Fall back to 80 when the width is unknown (not a tty): too narrow is
    // recoverable -- it just wraps -- whereas guessing too wide clips.
    const columns = process.stdout.columns || 80;
    try {
      ctx.ui?.setWidget?.('pi-statusline', renderStatusBarLines(summary, columns),
        { placement: 'belowEditor' });
    } catch {
      // Swallow — status bar is best-effort
    }
    return;
  }

  process.stderr.write(renderStatusBar(summary) + '\n');
}
