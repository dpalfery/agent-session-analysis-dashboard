#!/usr/bin/env python3
"""Export completed AGY transcript records as OTLP/HTTP spans.

The AGY hook process is short-lived, so deduplication is persisted outside the
plugin directory. Hook stdout is reserved for the AGY JSON hook response.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path


PLUGIN_VERSION = "0.1.0"
TRACE_NAMESPACE = uuid.UUID("7fce1b38-4c7e-4acb-9df1-4e2b91c8df00")


def _int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _attr(key, value):
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": value}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _json_text(value, limit=None):
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if limit and len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _iso_ns(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1_000_000_000)
    except (TypeError, ValueError, OverflowError):
        return None


def _record_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "\n".join(parts)
    if value is None:
        return ""
    return _json_text(value) or ""


def load_records(path):
    """Read the append-only transcript and fold duplicate record updates."""
    records = OrderedDict()
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or "$set" in record:
                continue
            record_id = record.get("id")
            if record_id:
                records[record_id] = record
    return list(records.values())


def _workspace(payload):
    paths = payload.get("workspacePaths") or payload.get("workspace_paths") or []
    if isinstance(paths, str):
        paths = [paths]
    return paths[0] if paths else payload.get("cwd") or os.getcwd()


def _repo_branch(workspace):
    repo = os.path.basename(os.path.abspath(workspace)) or "workspace"
    branch = ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return repo, branch


def _payload_value(payload, camel, snake=None):
    return payload.get(camel) if camel in payload else payload.get(snake or camel)


def _transcript_path(payload):
    return _payload_value(payload, "transcriptPath", "transcript_path")


def _conversation_id(payload, records):
    value = _payload_value(payload, "conversationId", "conversation_id")
    if value:
        return str(value)
    for record in records:
        if record.get("sessionId"):
            return str(record["sessionId"])
    return "unknown"


def _session_attrs(payload, conversation_id):
    workspace = _workspace(payload)
    repo, branch = _repo_branch(workspace)
    return {
        "gen_ai.system": "gemini",
        "gen_ai.provider.name": "google",
        "gen_ai.agent.name": "antigravity",
        "gen_ai.session.id": conversation_id,
        "copilot_chat.chat_session_id": conversation_id,
        "vcs.repository.name": repo,
        "vcs.ref.head.name": branch or "",
        "agy.plugin.version": PLUGIN_VERSION,
    }


def _span_ids(conversation_id, record_id, kind):
    trace_id = uuid.uuid5(TRACE_NAMESPACE, conversation_id).hex
    span_id = uuid.uuid5(TRACE_NAMESPACE, f"{conversation_id}:{kind}:{record_id}").hex[:16]
    return trace_id, span_id


def _messages(previous_user, capture_content):
    if not capture_content or not previous_user:
        return None
    text = _record_text(previous_user.get("content"))
    if not text:
        return None
    return [{"role": "user", "parts": [{"type": "text", "text": text}]}]


def _model_span(payload, record, previous_user, event):
    conversation_id = _conversation_id(payload, [record])
    record_id = str(record["id"])
    trace_id, span_id = _span_ids(conversation_id, record_id, "chat")
    tokens = record.get("tokens") or {}
    model = record.get("model") or _payload_value(payload, "modelName", "model_name") or "unknown"
    start_ns = _iso_ns(record.get("timestamp")) or time.time_ns()
    end_ns = max(start_ns + 1_000_000, time.time_ns())
    capture_content = os.environ.get("AGY_OTEL_CAPTURE_CONTENT", "1") != "0"
    max_content = _int(os.environ.get("AGY_OTEL_MAX_CONTENT", "200000")) or 200000
    attrs = _session_attrs(payload, conversation_id)
    attrs.update({
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model,
        "gen_ai.response.model": model,
        "agy.hook.event": event,
        "agy.transcript.record_id": record_id,
    })
    for source, target in (
        ("input", "gen_ai.usage.input_tokens"),
        ("cached", "gen_ai.usage.cache_read.input_tokens"),
        ("output", "gen_ai.usage.output_tokens"),
        ("thoughts", "gen_ai.usage.reasoning.output_tokens"),
        ("thoughts", "agy.usage.thought_tokens"),
        ("tool", "agy.usage.tool_tokens"),
        ("total", "agy.usage.total_tokens"),
    ):
        value = _int(tokens.get(source))
        if value is not None:
            attrs[target] = value
    input_messages = _messages(previous_user, capture_content)
    if input_messages:
        attrs["gen_ai.input.messages"] = _json_text(input_messages, max_content)
    output_text = _record_text(record.get("content"))
    if capture_content and output_text:
        attrs["gen_ai.output.messages"] = _json_text(
            [{"role": "assistant", "parts": [{"type": "text", "text": output_text}]}],
            max_content,
        )
    if capture_content and record.get("thoughts"):
        attrs["agy.thoughts"] = _json_text(record["thoughts"], max_content)
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": f"chat {model}",
        "kind": 3,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [_attr("gen_ai.operation.name", attrs.pop("gen_ai.operation.name"))]
        + [_attr(key, value) for key, value in attrs.items()],
        "status": {"code": 1},
    }


def _tool_span(payload, record, call, parent_id, event):
    conversation_id = _conversation_id(payload, [record])
    tool_id = str(call.get("id") or f"{record.get('id')}:tool:{call.get('name', 'unknown')}")
    trace_id, span_id = _span_ids(conversation_id, tool_id, "tool")
    model = record.get("model") or "unknown"
    timestamp_ns = _iso_ns(call.get("timestamp")) or _iso_ns(record.get("timestamp")) or time.time_ns()
    max_content = _int(os.environ.get("AGY_OTEL_MAX_CONTENT", "200000")) or 200000
    capture_content = os.environ.get("AGY_OTEL_CAPTURE_CONTENT", "1") != "0"
    attrs = _session_attrs(payload, conversation_id)
    attrs.update({
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.request.model": model,
        "gen_ai.tool.name": call.get("name") or "unknown",
        "agy.hook.event": event,
        "agy.transcript.tool_id": tool_id,
    })
    if capture_content:
        arguments = call.get("args")
        result = call.get("result")
        if arguments is not None:
            attrs["gen_ai.tool.call.arguments"] = _json_text(arguments, max_content)
        if result is not None:
            attrs["gen_ai.tool.call.result"] = _json_text(result, max_content)
    status = str(call.get("status") or "").lower()
    failed = status not in ("", "success", "ok", "completed")
    attrs["agy.tool.status"] = status or "unknown"
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_id,
        "name": f"execute_tool {call.get('name') or 'unknown'}",
        "kind": 3,
        "startTimeUnixNano": str(max(1, timestamp_ns - 1_000_000)),
        "endTimeUnixNano": str(timestamp_ns),
        "attributes": [_attr("gen_ai.operation.name", attrs.pop("gen_ai.operation.name"))]
        + [_attr(key, value) for key, value in attrs.items()],
        "status": {"code": 2 if failed else 1},
    }


def build_spans(payload, event, emitted_models=None, emitted_tools=None, tools_only=False):
    """Build new model/tool spans without performing network or state I/O."""
    emitted_models = emitted_models or set()
    emitted_tools = emitted_tools or set()
    path = _transcript_path(payload)
    if not path or not os.path.isfile(path):
        return [], set(), set()
    records = load_records(path)
    conversation_id = _conversation_id(payload, records)
    spans = []
    model_ids = set()
    tool_ids = set()
    for index, record in enumerate(records):
        if record.get("type") not in ("gemini", "model", "assistant") or not record.get("tokens"):
            continue
        record_id = str(record.get("id") or "")
        if not record_id:
            continue
        previous_user = next(
            (candidate for candidate in reversed(records[:index]) if candidate.get("type") == "user"),
            None,
        )
        model_span_id = _span_ids(conversation_id, record_id, "chat")[1]
        if not tools_only and record_id not in emitted_models:
            spans.append(_model_span(payload, record, previous_user, event))
            model_ids.add(record_id)
        for call in record.get("toolCalls") or []:
            tool_id = str(call.get("id") or f"{record_id}:tool:{call.get('name', 'unknown')}")
            if tool_id in emitted_tools:
                continue
            spans.append(_tool_span(payload, record, call, model_span_id, event))
            tool_ids.add(tool_id)
    return spans, model_ids, tool_ids


def _state_path(payload):
    transcript = _transcript_path(payload) or _payload_value(payload, "conversationId", "conversation_id") or "unknown"
    digest = hashlib.sha256(str(transcript).encode()).hexdigest()[:24]
    root = Path(os.path.expanduser(os.environ.get("AGY_OTEL_STATE_DIR", "~/.gemini/agy-otel-telemetry")))
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{digest}.json"


def _load_state(path):
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
        return {
            "models": set(value.get("models", [])),
            "tools": set(value.get("tools", [])),
        }
    except (OSError, json.JSONDecodeError, AttributeError):
        return {"models": set(), "tools": set()}


def _save_state(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"models": sorted(state["models"]), "tools": sorted(state["tools"])}),
        encoding="utf-8",
    )
    temporary.replace(path)


def _endpoint():
    value = os.environ.get("AGY_OTEL_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
    value = value.rstrip("/")
    return value if value.endswith("/v1/traces") else value + "/v1/traces"


def _send(spans):
    payload = {
        "resourceSpans": [{
            "resource": {"attributes": [
                _attr("service.name", "agy"),
                _attr("service.version", PLUGIN_VERSION),
            ]},
            "scopeSpans": [{
                "scope": {"name": "agy-otel-telemetry", "version": PLUGIN_VERSION},
                "spans": spans,
            }],
        }],
    }
    request = urllib.request.Request(
        _endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("AGY_OTEL_TIMEOUT", "2"))) as response:
            return 200 <= response.status < 300
    except (OSError, ValueError):
        return False


def process(payload, event):
    if event == "pre-invocation":
        return
    path = _state_path(payload)
    state = _load_state(path)
    spans, model_ids, tool_ids = build_spans(
        payload,
        event,
        state["models"],
        state["tools"],
        tools_only=event == "post-tool-use",
    )
    if not spans or not _send(spans):
        return
    state["models"].update(model_ids)
    state["tools"].update(tool_ids)
    _save_state(path, state)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            process(payload, args.event)
    except Exception as error:  # Hook failures must not break the agent loop.
        print(f"agy-otel-telemetry: {type(error).__name__}: {error}", file=sys.stderr)
    print("{}")


if __name__ == "__main__":
    main()
