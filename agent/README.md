# Work Assistant

A personal chief-of-staff agent that polls your work sources, synthesizes what needs attention, and writes structured output back to Obsidian.

## Documentation

| Doc | What's in it |
|-----|----------------|
| **This file** | Overview, quick start, config reference |
| [OVERVIEW.md](OVERVIEW.md) | Product description + technical deep dive — memory cycle, heartbeat, Raycast, meeting briefs, design tradeoffs |
| [SETUP.md](SETUP.md) | Full install checklist — automated + manual, every source connector, scheduling, Raycast |
| [WINDOWS.md](WINDOWS.md) | Windows install (`setup.ps1`), Task Scheduler, pilot tips for coworkers |
| [config.example.yaml](config.example.yaml) | Annotated config template |
| [templates/domain/README.md](templates/domain/README.md) | How to write domain-specific prompt rules |

## What it does

On each run the assistant:

1. **Fetches** from enabled sources (Slack, Gmail, JIRA, Confluence, Google Meet, Obsidian)
2. **Pre-processes** long content with parallel LLM passes (note/meeting/Slack summarization)
3. **Investigates** standing questions via heartbeat search
4. **Synthesizes** a deduplicated task list, memory tier updates, decision/experiment log entries, and people trajectories
5. **Runs an absence sweep** — second pass for what should be present but isn't
6. **Writes** to your Obsidian vault + SQLite state + desktop notifications (macOS / Windows)

Auxiliary tools:

- **`ask.py`** — on-demand Q&A against your knowledge base ([Raycast](SETUP.md#10-raycast-optional)-friendly on macOS)
- **`meeting_brief.py`** — auto-generates pre-meeting briefs from calendar + people + embeddings

## Architecture

```
Sources (Slack, Gmail, JIRA, …)
        ↓
   main.py orchestrator
        ↓
  gpt_processor.py (multi-pass synthesis)
        ↓
  llm/ (Anthropic | OpenAI | Ollama)
        ↓
  Obsidian vault + state.db
```

Prompts and domain rules live in [`templates/`](templates/) — swap these to adapt the assistant to your organization.

## Quick start

### macOS / Linux

```bash
cd agent
./setup.sh

source .venv/bin/activate
source .env              # if setup wrote API keys here
python main.py --dry-run
```

Or use the helper: `./run.sh --dry-run`

### Windows

```powershell
cd agent
powershell -ExecutionPolicy Bypass -File setup.ps1

.\.venv\Scripts\Activate.ps1
. .\.env.ps1             # if setup wrote API keys here
python main.py --dry-run
```

Or use the helper: `.\run.ps1 --dry-run`

Both setup scripts walk you through LLM provider/API key, assistant identity, timezone, optional source toggles, Obsidian vault creation (sibling folder to `agent/`), and write `config.yaml`.

### Manual setup

If you prefer not to use the setup scripts:

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp people.example.yaml people.yaml

export ANTHROPIC_API_KEY=sk-...   # or OPENAI_API_KEY, or use Ollama locally
python main.py --dry-run
```

**Full step-by-step** (Google OAuth, Slack tokens, JIRA, scheduling, etc.): [SETUP.md](SETUP.md)

## Smoke test

```bash
python main.py --sources-only    # fetch connectors, no LLM cost
python main.py --dry-run         # full synthesis, no writes
python ask.py "What are my open tasks?"
python meeting_brief.py --dry-run
```

## Configuration

| File | Purpose |
|------|---------|
| `config.yaml` | Credentials, source toggles, LLM backend, timezone (gitignored — copy from `config.example.yaml`) |
| `people.yaml` | Org directory + relationship trajectories (gitignored — copy from `people.example.yaml`) |
| `.env` / `.env.ps1` | API keys written by setup scripts (gitignored) |
| `templates/` | System prompts and domain-specific rules |

Minimal config shape:

```yaml
assistant:
  name: "Work Assistant"
  role_description: "Product Manager"

llm:
  provider: anthropic          # anthropic | openai | ollama
  model: claude-sonnet-4-20250514
  api_key_env: ANTHROPIC_API_KEY

agent:
  timezone: "America/New_York"   # IANA name — memory tiers, timestamps, prompts

sources:
  obsidian:
    enabled: true
    vault_path: "../WorkVault"
  jira:
    enabled: false               # disable sources you don't use yet
```

### LLM backends

| Provider | Requires |
|----------|----------|
| `anthropic` | `ANTHROPIC_API_KEY` env var |
| `openai` | `OPENAI_API_KEY` env var |
| `ollama` | Local Ollama server (`ollama serve`) |

Local models work well for `ask.py` and pre-processing passes. Use a cloud model for the main synthesis pass (large JSON output).

### Disable sources

```yaml
sources:
  jira:
    enabled: false
```

## Scheduling

**macOS:** example launchd plists included — see [SETUP.md §9](SETUP.md#9-schedule-macos) for install commands.

- `com.work-assistant.agent.plist` — 4×/weekday main runs
- `com.work-assistant.meeting-brief.plist` — 5-minute meeting-brief poll

Update Python and script paths in each plist before loading.

**Windows:** Task Scheduler — see [WINDOWS.md](WINDOWS.md#scheduling-task-scheduler).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `WORK_ASSISTANT_CONFIG` | Override path to config.yaml |
| `OBSIDIAN_VAULT_PATH` | Override Obsidian vault path |

## Security note

If `config.yaml` or OAuth tokens were ever committed to git, rotate those credentials. The repo `.gitignore` excludes `config.yaml`, `.config/`, `people.yaml`, and `.env` going forward.
