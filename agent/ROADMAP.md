# Roadmap

Working list of what's shipped, what's scoped, and what's still just an idea.

## Shipped

- **Pluggable LLM layer** (`llm/`): Anthropic, OpenAI, and Ollama backends via `create_llm()`.
- **Portable template extraction**: prompts in `templates/`, domain rules in `templates/domain/`, config-driven via `config_loader.py`.
- **Source enable/disable**: `sources.<name>.enabled` in config; disabled sources skipped in `main.py` and `ask.py`.
- **Tiered memory** (`memory.py`): daily through quarterly tiers with staleness catch-up if a trigger window is missed.
- **Decisions & experiments logs**: append-only Obsidian logs with standing vs. scoped durability and decay sweep for scoped entries.
- **Local embeddings** (`embeddings.py`): offline semantic search over decisions/experiments; wired into `ask.py`, main synthesis, heartbeat, and meeting briefs.
- **Absence sweep**: second LLM pass for gaps — things that should be present but aren't.
- **Pre-meeting briefs** (`meeting_brief.py`): calendar poller + attendee trajectories + semantic context + open tasks.
- **Cross-platform setup**: `setup.sh` / `setup.ps1`, `run.sh` / `run.ps1`, Windows docs, desktop notifications.
- **Repo hygiene**: `.gitignore` for secrets, `config.example.yaml`, `people.example.yaml`.

## Scoped, not built

### Issue tracker hygiene sweep
Full periodic audit against domain rules in `templates/domain/` — not just today's deltas.

Likely needs: pagination in `jira_source.py`, issue link parsing, custom-field discovery, a batched LLM pass over the card graph, and a monthly trigger.

## Known limitations

- **Notification click-through**: tapping a notification doesn't jump anywhere on macOS without extra tooling (e.g. `terminal-notifier`).
- **Meeting-brief task matching** uses a first-name substring heuristic — simple but imprecise.
- **Heartbeat semantic search** uses keyword extraction rather than the raw question for embedding lookup.
- **Tasks vanish two ways**: resolved (checked off) vs. stale (not carried forward) — `tasks.md` doesn't distinguish which.

## Backlog

- **`heartbeat.md`** ships with placeholder examples — replace with your standing questions.

## Ideas (not yet scoped)

- **Contradiction detection**: flag new signals that conflict with prior decisions, not just duplicates.
- **Orphaned-implications sweep**: cross-reference decision implications against open tasks/tickets.
- **Theme-mining**: cluster decision embeddings to surface recurring patterns.
- **Usage rollup**: monthly summary of LLM pass costs from `state.db`.
- **Mobile access to `ask.py`**: Raycast is desk-bound; Tailscale or a Shortcut could extend reach.
