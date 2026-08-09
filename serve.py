#!/usr/bin/env python3
"""
serve.py -- local API + static host for the dashboard.

    python3 serve.py            # then open http://localhost:8899/

The UI is served-only: dashboard.html is a static shell that fetches its data
from this process. That keeps captured telemetry out of the HTML entirely (so
the UI can be tracked in git) and lets the page update without a rebuild.

Live updates come from a supervised `aspire otel spans --follow` subprocess that
streams new spans into SQLite as they arrive. The browser polls /api/status --
a local, ~200-byte call -- and refetches only when the span counter moves. It
never triggers a multi-megabyte re-export just to check for changes.

Binds 127.0.0.1 only. It runs subprocesses; do not expose it.
"""

import json
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agentdash import ingest, pipeline  # noqa: E402
from agentdash.cost import load_rates  # noqa: E402
from agentdash.store import Store  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(ROOT, "ui")
PORT = int(os.environ.get("PORT", "8899"))
DB = os.environ.get("AGENTDASH_DB", os.path.join(ROOT, "sessions.db"))
FOLLOW = os.environ.get("AGENTDASH_FOLLOW", "1") not in ("0", "false", "no")

_lock = threading.Lock()
_state = {"rebuilding": False, "last_rebuild": None, "generation": 0, "error": None}


def store():
    """One connection per thread -- sqlite3 objects are not shareable."""
    tl = threading.local()
    if not hasattr(tl, "s"):
        tl.s = Store(DB)
    return tl.s


