#!/usr/bin/env python3
"""Generate sample Copilot OTel spans for dashboard demonstration.

Run:  python3 generate_sample_data.py
Then: python3 -m agentdash.ingest .spans/sample.json
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone


def ts(base, offset_s=0):
    t = base + timedelta(seconds=offset_s)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uid():
    return uuid.uuid4().hex[:16]


SESSION_1 = "sess-" + uuid.uuid4().hex[:8]
SESSION_2 = "sess-" + uuid.uuid4().hex[:8]

BASE1 = datetime(2026, 8, 9, 9, 0, 0, tzinfo=timezone.utc)
BASE2 = BASE1 + timedelta(hours=1)

SYSTEM_PROMPT = (
    "You are GitHub Copilot, an AI programming assistant. "
    "When asked for your name, you must respond with GitHub Copilot. "
    "Follow the user's requirements carefully and to the letter."
)

TOOLS_1 = json.dumps([
    {"name": "read_file", "description": "Read the contents of a file at a given path.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "File path"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file, creating it if it doesn't exist.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "grep_search", "description": "Search code using a regex pattern.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}}, "required": ["query"]}},
    {"name": "list_directory", "description": "List files and directories at a path.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "run_terminal_command", "description": "Execute a shell command in the workspace terminal.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "mcp_github_get_pull_request", "description": "Get details of a GitHub pull request.",
     "parameters": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"}}, "required": ["owner", "repo", "pull_number"]}},
])

TOOLS_2 = json.dumps([
    {"name": "read_file", "description": "Read the contents of a file at a given path.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file, creating it if it doesn't exist.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "list_directory", "description": "List files and directories at a path.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "run_terminal_command", "description": "Execute a shell command in the workspace terminal.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "grep_search", "description": "Search code using a regex pattern.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}}, "required": ["query"]}},
    {"name": "mcp_jira_get_issue", "description": "Retrieve a JIRA issue by key.",
     "parameters": {"type": "object", "properties": {"issue_key": {"type": "string"}}, "required": ["issue_key"]}},
    {"name": "mcp_github_create_pull_request", "description": "Create a new GitHub pull request.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "head": {"type": "string"}, "base": {"type": "string"}}, "required": ["title", "head", "base"]}},
])

spans = []

# ---------------------------------------------------------------------------
# SESSION 1: "Refactor authentication module" — 3 turns, gpt-4o, no cache
# Token reconciliation: root.input = sum(chat.input)
#   Turn 1: 3500   Turn 2: 5200   Turn 3: 6300   Root: 15000  ✓
#   Turn 1 out: 350  Turn 2 out: 520  Turn 3 out: 330  Root out: 1200  ✓
# ---------------------------------------------------------------------------
T1 = uuid.uuid4().hex
ROOT1   = uid()
CHAT1_1 = uid(); CHAT1_2 = uid(); CHAT1_3 = uid()
TOOL1_RF = uid(); TOOL1_GR = uid(); TOOL1_WF = uid(); TOOL1_RUN = uid()

spans.append({
    "spanId": ROOT1, "traceId": T1, "parentSpanId": None,
    "source": "copilot-chat", "name": "invoke_agent implement",
    "kind": "server", "timestamp": ts(BASE1, 0), "durationMs": 46000, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_1,
        "copilot_chat.user_request": "Refactor the authentication module to use JWT tokens instead of sessions",
        "copilot_chat.turn_count": 3,
        "github.copilot.git.repository": "github.com/acme/webapp",
        "github.copilot.git.branch": "feature/jwt-auth",
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 15000,
        "gen_ai.usage.output_tokens": 1200,
    }
})

# Turn 1
spans.append({
    "spanId": CHAT1_1, "traceId": T1, "parentSpanId": ROOT1,
    "source": "copilot-chat", "name": "chat",
    "kind": "client", "timestamp": ts(BASE1, 1), "durationMs": 8200, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "chat",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_1,
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 3500,
        "gen_ai.usage.output_tokens": 350,
        "gen_ai.response.finish_reasons": json.dumps(["tool_calls"]),
        "copilot_chat.time_to_first_token": 420,
        "gen_ai.system_instructions": SYSTEM_PROMPT,
        "gen_ai.input.messages": json.dumps([
            {"role": "system", "parts": [{"type": "text", "content": SYSTEM_PROMPT}]},
            {"role": "user", "parts": [{"type": "text", "content": "Refactor the authentication module to use JWT tokens instead of sessions"}]},
        ]),
        "gen_ai.output.messages": json.dumps([
            {"role": "assistant", "parts": [{"type": "text", "content": "I'll help refactor the auth module. Let me start by reading the current implementation."},
                                             {"type": "tool_call", "id": "tc1", "name": "read_file", "arguments": {"path": "src/auth.py"}}]},
        ]),
        "gen_ai.tool.definitions": TOOLS_1,
    }
})

spans.append({
    "spanId": TOOL1_RF, "traceId": T1, "parentSpanId": CHAT1_1,
    "source": "copilot-chat", "name": "execute_tool read_file",
    "kind": "internal", "timestamp": ts(BASE1, 9.3), "durationMs": 140, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "read_file",
        "gen_ai.tool.type": "function",
        "copilot_chat.chat_session_id": SESSION_1,
        "gen_ai.tool.call.arguments": json.dumps({"path": "src/auth.py"}),
        "gen_ai.tool.call.result": (
            "import uuid\nfrom datetime import datetime\n\n"
            "class AuthManager:\n    def __init__(self):\n        self.sessions = {}\n\n"
            "    def login(self, username, password):\n        if self._verify(username, password):\n"
            "            token = uuid.uuid4().hex\n            self.sessions[token] = {'user': username, 'created': datetime.utcnow()}\n"
            "            return token\n        return None\n\n"
            "    def logout(self, token):\n        self.sessions.pop(token, None)\n\n"
            "    def verify_token(self, token):\n        return token in self.sessions\n"
        ),
    }
})

spans.append({
    "spanId": TOOL1_GR, "traceId": T1, "parentSpanId": CHAT1_1,
    "source": "copilot-chat", "name": "execute_tool grep_search",
    "kind": "internal", "timestamp": ts(BASE1, 9.5), "durationMs": 75, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "grep_search",
        "gen_ai.tool.type": "function",
        "copilot_chat.chat_session_id": SESSION_1,
        "gen_ai.tool.call.arguments": json.dumps({"query": "AuthManager|verify_token|from auth", "path": "src/"}),
        "gen_ai.tool.call.result": (
            "src/app.py:3: from auth import AuthManager\n"
            "src/routes/user.py:8: from auth import AuthManager\n"
            "src/routes/user.py:42:     if not auth.verify_token(request.headers.get('X-Token')):\n"
            "src/middleware.py:15: from auth import AuthManager\n"
        ),
    }
})

# Turn 2
spans.append({
    "spanId": CHAT1_2, "traceId": T1, "parentSpanId": ROOT1,
    "source": "copilot-chat", "name": "chat",
    "kind": "client", "timestamp": ts(BASE1, 10), "durationMs": 16000, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "chat",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_1,
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 5200,
        "gen_ai.usage.output_tokens": 520,
        "gen_ai.response.finish_reasons": json.dumps(["tool_calls"]),
        "copilot_chat.time_to_first_token": 650,
        "gen_ai.input.messages": json.dumps([
            {"role": "system", "parts": [{"type": "text", "content": SYSTEM_PROMPT}]},
            {"role": "user", "parts": [{"type": "text", "content": "Refactor the authentication module to use JWT tokens instead of sessions"}]},
            {"role": "assistant", "parts": [{"type": "text", "content": "I'll help refactor the auth module."},
                                             {"type": "tool_call", "id": "tc1", "name": "read_file", "arguments": {"path": "src/auth.py"}}]},
            {"role": "user", "parts": [{"type": "tool_call_result", "response": "class AuthManager: ..."},
                                        {"type": "tool_call_result", "response": "src/app.py:3: from auth import ..."}]},
        ]),
        "gen_ai.output.messages": json.dumps([
            {"role": "assistant", "parts": [
                {"type": "text", "content": "Now I'll write the JWT-based implementation."},
                {"type": "tool_call", "id": "tc2", "name": "write_file",
                 "arguments": {"path": "src/auth.py", "content": "import jwt\n..."}}
            ]},
        ]),
        "gen_ai.tool.definitions": TOOLS_1,
    }
})

spans.append({
    "spanId": TOOL1_WF, "traceId": T1, "parentSpanId": CHAT1_2,
    "source": "copilot-chat", "name": "execute_tool write_file",
    "kind": "internal", "timestamp": ts(BASE1, 26.1), "durationMs": 95, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "write_file",
        "gen_ai.tool.type": "function",
        "copilot_chat.chat_session_id": SESSION_1,
        "gen_ai.tool.call.arguments": json.dumps({"path": "src/auth.py", "content": "import jwt\nfrom datetime import datetime, timedelta\n\nSECRET_KEY = 'change-me'\n\nclass AuthManager:\n    def login(self, username, password):\n        if self._verify(username, password):\n            return jwt.encode({'sub': username, 'exp': datetime.utcnow() + timedelta(hours=24)}, SECRET_KEY)\n        return None\n\n    def verify_token(self, token):\n        try:\n            return jwt.decode(token, SECRET_KEY, algorithms=['HS256'])\n        except jwt.InvalidTokenError:\n            return None\n"}),
        "gen_ai.tool.call.result": "File written successfully (42 lines)",
    }
})

# Turn 3
spans.append({
    "spanId": CHAT1_3, "traceId": T1, "parentSpanId": ROOT1,
    "source": "copilot-chat", "name": "chat",
    "kind": "client", "timestamp": ts(BASE1, 27), "durationMs": 13000, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "chat",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_1,
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 6300,
        "gen_ai.usage.output_tokens": 330,
        "gen_ai.response.finish_reasons": json.dumps(["tool_calls"]),
        "copilot_chat.time_to_first_token": 510,
        "gen_ai.tool.definitions": TOOLS_1,
    }
})

spans.append({
    "spanId": TOOL1_RUN, "traceId": T1, "parentSpanId": CHAT1_3,
    "source": "copilot-chat", "name": "execute_tool run_terminal_command",
    "kind": "internal", "timestamp": ts(BASE1, 40.1), "durationMs": 2400, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "run_terminal_command",
        "gen_ai.tool.type": "function",
        "copilot_chat.chat_session_id": SESSION_1,
        "gen_ai.tool.call.arguments": json.dumps({"command": "pytest src/tests/test_auth.py -v"}),
        "gen_ai.tool.call.result": (
            "============================== test session starts ==============================\n"
            "src/tests/test_auth.py::test_login_valid PASSED\n"
            "src/tests/test_auth.py::test_login_invalid PASSED\n"
            "src/tests/test_auth.py::test_token_verify PASSED\n"
            "src/tests/test_auth.py::test_token_expired PASSED\n"
            "============================== 4 passed in 0.48s ==============================="
        ),
    }
})

# ---------------------------------------------------------------------------
# SESSION 2: "Add unit tests for the payment service" — 4 turns, claude-sonnet-5
# Claude uses cache_creation on turn 1, cache_read on subsequent turns.
# input_tokens is INCLUSIVE of cache (OTel GenAI convention).
#
# Token reconciliation:
#   T1: input=8000, cache_creation=2000, fresh=6000, output=800
#   T2: input=12000, cache_read=7000,  fresh=5000, output=1200
#   T3: input=15500, cache_read=11000, fresh=4500, output=650
#   T4: input=17500, cache_read=14000, fresh=3500, output=350
#   Root: input=53000 (8000+12000+15500+17500), output=3000 ✓
# ---------------------------------------------------------------------------
T2 = uuid.uuid4().hex
ROOT2   = uid()
CHAT2_1 = uid(); CHAT2_2 = uid(); CHAT2_3 = uid(); CHAT2_4 = uid()
TOOL2_LS = uid(); TOOL2_RF = uid(); TOOL2_WF = uid(); TOOL2_RUN = uid()

spans.append({
    "spanId": ROOT2, "traceId": T2, "parentSpanId": None,
    "source": "copilot-chat", "name": "invoke_agent implement",
    "kind": "server", "timestamp": ts(BASE2, 0), "durationMs": 97000, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_2,
        "copilot_chat.user_request": "Add comprehensive unit tests for the payment service, including edge cases for refunds and failed transactions",
        "copilot_chat.turn_count": 4,
        "github.copilot.git.repository": "github.com/acme/webapp",
        "github.copilot.git.branch": "feature/payment-tests",
        "gen_ai.request.model": "claude-sonnet-5",
        "gen_ai.response.model": "claude-sonnet-5",
        "gen_ai.usage.input_tokens": 53000,
        "gen_ai.usage.output_tokens": 3000,
    }
})

# Turn 1 — first turn, cache_creation fires
spans.append({
    "spanId": CHAT2_1, "traceId": T2, "parentSpanId": ROOT2,
    "source": "copilot-chat", "name": "chat",
    "kind": "client", "timestamp": ts(BASE2, 1), "durationMs": 19000, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "chat",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_2,
        "gen_ai.request.model": "claude-sonnet-5",
        "gen_ai.response.model": "claude-sonnet-5",
        "gen_ai.usage.input_tokens": 8000,
        "gen_ai.usage.cache_creation.input_tokens": 2000,
        "gen_ai.usage.output_tokens": 800,
        "gen_ai.response.finish_reasons": json.dumps(["tool_calls"]),
        "copilot_chat.time_to_first_token": 920,
        "gen_ai.system_instructions": SYSTEM_PROMPT,
        "gen_ai.input.messages": json.dumps([
            {"role": "system", "parts": [{"type": "text", "content": SYSTEM_PROMPT}]},
            {"role": "user", "parts": [{"type": "text", "content": "Add comprehensive unit tests for the payment service, including edge cases for refunds and failed transactions"}]},
        ]),
        "gen_ai.output.messages": json.dumps([
            {"role": "assistant", "parts": [
                {"type": "text", "content": "I'll add comprehensive unit tests. Let me first explore the payment service structure."},
                {"type": "tool_call", "id": "tc1", "name": "list_directory", "arguments": {"path": "src/payments"}},
            ]},
        ]),
        "gen_ai.tool.definitions": TOOLS_2,
    }
})

spans.append({
    "spanId": TOOL2_LS, "traceId": T2, "parentSpanId": CHAT2_1,
    "source": "copilot-chat", "name": "execute_tool list_directory",
    "kind": "internal", "timestamp": ts(BASE2, 20.1), "durationMs": 55, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "list_directory",
        "gen_ai.tool.type": "function",
        "copilot_chat.chat_session_id": SESSION_2,
        "gen_ai.tool.call.arguments": json.dumps({"path": "src/payments"}),
        "gen_ai.tool.call.result": "payment_service.py\nmodels.py\nexceptions.py\nprocessors/\ntests/",
    }
})

spans.append({
    "spanId": TOOL2_RF, "traceId": T2, "parentSpanId": CHAT2_1,
    "source": "copilot-chat", "name": "execute_tool read_file",
    "kind": "internal", "timestamp": ts(BASE2, 20.2), "durationMs": 130, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "read_file",
        "gen_ai.tool.type": "function",
        "copilot_chat.chat_session_id": SESSION_2,
        "gen_ai.tool.call.arguments": json.dumps({"path": "src/payments/payment_service.py"}),
        "gen_ai.tool.call.result": (
            "from .models import Payment, PaymentStatus\nfrom .exceptions import PaymentError, InsufficientFundsError\n\n"
            "class PaymentService:\n    def __init__(self, processor):\n        self.processor = processor\n\n"
            "    def charge(self, amount: float, card_token: str, currency: str = 'USD') -> Payment:\n"
            "        if amount <= 0:\n            raise PaymentError('Amount must be positive')\n"
            "        result = self.processor.charge(amount, card_token)\n"
            "        return Payment(id=result['id'], amount=amount, status=PaymentStatus.COMPLETED)\n\n"
            "    def refund(self, payment_id: str, amount: float = None) -> Payment:\n"
            "        payment = self._get_payment(payment_id)\n"
            "        refund_amount = amount or payment.amount\n"
            "        if refund_amount > payment.amount:\n            raise PaymentError('Refund exceeds original charge')\n"
            "        result = self.processor.refund(payment_id, refund_amount)\n"
            "        return Payment(id=result['id'], amount=refund_amount, status=PaymentStatus.REFUNDED)\n"
        ),
    }
})

# Turn 2 — cache_read kicks in
spans.append({
    "spanId": CHAT2_2, "traceId": T2, "parentSpanId": ROOT2,
    "source": "copilot-chat", "name": "chat",
    "kind": "client", "timestamp": ts(BASE2, 21), "durationMs": 26000, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "chat",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_2,
        "gen_ai.request.model": "claude-sonnet-5",
        "gen_ai.response.model": "claude-sonnet-5",
        "gen_ai.usage.input_tokens": 12000,
        "gen_ai.usage.cache_read.input_tokens": 7000,
        "gen_ai.usage.output_tokens": 1200,
        "gen_ai.response.finish_reasons": json.dumps(["tool_calls"]),
        "copilot_chat.time_to_first_token": 345,
        "gen_ai.tool.definitions": TOOLS_2,
    }
})

spans.append({
    "spanId": TOOL2_WF, "traceId": T2, "parentSpanId": CHAT2_2,
    "source": "copilot-chat", "name": "execute_tool write_file",
    "kind": "internal", "timestamp": ts(BASE2, 47.1), "durationMs": 115, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "write_file",
        "gen_ai.tool.type": "function",
        "copilot_chat.chat_session_id": SESSION_2,
        "gen_ai.tool.call.arguments": json.dumps({"path": "src/payments/tests/test_payment_service.py",
                                                   "content": "import pytest\nfrom unittest.mock import MagicMock\nfrom ..payment_service import PaymentService\n..."}),
        "gen_ai.tool.call.result": "File written successfully (98 lines)",
    }
})

# Turn 3
spans.append({
    "spanId": CHAT2_3, "traceId": T2, "parentSpanId": ROOT2,
    "source": "copilot-chat", "name": "chat",
    "kind": "client", "timestamp": ts(BASE2, 48), "durationMs": 21000, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "chat",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_2,
        "gen_ai.request.model": "claude-sonnet-5",
        "gen_ai.response.model": "claude-sonnet-5",
        "gen_ai.usage.input_tokens": 15500,
        "gen_ai.usage.cache_read.input_tokens": 11000,
        "gen_ai.usage.output_tokens": 650,
        "gen_ai.response.finish_reasons": json.dumps(["tool_calls"]),
        "copilot_chat.time_to_first_token": 280,
        "gen_ai.tool.definitions": TOOLS_2,
    }
})

# Turn 4 — final, runs the tests
spans.append({
    "spanId": CHAT2_4, "traceId": T2, "parentSpanId": ROOT2,
    "source": "copilot-chat", "name": "chat",
    "kind": "client", "timestamp": ts(BASE2, 70), "durationMs": 16000, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "chat",
        "gen_ai.agent.name": "copilot",
        "github.copilot.agent.type": "workspace",
        "copilot_chat.chat_session_id": SESSION_2,
        "gen_ai.request.model": "claude-sonnet-5",
        "gen_ai.response.model": "claude-sonnet-5",
        "gen_ai.usage.input_tokens": 17500,
        "gen_ai.usage.cache_read.input_tokens": 14000,
        "gen_ai.usage.output_tokens": 350,
        "gen_ai.response.finish_reasons": json.dumps(["stop"]),
        "copilot_chat.time_to_first_token": 255,
        "gen_ai.tool.definitions": TOOLS_2,
    }
})

spans.append({
    "spanId": TOOL2_RUN, "traceId": T2, "parentSpanId": CHAT2_4,
    "source": "copilot-chat", "name": "execute_tool run_terminal_command",
    "kind": "internal", "timestamp": ts(BASE2, 86.1), "durationMs": 5800, "status": "Ok",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "run_terminal_command",
        "gen_ai.tool.type": "function",
        "copilot_chat.chat_session_id": SESSION_2,
        "gen_ai.tool.call.arguments": json.dumps({"command": "pytest src/payments/tests/ -v --tb=short"}),
        "gen_ai.tool.call.result": (
            "============================== test session starts ==============================\n"
            "src/payments/tests/test_payment_service.py::test_charge_valid PASSED\n"
            "src/payments/tests/test_payment_service.py::test_charge_negative_amount PASSED\n"
            "src/payments/tests/test_payment_service.py::test_charge_zero_amount PASSED\n"
            "src/payments/tests/test_payment_service.py::test_refund_full PASSED\n"
            "src/payments/tests/test_payment_service.py::test_refund_partial PASSED\n"
            "src/payments/tests/test_payment_service.py::test_refund_exceeds_original PASSED\n"
            "src/payments/tests/test_payment_service.py::test_processor_failure PASSED\n"
            "src/payments/tests/test_payment_service.py::test_currency_codes PASSED\n"
            "src/payments/tests/test_payment_service.py::test_idempotency_key PASSED\n"
            "============================== 9 passed in 1.83s ==============================="
        ),
    }
})

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
os.makedirs(".spans", exist_ok=True)
out = ".spans/sample.json"
with open(out, "w") as f:
    json.dump(spans, f, indent=2)

print(f"Generated {len(spans)} spans → {out}")
print(f"  Session 1 ({SESSION_1}): 3 turns · gpt-4o · no cache")
print(f"  Session 2 ({SESSION_2}): 4 turns · claude-sonnet-5 · cache_creation + cache_read")
print()
print("Next: python3 -m agentdash.ingest .spans/sample.json")
