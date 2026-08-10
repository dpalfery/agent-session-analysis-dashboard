#!/usr/bin/env python3
"""
test_pi_adapter.py -- the gate on pi's token, grouping and costing conventions.

Spans here are SYNTHETIC. Real pi spans embed repo content pulled into tool
results, and a committed fixture of them would be permanent in git history --
the same reasoning that makes the Copilot baseline a content-free digest. These
are hand-built to encode the conventions, with the shapes taken from measured
exports rather than imagined.

What this actually guards, in order of how badly it fails silently:

  1. pi's gen_ai.usage.input_tokens EXCLUDES the cache classes. Copilot's key of
     the same name includes them. Applying the wrong one produces numbers that
     look entirely ordinary -- so the test asserts both that pi's math is right
     AND that validate() catches the mixup rather than passing it through.
  2. Session membership comes from pi.session.id, not ancestry, so spans whose
     parent was evicted from the dashboard's ring buffer are still counted.
  3. rates.json is scoped by applies_to, so a model name that appears in both
     GitHub's table and a pi run is not priced at GitHub's rate.

    python3 tests/test_pi_adapter.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentdash.adapters import registry                       # noqa: E402
from agentdash.adapters.pi import PiAdapter                   # noqa: E402
from agentdash.canonical import validate_tokens               # noqa: E402
from agentdash.cost import cost_block, rates_apply_to         # noqa: E402

failures = []
checks = 0


def check(name, got, want):
    global checks
    checks += 1
    if got != want:
        failures.append(f"{name}: got {got!r}, expected {want!r}")


def ok(name, cond, detail=""):
    global checks
    checks += 1
    if not cond:
        failures.append(f"{name}{': ' + detail if detail else ''}")


SESSION = "019fe327-5818-700f-8442-cfe8848f0dc2"
COMMON = {
    "pi.session.id": SESSION,
    "pi.agent.id": "agent-1", "pi.agent.root_id": "agent-1", "pi.agent.role": "root",
    "pi.workflow.id": "workflow-1", "pi.workflow.root_agent_id": "agent-1",
    "observme.semconv.version": "0.1.0",
    "observme.capture.prompts": "false",
    "observme.capture.tool_arguments": "false",
    "observme.truncated": "false",
}


def span(span_id, name, parent, attrs, ts="2026-08-08T20:55:17.51Z", **kw):
    """A pi span. Note the 2-digit fractional second -- pi's exporter drops
    trailing zeros, which Python 3.9's fromisoformat rejects outright."""
    return {"spanId": span_id, "traceId": "trace-1", "parentSpanId": parent,
            "name": name, "source": "observme-pi-extension-41d96649",
            "kind": "Internal", "timestamp": ts, "durationMs": 100.0,
            "status": "Ok", "attributes": {**COMMON, **attrs}, **kw}


def chat(span_id, parent, run, fresh, cache_read, cache_creation, out,
         model="glm-5.2", usd="0.001", reasoning=None):
    """An LLM span whose counters follow pi's EXCLUSIVE convention."""
    total = fresh + cache_read + cache_creation + out
    a = {
        "pi.agent.run.id": run,
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model,
        "gen_ai.provider.name": "zai",
        "gen_ai.usage.input_tokens": str(fresh),
        "gen_ai.usage.cache_read.input_tokens": str(cache_read),
        "gen_ai.usage.cache_creation.input_tokens": str(cache_creation),
        "gen_ai.usage.output_tokens": str(out),
        "pi.llm.usage.total_tokens": str(total),
        "pi.llm.cost.input_usd": usd, "pi.llm.cost.output_usd": "0",
        "pi.llm.cost.cache_read_usd": "0", "pi.llm.cost.cache_write_usd": "0",
        "pi.llm.cost.total_usd": usd,
        "pi.llm.prompt.redacted": "You are an expert coding assistant.",
    }
    if reasoning is not None:
        a["gen_ai.usage.reasoning.output_tokens"] = str(reasoning)
    return span(span_id, "pi.llm.request", parent, a)


