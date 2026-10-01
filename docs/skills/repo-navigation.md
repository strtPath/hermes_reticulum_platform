# Skill: repo navigation

How to find the document that owns a topic, so you load the one you need instead of the
whole tree. `AGENTS.md` routes at the top level; this file goes one layer deeper for the
adapter work and the recurring questions.

## The document set

**Specs** (`docs/spec-*.md`) describe behaviour and record decisions. The main spec is
`spec-rns-hermes-endpoint.md`; everything else is a sub-spec. Convention: a sub-spec
describes behaviour, the main spec records decisions. Change a behaviour and you update the
sub-spec in the same commit. Settle a decision and it moves out of the main spec's open list
into its decisions section.

**Findings** (`docs/mesh-bridge-findings-<date>-*.md`) are per-incident, dated. This is the
history. When a problem feels familiar, search these before re-deriving a cause.

**Notes** (`docs/_notes-*.md`) are the raw research behind the specs, kept because they
carry file:line citations the polished docs avoid (line numbers go stale). Read when a spec
assertion needs its source.

**The adapter feature bundle** (`docs/specs/adapter/`) carries the design and build units:
`spec.md` (the design, §1-17), `plan.md` (working decisions, old spec §18-21 with numbering
kept), `tasks.md` (build cards A1-A10, each sized to one PR, with blocking edges and
deliverables), and `verification.md` (acceptance criteria to evidence). Pick your card in
`tasks.md`; record its proof in `verification.md`.

## Where code lives

| Area | Path |
|---|---|
| bridge entry, wiring | `src/hermes_reticulum/cli.py` |
| LXMF and sending | `src/hermes_reticulum/core/bridge.py` |
| turn execution, step watcher, clarify | `src/hermes_reticulum/core/hermes_client.py` |
| approval control | `src/hermes_reticulum/core/control_server.py` |
| outbound queue | `src/hermes_reticulum/core/downlink.py` |
| access control | `src/hermes_reticulum/core/acl.py` |
| adapter declaration | `src/hermes_reticulum/plugin/registration.py` |
| adapter itself | `src/hermes_reticulum/plugin/adapter.py` |
| pre-execution tool gate | `src/hermes_reticulum/mesh-tool-gate/` |
| PII gate | `scripts/security-sweep.py` |

## The recurring questions and where their answers live

- **"Why doesn't feature X work end to end?"** The bridge drives Hermes' one-shot path while
  expecting interactive behaviour. Read the CLI interaction contract first, then the main
  spec section 5.
- **"The agent replied but nothing came over the mesh."** Outbound delivery layer. There is a
  procedure covering the send path, the capabilities flag, and the config half.
- **"Messages are not reaching the agent."** Inbound. Check the allowlist boundary and the
  identity files first; a component that finds no identity file generates a new one silently,
  so two components can end up with two addresses.
- **"The platform is enabled but never serves."** The enablement gate treats `is_connected`
  as "is configured", and the display name is mandatory or the platform never registers.
  Both are in the gateway platform env config procedure.

## Branch and commit model

- `main` is release, `dev` is integration, `adapter` is the platform-adapter integration
  branch.
- One build card is one branch is one PR against `adapter`.
- Feature branches here run far ahead of `dev`. Before branching, check the base with
  `git log --oneline dev..HEAD` (what would come along) and `git log --oneline HEAD..dev`
  (what you would miss). When the current branch is ahead of `dev`, ask which base rather
  than assuming.
- Never add AI tool attribution to a commit. Set identity in git config, never in a trailer.
