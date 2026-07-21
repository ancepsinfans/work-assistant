# Domain Rules

Replace `jira_rules.md` with rules specific to your organization: issue tracker hierarchy, naming conventions, cross-system workflow patterns, and definitions of done.

The bundled `jira_rules.md` is a **generic placeholder** — edit or replace it entirely for your context.

## Issue tracker hierarchy

Treat card type as semantically load-bearing when your tracker uses a hierarchy like:

- **Epic** — strategic initiative or outcome spanning sprints
- **Story** — deliverable unit with a clear definition of done
- **Task** — sub-step linked to a story

A status change on an epic is not the same as a status change on a story. Synthesis should respect that distinction.

## Cross-system workflow patterns

Watch for these handoff patterns and draft messages when detected:

- **Story moves to QA / Ready to Test**: draft a message with the ticket key, what changed, and what to validate.
- **Story marked Done**: flag if downstream notifications are likely needed.
- **Question in Slack answered in a ticket, email, wiki, or meeting**: surface the connection.
- **Decision in Slack or meeting that should be captured in tracker or wiki**: flag it.
- **Wiki page updated but linked tickets don't reflect the change**: flag the drift.
- **Meeting action items without a ticket or follow-up**: extract with owner and flag gaps.

See `jira_rules.md` for a starter template you can customize.
