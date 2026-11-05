# In-repo skills

Procedures an agent loads only when the task calls for it. This keeps `AGENTS.md` short
while preserving the long how-to content somewhere every agent can reach it.

Each file is one procedure. Read the one that matches your task, not all of them.

| Skill | Load when |
|---|---|
| `pii-check-before-commit-and-pr.md` | about to commit, push, or open/update a PR |
| `repo-navigation.md` | you need to find where something lives or which doc owns a topic |
| `lost-work-audit.md` | asked whether anything was lost after a merge or a gap |
| `live-testing.md` | exercising a change against the running bridge or adapter |
| `updating-the-adapter.md` | asked how the adapter updates, or asked to pull/update it on a running gateway |
| `babysit-pr-for-review-bot.md` | watching a PR until the review bot has no unresolved comments, and its score is at target |

`docs/PII-RULES.md` is the rule; `pii-check-before-commit-and-pr.md` is the procedure that
applies it. Both are mandatory reading before any push.

`babysit-pr-for-review-bot.md` carries a hard rule: while babysitting, the agent never posts
any comment on the repo. A comment can dismiss the review bot's review state. The agent pushes
fixes; the human owns the conversation.
