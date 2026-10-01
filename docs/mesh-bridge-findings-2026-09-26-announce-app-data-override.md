---
title: Finding — adapter override wipes LXMF's versioned announce app_data
date: 2026-09-26
status: fix-applied
---

# The announce display name: what the adapter override actually breaks

## Symptom

Announces now fire (fix 2026-09-26, `mesh-bridge-findings-2026-09-26-adapter-never-announced.md`),
but the display name handling diverges from both the spec and the dev bridge's
`core/bridge.py` in one concrete way: the adapter overwrites the destination's
default app_data with a bare UTF-8 name, dropping the versioned announce blob
LXMF builds from `register_delivery_identity(display_name=...)`.

## Root cause

`src/hermes_reticulum/plugin/transport.py`, `start()` does two things back to back:

1. `register_delivery_identity(self.identity, display_name=self.display_name)`
   — LXMF's `LXMRouter.register_delivery_identity` (LXMRouter.py:348) sets the
   destination's default app_data to a callable returning
   `get_announce_app_data(hash)` (LXMRouter.py:365-368), which
   `msgpack.packb`s `[display_name, stamp_cost, supported_functionality]`
   (LXMRouter.py:1034). That is the versioned 0.5.0+ announce format the client
   decodes via the `app_data[0] in 0x90..0x9f / == 0xdc` branch of
   `display_name_from_app_data` (LXMF.py:156).
2. `self.destination.set_default_app_data(lambda: self.display_name.encode("utf-8"))`
   — `Destination.set_default_app_data` just assigns `self.default_app_data`
   (Destination.py:678), so this **replaces** LXMF's complete msgpack blob with a
   bare UTF-8 name.

`set_display_name()` does the same on a runtime rename.

## Why the name still (mostly) shows, and what is lost

A modern client hitting a bare UTF-8 app_data falls to the "original announce
format" branch of `display_name_from_app_data` (LXMF.py:172) and decodes the raw
string, so the display name generally still appears — which is why this is easy
to miss. What it silently drops:

- **Stamp cost** and **compression support** — they live in the same versioned
  blob, so an operator who configured a stamp cost sees it vanish.
- **The version marker itself** — the announce is no longer in the 0.5.0+
  format, so `display_name_from_app_data`, `stamp_cost_from_app_data` and
  `compression_support_from_app_data` all take the unversioned fallback path.

## Dev bridge comparison

`core/bridge.py` (dev) passes `display_name=self.display_name` to
`register_delivery_identity` and never calls `set_default_app_data` afterward,
so it keeps LXMF's correct versioned blob. The adapter added the explicit
override (and the runtime `set_display_name` path) to satisfy spec section 12's
"name changes at runtime are worth keeping" point, but the override mechanism it
chose clobbers LXMF's own registration.

## Fix (applied 2026-09-26)

`src/hermes_reticulum/plugin/transport.py`:

- `start()` no longer calls `set_default_app_data` after
  `register_delivery_identity(display_name=...)` — LXMF's own versioned blob
  is left in place.
- `set_display_name()` now mutates `destination.display_name` (which
  `get_announce_app_data` reads per announce) instead of re-pointing
  `default_app_data`, and fires a one-off `destination.announce()` so peers
  see the new name immediately (spec section 12).

## Tests

`tests/test_reticulum_transport.py`:
- `TestDisplayName::test_set_display_name_mutates_dest_and_announces`
  replaces the old callable-app_data assertion: asserts the destination's
  `display_name` is updated and a one-off announce fires.
- `_FakeRouter.register_delivery_identity` and `_FakeDestination` now mirror
  the real router by carrying `display_name`.

Transport suite: 49 passed. Full suite: 448 passed, 1 pre-existing unrelated
failure (`test_tool_emoji.py` `setup_mcp` alias parity).

## Verification state

- Real-mechanism probe (2026-09-26, standalone interface-less RNS config):
  `start()` leaves the destination's default app_data as LXMF's versioned
  msgpack blob (first byte 0x93), a real client decode via
  `LXMF.display_name_from_app_data` returns the name, and `set_display_name`
  updates `destination.display_name`, the blob, and fires the one-off
  announce. PROBE PASS.
- Still pending (end-to-end, needs the live mesh): confirm the phone decodes
  the versioned announce (name + any stamp cost) and can complete a round
  trip. The inbound path remains unverified end to end per
  `handoff-2026-09-25-inbound-unverified.md`.

Resolve against real LXMF/`get_announce_app_data` source before editing; do not
read this finding as a substitute for the read.