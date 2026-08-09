"""
copilot.py -- GitHub Copilot Chat (VS Code extension) via OTel GenAI conventions.

Every Copilot-specific fact lives in this file: attribute names, token-counter
semantics, session assembly, and which calls are harness-internal. Downstream
code sees only canonical types.

The corrections encoded here were each found by reconciling against a number the
harness reported itself. They are not cosmetic:

  * gen_ai.usage.input_tokens INCLUDES cache_read and cache_creation. Verified:
    cache_read > input on 0/32 spans, and input-cache_read-cache_creation lands
    at 1..23 on every claude-sonnet-5 span, never negative. Treating the classes
    as additive double-counts input by up to 2x.
  * cache_creation is model-dependent, not globally absent: present on 13/13
    claude-sonnet-5 chat spans, 0 on grok-4.5 / gpt-4o-mini / gpt-5.6-luna /
    gpt-5.3-codex / copilot-nes.
  * reasoning is a subset of output on every span carrying it.
  * A session may hold SEVERAL invoke_agent roots (one per user request), and a
    trace may hold SEVERAL sessions (subagent roots hang off an
    `execute_tool runSubagent` span inside the parent's trace). So membership is
    resolved by nearest-invoke_agent-ancestor, not by traceId.
  * Auxiliary agents can run UNDERNEATH a real root. A backgroundTodoAgent span
    beneath the `implement` root accounted for exactly the 2,841 in / 55 out
    reconciliation delta; roots count only their own agent turns.
  * Message part shape differs by model: claude-sonnet-5 puts tool results under
    role="user" and emits assistant "reasoning" parts; grok-4.5 uses role="tool".
    Bucket on part TYPE, never on role.
"""

import collections
import json
from typing import Optional

from ..canonical import (CanonicalSpan, Problem, Request, Session, TokenUsage,
                         validate_tokens)
from .base import Adapter

# ==========================================================================
# HEURISTICS -- Copilot-specific, tunable. Documented in README.md.
# ==========================================================================
HEURISTICS = {
    # A leading user message is harness-injected context, not the human's words,
    # when it opens with one of these XML-ish tags.
    "instruction_block_tags": [
        "environment_info", "workspace_info", "instructions",
        "copilot_instructions", "context", "reminderInstructions",
        "editor_context", "tool_use_instructions", "notebook_info",
    ],
    "mcp_prefix": "mcp_",
    "fresh_input_jump_threshold": 0.25,
    # Harness-internal chat calls. Membership is decided primarily by ANCESTRY
    # (no invoke_agent ancestor = out of session); this list catches the ones
    # that do sit under a real root.
    "auxiliary_agent_names": [
        "copilotLanguageModelWrapper", "XtabProvider", "backgroundTodoAgent",
        "title", "progressMessages", "debugCommandIdentifier",
    ],
}

# Content attributes lifted into CanonicalSpan.content under canonical keys.
CONTENT_KEYS = {
    "gen_ai.system_instructions": "system_instructions",
    "gen_ai.input.messages": "input_messages",
    "gen_ai.output.messages": "output_messages",
    "gen_ai.tool.definitions": "tool_definitions",
    "gen_ai.tool.call.arguments": "tool_call_arguments",
    "gen_ai.tool.call.result": "tool_call_result",
}

# Copilot part types -> canonical part types.
PART_TYPES = {
    "text": "text",
    "tool_call": "tool_call",
    "reasoning": "reasoning",
    "tool_call_response": "tool_result",
    "tool_call_result": "tool_result",
    "tool_result": "tool_result",
}


def _attr(span, key, default=None):
    return (span.get("attributes") or {}).get(key, default)