def build_spans():
    """One session: a complete run, plus a run whose root and turn parents were
    evicted -- the shape measured on a live buffer, where 25 of 1009 spans had
    lost their parent and 7 of 27 runs had lost their root span entirely."""
    s = [
        span("sess1", "pi.session", None, {"pi.session.name": "unknown",
                                           "pi.model.id.current": "glm-5.2"}),
        span("run1", "pi.agent.run", "sess1",
             {"pi.agent.run.id": "agent-run-000001", "pi.agent.run.index": "1",
              "pi.agent.run.outcome": "ok", "pi.agent.run.source": "unknown",
              "pi.agent.depth": "0"}),
        span("turn1", "pi.turn", "run1",
             {"pi.agent.run.id": "agent-run-000001", "pi.turn.index": "0",
              "pi.turn.id": "agent-run-000001-turn-000000", "pi.turn.outcome": "ok"}),
        # cache_read far exceeds input_tokens -- Copilot's subtraction would make
        # fresh negative here. Measured on 287 of 307 real spans.
        chat("chat1", "turn1", "agent-run-000001", 9400, 41856, 0, 89),
        span("tool1", "pi.tool.call", "turn1",
             {"pi.agent.run.id": "agent-run-000001", "pi.tool.name": "bash",
              "pi.tool.category": "shell", "gen_ai.tool.name": "bash",
              "gen_ai.tool.type": "function", "pi.tool.success": "true",
              "pi.tool.error": "false", "pi.tool.result.size": "9161"}),

        # Run 2: root span evicted, and the turn's parent is gone too.
        span("turn2", "pi.turn", "MISSING-PARENT",
             {"pi.agent.run.id": "agent-run-000002", "pi.turn.index": "0",
              "pi.turn.outcome": "ok"}),
        chat("chat2", "turn2", "agent-run-000002", 1200, 8000, 500, 40,
             model="gpt-5.6-luna", usd="0.002", reasoning=10),
    ]
    return s


