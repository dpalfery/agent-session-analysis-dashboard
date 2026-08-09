"""
canonical.py -- the harness-independent data model.

This module is the contract. Adapters translate a harness's raw OTel spans into
these types; everything downstream (store, views, UI) speaks only this language
and must never reference a harness-specific attribute name.

THE TOKEN MODEL IS THE POINT OF THIS FILE.

Harnesses disagree about what their own token counters mean, and the
disagreement is silent:

  GitHub Copilot (OTel GenAI)
      gen_ai.usage.input_tokens INCLUDES cache_read and cache_creation.
      Verified: cache_read > input_tokens on 0/32 spans, and
      input - cache_read - cache_creation lands at 1..23 on claude-sonnet-5.
      -> fresh = input - cache_read - cache_creation

  OpenCode (OpenInference)
      llm.token_count.prompt EXCLUDES cache_read.
      Verified: prompt 3528 + cache_read 39744 + completion 17 = total 43289.
      -> fresh = prompt, used as-is

Mapping one of those conventions onto the other produces numbers that look
plausible and are wrong -- negative fresh input, or input double-counted by up
to 2x. So TokenUsage stores the classes DISJOINTLY: fresh_input, cache_read and
cache_creation never overlap, and their sum is always the true billable input.
Each adapter converts on the way in. Nothing downstream needs to know which
convention produced the numbers.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# Bump when an adapter's output shape or semantics change, so derived rows can
# be selectively recomputed instead of wiping the whole store.
SCHEMA_VERSION = 1


@dataclass
class TokenUsage:
    """Disjoint token classes. fresh + cache_read + cache_creation == total input.

    None means NOT RECORDED by the harness -- never coerce it to 0. A missing
    counter and a measured zero are different facts and are rendered
    differently in the UI.
    """
    fresh_input: Optional[int] = None
    cache_read: Optional[int] = None
    cache_creation: Optional[int] = None
    output: Optional[int] = None
    reasoning: Optional[int] = None

    # What the harness itself reported for total input, kept verbatim so
    # validate() can prove our decomposition against the source of truth.
    reported_input: Optional[int] = None
    reported_output: Optional[int] = None

    @property
    def total_input(self) -> Optional[int]:
        parts = [self.fresh_input, self.cache_read, self.cache_creation]
        if all(p is None for p in parts):
            return None
        return sum(p or 0 for p in parts)

    @property
    def visible_output(self) -> Optional[int]:
        """Output excluding reasoning tokens (reasoning is a subset of output)."""
        if self.output is None:
            return None
        return self.output - (self.reasoning or 0)

    def is_consistent(self) -> bool:
        """Does our decomposition add back up to what the harness reported?"""
        if self.reported_input is None:
            return True
        return self.total_input == self.reported_input


@dataclass
class CanonicalSpan:
    """One span, harness-normalized."""
    span_id: str
    trace_id: str
    parent_span_id: Optional[str]
    harness: str
    source: str                      # raw OTel resource name, kept for diagnosis
    name: str
    op: Optional[str]                # invoke_agent | chat | execute_tool | embeddings | ...
    kind: Optional[str]
    timestamp: str
    duration_ms: Optional[float]
    status: Optional[str]

    model: Optional[str] = None
    tokens: TokenUsage = field(default_factory=TokenUsage)

    session_id: Optional[str] = None
    agent_name: Optional[str] = None
    is_auxiliary: bool = False       # harness-internal call, not an agent turn

    tool_name: Optional[str] = None
    tool_type: Optional[str] = None
    mcp_server: Optional[str] = None
    skill_name: Optional[str] = None

    ttft_ms: Optional[int] = None
    finish_reasons: Optional[list] = None
    error_type: Optional[str] = None

    # Content-bearing attributes, harness-normalized keys. Tokenized lazily and
    # cached by (span_id, key) -- see tokens.py.
    content: dict = field(default_factory=dict)

    # Everything else, verbatim, for the timeline's attribute inspector.
    raw_attributes: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class Problem:
    """A validation failure. Adapters must surface these rather than emit bad data."""
    severity: str                    # "error" | "warning"
    code: str
    message: str
    span_id: Optional[str] = None
    session_id: Optional[str] = None

    def __str__(self) -> str:
        where = self.span_id or self.session_id or "-"
        return f"[{self.severity}] {self.code} ({where}): {self.message}"


@dataclass
class Request:
    """One user request -- an invoke_agent root and the turns beneath it."""
    root_span_id: str
    text: Optional[str]
    timestamp: str
    reported_turns: Optional[int] = None
    model: Optional[str] = None


@dataclass
class Session:
    """A conversation: one or more requests sharing a session id."""
    session_id: str
    harness: str
    label: str
    requests: list = field(default_factory=list)     # list[Request]
    span_ids: list = field(default_factory=list)

    is_subagent: bool = False
    parent_session: Optional[str] = None
    agent_name: Optional[str] = None
    agent_type: Optional[str] = None

    repo: Optional[str] = None
    branch: Optional[str] = None
    commit: Optional[str] = None

    started: Optional[str] = None
    ended: Optional[str] = None


def validate_tokens(span: CanonicalSpan) -> list:
    """Universal token invariants. Adapters add their own on top.

    These catch the exact failure mode that motivated the disjoint model: an
    adapter mapping an inclusive counter onto an exclusive one (or vice versa)
    shows up immediately as negative fresh input or a broken sum.
    """
    problems = []
    t = span.tokens
    for name in ("fresh_input", "cache_read", "cache_creation", "output", "reasoning"):
        v = getattr(t, name)
        if v is not None and v < 0:
            problems.append(Problem(
                "error", "negative_tokens",
                f"{name} is negative ({v}) -- the adapter is very likely treating an "
                f"inclusive token counter as exclusive, or vice versa.",
                span_id=span.span_id))
    if not t.is_consistent():
        problems.append(Problem(
            "error", "token_decomposition_mismatch",
            f"fresh+cache_read+cache_creation = {t.total_input} but the harness "
            f"reported input = {t.reported_input}.",
            span_id=span.span_id))
    if t.output is not None and t.reasoning is not None and t.reasoning > t.output:
        problems.append(Problem(
            "error", "reasoning_exceeds_output",
            f"reasoning ({t.reasoning}) > output ({t.output}); reasoning is expected "
            f"to be a subset of output.",
            span_id=span.span_id))
    return problems
