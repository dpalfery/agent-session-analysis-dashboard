import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdash.adapters import registry


def test_gemini_mimicking_copilot_routes_to_gemini():
    span = {
        "spanId": "gemini-mimic",
        "traceId": "trace-gemini",
        "source": "agy",
        "name": "chat gemini-3.6-flash",
        "attributes": {
            "gen_ai.system": "gemini",
            "gen_ai.operation.name": "chat",
            "copilot_chat.chat_session_id": "sess-gemini",
            "github.copilot.tool.parameters.skill_name": "search",
        },
    }

    adapter, confidence = registry.score_span(span)

    assert adapter is not None
    assert adapter.name == "gemini"
    assert confidence >= registry.MIN_CONFIDENCE
    assert registry.detect_groups([span])[span["spanId"]] == "gemini"


def test_real_copilot_span_routes_to_copilot():
    span = {
        "spanId": "copilot-real",
        "traceId": "trace-copilot",
        "source": "vscode",
        "name": "chat",
        "attributes": {
            "gen_ai.system": None,
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": "100",
            "gen_ai.usage.output_tokens": "20",
            "github.copilot.agent.type": "main",
            "copilot_chat.chat_session_id": "sess-copilot",
        },
    }

    adapter, confidence = registry.score_span(span)

    assert adapter is not None
    assert adapter.name == "copilot"
    assert confidence >= registry.MIN_CONFIDENCE
    assert registry.detect_groups([span])[span["spanId"]] == "copilot"


def test_score_threshold_quarantines_unknown():
    span = {
        "spanId": "unknown-genai",
        "traceId": "trace-unknown",
        "source": "unknown",
        "name": "chat",
        "attributes": {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "some-model",
            "gen_ai.usage.input_tokens": "100",
            "gen_ai.usage.output_tokens": "10",
        },
    }

    assert registry.score_span(span)[0] is None


if __name__ == "__main__":
    test_gemini_mimicking_copilot_routes_to_gemini()
    test_real_copilot_span_routes_to_copilot()
    test_score_threshold_quarantines_unknown()
    print("ALL REGISTRY GEMINI DETECTION TESTS PASSED SUCCESSFULLY!")
