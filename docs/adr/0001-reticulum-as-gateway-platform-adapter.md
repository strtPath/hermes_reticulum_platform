# 0001. Reticulum ships as a gateway platform adapter, not a CLI-driving bridge

Status: Accepted
Source: `docs/specs/adapter/spec.md` §1, `docs/spec-feasibility-reticulum-platform.md`

## Context

The bridge (`src/hermes_reticulum/`, the long-lived process) talks to the mesh and drives a
Hermes child by spawning `hermes chat -q <message> -Q` in one-shot mode. It then reimplements,
outside the gateway: session handling, tool-call streaming, clarify, and approvals. Those are
the four subsystems that have produced the project's hardest defects, because the one-shot
path assumes no user is present while the bridge has a live human on the other end of the mesh.

Every one of those subsystems is something the Hermes gateway already implements properly for
its other platforms.

## Decision

Ship Reticulum as a **Hermes gateway platform adapter** (a plugin that registers through
`ctx.register_platform()`), and delete the bridge machinery that the gateway replaces.

The adapter form was chosen because it *deletes* that machinery rather than fixing it. It is a
plugin, not a core contribution: `ADDING_A_PLATFORM.md` states the plugin path requires zero
changes to core Hermes code, and out-of-tree registration is the documented recommendation for
community platforms.

## Consequences

- Session adoption heuristics, the `state.db` step watcher, the clarify gate, and the approval
  control server are deleted as components (see spec §7 for the per-component disposition).
- The gateway owns sessions, tool events, clarify, and approvals. The adapter supplies only the
  platform surface (connect, disconnect, send, get_chat_info, send_typing) and its capability
  flags.
- The mesh is then one platform among the gateway's others, and inherits the gateway's behaviour
  rather than re-implementing it.
- The 900s approval window and the ACL semantics must be preserved through the gateway's own
  paths; they are not dropped with the components (ADR-0003).
