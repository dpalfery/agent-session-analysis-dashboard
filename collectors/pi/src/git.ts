/**
 * Git branch lookup for the status bar's 🌿 segment.
 *
 * The AGY collector shells out to `git rev-parse --abbrev-ref HEAD` on every
 * render, which is free for it: AGY's status line is a fresh subprocess each
 * time. Here the handler runs inside the pi process, so the call is cached per
 * working directory and only re-checked between turns -- a branch switch shows
 * up on the next turn rather than mid-stream.
 */
import { execFileSync } from "node:child_process";

const cache = new Map<string, string>();

/**
 * Current branch for `cwd`, or undefined outside a repo / on a detached HEAD.
 *
 * `--abbrev-ref HEAD` prints the literal string "HEAD" when detached, which is
 * a branch name only by accident; it is treated as "no branch" so the segment
 * is omitted rather than showing a misleading 🌿 HEAD.
 */
export function gitBranch(cwd: string | undefined): string | undefined {
  if (!cwd) return undefined;
  const hit = cache.get(cwd);
  if (hit !== undefined) return hit || undefined;

  let branch = "";
  try {
    branch = execFileSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 500,
    }).trim();
  } catch {
    branch = "";                       // not a repo, no git, or it timed out
  }
  if (branch === "HEAD") branch = "";   // detached
  cache.set(cwd, branch);
  return branch || undefined;
}

/** Drop the cache so the next lookup re-reads (called between turns). */
export function invalidateGitBranch(): void {
  cache.clear();
}
