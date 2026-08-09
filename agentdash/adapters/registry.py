"""
registry.py -- harness detection by attribute fingerprint.

Dispatch is by ATTRIBUTE FINGERPRINT, not by the OTel resource/source name.

Why not `source`:
  * It carries a per-instance suffix -- `opencode-4dbe5ffe`, `opencode-d09d2218`,
    `opencode-7d2eb384` are all one harness.
  * It does not track content. The bare source `opencode` held the 17 LLM spans
    while the suffixed instances held only app-internal telemetry.
  * It is not stable. The same harness changed its emitted signature after being
    reconfigured.

An unmatched span goes to `quarantine` with its observed namespaces. It is never
handed to "the adapter we happen to have", because a wrong adapter produces
numbers that look right -- the failure mode this whole design guards against.
"""

from .copilot import CopilotAdapter

# Registration order is irrelevant; the highest score wins.
ADAPTERS = [CopilotAdapter()]

# Below this, we would be guessing. Quarantine instead.
MIN_CONFIDENCE = 0.5


def by_name(name):
    for a in ADAPTERS:
        if a.name == name:
            return a
    return None


def score_span(span):
    """(adapter, confidence) for the best match, or (None, 0.0)."""
    best, best_score = None, 0.0
    for a in ADAPTERS:
        try:
            s = a.detect(span)
        except Exception:
            s = 0.0
        if s > best_score:
            best, best_score = a, s
    return (best, best_score) if best_score >= MIN_CONFIDENCE else (None, best_score)


def detect_groups(spans):
    """Assign a harness per span, in two passes.

    Pass 1 -- fingerprint vote per (source, trace_id) group. Voting per group
    rather than per span matters because most spans in a trace carry nothing
    distinguishing: an `execute_tool` span looks much like any other. The few
    spans that DO fingerprint clearly carry their whole trace with them.

    Pass 2 -- source inheritance. Some traces contain only ambiguous spans and
    get no vote at all (measured: 15 `execute_tool manage_todo_list` spans that
    carry `gen_ai.*` but no Copilot namespace, each alone in its own trace).
    Once pass 1 has CONFIDENTLY established that a source maps to a harness,
    remaining undecided spans from that same source inherit it.

    Source is still never the primary signal -- it cannot be, since one harness
    emits several sources (`opencode`, `opencode-4dbe5ffe`, ...) and those
    sources carry different content. It is only used to extend a mapping that
    fingerprints already proved. A source that never fingerprints confidently
    stays undecided, and its spans are quarantined rather than guessed at.

    Returns {span_id: harness_name_or_None}.
    """
    groups = {}
    for s in spans:
        groups.setdefault((s.get("source"), s.get("traceId")), []).append(s)

    out = {}
    source_votes = {}
    for (source, _trace), members in groups.items():
        tally = {}
        for s in members:
            a, conf = score_span(s)
            if a is not None:
                tally[a.name] = tally.get(a.name, 0.0) + conf
        winner = max(tally, key=tally.get) if tally else None
        if winner:
            bucket = source_votes.setdefault(source, {})
            bucket[winner] = bucket.get(winner, 0.0) + tally[winner]
        for s in members:
            out[s["spanId"]] = winner

    source_harness = {src: max(t, key=t.get) for src, t in source_votes.items() if t}
    for s in spans:
        if out.get(s["spanId"]) is None:
            inherited = source_harness.get(s.get("source"))
            if inherited:
                out[s["spanId"]] = inherited
    return out


def harness_resolver(spans):
    """Build a `harness_of(span)` callable for Store.add_spans."""
    mapping = detect_groups(spans)
    return lambda s: mapping.get(s.get("spanId"))
