"""
pipeline.py -- rebuild derived sessions from stored spans.

Per harness: normalize -> group -> validate -> build payloads -> persist.

A session with an "error" Problem is still stored (so the UI can show what
broke) but is flagged. Silence is the failure mode this project keeps guarding
against: a wrong adapter produces numbers that look entirely reasonable.
"""

import json
import sys

from . import views
from .adapters import registry
from .canonical import SCHEMA_VERSION, validate_tokens
from .cost import load_rates
from .tokens import Counter


def rebuild(store, rates_path="rates.json", harness=None, verbose=True):
    # add_spans/INSERT OR IGNORE never revisits a stored span, so a detection fix
    # only routes new ingests. Migrate stored spans once per schema version.
    if store.get_meta("harness_reclassify_version") != str(SCHEMA_VERSION):
        store.reclassify(registry.harness_resolver(store.raw_spans()))
        store.set_meta("harness_reclassify_version", str(SCHEMA_VERSION))
    rates = load_rates(rates_path)
    counter = Counter(store)
    results = {}

    targets = [harness] if harness else [h for h, _n in store.harnesses() if h]
    for hname in targets:
        adapter = registry.by_name(hname)
        if adapter is None:
            if verbose:
                print(f"  {hname}: no adapter registered, skipping", file=sys.stderr)
            continue

        raw = store.raw_spans(harness=hname)
        canon = []
        for s in raw:
            if not adapter.is_relevant(s):
                continue
            c = adapter.normalize(s, counter)
            if c is not None:
                canon.append(c)

        sessions = adapter.group(canon)
        by_id = {c.span_id: c for c in canon}

        # Token invariants apply to EVERY normalized span, including ones that
        # belong to no session. Orphans (title generation, next-edit
        # suggestions, background agents) still carry token counters, and a
        # decomposition bug there is the same bug -- it just would not surface
        # if validation only ran over session members.
        span_problems = []
        for c in canon:
            span_problems.extend(validate_tokens(c))

        # ground-truth MCP server names, for attributing definition-only tools
        known_servers = {c.mcp_server for c in canon if c.mcp_server}

        out, problems = [], list(span_problems)
        claimed = set()
        for sess in sessions:
            member = [by_id[sid] for sid in sess.span_ids if sid in by_id]
            claimed.update(sess.span_ids)
            probs = adapter.validate(sess, member)
            for p in probs:
                p.session_id = sess.session_id
            problems.extend(probs)
            payload = views.build_payload(sess, member, adapter, rates,
                                          counter, known_servers)
            payload["problems"] = [
                {"severity": p.severity, "code": p.code, "message": p.message}
                for p in probs]
            out.append((sess, payload))

        # aggregate across this harness's sessions
        if out:
            agg_spans = [c for c in canon if c.span_id in claimed]
            agg_sess = _aggregate_session(hname, sessions, agg_spans)
            agg_payload = views.build_payload(agg_sess, agg_spans, adapter, rates,
                                              counter, known_servers)
            agg_payload["problems"] = []
            out.append((agg_sess, agg_payload))

        orphans = [c for c in canon if c.span_id not in claimed]
        store.replace_sessions(hname, out, problems)
        store.set_meta(f"meta:{hname}", json.dumps({
            "harness": hname,
            "adapter_version": adapter.version,
            "notes": adapter.notes(canon),
            "orphans": _orphan_summary(orphans),
            **_attribute_presence(raw, getattr(adapter, "watched_attributes", None)),
        }))
        store.db.commit()
        results[hname] = {
            "spans": len(canon), "sessions": len(sessions),
            "orphans": len(orphans),
            "errors": len([p for p in problems if p.severity == "error"]),
            "orphan_detail": _orphan_summary(orphans),
        }
        if verbose:
            r = results[hname]
            print(f"  {hname}: {r['sessions']} sessions, {r['spans']} spans, "
                  f"{r['orphans']} orphans, {r['errors']} validation errors")
            for p in problems:
                if p.severity == "error":
                    print(f"    ERROR {p}", file=sys.stderr)

    n = counter.flush()
    if verbose:
        st = counter.stats
        print(f"  tokens: {st['hits']} cached, {st['misses']} computed"
              + (f" ({st['hit_rate']:.0%} hit rate)" if st["hit_rate"] is not None else "")
              + f", {n} newly persisted")
    return results


def aggregate_id(harness):
    """The combined-view session id for one harness.

    Harness-scoped because session_id is the primary key and every harness
    builds an aggregate: with a bare "__all__" the second harness to rebuild
    collided with the first, and replace_sessions() only clears its own
    harness's rows so the insert failed outright.
    """
    return f"__all__:{harness}"


def _aggregate_session(harness, sessions, spans):
    from .canonical import Session, Request
    reqs = [r for s in sessions for r in s.requests]
    reqs.sort(key=lambda r: r.timestamp or "")
    times = sorted(s.timestamp for s in spans if s.timestamp)
    return Session(
        session_id=aggregate_id(harness), harness=harness,
        label=f"All {len(sessions)} {harness} sessions combined",
        requests=reqs, span_ids=[s.span_id for s in spans],
        started=times[0] if times else None, ended=times[-1] if times else None)


# Fallback watch list. Each adapter overrides it with the attributes that are
# meaningful for ITS harness -- reporting a Copilot attribute as "missing" from
# a pi export says nothing, since it was never going to be there.
WATCHED_ATTRS = [
    "gen_ai.usage.cache_creation.input_tokens",
    "error.type",
]
KNOWN_OPS = ("invoke_agent", "chat", "execute_tool", "execute_hook", "embeddings")


def _attribute_presence(raw_spans, watched=None):
    import collections
    present = collections.Counter()
    ops = set()
    for s in raw_spans:
        present.update((s.get("attributes") or {}).keys())
        o = (s.get("attributes") or {}).get("gen_ai.operation.name")
        if o:
            ops.add(o)
    n = len(raw_spans)
    watched = watched or WATCHED_ATTRS
    return {
        "span_count": n,
        "missing_attributes": [a for a in watched if present.get(a, 0) == 0],
        "partial_attributes": {a: present[a] for a in watched
                               if 0 < present.get(a, 0) < n},
        "absent_span_types": [o for o in KNOWN_OPS if o not in ops],
    }


def _orphan_summary(orphans):
    import collections
    return {
        "span_count": len(orphans),
        "by_op": dict(collections.Counter(o.op for o in orphans)),
        "by_agent": dict(collections.Counter(o.agent_name or "(none)" for o in orphans)),
        "models": dict(collections.Counter(o.model for o in orphans if o.model)),
        "total_input": sum((o.tokens.reported_input or 0) for o in orphans),
        "total_output": sum((o.tokens.reported_output or 0) for o in orphans),
        "note": "Spans with no invoke_agent ancestor -- harness-internal calls. "
                "Real token spend, but not part of any agent session.",
    }


def main(argv=None):
    from .store import Store
    argv = list(argv if argv is not None else sys.argv[1:])
    db = "sessions.db"
    if "--db" in argv:
        i = argv.index("--db")
        db = argv[i + 1]
        del argv[i:i + 2]
    store = Store(db)
    print("rebuilding derived sessions:")
    rebuild(store)
    st = store.status()
    print(f"store: {st['spans']} spans, {st['sessions']} session rows, "
          f"{st['quarantined']} quarantined, {st['problems']} problems")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
