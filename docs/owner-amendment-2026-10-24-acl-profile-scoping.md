# Owner amendment — ACL profile-scoping (2026-10-24)

Status: owner ruling, dated 2026-10-24. Handover artifact for the A3 work in
`feat/a3-acl-rejection`. Committed to `dev` on 2026-10-24 (docs/a11-handoff) so the
citation from tasks.md card A11 resolves from a fresh clone; it was deliberately
kept out of the A3 *PR branch* to avoid scope creep, not because it is throwaway.

## The ruling (one sentence)

The four `HERMES_RETICULUM_*` ACL knobs are read **profile-scoped** via
`platform_gate_env` — a process-global `.env` is the source only in single-profile
deployments — and every authorization-decision point parses with **one shared parser**.

## Why (context that changed the plan)

- **Per-profile identity + ACL is an intended goal:** each Hermes profile should be able
  to carry its own Reticulum identity and its own trust list, so peers can talk to
  profiles individually. The mesh leans **TCP transports**, which makes co-resident
  multi-profile bridges the *target* topology, not an edge case. Two profiles on one box
  need different ACLs → a single `.env` cannot own them.
- **The merged A3 spec already mandates profile-scoped reads:** `spec.md:99-101` (hard
  rule — "never `os.getenv` directly … a raw read returns the default profile's value,
  which is a silent allowlist leak") and `spec.md:17.5`. The earlier same-day "make ACL
  `.env`-only" ruling would have regressed this; it is superseded.
- **P2-5 is a parser bug, not a license to delete scoping.** `core/acl.py`
  `_load_config()` parses raw `os.getenv` with `("true","1","yes")`; adapter
  `_build_access_control()` re-derives allow_all from `platform_gate_env` with
  `("1","true","yes","on")`. Same value, different verdict by path. Fix the parser; keep
  the scoping.

## Edits already applied (option B, in the pr16 clone)

1. `PR16-FIXES.md` — Part B rewritten to this ruling (was: delete override / `.env`-only).
2. `docs/specs/adapter/a3-spec.md` — D1 and I3 restated to profile-scoped reads; dated
   AMENDMENT note added at end of §2 Rulings.
3. `docs/specs/adapter/spec.md` — added a dated "Amended 2026-10-24" note at the §17.8
   pointer (line ~675).

Note: the current `core/acl.py`/`adapter.py` **code was not touched** here — the active
A3 agent owns those.

## What the implementing agent does (from the amended PR16-FIXES Part B)

- Keep `platform_gate_env` as the reader for all four knobs; do not delete the override.
- One shared parser: `AccessControl.parse_allow_all` + `ALLOW_ALL_TRUTHY` =
  `{true,1,yes,on}`; empty/false/no/0/garbage → closed. The override calls
  `parse_allow_all(scoped)`.
- Close the raw-env gap (spec.md:99-101): `AccessControl._load_config()` must not read
  raw `os.getenv` as the deciding source. Cleanest: keep `parse_allow_all` pure and
  gateway-agnostic; adapter injects the profile-resolved values (`from_env(reader)` or
  constructor arg) so `core/acl.py` never imports the gateway and tests can inject.
  Floor if that refactor is too big for PR #16: core reads via the shared parser AND the
  adapter passes profile-scoped values through → agree by construction (D1/I3).
- Cooldown: keep clamp-to-default; prefer the same profile-scoped read (low-stakes).
- Tests: existing 91 stay green. Add (1) `_build_access_control()` ==
  `parse_allow_all(platform_gate_env(ALLOW_ALL))`; (2) the old "on"-by-source asymmetry
  is gone; (3) `parse_allow_all` truthiness table; (4) injected-reader unit test if the
  refactor lands.
- Done when: one parser everywhere, `platform_gate_env` is the sole env reader, acl.py
  gateway-import-free (or seam documented), a3-spec/spec verified amended, pytest +
  ruff green, push + retrigger Greptile.

## Still open (your call, not decided here)

- Whether to add a new task card for "per-profile mesh identity" (beyond A3) tracking the
  multi-bridge/TCP topology, or fold it into A3's after-merge follow-ups.
- Whether `platform_gate_env` single-reader should also subsume the transport knobs
  (`RETICULUM_DISPLAY_NAME` already profile-scoped; `RETICULUM_ANNOUNCE_INTERVAL` too).