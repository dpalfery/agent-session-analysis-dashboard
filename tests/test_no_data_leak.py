#!/usr/bin/env python3
"""
test_no_data_leak.py -- assert the tracked UI carries no captured telemetry.

The whole reason ui/ is committed while spans*.json and sessions.db are not is
that the UI fetches its data at runtime. If anyone ever re-inlines a payload at
build time, this fails -- and it fails before the content reaches git history,
where it would be permanent.

    python3 tests/test_no_data_leak.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Generic markers -- safe to publish. These are structural: they appear in
# captured telemetry regardless of whose repo produced it.
MARKERS = [
    "dev.azure.com", "blob.core.windows.net", "githubusercontent.com",
    "gen_ai.input.messages", "gen_ai.tool.definitions",
    "copilot_chat.chat_session_id", "chat-session-resources",
    "/Users/", "/home/",
]

# Deployment-specific markers (org names, repo names, storage accounts) belong
# in a LOCAL file, not in a committed test. Naming them here would publish the
# very identifiers the check exists to keep out of the repo.
#
#   tests/markers.local.txt   one marker per line, '#' comments ignored
#
# The file is gitignored. Without it the check still works, just less
# specifically -- so keep one locally if you analyse private repos.
LOCAL_MARKERS_FILE = Path(__file__).parent / "markers.local.txt"

TRACKED_UI = ["ui/dashboard.html", "ui/app.js", "ui/app.css"]
MAX_UI_BYTES = 200 * 1024      # a data-free UI has no reason to be large


def load_markers():
    markers = list(MARKERS)
    if LOCAL_MARKERS_FILE.exists():
        for line in LOCAL_MARKERS_FILE.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                markers.append(line)
    return markers


def main():
    failures = []
    markers = load_markers()
    extra = len(markers) - len(MARKERS)
    print(f"  {len(markers)} markers"
          + (f" ({extra} from markers.local.txt)" if extra else
             "  [no markers.local.txt -- generic checks only]"))

    for rel in TRACKED_UI:
        p = ROOT / rel
        if not p.exists():
            failures.append(f"{rel}: missing")
            continue
        text = p.read_text(errors="replace")
        for m in markers:
            if m.lower() in text.lower():
                failures.append(f"{rel}: contains captured-data marker {m!r}")
        if p.stat().st_size > MAX_UI_BYTES:
            failures.append(f"{rel}: {p.stat().st_size:,} bytes exceeds "
                            f"{MAX_UI_BYTES:,} -- is a payload inlined?")

    # Nothing gitignored should ever be staged.
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True).stdout.split()
        for bad in tracked:
            if (bad.startswith("spans") and bad.endswith(".json")) or \
               bad in ("session.json", "sessions.db", "dashboard.html"):
                failures.append(f"data file is TRACKED IN GIT: {bad}")
    except Exception as e:
        print(f"  (git check skipped: {e})")

    for rel in TRACKED_UI:
        p = ROOT / rel
        if p.exists():
            print(f"  {rel:22} {p.stat().st_size:>7,} bytes  clean")

    if failures:
        print("\nDATA LEAK CHECK FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nno captured data in the tracked UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
