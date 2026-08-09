"""
ingest.py -- get spans into the store, from files or from a live follow stream.

Both paths funnel through Store.add_spans, which is idempotent on span_id, so
re-ingesting an export or a restarted follow stream re-delivering spans costs
nothing and cannot corrupt state.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

from .adapters import registry

DASHBOARD_URL = os.environ.get("ASPIRE_DASHBOARD_URL", "http://localhost:18888")

# Raw exports live here and are gitignored. They carry real repo content pulled
# into tool results, so they must never reach git history.
SPANS_DIR = os.environ.get(
    "AGENTDASH_SPANS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".spans"))


def spans_dir():
    os.makedirs(SPANS_DIR, exist_ok=True)
    return SPANS_DIR


def export_files():
    """Every raw export on disk, oldest first."""
    import glob
    return sorted(glob.glob(os.path.join(SPANS_DIR, "*.json")))


def aspire_bin():
    """The CLI is a dotnet global tool and is often absent from a non-interactive PATH."""
    return shutil.which("aspire") or (
        os.path.expanduser("~/.dotnet/tools/aspire")
        if os.path.exists(os.path.expanduser("~/.dotnet/tools/aspire")) else None)


def ingest_spans(store, spans, origin):
    """Detect harness per (source, trace) group, then upsert."""
    resolver = registry.harness_resolver(spans)
    return store.add_spans(spans, resolver, origin=origin)


def ingest_file(store, path):
    with open(path) as f:
        spans = json.load(f)
    if not isinstance(spans, list):
        raise SystemExit(f"FATAL: {path}: expected a top-level JSON array, "
                         f"got {type(spans).__name__}.")
    return ingest_spans(store, spans, origin=os.path.basename(path))


def export_once(dest, limit=None, timeout=300):
    """Full export to `dest`. Returns the span list."""
    exe = aspire_bin()
    if not exe:
        raise RuntimeError("aspire CLI not found (PATH or ~/.dotnet/tools/aspire).")
    cmd = [exe, "otel", "spans", "--dashboard-url", DASHBOARD_URL, "--format", "Json"]
    if limit:
        cmd += ["--limit", str(limit)]
    with open(dest, "w") as fh:
        p = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, text=True,
                           timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"aspire export failed (exit {p.returncode}): {p.stderr[:600]}")
    with open(dest) as f:
        return json.load(f)


class FollowIngest:
    """Supervises `aspire otel spans --follow`, streaming spans into the store.

    Verified output shape: ONE COMPLETE JSON ARRAY PER LINE (each line parses
    standalone), and the stream replays the existing backlog before tailing --
    so a single mechanism covers both backfill and live updates, with no
    repeated multi-megabyte exports.

    The subprocess is restarted with backoff if it exits. Re-delivery after a
    restart is harmless because ingest is idempotent.
    """

    def __init__(self, db_path, dashboard_url=DASHBOARD_URL, on_batch=None):
        # Takes a PATH, not a Store: sqlite3 connections are bound to the thread
        # that created them, so this opens its own inside _run().
        self.db_path = db_path
        self.store = None
        self.url = dashboard_url
        self.on_batch = on_batch
        self.proc = None
        self.thread = None
        self._stop = threading.Event()
        self.started_at = None
        self.last_span_at = None
        self.spans_seen = 0
        self.spans_new = 0
        self.restarts = 0
        self.error = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="follow-ingest")
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    @property
    def alive(self):
        return bool(self.thread and self.thread.is_alive()
                    and self.proc and self.proc.poll() is None)

    def status(self):
        return {
            "alive": self.alive, "started_at": self.started_at,
            "last_span_at": self.last_span_at, "spans_seen": self.spans_seen,
            "spans_new": self.spans_new, "restarts": self.restarts,
            "error": self.error,
        }

    def _run(self):
        exe = aspire_bin()
        if not exe:
            self.error = "aspire CLI not found"
            return
        from .store import Store
        self.store = Store(self.db_path)     # owned by THIS thread
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.proc = subprocess.Popen(
                    [exe, "otel", "spans", "--dashboard-url", self.url,
                     "--format", "Json", "--follow"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1)
                self.started_at = time.time()
                self.error = None
                backoff = 1.0
                self._pump(self.proc.stdout)
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
            if self._stop.is_set():
                break
            self.restarts += 1
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        if self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass

    def _pump(self, stream):
        """Read line-delimited JSON arrays, batching to keep write pressure low."""
        batch = []
        last_flush = time.time()
        for line in stream:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue          # partial/interleaved line; idempotency covers us
            if isinstance(chunk, dict):
                chunk = [chunk]
            if not isinstance(chunk, list):
                continue
            batch.extend(chunk)
            now = time.time()
            if len(batch) >= 200 or (batch and now - last_flush > 2.0):
                self._flush(batch)
                batch, last_flush = [], now
        if batch:
            self._flush(batch)

    def _flush(self, batch):
        if self.store is None:
            return
        try:
            seen, new, _q = ingest_spans(self.store, batch, origin="follow")
        except Exception as e:
            self.error = f"ingest: {type(e).__name__}: {e}"
            return
        self.spans_seen += seen
        self.spans_new += new
        self.last_span_at = time.time()
        if new and self.on_batch:
            try:
                self.on_batch(new)
            except Exception:
                pass


def main(argv=None):
    """CLI: python3 -m agentdash.ingest [FILE ...]   (defaults to .spans/*.json)"""
    from .store import Store
    argv = list(argv if argv is not None else sys.argv[1:])
    db = "sessions.db"
    if "--db" in argv:
        i = argv.index("--db")
        db = argv[i + 1]
        del argv[i:i + 2]
    if not argv:
        argv = export_files()
        if not argv:
            print(f"no exports found in {SPANS_DIR}")
            print("usage: python3 -m agentdash.ingest [--db PATH] [FILE ...]")
            return 1
        print(f"ingesting {len(argv)} export(s) from {SPANS_DIR}")
    store = Store(db)
    for path in argv:
        seen, new, quar = ingest_file(store, path)
        print(f"{path}: {seen} spans, {new} new, {quar} quarantined")
    st = store.status()
    print(f"store: {st['spans']} spans, harnesses={st['harnesses']}, "
          f"quarantined={st['quarantined']}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
