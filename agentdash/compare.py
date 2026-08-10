"""
compare.py -- put two or more harnesses side by side on canonical metrics.

The point of the canonical model is that a number from one harness can be read
against the same number from another. This module is where that claim gets
cashed in, and it is also where it is most dangerous: a comparison table invites
the reader to treat every filled cell as commensurable, so the work here is
mostly about refusing to fill cells that are not.

THREE RULES, each earned from a real way this goes wrong.

1. ABSENT IS NOT ZERO, AND THE REASON MATTERS. pi's "tools offered" is 0 only
   because it never exports tool definitions; Copilot's would be 0 if the model
   genuinely got no tools. Rendering both as 0 invents a finding ("pi offers no
   tools") that the data does not support -- and pi in fact invoked 14 distinct
   tools across 368 calls. Every metric therefore declares when it is
   MEASURABLE for a harness, separately from its value, and an unmeasurable
   metric carries the adapter's explanation of why.

2. RATIOS BEFORE TOTALS. The two corpora are whatever happened to be in the
   dashboard buffer -- here 1009 pi spans against 17 Copilot ones. Comparing
   total spend across them measures how long each was left running and nothing
   else. Totals are still shown, because the reader asked for them, but every
   total that has a meaningful per-turn or per-request form carries it too, and
   the UI leads with the normalized column.

3. COST IS ONLY COMPARABLE THROUGH ITS BASIS. Copilot's USD is derived from
   GitHub's published credit table; pi's is the figure pi computed from its own
   provider's prices. Both are USD and neither is wrong, but they are not
   measurements of the same thing, so the basis travels with the number.
"""

from . import pipeline


def _ratio(num, den):
    if num is None or not den:
        return None
    return num / den


def _summary(p):
    return (p or {}).get("summary") or {}


# ---------------------------------------------------------------------------
# The metric table.
#
# `get`     -> the value, or None
# `avail`   -> is this metric MEASURABLE for this harness? Defaults to
#              "get() is not None", which is right whenever a missing value and
#              an unreported one coincide. Where they don't -- a genuine 0 that
#              means "not exported" -- the metric says so explicitly.
# `fmt`     -> how the UI should render it
# `per`     -> denominator for the normalized column, if one makes sense
# ---------------------------------------------------------------------------
METRICS = [
    dict(key="sessions", label="Sessions", group="Volume", fmt="int",
         get=lambda p, x: x["sessions"]),
    dict(key="request_count", label="Requests", group="Volume", fmt="int",
         get=lambda p, x: _summary(p).get("request_count")),
    dict(key="turn_count", label="Model turns", group="Volume", fmt="int",
         get=lambda p, x: _summary(p).get("turn_count")),
    dict(key="turns_per_request", label="Turns per request", group="Volume", fmt="float1",
         get=lambda p, x: _ratio(_summary(p).get("turn_count"),
                                 _summary(p).get("request_count"))),
    dict(key="span_count", label="Spans", group="Volume", fmt="int",
         get=lambda p, x: (p or {}).get("span_count")),
    dict(key="duration_ms", label="Wall clock", group="Volume", fmt="duration",
         get=lambda p, x: _summary(p).get("duration_ms")),

    dict(key="total_input", label="Input tokens", group="Tokens", fmt="int",
         per="turn_count",
         get=lambda p, x: _summary(p).get("total_input")),
    dict(key="total_output", label="Output tokens", group="Tokens", fmt="int",
         per="turn_count",
         get=lambda p, x: _summary(p).get("total_output")),
    dict(key="total_cache_read", label="Cache-read tokens", group="Tokens", fmt="int",
         per="turn_count",
         get=lambda p, x: _summary(p).get("total_cache_read")),
    dict(key="cache_hit_ratio", label="Cache hit ratio", group="Tokens", fmt="pct",
         note="cache_read as a share of total input. Both harnesses store the "
              "input classes disjointly, so this means the same thing on each "
              "side despite their counters disagreeing at the source.",
         get=lambda p, x: _summary(p).get("cache_hit_ratio")),
    dict(key="total_cache_creation", label="Cache-creation tokens", group="Tokens",
         fmt="int",
         # None here genuinely means "these models emit no such counter".
         get=lambda p, x: _summary(p).get("total_cache_creation")),
    dict(key="total_reasoning", label="Reasoning tokens", group="Tokens", fmt="int",
         per="turn_count",
         get=lambda p, x: _summary(p).get("total_reasoning")),

    dict(key="tool_calls", label="Tool calls", group="Tools", fmt="int",
         per="turn_count",
         get=lambda p, x: _summary(p).get("tool_calls")),
    dict(key="tools_invoked", label="Distinct tools invoked", group="Tools", fmt="int",
         get=lambda p, x: _summary(p).get("tools_invoked")),
    dict(key="tools_offered", label="Tools offered (in schema)", group="Tools", fmt="int",
         # 0 offered alongside real invocations means the harness never exported
         # its tool definitions -- not that the model was given no tools.
         avail=lambda p, x: ((p or {}).get("coverage") or {}).get("tool_definitions", 0) > 0,
         get=lambda p, x: _summary(p).get("tools_offered")),
    dict(key="schema_tokens_per_turn", label="Tool schema tokens per turn",
         group="Tools", fmt="int",
         avail=lambda p, x: ((p or {}).get("coverage") or {}).get("tool_definitions", 0) > 0,
         get=lambda p, x: _summary(p).get("schema_tokens_per_turn")),
    dict(key="unused_schema_per_turn", label="…of which never invoked",
         group="Tools", fmt="int",
         avail=lambda p, x: ((p or {}).get("coverage") or {}).get("tool_definitions", 0) > 0,
         get=lambda p, x: _summary(p).get("unused_schema_per_turn")),

    dict(key="cost_usd", label="Cost (USD)", group="Cost", fmt="usd",
         note="Read with the basis row below -- these are USD from two "
              "different pricing sources, not one measurement.",
         get=lambda p, x: (_summary(p).get("cost") or {}).get("usd")),
    dict(key="cost_basis", label="Cost basis", group="Cost", fmt="text",
         get=lambda p, x: (_summary(p).get("cost") or {}).get("basis")),
    dict(key="usd_per_turn", label="Cost per turn", group="Cost", fmt="usd4",
         get=lambda p, x: _ratio((_summary(p).get("cost") or {}).get("usd"),
                                 _summary(p).get("turn_count"))),
    dict(key="usd_per_m_input", label="Cost per 1M input tokens", group="Cost", fmt="usd4",
         get=lambda p, x: _ratio((_summary(p).get("cost") or {}).get("usd"),
                                 (_summary(p).get("total_input") or 0) / 1e6)),

    dict(key="error_count", label="Spans with errors", group="Quality", fmt="int",
         get=lambda p, x: _summary(p).get("error_count")),
    dict(key="median_ttft_ms", label="Median time to first token", group="Quality",
         fmt="ms",
         get=lambda p, x: _summary(p).get("median_ttft_ms")),
    dict(key="structured_messages", label="Turns exporting message content",
         group="Coverage", fmt="int",
         note="How many model turns carried the conversation itself, rather "
              "than only its token counts. This is what decides whether "
              "context composition can be broken down at all.",
         get=lambda p, x: ((p or {}).get("coverage") or {}).get("structured_messages")),
    dict(key="tool_results", label="Tool calls exporting their result",
         group="Coverage", fmt="int",
         get=lambda p, x: ((p or {}).get("coverage") or {}).get("tool_results")),
]


