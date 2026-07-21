#!/usr/bin/env bash
#
# Interactive first-run setup for Work Assistant.
# Usage: ./setup.sh
#
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$AGENT_DIR")"
cd "$AGENT_DIR"
export AGENT_DIR
VENV_DIR="${AGENT_DIR}/.venv"
ENV_FILE="${AGENT_DIR}/.env"
CONFIG_FILE="${AGENT_DIR}/config.yaml"
PEOPLE_FILE="${AGENT_DIR}/people.yaml"

# ─── Helpers ────────────────────────────────────────────────────────────────

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
dim()   { printf '\033[2m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[0;33m%s\033[0m\n' "$*"; }

prompt() {
  local message="$1"
  local default="${2:-}"
  local reply
  if [[ -n "$default" ]]; then
    read -r -p "${message} [${default}]: " reply
    reply="${reply:-$default}"
  else
    read -r -p "${message}: " reply
  fi
  printf '%s' "$reply"
}

prompt_yes_no() {
  local message="$1"
  local default="${2:-y}"
  local hint="Y/n"
  [[ "$default" == "n" ]] && hint="y/N"
  local reply
  read -r -p "${message} [${hint}]: " reply
  reply="${reply:-$default}"
  [[ "$reply" =~ ^[Yy] ]]
}

prompt_choice() {
  local message="$1"
  shift
  local options=("$@")
  local i choice
  echo "$message"
  for i in "${!options[@]}"; do
    echo "  $((i + 1))) ${options[$i]}"
  done
  while true; do
    read -r -p "Choice [1]: " choice
    choice="${choice:-1}"
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#options[@]} )); then
      echo "${options[$((choice - 1))]}"
      return
    fi
    yellow "Enter a number between 1 and ${#options[@]}."
  done
}

# ─── Banner ─────────────────────────────────────────────────────────────────

echo
bold "Work Assistant — Setup"
dim  "This script installs dependencies, configures your LLM backend,"
dim  "creates an Obsidian vault next to this folder, and writes config.yaml."
echo

if [[ -f "$CONFIG_FILE" ]]; then
  yellow "config.yaml already exists."
  if ! prompt_yes_no "Overwrite it?" "n"; then
    dim "Keeping existing config.yaml. Other steps will still run where safe."
    SKIP_CONFIG=1
  else
    cp "$CONFIG_FILE" "${CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
    green "Backed up existing config to ${CONFIG_FILE}.bak.*"
  fi
fi

# ─── Prerequisites ──────────────────────────────────────────────────────────

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but not found." >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"
dim "Using Python: $($PYTHON --version 2>&1)"

# ─── Step 1: LLM provider ───────────────────────────────────────────────────

bold "Step 1 — LLM backend"

PROVIDER="$(prompt_choice "Select your LLM provider:" "anthropic" "openai" "ollama")"

case "$PROVIDER" in
  anthropic)
    API_KEY_ENV="ANTHROPIC_API_KEY"
    DEFAULT_MODEL="claude-sonnet-4-20250514"
    DEFAULT_MODEL_MAIN="claude-opus-4-6"
    ;;
  openai)
    API_KEY_ENV="OPENAI_API_KEY"
    DEFAULT_MODEL="gpt-4o"
    DEFAULT_MODEL_MAIN="gpt-4o"
    ;;
  ollama)
    API_KEY_ENV=""
    DEFAULT_MODEL="llama3.1:latest"
    DEFAULT_MODEL_MAIN="llama3.1:latest"
    ;;
esac

API_KEY=""
if [[ "$PROVIDER" != "ollama" ]]; then
  echo
  dim "Your API key is saved to .env (gitignored) and referenced from config.yaml."
  read -r -s -p "Enter ${API_KEY_ENV}: " API_KEY
  echo
  if [[ -z "$API_KEY" ]]; then
    yellow "No key entered — you'll need to set ${API_KEY_ENV} before running the agent."
  fi
else
  if curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
    green "Ollama is reachable at http://localhost:11434"
  else
    yellow "Ollama doesn't appear to be running. Start it with: ollama serve"
  fi
fi

echo
dim "Cloud models are recommended for the main synthesis pass (large JSON output)."
dim "Local Ollama models work well for ask.py and pre-processing."
MODEL="$(prompt "Model name" "$DEFAULT_MODEL_MAIN")"

# ─── Step 2: Assistant identity (prompt customization) ──────────────────────

