# Setup Guide

## Automated setup (recommended)

```bash
cd agent
./setup.sh
```

The script will:

1. Ask for **LLM provider** (Anthropic / OpenAI / Ollama) and **API key**
2. Set **assistant name** and **role** (injected into prompt templates)
3. Optionally enable Slack, Gmail, JIRA, Confluence, Calendar sources
4. Collect optional **domain context** (saved to `templates/domain/custom_context.md`)
5. Create an **Obsidian vault** at `../WorkVault` by default (sibling to `agent/`)
6. Install Python dependencies into `.venv`
7. Write `config.yaml`, `.env`, and seed vault folders (`inbox/`, `daily/`, `agent-memory/`)

After setup:

```bash
source .venv/bin/activate
source .env          # if created
python main.py --dry-run
```

Open the vault path in Obsidian as a folder vault.

Use `./run.sh` (macOS/Linux) or `.\run.ps1` (Windows) to activate the venv, load secrets, and run the agent.

**Windows coworkers:** see [WINDOWS.md](WINDOWS.md) for `setup.ps1`, Task Scheduler, and pilot tips.

---

## Manual setup

Step-by-step first-run checklist if you prefer not to use `setup.sh`.

## 1. Install dependencies

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

First run downloads the local embedding model (`all-mpnet-base-v2`) — allow a few minutes.

## 2. Create config files

```bash
cp config.example.yaml config.yaml
cp people.example.yaml people.yaml   # skip if you already have people.yaml
```

Edit `config.yaml`:

- Set `llm.provider` and `llm.model`
- Set `sources.obsidian.vault_path` to your Obsidian vault
- Fill in credentials for each enabled source

## 3. Set up LLM credentials

**Anthropic (recommended for main synthesis):**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**OpenAI:**

```bash
export OPENAI_API_KEY=sk-...
```

**Ollama (local, no API key):**

```bash
ollama serve
ollama pull llama3.1:latest
```

Then in `config.yaml`:

```yaml
llm:
  provider: ollama
  model: llama3.1:latest
  base_url: http://localhost:11434
```

## 4. Google OAuth (Gmail + Calendar)

1. Create a Google Cloud project and enable Gmail API + Calendar API
2. Download OAuth credentials JSON to `.config/agent/gmail_credentials.json`
3. Run the agent once — it will open a browser for authorization and write token files

```bash
mkdir -p .config/agent
# place gmail_credentials.json here
python main.py --sources-only
```

## 5. Slack

Slack uses browser-scraped tokens (fragile, expires periodically):

1. Open Slack in browser → DevTools → Network
2. Send a message, find a request to `api.slack.com`
3. Copy `token` (xoxc-…) and `d` cookie into `config.yaml`

## 6. Atlassian (JIRA + Confluence)

Create API tokens at https://id.atlassian.com/manage-profile/security/api-tokens

Fill in `sources.jira` and `sources.confluence` in config.

## 7. Customize prompts (optional)

- [`templates/system_prompt.md`](templates/system_prompt.md) — core assistant instructions
- [`templates/domain/jira_rules.md`](templates/domain/jira_rules.md) — your org's issue tracker rules
- [`templates/domain/README.md`](templates/domain/README.md) — guide to writing domain rules

Disable JIRA to skip domain rules injection:

```yaml
sources:
  jira:
    enabled: false
```

## 8. Smoke test

```bash
# Fetch sources only (no LLM cost)
python main.py --sources-only

# Full synthesis, no writes
python main.py --dry-run

# Q&A
python ask.py "What are my open tasks?"
```

## 9. Schedule (macOS)

1. Edit paths in `com.work-assistant.agent.plist` and `com.work-assistant.meeting-brief.plist`
2. Install:

```bash
cp com.work-assistant.agent.plist ~/Library/LaunchAgents/
cp com.work-assistant.meeting-brief.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.work-assistant.agent.plist
launchctl load ~/Library/LaunchAgents/com.work-assistant.meeting-brief.plist
```

## 10. Raycast (optional)

Point a Raycast script command at `raycast_ask.sh`, or run:

```bash
./raycast_ask.sh "What's the status of X?"
```

## Rotate exposed secrets

If this repo was ever shared with secrets in `config.yaml` or OAuth files, regenerate:

- Slack browser token
- Atlassian API tokens
- Google OAuth client secret

The `.gitignore` now excludes these files from future commits.
