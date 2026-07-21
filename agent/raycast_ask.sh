#!/bin/bash

# @raycast.schemaVersion 1
# @raycast.title Ask Knowledge Base
# @raycast.mode fullOutput
# @raycast.packageName Work Assistant
# @raycast.icon 🧠
# @raycast.argument1 { "type": "text", "placeholder": "Ask a question...", "percentEncoded": false }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
python3 ask.py "$1"
