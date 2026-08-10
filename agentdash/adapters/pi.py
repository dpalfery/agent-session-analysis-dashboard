"""
pi.py -- the `pi` coding agent, instrumented by the ObservMe extension
(npm @senad-d/observme), exported over OTLP to the Aspire dashboard.

Every pi-specific fact lives in this file. Downstream code sees only canonical
types. The attribute and span-name vocabulary is ObservMe's published semantic
convention (`src/semconv/{attributes,spans}.ts`, `observme.semconv.version` is
stamped on every span), not something inferred from a sample.

THE ONE THING TO GET RIGHT: pi borrows the OTel GenAI attribute NAMES but not
Copilot's semantics.

    gen_ai.usage.input_tokens EXCLUDES cache_read and cache_creation.

Copilot's key of the same name INCLUDES them. Applying Copilot's subtraction to
pi produces negative fresh input on 293 of 307 measured spans; applying pi's
pass-through to Copilot double-counts input by up to 2x. Verified against the
figure pi reports for itself:

    input + cache_read + cache_creation + output == pi.llm.usage.total_tokens

exactly, on 303/307 spans. The remaining 4 have both cache classes at zero,
where the two conventions are arithmetically indistinguishable. validate()
re-checks this on every span rather than trusting the comment.

Other corrections encoded here:

  * SESSION MEMBERSHIP IS AN ATTRIBUTE, NOT ANCESTRY. Copilot resolves sessions
    by nearest-invoke_agent-ancestor because its session id is only on some
    spans. pi stamps `pi.session.id` on 1009/1009 spans, and its parent links
    are NOT reliable: the dashboard is a ring buffer, and 25 of 1009 spans were
    already orphaned by eviction of their parent (14 `pi.agent.run`, 11
    `pi.turn`). Grouping by ancestry would silently drop those turns and their
    tokens. Same reasoning for nearest_root(), which resolves by
    `pi.agent.run.id` instead of walking parents.

  * A REQUEST IS AN AGENT RUN, and its span may be missing. 17 sessions held 27
    distinct `pi.agent.run.id` values but only 20 `pi.agent.run` spans. Requests
    are therefore built from the run IDs carried on child spans, so an evicted
    run root costs its label, not the whole request.

  * pi DOES NOT EMIT TOOL DEFINITIONS. `pi.llm.request.tool_schema_count` gives
    the count and nothing else, so the schema-cost view has no input for pi.
    That is reported as "not recorded", never estimated from the count.

  * CONTENT ARRIVES FLATTENED. With `observme.capture.*` off, pi emits
    `pi.llm.prompt.redacted` -- the entire prompt pre-joined into one string,
    truncated (measured: 33,470 of 56,273 chars) with `observme.truncated` and
    `observme.original_length` recording the cut. There is no per-message
    structure to bucket, so it maps to the flattened canonical content keys.
    When capture IS enabled ObservMe emits gen_ai.input/output.messages too, and
    those are preferred when present.

  * pi PRICES ITSELF. `pi.llm.cost.*_usd` is the provider's own cost for the
    call. It is carried through as reported_cost_usd rather than re-derived --
    and rates.json must NOT be applied to pi: it is GitHub's credit table, and
    two models here (gpt-5.6-luna) appear in it while pi ran them through a
    different provider entirely. See cost.py's `applies_to` guard.
"""

import collections
import json
from typing import Optional

from ..canonical import (CanonicalSpan, Problem, Request, Session, TokenUsage)
from .base import Adapter

# ==========================================================================
# ObservMe span name -> canonical op.
#
# Only names whose canonical meaning is unambiguous get an op. Everything else
# stays structural (op=None): it still appears in the timeline and still counts
# toward the session, but it is not counted as a turn or a tool call. In
# particular `pi.turn` is deliberately NOT `chat` -- it WRAPS the chat span
# (measured exactly 1 `pi.llm.request` child per turn), so opping it would
# double every turn in the charts.
# ==========================================================================
SPAN_OPS = {
    "pi.agent.run":   "invoke_agent",     # one user request / agent invocation
    "pi.llm.request": "chat",             # one model call
    "pi.tool.call":   "execute_tool",     # one tool invocation
}

