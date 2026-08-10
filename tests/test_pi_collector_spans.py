"""
Verifies the pi statusline COLLECTOR's spans (collectors/pi/) flow through the
PiAdapter into the dashboard's canonical content model.

The collector emits gen_ai.system_instructions + gen_ai.input.messages +
gen_ai.output.messages + pi.skills/pi.tools.* alongside the usage/cost attrs.
This test pins the end-to-end contract: those attributes must land in
CanonicalSpan.content so views.bucket_context can break them into the
system-prompt / history / tool-result buckets.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdash.adapters.pi import PiAdapter
from agentdash.canonical import validate_tokens


def _collector_span(**extra_attrs):
    """A span shaped exactly like collectors/pi/src/otlp.ts emits."""
    attrs = {
        # Detection + grouping (PiAdapter fingerprint + session grouping).
        "observme.semconv.version": "1.0.0",
        "pi.session.id": "sess-1",
        "pi.agent.run.id": "run-1",
        "pi.turn.index": 0,
        "gen_ai.operation.name": "chat",
        "gen_ai.system": "pi",
        "gen_ai.request.model": "glm-5.2",
        # Exclusive token convention.
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 40,
        "pi.llm.usage.total_tokens": 160,
    }
    attrs.update(extra_attrs)
    return {
        "spanId": "s1",
        "traceId": "t1",
        "name": "pi.llm.request",
        "timestamp": "2026-08-10T01:00:00Z",
        "attributes": attrs,
    }


def test_collector_system_prompt_lands_in_content():
    span = _collector_span(**{
        "gen_ai.system_instructions": "You are pi, a coding agent.",
    })
    c = PiAdapter().normalize(span)
    assert c is not None
    assert c.content["system_instructions"] == "You are pi, a coding agent."


def test_collector_input_messages_normalize_to_canonical_parts():
    msgs = [
        {"role": "user", "parts": [{"type": "text", "text": "do the thing"}]},
        {"role": "toolResult",
         "parts": [{"type": "tool_result",
                    "raw": {"toolName": "read", "isError": False}}]},
    ]
    span = _collector_span(**{
        "gen_ai.system_instructions": "sys",
        "gen_ai.input.messages": json.dumps(msgs),
    })
    c = PiAdapter().normalize(span)
    norm = c.content["input_messages"]
    # user text part + tool_result part both present; bucketing is by part type
    assert norm[0]["role"] == "user"
    assert norm[0]["parts"][0]["type"] == "text"
    assert norm[1]["parts"][0]["type"] == "tool_result"
    # System prompt still captured alongside the messages.
    assert c.content["system_instructions"] == "sys"
    # No token invariants violated.
    assert validate_tokens(c) == []


def test_collector_skills_and_tools_visible_in_raw_attributes():
    span = _collector_span(**{
        "pi.skills": json.dumps([{"name": "conductor"}]),
        "pi.tools.selected": json.dumps(["read", "bash"]),
    })
    c = PiAdapter().normalize(span)
    assert c.raw_attributes["pi.skills"] == json.dumps([{"name": "conductor"}])
    assert c.raw_attributes["pi.tools.selected"] == json.dumps(["read", "bash"])


if __name__ == "__main__":
    test_collector_system_prompt_lands_in_content()
    test_collector_input_messages_normalize_to_canonical_parts()
    test_collector_skills_and_tools_visible_in_raw_attributes()
    print("ALL PI COLLECTOR SPAN TESTS PASSED SUCCESSFULLY!")
