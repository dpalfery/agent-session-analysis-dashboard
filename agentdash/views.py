"""
views.py -- canonical spans -> the JSON payload the UI renders.

Harness-independent by construction: this module reads CanonicalSpan fields and
canonical content keys only. If a harness name or a raw OTel attribute key ever
appears here, the abstraction has leaked and the adapter is doing too little.

Payload shape is unchanged from the previous pipeline so the parity gate can
diff old against new.
"""

import collections
import json
import statistics
from datetime import datetime

from . import cost as costing

# Bucketing heuristics that are genuinely generic (they operate on canonical
# message parts). Harness-specific tag lists live in the adapter.
DEFAULT_INSTRUCTION_TAGS = [
    "environment_info", "workspace_info", "instructions",
    "copilot_instructions", "context", "reminderInstructions",
    "editor_context", "tool_use_instructions", "notebook_info",
]
FRESH_JUMP_THRESHOLD = 0.25
MCP_PREFIX = "mcp_"


def _parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def tool_def_name(t):
    return t.get("name") or t.get("function", {}).get("name") or "<unnamed>"


def split_mcp(name, known_servers):
    """(server, is_mcp). Ground-truth servers win; otherwise first segment."""
    if not name.startswith(MCP_PREFIX):
        return None, False
    rest = name[len(MCP_PREFIX):]
    for srv in sorted(known_servers, key=len, reverse=True):
        if rest.startswith(srv + "_"):
            return srv, True
    return rest.split("_")[0], True


def strip_instruction_blocks(text, tags=DEFAULT_INSTRUCTION_TAGS):
    """Split text into (harness-injected context, remaining user text)."""
    instr, remain, i = [], [], 0
    while i < len(text):
        hit = None
        for tag in tags:
            open_t = f"<{tag}>"
            if text.startswith(open_t, i):
                close_t = f"</{tag}>"
                end = text.find(close_t, i)
                hit = end + len(close_t) if end != -1 else len(text)
                break
        if hit:
            instr.append(text[i:hit])
            i = hit
        else:
            nxt = len(text)
            for tag in tags:
                j = text.find(f"<{tag}>", i + 1)
                if j != -1:
                    nxt = min(nxt, j)
            remain.append(text[i:nxt])
            i = nxt
    return "".join(instr), "".join(remain)


def bucket_context(span, known_servers, counter):
    """What occupied the context window for one chat span."""
    sysinstr = span.content.get("system_instructions")
    sys_tok = (counter.count(span.span_id, "system_instructions", sysinstr)
               if sysinstr is not None else None)

    defs = span.content.get("tool_definitions")
    tool_buckets = collections.OrderedDict()
    if defs is None:
        tool_buckets["Tool definitions"] = None
    else:
        per = collections.Counter()
        for t in defs:
            srv, is_mcp = split_mcp(tool_def_name(t), known_servers)
            per["mcp: " + srv if is_mcp else "built-in tools"] += \
                counter.count_text(json.dumps(t))
        for k in sorted(per, key=lambda k: (-per[k], k)):
            tool_buckets[k] = per[k]

    instr_tok = hist_tok = result_tok = 0
    for m in (span.content.get("input_messages") or []):
        role = m.get("role")
        for part in m.get("parts") or []:
            ptype = part.get("type")
            if ptype == "text":
                text = part.get("text") or ""
                if role == "user":
                    ins, rem = strip_instruction_blocks(text)
                    instr_tok += counter.count_text(ins)
                    hist_tok += counter.count_text(rem)
                else:
                    hist_tok += counter.count_text(text)
            elif ptype == "tool_result":
                # Bucket on part TYPE, never role -- harnesses disagree on role.
                result_tok += counter.count_text(json.dumps(part.get("raw")))
            else:
                hist_tok += counter.count_text(json.dumps(part.get("raw", part)))

    out = collections.OrderedDict()
    out["System prompt"] = sys_tok
    for k, v in tool_buckets.items():
        out[k] = v
    out["Instruction files / workspace context"] = instr_tok
    # Skill descriptions cannot be isolated in the context window: the catalogue
    # is not separable from the system prompt. null, not zero.
    out["Skill descriptions"] = None
    out["Conversation history"] = hist_tok
    out["File contents via tool results"] = result_tok
    return out


