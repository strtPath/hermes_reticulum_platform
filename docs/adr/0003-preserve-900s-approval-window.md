# 0003. Route approvals through the gateway, preserving the 900s window

Status: Accepted
Source: `docs/specs/adapter/spec.md` §7, `docs/mesh-bridge-findings-2026-08-30-*` (approval timeout)

## Context

The bridge ran its own approval control server with a long timeout (raised to 900s), because
radio hops are slow, connections drop, and the user is away from the mesh before a
deny-by-default verdict lands. The timeout exists for LoRa hop latency, dropped connections,
and AFK users — not as a preference.

Under the adapter design, the approval control server is deleted as a component. The gateway's
own approval path is strictly richer: a shared prompt template (`_format_exec_approval`), a
plain-text fallback for adapters without buttons (correct for LXMF), and natural-language
acceptance ("yes", "ok", "confirm", "y", thumbs-up) routed to the canonical `/approve`
handlers, not only the slash forms.

## Decision

Delete the approval control server as a component but **do not lose its behaviour**. Move the
gate over to the gateway's approval path, and ship an `approvals.timeout` of 900s so the mesh
keeps the window the radio link requires.

## Status of the decision

Decided and implemented. The plugin ships `approvals.timeout: 900` (in
`plugins/reticulum/config.yaml`, documented with the LoRa rationale, and applied at install time
by `install.sh`, which sets it in the user's `config.yaml` unless they already chose a value).
Spec section 17.6's gap is closed.

## Consequences

- The adapter has no approval component of its own; it inherits the gateway's, which already
  handles the plain-text/no-button case LXMF needs.
- The 900s window must be configured on the gateway side (`approvals.timeout`), and the
  two timeout layers (gateway and any plugin-side gate) must stay at least equal or the outer
  one denies first.
- Tracked as card A8.
