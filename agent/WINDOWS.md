# Windows Setup

Work Assistant runs on Windows. The core agent is cross-platform Python; a few shell scripts and schedulers are macOS-oriented, but Windows equivalents exist.

## Quick start

```powershell
cd agent
powershell -ExecutionPolicy Bypass -File setup.ps1
.\.venv\Scripts\Activate.ps1
. .\.env.ps1          # if setup created API key file
python main.py --dry-run
```

## What works out of the box on Windows

| Feature | Status |
|---------|--------|
| Main agent loop (`main.py`) | Yes |
| LLM backends (Anthropic, OpenAI, Ollama) | Yes |
| Obsidian vault read/write | Yes |
| Google OAuth (Gmail, Calendar) | Yes — browser opens for consent |
| Slack / JIRA / Confluence APIs | Yes |
| Local embeddings | Yes — first run downloads ~400MB model; allow 10–15 min |
| Desktop notifications | Yes — Windows 10+ toast via PowerShell |
| `ask.py` Q&A | Yes |

## Differences from macOS

| macOS | Windows |
|-------|---------|
| `./setup.sh` | `setup.ps1` |
| `./run.sh` | `run.ps1` (below) or manual venv activate |
| launchd plists | Task Scheduler (see below) |
| Raycast | Pin `ask.py` to Start menu, or use PowerToys Run |

### run.ps1 helper

```powershell
# run.ps1 — activate venv, load secrets, run agent
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir
if (Test-Path .\.venv\Scripts\Activate.ps1) { . .\.venv\Scripts\Activate.ps1 }
if (Test-Path .\.env.ps1) { . .\.env.ps1 }
python main.py @args
```

## Scheduling (Task Scheduler)

For coworkers testing daily runs:

1. Open **Task Scheduler** → Create Task
2. Trigger: weekdays at 9:10 AM (repeat for other run times, or start with one)
3. Action: Start a program
   - Program: `C:\path\to\agent\.venv\Scripts\python.exe`
   - Arguments: `main.py`
   - Start in: `C:\path\to\agent`
4. Optionally add a batch wrapper that dot-sources `.env.ps1` first

Meeting briefs (`meeting_brief.py`) can run every 5 minutes on a second task.

## Timezone

Set your local IANA timezone in `config.yaml`:

```yaml
agent:
  timezone: "America/Chicago"   # or Europe/Warsaw, etc.
```

All memory tier triggers, task timestamps, and prompt "current time" lines use this.

## Common Windows gotchas

**Python not found** — Install Python 3.11+ from [python.org](https://www.python.org/downloads/) and check "Add to PATH".

**Embeddings install is slow** — `pip install torch sentence-transformers` is large on Windows. Run setup once on Wi-Fi; subsequent runs use the cached model.

**Long paths** — Keep the vault path short (e.g. `C:\Users\you\WorkVault`). Very deep OneDrive paths sometimes cause issues with Obsidian + git.

**Corporate proxy** — Anthropic/OpenAI API calls need HTTPS out. Slack/JIRA/Atlassian same.

**Obsidian** — Coworkers should install Obsidian desktop and open the vault folder created by setup. The agent writes markdown they can browse normally.

## Recommended pilot config for Windows coworkers

Start minimal:

- LLM: Anthropic or OpenAI (cloud — local Ollama optional for power users)
- Sources: Obsidian only + maybe Gmail
- Skip Slack browser-token setup until they're committed
- `timezone` set to their locale

```powershell
python main.py --sources-only   # test connectors, no LLM cost
python main.py --dry-run        # full synthesis, no writes
python ask.py "what are my open tasks?"
```

## Graceful degradation

Nothing critical is macOS-only anymore:

- Notifications skip silently if PowerShell toast fails
- Git vault auto-commit skips if git isn't installed
- Disabled sources are ignored — partial configs are fine

The agent is designed to fail soft: one broken connector logs an error and the rest of the run continues.
