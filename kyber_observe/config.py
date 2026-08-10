"""Configuration paths and defaults for the Kyber Observe installer."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENDPOINT = "http://localhost:4318"

GEMINI_CLI_HOME = Path.home() / ".gemini" / "antigravity-cli"
GEMINI_PLUGINS_HOME = GEMINI_CLI_HOME / "plugins"
PI_AGENT_HOME = Path.home() / ".pi" / "agent"

_DEFAULT_KYBER_OBSERVE_HOME = Path.home() / ".config" / "kyber-observe"
KYBER_OBSERVE_HOME = Path(
    os.environ.get("KYBER_OBSERVE_HOME", str(_DEFAULT_KYBER_OBSERVE_HOME))
).expanduser()
