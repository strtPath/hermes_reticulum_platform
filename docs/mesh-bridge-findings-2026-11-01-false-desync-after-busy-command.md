# Mesh-bridge findings — 2026-11-01 — false "Access not authorized" after a busy-session command

**Status:** diagnosed from live gateway logs and source; no code change yet. The fix needs a
card ruling because it changes the D7 deny signal's meaning on one path (see "Fix options").
Card A12 in `docs/specs/adapter/tasks.md` is the proposal.

## TL;DR

An allowlisted peer sent `/stop` while a turn was running. The gateway dispatched the command
inline, sent its own ack (`⚡ Stopped. You can continue this session.`), cancelled the turn —
and returned from `handle_message` with `event._gateway_accepted` still `False`, because the
inline-dispatch paths never set it. The adapter reads that flag as "the gateway authz layer
denied this event" (A3 4.5 D7), logs a `gateway desync` warning, and sends the canned
`⛔ Access not authorized.` to the peer.

So the peer sees its own command ack followed by an authorization rejection, while its hash is
in the allowlist and the gateway never denied anything. The ACL is not involved. The rejection
is an artifact of an unset boolean on the base's busy-session bypass path.

## What the peer saw

Phone transcript, one second apart:

1. `⚡ Stopped. You can continue this session.` — the real `/stop` ack.
2. `⛔ Access not authorized.` — the false rejection.

Host log for the same second (identifiers redacted):

```
<timestamp> INFO  gateway.run: Invalidated run generation for session <session-id> -> 11 (stop_command)
<timestamp> INFO  gateway.run: STOP for session <session-id> — agent interrupted, session lock released
<timestamp> INFO  gateway.platforms.base: [Reticulum] Sending command '/stop' response (41 chars) to <peer-A>
<timestamp> INFO  agent.conversation_loop: Turn ended: reason=interrupted_during_api_call ...
<timestamp> INFO  hermes_reticulum.transport: Message handed to the mesh for <peer-A>.. (hops=2, method=direct)
<timestamp> WARNING hermes_reticulum.adapter: Reticulum: gateway desync — adapter admitted <peer-A> but the gateway authz layer denied the delivered event
<timestamp> INFO  hermes_reticulum.adapter: Reticulum: access denied from <peer-A> (not permitted by the ACL)
<timestamp> INFO  hermes_reticulum.adapter: Reticulum: outbound message reached DELIVERED (delivered)
<timestamp> WARNING hermes_reticulum.adapter: Reticulum: outcome for unknown payload; attributing to the last send (<peer-A>); receipt may be misattributed
```

The word "admitted" in the desync line is the proof the ACL let the peer through: the adapter's
own gate ran first and passed, then the D7 check fired on the delivered event.

## The mechanism (source citations)

Gateway (`gateway/platforms/base.py`, the installed hermes-agent build):

- `handle_message` sets `event._gateway_accepted = False` on entry (line 3992). That is the
  default the adapter later reads.
- If the session key is already active, `handle_message` hands the event to
  `_handle_message_while_active` and **returns** (lines 4027-4029). It never reaches the
  `event._gateway_accepted = self._start_session_processing(...)` assignment at line 4031,
  which is the only place a fresh inbound becomes `True`.
- `_handle_message_while_active` handles a bypass command by calling
  `_dispatch_active_session_command` and returning (lines 4073-4085). That helper sends the
  reply inline through `_dispatch_inline_reply` and then cancels the session (lines 3966-3987);
  neither it nor `_dispatch_inline_reply` assigns `_gateway_accepted`.
- The clarify bypass takes the same shape: a non-command message answering a pending clarify
  prompt is dispatched inline and returns at lines 4092-4105, again without setting the flag.
- The flag is only set on the paths that queue or start a background turn: 4113 (busy handler
  already claimed it), 4132, 4144 (photo follow-up), 4156 (queued text follow-up).

Adapter (`src/hermes_reticulum/plugin/adapter.py`):

