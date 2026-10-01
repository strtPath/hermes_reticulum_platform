# Skill: live testing the bridge and adapter

How to exercise a change against the running system, as opposed to a mocked suite. Load
before any live adapter run, provider cutover, or "it works in tests but not for real".

## Pre-flight, before you trust any live result

1. **Transport up.** `rnsd` running, the component attached to the shared instance, radio or
   TCP peer reachable. A component that builds its OWN `RNS.Reticulum()` instance instead of
   attaching to the daemon sees an empty path table; if you probe from a script you wrote, it
   will report "nothing heard" while the daemon hears fine. Verify with the daemon-attached
   tools (`rnstatus`, `rnpath`), not with your own instance.
2. **Identity.** The adapter does NOT inherit the bridge's `hermes_identity`. An adapter that
   creates its own identity announces a different hash and invalidates the deployment's
   allowlist and known peers. Resolve each identity to its destination hash and compare
   against what the peer's client has saved, before declaring the cutover done.
3. **Allowlist.** During a bridge-to-plugin cutover the allowlist can silently deny every
   message. A denied message produces no reply and no visible error, so **absence of a
   rejection is not evidence that anything got through.** Test the ACL directly rather than
   inferring it from silence.

## A mocked suite cannot validate a start path

Stubs that agree with the bug will pass. If a change touches the start path, the identity
path, or the transport attach, the only valid test is a real run. Keep the mock suite for
what it can prove (pure logic) and run the real path for the rest.

## Reading a send result

`send_to()` returning `True` proves acceptance, not delivery. A cancelled message can report
success and log nothing. When debugging "the reply vanished", look for a send line and a
delivery-obligations row; their absence points upstream of the transport. A stale gateway
after a restart is the usual culprit for "generated but never dispatched".

## Stop and restart

SIGTERM does not always exit. Verify the process is actually gone before starting a new one,
or you will debug the old program under a new name. If both components are scheduled to run,
confirm the outgoing one is inactive (`is-active` on its unit) so an inbound message cannot
be accepted by the component you are retiring.

## After the run

Sweep for PII before committing anything you learned. Live output is where real hashes and
session ids come from, and it is how they end up in a tracked findings doc. See
`docs/PII-RULES.md`.
