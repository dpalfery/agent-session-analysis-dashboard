"""
gemini.py -- Google Gemini / AGY Agentic AI adapter via OTel GenAI conventions.
"""

import collections
import json
from typing import Optional

from ..canonical import (CanonicalSpan, Problem, Request, Session, TokenUsage,
                         validate_tokens)
from .base import Adapter

CONTENT_KEYS = {
    "gen_ai.system_instructions": "system_instructions",
    "gen_ai.input.messages": "input_messages",
    "gen_ai.output.messages": "output_messages",
    "gen_ai.tool.definitions": "tool_definitions",
    "gen_ai.tool.call.arguments": "tool_call_arguments",
    "gen_ai.tool.call.result": "tool_call_result",
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
    except Exception:
        return None


class GeminiAdapter(Adapter):
    name = "gemini"
    version = 1

    watched_attributes = [
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.skills",
        "gen_ai.agent.name",
    ]

    def detect(self, span: dict) -> float:
        """Fingerprint on Gemini / AGY attribute signatures."""
        attrs = span.get("attributes") or {}
        if not attrs:
            return 0.0
        
        sys = _attr(span, "gen_ai.system")
        agent = _attr(span, "gen_ai.agent.name")
        source = span.get("source") or ""

        if sys == "gemini" or agent == "antigravity" or "agy" in source:
            return 1.0
        
        # Weak signal only: generic gen_ai.* with no system/agent/source claim is
        # below MIN_CONFIDENCE so genuinely unknown spans quarantine rather than
        # get guessed as Gemini (the registry never hands a span to an adapter it
        # merely might be). Real AGY spans always hit the 1.0 branch above; this
        # fallback therefore never legitimately fires for them.
        has_genai = any(a.startswith("gen_ai.") for a in attrs)
        if has_genai and _attr(span, "gen_ai.operation.name") == "chat":
            return 0.4

        return 0.0

    def is_relevant(self, span: dict) -> bool:
        return _attr(span, "gen_ai.operation.name") is not None or (span.get("name") or "").startswith("chat")

    def normalize(self, span: dict, counter=None) -> Optional[CanonicalSpan]:
        op = _attr(span, "gen_ai.operation.name")

        inp = _iattr(span, "gen_ai.usage.input_tokens")
        cr = _iattr(span, "gen_ai.usage.cache_read.input_tokens")
        cc = _iattr(span, "gen_ai.usage.cache_creation.input_tokens")
        out = _iattr(span, "gen_ai.usage.output_tokens")
        rsn = _iattr(span, "gen_ai.usage.reasoning.output_tokens")

        # EXCLUSIVE convention (same as pi): input_tokens EXCLUDES the cache classes.
        # Verified on live data: 20/3279 spans have cache_read > input_tokens, which is
        # impossible under the inclusive (Copilot) reading. So fresh = input as-is.
        fresh = inp
        parts = [fresh, cr, cc]
        total_in = None if all(p is None for p in parts) else sum(p or 0 for p in parts)

        tokens = TokenUsage(
            fresh_input=fresh, cache_read=cr, cache_creation=cc,
            output=out, reasoning=rsn,
            reported_input=total_in, reported_output=out,
        )

        content = {}
        for raw_key, canon_key in CONTENT_KEYS.items():
            v = _attr(span, raw_key)
            if v is None:
                continue
            if canon_key == "tool_definitions":
                # AGY emits only tool NAMES (a free-form string, or sometimes a
                # JSON array of {name} stubs), never the schema block. Per the
                # project's 'absent is not zero' rule and the pi precedent,
                # do NOT populate tool_definitions -- tokenizing bare names and
                # presenting them as schema cost would mislead in the cross-
                # harness compare view. The names stay in raw_attributes for the
                # timeline inspector; notes() explains the gap.
                continue
            if canon_key in ("input_messages", "output_messages"):
                parsed = _jattr(span, raw_key)
                if parsed is not None:
                    content[canon_key] = self._normalize_messages(parsed)
                else:
                    content[canon_key] = v
            else:
                content[canon_key] = v

        tool_name = _attr(span, "gen_ai.tool.name")
        session_id = (_attr(span, "copilot_chat.chat_session_id") or 
                      _attr(span, "gen_ai.session.id") or 
                      _attr(span, "service.instance.id"))

        return CanonicalSpan(
            span_id=span["spanId"], trace_id=span.get("traceId", ""),
            parent_span_id=span.get("parentSpanId"),
            harness=self.name, source=span.get("source"),
            name=span.get("name"), op=op, kind=span.get("kind"),
            timestamp=span.get("timestamp", ""), duration_ms=span.get("durationMs"),
            status=span.get("status"),
            model=(_attr(span, "gen_ai.response.model")
                   or _attr(span, "gen_ai.request.model")),
            tokens=tokens,
            session_id=session_id,
            agent_name=_attr(span, "gen_ai.agent.name"),
            is_auxiliary=False,
            tool_name=tool_name, tool_type=_attr(span, "gen_ai.tool.type"),
            mcp_server=_attr(span, "mcp.server.name"),
            skill_name=_attr(span, "github.copilot.tool.parameters.skill_name") or _attr(span, "gen_ai.skills"),
            ttft_ms=_iattr(span, "gen_ai.time_to_first_token"),
            finish_reasons=_jattr(span, "gen_ai.response.finish_reasons"),
            error_type=_attr(span, "error.type"),
            content=content,
            raw_attributes=dict(span.get("attributes") or {}),
        )

    def group(self, spans: list) -> list:
        """One Session per distinct session id. Flat: AGY emits one chat span per
        turn with no parentSpanId and no invoke_agent root, so grouping is by the
        session-id attribute (not ancestry) and there are no Requests to build.
        """
        members = collections.defaultdict(list)
        for s in spans:
            if s.session_id:
                members[s.session_id].append(s)
        sessions = []
        for sid, grp in members.items():
            grp.sort(key=lambda s: s.timestamp or "")
            attrs = grp[0].raw_attributes if grp else {}
            times = sorted(s.timestamp for s in grp if s.timestamp)
            sessions.append(Session(
                session_id=sid, harness=self.name,
                label=self._session_label(attrs, grp),
                requests=[],                  # no invoke_agent roots exist
                span_ids=[s.span_id for s in grp],
                agent_name=attrs.get("gen_ai.agent.name"),
                repo=attrs.get("vcs.repository.name"),
                branch=attrs.get("vcs.ref.head.name"),
                started=times[0] if times else None,
                ended=times[-1] if times else None,
            ))
        sessions.sort(key=lambda s: s.started or "")
        return sessions

    def _session_label(self, attrs, grp):
        repo = attrs.get("vcs.repository.name")
        models = sorted({s.model for s in grp if s.model})
        base = repo or f"gemini session {(grp[0].session_id or '')[:8]}"
        if models:
            base = f"{base} · {', '.join(models)}"
        if len(grp) > 1:
            base = f"{base} ({len(grp)} turns)"
        return base

    def nearest_root(self, span_id, by_id):
        """No invoke_agent roots are emitted, so there is no request root to resolve
        to. The reconciliation loop iterates session.requests (empty), so None is
        safe and renders an empty reconciliation section.
        """
        return None

    def validate(self, session, spans) -> list:
        """No harness-reported per-call token total exists to reconcile against
        (AGY emits only the four disjoint counters plus a 5-section breakdown that
        is a different decomposition, not a total). The universal non-negativity
        check (validate_tokens) already runs on every normalized span in the
        pipeline, and is_consistent is tautological here because reported_input is
        our own disjoint sum. So there is nothing honest to assert at the session
        level; the gap is surfaced to the UI via notes().
        """
        return []

    def notes(self, spans) -> list:
        chats = [s for s in spans if s.op == "chat"]
        out = [
            "Gemini/AGY spans are emitted one chat span per turn by a custom "
            "statusline collector — they carry no invoke_agent root, so request-level "
            "grouping and turn reconciliation are not available (every turn is a "
            "top-level chat span).",
            "The collector reports no per-call token total, so Gemini turns cannot be "
            "reconciled against a harness figure; token non-negativity is still checked.",
            "Gemini uses the EXCLUSIVE token convention (like pi): gen_ai.usage."
            "input_tokens excludes the cache classes, so cache_read can exceed fresh "
            "input — that is correct, not a parsing error.",
        ]
        tool_defs = [s for s in chats if "tool_definitions" in s.content]
        if not tool_defs:
            out.append("AGY exports tool names but not their schemas, so per-tool "
                       "schema cost is not available for this harness (the names "
                       "remain visible in each turn's raw attributes). This mirrors "
                       "the pi adapter's treatment of its tool_schema_count.")
        else:
            out.append(f"{len(tool_defs)} of {len(chats)} turns exported tool definitions; "
                       "no tool-RESULT content is exported, so result-token cells read "
                       "'not recorded'.")
        return out

    def _normalize_messages(self, msgs):
        if not isinstance(msgs, list):
            return msgs
        out = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            parts = []
            for p in (m.get("parts") or []):
                if isinstance(p, dict):
                    t = p.get("type", "text")
                    text = p.get("text") or p.get("content") or ""
                    parts.append({"type": t, "text": text})
                else:
                    parts.append({"type": "text", "text": str(p)})
            out.append({"role": m.get("role", "user"), "parts": parts})
        return out
