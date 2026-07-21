# Work Assistant

A personal chief-of-staff agent: polls Slack, Gmail, JIRA, Confluence, Calendar, and Obsidian; deduplicates overlapping signals into a prioritized task list; and runs an **absence sweep** — a second pass that looks for what *should* be in your data but isn't (stale tickets, unanswered threads, missing follow-ups).

Reference implementation, extracted from a private monorepo where development continues.

## What makes it a system, not a script

Sources plug into a shared ingest loop. The agent doesn't just read third-party tools — it can pull from your own apps too. [TaskFlow](https://github.com/ancepsinfans/taskflow) is one example: [`agent/sources/taskflow_sync.py`](agent/sources/taskflow_sync.py) pulls inbox and today tasks from TaskFlow's REST API into the same synthesis pipeline as Slack and JIRA, then merges into the Obsidian task list.

LLM backends are swappable (Anthropic, OpenAI, Ollama). Prompts and domain rules live in markdown templates. Output goes to an Obsidian vault you own; operational state lives in local SQLite.

## Get started

All code and documentation live in [`agent/`](agent/):

- [Quick start](agent/README.md)
- [How it works](agent/OVERVIEW.md) — memory tiers, heartbeat, Raycast, meeting briefs, design tradeoffs
- [Setup guide](agent/SETUP.md)

## License

MIT — see [LICENSE](LICENSE).
