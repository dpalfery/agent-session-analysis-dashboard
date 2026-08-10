import os, sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdash.adapters.gemini import GeminiAdapter
from agentdash.canonical import Session, validate_tokens


def _gemini_span(span_id="s1", session_id="sess-1", **attributes):
    attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": "gemini",
        "gen_ai.request.model": "gemini-3.6-flash",
        "gen_ai.agent.name": "antigravity",
        "copilot_chat.chat_session_id": session_id,
        "gen_ai.usage.input_tokens": "10",
        "gen_ai.usage.cache_read.input_tokens": "2",
        "gen_ai.usage.output_tokens": "3",
    }
    attrs.update(attributes)
    return {
        "spanId": span_id,
        "traceId": "trace-1",
        "parentSpanId": None,
        "source": "agy",
        "name": "chat gemini-3.6-flash",
        "kind": "SPAN_KIND_CLIENT",
        "timestamp": "2026-08-09T15:00:00Z",
        "durationMs": 450.0,
        "status": "OK",
        "attributes": attrs,
    }


def test_gemini_adapter_detection():
    adapter = GeminiAdapter()

    # Gemini span with gen_ai.system="gemini"
    span_gemini = {
        "spanId": "s1001",
        "traceId": "t1001",
        "source": "agy",
        "name": "chat gemini-3.6-flash",
        "attributes": {
            "gen_ai.system": "gemini",
            "gen_ai.operation.name": "chat",
            "gen_ai.agent.name": "antigravity",
            "copilot_chat.chat_session_id": "sess-12345",
        }
    }
    assert adapter.detect(span_gemini) == 1.0
    assert adapter.is_relevant(span_gemini) is True


def test_gemini_adapter_normalization():
    adapter = GeminiAdapter()

    input_msgs = [
        {"role": "system", "parts": [{"type": "text", "text": "<identity>You are Antigravity</identity>"}]},
        {"role": "user", "parts": [{"type": "text", "text": "Help me refactor"}]}
    ]

    span = {
        "spanId": "s1002",
        "traceId": "t1002",
        "parentSpanId": None,
        "source": "agy",
        "name": "chat gemini-3.6-flash",
        "kind": "SPAN_KIND_CLIENT",
        "timestamp": "2026-08-09T15:00:00Z",
        "durationMs": 450.0,
        "attributes": {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "gemini",
            "gen_ai.provider.name": "google",
            "gen_ai.request.model": "gemini-3.6-flash",
            "gen_ai.agent.name": "antigravity",
            "copilot_chat.chat_session_id": "sess-12345",
            "gen_ai.system_instructions": "<identity>You are Antigravity</identity>",
            "gen_ai.input.messages": json.dumps(input_msgs),
            "gen_ai.tool.definitions": json.dumps([{"name": "grep_search"}]),
            "gen_ai.usage.input_tokens": "5000",
            "gen_ai.usage.cache_read.input_tokens": "4000",
            "gen_ai.usage.output_tokens": "250",
        }
    }

    canon = adapter.normalize(span)
    assert canon is not None
    assert canon.harness == "gemini"
    assert canon.session_id == "sess-12345"
    assert canon.agent_name == "antigravity"
    assert canon.model == "gemini-3.6-flash"

    # Gemini's input_tokens is EXCLUSIVE of cache classes.
    assert canon.tokens.fresh_input == 5000
    assert canon.tokens.cache_read == 4000
    assert canon.tokens.output == 250
    assert canon.tokens.reported_input == 9000
    assert validate_tokens(canon) == []

    # Content normalization checks
    assert canon.content["system_instructions"] == "<identity>You are Antigravity</identity>"
    assert len(canon.content["input_messages"]) == 2
    assert canon.content["input_messages"][0]["role"] == "system"


def test_gemini_tokens_exclusive_when_cache_exceeds_input():
    adapter = GeminiAdapter()
    span = _gemini_span(
        span_id="cache-heavy",
        **{
            "gen_ai.usage.input_tokens": "19590",
            "gen_ai.usage.cache_read.input_tokens": "24205",
        },
    )

    canon = adapter.normalize(span)

    assert canon.tokens.fresh_input == 19590
    assert canon.tokens.cache_read == 24205
    assert validate_tokens(canon) == []


def test_gemini_group_flat_by_session():
    adapter = GeminiAdapter()
    c1 = adapter.normalize(_gemini_span("g1", "sess-X"))
    c2 = adapter.normalize(_gemini_span("g2", "sess-X"))

    sessions = adapter.group([c1, c2])

    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "sess-X"
    assert session.requests == []
    assert len(session.span_ids) == 2


def test_gemini_nearest_root_is_none():
    adapter = GeminiAdapter()
    c = adapter.normalize(_gemini_span("rootless"))
    by_id = {c.span_id: c}

    assert adapter.nearest_root(c.span_id, by_id) is None


def test_gemini_validate_empty():
    adapter = GeminiAdapter()
    c1 = adapter.normalize(_gemini_span("v1", "sess-validate"))
    c2 = adapter.normalize(_gemini_span("v2", "sess-validate"))
    session = Session(
        session_id="sess-validate",
        harness="gemini",
        label="Gemini session",
        requests=[],
        span_ids=[c1.span_id, c2.span_id],
    )

    assert adapter.validate(session, [c1, c2]) == []


def test_gemini_notes_nonempty():
    adapter = GeminiAdapter()
    c1 = adapter.normalize(_gemini_span("n1", "sess-notes"))
    c2 = adapter.normalize(_gemini_span("n2", "sess-notes"))

    notes = adapter.notes([c1, c2])

    assert isinstance(notes, list)
    assert notes
    assert all(isinstance(note, str) for note in notes)


if __name__ == "__main__":
    test_gemini_adapter_detection()
    test_gemini_adapter_normalization()
    test_gemini_tokens_exclusive_when_cache_exceeds_input()
    test_gemini_group_flat_by_session()
    test_gemini_nearest_root_is_none()
    test_gemini_validate_empty()
    test_gemini_notes_nonempty()
    print("ALL GEMINI ADAPTER TESTS PASSED SUCCESSFULLY!")