bold "Step 2 — Assistant identity"
dim "These values are injected into templates/system_prompt.md and ask_prompt.md."

ASSISTANT_NAME="$(prompt "Assistant display name" "Work Assistant")"
ROLE_DESC="$(prompt "Your role (e.g. Product Manager, Engineering Lead)" "Product Manager")"
TIMEZONE="$(prompt "Timezone (IANA name, e.g. America/New_York, Europe/Warsaw)" "America/New_York")"

# ─── Step 3: Data sources ───────────────────────────────────────────────────

bold "Step 3 — Data sources"
dim "Obsidian is required. Other sources can be configured later in config.yaml."

ENABLE_SLACK=0
ENABLE_GMAIL=0
ENABLE_JIRA=0
ENABLE_CONFLUENCE=0
ENABLE_MEETINGS=0

prompt_yes_no "Enable Slack monitoring?" "n" && ENABLE_SLACK=1
prompt_yes_no "Enable Gmail monitoring?" "n" && ENABLE_GMAIL=1
prompt_yes_no "Enable JIRA monitoring?" "n" && ENABLE_JIRA=1
prompt_yes_no "Enable Confluence monitoring?" "n" && ENABLE_CONFLUENCE=1
prompt_yes_no "Enable Google Calendar / Meet?" "n" && ENABLE_MEETINGS=1

# ─── Step 4: Domain context (optional prompt customization) ─────────────────

bold "Step 4 — Domain context (optional)"
dim "Describe your team's tools, ticket hierarchy, or workflow patterns."
dim "Saved to templates/domain/custom_context.md and injected into the system prompt."
echo "Press Enter on an empty line when done."
DOMAIN_LINES=()
while IFS= read -r line; do
  [[ -z "$line" ]] && break
  DOMAIN_LINES+=("$line")
done

