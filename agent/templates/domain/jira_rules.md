# Example domain rules

Replace this file with rules specific to your organization: issue tracker hierarchy, naming conventions, cross-system workflow patterns, and definitions of done.

## Issue tracker hierarchy (example)

If your tracker uses a hierarchy like Epic → Story → Task, treat card type as semantically load-bearing:

- **Epic** — outcome or initiative spanning multiple sprints. Tracks strategic progress, not day-to-day delivery.
- **Story** — user-visible unit of work with a clear definition of done.
- **Task** — engineering or ops sub-step. Usually linked to a parent story.

A status change on an epic means initiative-level movement. A status change on a story means delivery progress. Do not conflate the two when synthesizing tasks.

## Naming conventions (example)

- Engineering tickets: `ENG-1234`
- Content or ops tickets: `OPS-1234`
- Experiments: prefix with `exp-` in experiment log keys

Adjust to match your org.

## Cross-system handoff patterns

Watch for these patterns and draft messages when detected:

- **Story moves to QA / Ready to Test**: draft a message to QA or stakeholders with the ticket key, what changed, and what to validate.
- **Story marked Done**: flag if downstream notifications (docs, customers, dependent teams) are likely needed.
- **Question answered in chat but not reflected in the tracker**: surface the connection and suggest a ticket update.
- **Decision in a meeting without an owner or ticket**: extract with owner and flag the gap.
- **Wiki page updated but linked tickets don't reflect the change**: flag doc drift.

## Sprint timing (optional)

If you maintain a sprint or release calendar in Obsidian, add it to `sources.obsidian.memory_extra_files` in config. The agent can prefer that file over rolling memory for "what sprint are we in?" questions.

See `README.md` in this directory for how domain rules are injected into prompts.