# Structural spans: carried for the timeline, never counted as turns or tools.
# `pi.bash.execution` is excluded from SPAN_OPS on purpose -- it nests under the
# `bash` pi.tool.call, so opping it would count that tool twice.
STRUCTURAL_SPANS = {
    "pi.session", "pi.turn", "pi.agent.spawn", "pi.agent.wait", "pi.agent.join",
    "pi.bash.execution", "pi.compaction", "pi.branch", "pi.model.change",
    "pi.thinking.change",
}

# Content attributes -> canonical content keys, in preference order. The first
# key present wins, so structured messages beat the flattened blob when
# observme.capture.* is enabled. The pi statusline collector emits the same
# gen_ai.* keys, so it flows through here unchanged.
CONTENT_SOURCES = {
    "system_instructions": ["gen_ai.system_instructions"],
    "input_messages":      ["gen_ai.input.messages"],
    "output_messages":     ["gen_ai.output.messages"],
    "prompt_text":         ["pi.llm.prompt.redacted"],
    "response_text":       ["pi.llm.response.redacted"],
    "reasoning_text":      ["pi.llm.thinking.redacted"],
    "tool_call_arguments": ["gen_ai.tool.call.arguments", "pi.tool.arguments.redacted"],
    "tool_call_result":    ["gen_ai.tool.call.result", "pi.tool.result.redacted"],
}
JSON_CONTENT = {"input_messages", "output_messages"}

COST_PARTS = ["pi.llm.cost.input_usd", "pi.llm.cost.output_usd",
              "pi.llm.cost.cache_read_usd", "pi.llm.cost.cache_write_usd"]


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


