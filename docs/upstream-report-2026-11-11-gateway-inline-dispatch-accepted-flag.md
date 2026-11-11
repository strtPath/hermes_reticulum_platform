# Upstream report: `_gateway_accepted` stays False on the base's inline-dispatch paths

Date: 2026-11-11. Origin: card A12 (`docs/specs/adapter/tasks.md`) and its findings doc
`docs/mesh-bridge-findings-2026-11-01-false-desync-after-busy-command.md`. Fix option 3 there;
it belongs to the gateway install, not this repo, so it is filed here as a report.

## The flag's contract, as platform adapters read it

`gateway/platforms/base.py` sets `event._gateway_accepted = False` on entry to
`handle_message` and flips it True when a fresh inbound is claimed for a session. Platform
adapters that admit a peer themselves (the Reticulum adapter does, A3 4.5) use the post-call
value as a deny signal: False after the call means "the gateway's own authz/ingress gate
dropped the event".

## Where the contract breaks

The busy-session inline-dispatch path — `_handle_message_while_active` — handles `/stop`,
`/new`, `/reset` and clarify/confirm text answers while a turn is running. Both its branches
(the bypass-command branch and the awaiting-text/clarify branch) run the handling, send the
reply through the platform's own `send()`, and return WITHOUT setting the flag. The event *was*
claimed and handled, yet the flag still reads False — an adapter following the contract above
then reports a gateway deny that never happened.

Live evidence (2026-11-01, this repo's gateway logs): `gateway desync — adapter admitted
<hash> but the gateway authz layer denied the delivered event`, immediately after a `/stop`
that had worked; three earlier instances (2026-10-26 x2, 2026-10-30) match the same path.

## Suggested fix

Set `event._gateway_accepted = True` once the inline handling completes in
`_handle_message_while_active` — both branches — so the flag means "handled by the gateway"
on every path, matching what A3 4.5 documents. This is semantically correct and makes the
signal honest for every platform adapter, not just this one.

## Status here

This repo shipped the adapter-side discriminator instead (fix option 1, card A12): the
adapter reads False as a deny only when no send was attempted for the event during the
`handle_message` await. That keeps the false alarm fixed without touching the gateway install.
If this report lands upstream, the discriminator stays harmless — a True flag short-circuits
the deny read either way — so no revert is needed here when the gateway fix arrives.
