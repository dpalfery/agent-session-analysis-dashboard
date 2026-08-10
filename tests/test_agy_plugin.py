import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "collectors/gemini/agy-otel-telemetry/telemetry.py"
SPEC = importlib.util.spec_from_file_location("agy_telemetry", PLUGIN)
agy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agy)


def _write_transcript(tmp_path):
    path = tmp_path / "transcript.jsonl"
    user = {
        "id": "user-1",
        "timestamp": "2026-08-10T01:00:00Z",
        "type": "user",
        "content": "Inspect the service",
    }
    model = {
        "id": "model-1",
        "timestamp": "2026-08-10T01:00:01Z",
        "type": "gemini",
        "content": "I will inspect it.",
        "tokens": {"input": 10, "cached": 4, "output": 3, "thoughts": 1, "tool": 2, "total": 20},
        "model": "gemini-3.6-flash",
    }
    updated = {
        **model,
        "toolCalls": [{
            "id": "tool-1",
            "name": "list_directory",
            "args": {"dir_path": "."},
            "result": {"output": "README.md"},
            "status": "success",
            "timestamp": "2026-08-10T01:00:02Z",
        }],
    }
    path.write_text("\n".join(json.dumps(item) for item in [user, model, {"$set": {}}, updated]) + "\n")
    return path


def test_load_records_folds_transcript_updates(tmp_path):
    path = _write_transcript(tmp_path)

    records = agy.load_records(path)

    assert [record["id"] for record in records] == ["user-1", "model-1"]
    assert records[-1]["toolCalls"][0]["id"] == "tool-1"


def test_build_spans_uses_real_tokens_and_tool_child(tmp_path, monkeypatch):
    path = _write_transcript(tmp_path)
    monkeypatch.setenv("AGY_OTEL_CAPTURE_CONTENT", "1")
    payload = {
        "conversationId": "conversation-1",
        "transcriptPath": str(path),
        "workspacePaths": [str(tmp_path)],
    }

    spans, models, tools = agy.build_spans(payload, "post-invocation")

    assert models == {"model-1"}
    assert tools == {"tool-1"}
    assert len(spans) == 2
    model = next(span for span in spans if span["name"].startswith("chat "))
    attrs = {item["key"]: item["value"] for item in model["attributes"]}
    assert attrs["gen_ai.usage.input_tokens"] == {"intValue": 10}
    assert attrs["gen_ai.usage.cache_read.input_tokens"] == {"intValue": 4}
    assert attrs["gen_ai.input.messages"]["stringValue"]
    tool = next(span for span in spans if span["name"].startswith("execute_tool "))
    assert tool["parentSpanId"] == model["spanId"]


def test_post_tool_use_does_not_emit_model_span(tmp_path):
    path = _write_transcript(tmp_path)
    payload = {
        "conversationId": "conversation-1",
        "transcriptPath": str(path),
        "workspacePaths": [str(tmp_path)],
    }

    spans, models, tools = agy.build_spans(payload, "post-tool-use", tools_only=True)

    assert models == set()
    assert tools == {"tool-1"}
    assert len(spans) == 1
    assert spans[0]["name"] == "execute_tool list_directory"
