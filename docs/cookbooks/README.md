# Flyn Orchestrator — Cookbooks

How-to guides for extending the orchestrator. Each cookbook is a step-by-step recipe with code snippets, the existing reference implementations, and an anti-pattern section.

## Available cookbooks

| Cookbook | When to use it |
|---|---|
| [Add a workflow](add-a-workflow.md) | Adding a new task domain with its own role lineup (e.g., `legal`, `support`, `analytics`). Not for prompt variants. |
| [Add a PMAdapter](add-a-pm-adapter.md) | Mirroring task lifecycle to a new PM system (Jira, Asana, Notion, etc). Not for generic webhooks — use `WebhookPMAdapter` for those. |
| [Add a ChannelAdapter](add-a-channel-adapter.md) | Adding inbound/outbound messaging via a new system (Google Chat, Slack, SMS, Discord). |

## Conventions

All cookbooks share these conventions:

- **Reference implementations** are called out by file name. Read them first; the cookbook explains the rationale.
- **The contract** section quotes the Protocol verbatim from `adapters/base.py` (or the equivalent canonical source).
- **Anti-patterns** section is concrete. If a section says "don't do X", X is something someone has actually done that caused a real bug.
- **Ship checklist** is a literal list you copy into your PR description.

## When a cookbook doesn't fit

Sometimes you need to extend the orchestrator in a way that doesn't fit any cookbook:
- Modifying the state machine itself (adding a TaskState)
- Adding a new backend (alongside `claude-p` and `codex-exec`)
- Changing how the `PhaseServices` bundle is constructed
- Adding a new approval gate type beyond the existing tier-keyed model

For those, read the design spec: `docs/superpowers/specs/2026-05-15-flyn-orchestrator-design.md` and the relevant `audit/_baseline.md §Δ` entries. If you can't find a precedent, this is design work — start with a brainstorm + spec before implementation.

## Adding a new cookbook

If you've extended the orchestrator in a way that establishes a reusable pattern, add a cookbook here. Keep them under ~400 lines; cover step-by-step build + tests + ship checklist + anti-patterns + cross-references.
