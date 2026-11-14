# Upstream report: `_gateway_accepted` stays False on the base's inline-dispatch paths

Date: 2026-11-11. Origin: card A12 (`docs/specs/adapter/tasks.md`) and its findings doc
`docs/mesh-bridge-findings-2026-11-01-false-desync-after-busy-command.md`. Fix option 3 there;
it belongs to the gateway install, not this repo, so it is filed here as a report.

## The flag's contract, as platform adapters read it

`gateway/platforms/base.py` sets `event._gateway_accepted = False` on entry to
`handle_message` (`base.py:3927`) and flips it True only when a fresh inbound is claimed for
a session (`base.py:3966`). Platform
adapters that admit a peer themselves (the Reticulum adapter does, A3 4.5) use the post-call
value as a deny signal: False after the call means "the gateway's own authz/ingress gate
dropped the event".

## Where the contract breaks

The busy-session inline-dispatch path — `_handle_message_while_active` (`base.py:3990`) —
handles `/stop`, `/new`, `/reset` and clarify/confirm text answers while a turn is running.
Both its branches (the bypass-command branch dispatch at `base.py:4017`, the
awaiting-text/clarify dispatch at `base.py:4037`) call `_dispatch_inline_reply`, which awaits
the handler and — when it produced text — sends the reply through the platform's own send
path (`base.py:3498`); both branches then return WITHOUT setting the flag. The event *was*
handled, yet the flag still reads False — an adapter following the contract above then
reports a gateway deny that never happened.

Two sibling paths handle the event and send NOTHING: a handler that returns no text exits
`_dispatch_inline_reply` early (`base.py:3492`), and a handler exception is swallowed by the
dispatch branches' `except Exception` (`base.py:4018-4019`, `:4038-4039`). On those paths no
adapter-side evidence rule can separate handled from dropped — the flag itself has to be set
correctly, which is the strongest argument for fixing this in the gateway rather than in
adapters.

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
adapter reads False as a deny only when no send to the event's peer was attempted during the
`handle_message` await (the attempts marker has been peer-matched since review round 1,
2026-11-14). That keeps the false alarm fixed without touching the gateway install; the
handled-but-silent paths described above remain a documented residue (card A14).
If this report lands upstream, the discriminator stays harmless — a True flag short-circuits
the deny read either way — so no revert is needed here when the gateway fix arrives.