CUSTOM_CONTEXT_FILE="${AGENT_DIR}/templates/domain/custom_context.md"
if ((${#DOMAIN_LINES[@]} > 0)); then
  {
    echo "# Custom work context"
    echo
    echo "Added during setup. Edit freely."
    echo
    for line in "${DOMAIN_LINES[@]}"; do
      echo "$line"
    done
  } > "$CUSTOM_CONTEXT_FILE"
  green "Wrote ${CUSTOM_CONTEXT_FILE}"
else
  dim "Skipped custom context."
fi

# ─── Step 5: Obsidian vault ─────────────────────────────────────────────────

bold "Step 5 — Obsidian vault"
DEFAULT_VAULT="${PARENT_DIR}/WorkVault"
dim "Default location: sibling folder to agent/ (${DEFAULT_VAULT})"

VAULT_INPUT="$(prompt "Vault path" "$DEFAULT_VAULT")"
# Expand ~ manually for display; Python will expand fully when writing config
VAULT_PATH="$VAULT_INPUT"

if [[ ! -d "$VAULT_PATH" ]]; then
  prompt_yes_no "Create vault at ${VAULT_PATH}?" "y" || {
    echo "Aborting — vault directory is required." >&2
    exit 1
  }
fi

# ─── Step 6: Python environment ─────────────────────────────────────────────

bold "Step 6 — Python environment"

if [[ ! -d "$VENV_DIR" ]]; then
  dim "Creating virtualenv at .venv ..."
  "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
pip install -q --upgrade pip
pip install -q -r "${AGENT_DIR}/requirements.txt"
green "Dependencies installed."

# ─── Step 7: Write .env ─────────────────────────────────────────────────────

if [[ -n "$API_KEY_ENV" && -n "$API_KEY" ]]; then
  {
    echo "# Work Assistant — generated by setup.sh"
    echo "# Source before running: source .env"
    echo "export ${API_KEY_ENV}=\"${API_KEY}\""
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  green "Wrote ${ENV_FILE}"
fi

# ─── Step 8: Seed Obsidian vault ────────────────────────────────────────────

bold "Step 8 — Obsidian folder structure"

export SETUP_VAULT_PATH="$VAULT_PATH"
export AGENT_DIR
"$PYTHON" << 'PYEOF'
import os
from pathlib import Path

vault = Path(os.environ["SETUP_VAULT_PATH"]).expanduser()
memory = vault / "agent-memory"

dirs = [
    vault / "inbox",
    vault / "daily",
    memory,
]
for d in dirs:
    d.mkdir(parents=True, exist_ok=True)

tier_defaults = {
    "daily.md": "# Daily Memory\n\n*Auto-managed by Work Assistant. Last updated: never*\n\nNo context yet.\n",
    "weekly.md": "# Weekly Memory\n\n*Auto-managed by Work Assistant. Last updated: never*\n\nNo context yet.\n",
    "sprintly.md": "# Sprintly Memory\n\n*Auto-managed by Work Assistant. Last updated: never*\n\nNo context yet.\n",
    "monthly.md": "# Monthly Memory\n\n*Auto-managed by Work Assistant. Last updated: never*\n\nNo context yet.\n",
    "quarterly.md": "# Quarterly Memory\n\n*Auto-managed by Work Assistant. Last updated: never*\n\nNo context yet.\n",
    "tasks.md": "# Tasks\n\n*Auto-managed by Work Assistant.*\n\nNo open tasks yet.\n",
    "heartbeat.md": (
        "# Standing Questions\n\n"
        "Unchecked items are searched every agent run.\n\n"
        "- [ ] Example: Has the team responded to my last request?\n"
        "- [ ] Example: Any movement on the top-priority ticket?\n"
    ),
    "decisions.md": (
        "# Decisions Log\n\n"
        "Persistent record of decisions, commitments, and direction changes.\n"
        "Auto-maintained by Work Assistant. Manual entries welcome.\n\n"
        "---\n\n"
    ),
    "experiments.md": (
        "# Experiment Log\n\n"
        "Structured record of A/B test results and learnings.\n"
        "Auto-maintained by Work Assistant. Manual entries welcome.\n\n"
        "---\n\n"
    ),
}

for name, content in tier_defaults.items():
    path = memory / name
    if not path.exists():
        path.write_text(content, encoding="utf-8")

readme = vault / "inbox" / "Welcome.md"
if not readme.exists():
    readme.write_text(
        "# Welcome to your Work Assistant vault\n\n"
        "This vault is managed by the Work Assistant agent.\n\n"
        "- **inbox/** — meeting briefs, ask logs, agent notes\n"
        "- **daily/** — your daily notes (watched by the agent)\n"
        "- **agent-memory/** — tasks, tiered memory, decisions, experiments\n\n"
        "Open this folder as an Obsidian vault to browse output.\n",
        encoding="utf-8",
    )

print(f"Vault ready at {vault}")
PYEOF

green "Obsidian vault seeded."

# ─── Step 9: people.yaml ────────────────────────────────────────────────────

if [[ ! -f "$PEOPLE_FILE" ]]; then
  cp "${AGENT_DIR}/people.example.yaml" "$PEOPLE_FILE"
  green "Created people.yaml from people.example.yaml"
else
  dim "Keeping existing people.yaml"
fi

mkdir -p "${AGENT_DIR}/.config/agent"

# ─── Step 10: Write config.yaml ─────────────────────────────────────────────

if [[ "${SKIP_CONFIG:-0}" != "1" ]]; then
  bold "Step 9 — Writing config.yaml"

  export SETUP_PROVIDER="$PROVIDER"
  export SETUP_MODEL="$MODEL"
  export SETUP_API_KEY_ENV="$API_KEY_ENV"
  export SETUP_ASSISTANT_NAME="$ASSISTANT_NAME"
  export SETUP_ROLE_DESC="$ROLE_DESC"
  export SETUP_TIMEZONE="$TIMEZONE"
  export SETUP_VAULT_PATH="$VAULT_PATH"
  export SETUP_ENABLE_SLACK="$ENABLE_SLACK"
  export SETUP_ENABLE_GMAIL="$ENABLE_GMAIL"
  export SETUP_ENABLE_JIRA="$ENABLE_JIRA"
  export SETUP_ENABLE_CONFLUENCE="$ENABLE_CONFLUENCE"
  export SETUP_ENABLE_MEETINGS="$ENABLE_MEETINGS"
  export SETUP_HAS_CUSTOM_CONTEXT="$(( ${#DOMAIN_LINES[@]} > 0 ))"

  "$PYTHON" << PYEOF
import os
from pathlib import Path

import yaml

agent_dir = Path(os.environ["AGENT_DIR"])
vault_path = str(Path(os.environ["SETUP_VAULT_PATH"]).expanduser())
provider = os.environ["SETUP_PROVIDER"]
model = os.environ["SETUP_MODEL"]
api_key_env = os.environ.get("SETUP_API_KEY_ENV", "")

def yn(name: str) -> bool:
    return os.environ.get(name, "0") == "1"

llm = {
    "provider": provider,
    "model": model,
    "temperature": 0.4,
    "max_tokens": 20000,
    "max_chars_per_source": 12000,
}
if api_key_env:
    llm["api_key_env"] = api_key_env
if provider == "ollama":
    llm["base_url"] = "http://localhost:11434"

config = {
    "assistant": {
        "name": os.environ["SETUP_ASSISTANT_NAME"],
        "id": "work-assistant",
        "role_description": os.environ["SETUP_ROLE_DESC"],
    },
    "llm": llm,
    "templates": {
        "directory": "templates",
        "system_prompt": "templates/system_prompt.md",
        "absence_prompt": "templates/absence_prompt.md",
        "ask_prompt": "templates/ask_prompt.md",
        "domain_rules": "templates/domain/jira_rules.md",
    },
    "sources": {
        "slack": {
            "enabled": yn("SETUP_ENABLE_SLACK"),
            "token": "xoxc-...",
            "cookie_d": "xoxd-...",
            "workspace": "your-workspace",
            "priority_channels": [],
            "include_dms": True,
            "include_mentions": True,
        },
        "gmail": {
            "enabled": yn("SETUP_ENABLE_GMAIL"),
            "credentials_path": ".config/agent/gmail_credentials.json",
            "token_path": ".config/agent/gmail_token.json",
            "query_filter": "is:unread -category:promotions -category:social",
            "max_results": 25,
        },
        "jira": {
            "enabled": yn("SETUP_ENABLE_JIRA"),
            "email": "you@company.com",
            "token": "YOUR_JIRA_API_TOKEN",
            "base_url": "https://your-org.atlassian.net",
            "jql_filter": "assignee = currentUser() OR watcher = currentUser()",
        },
        "confluence": {
            "enabled": yn("SETUP_ENABLE_CONFLUENCE"),
            "email": "you@company.com",
            "token": "YOUR_CONFLUENCE_API_TOKEN",
            "base_url": "https://your-org.atlassian.net",
            "cql_filter": "watcher = currentUser() OR creator = currentUser()",
        },
        "meetings": {
            "enabled": yn("SETUP_ENABLE_MEETINGS"),
            "credentials_path": ".config/agent/gmail_credentials.json",
            "token_path": ".config/agent/meetings_token.json",
            "max_results": 20,
        },
        "obsidian": {
            "enabled": True,
            "vault_path": vault_path,
            "watch_folders": ["inbox", "daily"],
            "recency_minutes": 160,
            "memory_folder": "agent-memory",
            "memory_extra_files": [],
        },
        "taskflow": {"enabled": False},
    },
    "agent": {
        "state_db": "~/.config/work-assistant/state.db",
        "log_file": "~/.config/work-assistant/agent.log",
        "timezone": os.environ.get("SETUP_TIMEZONE", "America/New_York"),
        "max_chars_per_source": 8000,
    },
}

config_path = agent_dir / "config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

print(f"Wrote {config_path}")
PYEOF

  green "config.yaml written."
fi

# ─── Step 11: Smoke test ────────────────────────────────────────────────────

bold "Step 10 — Quick validation"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

if "$VENV_DIR/bin/python" -c "
from config_loader import load_config
from prompts import load_prompt
cfg = load_config()
p = load_prompt('system_prompt', cfg)
assert 'Job 1' in p
print('Config and prompts OK')
" 2>/dev/null; then
  green "Config and prompt loading verified."
else
  yellow "Validation skipped or failed — check config.yaml manually."
fi

# ─── Done ───────────────────────────────────────────────────────────────────

echo
bold "Setup complete!"
echo
echo "Vault:     ${VAULT_PATH}"
echo "Config:    ${CONFIG_FILE}"
[[ -f "$ENV_FILE" ]] && echo "Secrets:   ${ENV_FILE}"
echo
bold "Next steps:"
echo "  1. Open Obsidian → Open folder as vault → ${VAULT_PATH}"
echo "  2. source .venv/bin/activate"
[[ -f "$ENV_FILE" ]] && echo "  3. source .env"
echo "  4. Edit agent-memory/heartbeat.md with your standing questions"
echo "  5. python main.py --dry-run"
echo
dim "To connect Slack, Gmail, JIRA, etc., edit config.yaml — see SETUP.md."
dim "Add teammates to people.yaml for relationship tracking."
echo
