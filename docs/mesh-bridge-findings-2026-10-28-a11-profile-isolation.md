# Mesh-bridge findings — 2026-10-28 — A11 closeout: profile ACL isolation proven live

**Status:** A11 card closed. The last open criterion — no cross-profile ACL leak —
verified on the live broker with `build_profile_secret_scope` (import from
`agent.secret_scope`, NOT `hermes_cli.env_loader`).

## The proof

The gateway multiplex broker serves both `default` and `mesh-test`
(`gateway_state.json` → `served_profiles`). Each profile resolves its own secret
scope from its own home:

```python
from agent.secret_scope import build_profile_secret_scope
default_scope = build_profile_secret_scope("$HERMES_HOME")
mesh_scope    = build_profile_secret_scope("$HERMES_HOME/profiles/mesh-test")
```

Result (2026-10-28):

| Profile  | HERMES_RETICULUM_ALLOWED_USERS | ALLOW_ALL |
|---|---|---|
| default  | owner hash (real peer)               | false |
| mesh-test| synthetic `bbbb…` (no real hashes)   | false |

Distinct trust lists, both fail-closed. The owner hash appears in no mesh-test
scope. Criterion 1 (no cross-profile leak) holds.

## Why this proof is the right one

The A3 read path injects the profile-scoped reader (`platform_gate_env`) into
`AccessControl`; `test_build_access_control_reads_profile_scoped_env` covers the
injection in CI. What CI cannot cover is the two real homes on this box; live
scope resolution is the missing half.

## Runtime layer (Greptile P2, 2026-10-28): the gate read is profile-bound

Reviewer note: the homes differing does not by itself prove the broker hands
each adapter the right scope, and the CI test injects a fake reader, so neither
check would catch a broker that bound the default scope for both adapters. The
runtime layer answers that: bind each profile's scope exactly as the broker does
per turn (`set_secret_scope(..., profile_home=...)` under `set_multiplex_context`),
then read through the adapter's actual gate function `platform_gate_env`:

| Bound scope   | platform_gate_env(ALLOWED_USERS) |
|---|---|
| default       | owner hash (redacted)            |
| mesh-test     | synthetic (placeholder)          |

Run with a deliberately poisoned `os.environ[HERMES_RETICULUM_ALLOWED_USERS] =
deadbeef…` (the first-writer bridged-value leak of #72348). The bound scope wins
in both cases; the env value never appears. So the adapter's real gate read is
per-profile scope driven, and an env contamination cannot leak the default
profile's allowlist into a served profile's gate.

## What still needs a live mesh-test node (out of scope for A11)

`mesh-test` is config-only: it has no `RETICULUM_DISPLAY_NAME` and no LXMF
identity, so no real adapter has run under it. The per-turn binding mechanism
above is the machinery the broker uses; a live mesh-test adapter node (giving it
an identity + display name, then a real round-trip) would close the last
integration gap between this proof and a served mesh tenant. That is follow-up
work, not an A11 blocker: the card's acceptance (distinct trust lists, no raw
`os.getenv` in the read path) is met.

## Harness notes

- `mesh-test` profile is config-only (distinct `.env`, no real hashes) — it has
  no LXMF identity, so it is a tenant for isolation tests, not a live mesh node.
- Do not clone a profile with `hermes profile create --clone` for this: cloning
  copies the real ACL/trust values into the harness.