def rebuild_now():
    """Recompute derived sessions. Serialized; safe to call from any thread."""
    with _lock:
        _state["rebuilding"] = True
        try:
            s = Store(DB)
            try:
                res = pipeline.rebuild(s, verbose=False)
            finally:
                s.close()
            _state["last_rebuild"] = time.time()
            _state["generation"] += 1
            _state["error"] = None
            return res
        except Exception as e:
            _state["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            _state["rebuilding"] = False


class _Debounce:
    """Coalesce follow-ingest bursts into at most one rebuild every `delay`s."""

    def __init__(self, delay=3.0):
        self.delay = delay
        self.timer = None

    def trigger(self, _n=0):
        if self.timer and self.timer.is_alive():
            return
        self.timer = threading.Timer(self.delay, self._fire)
        self.timer.daemon = True
        self.timer.start()

    def _fire(self):
        try:
            rebuild_now()
        except Exception:
            pass


_debounce = _Debounce()
follower = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=UI_DIR, **kw)

    # -- helpers -----------------------------------------------------------
    def _send(self, body, code=200, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=500):
        self._send({"ok": False, "error": msg}, code)

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self._api_get(path)
        if path in ("/", ""):
            self.path = "/dashboard.html"
        return super().do_GET()

    def _api_get(self, path):
        try:
            s = Store(DB)
            try:
                if path == "/api/status":
                    st = s.status()
                    st["follow"] = follower.status() if follower else {"alive": False,
                                                                       "error": "disabled"}
                    st["generation"] = _state["generation"]
                    st["rebuilding"] = _state["rebuilding"]
                    st["last_rebuild"] = _state["last_rebuild"]
                    st["error"] = _state["error"]
                    return self._send(st)

                if path == "/api/sessions":
                    rows = s.session_list()
                    out = []
                    for r in rows:
                        p = s.session_payload(r["session_id"]) or {}
                        summ = p.get("summary", {})
                        out.append({
                            **r,
                            "turn_count": summ.get("turn_count"),
                            "request_count": summ.get("request_count"),
                            "total_input": summ.get("total_input"),
                            "total_output": summ.get("total_output"),
                            "cost_usd": (summ.get("cost") or {}).get("usd"),
                            "models": summ.get("models"),
                            "problems": len(p.get("problems") or []),
                        })
                    return self._send({"sessions": out,
                                       "generation": _state["generation"]})

                if path.startswith("/api/session/"):
                    sid = unquote(path[len("/api/session/"):])
                    p = s.session_payload(sid)
                    if p is None:
                        return self._err(f"no such session: {sid}", 404)
                    return self._send(p)

                if path == "/api/rates":
                    return self._send(load_rates(os.path.join(ROOT, "rates.json")))

                if path == "/api/meta":
                    # Global context the views annotate with: rate provenance,
                    # tokenizer identity, attribute coverage, out-of-session spend.
                    from agentdash.tokens import ENCODING
                    rates = load_rates(os.path.join(ROOT, "rates.json"))
                    per_harness = {}
                    for h, _n in s.harnesses():
                        blob = s.get_meta(f"meta:{h}")
                        if blob:
                            per_harness[h] = json.loads(blob)
                    ing = [dict(r) for r in s.db.execute(
                        "SELECT origin, SUM(spans_seen) seen, SUM(spans_new) new"
                        " FROM ingest_log GROUP BY origin ORDER BY MIN(id)")]
                    return self._send({
                        "span_count": s.counts()["spans"],
                        "quarantined": s.counts()["quarantined"],
                        "tokenizer": {
                            "kind": f"tiktoken/{ENCODING}",
                            "note": "Proxy tokenizer -- models here do not publish "
                                    "theirs; counts are directionally correct, not "
                                    "billing-exact.",
                        },
                        "rates": {"credit_usd": rates.get("credit_usd"),
                                  "source": rates.get("source"),
                                  "retrieved": rates.get("retrieved"),
                                  "note": rates.get("note")},
                        "harnesses": per_harness,
                        "sources": ing,
                    })

                if path == "/api/quarantine":
                    return self._send({"spans": s.quarantined()})

                if path == "/api/problems":
                    return self._send({"problems": s.problems()})

                return self._err("not found", 404)
            finally:
                s.close()
        except Exception as e:
            return self._err(f"{type(e).__name__}: {e}")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/refresh":
            return self._refresh()
        if path == "/api/rebuild":
            try:
                res = rebuild_now()
                return self._send({"ok": True, "result": res,
                                   "generation": _state["generation"]})
            except Exception as e:
                return self._err(f"{type(e).__name__}: {e}")
        self._err("not found", 404)

    def _refresh(self):
        """Force a full export + ingest + rebuild (the manual reconcile path)."""
        try:
            s = Store(DB)
            try:
                before = s.counts()["spans"]
                dest = os.path.join(ingest.spans_dir(),
                                    f"spans-{time.strftime('%Y%m%d-%H%M%S')}.json")
                spans = ingest.export_once(dest)
                seen, new, quar = ingest.ingest_spans(
                    s, spans, origin=os.path.basename(dest))
                # Keep the raw export only when it actually contributed spans.
                if not new:
                    os.remove(dest)
                after = s.counts()["spans"]
            finally:
                s.close()
            if new:
                rebuild_now()
            return self._send({
                "ok": True,
                "file": os.path.basename(dest) if new else "(discarded — no new spans)",
                "exported_spans": seen, "new_spans": new, "quarantined": quar,
                "total_spans": after, "before": before,
                "generation": _state["generation"],
            })
        except Exception as e:
            return self._err(f"{type(e).__name__}: {e}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/status" in (args[0] if args else ""):
            return                       # status polls every few seconds; stay quiet
        sys.stderr.write("  %s\n" % (fmt % args))


def main():
    global follower
    s = Store(DB)
    counts = s.counts()
    s.close()
    if counts["spans"] and not counts["sessions"]:
        print("building derived sessions...")
        rebuild_now()

    if FOLLOW:
        follower = ingest.FollowIngest(DB, on_batch=_debounce.trigger)
        follower.start()

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"dashboard  http://localhost:{PORT}/")
    print(f"db         {DB}  ({counts['spans']} spans, {counts['sessions']} session rows)")
    print(f"follow     {'on' if FOLLOW else 'off (AGENTDASH_FOLLOW=0)'}")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        if follower:
            follower.stop()


if __name__ == "__main__":
    main()