def main():
    a = PiAdapter()
    spans = build_spans()

    # ---- 1. detection ----------------------------------------------------
    print("=== detection ===")
    mapping = registry.detect_groups(spans)
    check("every span routed to pi", set(mapping.values()), {"pi"})
    ok("copilot does not outscore pi on a pi chat span",
       a.detect(spans[3]) > registry.by_name("copilot").detect(spans[3]),
       f"pi={a.detect(spans[3])} copilot={registry.by_name('copilot').detect(spans[3])}")
    # And the reverse: a Copilot span must not be claimed by pi.
    copilot_span = {"spanId": "c1", "traceId": "t", "name": "chat",
                    "attributes": {"gen_ai.operation.name": "chat",
                                   "copilot_chat.chat_session_id": "x"}}
    check("pi does not claim a copilot span", a.detect(copilot_span), 0.0)
    print(f"  routed {len(mapping)} spans, all to pi")

    # ---- 2. the token convention ------------------------------------------
    print("\n=== token convention (EXCLUSIVE) ===")
    canon = [c for c in (a.normalize(s) for s in spans if a.is_relevant(s)) if c]
    by_id = {c.span_id: c for c in canon}
    c1 = by_id["chat1"]
    check("fresh == input_tokens, passed through", c1.tokens.fresh_input, 9400)
    check("cache_read preserved", c1.tokens.cache_read, 41856)
    check("total input is the sum of disjoint classes", c1.tokens.total_input, 51256)
    check("reported_input equals that sum", c1.tokens.reported_input, 51256)
    ok("no universal token problem", not validate_tokens(c1))

    # The failure this whole design exists to prevent: had the adapter used
    # Copilot's subtraction, fresh would be 9400-41856-0 = -32456.
    ok("copilot's math would have gone negative here",
       9400 - 41856 - 0 < 0)

    # ---- 3. validate() must CATCH the mixup, not just avoid it -------------
    print("\n=== validate() catches an inclusive/exclusive mixup ===")
    sessions = a.group(canon)
    check("one session", len(sessions), 1)
    sess = sessions[0]
    member = [by_id[i] for i in sess.span_ids]
    check("clean spans validate with no errors",
          [p for p in a.validate(sess, member) if p.severity == "error"], [])

    # Corrupt exactly as a wrong adapter would: treat input as inclusive.
    import copy
    bad = copy.deepcopy(by_id["chat1"])
    bad.tokens.fresh_input = 9400 - 41856      # the Copilot subtraction
    bad_member = [bad if m.span_id == "chat1" else m for m in member]
    probs = [p for p in a.validate(sess, bad_member) if p.severity == "error"]
    ok("mixup produces a validation error", probs, "validate() let it through")
    if probs:
        check("and names the right failure", probs[0].code, "token_total_mismatch")
        print(f"  caught: {probs[0].message[:96]}...")

    # ---- 4. grouping survives an evicted parent ---------------------------
    print("\n=== grouping by attribute, not ancestry ===")
    check("all spans claimed by the session", len(sess.span_ids), len(canon))
    check("both runs became requests", len(sess.requests), 2)
    orphan = by_id["turn2"]
    ok("span with a missing parent is still in the session",
       orphan.span_id in sess.span_ids)
    ok("its chat resolves to its run despite no ancestry",
       a.nearest_root("chat1", by_id) == "run1",
       f"got {a.nearest_root('chat1', by_id)}")
    # Run 2's root was evicted, so there is nothing to resolve to -- and the
    # request still exists rather than the turns being dropped.
    check("evicted root resolves to None", a.nearest_root("chat2", by_id), None)
    ok("the evicted run still has a request",
       any(r.root_span_id.endswith("agent-run-000002") for r in sess.requests))
    print(f"  {len(sess.span_ids)} spans, {len(sess.requests)} requests, 0 dropped")

    # ---- 5. op mapping ----------------------------------------------------
    print("\n=== op mapping ===")
    ops = {}
    for c in canon:
        ops[c.op] = ops.get(c.op, 0) + 1
    check("one chat per llm.request", ops.get("chat"), 2)
    check("one execute_tool per tool.call", ops.get("execute_tool"), 1)
    check("one invoke_agent per surviving run", ops.get("invoke_agent"), 1)
    # pi.turn WRAPS pi.llm.request; opping it as chat would double every turn.
    check("turn/session spans stay structural", ops.get(None), 3)

    # ---- 6. cost scoping ---------------------------------------------------
    print("\n=== cost basis ===")
    github = {"applies_to": ["copilot"], "credit_usd": 0.01, "models": {
        "gpt-5.6-luna": {"credits_per_1m_input": 20, "credits_per_1m_output": 120}}}
    ok("github's table does not apply to pi", not rates_apply_to(github, "pi"))
    ok("github's table does apply to copilot", rates_apply_to(github, "copilot"))

    turns = [{"model": c.model, "input": c.tokens.reported_input,
              "output": c.tokens.output, "fresh": c.tokens.fresh_input,
              "cache_read": c.tokens.cache_read,
              "cache_creation": c.tokens.cache_creation,
              "reported_usd": c.reported_cost_usd}
             for c in canon if c.op == "chat"]
    block = cost_block(turns, github, harness="pi")
    check("basis is the harness's own figure", block["basis"], "harness_reported")
    check("no credits are invented", block["credits"], None)
    ok("usd equals what pi reported", abs(block["usd"] - 0.003) < 1e-9,
       f"got {block['usd']}")
    ok("the rate table was recorded as out of scope", block["rates_in_scope"] is False)
    # gpt-5.6-luna is in the table AND in this pi run. Unguarded, it would price.
    luna = next(r for r in block["by_model"] if r["model"] == "gpt-5.6-luna")
    check("the colliding model is not priced from the table", luna["credits"], None)
    check("it is marked out of scope", luna["status"], "out_of_scope")
    print(f"  pi: ${block['usd']:.6f} ({block['basis']}), 0 credits invented")

    # ---- 7. notes ----------------------------------------------------------
    print("\n=== coverage notes ===")
    notes = a.notes(canon)
    ok("explains the redaction default", any("capture.prompts" in n for n in notes))
    ok("explains the missing tool definitions",
       any("tool definitions" in n for n in notes))
    print(f"  {len(notes)} note(s) surfaced")

    print()
    if failures:
        print(f"PI ADAPTER FAILED -- {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PI ADAPTER OK -- {checks} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
