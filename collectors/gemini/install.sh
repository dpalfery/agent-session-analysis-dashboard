#!/usr/bin/env bash
set -e

echo "⚠️ DEPRECATED: This installer is kept for compatibility."
echo "   Use: kyber-observe install gemini --component statusline"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.gemini/antigravity-cli"
TARGET_STATUSLINE="$TARGET_DIR/statusline.py"
TARGET_SETTINGS="$TARGET_DIR/settings.json"
SOURCE_STATUSLINE="$SCRIPT_DIR/statusline.py"

echo "🚀 Installing Antigravity CLI Status Line Collector..."

# Ensure target directory exists
mkdir -p "$TARGET_DIR"

# Copy statusline.py
echo "📦 Copying $SOURCE_STATUSLINE -> $TARGET_STATUSLINE"
cp "$SOURCE_STATUSLINE" "$TARGET_STATUSLINE"
chmod +x "$TARGET_STATUSLINE"

# Update or verify settings.json
if [ -f "$TARGET_SETTINGS" ]; then
    echo "⚙️ Checking settings at $TARGET_SETTINGS..."
    python3 -c "
import json, os
path = os.path.expanduser('$TARGET_SETTINGS')
with open(path, 'r') as f:
    data = json.load(f)
sl = data.get('statusLine', {})
sl['enabled'] = True
sl['type'] = 'command'
sl['command'] = 'python3 ' + os.path.expanduser('$TARGET_STATUSLINE')
data['statusLine'] = sl
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
"
    echo "✅ Updated $TARGET_SETTINGS"
else
    echo "⚙️ Creating initial $TARGET_SETTINGS..."
    cat <<EOF > "$TARGET_SETTINGS"
{
  "allowNonWorkspaceAccess": true,
  "statusLine": {
    "type": "command",
    "command": "python3 $TARGET_STATUSLINE",
    "enabled": true
  }
}
EOF
    echo "✅ Created $TARGET_SETTINGS"
fi

# Verification step
echo "🧪 Running verification self-test..."
SAMPLE_STDIN="$TARGET_DIR/statusline_last_stdin.json"
if [ -f "$SAMPLE_STDIN" ]; then
    cat "$SAMPLE_STDIN" | python3 "$TARGET_STATUSLINE"
else
    echo '{"cwd": "'"$PWD"'", "model": "gemini-3.6-flash", "quota": {"gemini-5h": {"remaining_fraction": 0.5, "reset_in_seconds": 3600}}}' | python3 "$TARGET_STATUSLINE"
fi

echo "🎉 Status bar collector successfully published and verified!"