def _request_text(session, root_span):
    """Canonical request text for the root that starts a turn."""
    for r in session.requests:
        if r.root_span_id == root_span.span_id:
            return r.text or ""
    return ""


def build_payload(session, spans, adapter, rates, counter, known_servers, meta=None):
    """Full view payload for one session."""
    spans = sorted(spans, key=lambda s: s.timestamp or "")
    by_id = {s.span_id: s for s in spans}
    roots = [s for s in spans if s.op == "invoke_agent"]

    chats_all = [s for s in spans if s.op == "chat"]
    chats = [s for s in chats_all if not s.is_auxiliary]
    aux_chats = [s for s in chats_all if s.is_auxiliary]
    tools = [s for s in spans if s.op == "execute_tool"]
    hooks = [s for s in spans if s.op == "execute_hook"]
    embeds = [s for s in spans if s.op == "embeddings"]

    req_start = {}
    for r in sorted(roots, key=lambda x: x.timestamp or ""):
        for i, c in enumerate(chats):
            if (c.timestamp or "") >= (r.timestamp or ""):
                req_start.setdefault(i, r)
                break

    # ---- per-turn token spend --------------------------------------------
    turns, cum, prev_fresh = [], 0, None
    for i, s in enumerate(chats):
        t = s.tokens
        inp = t.reported_input
        cum += inp or 0
        jump = None
        if prev_fresh not in (None, 0) and t.fresh_input is not None:
            d = (t.fresh_input - prev_fresh) / prev_fresh
            if d > FRESH_JUMP_THRESHOLD:
                jump = round(d * 100)
        prev_fresh = t.fresh_input
        r = req_start.get(i)
        turns.append({
            "index": i + 1, "spanId": s.span_id, "timestamp": s.timestamp,
            "durationMs": s.duration_ms, "model": s.model,
            "input": inp, "output": t.output,
            "cache_read": t.cache_read, "cache_creation": t.cache_creation,
            "fresh": t.fresh_input, "reasoning": t.reasoning,
            "visible_output": t.visible_output,
            "cumulative_input": cum, "fresh_jump_pct": jump,
            "finish_reasons": s.finish_reasons, "ttft_ms": s.ttft_ms,
            "has_tool_defs": "tool_definitions" in s.content,
            "request_start": _request_text(session, r)[:70] if r else None,
        })

    # ---- tool cost ranking -----------------------------------------------
    invoked = collections.Counter(s.tool_name for s in tools)
    result_tok, result_missing = collections.Counter(), collections.Counter()
    for s in tools:
        r = s.content.get("tool_call_result")
        if r is None:
            result_missing[s.tool_name] += 1
        else:
            result_tok[s.tool_name] += counter.count(s.span_id, "tool_call_result", r)

    resident, schema_tok, tool_type = collections.Counter(), {}, {}
    defs_turns = 0
    for s in chats:
        defs = s.content.get("tool_definitions")
        if defs is None:
            continue
        defs_turns += 1
        for t in defs:
            n = tool_def_name(t)
            resident[n] += 1
            schema_tok[n] = counter.count_text(json.dumps(t))
            tool_type[n] = t.get("type", "function")

    tool_rows = []
    for n in set(schema_tok) | set(invoked):
        st, res, inv = schema_tok.get(n), resident.get(n, 0), invoked.get(n, 0)
        total = None if st is None else st * res
        srv, is_mcp = split_mcp(n, known_servers)
        tool_rows.append({
            "name": n, "server": srv if is_mcp else "built-in", "is_mcp": is_mcp,
            "type": tool_type.get(n, "extension" if is_mcp else "function"),
            "schema_tokens": st, "invocations": inv, "turns_resident": res,
            "total_schema_cost": total,
            "cost_per_invocation": None if total is None else round(total / max(inv, 1), 1),
            "result_tokens": result_tok.get(n, 0) if n in invoked else None,
            "results_not_recorded": result_missing.get(n, 0),
            "in_definitions": st is not None,
        })
    tool_rows.sort(key=lambda r: (-(r["total_schema_cost"] or 0), r["name"]))

    servers = collections.OrderedDict()
    for r in tool_rows:
        g = servers.setdefault(r["server"], {
            "server": r["server"], "is_mcp": r["is_mcp"], "tools": 0,
            "schema_tokens": 0, "total_schema_cost": 0,
            "invocations": 0, "unused_tools": 0, "unused_cost": 0})
        g["tools"] += 1
        g["schema_tokens"] += r["schema_tokens"] or 0
        g["total_schema_cost"] += r["total_schema_cost"] or 0
        g["invocations"] += r["invocations"]
        if r["invocations"] == 0 and r["in_definitions"]:
            g["unused_tools"] += 1
            g["unused_cost"] += r["total_schema_cost"] or 0
    server_rows = sorted(servers.values(), key=lambda g: -g["total_schema_cost"])

    # ---- context composition ---------------------------------------------
    ctx_spans = [s for s in chats if "input_messages" in s.content]
    context = {"first": None, "last": None}
    if ctx_spans:
        context["first"] = {"turn": chats.index(ctx_spans[0]) + 1,
                            "buckets": bucket_context(ctx_spans[0], known_servers, counter),
                            "reported_input": ctx_spans[0].tokens.reported_input}
        if len(ctx_spans) > 1:
            context["last"] = {"turn": chats.index(ctx_spans[-1]) + 1,
                               "buckets": bucket_context(ctx_spans[-1], known_servers, counter),
                               "reported_input": ctx_spans[-1].tokens.reported_input}

    # ---- summary ----------------------------------------------------------
    tot_in = sum(t["input"] or 0 for t in turns)
    tot_out = sum(t["output"] or 0 for t in turns)
    tot_cr = sum(t["cache_read"] or 0 for t in turns)
    cc_turns = [t for t in turns if t["cache_creation"] is not None]
    tot_cc = sum(t["cache_creation"] for t in cc_turns) if cc_turns else None
    ttfts = [t["ttft_ms"] for t in turns if t["ttft_ms"] is not None]
    times = [_parse_ts(s.timestamp) for s in spans if s.timestamp]
    errors = [s for s in spans if s.error_type] + \
             [s for s in spans if s.status not in (None, "Ok", "Unset")]
    skills = collections.Counter(s.skill_name for s in tools if s.skill_name)

    summary = {
        "total_input": tot_in, "total_output": tot_out,
        "total_cache_read": tot_cr, "total_cache_creation": tot_cc,
        "cache_creation_coverage": f"{len(cc_turns)}/{len(turns)}" if turns else None,
        "total_reasoning": sum(t["reasoning"] or 0 for t in turns) or None,
        # All input classes are disjoint subsets of the reported total, so the
        # requested ratio reduces to cache_read / input_tokens either way.
        "cache_hit_ratio": (tot_cr / tot_in) if tot_in else None,
        "turn_count": len(turns), "request_count": len(roots),
        "reported_turn_count": sum(r.reported_turns or 0 for r in session.requests) or None,
        "duration_ms": int((max(times) - min(times)).total_seconds() * 1000) if len(times) > 1 else None,
        "start": min(times).isoformat() if times else None,
        "end": max(times).isoformat() if times else None,
        "models": sorted({t["model"] for t in turns if t["model"]}),
        "aux_chat_calls": len(aux_chats),
        "aux_models": sorted({s.model for s in aux_chats if s.model}),
        "aux_input": sum((s.tokens.reported_input or 0) for s in aux_chats),
        "aux_output": sum((s.tokens.reported_output or 0) for s in aux_chats),
        "tool_calls": len(tools), "hook_calls": len(hooks),
        "embedding_calls": len(embeds), "error_count": len(errors),
        "median_ttft_ms": statistics.median(ttfts) if ttfts else None,
        "tools_offered": len(schema_tok),
        "tools_invoked": len([r for r in tool_rows if r["invocations"] > 0]),
        "schema_tokens_per_turn": sum(schema_tok.values()) or None,
        "unused_schema_per_turn": sum(v for k, v in schema_tok.items()
                                      if invoked.get(k, 0) == 0) or None,
        "defs_turns": defs_turns,
        "skills_invoked": dict(skills) or None,
    }

    for t in turns:
        c, st = costing.turn_credits(t, rates)
        t["credits"] = c
        t["cost_status"] = st
        t["usd"] = None if (c is None or rates.get("credit_usd") is None) \
            else c * rates["credit_usd"]
    summary["cost"] = costing.cost_block(turns, rates)
    summary["schema_waste_cost"] = costing.schema_waste_cost(
        summary["unused_schema_per_turn"], defs_turns, turns, rates)

    # ---- timeline ---------------------------------------------------------
    kids = collections.defaultdict(list)
    tl_roots = []
    for s in spans:
        (kids[s.parent_span_id] if s.parent_span_id in by_id else tl_roots).append(s)
    t0 = min(times) if times else None

    def node(s):
        return {
            "spanId": s.span_id, "traceId": s.trace_id, "name": s.name,
            "op": s.op, "kind": s.kind, "durationMs": s.duration_ms,
            "offsetMs": int((_parse_ts(s.timestamp) - t0).total_seconds() * 1000)
                        if t0 and s.timestamp else 0,
            "status": s.status,
            "input": s.tokens.reported_input, "output": s.tokens.reported_output,
            "tool": s.tool_name,
            "attributes": {k: (v[:2000] + f"... [truncated, {len(v)} chars]"
                               if isinstance(v, str) and len(v) > 2000 else v)
                           for k, v in s.raw_attributes.items()},
            "children": [node(c) for c in sorted(kids.get(s.span_id, []),
                                                 key=lambda x: x.timestamp or "")],
        }

    timeline = [node(s) for s in sorted(tl_roots, key=lambda x: x.timestamp or "")]

    # ---- reconciliation ---------------------------------------------------
    recon_rows = []
    for req in session.requests:
        root = by_id.get(req.root_span_id)
        if root is None:
            continue
        own = [c for c in chats
               if adapter.nearest_root(c.span_id, by_id) == root.span_id]
        ci = sum((c.tokens.reported_input or 0) for c in own)
        co = sum((c.tokens.reported_output or 0) for c in own)
        recon_rows.append({
            "request": (req.text or "")[:60],
            "root_input": root.tokens.reported_input, "sum_chat_input": ci,
            "input_match": root.tokens.reported_input == ci,
            "root_output": root.tokens.reported_output, "sum_chat_output": co,
            "output_match": root.tokens.reported_output == co,
        })

    payload = {
        "id": session.session_id, "harness": session.harness,
        "label": session.label,
        "traceId": by_id[session.requests[0].root_span_id].trace_id
                   if session.requests else None,
        "repo": session.repo, "branch": session.branch, "commit": session.commit,
        "agent_type": session.agent_type,
        "agent_name": session.agent_name,
        "is_subagent": session.is_subagent, "parent_session": session.parent_session,
        "span_count": len(spans), "summary": summary, "turns": turns,
        "tools": tool_rows, "servers": server_rows, "context": context,
        "timeline": timeline, "reconciliation": recon_rows,
        "requests": [{"request": (r.text or "")[:120], "timestamp": r.timestamp,
                      "turns": r.reported_turns, "model": r.model}
                     for r in session.requests],
    }
    if meta:
        payload.update(meta)
    return payload