- `deliver_inbound` awaits the inherited `handle_message` and then tests
  `getattr(event, "_gateway_accepted", True) is False` (line 618). False routes to
  `_note_gateway_desync` (line 629), which logs the warning and calls
  `_handle_denied_inbound` (line 565) — full-hash deny log, D6 suppression check, D4 cooldown,
  then the canned literal `REJECTION_TEXT` (line 55) through the transport seam.
- Nothing in the adapter can distinguish "the gateway dropped this event" from "the gateway
  handled this event inline and its flag was never set", because both leave the flag False.

Spec premise that breaks: A3 4.5 (`docs/specs/adapter/a3-spec.md`) defines the flag as
"False when its own authz/ingress gate dropped the event, True once the event is claimed for a
session". A command dispatched inline while a session is active *is* claimed — the base just
does not say so. The premise holds on every queued/started path and fails on both inline
paths.

## Why the ACL is not involved

- The adapter's own allowlist gate runs before the event is delivered at all; the log line
  "adapter admitted" is that gate passing.
- The gateway's authz layer reads the same allowlist values through the registration fields
  (A3 D1), so a genuine gateway deny for this peer would mean the two gates disagree — there is
  no evidence of that here, and the same peer's normal turns succeed.
- The rejection text carries no explanation by design (A3 4.3), so the peer cannot tell a false
  alarm from a real deny. That is the reason this is worth fixing rather than tolerating.

## Recurrence

The same desync line appears for the same peer three times before this incident
(2026-10-26 twice, 2026-10-30 once) in the gateway logs. Those are almost certainly the same
busy-session command path; each one sent an extra rejection the peer probably read as noise.
D4's 300 s per-destination cooldown is why the false rejection does not appear after *every*
command — repeated commands inside the window are suppressed, so the symptom looks
intermittent.

## Fix options (ruled 2026-11-11: option 1)

1. **Adapter-side, evidence-based (preferred).** Only treat `_gateway_accepted is False` as a
   deny when no send was *attempted* for this event during the `handle_message` await. The base's
   inline dispatch sends its own reply through the adapter's `send()`, and a real gate deny sends
   nothing — so "a send was attempted" is exactly the discriminator. The marker records the
   attempt, not its result: `send()` can fail and even a successful `SendResult` does not prove
   delivery, so keying on delivery would re-open the false alarm whenever an accepted command's
   reply failed or was still in flight when the await returned. Implement with a context-local
   marker set around the await in `deliver_inbound` and recorded at `send()` entry. Keeps
   criterion 10 true for genuine denies and needs no gateway change.
2. **Adapter-side, command-based.** Skip the desync alarm when `event.get_command()` resolves to
   a known command. Cheaper, but it silences a genuine gateway deny on a command message and
   still leaves the clarify path (a non-command) leaking, so it only half-fixes.
3. **Gateway-side (correct, not shippable from this repo).** Set `_gateway_accepted = True` after
   a successful inline dispatch in `_handle_message_while_active` — both the bypass-command
   branch and the clarify branch. This makes the flag mean what A3 4.5 says it means. The
   gateway is a separate install, so this lands as an upstream report or a documented
   base-class override in the adapter, not as a patch here.

## What this does not change

- A3's security property: the adapter still denies and still answers strangers itself; no
  model involvement, no prompt exposure. The false alarm only ever fires for a peer the adapter
  already admitted.
- The turn that was stopped. `/stop` worked; the ack and the cancellation both landed.
- The reason `/stop` was needed in this session (a provider stream that returned no chunks for
  about two minutes and ended in `EmptyStreamError`). That is separate, already-known provider
  behaviour, not this bug.

## Verification so far

- Live log sequence above, matching the phone transcript in order and timing.
- Source read at the cited lines in both the installed gateway and this repo's adapter.
- A regression test now reproduces the false alarm (added with card A12 on
  `step/A12-false-desync`, 2026-11-11): five cases fail on `dev` (ack followed by the canned
  rejection and the desync line) and pass after the discriminator, in both the real-base and
  CI-stub runs; the conftest stub gained the busy-session claim/no-claim shape the note above
  said it lacked.
