"""
base.py -- the adapter contract.

An adapter owns every harness-specific fact: attribute names, token-counter
semantics, how spans group into sessions, and which calls are harness-internal
rather than real agent turns. Nothing outside `adapters/` may reference a
harness-specific attribute key. That is the invariant this refactor exists to
create.

validate() is mandatory, not decorative. Adapters are exactly the place where a
plausible-but-wrong number gets minted -- an inclusive token counter read as
exclusive, an ancestry rule that silently drops turns. Every failure of that
kind in this project so far was caught by reconciling against a figure the
harness reported itself, so adapters are required to do that reconciliation and
to fail loudly when it does not hold.
"""

from typing import Optional


class Adapter:
    name: str = "base"
    version: int = 1

    # ---- detection -------------------------------------------------------
    def detect(self, span: dict) -> float:
        """Confidence 0.0-1.0 that this span came from this harness.

        Score on ATTRIBUTE FINGERPRINT, never on the OTel resource/source name.
        Evidence: in the Docker dashboard the bare source `opencode` carried the
        LLM spans while `opencode-4dbe5ffe` / `opencode-d09d2218` carried only
        app-internal spans -- same harness, different sources, different content.
        The signature also changed once the harness was reconfigured. Source is
        recorded for diagnosis but must not drive dispatch.
        """
        raise NotImplementedError

    def is_relevant(self, span: dict) -> bool:
        """Cheap pre-filter: is this span worth normalizing and tokenizing?

        Real buffers are mostly noise -- in one 20,000-span sample only 17 spans
        (0.085%) carried LLM telemetry. Returning False here keeps the expensive
        work off spans that can never contribute.
        """
        return True

    # ---- normalization ---------------------------------------------------
    def normalize(self, span: dict, counter) -> Optional["CanonicalSpan"]:
        """Raw span -> CanonicalSpan, or None to skip.

        This is where token-counter semantics are reconciled. Produce DISJOINT
        fresh_input / cache_read / cache_creation, and set reported_input to
        whatever the harness called its own input total so validate() can prove
        the decomposition.
        """
        raise NotImplementedError

    def group(self, spans: list) -> list:
        """Canonical spans -> list[Session]."""
        raise NotImplementedError

    # ---- validation ------------------------------------------------------
    def validate(self, session, spans) -> list:
        """Return Problems. An "error" severity must block the session.

        At minimum, reconcile per-turn token sums against the totals the harness
        reported on its own request roots.
        """
        raise NotImplementedError