def _fattr(span, key):
    v = _attr(span, key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _jattr(span, key):
    v = _attr(span, key)
    if v is None:
        return None
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return None


def _battr(span, key):
    """ObservMe serializes booleans as the strings 'true'/'false'."""
    v = _attr(span, key)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


class PiAdapter(Adapter):
    name = "pi"
    version = 1

    # Attributes whose presence genuinely varies for pi, and whose absence the
    # footer should therefore report. The capture flags are here because they
    # decide whether content-bearing views can work at all.
    watched_attributes = [
        "gen_ai.usage.cache_creation.input_tokens",
        "pi.llm.prompt.redacted",
        "gen_ai.input.messages",
        "error.type",
    ]

    # ---- detection -------------------------------------------------------
    def detect(self, span: dict) -> float:
        """Fingerprint on the pi./observme. namespaces, never on `source`.

        `source` is per-process here in the extreme: 1009 spans arrived under 17
        distinct sources (`observme-pi-extension-41d96649`, `-6e7a142d`, ...),
        one per pi invocation. It identifies an instance, not a harness.

        The `observme.semconv.version` stamp is the strongest single signal --
        it is on every span ObservMe emits and on nothing else.
        """
        attrs = span.get("attributes") or {}
        if not attrs:
            return 0.0
        if "observme.semconv.version" in attrs:
            return 1.0
        has_pi = any(a.startswith("pi.") for a in attrs)
        has_observme = any(a.startswith("observme.") for a in attrs)
        if has_pi and has_observme:
            return 1.0
        if has_pi and span.get("name", "").startswith("pi."):
            return 0.9
        return 0.0

    def is_relevant(self, span: dict) -> bool:
        """Keep the spans that carry pi's session/turn skeleton, drop the rest.

        Unlike Copilot this cannot filter on `gen_ai.operation.name`: only the
        LLM spans have it, and the turn/run/session spans that give the session
        its shape would all be dropped.
        """
        name = span.get("name") or ""
        return name in SPAN_OPS or name in STRUCTURAL_SPANS

    # ---- normalization ---------------------------------------------------
    def normalize(self, span: dict, counter=None) -> Optional[CanonicalSpan]:
        name = span.get("name") or ""
        if not self.is_relevant(span):
            return None
        op = SPAN_OPS.get(name)

        # input_tokens EXCLUDES both cache classes here -- pass it through as
        # fresh. reported_input is the harness's true TOTAL input, which pi does
        # not name directly, so it is the sum of the disjoint classes; validate()
        # proves that sum against pi.llm.usage.total_tokens.
        fresh = _iattr(span, "gen_ai.usage.input_tokens")
        cr = _iattr(span, "gen_ai.usage.cache_read.input_tokens")
        cc = _iattr(span, "gen_ai.usage.cache_creation.input_tokens")
        out = _iattr(span, "gen_ai.usage.output_tokens")
        rsn = _iattr(span, "gen_ai.usage.reasoning.output_tokens")

        parts = [fresh, cr, cc]
        total_in = None if all(p is None for p in parts) else sum(p or 0 for p in parts)

        tokens = TokenUsage(
            fresh_input=fresh, cache_read=cr, cache_creation=cc,
            output=out, reasoning=rsn,
            reported_input=total_in, reported_output=out,
        )

        content = {}
        for canon_key, raw_keys in CONTENT_SOURCES.items():
            for raw_key in raw_keys:
                v = _attr(span, raw_key)
                if v is None:
                    continue
                if canon_key in JSON_CONTENT:
                    parsed = _jattr(span, raw_key)
                    if parsed is None:
                        continue
                    content[canon_key] = self._normalize_messages(parsed)
                else:
                    content[canon_key] = v
                break

        return CanonicalSpan(
            span_id=span["spanId"], trace_id=span.get("traceId"),
            parent_span_id=span.get("parentSpanId"),
            harness=self.name, source=span.get("source"),
            name=name, op=op, kind=span.get("kind"),
            timestamp=span.get("timestamp"), duration_ms=span.get("durationMs"),
            status=span.get("status"),
            model=(_attr(span, "gen_ai.response.model")
                   or _attr(span, "gen_ai.request.model")
                   or _attr(span, "pi.model.id.current")),
            tokens=tokens,
            session_id=_attr(span, "pi.session.id"),
            agent_name=(_attr(span, "pi.agent.display_name")
                        or _attr(span, "gen_ai.agent.name")),
            # pi has no equivalent of Copilot's harness-internal chat agents:
            # every LLM call observed belongs to a real agent run. Left False
            # rather than guessed at.
            is_auxiliary=False,
            tool_name=(_attr(span, "pi.tool.name") or _attr(span, "gen_ai.tool.name")),
            tool_type=_attr(span, "gen_ai.tool.type"),
            # pi routes every MCP call through one `mcp` gateway tool and does
            # not name the server on the span, so per-server attribution is
            # genuinely unavailable -- null, not "unknown".
            mcp_server=None,
            skill_name=None,
            ttft_ms=None,                 # not emitted by ObservMe
            finish_reasons=_jattr(span, "gen_ai.response.finish_reasons"),
            error_type=_attr(span, "error.type"),
            reported_cost_usd=_fattr(span, "pi.llm.cost.total_usd"),
            content=content,
            raw_attributes=dict(span.get("attributes") or {}),
        )

    def _normalize_messages(self, msgs):
        """ObservMe message shape -> canonical {role, parts:[{type, ...}]}.

        Only reached when observme.capture.prompts/responses are enabled; with
        the default privacy settings pi emits the flattened blob instead.
        """
        out = []
        for m in msgs or []:
            parts = []
            raw_parts = m.get("parts")
            if raw_parts is None:
                c = m.get("content")
                if isinstance(c, str):
                    raw_parts = [{"type": "text", "content": c}]
                elif isinstance(c, list):
                    raw_parts = c
                else:
                    raw_parts = []
            for p in raw_parts:
                if not isinstance(p, dict):
                    parts.append({"type": "text", "text": str(p)})
                    continue
                t = p.get("type")
                if t in ("text", "input_text", "output_text"):
                    parts.append({"type": "text",
                                  "text": p.get("content") or p.get("text") or ""})
                elif t in ("tool_result", "tool_call_result", "tool_call_response"):
                    parts.append({"type": "tool_result", "raw": p.get("response", p)})
                elif t in ("tool_call", "tool_use"):
                    parts.append({"type": "tool_call", "raw": p})
                elif t in ("reasoning", "thinking"):
                    parts.append({"type": "reasoning", "raw": p})
                else:
                    parts.append({"type": "other", "raw": p})
            out.append({"role": m.get("role"), "parts": parts})
        return out

    # ---- grouping --------------------------------------------------------
    def run_id(self, span):
        """The agent run a span belongs to, from its own attributes."""
        return span.raw_attributes.get("pi.agent.run.id")

    def nearest_root(self, span_id, by_id):
        """spanId of this span's agent-run root, resolved by ATTRIBUTE.

        Not by walking parents. 25 of 1009 measured spans had already lost their
        parent to ring-buffer eviction, and every one of them still carried
        `pi.agent.run.id` -- so the attribute answers correctly where ancestry
        returns None and silently drops the turn from its request.
        """
        cur = by_id.get(span_id)
        if cur is None:
            return None
        rid = self.run_id(cur)
        if rid is None:
            return None
        sid = cur.session_id
        for s in by_id.values():
            if s.op == "invoke_agent" and self.run_id(s) == rid and s.session_id == sid:
                return s.span_id
        return None

    def group(self, spans: list) -> list:
        """Assign spans to sessions by `pi.session.id`.

        Flat and total: the attribute is on every span pi emits, so no span can
        be lost to a broken parent link. One session == one trace == one pi
        process here (verified: 0 traces carried more than one session id), but
        the attribute is what the grouping rests on, not the trace.
        """
        members = collections.defaultdict(list)
        for s in spans:
            if s.session_id:
                members[s.session_id].append(s)

        sessions = []
        for sid, grp in members.items():
            grp.sort(key=lambda s: s.timestamp or "")
            roots = {s.span_id: s for s in grp if s.op == "invoke_agent"}

            # Requests are the distinct run IDs observed, not the run spans:
            # 17 sessions carried 27 run IDs but only 20 run spans survived the
            # buffer. A request whose root was evicted keeps its turns.
            first_seen, root_of = {}, {}
            for s in grp:
                rid = self.run_id(s)
                if rid is None:
                    continue
                first_seen.setdefault(rid, s.timestamp)
                if s.op == "invoke_agent":
                    root_of[rid] = s

            requests = []
            for rid in sorted(first_seen, key=lambda r: (first_seen[r] or "", r)):
                root = root_of.get(rid)
                requests.append(Request(
                    # A missing root gets a synthetic id that resolves to no
                    # span, so reconciliation skips it instead of inventing one.
                    root_span_id=root.span_id if root else f"{sid}:{rid}",
                    text=self._request_label(root, rid),
                    timestamp=(root.timestamp if root else first_seen[rid]),
                    reported_turns=self._turn_count(grp, rid),
                    model=(root.model if root else None)))

            sess_span = next((s for s in grp if s.name == "pi.session"), None)
            anchor = sess_span or (grp[0] if grp else None)
            attrs = anchor.raw_attributes if anchor else {}

            role = attrs.get("pi.agent.role")
            times = sorted(s.timestamp for s in grp if s.timestamp)
            sessions.append(Session(
                session_id=sid, harness=self.name,
                label=self._session_label(attrs, requests, sid),
                requests=requests,
                span_ids=[s.span_id for s in grp],
                is_subagent=bool(role and role != "root"),
                parent_session=attrs.get("pi.agent.parent_id"),
                agent_name=attrs.get("pi.agent.display_name"),
                agent_type=role,
                # pi hashes the working directory for privacy and never emits a
                # git remote or sha, so repo/branch/commit are genuinely absent.
                # cwd_basename is the only human-readable locator it offers.
                repo=attrs.get("pi.cwd.basename") or attrs.get("pi.project.name"),
                branch=None, commit=None,
                started=times[0] if times else None,
                ended=times[-1] if times else None,
            ))
        sessions.sort(key=lambda s: s.started or "")
        return sessions

    def _turn_count(self, spans, rid):
        """Turns pi itself numbered for this run: max(pi.turn.index) + 1.

        Reported rather than counted, so it stays comparable with the number of
        turn spans that actually survived the buffer -- validate() flags the gap.
        """
        idx = [_safe_int(s.raw_attributes.get("pi.turn.index"))
               for s in spans if s.name == "pi.turn" and self.run_id(s) == rid]
        idx = [i for i in idx if i is not None]
        return max(idx) + 1 if idx else None

    def _request_label(self, root, rid):
        """pi hashes user prompts rather than emitting them (privacy default).

        There is no user_request attribute to show, so the run is labelled by
        what pi does report -- its index and how it was started.
        """
        if root is None:
            return f"{rid} (run root not in buffer)"
        a = root.raw_attributes
        src = a.get("pi.agent.run.source")
        n = a.get("pi.agent.run.index") or rid
        bits = [f"run {n}"]
        if src and src != "unknown":
            bits.append(f"via {src}")
        if a.get("pi.agent.run.outcome"):
            bits.append(a["pi.agent.run.outcome"])
        return " · ".join(bits)

    def _session_label(self, attrs, requests, sid):
        name = (attrs.get("pi.session.name") or "").strip()
        if name and name != "unknown":
            base = name
        elif attrs.get("pi.cwd.basename"):
            base = attrs["pi.cwd.basename"]
        else:
            base = f"pi session {sid[:8]}"
        model = attrs.get("pi.model.id.current")
        if model:
            base = f"{base} · {model}"
        if len(requests) > 1:
            base = f"{base} (+{len(requests)-1} more run{'s' if len(requests) > 2 else ''})"
        return base

    # ---- reporting -------------------------------------------------------
    def notes(self, spans) -> list:
        """Explain what ObservMe's privacy defaults withhold, and why.

        This matters for the context-composition view specifically. pi does
        emit `pi.llm.prompt.redacted`, but with `observme.capture.prompts` off
        that string is a reduced rendering, not the request: measured against
        pi's own `pi.llm.request.input_chars` it carries a median of 19.6% of
        the characters actually sent (range 4.4-78%), while
        `observme.truncated` stays false on all 307 spans -- so the shortfall is
        deliberate redaction, not truncation, and bucketing it against reported
        input would read as a ~80% "tokenizer drift" residual that is nothing of
        the kind. So it is not bucketed, and this says why instead.
        """
        out = []
        chats = [s for s in spans if s.op == "chat"]
        tools = [s for s in spans if s.op == "execute_tool"]
        flag_off = lambda k: any(s.raw_attributes.get(k) == "false" for s in spans)

        # Driven by what the spans actually carry, not by the flag alone: the
        # capture flags govern RAW capture, and ObservMe still exports a
        # redacted rendering with them off. Asserting "not exported" from the
        # flag would have been wrong about tool results, which are present.
        structured = [s for s in chats if "input_messages" in s.content]
        flat = [s for s in chats if "prompt_text" in s.content]
        if chats and not structured:
            msg = ("No model turn exported per-message structure, so context "
                   "composition cannot be broken down.")
            if flag_off("observme.capture.prompts"):
                msg += (" ObservMe's privacy default is in effect "
                        "(observme.capture.prompts = false).")
            if flat:
                msg += (f" {len(flat)} of {len(chats)} turns did export a flattened "
                        "prompt string, but it is a reduced rendering rather than the "
                        "request — measured against pi's own input_chars it carries a "
                        "median 19.6% of the characters sent — so bucketing it would "
                        "understate every band and show the shortfall as tokenizer "
                        "drift, which it is not.")
            msg += (" Set observme.capture.prompts: true in ~/.pi/agent/observme.yaml "
                    "to enable the breakdown.")
            out.append(msg)

        missing_results = [s for s in tools if "tool_call_result" not in s.content]
        if missing_results:
            out.append(
                f"{len(missing_results)} of {len(tools)} tool calls exported no result "
                "content; pi reports their size in bytes instead, so those "
                "result-token cells read 'not recorded' rather than zero.")

        out.append(
            "pi does not export tool definitions — only "
            "pi.llm.request.tool_schema_count, a count with no schemas — so "
            "per-tool schema cost cannot be computed for this harness at all. "
            "That is a gap in what is emitted, not a session with no tools.")
        return out

    # ---- validation ------------------------------------------------------
    def validate(self, session, spans) -> list:
        """Reconcile against the two totals pi computes for itself.

        pi puts no token or cost totals on its run roots, so there is no
        Copilot-style root-vs-turns check to run. What it does report is a
        per-call total and a per-call cost breakdown, and both are independent
        of the decomposition this adapter performs -- which makes them exactly
        the right thing to prove against.
        """
        problems = []
        for s in spans:
            if s.op != "chat":
                continue
            a = s.raw_attributes

            # 1. token total. THE check that catches an inclusive/exclusive mixup:
            #    reading input_tokens Copilot's way changes this sum immediately.
            reported_total = _safe_int(a.get("pi.llm.usage.total_tokens"))
            if reported_total is not None:
                ours = (s.tokens.total_input or 0) + (s.tokens.output or 0)
                if ours != reported_total:
                    problems.append(Problem(
                        "error", "token_total_mismatch",
                        f"fresh+cache_read+cache_creation+output = {ours:,} but pi "
                        f"reports total_tokens = {reported_total:,} "
                        f"(delta {ours-reported_total:+,}). The most likely cause is "
                        f"treating gen_ai.usage.input_tokens as INCLUSIVE of the "
                        f"cache classes -- in pi it is exclusive.",
                        span_id=s.span_id, session_id=session.session_id))

            # 2. cost breakdown vs pi's own total, guarding the USD we surface.
            total_usd = _safe_float(a.get("pi.llm.cost.total_usd"))
            parts = [_safe_float(a.get(k)) for k in COST_PARTS]
            if total_usd is not None and any(p is not None for p in parts):
                summed = sum(p or 0.0 for p in parts)
                if abs(summed - total_usd) > max(1e-9, total_usd * 1e-6):
                    problems.append(Problem(
                        "error", "cost_decomposition_mismatch",
                        f"pi.llm.cost parts sum to {summed:.9f} USD but "
                        f"pi.llm.cost.total_usd = {total_usd:.9f}.",
                        span_id=s.span_id, session_id=session.session_id))

        # 3. turn coverage. pi numbers its turns, so the gap between the highest
        #    index it assigned and the turn spans actually present is
        #    measurable -- and expected, because the dashboard evicts. A
        #    warning, not an error: those spans are gone from the buffer, they
        #    were not mis-parsed, and the distinction is the whole point of
        #    saying so instead of silently reporting a smaller total.
        by_run = collections.defaultdict(list)
        for s in spans:
            rid = self.run_id(s)
            if rid:
                by_run[rid].append(s)
        for rid, grp in sorted(by_run.items()):
            reported = self._turn_count(grp, rid)
            seen = len([s for s in grp if s.name == "pi.turn"])
            if reported and seen and seen < reported:
                problems.append(Problem(
                    "warning", "turns_evicted",
                    f"run {rid}: pi numbered {reported} turns but only {seen} turn "
                    f"span(s) are in the buffer -- {reported - seen} were evicted "
                    f"before export, so this run's totals are a floor.",
                    session_id=session.session_id))
        return problems


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