def _iattr(span, key):
    v = _attr(span, key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _jattr(span, key):
    v = _attr(span, key)
    if v is None:
        return None
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return None


class CopilotAdapter(Adapter):
    name = "copilot"
    version = 1

    # ---- detection -------------------------------------------------------
    def detect(self, span: dict) -> float:
        """Fingerprint on attribute namespaces, never on `source`."""
        attrs = span.get("attributes") or {}
        if not attrs:
            return 0.0
        has_genai = any(a.startswith("gen_ai.") for a in attrs)
        has_copilot = any(a.startswith(("github.copilot.", "copilot_chat."))
                          for a in attrs)
        if has_genai and has_copilot:
            return 1.0
        if has_copilot:
            return 0.9          # execute_tool spans carry copilot_chat.* only
        if has_genai and "gen_ai.operation.name" in attrs:
            # GenAI conventions but no Copilot namespace: probably another
            # GenAI-emitting harness. Weak -- below MIN_CONFIDENCE on its own.
            return 0.4
        return 0.0

    def is_relevant(self, span: dict) -> bool:
        return _attr(span, "gen_ai.operation.name") is not None

    # ---- normalization ---------------------------------------------------
    def normalize(self, span: dict, counter=None) -> Optional[CanonicalSpan]:
        op = _attr(span, "gen_ai.operation.name")
        if op is None:
            return None

        inp = _iattr(span, "gen_ai.usage.input_tokens")
        cr = _iattr(span, "gen_ai.usage.cache_read.input_tokens")
        cc = _iattr(span, "gen_ai.usage.cache_creation.input_tokens")
        out = _iattr(span, "gen_ai.usage.output_tokens")
        rsn = _iattr(span, "gen_ai.usage.reasoning.output_tokens")

        # input_tokens is INCLUSIVE of both cache classes -- subtract to get the
        # disjoint fresh figure the canonical model requires.
        fresh = None if inp is None else inp - (cr or 0) - (cc or 0)

        tokens = TokenUsage(
            fresh_input=fresh, cache_read=cr, cache_creation=cc,
            output=out, reasoning=rsn,
            reported_input=inp, reported_output=out,
        )

        content = {}
        for raw_key, canon_key in CONTENT_KEYS.items():
            v = _attr(span, raw_key)
            if v is None:
                continue
            if canon_key in ("input_messages", "output_messages", "tool_definitions"):
                parsed = _jattr(span, raw_key)
                if parsed is None:
                    continue
                content[canon_key] = (self._normalize_messages(parsed)
                                      if canon_key.endswith("messages") else parsed)
            else:
                content[canon_key] = v

        tool_name = _attr(span, "gen_ai.tool.name")
        mcp_server = _attr(span, "github.copilot.tool.parameters.mcp_server_name")

        return CanonicalSpan(
            span_id=span["spanId"], trace_id=span.get("traceId"),
            parent_span_id=span.get("parentSpanId"),
            harness=self.name, source=span.get("source"),
            name=span.get("name"), op=op, kind=span.get("kind"),
            timestamp=span.get("timestamp"), duration_ms=span.get("durationMs"),
            status=span.get("status"),
            model=(_attr(span, "gen_ai.response.model")
                   or _attr(span, "gen_ai.request.model")),
            tokens=tokens,
            session_id=_attr(span, "copilot_chat.chat_session_id"),
            agent_name=_attr(span, "gen_ai.agent.name"),
            is_auxiliary=_attr(span, "gen_ai.agent.name") in HEURISTICS["auxiliary_agent_names"],
            tool_name=tool_name, tool_type=_attr(span, "gen_ai.tool.type"),
            mcp_server=mcp_server,
            skill_name=_attr(span, "github.copilot.tool.parameters.skill_name"),
            ttft_ms=_iattr(span, "copilot_chat.time_to_first_token"),
            finish_reasons=_jattr(span, "gen_ai.response.finish_reasons"),
            error_type=_attr(span, "error.type"),
            content=content,
            raw_attributes=dict(span.get("attributes") or {}),
        )

    def _normalize_messages(self, msgs):
        """Copilot message shape -> canonical {role, parts:[{type, ...}]}.

        Part TYPE decides the bucket, never role: claude-sonnet-5 files tool
        results under role="user" while grok-4.5 uses role="tool".
        """
        out = []
        for m in msgs or []:
            parts = []
            for p in (m.get("parts") or []):
                t = PART_TYPES.get(p.get("type"), "other")
                if t == "text":
                    parts.append({"type": t, "text": p.get("content") or ""})
                elif t == "tool_result":
                    parts.append({"type": t, "raw": p.get("response", p)})
                else:
                    parts.append({"type": t, "raw": p})
            out.append({"role": m.get("role"), "parts": parts})
        return out

    # ---- grouping --------------------------------------------------------
    def nearest_root(self, span_id, by_id):
        """spanId of the nearest invoke_agent ancestor (or self), else None."""
        cur = by_id.get(span_id)
        if cur is None:
            return None
        if cur.op == "invoke_agent":
            return cur.span_id
        seen = set()
        while True:
            p = cur.parent_span_id
            if not p or p in seen or p not in by_id:
                return None
            seen.add(p)
            cur = by_id[p]
            if cur.op == "invoke_agent":
                return cur.span_id

    def group(self, spans: list) -> list:
        """Assign spans to sessions by nearest invoke_agent ancestor.

        NOT by traceId: one session can span several traces (a root per user
        request), and one trace can contain several sessions (subagents).
        """
        by_id = {s.span_id: s for s in spans}
        roots = [s for s in spans if s.op == "invoke_agent"]
        root_by_id = {r.span_id: r for r in roots}

        def skey(r):
            return r.session_id or r.trace_id

        members = collections.defaultdict(list)
        for s in spans:
            rid = self.nearest_root(s.span_id, by_id)
            if rid and rid in root_by_id:
                members[skey(root_by_id[rid])].append(s)

        sessions = []
        for sid, grp in members.items():
            sroots = sorted([r for r in roots if skey(r) == sid],
                            key=lambda x: x.timestamp)
            first = sroots[0]

            # A subagent root hangs off an execute_tool (runSubagent) span.
            parent = by_id.get(first.parent_span_id) if first.parent_span_id else None
            is_sub = bool(parent and parent.op == "execute_tool")
            parent_sid = None
            if is_sub:
                prid = self.nearest_root(parent.span_id, by_id)
                if prid and prid in root_by_id:
                    parent_sid = skey(root_by_id[prid])

            label = (first.raw_attributes.get("copilot_chat.user_request")
                     or "(no request recorded)").strip()
            if len(sroots) > 1:
                label = f"{label} (+{len(sroots)-1} more request{'s' if len(sroots) > 2 else ''})"

            times = sorted(s.timestamp for s in grp if s.timestamp)
            sessions.append(Session(
                session_id=sid, harness=self.name, label=label,
                requests=[Request(
                    root_span_id=r.span_id,
                    text=(r.raw_attributes.get("copilot_chat.user_request") or "")[:200],
                    timestamp=r.timestamp,
                    reported_turns=_safe_int(r.raw_attributes.get("copilot_chat.turn_count")),
                    model=r.model) for r in sroots],
                span_ids=[s.span_id for s in grp],
                is_subagent=is_sub, parent_session=parent_sid,
                agent_name=first.agent_name,
                agent_type=first.raw_attributes.get("github.copilot.agent.type"),
                repo=first.raw_attributes.get("github.copilot.git.repository"),
                branch=first.raw_attributes.get("github.copilot.git.branch"),
                commit=first.raw_attributes.get("github.copilot.git.commit_sha"),
                started=times[0] if times else None,
                ended=times[-1] if times else None,
            ))
        sessions.sort(key=lambda s: s.started or "")
        return sessions

    # ---- validation ------------------------------------------------------
    def validate(self, session, spans) -> list:
        """Reconcile per-turn sums against each invoke_agent root's own totals.

        Auxiliary calls are excluded from the comparand: a root reports only its
        own agent turns. This is exactly how the 2,841-token `implement`
        discrepancy resolved -- the root was right, the comparand was wrong.
        """
        # Token invariants are checked once, over ALL normalized spans, in
        # pipeline.rebuild() -- including spans that belong to no session.
        # Here we only do the session-level reconciliation.
        problems = []
        by_id = {s.span_id: s for s in spans}

        for req in session.requests:
            root = by_id.get(req.root_span_id)
            if root is None:
                continue
            own = [s for s in spans
                   if s.op == "chat" and not s.is_auxiliary
                   and self.nearest_root(s.span_id, by_id) == root.span_id]
            ci = sum((s.tokens.reported_input or 0) for s in own)
            co = sum((s.tokens.reported_output or 0) for s in own)
            ri = root.tokens.reported_input
            ro = root.tokens.reported_output
            if ri is not None and ci != ri:
                problems.append(Problem(
                    "error", "input_reconciliation",
                    f"request {req.text[:40]!r}: turns sum to {ci:,} input but the "
                    f"invoke_agent root reports {ri:,} (delta {ci-ri:+,}).",
                    span_id=root.span_id, session_id=session.session_id))
            if ro is not None and co != ro:
                problems.append(Problem(
                    "error", "output_reconciliation",
                    f"request {req.text[:40]!r}: turns sum to {co:,} output but the "
                    f"invoke_agent root reports {ro:,} (delta {co-ro:+,}).",
                    span_id=root.span_id, session_id=session.session_id))
        return problems


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