def build(store):
    """Comparison payload across every harness with derived sessions."""
    rows = store.session_list()
    harnesses = []
    for hname, _n in store.harnesses():
        if not hname:
            continue
        agg = store.session_payload(pipeline.aggregate_id(hname))
        if agg is None:
            continue
        real = [r for r in rows
                if r["harness"] == hname and not r["session_id"].startswith("__all__")]
        harnesses.append({
            "harness": hname,
            "payload": agg,
            "extra": {"sessions": len(real)},
        })

    names = [h["harness"] for h in harnesses]
    metrics = []
    for m in METRICS:
        cells = {}
        for h in harnesses:
            p, x = h["payload"], h["extra"]
            avail_fn = m.get("avail")
            try:
                value = m["get"](p, x)
            except Exception:
                value = None
            available = avail_fn(p, x) if avail_fn else (value is not None)
            per_key = m.get("per")
            per_value = None
            if per_key and available and isinstance(value, (int, float)):
                per_value = _ratio(value, _summary(p).get(per_key))
            cells[h["harness"]] = {
                "value": value if available else None,
                "available": bool(available),
                "per_turn": per_value,
            }
        metrics.append({
            "key": m["key"], "label": m["label"], "group": m["group"],
            "fmt": m["fmt"], "note": m.get("note"),
            "per": m.get("per"), "cells": cells,
        })

    return {
        "harnesses": [{
            "harness": h["harness"],
            "sessions": h["extra"]["sessions"],
            "label": h["payload"].get("label"),
            "models": _summary(h["payload"]).get("models") or [],
            "started": _summary(h["payload"]).get("start"),
            "ended": _summary(h["payload"]).get("end"),
            "notes": h["payload"].get("notes") or [],
            "coverage": h["payload"].get("coverage") or {},
            "cost": _summary(h["payload"]).get("cost") or {},
        } for h in harnesses],
        "names": names,
        "metrics": metrics,
        "caveat":
            "Totals reflect how much of each harness happened to be in the "
            "dashboard's ring buffer when it was exported, not how much each "
            "one costs to use. Compare the per-turn and per-request columns; "
            "read the totals as sample sizes.",
    }
