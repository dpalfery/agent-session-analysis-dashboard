"""
store.py -- SQLite persistence for spans, token counts, and derived sessions.

Why SQLite rather than a directory of JSON:

  * Incremental ingest. `span_id` is the primary key and every insert is
    INSERT OR IGNORE, so re-ingesting an export is a no-op instead of
    "re-read 28 MB and rebuild a Python set".
  * Token cache. Measured: 0.46s of a 0.9s pipeline run was tiktoken over
    13.4 MB, recomputed every run for spans that had not changed. Counts are a
    pure function of (span_id, attr_key, encoding), so they are cached here and
    never recomputed.
  * Selectivity. In a real Docker-dashboard buffer only ~0.085% of spans carry
    LLM telemetry. Indexed lookup beats rescanning JSON.
  * Concurrency. The follow-ingest writes while the API reads. WAL handles that;
    two processes racing on session.json do not.

stdlib sqlite3 -- no new dependency.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .canonical import SCHEMA_VERSION

DEFAULT_DB = "sessions.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Raw spans, one row per span_id. Idempotent by construction.
CREATE TABLE IF NOT EXISTS span (
    span_id        TEXT PRIMARY KEY,
    trace_id       TEXT NOT NULL,
    parent_span_id TEXT,
    source         TEXT,
    harness        TEXT,              -- resolved by the adapter registry
    name           TEXT,
    op             TEXT,
    kind           TEXT,
    timestamp      TEXT NOT NULL,
    duration_ms    REAL,
    status         TEXT,
    raw            TEXT NOT NULL,     -- full original span JSON
    ingested_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_span_trace   ON span(trace_id);
CREATE INDEX IF NOT EXISTS ix_span_parent  ON span(parent_span_id);
CREATE INDEX IF NOT EXISTS ix_span_harness ON span(harness, timestamp);
CREATE INDEX IF NOT EXISTS ix_span_op      ON span(op);

-- Token counts keyed by content, never recomputed.
CREATE TABLE IF NOT EXISTS token_cache (
    span_id   TEXT NOT NULL,
    attr_key  TEXT NOT NULL,
    encoding  TEXT NOT NULL,
    n_tokens  INTEGER NOT NULL,
    PRIMARY KEY (span_id, attr_key, encoding)
) WITHOUT ROWID;

-- Spans no adapter claimed. Never guessed at, never silently parsed.
CREATE TABLE IF NOT EXISTS quarantine (
    span_id    TEXT PRIMARY KEY,
    source     TEXT,
    name       TEXT,
    namespaces TEXT,                  -- observed attribute namespaces, for triage
    reason     TEXT,
    seen_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    origin      TEXT NOT NULL,        -- file path or "follow"
    at          REAL NOT NULL,
    spans_seen  INTEGER NOT NULL,
    spans_new   INTEGER NOT NULL,
    quarantined INTEGER NOT NULL DEFAULT 0
);

-- Derived. Rebuilt per harness when an adapter version changes.
CREATE TABLE IF NOT EXISTS session (
    session_id      TEXT PRIMARY KEY,
    harness         TEXT NOT NULL,
    adapter_version INTEGER NOT NULL,
    label           TEXT,
    is_subagent     INTEGER NOT NULL DEFAULT 0,
    parent_session  TEXT,
    agent_name      TEXT,
    repo TEXT, branch TEXT, commit_sha TEXT,
    started TEXT, ended TEXT,
    payload         TEXT NOT NULL     -- full computed view payload
);
CREATE INDEX IF NOT EXISTS ix_session_harness ON session(harness, started);

CREATE TABLE IF NOT EXISTS session_span (
    session_id TEXT NOT NULL,
    span_id    TEXT NOT NULL,
    PRIMARY KEY (session_id, span_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS problem (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    span_id    TEXT,
    severity   TEXT NOT NULL,
    code       TEXT NOT NULL,
    message    TEXT NOT NULL,
    at         REAL NOT NULL,
    harness    TEXT
);
"""


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=30.0)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(problem)")}
        if "harness" not in cols:
            self.db.execute("ALTER TABLE problem ADD COLUMN harness TEXT")
            # Clear stale rows accumulated by the old session_id-scoped delete
            # (they were never cleaned and grew to thousands across rebuilds).
            # Fully regenerable on the next pipeline rebuild.
            self.db.execute("DELETE FROM problem")
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        self.db.commit()

    # -- plumbing ----------------------------------------------------------
    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def tx(self):
        try:
            yield self.db
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))

    def get_meta(self, key, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    # -- ingest ------------------------------------------------------------
    def add_spans(self, spans, harness_of, origin="unknown"):
        """Insert raw spans. Returns (seen, new, quarantined).

        `harness_of` maps a raw span dict to a harness name, or None to
        quarantine it. Existing span_ids are left untouched -- ingest is
        idempotent, so re-running an export costs nothing.
        """
        seen = new = quar = 0
        now = time.time()
        with self.tx() as db:
            for s in spans:
                seen += 1
                sid = s.get("spanId")
                if not sid:
                    continue
                harness = harness_of(s)
                if harness is None:
                    ns = sorted({a.split(".")[0] for a in (s.get("attributes") or {})})
                    cur = db.execute(
                        "INSERT OR IGNORE INTO quarantine"
                        "(span_id,source,name,namespaces,reason,seen_at) VALUES(?,?,?,?,?,?)",
                        (sid, s.get("source"), s.get("name"), ",".join(ns),
                         "no adapter matched", now))
                    quar += cur.rowcount
                    continue
                cur = db.execute(
                    "INSERT OR IGNORE INTO span"
                    "(span_id,trace_id,parent_span_id,source,harness,name,op,kind,"
                    " timestamp,duration_ms,status,raw,ingested_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, s.get("traceId"), s.get("parentSpanId"), s.get("source"),
                     harness, s.get("name"),
                     (s.get("attributes") or {}).get("gen_ai.operation.name"),
                     s.get("kind"), s.get("timestamp"), s.get("durationMs"),
                     s.get("status"), json.dumps(s, separators=(",", ":")), now))
                new += cur.rowcount
            db.execute(
                "INSERT INTO ingest_log(origin,at,spans_seen,spans_new,quarantined)"
                " VALUES(?,?,?,?,?)", (origin, now, seen, new, quar))
        return seen, new, quar

    # -- reads -------------------------------------------------------------
    def span_ids(self):
        return {r["span_id"] for r in self.db.execute("SELECT span_id FROM span")}

    def raw_spans(self, harness=None):
        q = "SELECT raw FROM span"
        args = ()
        if harness:
            q += " WHERE harness=?"
            args = (harness,)
        q += " ORDER BY timestamp"
        return [json.loads(r["raw"]) for r in self.db.execute(q, args)]

    def reclassify(self, resolver):
        """Re-run detection over stored spans and UPDATE harness where it changed.

        add_spans/INSERT OR IGNORE never revisits a stored span, so a fix to an
        adapter's detect() only routes NEW ingests -- spans already in the store
        keep their original harness. Call this once per SCHEMA_VERSION to migrate
        stored spans after a detection change. Returns the number of rows updated.
        """
        updated = 0
        with self.tx() as db:
            for r in db.execute("SELECT span_id, harness, raw FROM span"):
                sp = json.loads(r["raw"])
                new = resolver(sp)
                if new is not None and new != r["harness"]:
                    db.execute("UPDATE span SET harness=? WHERE span_id=?",
                               (new, r["span_id"]))
                    updated += 1
        return updated

    def harnesses(self):
        return [(r["harness"], r["n"]) for r in self.db.execute(
            "SELECT harness, COUNT(*) n FROM span GROUP BY harness ORDER BY n DESC")]

    def counts(self):
        one = lambda q: self.db.execute(q).fetchone()[0]
        return {
            "spans": one("SELECT COUNT(*) FROM span"),
            "sessions": one("SELECT COUNT(*) FROM session"),
            "quarantined": one("SELECT COUNT(*) FROM quarantine"),
            "token_cache": one("SELECT COUNT(*) FROM token_cache"),
            "problems": one("SELECT COUNT(*) FROM problem"),
        }

    def status(self):
        c = self.counts()
        last = self.db.execute(
            "SELECT origin,at,spans_seen,spans_new FROM ingest_log"
            " ORDER BY id DESC LIMIT 1").fetchone()
        c["last_ingest"] = dict(last) if last else None
        c["harnesses"] = {h: n for h, n in self.harnesses()}
        return c

    # -- derived -----------------------------------------------------------
    def replace_sessions(self, harness, sessions_with_payloads, problems=()):
        """Swap in a harness's derived sessions. Other harnesses are untouched."""
        with self.tx() as db:
            old = [r["session_id"] for r in db.execute(
                "SELECT session_id FROM session WHERE harness=?", (harness,))]
            if old:
                qs = ",".join("?" * len(old))
                db.execute(f"DELETE FROM session_span WHERE session_id IN ({qs})", old)
            db.execute("DELETE FROM problem WHERE harness=?", (harness,))
            db.execute("DELETE FROM session WHERE harness=?", (harness,))
            for sess, payload in sessions_with_payloads:
                db.execute(
                    "INSERT INTO session(session_id,harness,adapter_version,label,"
                    "is_subagent,parent_session,agent_name,repo,branch,commit_sha,"
                    "started,ended,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sess.session_id, harness, SCHEMA_VERSION, sess.label,
                     int(sess.is_subagent), sess.parent_session, sess.agent_name,
                     sess.repo, sess.branch, sess.commit,
                     sess.started, sess.ended,
                     json.dumps(payload, separators=(",", ":"))))
                db.executemany(
                    "INSERT OR IGNORE INTO session_span(session_id,span_id) VALUES(?,?)",
                    [(sess.session_id, sid) for sid in sess.span_ids])
            now = time.time()
            for p in problems:
                db.execute(
                    "INSERT INTO problem(session_id,span_id,severity,code,message,at,harness)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (p.session_id, p.span_id, p.severity, p.code, p.message, now,
                     harness))

    def session_list(self):
        return [dict(r) for r in self.db.execute(
            "SELECT session_id,harness,label,is_subagent,parent_session,agent_name,"
            "repo,branch,started,ended FROM session ORDER BY started")]

    def session_payload(self, session_id):
        r = self.db.execute("SELECT payload FROM session WHERE session_id=?",
                            (session_id,)).fetchone()
        return json.loads(r["payload"]) if r else None

    def problems(self, severity=None):
        q = "SELECT * FROM problem"
        args = ()
        if severity:
            q += " WHERE severity=?"
            args = (severity,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id DESC", args)]

    def quarantined(self, limit=200):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM quarantine ORDER BY seen_at DESC LIMIT ?", (limit,))]


def main(argv=None):
    """CLI: python3 -m agentdash.store --schema | --live [--db PATH]

    The schema is version-controlled HERE, as code -- there is no committed .db
    or .sql artifact. Any clone builds an empty store on first Store() call, so
    the database file is pure local data and stays gitignored. `--schema` prints
    what would be created; `--live` prints what an existing file actually has,
    which is how you spot a store built by an older version.
    """
    import sys
    argv = list(argv if argv is not None else sys.argv[1:])
    db = DEFAULT_DB
    if "--db" in argv:
        i = argv.index("--db")
        db = argv[i + 1]
        del argv[i:i + 2]
    if "--live" in argv:
        s = Store(db)
        for r in s.db.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL"
                " ORDER BY type DESC, name"):
            print(f"-- {r['type']} {r['name']}\n{r['sql']};\n")
        print(f"-- schema_version = {s.get_meta('schema_version')}")
        s.close()
    else:
        print(SCHEMA.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
