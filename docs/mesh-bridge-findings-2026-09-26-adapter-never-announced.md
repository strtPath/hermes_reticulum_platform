---
title: Finding — adapter never announced its delivery identity; phone never heard it
date: 2026-09-26
status: fix-applied
---

# The "hasn't announced in 20 hours" root cause

## Symptom

After the identity swap (adapter delivery identity, seen as "<new-identity>"), the gateway's Reticulum
adapter connected fine (`✓ reticulum connected` in gateway.log) and had a full
path table (18k+ entries), but the peer's phone never saw the announce and no
inbound message could ever be delivered.

## Root cause (not the version, not the stack)

`src/hermes_reticulum/plugin/transport.py` registered the LXMF delivery
identity (`register_delivery_identity`) and set the display name, but **never
called `.announce()` on it**, and had no periodic re-announce timer.

RNS/LXMF do NOT auto-announce a delivery destination on registration. A
destination only announces when `.announce()` is explicitly called. The old
bridge (`src/hermes_reticulum/core/bridge.py`) did this correctly —
`_do_announce()` calls `self.destination.announce()` on startup and
`_start_announce_timer()` re-announces every `announce_interval_min` minutes.
The plugin adapter inherited the identity-registration machinery but **dropped
the announce call** when the transport was split out. Result: the peer could
never hear "<new-identity>".

Red herrings ruled out along the way:
- **RNS version mismatch** (gateway 1.5.2 vs repo/daemons 1.5.4): real drift,
  but not the blocker. Fixed anyway (`pip install rns==1.5.4` in the gateway
  venv) because it's a latent risk, not the cause.
- **Wrong Reticulum stack/config dir**: the adapter attaches to `~/.reticulum`
  (rnsd/MichMesh). The alternate `<home>/reticulum` (nomadnet daemon) is
  a dead AutoInterface-only stack with multicast blocked. Neither is the
  announce blocker. As the operator stated, RNS handles routing transparently.

## Fix (applied 2026-09-26)

`src/hermes_reticulum/plugin/transport.py`, `start()`:
- Call `self.destination.announce()` immediately after
  `register_delivery_identity` + `set_default_app_data`.
- Start a daemon-thread periodic re-announce timer at `announce_interval_min`
  (default 60; 0 disables), mirroring `core/bridge.py`.

New methods: `_announce_job`, `_start_announce_timer`, `_stop_announce_timer`.
Timer attributes `_announce_timer` / `_announce_timer_stop` initialized in
`__init__`, joined in `stop()`.

## Tests

`tests/test_reticulum_transport.py` — new `TestAnnounceOnStart` class:
- `start()` announces immediately and arms the timer; `stop()` tears it down.
- `announce_interval=0` still announces once but disables the periodic timer.
- `stop()` clears both timer attributes.

Full transport suite: 49 passed. (One unrelated pre-existing failure in
`tests/test_tool_emoji.py` about a `setup_mcp` alias parity check.)

## Verification state

- Gateway restarted 2026-09-26 17:52:31 (PID 590239) — after the 17:36 edit,
  so the fixed transport is live.
- Adapter connected 17:53:15; `outbound_stamp_costs` growing; ratchet for the
  phone (mesh peer) touched 17:53 — live ratchet exchange.
- Pending: confirm the phone sees "<new-identity>" and can complete a round trip.