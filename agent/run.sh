#!/usr/bin/env bash
# Convenience wrapper: activate venv, load secrets, run the agent.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
[[ -f .venv/bin/activate ]] && source .venv/bin/activate
[[ -f .env ]] && source .env
exec python main.py "$@"
