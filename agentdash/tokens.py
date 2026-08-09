"""
tokens.py -- cache-backed tokenization.

Token counts are a pure function of (span_id, attr_key, encoding), so they are
computed once and stored. Measured on the Copilot corpus: 0.46s of a 0.9s
pipeline run was tiktoken over 13.4 MB, recomputed on every run even though
268 of 273 spans had not changed. With the cache, a re-run tokenizes only spans
it has never seen.

o200k_base is a PROXY and fidelity varies sharply by model -- measured 2.8-4.4%
unattributed residual on grok-4.5 but 35-41% on claude-sonnet-5, because neither
publishes its tokenizer. Counts are directionally right for comparing things
against each other; they are not billing-exact. The UI must keep saying so.
"""

import sys

ENCODING = "o200k_base"

try:
    import tiktoken
    _ENC = tiktoken.get_encoding(ENCODING)
except Exception as e:  # stop rather than silently degrade to len/4
    print(f"FATAL: tiktoken unavailable ({e}).", file=sys.stderr)
    print("Install it: pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(1)


def count(text) -> int:
    """Uncached count. Prefer Counter.count() so results are memoized."""
    if not text:
        return 0
    return len(_ENC.encode(text, disallowed_special=()))


class Counter:
    """Tokenizer with a SQLite-backed memo keyed by (span_id, attr_key)."""

    def __init__(self, store, encoding=ENCODING):
        self.store = store
        self.encoding = encoding
        self._mem = {}          # in-process memo for this run
        self.hits = 0
        self.misses = 0
        self._pending = []
        if store is not None:
            for r in store.db.execute(
                    "SELECT span_id,attr_key,n_tokens FROM token_cache WHERE encoding=?",
                    (encoding,)):
                self._mem[(r["span_id"], r["attr_key"])] = r["n_tokens"]

    def count(self, span_id, attr_key, text) -> int:
        """Token count for one span attribute, memoized."""
        if not text:
            return 0
        key = (span_id, attr_key)
        got = self._mem.get(key)
        if got is not None:
            self.hits += 1
            return got
        self.misses += 1
        n = len(_ENC.encode(text, disallowed_special=()))
        self._mem[key] = n
        self._pending.append((span_id, attr_key, self.encoding, n))
        return n

    def count_text(self, text) -> int:
        """Count text with no stable identity (a slice of a larger attribute).

        Not cached -- caching would need a content hash, and hashing costs about
        as much as encoding for the short strings this is used on.
        """
        return count(text)

    def flush(self):
        """Persist newly computed counts."""
        if not self._pending or self.store is None:
            return 0
        with self.store.tx() as db:
            db.executemany(
                "INSERT OR REPLACE INTO token_cache(span_id,attr_key,encoding,n_tokens)"
                " VALUES(?,?,?,?)", self._pending)
        n = len(self._pending)
        self._pending = []
        return n

    @property
    def stats(self):
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else None,
            "encoding": self.encoding,
        }
