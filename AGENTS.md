# AGENTS.md — start here

This file is the entry point for any agent working in this repo. It routes you to the
right document. It does not teach the system; the specs do that.

Read this file first, then load ONLY the document that matches your task. Do not read the
whole `docs/` tree; it is large on purpose and most of it will be irrelevant to you.

## What this repo is

A bridge between a Reticulum mesh network and a Hermes AI agent. A message arrives over
LXMF from an authorised peer, drives a Hermes agent turn, and the answer goes back over the
mesh. The link may be a radio link: slow, lossy, and operated by someone not at a terminal.
Every design decision follows from that.

There are two architectures in the tree at once, and knowing which one you are touching is
the first thing to settle:

- **The bridge** (`src/hermes_reticulum/`, the long-lived process) drives the Hermes CLI in
  one-shot mode. This is the deployed, working system.
- **The platform adapter** (`src/hermes_reticulum/plugin/`) ships Reticulum as a Hermes
  gateway platform instead of driving the CLI. This is the direction of travel.

`docs/spec-feasibility-reticulum-platform.md` settled *whether* to build the adapter;
`docs/specs/adapter/spec.md` is the design. Read the adapter design before
writing any adapter code.

## The one thing that explains most surprises

The bridge drives Hermes' *programmatic* (one-shot) path while expecting *interactive*
behaviour from it. The two halves disagree about whether a user exists, and a feature can be
implemented correctly at every layer and still fail because the layers hold different
assumptions no document states. Full explanation: `docs/spec-rns-hermes-endpoint.md`, section 5.

## Where to look for what

| If you are... | Read this |
|---|---|
| new to the repo, need the system shape | `docs/spec-rns-hermes-endpoint.md` (main spec: parts, turn shape, decisions, invariants) |
| chasing an end-to-end behaviour that "should work" | `docs/spec-hermes-cli-interaction-contract.md` first |
| working on one message's path | `docs/spec-turn-lifecycle.md` |
| touching the approve/deny gate or step push | `docs/spec-approval-and-steppush.md` |
| touching Reticulum/LXMF transport or the send queue | `docs/spec-transport-downlink.md` |
| building the platform adapter | `docs/specs/adapter/spec.md` (design), `plan.md` (decisions), `tasks.md` (cards) |
| picking up a build task | `docs/specs/adapter/tasks.md` (cards A1-A10, each one PR) |
| about to commit or push | `docs/PII-RULES.md` (mandatory) |
| doing a recurring procedure (PII sweep, lost-work audit, live testing) | `docs/skills/` |
| babysitting a PR for the review bot | `docs/skills/babysit-pr-for-review-bot.md` |

`docs/_notes-*.md` are the raw research behind the specs, kept for their file:line
citations. Read them when a spec assertion needs its source, not first.

`docs/mesh-bridge-findings-*.md` are per-incident findings, dated. They are the history;
read them when debugging a problem that has happened before.

`docs/feature-parity-roadmap.md` is bridge-era history only. The current adapter gap list is
in the adapter spec's parity section.

## Rules that are not optional

**PII.** This repo is treated as public for all artifact rules, whatever its current GitHub
visibility. It is private at present and may be made public again; the rules do not relax
while it is private. No identity hashes in any form (full or 8+ hex
prefix), no hostnames, no host:port endpoints, no absolute home paths, no session ids, no
chat ids — in docs, code, commits, branches, or PR bodies. Full rule and the enforcement
script: `docs/PII-RULES.md`. Run the sweep before every commit and push.

**No AI attribution in commits.** No `Co-Authored-By` naming an AI tool or vendor, no
"Generated with" footer. Authorship is the only identity a commit needs. Set identity in
git config, never in a trailer.

**Spec content is not authored from inference.** If you are an agent: design proposals go
to the human for a ruling BEFORE they land in a spec. A spec records decided rulings,
observed behaviour with a source citation, or an open question explicitly labelled as open.
It does not record what you guessed. When a ruling is decided, spec + code + the relevant
skill update together; a pure spec-planning session touches spec and skill only.

**No code before an approved card.** Work on the adapter moves one card at a time from
`docs/specs/adapter/tasks.md`. Do not start a card that is not approved, and do not begin
editing until the card's scope is agreed. Each card is one branch and one PR.

**No scope expansion past the card.** When implementing, make the change the card names and
nothing else. A defect you notice outside the card becomes a new card or a findings doc, not
an unrequested edit. Completion is claimed only from the card's acceptance criteria, with
the evidence recorded in `docs/specs/adapter/verification.md`; a card is not done because a
test passes, it is done when every acceptance row has evidence a second party can re-run.

**Never comment on the repo while babysitting a PR.** When watching a PR for the review bot,
the agent's only GitHub write is a push of a fix commit to the head branch. It must not post
a PR comment, a review, a review comment, or a reply on any thread; a comment can dismiss the
review bot's review state and a dismissed review reads as resolved when nothing was. The human
owns the conversation with the bot; the agent owns the code. Full procedure and the escalation
path for a finding that conflicts with a settled decision: `docs/skills/babysit-pr-for-review-bot.md`.

**Verify before asserting.** Read the source at file:line before stating how something
behaves. A plausible mechanism stated as fact before the confirming read is the recurring
failure mode in this project's history.

## Layout

- `src/hermes_reticulum/` — the bridge. `cli.py` (entry, wiring), `core/bridge.py` (LXMF and
  sending), `core/hermes_client.py` (turn execution, step watcher, clarify),
  `core/control_server.py` (approval control), `core/downlink.py` (outbound queue),
  `core/acl.py` (access control).
- `src/hermes_reticulum/plugin/` — the adapter. `registration.py` (the
  `ctx.register_platform` declaration), `adapter.py` (the adapter).
- `src/hermes_reticulum/mesh-tool-gate/` — the pre-execution tool approve/deny gate plugin.
- `docs/` — specs, findings, notes, procedures.
- `scripts/` — tooling, including `security-sweep.py` (the PII gate).
- `tests/` — see `docs/skills/` for how testing works here.

## Branches

`main` is release, `dev` is integration, `adapter` is the integration branch for the
platform-adapter work. Feature work for the adapter branches from `adapter`; one build card
is one branch is one PR. Branch from the right base and confirm it first — feature branches
here run far ahead of `dev`, so branching off whatever HEAD happens to be drags unrelated
work into your diff.
